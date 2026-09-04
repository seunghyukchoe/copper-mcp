"""Why a preview withheld an apply token, on every surface that can withhold one.

`R-149`: a caller receiving `apply_token: null` could not tell which of six causes produced it,
and one of the six was a `KiCadPlacementPatchError` swallowed by a bare `except ... : pass`. The
repair is a closed literal set defined once in `copper_mcp.apply_token_reasons` and emitted on
every surface, so these tests are organised around the two things that can go wrong with such a
set: it can be **open** (a surface withholds for a reason nobody wrote down, or forgets to say
anything at all), and it can be **leaky** (a reason carries something about the board).

Every reason is reached here by a constructed request rather than by patching the branch under
test, which is what the audit's `E1` asks for: "each reason in the set is reachable by a
constructed request, and the set is closed."
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import copper_mcp.kicad_ipc as kicad_ipc
import copper_mcp.route_preview as route_preview
from copper_mcp.adapters import KiCadConstraintProfile, net_id_for_name, parse_kicad_bytes
from copper_mcp.apply.tokens import ApplyTokenAuthority
from copper_mcp.apply_token_reasons import (
    APPLY_TOKEN_WITHHELD_REASONS,
    apply_token_withheld_reason,
)
from copper_mcp.board_ir import NetClass, PointNM
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import ZoneFillAuthority
from copper_mcp.layered_route_preview import preview_layered_route
from copper_mcp.live_layered_route_preview import preview_live_layered_route
from copper_mcp.mcp_contracts import (
    LayeredRoutePreviewToolResponse,
    PlacementPreviewToolResponse,
    RoutePreviewToolResponse,
)
from copper_mcp.placement_preview import preview_live_placement, preview_placement
from copper_mcp.route_preview import preview_live_route, preview_route
from copper_mcp.zone_fill import FillIsland

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "copper_mcp"
ROUTE_FIXTURES = ROOT / "tests" / "fixtures" / "route-candidate"
ROUTE_FIXTURE = ROUTE_FIXTURES / "two-pad.kicad_pcb"
PLACEMENT_FIXTURE = ROOT / "tests" / "fixtures" / "placement-v0.1" / "placement-legal.kicad_pcb"

CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}
PLACEMENT_CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}
PLACEMENT_SUBJECTS = [
    "footprint:kicad:90000000-0000-0000-0000-000000000001",
    "footprint:kicad:90000000-0000-0000-0000-000000000003",
]
#: Removing this uuid leaves the board's Edge.Cuts outline with a content-derived Board IR
#: identity, which is exactly the property both append-only write-back paths refuse.
PLACEMENT_OUTLINE_UUID = b'\n    (uuid "90000000-0000-0000-0000-000000000005")'
ROUTE_OUTLINE_UUID = '\n    (uuid "20000000-0000-0000-0000-000000000005")'

SESSION_TOKEN = "copper-mcp-test-kicad-session"
EDITOR_INSTANCE_TOKEN = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"


# --- the set itself ----------------------------------------------------------------------


def test_the_set_is_exactly_the_eight_literals_and_nothing_derives_from_a_board() -> None:
    """A withheld reason is disclosed to whoever asked. It may say why, never about what.

    The pattern is the whole point: no digit, no dot, no slash, no colon. A reason that could
    carry a net name, a reference designator, a locator or a coordinate would turn a refusal
    into a side channel out of a private design.
    """

    assert APPLY_TOKEN_WITHHELD_REASONS == {
        "unsupported_surface",
        "not_requested",
        "apply_disabled",
        "no_candidate",
        "no_move",
        "board_not_appliable",
        "fill_bound_candidate",
        "replay_refused",
    }
    for reason in APPLY_TOKEN_WITHHELD_REASONS:
        assert re.fullmatch(r"[a-z]+(?:_[a-z]+)*", reason), reason


#: Every module that decides or serializes a withheld reason. None of them may spell a literal.
WITHHOLDING_MODULES = (
    "route_preview.py",
    "placement_preview.py",
    "placement/contracts.py",
    "layered_route_preview.py",
    "live_layered_route_preview.py",
)


def test_the_vocabulary_is_written_down_once() -> None:
    """The value set is derived from the type, so the two cannot drift apart.

    Two files legitimately spell `apply_disabled` for something else entirely:
    `apply/contracts.py` and the `ApplyDiagnosticContract` in `mcp_contracts.py`, where it is the
    *apply* surface's refusal code — a different field in a different response. Sharing the word
    is deliberate; a caller meeting both conditions should meet one name for them. Neither file
    restates the withheld-reason set, and `mcp_contracts.py` is checked separately below for
    reusing the `Literal` rather than retyping it.
    """

    source = (SRC / "apply_token_reasons.py").read_text(encoding="utf-8")
    for reason in APPLY_TOKEN_WITHHELD_REASONS:
        assert source.count(f'"{reason}"') == 2, (
            f"{reason} should appear exactly twice in the definition module - once in the "
            "Literal and once as the value the order function returns"
        )
    for module in WITHHOLDING_MODULES:
        text = (SRC / module).read_text(encoding="utf-8")
        for reason in APPLY_TOKEN_WITHHELD_REASONS:
            assert f'"{reason}"' not in text, (
                f"{module} spells the literal {reason!r} out for itself; every surface must "
                "read it out of the shared order instead, or the set is only a set by convention"
            )


def test_the_response_contracts_reuse_the_shared_literal_rather_than_restating_it() -> None:
    contracts = (SRC / "mcp_contracts.py").read_text(encoding="utf-8")

    assert "from copper_mcp.apply_token_reasons import ApplyTokenWithheldReason" in contracts
    assert contracts.count("apply_token_withheld_reason: ApplyTokenWithheldReason") == 11


def test_public_schemas_require_the_closed_reason_and_move_their_versions() -> None:
    route_schema = RoutePreviewToolResponse.model_json_schema()
    layered_schema = LayeredRoutePreviewToolResponse.model_json_schema()
    placement_schema = PlacementPreviewToolResponse.model_json_schema()

    for schema in (route_schema, layered_schema):
        for variant_ref in schema["anyOf"]:
            variant = schema["$defs"][variant_ref["$ref"].rsplit("/", 1)[-1]]
            assert variant["properties"]["schema_version"]["const"] == "1.1"
            assert {"apply_token", "apply_token_withheld_reason"} <= set(variant["required"])
            reason_schema = variant["properties"]["apply_token_withheld_reason"]
            alternatives = reason_schema.get("anyOf", [reason_schema])
            enum = next(item["enum"] for item in alternatives if "enum" in item)
            assert set(enum) == APPLY_TOKEN_WITHHELD_REASONS

    assert placement_schema["properties"]["placement_version"]["const"] == "0.2.0"
    assert {"apply_token", "apply_token_withheld_reason"} <= set(placement_schema["required"])
    placement_reason = placement_schema["properties"]["apply_token_withheld_reason"]
    enum = next(item["enum"] for item in placement_reason["anyOf"] if "enum" in item)
    assert set(enum) == APPLY_TOKEN_WITHHELD_REASONS


def test_no_reason_is_returned_when_every_condition_for_issuing_holds() -> None:
    assert (
        apply_token_withheld_reason(requested=True, apply_enabled=True, has_candidate=True) is None
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"surface_mints_tokens": False}, "unsupported_surface"),
        ({"requested": False}, "not_requested"),
        ({"apply_enabled": False}, "apply_disabled"),
        ({"has_candidate": False}, "no_candidate"),
        ({"candidate_moves": False}, "no_move"),
        ({"board_appliable": False}, "board_not_appliable"),
        ({"fill_bound": True}, "fill_bound_candidate"),
        ({"replay_accepted": False}, "replay_refused"),
    ],
)
def test_each_unmet_condition_names_its_own_reason(
    overrides: dict[str, bool], expected: str
) -> None:
    conditions: dict[str, bool] = {
        "requested": True,
        "apply_enabled": True,
        "has_candidate": True,
    }
    conditions.update(overrides)
    assert apply_token_withheld_reason(**conditions) == expected


def test_the_order_is_what_the_caller_can_act_on_first() -> None:
    """Precedence is not arbitrary: request, then server, then proposal, then board.

    With every condition unmet at once the answer is `unsupported_surface`, and removing the
    reasons one at a time walks the published order. A caller who did not ask for a token is
    told that and nothing about the board, which is the disclosure half of the same decision.
    """

    walk = []
    conditions: dict[str, bool] = {
        "surface_mints_tokens": False,
        "requested": False,
        "apply_enabled": False,
        "has_candidate": False,
        "candidate_moves": False,
        "board_appliable": False,
        "fill_bound": True,
        "replay_accepted": False,
    }
    relaxations = [
        ("surface_mints_tokens", True),
        ("requested", True),
        ("apply_enabled", True),
        ("has_candidate", True),
        ("candidate_moves", True),
        ("board_appliable", True),
        ("fill_bound", False),
        ("replay_accepted", True),
    ]
    for name, value in relaxations:
        walk.append(apply_token_withheld_reason(**conditions))
        conditions[name] = value
    assert walk == [
        "unsupported_surface",
        "not_requested",
        "apply_disabled",
        "no_candidate",
        "no_move",
        "board_not_appliable",
        "fill_bound_candidate",
        "replay_refused",
    ]
    assert apply_token_withheld_reason(**conditions) is None


# --- the surface census ------------------------------------------------------------------

#: Every place in `src/` that mints an apply token, from a `codebase-memory` inbound trace of
#: `ApplyTokenAuthority.issue` at `7b6d7aa` (index SHA
#: `7b6d7aa1cf40623d6d2e85fb75b615a6af46192c`). Three of the four are preview surfaces and are
#: covered below; `cli.py` mints its own capability for its own immediately-following apply and
#: returns no preview document, so it has nothing to withhold.
TOKEN_MINTING_MODULES = {
    "cli.py",
    "live_layered_route_preview.py",
    "placement_preview.py",
    "route_preview.py",
}

#: Every place in `src/` that writes an `"apply_token"` key into a document. `cli.py` writes one
#: into an apply *request* it is about to send to itself, which is why it is named and excluded
#: rather than silently missing.
APPLY_TOKEN_DOCUMENT_MODULES = {
    "cli.py": "builds an apply request, not a preview response",
    "layered_route_preview.py": "response document, shared with the live layered surface",
    "placement/contracts.py": "response document, shared with the live placement surface",
    "route_preview.py": "response document, shared with the live single-layer surface",
}


def _modules_matching(needle: str) -> set[str]:
    return {
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if needle in path.read_text(encoding="utf-8")
    }


def test_the_set_of_token_minting_modules_has_not_grown() -> None:
    """A new minting site is a new way to withhold, so it must not land unnoticed.

    This is the completeness half of the closed set. The literals can be exhaustive and still
    describe nothing if a fifth surface starts issuing capabilities without consulting them.
    """

    assert _modules_matching(".issue(") == TOKEN_MINTING_MODULES


def test_every_document_carrying_an_apply_token_key_carries_its_reason() -> None:
    assert _modules_matching('"apply_token": ') == set(APPLY_TOKEN_DOCUMENT_MODULES)
    for module, note in APPLY_TOKEN_DOCUMENT_MODULES.items():
        source = (SRC / module).read_text(encoding="utf-8")
        if module == "cli.py":
            assert "request" in note
            continue
        assert '"apply_token_withheld_reason": ' in source, module


# --- file-backed single-layer route -------------------------------------------------------


def _route_workspace(tmp_path: Path, source: bytes, *, allow_apply: bool) -> Settings:
    (tmp_path / "board.kicad_pcb").write_bytes(source)
    return replace(
        Settings(workspace=tmp_path.resolve(), max_drc_report_bytes=4096),
        allow_apply=allow_apply,
    )


def _route_request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "board": "board.kicad_pcb",
        "net": "AUDIO",
        "layer": "F.Cu",
        "seed": 23,
        "constraints": dict(CONSTRAINTS),
    }
    request.update(overrides)
    return request


def _route(tmp_path: Path, source: bytes, *, allow_apply: bool, **overrides: Any) -> dict[str, Any]:
    settings = _route_workspace(tmp_path, source, allow_apply=allow_apply)
    return preview_route(_route_request(**overrides), settings, ApplyTokenAuthority()).to_dict()


def test_route_issues_a_token_and_then_says_nothing_about_why_it_did_not(tmp_path: Path) -> None:
    document = _route(
        tmp_path, ROUTE_FIXTURE.read_bytes(), allow_apply=True, include_apply_token=True
    )

    assert document["status"] == "routed"
    assert document["apply_token"] is not None
    assert document["apply_token_withheld_reason"] is None


def test_route_says_not_requested_when_the_flag_is_off(tmp_path: Path) -> None:
    document = _route(tmp_path, ROUTE_FIXTURE.read_bytes(), allow_apply=True)

    assert document["status"] == "routed"
    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "not_requested"


def test_route_says_apply_disabled_when_the_operator_did_not_enable_apply(
    tmp_path: Path,
) -> None:
    document = _route(
        tmp_path, ROUTE_FIXTURE.read_bytes(), allow_apply=False, include_apply_token=True
    )

    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "apply_disabled"


def test_route_says_apply_disabled_when_no_authority_was_wired(tmp_path: Path) -> None:
    """The embedder half of the same answer: apply is on, but nothing can sign."""

    settings = _route_workspace(tmp_path, ROUTE_FIXTURE.read_bytes(), allow_apply=True)

    document = preview_route(_route_request(include_apply_token=True), settings).to_dict()

    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "apply_disabled"


def test_route_says_no_candidate_when_the_net_is_already_connected(tmp_path: Path) -> None:
    connected = (ROUTE_FIXTURES / "connected-net.kicad_pcb").read_bytes()

    document = _route(tmp_path, connected, allow_apply=True, include_apply_token=True)

    assert document["status"] == "already_connected"
    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "no_candidate"


def test_route_says_no_candidate_when_the_board_is_stale(tmp_path: Path) -> None:
    document = _route(
        tmp_path,
        ROUTE_FIXTURE.read_bytes(),
        allow_apply=True,
        include_apply_token=True,
        expect_board_revision="sha256:" + "0" * 64,
    )

    assert document["status"] == "not_routed"
    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "no_candidate"


def test_route_says_board_not_appliable_for_a_derived_identity_board(tmp_path: Path) -> None:
    """The board routes; the append-only engine could never write to it (ADR-0025).

    Not vacuous: the byte-identical board *with* its outline uuid mints a token under the same
    request, so this distinguishes the board rather than the request.
    """

    text = ROUTE_FIXTURE.read_text(encoding="utf-8")
    derived = text.replace(ROUTE_OUTLINE_UUID, "")
    assert derived != text

    document = _route(tmp_path, derived.encode("utf-8"), allow_apply=True, include_apply_token=True)

    assert document["status"] == "routed"
    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "board_not_appliable"


def test_route_says_fill_bound_candidate_for_a_candidate_the_pour_shaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#163 / ADR-0103, now legible: the token was already withheld, silently.

    Apply replays in a later process holding no fill evidence, so a candidate the exact pour
    shaped can only refuse there. The same board routed against the conservative envelope still
    mints a token, so the difference is the fill binding and not the board.
    """

    fixture = ROUTE_FIXTURES / "blocked-zone.kicad_pcb"
    source = fixture.read_bytes()
    settings = _route_workspace(tmp_path, source, allow_apply=True)
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"
    authority = ZoneFillAuthority(
        source_revision=board_revision,
        context_revision=f"sha256:{'a' * 64}",
        source_fill_digest=f"sha256:{'b' * 64}",
        refilled_fill_digest=f"sha256:{'b' * 64}",
        kicad_version="10.0.5",
        fill_polygon_count=1,
        fill_vertex_count=4,
    )
    island = FillIsland(
        net_id=net_id_for_name("POWER"),
        layer_id="layer:F.Cu",
        points=(
            PointNM(18_000_000, 11_000_000),
            PointNM(22_000_000, 11_000_000),
            PointNM(22_000_000, 14_000_000),
            PointNM(18_000_000, 14_000_000),
        ),
    )
    monkeypatch.setattr(route_preview, "run_zone_fill_authority", lambda *_: (authority, (island,)))
    token_authority = ApplyTokenAuthority()

    fill_routed = preview_route(
        _route_request(include_fill_authority=True, include_apply_token=True),
        settings,
        token_authority,
    ).to_dict()
    envelope = preview_route(
        _route_request(include_apply_token=True), settings, token_authority
    ).to_dict()

    assert fill_routed["status"] == "routed"
    assert fill_routed["apply_token"] is None
    assert fill_routed["apply_token_withheld_reason"] == "fill_bound_candidate"
    assert envelope["apply_token"] is not None
    assert envelope["apply_token_withheld_reason"] is None


