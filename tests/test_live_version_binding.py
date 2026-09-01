"""ADR-0128's live API version binding, exercised in both directions on every live surface.

Every surface that opens an IPC session gets the same four cases: an exact version match must
publish ``compatible``; a newer editor and an older editor must each be *observed* and carry a
verdict naming the direction; and a pair spanning a major boundary must be refused with both
versions in the message.  The older direction is the one this project shipped wrong -- until
ADR-0128 a KiCad a whole major behind the binding was published as ``compatible`` -- so its
tests are written as regressions rather than as new coverage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import copper_mcp.kicad_ipc as kicad_ipc
import copper_mcp.kicad_ipc_oracle as kicad_ipc_oracle
from copper_mcp.config import Settings
from copper_mcp.kicad_ipc import (
    ACCEPTED_API_COMPATIBILITY,
    VERIFIED_API_COMPATIBILITY,
    KicadIpcVersionError,
    capture_live_editor_context,
    classify_api_compatibility,
    inspect_live_board,
)
from copper_mcp.live_editor_context import inspect_live_editor_context_raw

ROOT = Path(__file__).resolve().parents[1]
SESSION_TOKEN = "copper-mcp-test-kicad-session"
EDITOR_INSTANCE_TOKEN = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"

# A board small enough to convert quickly and complete enough to reach the editor-context reads.
BOARD = '(kicad_pcb (net 1 "N") (footprint "F" (pad "1" (net 1 "N"))))'


@pytest.fixture(autouse=True)
def _fake_kicad_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KICAD_API_TOKEN", SESSION_TOKEN)
    monkeypatch.setattr(kicad_ipc, "_SESSION_REVISION_SALT", b"\x42" * 32)


class _Version:
    def __init__(self, major: int, minor: int, patch: int) -> None:
        self.major = major
        self.minor = minor
        self.patch = patch


class FutureVersionError(Exception):
    """Stands in for ``kipy.errors.FutureVersionError``, matched by class *name*."""


class _Board:
    def __init__(self, source: str = BOARD) -> None:
        self.source = source

    def get_as_string(self) -> str:
        return self.source

    def get_active_layer(self) -> int:
        return 3

    def get_layer_name(self, layer: int) -> str:
        return "F.Cu"

    def get_selection(self) -> list[object]:
        return []


class _KipyClient:
    def __init__(self, instance_token: str) -> None:
        self._kicad_token = instance_token


class _KiCad:
    """A fake whose ``check_version`` reproduces ``kipy`` 0.7.1's exact asymmetry.

    This matters more than it looks: the binding raises only when the editor is strictly newer
    on the ``(major, minor, patch)`` tuple, and returns ``True`` for every older editor.  A fake
    that refused both directions would make the legacy-direction tests below pass for the wrong
    reason and would hide the very defect they exist to pin.
    """

    def __init__(
        self,
        kicad: tuple[int, int, int],
        api: tuple[int, int, int],
        source: str = BOARD,
        instance_token: str = EDITOR_INSTANCE_TOKEN,
    ) -> None:
        self.kicad = kicad
        self.api = api
        self._board = _Board(source)
        self._client = _KipyClient(instance_token)

    def get_version(self) -> _Version:
        return _Version(*self.kicad)

    def get_api_version(self) -> _Version:
        return _Version(*self.api)

    def check_version(self) -> bool:
        if self.kicad > self.api:
            raise FutureVersionError("connected KiCad is newer")
        return True

    def get_board(self) -> _Board:
        return self._board


def _factory(kicad: tuple[int, int, int], api: tuple[int, int, int], **kwargs: Any) -> Any:
    def factory(**_: object) -> _KiCad:
        return _KiCad(kicad, api, **kwargs)

    return factory


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"workspace": ROOT, "allow_live_ipc": True}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# The policy itself, as a pure function.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kicad", "api", "expected"),
    [
        # Exact match is the only verified verdict.
        ("10.0.1", "10.0.1", "compatible"),
        ("9.0.0", "9.0.0", "compatible"),
        # Newer editor, same major: KiCad's field-meaning guarantee covers the read.
        ("10.0.5", "10.0.1", "future_api_unverified"),  # the pair B-138 measured
        ("10.0.2", "10.0.1", "future_api_unverified"),
        ("10.1.0", "10.0.1", "future_api_unverified"),
        # Older editor, same major: no guarantee covers this, and it is not "compatible".
        ("10.0.0", "10.0.1", "legacy_api_unverified"),
        ("10.0.1", "10.1.0", "legacy_api_unverified"),
    ],
)
def test_the_window_classifies_every_same_major_pair(kicad: str, api: str, expected: str) -> None:
    assert classify_api_compatibility(kicad, api) == expected
    assert expected in ACCEPTED_API_COMPATIBILITY


@pytest.mark.parametrize(
    ("kicad", "api"),
    [
        ("9.0.0", "10.0.1"),  # the pair kipy accepts silently and this project published as OK
        ("8.0.4", "10.0.1"),
        ("11.0.0", "10.0.1"),
        ("10.0.1", "9.0.8"),
    ],
)
def test_a_major_boundary_is_refused_and_the_refusal_names_both_versions(
    kicad: str, api: str
) -> None:
    with pytest.raises(KicadIpcVersionError) as error:
        classify_api_compatibility(kicad, api)
    message = str(error.value)
    assert kicad in message
    assert api in message
    assert "major" in message


def test_only_an_exact_match_is_verified() -> None:
    """The distinction the whole ADR exists to preserve, asserted as a set relation."""

    assert VERIFIED_API_COMPATIBILITY == {"compatible"}
    assert VERIFIED_API_COMPATIBILITY < ACCEPTED_API_COMPATIBILITY
    assert classify_api_compatibility("10.0.5", "10.0.1") not in VERIFIED_API_COMPATIBILITY
    assert classify_api_compatibility("10.0.0", "10.0.1") not in VERIFIED_API_COMPATIBILITY


# --------------------------------------------------------------------------------------------
# The binding's own check must agree with the reported versions.
# --------------------------------------------------------------------------------------------


def test_a_future_error_from_a_non_future_version_pair_is_refused() -> None:
    """An editor reporting one thing and the binding concluding another is not observable."""

    class _Inconsistent(_KiCad):
        def check_version(self) -> bool:
            raise FutureVersionError("claims future on an exact match")

    with pytest.raises(KicadIpcVersionError, match="inconsistent"):
        inspect_live_board(
            _settings(), client_factory=lambda **_: _Inconsistent((10, 0, 1), (10, 0, 1))
        )


def test_a_passing_check_on_a_future_version_pair_is_refused() -> None:
    class _Inconsistent(_KiCad):
        def check_version(self) -> bool:
            return True

    with pytest.raises(KicadIpcVersionError, match="inconsistent"):
        inspect_live_board(
            _settings(), client_factory=lambda **_: _Inconsistent((10, 0, 5), (10, 0, 1))
        )


def test_a_non_future_binding_failure_is_still_a_version_refusal() -> None:
    class _Broken(_KiCad):
        def check_version(self) -> bool:
            raise RuntimeError("binding fault")

    with pytest.raises(KicadIpcVersionError, match="validation failed"):
        inspect_live_board(_settings(), client_factory=lambda **_: _Broken((10, 0, 1), (10, 0, 1)))


# --------------------------------------------------------------------------------------------
# Surface 1: inspect_live_board.
# --------------------------------------------------------------------------------------------


def test_inspect_live_board_publishes_a_verified_verdict_on_an_exact_match() -> None:
    observation = inspect_live_board(_settings(), client_factory=_factory((10, 0, 1), (10, 0, 1)))
    assert observation.compatibility == "compatible"
    assert observation.to_dict()["compatibility"] == "compatible"


def test_inspect_live_board_observes_a_future_editor_with_a_typed_disclosure() -> None:
    """B-138's headline case: this pairing used to be a refusal on the default path."""

    observation = inspect_live_board(_settings(), client_factory=_factory((10, 0, 5), (10, 0, 1)))
    assert observation.compatibility == "future_api_unverified"
    assert observation.kicad_version == "10.0.5"
    assert observation.api_version == "10.0.1"


