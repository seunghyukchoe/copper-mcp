"""Regressions for the gated live one-undo-commit apply preconditions (ADR-0074).

The mutation is not implemented, so these tests pin the two things that *are*: that no path
reaches a running editor without three separate consents and a live-scoped capability, and that
every refusal this surface can produce is reachable and typed.

`kicad-python` is an optional dependency and absent from CI, so the KiCad end of the boundary is
a fake, exactly as SEC-118 recorded for the observer's binding-failure paths. What these tests
pin is CopperMCP's typed refusal and the order of its checks -- **not** a claim about where the
real binding raises.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import copper_mcp.apply.tokens as tokens_module
import copper_mcp.kicad_ipc as kicad_ipc
import copper_mcp.live_apply as live_apply
from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.apply.tokens import ApplyBinding, ApplyTokenAuthority, LiveApplyBinding
from copper_mcp.board_ir import ConversionResult, Diagnostic, NetClass, Severity
from copper_mcp.config import ConfigurationError, Settings
from copper_mcp.live_apply import (
    LiveApplyError,
    LiveApplyFailureCode,
    LiveApplyPrecondition,
    apply_live_candidate,
)
from copper_mcp.live_layered_route_preview import preview_live_layered_route
from copper_mcp.request_boundary import RequestError
from copper_mcp.routing import canonical_layered_candidate_bytes, verify_layered_candidate_id

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
SESSION_TOKEN = "copper-mcp-test-kicad-session"
# The identity KiCad's API server generates once per process and returns in every response
# envelope. A restarted editor reports a different one; CopperMCP's own environment cannot
# change it, which is the whole point of deriving the session revision from it.
EDITOR_INSTANCE_TOKEN = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
RESTARTED_EDITOR_INSTANCE_TOKEN = "9c5b94b1-35ad-49bb-b118-8e8fc24abf80"
CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}


@pytest.fixture(autouse=True)
def _fake_kicad_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KICAD_API_TOKEN", SESSION_TOKEN)
    monkeypatch.setattr(kicad_ipc, "_SESSION_REVISION_SALT", b"\x42" * 32)


class FutureVersionError(Exception):
    """Stands in for ``kipy.errors.FutureVersionError``.

    The name is load-bearing: the adapter matches this exception by class *name* because
    ``kicad-python`` is an optional dependency that cannot be imported for an isinstance check.
    """


class _FakeVersion:
    def __init__(self, major: int = 10, minor: int = 0, patch: int = 5) -> None:
        self.major = major
        self.minor = minor
        self.patch = patch


class _FakeLiveBoard:
    def __init__(self, source: str) -> None:
        self._source = source

    def get_as_string(self) -> str:
        return self._source


class _FakeKipyClient:
    """Mirror of ``kipy.client.KiCadClient``'s learned-instance-token attribute.

    The real client stores the token KiCad returned in ``ApiResponseHeader.kicad_token`` on
    ``self._kicad_token``.  The fake carries it at the same place so that these tests exercise
    the production accessor rather than a bespoke test-only seam.
    """

    def __init__(self, instance_token: str) -> None:
        self._kicad_token = instance_token


class _FakeLiveKiCad:
    def __init__(
        self,
        source: str,
        instance_token: str = EDITOR_INSTANCE_TOKEN,
        kicad: tuple[int, int, int] = (10, 0, 5),
        api: tuple[int, int, int] = (10, 0, 5),
    ) -> None:
        self._board = _FakeLiveBoard(source)
        self._client = _FakeKipyClient(instance_token)
        self._kicad = kicad
        self._api = api

    def get_version(self) -> _FakeVersion:
        return _FakeVersion(*self._kicad)

    def get_api_version(self) -> _FakeVersion:
        return _FakeVersion(*self._api)

    def check_version(self) -> bool:
        # Reproduces kipy 0.7.1's asymmetry: raises only for a strictly newer editor.
        if self._kicad > self._api:
            raise FutureVersionError()
        return True

    def get_board(self) -> _FakeLiveBoard:
        return self._board


def _client_factory(
    source: bytes,
    instance_token: str = EDITOR_INSTANCE_TOKEN,
    kicad: tuple[int, int, int] = (10, 0, 5),
    api: tuple[int, int, int] = (10, 0, 5),
) -> Any:
    text = source.decode("utf-8")

    def factory(**_: object) -> _FakeLiveKiCad:
        return _FakeLiveKiCad(text, instance_token, kicad, api)

    return factory


def _unreachable(**_: object) -> Any:
    raise AssertionError("a refusal before the endpoint must not create an IPC client")


def _session_revision(instance_token: str = EDITOR_INSTANCE_TOKEN) -> str:
    revision = kicad_ipc._session_revision(instance_token)
    assert revision is not None
    return revision


def _enabled(tmp_path: Path) -> Settings:
    return Settings(workspace=tmp_path, allow_live_ipc=True, allow_live_apply=True)


def _live_context(tmp_path: Path) -> tuple[bytes, str, str, str, str]:
    """Return the fixture bytes and the four values a live proposal is bound to."""

    source = FIXTURE.read_bytes()
    profile = KiCadConstraintProfile(
        net_classes=(NetClass(id="class:request", name="Request", **CONSTRAINTS),),
        default_net_class_id="class:request",
    )
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    pads = conversion.snapshot.content.pads
    return (
        source,
        pads[0].id,
        pads[1].id,
        f"sha256:{hashlib.sha256(source).hexdigest()}",
        conversion.snapshot.snapshot_digest,
    )


def _preview(
    tmp_path: Path,
    authority: ApplyTokenAuthority,
    settings: Settings | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    source, start, end, board_revision, snapshot_digest = _live_context(tmp_path)
    request: dict[str, Any] = {
        "board": "live",
        "start_pad_id": start,
        "end_pad_id": end,
        "constraints": dict(CONSTRAINTS),
        "expect_board_revision": board_revision,
        "expect_snapshot_digest": snapshot_digest,
        "expect_session_revision": _session_revision(),
        "grid_step_nm": 250_000,
        "seed": 23,
        "include_apply_token": True,
    }
    request.update(overrides)
    result = preview_live_layered_route(
        request,
        settings if settings is not None else _enabled(tmp_path),
        authority,
        client_factory=_client_factory(source),
    )
    return dict(result)


def _authorized_request(preview: dict[str, Any]) -> dict[str, Any]:
    assert preview["status"] == "routed", preview.get("diagnostic")
    assert preview["apply_token"] is not None
    return {
        "board": "live",
        "candidate": preview["candidate"],
        "constraints": dict(CONSTRAINTS),
        "apply_token": preview["apply_token"],
        "expect_board_revision": preview["board_revision"],
        "expect_snapshot_digest": preview["snapshot_digest"],
        "expect_session_revision": _session_revision(),
    }


def _apply(
    tmp_path: Path,
    request: dict[str, Any],
    authority: ApplyTokenAuthority,
    settings: Settings | None = None,
    source: bytes | None = None,
    kicad: tuple[int, int, int] = (10, 0, 5),
    api: tuple[int, int, int] = (10, 0, 5),
) -> dict[str, Any]:
    live_source = source if source is not None else FIXTURE.read_bytes()
    return dict(
        apply_live_candidate(
            request,
            settings if settings is not None else _enabled(tmp_path),
            authority,
            client_factory=_client_factory(live_source, kicad=kicad, api=api),
        )
    )


@pytest.mark.parametrize(
    ("kicad", "api", "verdict"),
    [
        ((10, 0, 5), (10, 0, 1), "future_api_unverified"),
        ((10, 0, 0), (10, 0, 1), "legacy_api_unverified"),
    ],
)
def test_live_apply_refuses_every_acceptance_the_read_paths_allow(
    tmp_path: Path,
    kicad: tuple[int, int, int],
    api: tuple[int, int, int],
    verdict: str,
) -> None:
    """ADR-0128 tiers the window: a read may publish an unverified verdict, a mutation may not.

    Both pairs here are *observable* on every read surface. Apply refuses them anyway, because
    a caller cannot act on a disclosure attached to a board that has already changed.
    """

    authority = ApplyTokenAuthority()
    result = _apply(
        tmp_path,
        _authorized_request(_preview(tmp_path, authority)),
        authority,
        kicad=kicad,
        api=api,
    )
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.UNSUPPORTED_KICAD_VERSION.value
    assert verdict in diagnostic["message"]
    assert ".".join(str(part) for part in kicad) in diagnostic["message"]
    assert ".".join(str(part) for part in api) in diagnostic["message"]
    assert result["status"] == "refused"
    assert result["mutation_attempted"] is False
    assert result["board_revision_after"] is None


def test_live_apply_still_reaches_its_boundary_on_a_verified_binding(tmp_path: Path) -> None:
    """Not vacuous: the version gate refuses drift and nothing else."""

    authority = ApplyTokenAuthority()
    result = _apply(
        tmp_path,
        _authorized_request(_preview(tmp_path, authority)),
        authority,
        kicad=(10, 0, 1),
        api=(10, 0, 1),
    )
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.CAPABILITY_NOT_IMPLEMENTED.value


# --------------------------------------------------------------------------------------------
# Flag gating
# --------------------------------------------------------------------------------------------


def test_live_apply_is_off_by_default_and_reads_exact_membership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("COPPER_MCP_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("COPPER_MCP_ALLOW_LIVE_APPLY", raising=False)
    assert Settings.from_env().allow_live_apply is False

    monkeypatch.setenv("COPPER_MCP_ALLOW_LIVE_APPLY", "1")
    assert Settings.from_env().allow_live_apply is True
    monkeypatch.setenv("COPPER_MCP_ALLOW_LIVE_APPLY", "0")
    assert Settings.from_env().allow_live_apply is False

    # `bool()` is true for every one of these. A flag that authorizes mutating a document the
    # operator is looking at must never be switched on by an ambiguous spelling.
    for ambiguous in (
        "true",
        "True",
        "TRUE",
        "false",
        "False",
        "yes",
        "no",
        "on",
        "off",
        "",
        " 1",
        "1 ",
        "01",
        "1.0",
        "y",
    ):
        monkeypatch.setenv("COPPER_MCP_ALLOW_LIVE_APPLY", ambiguous)
        with pytest.raises(ConfigurationError, match="COPPER_MCP_ALLOW_LIVE_APPLY"):
            Settings.from_env()


def test_disabled_live_apply_refuses_before_opening_anything(tmp_path: Path) -> None:
    def refuse(**_: object) -> object:  # pragma: no cover - must never run
        raise AssertionError("a disabled live apply must not create an IPC client")

    result = dict(
        apply_live_candidate(
            {"board": "live"},
            Settings(workspace=tmp_path, allow_live_ipc=True, allow_live_apply=False),
            ApplyTokenAuthority(),
            client_factory=refuse,
        )
    )
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.LIVE_APPLY_DISABLED.value
    assert "COPPER_MCP_ALLOW_LIVE_APPLY" in diagnostic["message"]
    # The request was not even parsed: a disabled deployment must not be distinguishable by how
    # it fails on a well-formed versus a malformed request.
    assert result["request"] is None
    assert result["preconditions_verified"] == []
    assert result["mutation_attempted"] is False


def test_live_apply_also_requires_the_observation_opt_in(tmp_path: Path) -> None:
    def refuse(**_: object) -> object:  # pragma: no cover - must never run
        raise AssertionError("live apply without live IPC consent must not create a client")

    result = dict(
        apply_live_candidate(
            {"board": "live"},
            Settings(workspace=tmp_path, allow_live_ipc=False, allow_live_apply=True),
            ApplyTokenAuthority(),
            client_factory=refuse,
        )
    )
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.LIVE_IPC_DISABLED.value
    assert "COPPER_MCP_ALLOW_LIVE_IPC" in diagnostic["message"]


def test_file_apply_consent_neither_grants_nor_is_required_by_live_apply(tmp_path: Path) -> None:
    """`COPPER_MCP_ALLOW_APPLY` is orthogonal in both directions (ADR-0074)."""

    authority = ApplyTokenAuthority()
    # Granting file apply must not grant live apply.
    granted = dict(
        apply_live_candidate(
            {"board": "live"},
            Settings(workspace=tmp_path, allow_apply=True, allow_live_ipc=True),
            authority,
        )
    )
    granted_diagnostic = granted["diagnostic"]
    assert isinstance(granted_diagnostic, dict)
    assert granted_diagnostic["code"] == LiveApplyFailureCode.LIVE_APPLY_DISABLED.value

    # And withholding file apply must not withhold live apply: the narrower capability is
    # reachable without enabling the broader one.
    request = _authorized_request(_preview(tmp_path, authority))
    withheld = _apply(
        tmp_path,
        request,
        authority,
        Settings(workspace=tmp_path, allow_apply=False, allow_live_ipc=True, allow_live_apply=True),
    )
    withheld_diagnostic = withheld["diagnostic"]
    assert isinstance(withheld_diagnostic, dict)
    assert withheld_diagnostic["code"] == LiveApplyFailureCode.CAPABILITY_NOT_IMPLEMENTED.value


# --------------------------------------------------------------------------------------------
# Token minting and binding
# --------------------------------------------------------------------------------------------


def test_preview_mints_no_token_unless_asked_and_enabled(tmp_path: Path) -> None:
    authority = ApplyTokenAuthority()
    assert _preview(tmp_path, authority, include_apply_token=False)["apply_token"] is None
    # Asked for, but the operator has not opted in to live apply: minting a capability the apply
    # surface would refuse is how an unreachable destructive path got exercised once before.
    disabled = _preview(
        tmp_path,
        authority,
        Settings(workspace=tmp_path, allow_live_ipc=True, allow_live_apply=False),
    )
    assert disabled["apply_token"] is None
    assert _preview(tmp_path, authority)["apply_token"] is not None


def test_a_valid_capability_reaches_the_not_implemented_boundary(tmp_path: Path) -> None:
    authority = ApplyTokenAuthority()
    result = _apply(tmp_path, _authorized_request(_preview(tmp_path, authority)), authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.CAPABILITY_NOT_IMPLEMENTED.value
    assert result["preconditions_verified"] == [
        precondition.value for precondition in LiveApplyPrecondition
    ]
    assert result["status"] == "refused"
    assert result["mutation_attempted"] is False
    assert result["undo_steps_pushed"] == 0
    assert result["post_apply_observation"] == "not_run"
    assert result["board_revision_after"] is None


def test_the_capability_token_is_never_echoed_back(tmp_path: Path) -> None:
    authority = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, authority))
    result = _apply(tmp_path, request, authority)
    assert request["apply_token"] not in repr(result)


def test_a_capability_from_another_authority_is_refused(tmp_path: Path) -> None:
    minting = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, minting))
    # The signing key exists only inside the issuing process, so a restart invalidates every
    # outstanding token. A second authority stands in for that restart.
    result = _apply(tmp_path, request, ApplyTokenAuthority())
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.INVALID_TOKEN.value
    assert result["preconditions_verified"] == [LiveApplyPrecondition.OPERATOR_OPT_IN.value]


def test_a_file_apply_token_can_never_authorize_the_live_surface(tmp_path: Path) -> None:
    """Domain separation, not a field value, is what keeps the two capabilities apart."""

    authority = ApplyTokenAuthority()
    preview = _preview(tmp_path, authority)
    candidate = preview["candidate"]
    assert isinstance(candidate, dict)
    file_token = authority.issue(
        ApplyBinding(
            candidate_id=candidate["candidate_id"],
            base_revision=preview["snapshot_digest"],
            board_revision=preview["board_revision"],
            relative_path=_session_revision(),
        )
    )
    request = _authorized_request(preview)
    request["apply_token"] = file_token
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.INVALID_TOKEN.value


def test_the_live_binding_prefixes_the_live_domain_and_never_the_file_domain() -> None:
    """The domain is the mechanism, so assert the domain -- not a field value downstream of it.

    The refusal above is satisfied by *any* difference between the two payloads, and the two
    ``operation`` value sets happen to be disjoint, so it passes with the domain deleted. That
    is the belt-and-braces trap: ADR-0074 explicitly disclaims the ``operation`` field as the
    separating mechanism, so a test that only observes it proves the wrong thing. These
    assertions read the bytes the MAC is actually taken over.
    """

    expires_at = 1_800_000_000
    live = LiveApplyBinding(
        candidate_id="candidate:0",
        base_revision="sha256:" + "a" * 64,
        board_revision="sha256:" + "b" * 64,
        session_revision=_session_revision(),
    )
    file_binding = ApplyBinding(
        candidate_id="candidate:0",
        base_revision="sha256:" + "a" * 64,
        board_revision="sha256:" + "b" * 64,
        relative_path=_session_revision(),
    )
    live_payload = live.payload(expires_at)
    file_payload = file_binding.payload(expires_at)

    assert live_payload.startswith(tokens_module._LIVE_DOMAIN)
    assert not live_payload.startswith(tokens_module._DOMAIN)
    assert file_payload.startswith(tokens_module._DOMAIN)
    assert not file_payload.startswith(tokens_module._LIVE_DOMAIN)
    assert tokens_module._LIVE_DOMAIN != tokens_module._DOMAIN

    # And the domain is what an attacker would have to forge: neither payload's remainder may
    # be reachable under the other domain, whatever the `operation` values happen to be.
    assert (
        tokens_module._LIVE_DOMAIN + live_payload.removeprefix(tokens_module._LIVE_DOMAIN)
        == live_payload
    )
    assert not live_payload.removeprefix(tokens_module._LIVE_DOMAIN).startswith(
        tokens_module._DOMAIN
    )


def test_a_spent_capability_is_refused_as_already_used(tmp_path: Path) -> None:
    authority = ApplyTokenAuthority()
    preview = _preview(tmp_path, authority)
    request = _authorized_request(preview)
    verified = authority.verify(request["apply_token"], _binding_of(preview))
    authority.consume(verified)
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.TOKEN_ALREADY_USED.value


def test_an_expired_capability_is_refused(tmp_path: Path) -> None:
    now = [1_000_000.0]
    authority = ApplyTokenAuthority(ttl_seconds=60, clock=lambda: now[0])
    request = _authorized_request(_preview(tmp_path, authority))
    now[0] += 61
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.TOKEN_EXPIRED.value


def test_a_refusal_does_not_consume_the_capability(tmp_path: Path) -> None:
    """Nothing was spent, so a legitimate retry after a transient refusal must still work."""

    authority = ApplyTokenAuthority()
    preview = _preview(tmp_path, authority)
    request = _authorized_request(preview)
    first = _apply(tmp_path, request, authority)
    second = _apply(tmp_path, request, authority)
    for result in (first, second):
        diagnostic = result["diagnostic"]
        assert isinstance(diagnostic, dict)
        assert diagnostic["code"] == LiveApplyFailureCode.CAPABILITY_NOT_IMPLEMENTED.value


def _binding_of(preview: dict[str, Any]) -> LiveApplyBinding:
    candidate = preview["candidate"]
    assert isinstance(candidate, dict)
    return LiveApplyBinding(
        candidate_id=candidate["candidate_id"],
        base_revision=preview["snapshot_digest"],
        board_revision=preview["board_revision"],
        session_revision=_session_revision(),
    )


def test_the_capability_is_checked_before_a_socket_is_opened(tmp_path: Path) -> None:
    authority = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, authority))
    request["apply_token"] = "not-a-token"

    def refuse(**_: object) -> object:  # pragma: no cover - must never run
        raise AssertionError("an unauthorized caller must not reach the operator's editor")

    result = dict(
        apply_live_candidate(request, _enabled(tmp_path), authority, client_factory=refuse)
    )
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.INVALID_TOKEN.value


# --------------------------------------------------------------------------------------------
# Compare-and-swap
# --------------------------------------------------------------------------------------------


def test_a_board_edited_since_the_preview_is_refused_as_stale(tmp_path: Path) -> None:
    authority = ApplyTokenAuthority()
    preview = _preview(tmp_path, authority)
    request = _authorized_request(preview)
    edited = FIXTURE.read_bytes().replace(b"kicad_pcb", b"kicad_pcb", 1) + b"\n"
    result = _apply(tmp_path, request, authority, source=edited)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.STALE_BOARD_REVISION.value
    # Never auto-refreshed: the caller previewed one document and is told so, and the observed
    # revision is reported rather than substituted.
    assert result["board_revision_before"] != request["expect_board_revision"]
    assert result["preconditions_verified"] == [
        LiveApplyPrecondition.OPERATOR_OPT_IN.value,
        LiveApplyPrecondition.CAPABILITY_TOKEN.value,
        LiveApplyPrecondition.LIVE_SESSION_BOUND.value,
    ]


def test_both_flags_off_reports_the_live_apply_grant_first(tmp_path: Path) -> None:
    """Pin the reporting order, which nothing else covers.

    The PR argues explicitly for reporting order "so the operator learns *which* grant is
    missing", and both orders fail closed, so only a test can hold it. `live_apply_disabled` is
    named first because it is the grant specific to this tool; `live_ipc_disabled` is shared
    with every live surface and is the less informative answer to "why can't I apply?".
    """

    settings = Settings(workspace=tmp_path, allow_live_ipc=False, allow_live_apply=False)
    result = dict(
        apply_live_candidate({}, settings, ApplyTokenAuthority(), client_factory=_unreachable)
    )
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.LIVE_APPLY_DISABLED.value
    assert result["preconditions_verified"] == []


def test_a_candidate_id_rewritten_to_match_the_token_is_refused_by_the_verifier(
    tmp_path: Path,
) -> None:
    """The binding is sound, and this pins *where* it is enforced.

    `request.candidate_id` and `candidate.candidate_id` are both read from the manifest's own
    `candidate_id` key, so comparing them is a tautology that no input can fail. The check that
    actually earns the comment is the recomputation inside `verify_layered_candidate_id`, which
    re-derives the identity from the geometry. This moves the geometry and rewrites the claimed
    identity to match the token: the tautological comparison is satisfied throughout, and the
    refusal must still arrive.
    """

    authority = ApplyTokenAuthority()
    preview = _preview(tmp_path, authority)
    request = _authorized_request(preview)
    candidate = dict(request["candidate"])
    patch = dict(candidate["patch"])
    paths = [dict(path) for path in patch["paths"]]
    # Move the geometry and leave the claimed identity alone, so it still matches the token.
    paths[0]["vertices_nm"] = [[x + 1_000, y] for x, y in paths[0]["vertices_nm"]]
    patch["paths"] = paths
    candidate["patch"] = patch
    request["candidate"] = candidate

    # Both operands of the removed comparison are `_digest(..., <manifest>["candidate_id"])` over
    # this same mapping, so they agree for every possible input -- including this tampered one.
    parsed = live_apply.parse_live_apply_request(request)
    rebuilt = live_apply.layered_candidate_from_document(parsed.candidate)
    assert rebuilt.candidate_id == parsed.candidate_id

    # The refusal therefore comes from the geometry recomputation, not from that comparison.
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.CANDIDATE_VERIFICATION_FAILED.value


def test_a_restarted_editor_is_refused_as_a_stale_session(tmp_path: Path) -> None:
    """The restart is modelled at the editor, which is the only place it can happen.

    A KiCad restart constructs a fresh ``KICAD_API_SERVER`` whose ``m_token`` is a new random
    ``KIID``, and every response envelope carries it. It does **not** rewrite CopperMCP's own
    environment block -- a previous version of this test monkeypatched ``KICAD_API_TOKEN`` and
    so modelled an event the restart cannot cause. Here the editor reports a different instance
    and CopperMCP's environment is untouched, which is the real shape of the hazard: the board
    bytes are byte-identical and the capability still must not carry over.
    """

    authority = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, authority))
    result = dict(
        apply_live_candidate(
            request,
            _enabled(tmp_path),
            authority,
            client_factory=_client_factory(FIXTURE.read_bytes(), RESTARTED_EDITOR_INSTANCE_TOKEN),
        )
    )
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.STALE_SESSION.value
    assert (
        LiveApplyPrecondition.LIVE_BOARD_REVISION_BOUND.value
        not in (result["preconditions_verified"])
    )


def test_the_session_revision_tracks_the_editor_and_not_coppermcp_s_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The converse of the restart test, and the reason the leg is not inert.

    Rotating ``KICAD_API_TOKEN`` inside CopperMCP's own process is not a KiCad restart and must
    not be readable as one. If the session revision were derived from that variable -- as it was
    before this was corrected -- this apply would refuse with ``stale_session`` for an editor
    that never went away, and the genuine restart above would be accepted.
    """

    authority = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, authority))
    monkeypatch.setenv("KICAD_API_TOKEN", "an-unrelated-rotation-of-our-own-environment")
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.CAPABILITY_NOT_IMPLEMENTED.value