# --- live single-layer route --------------------------------------------------------------


class _FakeVersion:
    major = 10
    minor = 0
    patch = 5


class _FakeLiveBoard:
    def __init__(self, source: str) -> None:
        self._source = source

    def get_as_string(self) -> str:
        return self._source


class _FakeKipyClient:
    def __init__(self, instance_token: str) -> None:
        self._kicad_token = instance_token


class _FakeLiveKiCad:
    def __init__(self, source: str, instance_token: str = EDITOR_INSTANCE_TOKEN) -> None:
        self._board = _FakeLiveBoard(source)
        self._client = _FakeKipyClient(instance_token)

    def get_version(self) -> _FakeVersion:
        return _FakeVersion()

    def get_api_version(self) -> _FakeVersion:
        return _FakeVersion()

    def check_version(self) -> bool:
        return True

    def get_board(self) -> _FakeLiveBoard:
        return self._board


def _live_factory(source: bytes) -> Any:
    text = source.decode("utf-8")
    return lambda **_: _FakeLiveKiCad(text)


@pytest.fixture
def _fake_kicad_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KICAD_API_TOKEN", SESSION_TOKEN)
    monkeypatch.setattr(kicad_ipc, "_SESSION_REVISION_SALT", b"\x42" * 32)


def _snapshot_of(source: bytes, constraints: dict[str, int]) -> Any:
    profile = KiCadConstraintProfile(
        net_classes=(NetClass(id="class:request", name="Request", **constraints),),
        default_net_class_id="class:request",
    )
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    return conversion.snapshot