def test_inspect_live_board_no_longer_calls_an_older_editor_compatible() -> None:
    """Regression for the direction that was silently wrong rather than loudly refused."""

    observation = inspect_live_board(_settings(), client_factory=_factory((10, 0, 0), (10, 0, 1)))
    assert observation.compatibility == "legacy_api_unverified"
    assert observation.compatibility not in VERIFIED_API_COMPATIBILITY


def test_inspect_live_board_refuses_across_a_major_boundary() -> None:
    with pytest.raises(KicadIpcVersionError) as error:
        inspect_live_board(_settings(), client_factory=_factory((9, 0, 0), (10, 0, 1)))
    assert "9.0.0" in str(error.value)
    assert "10.0.1" in str(error.value)


# --------------------------------------------------------------------------------------------
# Surface 2: the editor-context capture and its MCP-shaped wrapper.
# --------------------------------------------------------------------------------------------


def test_editor_context_capture_publishes_the_same_verdict_as_the_board_surface() -> None:
    snapshot = capture_live_editor_context(
        _settings(), client_factory=_factory((10, 0, 5), (10, 0, 1))
    )
    assert snapshot.compatibility == "future_api_unverified"
    assert snapshot.kicad_version == "10.0.5"
    assert snapshot.api_version == "10.0.1"


