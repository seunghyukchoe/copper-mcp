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
from pathlib import Path
from typing import Any

import pytest

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

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
SESSION_TOKEN = "copper-mcp-test-kicad-session"
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


class _FakeVersion:
    major = 10
    minor = 0
    patch = 5


class _FakeLiveBoard:
    def __init__(self, source: str) -> None:
        self._source = source

    def get_as_string(self) -> str:
        return self._source


class _FakeLiveKiCad:
    def __init__(self, source: str) -> None:
        self._board = _FakeLiveBoard(source)

    def get_version(self) -> _FakeVersion:
        return _FakeVersion()

    def get_api_version(self) -> _FakeVersion:
        return _FakeVersion()

    def check_version(self) -> bool:
        return True

    def get_board(self) -> _FakeLiveBoard:
        return self._board


def _client_factory(source: bytes) -> Any:
    text = source.decode("utf-8")

    def factory(**_: object) -> _FakeLiveKiCad:
        return _FakeLiveKiCad(text)

    return factory


def _session_revision() -> str:
    revision = kicad_ipc._session_revision()
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
) -> dict[str, Any]:
    live_source = source if source is not None else FIXTURE.read_bytes()
    return dict(
        apply_live_candidate(
            request,
            settings if settings is not None else _enabled(tmp_path),
            authority,
            client_factory=_client_factory(live_source),
        )
    )


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


def test_a_restarted_editor_is_refused_as_a_stale_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, authority))
    # A fresh editor process gets a fresh `KICAD_API_TOKEN`. The board bytes may be byte-identical
    # and the capability still must not carry over: it is not the same document.
    monkeypatch.setenv("KICAD_API_TOKEN", "a-different-kicad-instance")
    result = _apply(tmp_path, request, authority)
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == LiveApplyFailureCode.STALE_SESSION.value
    assert (
        LiveApplyPrecondition.LIVE_BOARD_REVISION_BOUND.value
        not in (result["preconditions_verified"])
    )


def test_a_board_with_no_reachable_session_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = ApplyTokenAuthority()
    request = _authorized_request(_preview(tmp_path, authority))
    monkeypatch.delenv("KICAD_API_TOKEN", raising=False)
    result = _apply(tmp_path, request, authority)
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
        (kicad_ipc.KicadIpcDeadlineError("expired"), LiveApplyFailureCode.DEADLINE_EXPIRED),
        (
            kicad_ipc.KicadIpcConnectionError("closed"),
            LiveApplyFailureCode.LIVE_EDITOR_UNAVAILABLE,
        ),
        (kicad_ipc.KicadIpcDisabledError("off"), LiveApplyFailureCode.LIVE_IPC_DISABLED),
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