def test_live_route_says_unsupported_surface_because_it_mints_nothing(tmp_path: Path) -> None:
    """`not_requested` would be the wrong answer here: asking is refused at the parser.

    A caller told `not_requested` may reasonably ask again with the flag set. On this surface
    that request is rejected outright, and the closed set is what lets the response say so.
    """

    source = ROUTE_FIXTURE.read_bytes()
    snapshot = _snapshot_of(source, CONSTRAINTS)
    settings = replace(
        Settings(workspace=tmp_path.resolve()), allow_live_ipc=True, allow_apply=True
    )
    request = _route_request(
        board="live",
        net=None,
        net_ref_id=net_id_for_name("AUDIO"),
        expect_board_revision=f"sha256:{hashlib.sha256(source).hexdigest()}",
        expect_snapshot_digest=snapshot.snapshot_digest,
    )
    request.pop("net")

    document = preview_live_route(request, settings, client_factory=_live_factory(source)).to_dict()

    assert document["status"] == "routed"
    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "unsupported_surface"


# --- file-backed placement ----------------------------------------------------------------


def _placement(source: bytes, *, allow_apply: bool = True, **overrides: Any) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        (workspace / "board.kicad_pcb").write_bytes(source)
        settings = replace(Settings(workspace=workspace.resolve()), allow_apply=allow_apply)
        request: dict[str, Any] = {
            "board": "board.kicad_pcb",
            "constraints": dict(PLACEMENT_CONSTRAINTS),
            "subjects": list(PLACEMENT_SUBJECTS),
        }
        request.update(overrides)
        return preview_placement(request, settings, ApplyTokenAuthority()).to_dict()