def test_editor_context_capture_refuses_across_a_major_boundary() -> None:
    with pytest.raises(KicadIpcVersionError):
        capture_live_editor_context(_settings(), client_factory=_factory((9, 0, 0), (10, 0, 1)))


@pytest.mark.parametrize(
    ("kicad", "api", "expected"),
    [
        ((10, 0, 1), (10, 0, 1), "compatible"),
        ((10, 0, 5), (10, 0, 1), "future_api_unverified"),
        ((10, 0, 0), (10, 0, 1), "legacy_api_unverified"),
    ],
)
def test_the_mcp_editor_context_surface_reaches_a_real_editor_and_discloses_the_verdict(
    kicad: tuple[int, int, int], api: tuple[int, int, int], expected: str
) -> None:
    """The asymmetry B-138 named: this surface could not observe 10.0.5 at all, and even on
    its internal accept path it published no verdict for a caller to read."""

    factory = _factory(kicad, api)
    revision = capture_live_editor_context(_settings(), client_factory=factory).board_digest
    context = inspect_live_editor_context_raw(
        {"board": "live", "expect_board_revision": revision},
        _settings(),
        client_factory=factory,
    ).to_dict()
    assert context["compatibility"] == expected
    assert context["kicad_version"] == ".".join(str(part) for part in kicad)
    assert context["api_version"] == ".".join(str(part) for part in api)


def test_the_mcp_editor_context_surface_refuses_across_a_major_boundary() -> None:
    with pytest.raises(KicadIpcVersionError):
        inspect_live_editor_context_raw(
            {"board": "live", "expect_board_revision": "sha256:" + "0" * 64},
            _settings(),
            client_factory=_factory((9, 0, 0), (10, 0, 1)),
        )


# --------------------------------------------------------------------------------------------
# Defect (a): the count whose name did not describe the quantity.
# --------------------------------------------------------------------------------------------


def test_the_net_count_is_named_for_what_it_counts_and_the_old_name_is_gone() -> None:
    """B-138 measured ``nets: 0`` against an editor holding 15 nets. The count was right and
    the name was not, so the name moved and no invented cardinality replaced it."""

    observation = inspect_live_board(
        _settings(),
        client_factory=_factory((10, 0, 1), (10, 0, 1), source='(kicad_pcb (footprint "F"))'),
    )
    counts = observation.to_dict()["object_counts"]
    assert counts["net_declarations"] == 0
    assert "nets" not in counts


def test_a_root_net_declaration_is_still_counted() -> None:
    """Not vacuous: the renamed key still counts the thing it always counted."""

    observation = inspect_live_board(
        _settings(),
        client_factory=_factory(
            (10, 0, 1), (10, 0, 1), source='(kicad_pcb (net 1 "N") (net 2 "M"))'
        ),
    )
    assert observation.to_dict()["object_counts"]["net_declarations"] == 2