def test_an_editor_that_reports_no_instance_identity_is_refused(tmp_path: Path) -> None:
    """Fail closed: no observable editor identity means no session binding, not a free pass."""

    authority = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, authority))

    def factory(**_: object) -> Any:
        client = _FakeLiveKiCad(FIXTURE.read_bytes().decode("utf-8"))
        del client._client._kicad_token
        return client

    result = dict(
        apply_live_candidate(request, _enabled(tmp_path), authority, client_factory=factory)
    )
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.STALE_SESSION.value


@pytest.mark.parametrize(
    "field", ["expect_session_revision", "expect_board_revision", "expect_snapshot_digest"]
)
def test_no_compare_and_swap_value_is_caller_editable_after_minting(
    tmp_path: Path, field: str
) -> None:
    """All three CAS values are inside the MAC, so relaxing any of them invalidates the token."""

    authority = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, authority))
    request[field] = (
        "pbkdf2-hmac-sha256:" + "0" * 64
        if field == "expect_session_revision"
        else "sha256:" + "1" * 64
    )
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.INVALID_TOKEN.value


def test_a_snapshot_digest_bound_to_other_constraints_is_refused(tmp_path: Path) -> None:
    """Board IR carries net classes, so converting differently moves the snapshot digest."""

    authority = ApplyTokenAuthority()
    preview = _preview(tmp_path, authority)
    request = _authorized_request(preview)
    request["constraints"] = dict(CONSTRAINTS, clearance_nm=300_000)
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.STALE_SNAPSHOT_DIGEST.value
    assert result["snapshot_digest_before"] != request["expect_snapshot_digest"]