def _moved() -> list[dict[str, Any]]:
    return [{"subject": PLACEMENT_SUBJECTS[0], "offset_x_nm": 1_000_000}]


def test_placement_issues_a_token_and_then_says_nothing_about_why_it_did_not() -> None:
    document = _placement(
        PLACEMENT_FIXTURE.read_bytes(), include_apply_token=True, proposals=_moved()
    )

    assert document["status"] == "previewed"
    assert document["apply_token"] is not None
    assert document["apply_token_withheld_reason"] is None


def test_placement_says_not_requested_when_the_flag_is_off() -> None:
    document = _placement(PLACEMENT_FIXTURE.read_bytes(), proposals=_moved())

    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "not_requested"


def test_placement_says_apply_disabled_when_the_operator_did_not_enable_apply() -> None:
    document = _placement(
        PLACEMENT_FIXTURE.read_bytes(),
        allow_apply=False,
        include_apply_token=True,
        proposals=_moved(),
    )

    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "apply_disabled"


def test_placement_says_no_move_when_the_candidate_writes_the_board_it_read() -> None:
    document = _placement(PLACEMENT_FIXTURE.read_bytes(), include_apply_token=True)

    assert document["status"] == "previewed"
    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "no_move"


def test_placement_says_replay_refused_and_that_used_to_be_the_swallowed_exception() -> None:
    """`R-149`'s exact branch. It was `except KiCadPlacementPatchError: pass`.

    A derived-identity board previews a moved candidate perfectly well and then fails the
    source-preserving replay the token gate runs first. Before this change the refusal was
    discarded and the caller saw a `null` indistinguishable from five other outcomes. The
    reason names *which check refused*, never what the check found in the board.
    """

    source = PLACEMENT_FIXTURE.read_bytes()
    derived = source.replace(PLACEMENT_OUTLINE_UUID, b"")
    assert derived != source

    refused = _placement(derived, include_apply_token=True, proposals=_moved())
    intact = _placement(source, include_apply_token=True, proposals=_moved())

    assert refused["status"] == "previewed", "the candidate is still previewable"
    assert refused["apply_token"] is None
    assert refused["apply_token_withheld_reason"] == "replay_refused"
    assert intact["apply_token"] is not None, "and the intact board still mints a token"