# --------------------------------------------------------------------------------------------
# Defect (b): what the digest binds.
# --------------------------------------------------------------------------------------------


def test_both_live_surfaces_state_that_the_digest_binds_the_in_memory_document() -> None:
    factory = _factory((10, 0, 1), (10, 0, 1))
    observation = inspect_live_board(_settings(), client_factory=factory)
    assert observation.to_dict()["document_binding"] == "in_memory_unsaved_state_unobservable"

    revision = capture_live_editor_context(_settings(), client_factory=factory).board_digest
    context = inspect_live_editor_context_raw(
        {"board": "live", "expect_board_revision": revision},
        _settings(),
        client_factory=factory,
    ).to_dict()
    assert context["document_binding"] == "in_memory_unsaved_state_unobservable"


def test_no_live_surface_publishes_a_saved_state_claim() -> None:
    """The API exposes no dirty flag, so no field may imply one. This pins the absence."""

    observation = inspect_live_board(
        _settings(), client_factory=_factory((10, 0, 1), (10, 0, 1))
    ).to_dict()
    forbidden = ("saved", "dirty", "modified", "unsaved_changes", "file_digest", "board_path")
    for name in forbidden:
        assert name not in observation, f"{name} would claim something the API cannot report"


# --------------------------------------------------------------------------------------------
# The acceptance guards, made load-bearing.
#
# These three constructors are the last place a verdict can be checked before it reaches a
# caller. Nothing in normal operation builds an invalid one -- the classifier only ever returns
# members of the accepted set -- so without these tests the guards are unexercised, and a
# mutation run says so by surviving their removal. They exist for the widening that has not
# happened yet: a fourth verdict added to one surface and not to the others.
# --------------------------------------------------------------------------------------------


def _observation(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "kicad_version": "10.0.1",
        "api_version": "10.0.1",
        "compatibility": "compatible",
        "board_digest": "sha256:" + "0" * 64,
        "board_bytes": 32,
        "object_counts": {"net_declarations": 0},
        "socket_kind": "default-local-ipc",
    }
    values.update(overrides)
    return kicad_ipc.LiveBoardObservation(**values)


@pytest.mark.parametrize(
    "verdict", ["definitely_compatible", "unverified", "", "COMPATIBLE", "future_api"]
)
def test_an_observation_refuses_a_verdict_outside_the_accepted_set(verdict: str) -> None:
    with pytest.raises(kicad_ipc.KicadIpcError, match="compatibility is invalid"):
        _observation(compatibility=verdict)


def test_an_observation_refuses_a_document_binding_it_did_not_declare() -> None:
    with pytest.raises(kicad_ipc.KicadIpcError, match="document binding is invalid"):
        _observation(document_binding="saved_and_clean")


def test_every_accepted_verdict_actually_constructs() -> None:
    """Not vacuous: the guard rejects what is outside the set and nothing that is inside it."""

    for verdict in ACCEPTED_API_COMPATIBILITY:
        assert _observation(compatibility=verdict).compatibility == verdict


def test_the_editor_snapshot_refuses_a_verdict_outside_the_accepted_set() -> None:
    with pytest.raises(kicad_ipc.KicadIpcPayloadError, match="compatibility is invalid"):
        kicad_ipc.LiveEditorContextSnapshot(
            board_digest="sha256:" + "0" * 64,
            board_bytes=32,
            active_layer_index=3,
            active_layer_name="F.Cu",
            selection=(),
            compatibility="probably_fine",
        )


def test_the_oracle_refuses_a_verdict_the_observation_boundary_could_not_produce() -> None:
    """The oracle republishes the observation's verdict, so it revalidates rather than trusts."""

    with pytest.raises(ValueError, match="compatibility is invalid"):
        kicad_ipc_oracle.LiveIpcOracleResult(
            status="skipped",
            capability="kicad_plugin_environment_absent",
            socket_configured=False,
            token_configured=False,
            compatibility="probably_fine",
        )