# --------------------------------------------------------------------------------------------
# Candidate replay
# --------------------------------------------------------------------------------------------


def test_a_candidate_is_never_trusted_from_its_manifest(tmp_path: Path) -> None:
    authority = ApplyTokenAuthority()
    preview = _preview(tmp_path, authority)
    request = _authorized_request(preview)
    candidate = dict(request["candidate"])
    patch = dict(candidate["patch"])
    paths = [dict(path) for path in patch["paths"]]
    # Move the geometry without touching the claimed identity.
    paths[0]["vertices_nm"] = [[x + 1_000, y] for x, y in paths[0]["vertices_nm"]]
    patch["paths"] = paths
    candidate["patch"] = patch
    request["candidate"] = candidate
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.CANDIDATE_VERIFICATION_FAILED.value
    assert result["preconditions_verified"] == [
        LiveApplyPrecondition.OPERATOR_OPT_IN.value,
        LiveApplyPrecondition.CAPABILITY_TOKEN.value,
        LiveApplyPrecondition.LIVE_SESSION_BOUND.value,
        LiveApplyPrecondition.LIVE_BOARD_REVISION_BOUND.value,
        LiveApplyPrecondition.BOARD_IR_SNAPSHOT_BOUND.value,
    ]


def test_a_malformed_candidate_manifest_is_a_typed_refusal(tmp_path: Path) -> None:
    authority = ApplyTokenAuthority()
    preview = _preview(tmp_path, authority)
    request = _authorized_request(preview)
    candidate = dict(request["candidate"])
    candidate["patch"] = {"net_id": "net:1"}
    request["candidate"] = candidate
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.CANDIDATE_VERIFICATION_FAILED.value
    assert len(diagnostic["message"]) <= 1024