def test_placement_says_no_candidate_when_the_legalizer_refused() -> None:
    overlap = (PLACEMENT_FIXTURE.parent / "placement-overlap.kicad_pcb").read_bytes()

    document = _placement(overlap, include_apply_token=True, proposals=_moved())

    assert document["status"] == "refused"
    assert document["candidate"] is None
    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "no_candidate"


def test_live_placement_says_unsupported_surface(tmp_path: Path) -> None:
    source = PLACEMENT_FIXTURE.read_bytes()
    snapshot = _snapshot_of(source, PLACEMENT_CONSTRAINTS)
    settings = replace(Settings(workspace=tmp_path.resolve()), allow_live_ipc=True)
    request = {
        "board": "live",
        "constraints": dict(PLACEMENT_CONSTRAINTS),
        "subjects": list(PLACEMENT_SUBJECTS),
        "expect_board_revision": f"sha256:{hashlib.sha256(source).hexdigest()}",
        "expect_snapshot_digest": snapshot.snapshot_digest,
    }

    document = preview_live_placement(
        request, settings, client_factory=_live_factory(source)
    ).to_dict()

    assert document["status"] == "previewed"
    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "unsupported_surface"


# --- layered route, file-backed and live --------------------------------------------------


def _layered_request(source: bytes, **overrides: Any) -> dict[str, Any]:
    snapshot = _snapshot_of(source, CONSTRAINTS)
    pads = snapshot.content.pads
    request: dict[str, Any] = {
        "board": "board.kicad_pcb",
        "start_pad_id": pads[0].id,
        "end_pad_id": pads[1].id,
        "constraints": dict(CONSTRAINTS),
        "expect_board_revision": f"sha256:{hashlib.sha256(source).hexdigest()}",
        "expect_snapshot_digest": snapshot.snapshot_digest,
        "grid_step_nm": 250_000,
        "seed": 23,
    }
    request.update(overrides)
    return request


def test_file_backed_layered_route_says_unsupported_surface_on_every_outcome(
    tmp_path: Path,
) -> None:
    """This seam mints nothing under any setting, routed or refused, apply on or off."""

    source = ROUTE_FIXTURE.read_bytes()
    (tmp_path / "board.kicad_pcb").write_bytes(source)
    settings = replace(Settings(workspace=tmp_path.resolve()), allow_apply=True)

    routed = preview_layered_route(_layered_request(source), settings)
    stale = preview_layered_route(
        _layered_request(source, expect_board_revision="sha256:" + "0" * 64), settings
    )

    assert routed["status"] == "routed"
    assert stale["status"] == "not_routed"
    for document in (routed, stale):
        assert document["apply_token"] is None
        assert document["apply_token_withheld_reason"] == "unsupported_surface"


def _live_layered(
    tmp_path: Path,
    *,
    allow_live_apply: bool,
    authority: ApplyTokenAuthority | None,
    **overrides: Any,
) -> dict[str, Any]:
    source = ROUTE_FIXTURE.read_bytes()
    session_revision = kicad_ipc._session_revision(EDITOR_INSTANCE_TOKEN)
    assert session_revision is not None
    request = _layered_request(
        source,
        board="live",
        expect_session_revision=session_revision,
    )
    request.update(overrides)
    settings = Settings(
        workspace=tmp_path.resolve(),
        allow_live_ipc=True,
        allow_live_apply=allow_live_apply,
    )
    return dict(
        preview_live_layered_route(
            request, settings, authority, client_factory=_live_factory(source)
        )
    )


@pytest.mark.usefixtures("_fake_kicad_session")
def test_live_layered_issues_a_token_and_then_says_nothing_about_why_it_did_not(
    tmp_path: Path,
) -> None:
    document = _live_layered(
        tmp_path,
        allow_live_apply=True,
        authority=ApplyTokenAuthority(),
        include_apply_token=True,
    )

    assert document["status"] == "routed"
    assert document["apply_token"] is not None
    assert document["apply_token_withheld_reason"] is None