def test_an_oversized_candidate_is_refused_before_verification(tmp_path: Path) -> None:
    authority = ApplyTokenAuthority()
    preview = _preview(tmp_path, authority)
    request = _authorized_request(preview)
    candidate = dict(request["candidate"])
    patch = dict(candidate["patch"])
    patch["paths"] = [dict(patch["paths"][0]) for _ in range(1_000)]
    candidate["patch"] = patch
    request["candidate"] = candidate
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.CANDIDATE_VERIFICATION_FAILED.value


# --------------------------------------------------------------------------------------------
# Request validation and binding failures
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda request: request.pop("apply_token"), id="missing-token"),
        pytest.param(lambda request: request.pop("expect_session_revision"), id="missing-session"),
        pytest.param(lambda request: request.update(board="live.kicad_pcb"), id="file-board"),
        pytest.param(lambda request: request.update(unexpected=1), id="unknown-field"),
        pytest.param(
            lambda request: request.update(expect_board_revision="not-a-digest"),
            id="malformed-digest",
        ),
        pytest.param(
            lambda request: request.update(expect_session_revision="sha256:" + "0" * 64),
            id="wrong-session-shape",
        ),
        pytest.param(lambda request: request.update(candidate=[]), id="candidate-not-a-mapping"),
    ],
)
def test_a_malformed_request_is_refused_without_reaching_the_editor(
    tmp_path: Path, mutate: Any
) -> None:
    authority = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, authority))
    mutate(request)

    def refuse(**_: object) -> object:  # pragma: no cover - must never run
        raise AssertionError("a malformed request must not reach the operator's editor")

    result = dict(
        apply_live_candidate(request, _enabled(tmp_path), authority, client_factory=refuse)
    )
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.INVALID_REQUEST.value
    assert result["request"] is None
    # Consent had already passed, and `preconditions_verified` understates nothing either.
    assert result["preconditions_verified"] == [LiveApplyPrecondition.OPERATOR_OPT_IN.value]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            kicad_ipc.KicadIpcUnavailableError("no binding"),
            LiveApplyFailureCode.BINDING_UNAVAILABLE,
        ),
        (kicad_ipc.KicadIpcConfigurationError("bad socket"), LiveApplyFailureCode.INVALID_ENDPOINT),
        (
            kicad_ipc.KicadIpcVersionError("too new"),
            LiveApplyFailureCode.UNSUPPORTED_KICAD_VERSION,
        ),
        (
            kicad_ipc.KicadIpcPayloadError("too big"),
            LiveApplyFailureCode.LIVE_BOARD_OVER_BUDGET,
        ),
        # A payload KiCad returned in an unusable *shape* is not a budget overrun. It is checked
        # before its `KicadIpcPayloadError` base, the same way the deadline precedes its
        # connection base, so a type fault is never reported as an operator-fixable size limit.
        (
            kicad_ipc.KicadIpcPayloadTypeError("not text"),
            LiveApplyFailureCode.LIVE_EDITOR_UNAVAILABLE,
        ),
        (kicad_ipc.KicadIpcDeadlineError("expired"), LiveApplyFailureCode.DEADLINE_EXPIRED),
        (
            kicad_ipc.KicadIpcConnectionError("closed"),
            LiveApplyFailureCode.LIVE_EDITOR_UNAVAILABLE,
        ),
        (kicad_ipc.KicadIpcDisabledError("off"), LiveApplyFailureCode.LIVE_IPC_DISABLED),
        # P3-4: the base class itself. `LiveBoardObservation.__post_init__` raises it in seven
        # places from outside the capture's own try block, so without a catch-all it would leave
        # the MCP tool as an unhandled RuntimeError rather than a typed refusal.
        (
            kicad_ipc.KicadIpcError("untyped observer fault"),
            LiveApplyFailureCode.LIVE_EDITOR_UNAVAILABLE,
        ),
    ],
)
def test_every_capture_failure_maps_to_its_own_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: LiveApplyFailureCode,
) -> None:
    """The observer's typed errors must not collapse into one opaque refusal.

    `KicadIpcDeadlineError` subclasses `KicadIpcConnectionError`, so this also pins that a
    budget that ran out is not reported as an editor that would not answer.
    """

    authority = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, authority))

    def failing_capture(*_: object, **__: object) -> object:
        raise error

    monkeypatch.setattr("copper_mcp.live_apply.capture_live_board", failing_capture)
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == expected.value
    assert result["preconditions_verified"] == [
        LiveApplyPrecondition.OPERATOR_OPT_IN.value,
        LiveApplyPrecondition.CAPABILITY_TOKEN.value,
    ]