@pytest.mark.usefixtures("_fake_kicad_session")
@pytest.mark.parametrize(
    ("allow_live_apply", "authority", "requested", "expected"),
    [
        (True, True, False, "not_requested"),
        (False, True, True, "apply_disabled"),
        (True, False, True, "apply_disabled"),
    ],
)
def test_live_layered_names_its_request_and_operator_refusals(
    tmp_path: Path,
    allow_live_apply: bool,
    authority: bool,
    requested: bool,
    expected: str,
) -> None:
    document = _live_layered(
        tmp_path,
        allow_live_apply=allow_live_apply,
        authority=ApplyTokenAuthority() if authority else None,
        include_apply_token=requested,
    )

    assert document["status"] == "routed"
    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == expected


@pytest.mark.usefixtures("_fake_kicad_session")
def test_live_layered_says_no_candidate_when_the_snapshot_is_stale(tmp_path: Path) -> None:
    document = _live_layered(
        tmp_path,
        allow_live_apply=True,
        authority=ApplyTokenAuthority(),
        include_apply_token=True,
        expect_snapshot_digest="sha256:" + "1" * 64,
    )

    assert document["status"] == "not_routed"
    assert document["apply_token"] is None
    assert document["apply_token_withheld_reason"] == "no_candidate"


# --- the invariant, stated once more at the boundary --------------------------------------


ALL_SURFACES = (
    "route",
    "live_route",
    "placement",
    "live_placement",
    "layered_route",
    "live_layered_route",
)


def test_every_surface_that_can_withhold_answers_with_a_listed_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One assertion over every withholding surface: never a bare `null`, never both.

    The per-surface tests above check *which* reason. This checks the property that makes the
    set worth having at all, and it enumerates the surfaces so a new one cannot join silently.
    """

    monkeypatch.setenv("KICAD_API_TOKEN", SESSION_TOKEN)
    monkeypatch.setattr(kicad_ipc, "_SESSION_REVISION_SALT", b"\x42" * 32)
    source = ROUTE_FIXTURE.read_bytes()
    snapshot = _snapshot_of(source, CONSTRAINTS)
    live_route_request = _route_request(
        board="live",
        net_ref_id=net_id_for_name("AUDIO"),
        expect_board_revision=f"sha256:{hashlib.sha256(source).hexdigest()}",
        expect_snapshot_digest=snapshot.snapshot_digest,
    )
    live_route_request.pop("net")
    (tmp_path / "board.kicad_pcb").write_bytes(source)
    live_settings = replace(
        Settings(workspace=tmp_path.resolve()), allow_live_ipc=True, allow_apply=True
    )
    placement_source = PLACEMENT_FIXTURE.read_bytes()
    placement_snapshot = _snapshot_of(placement_source, PLACEMENT_CONSTRAINTS)

    documents = {
        "route": _route(tmp_path, source, allow_apply=True),
        "live_route": preview_live_route(
            live_route_request, live_settings, client_factory=_live_factory(source)
        ).to_dict(),
        "placement": _placement(placement_source),
        "live_placement": preview_live_placement(
            {
                "board": "live",
                "constraints": dict(PLACEMENT_CONSTRAINTS),
                "subjects": list(PLACEMENT_SUBJECTS),
                "expect_board_revision": (f"sha256:{hashlib.sha256(placement_source).hexdigest()}"),
                "expect_snapshot_digest": placement_snapshot.snapshot_digest,
            },
            replace(Settings(workspace=tmp_path.resolve()), allow_live_ipc=True),
            client_factory=_live_factory(placement_source),
        ).to_dict(),
        "layered_route": preview_layered_route(
            _layered_request(source),
            replace(Settings(workspace=tmp_path.resolve()), allow_apply=True),
        ),
        "live_layered_route": _live_layered(
            tmp_path, allow_live_apply=True, authority=ApplyTokenAuthority()
        ),
    }

    assert set(documents) == set(ALL_SURFACES)
    expected_versions = {
        "route": ("schema_version", "1.1"),
        "live_route": ("schema_version", "1.1"),
        "placement": ("placement_version", "0.2.0"),
        "live_placement": ("placement_version", "0.2.0"),
        "layered_route": ("schema_version", "1.1"),
        "live_layered_route": ("schema_version", "1.1"),
    }
    for name, document in documents.items():
        assert document["apply_token"] is None, name
        assert document["apply_token_withheld_reason"] in APPLY_TOKEN_WITHHELD_REASONS, name
        version_field, version = expected_versions[name]
        assert document[version_field] == version, name


# --- an unlisted reason is refused, which is what "closed" means ---------------------------


def test_a_route_preview_refuses_a_reason_that_is_not_in_the_set(tmp_path: Path) -> None:
    """Closed means enforced. An unlisted reason is refused even when it looks reasonable.

    The rejected value here is the shape the discipline exists to stop: a message that answers
    the caller's question by quoting the board back at them.
    """

    settings = _route_workspace(tmp_path, ROUTE_FIXTURE.read_bytes(), allow_apply=True)
    preview = preview_route(_route_request(), settings)

    with pytest.raises(route_preview.RoutePreviewError, match="closed reason"):
        replace(preview, apply_token_withheld_reason="board_has_3_derived_identities")
    with pytest.raises(route_preview.RoutePreviewError, match="closed reason"):
        replace(preview, apply_token_withheld_reason=None)


def test_a_placement_result_with_neither_a_token_nor_a_reason_never_serializes() -> None:
    from copper_mcp.placement.contracts import PlacementError, PlacementResult

    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        (workspace / "board.kicad_pcb").write_bytes(PLACEMENT_FIXTURE.read_bytes())
        settings = replace(Settings(workspace=workspace.resolve()), allow_apply=True)
        result = preview_placement(
            {
                "board": "board.kicad_pcb",
                "constraints": dict(PLACEMENT_CONSTRAINTS),
                "subjects": list(PLACEMENT_SUBJECTS),
            },
            settings,
            ApplyTokenAuthority(),
        )

    assert isinstance(result, PlacementResult)
    silent = replace(result, apply_token_withheld_reason=None)

    with pytest.raises(PlacementError, match="closed reason"):
        silent.to_dict()
    with pytest.raises(PlacementError, match="unlisted reason"):
        replace(result, apply_token_withheld_reason="board_outline_is_derived")


def test_a_layered_document_refuses_a_token_that_also_carries_a_reason(tmp_path: Path) -> None:
    from copper_mcp.layered_route_preview import (
        LayeredRoutePreviewError,
        _empty_result,
        parse_layered_route_preview_request,
    )

    source = ROUTE_FIXTURE.read_bytes()
    (tmp_path / "board.kicad_pcb").write_bytes(source)
    request = parse_layered_route_preview_request(_layered_request(source))

    with pytest.raises(LayeredRoutePreviewError, match="closed reason"):
        _empty_result("not_routed", request, "board.kicad_pcb", "sha256:" + "0" * 64)
    with pytest.raises(LayeredRoutePreviewError, match="cannot also be withheld"):
        _empty_result(
            "not_routed",
            request,
            "board.kicad_pcb",
            "sha256:" + "0" * 64,
            apply_token="token",
            apply_token_withheld_reason="not_requested",
        )


def test_public_route_contract_refuses_token_reason_ambiguity(tmp_path: Path) -> None:
    document = _route(
        tmp_path, ROUTE_FIXTURE.read_bytes(), allow_apply=True, include_apply_token=True
    )
    RoutePreviewToolResponse.model_validate(document)

    document["apply_token_withheld_reason"] = "not_requested"
    with pytest.raises(ValidationError, match="exactly one"):
        RoutePreviewToolResponse.model_validate(document)

    document.pop("apply_token_withheld_reason")
    with pytest.raises(ValidationError, match="Field required"):
        RoutePreviewToolResponse.model_validate(document)


def test_public_placement_contract_refuses_token_reason_ambiguity() -> None:
    document = _placement(
        PLACEMENT_FIXTURE.read_bytes(), include_apply_token=True, proposals=_moved()
    )
    PlacementPreviewToolResponse.model_validate(document)

    document["apply_token_withheld_reason"] = "not_requested"
    with pytest.raises(ValidationError, match="exactly one"):
        PlacementPreviewToolResponse.model_validate(document)

    document.pop("apply_token_withheld_reason")
    with pytest.raises(ValidationError, match="Field required"):
        PlacementPreviewToolResponse.model_validate(document)


def test_public_layered_contract_refuses_missing_reason(tmp_path: Path) -> None:
    source = ROUTE_FIXTURE.read_bytes()
    (tmp_path / "board.kicad_pcb").write_bytes(source)
    document = preview_layered_route(
        _layered_request(source), replace(Settings(workspace=tmp_path.resolve()), allow_apply=True)
    )
    LayeredRoutePreviewToolResponse.model_validate(document)

    document["apply_token_withheld_reason"] = None
    with pytest.raises(ValidationError, match="exactly one"):
        LayeredRoutePreviewToolResponse.model_validate(document)

    document.pop("apply_token_withheld_reason")
    with pytest.raises(ValidationError, match="Field required"):
        LayeredRoutePreviewToolResponse.model_validate(document)