def test_malformed_embedder_arguments_raise_rather_than_refuse(tmp_path: Path) -> None:
    with pytest.raises(LiveApplyError):
        apply_live_candidate({}, object(), ApplyTokenAuthority())  # type: ignore[arg-type]
    with pytest.raises(LiveApplyError):
        apply_live_candidate({}, _enabled(tmp_path), object())  # type: ignore[arg-type]


def test_a_board_outside_the_supported_subset_is_refused_after_the_board_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A board whose bytes match but whose conversion fails is its own refusal, not a stale one."""

    authority = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, authority))

    def unsupported(*_: object, **__: object) -> ConversionResult:
        return ConversionResult(
            snapshot=None,
            diagnostics=(
                Diagnostic(
                    code="unsupported.document",
                    severity=Severity.ERROR,
                    message="not a board",
                    source_locator="root",
                ),
            ),
        )

    monkeypatch.setattr(live_apply, "parse_kicad_bytes", unsupported)
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.UNSUPPORTED_BOARD.value
    assert result["preconditions_verified"] == [
        LiveApplyPrecondition.OPERATOR_OPT_IN.value,
        LiveApplyPrecondition.CAPABILITY_TOKEN.value,
        LiveApplyPrecondition.LIVE_SESSION_BOUND.value,
        LiveApplyPrecondition.LIVE_BOARD_REVISION_BOUND.value,
    ]
    assert result["conversion_diagnostic_counts"] == {"unsupported.document": 1}


def test_every_failure_code_is_covered_by_the_public_contract() -> None:
    """The typed vocabulary and the MCP contract must not drift apart."""

    from copper_mcp.mcp_contracts import LiveApplyDiagnosticContract

    declared = set(
        LiveApplyDiagnosticContract.model_fields["code"].annotation.__args__  # type: ignore[union-attr]
    )
    assert {code.value for code in LiveApplyFailureCode} == declared


def test_the_mcp_tool_stays_listed_and_truthfully_annotated() -> None:
    """An absent tool is indistinguishable from an unimplemented one (ADR-0025, ADR-0069)."""

    import asyncio

    from copper_mcp.mcp_server import mcp

    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    tool = tools["apply_live_candidate"]
    annotations = tool.annotations
    assert annotations is not None
    # The annotation describes the capability the tool *is*, not the subset implemented today.
    assert annotations.read_only_hint is False
    assert annotations.destructive_hint is True
    assert annotations.idempotent_hint is False
    assert tool.input_schema["additionalProperties"] is False
    assert set(tool.input_schema["properties"]["request"]["properties"]) == {
        "board",
        "candidate",
        "constraints",
        "apply_token",
        "expect_board_revision",
        "expect_snapshot_digest",
        "expect_session_revision",
    }


def test_apply_live_candidate_enforces_the_closed_object_it_advertises() -> None:
    """Advertising `additionalProperties: false` is not enforcing it.

    ``LiveApplyToolRequest`` is an ``Annotated[Any, WithJsonSchema(...)]``, so the SDK does not
    validate the wrapper: the ``call_tool`` guard is the only enforcement there is. Without it an
    extra top-level argument is silently discarded -- and the field most likely to be misplaced
    is ``apply_token``, which every document discusses at top level, so the caller would be told
    their request is missing the very field they sent.
    """

    import asyncio

    from mcp.server.mcpserver.exceptions import ToolError

    from copper_mcp.mcp_server import mcp

    with pytest.raises(ToolError, match="live apply tool arguments are malformed"):
        asyncio.run(mcp.call_tool("apply_live_candidate", {"request": {}, "smuggled": 1}))

    # The misplaced-token shape specifically, which is the reachable user-facing failure.
    with pytest.raises(ToolError, match="live apply tool arguments are malformed"):
        asyncio.run(
            mcp.call_tool("apply_live_candidate", {"request": {}, "apply_token": "at_top_level"})
        )


@pytest.mark.parametrize(
    "point",
    [
        pytest.param({"x_nm": 1, "y_nm": 2}, id="mapping-form"),
        pytest.param([1, 2], id="array-form"),
    ],
)
def test_a_via_center_is_accepted_in_both_serialized_forms(point: object) -> None:
    """`_candidate_document` emits vias as mappings and path vertices as arrays."""

    assert live_apply._point(point) == live_apply.PointNM(x=1, y=2)


@pytest.mark.parametrize(
    "point",
    [
        pytest.param("1,2", id="string"),
        pytest.param([1, 2, 3], id="wrong-arity"),
        pytest.param({"x_nm": True, "y_nm": 2}, id="boolean-coordinate"),
        pytest.param({"x_nm": 1.5, "y_nm": 2}, id="float-coordinate"),
        pytest.param({"x_nm": 1}, id="missing-coordinate"),
    ],
)
def test_a_malformed_geometry_point_is_refused(point: object) -> None:
    """`bool` is an `int` in Python, so it is excluded before the integer test, not after."""

    with pytest.raises(RequestError):
        live_apply._point(point)


def test_a_non_list_geometry_collection_is_refused() -> None:
    with pytest.raises(RequestError):
        live_apply._bounded_list("candidate.patch.paths", {"0": []}, 4)


@pytest.mark.parametrize("value", [True, 1.0, "1", None])
def test_a_non_integer_candidate_field_is_refused(value: object) -> None:
    with pytest.raises(RequestError):
        live_apply._integer("candidate.seed", value)


def test_an_out_of_range_candidate_integer_is_refused() -> None:
    with pytest.raises(RequestError):
        live_apply._integer("candidate.seed", 2**53)


def test_a_via_carrying_manifest_is_reconstructed_field_for_field(tmp_path: Path) -> None:
    """The two-pad fixture routes without vias, so the via branch needs its own case."""

    authority = ApplyTokenAuthority()
    document = dict(_preview(tmp_path, authority)["candidate"])
    patch = dict(document["patch"])
    layers = sorted({path["layer_id"] for path in patch["paths"]})
    other = "layer:B.Cu" if layers[0] != "layer:B.Cu" else "layer:F.Cu"
    patch["vias"] = [
        {
            "id": "via:live-apply-test",
            "center_nm": {"x_nm": patch["paths"][0]["vertices_nm"][0][0], "y_nm": 0},
            "diameter_nm": 800_000,
            "drill_nm": 400_000,
            "start_layer_id": layers[0],
            "end_layer_id": other,
        }
    ]
    document["patch"] = patch
    # The candidate's own validators cross-check via accounting, so the manifest has to be
    # internally consistent before the reconstruction can be inspected at all.
    document["cost"] = dict(
        document["cost"],
        via_count=1,
        via_cost_units=document["settings"]["via_cost"],
    )
    document["metrics"] = dict(document["metrics"], vias=1)
    candidate = live_apply.layered_candidate_from_document(document)
    via = candidate.patch.vias[0]
    assert via.id == "via:live-apply-test"
    assert (via.diameter_nm, via.drill_nm) == (800_000, 400_000)
    assert (via.start_layer_id, via.end_layer_id) == (layers[0], other)

    # And a manifest whose via accounting does not add up is refused by those same validators,
    # which is why this reconstruction does not have to re-derive them itself.
    with pytest.raises(ValueError, match="via accounting"):
        live_apply.layered_candidate_from_document(
            dict(document, metrics=dict(document["metrics"], vias=0))
        )


def test_a_fill_bound_layered_manifest_fails_closed_at_the_reconstruction(tmp_path: Path) -> None:
    """Live apply holds no fill evidence, so a fill-bound candidate must never verify here.

    ``layered_candidate_from_document`` ignores keys it does not read, and its docstring argues
    that is safe because the reconstruction is re-hashed and compared.  Adding ``fill_binding``
    to the canonical identity (ADR-0106) is what makes that argument load-bearing rather than
    merely true: the binding *is* part of the address now, so a manifest that claims one rebuilds
    without it and fails its own identity recomputation.  Nothing had to be added to this seam,
    and this test is what makes the absence evidence -- it could report a presence.
    """

    document = dict(_preview(tmp_path, ApplyTokenAuthority())["candidate"])
    envelope = live_apply.layered_candidate_from_document(document)
    assert envelope.fill_binding is None

    bound = replace(envelope, fill_binding=f"sha256:{'d' * 64}")
    bound = replace(
        bound,
        candidate_id=(
            f"sha256:{hashlib.sha256(canonical_layered_candidate_bytes(bound)).hexdigest()}"
        ),
    )
    assert bound.candidate_id != envelope.candidate_id
    assert verify_layered_candidate_id(bound)

    rebuilt = live_apply.layered_candidate_from_document(
        dict(document, fill_binding=bound.fill_binding, candidate_id=bound.candidate_id)
    )

    assert rebuilt.fill_binding is None
    with pytest.raises(ValueError, match="does not match canonical route content"):
        verify_layered_candidate_id(rebuilt)
