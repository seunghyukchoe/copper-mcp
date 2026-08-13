from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import copper_mcp.kicad_cli as kicad_cli
import copper_mcp.request_boundary as request_boundary
import copper_mcp.route_preview as route_preview
from copper_mcp.adapters import net_id_for_name
from copper_mcp.apply.tokens import ApplyTokenAuthority
from copper_mcp.board_ir import PointNM
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError, RouteCandidateDrcEvidence, ZoneFillAuthority
from copper_mcp.mcp_contracts import RoutePreviewToolResponse
from copper_mcp.models import DrcSummary
from copper_mcp.route_preview import (
    RoutePreview,
    RoutePreviewError,
    RoutePreviewStatus,
    parse_route_preview_request,
    preview_live_route,
    preview_route,
)
from copper_mcp.routing import (
    RouteConnection,
    RouteDiagnostic,
    RouteFailureCode,
    fill_binding_for,
)
from copper_mcp.zone_fill import FillIsland

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
_DISCOVERED_KICAD_CLI = shutil.which("kicad-cli")
REAL_KICAD_CLI = (
    Path(_DISCOVERED_KICAD_CLI)
    if _DISCOVERED_KICAD_CLI is not None
    else Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
)


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


def _request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "board": "two-pad.kicad_pcb",
        "net": "AUDIO",
        "layer": "F.Cu",
        "seed": 23,
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
    }
    request.update(overrides)
    return request


def _workspace(tmp_path: Path, *, source: bytes | None = None) -> tuple[Path, Settings]:
    board = tmp_path / "two-pad.kicad_pcb"
    board.write_bytes(source if source is not None else FIXTURE.read_bytes())
    return board, Settings(workspace=tmp_path, max_drc_report_bytes=4096)


def _entries(root: Path) -> dict[str, tuple[int, int, bytes]]:
    return {
        str(path.relative_to(root)): (
            path.stat().st_ino,
            path.stat().st_mtime_ns,
            path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _report(source: str) -> dict[str, object]:
    return {
        "$schema": "https://schemas.kicad.org/drc.v1.json",
        "source": source,
        "date": "2026-08-03T12:00:00+09:00",
        "coordinate_units": "mm",
        "kicad_version": "10.0.5",
        "violations": [],
        "unconnected_items": [],
        "schematic_parity": [],
        "included_severities": ["error", "warning", "exclusion"],
        "ignored_checks": [],
    }


def _install_fake_kicad(
    monkeypatch: pytest.MonkeyPatch,
    capture: dict[str, Any] | None = None,
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["shell"] is False
        assert "--save-board" not in command
        assert "--refill-zones" not in command
        snapshot_board = Path(command[-1])
        report_path = Path(command[command.index("--output") + 1])
        report_path.write_text(json.dumps(_report(snapshot_board.name)), encoding="utf-8")
        if capture is not None:
            capture["snapshot_bytes"] = snapshot_board.read_bytes()
            capture["temporary_mode"] = stat.S_IMODE(report_path.parent.stat().st_mode)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        kicad_cli, "discover_kicad_cli", lambda settings: Path("/trusted/kicad-cli")
    )
    monkeypatch.setattr(subprocess, "run", run)


def test_previews_a_deterministic_candidate_bound_to_the_board_revision(tmp_path: Path) -> None:
    board, settings = _workspace(tmp_path)

    preview = preview_route(_request(), settings)

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.diagnostic is None
    assert preview.drc_evidence is None
    assert preview.board_path == "two-pad.kicad_pcb"
    assert preview.board_revision == f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    assert preview.snapshot_digest == preview.candidate.base_revision
    assert preview.board_revision != preview.snapshot_digest
    assert preview.candidate.patch.layer_id == "layer:F.Cu"
    assert preview.candidate.metrics.hard_internal_violations == 0
    assert preview_route(_request(), settings).to_dict() == preview.to_dict()


def test_preview_serialization_is_detached_from_validated_state(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    preview = preview_route(_request(), settings)
    document = preview.to_dict()
    document["status"] = "tampered"
    document["candidate"]["cost"]["total_cost_nm"] = 1

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.to_dict()["status"] == "routed"
    assert preview.to_dict()["candidate"]["cost"]["total_cost_nm"] != 1
    with pytest.raises(TypeError):
        preview.conversion_diagnostic_counts["injected"] = 1  # type: ignore[index]


def test_preview_does_not_touch_the_workspace(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    (tmp_path / "two-pad.kicad_dru").write_text("(version 1)", encoding="utf-8")
    before = _entries(tmp_path)

    preview = preview_route(_request(), settings)

    assert preview.status is RoutePreviewStatus.ROUTED
    assert _entries(tmp_path) == before


def test_preview_routes_around_a_zone_outline_without_mutating_the_source(
    tmp_path: Path,
) -> None:
    fixture = FIXTURE.parent / "blocked-zone.kicad_pcb"
    board = tmp_path / fixture.name
    board.write_bytes(fixture.read_bytes())
    settings = Settings(workspace=tmp_path, max_drc_report_bytes=4096)
    before = _entries(tmp_path)

    first = preview_route(_request(board=fixture.name), settings)
    second = preview_route(_request(board=fixture.name), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.to_dict() == second.to_dict()
    assert first.candidate is not None
    assert first.candidate.cost.bend_count > 0
    # The POWER zone spans x=18..22 mm and y=11..19 mm. Its 0.375 mm
    # centreline margin includes route half-width and the governing clearance.
    assert all(
        not (17_625_000 < point.x < 22_375_000 and 10_625_000 < point.y < 19_375_000)
        for point in first.candidate.patch.paths[0].vertices
    )
    assert _entries(tmp_path) == before


def test_preview_exposes_fresh_foreign_fill_as_routing_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A routed candidate must tell an MCP caller when exact fill islands shaped the search."""

    fixture = FIXTURE.parent / "blocked-zone.kicad_pcb"
    board = tmp_path / fixture.name
    board.write_bytes(fixture.read_bytes())
    settings = Settings(workspace=tmp_path, max_drc_report_bytes=4096)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
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

    preview = preview_route(
        _request(board=fixture.name, include_fill_authority=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.fill_authority is authority
    assert preview.fill_routing_effect == "foreign_zone_obstacles"
    assert preview.to_dict()["fill_authority"]["routing_effect"] == "foreign_zone_obstacles"


def _copy_fixture(tmp_path: Path, name: str) -> Settings:
    board = tmp_path / name
    board.write_bytes((FIXTURE.parent / name).read_bytes())
    return Settings(workspace=tmp_path, max_drc_report_bytes=4096)


def test_preview_reports_an_already_connected_net(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "connected-net.kicad_pcb")
    before = _entries(tmp_path)

    preview = preview_route(_request(board="connected-net.kicad_pcb"), settings)
    document = preview.to_dict()

    assert preview.status is RoutePreviewStatus.ALREADY_CONNECTED
    assert preview.connection is not None
    assert preview.candidate is None
    assert preview.diagnostic is None
    assert preview.drc_evidence is None
    assert document["status"] == "already_connected"
    assert document["connection"]["attachment_segments"] == 1
    assert document["connection"]["component_objects"] == 3
    assert document["connection"]["base_revision"] == preview.snapshot_digest
    assert document["connection"]["start_pad_id"] != document["connection"]["end_pad_id"]
    assert _entries(tmp_path) == before


def test_already_connected_preview_skips_authoritative_drc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _copy_fixture(tmp_path, "connected-net.kicad_pcb")
    calls = 0

    def unexpected_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(
        kicad_cli, "discover_kicad_cli", lambda settings: Path("/trusted/kicad-cli")
    )
    monkeypatch.setattr(subprocess, "run", unexpected_run)

    preview = preview_route(_request(board="connected-net.kicad_pcb", include_drc=True), settings)

    assert preview.status is RoutePreviewStatus.ALREADY_CONNECTED
    assert preview.drc_evidence is None
    assert calls == 0


def test_preview_routes_around_an_octagonal_keepout(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "octagon-keepout.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="octagon-keepout.kicad_pcb"), settings)
    second = preview_route(_request(board="octagon-keepout.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.candidate is not None
    assert first.candidate.cost.bend_count > 0
    # The octagonal rule area spans x=17..23 mm and y=11..19 mm. Its 0.375 mm centreline
    # margin is the route half width plus the routed class clearance; a keepout carries no
    # net, so no second class clearance applies.
    assert all(
        not (16_625_000 < point.x < 23_375_000 and 10_625_000 < point.y < 19_375_000)
        for point in first.candidate.patch.paths[0].vertices
    )
    assert first.to_dict() == second.to_dict()
    assert _entries(tmp_path) == before


def test_preview_routes_around_a_foreign_diagonal_segment(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "diagonal-blocker.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="diagonal-blocker.kicad_pcb"), settings)
    second = preview_route(_request(board="diagonal-blocker.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.candidate is not None
    # The POWER diagonal runs (18, 11) to (22, 19) and crosses the straight corridor at
    # x=20 mm, so the previously refused board now detours instead.
    assert first.candidate.cost.bend_count > 0
    assert all(
        point.y <= 15_000_000 or point.y >= 19_375_000
        for point in first.candidate.patch.paths[0].vertices
    )
    assert first.to_dict() == second.to_dict()
    assert _entries(tmp_path) == before


def test_preview_routes_around_a_foreign_arc(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "arc-blocker.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="arc-blocker.kicad_pcb"), settings)
    second = preview_route(_request(board="arc-blocker.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.candidate is not None
    # The POWER arc is the semicircle (18, 11) -> (14, 15) -> (18, 19), which bulges across
    # the straight corridor at y=15 mm, so the previously refused board now detours.
    assert first.candidate.cost.bend_count > 0
    # The arc's chord runs x=18 mm, y=11..19 mm and its sagitta is 4 mm, so the envelope
    # spans x=13.875..22.125 mm and y=6.875..23.125 mm. Its 0.375 mm centreline margin is
    # the route half width plus the stricter of the two class clearances.
    assert all(
        not (13_500_000 < point.x < 22_500_000 and 6_500_000 < point.y < 23_500_000)
        for point in first.candidate.patch.paths[0].vertices
    )
    assert first.to_dict() == second.to_dict()
    assert _entries(tmp_path) == before


def test_preview_refuses_to_route_the_net_that_carries_the_arc(tmp_path: Path) -> None:
    """The same board, routed the other way round, is still a typed refusal."""

    settings = _copy_fixture(tmp_path, "arc-blocker.kicad_pcb")

    preview = preview_route(_request(board="arc-blocker.kicad_pcb", net="POWER"), settings)

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.candidate is None
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.UNSUPPORTED_GEOMETRY
    assert "attachment copper" in preview.diagnostic.message


def test_preview_completes_a_partial_route_from_existing_copper(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "partial-route.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="partial-route.kicad_pcb"), settings)
    second = preview_route(_request(board="partial-route.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.connection is None
    assert first.candidate is not None
    # The committed stub already spans x=10..20 mm, so only the remaining 10 mm is proposed.
    assert first.candidate.patch.paths[0].vertices == (
        PointNM(20_000_000, 15_000_000),
        PointNM(30_000_000, 15_000_000),
    )
    assert first.candidate.cost.length_nm == 10_000_000
    assert first.candidate.cost.bend_count == 0
    assert first.to_dict() == second.to_dict()
    assert _entries(tmp_path) == before


def test_preview_completes_a_route_from_a_diagonal_stub(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "diagonal-stub.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="diagonal-stub.kicad_pcb"), settings)
    second = preview_route(_request(board="diagonal-stub.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.candidate is not None
    # The stub runs diagonally from the start pad at (10, 15) to (16, 19); the proposal picks
    # it up at that far end and adds 18 mm instead of the 20 mm an empty board would need.
    assert first.candidate.patch.paths[0].vertices == (
        PointNM(16_000_000, 19_000_000),
        PointNM(16_000_000, 15_000_000),
        PointNM(30_000_000, 15_000_000),
    )
    assert first.candidate.cost.length_nm == 18_000_000
    assert first.to_dict() == second.to_dict()
    assert _entries(tmp_path) == before


def test_preview_reports_a_diagnostic_instead_of_routing_off_grid(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    preview = preview_route(_request(settings={"grid_step_nm": 300_000}), settings)

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.candidate is None
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.OFF_GRID
    assert preview.snapshot_digest is not None
    assert preview.to_dict()["diagnostic"]["code"] == "off_grid"


def test_an_off_grid_preview_publishes_the_pad_the_pitch_and_the_miss(tmp_path: Path) -> None:
    """The public document carries the evidence, as exact integers (ADR-0093).

    The fixture's two pads are 20 mm apart on one axis, so a 300,000 nm lattice misses by
    100,000 nm and the largest step representing the pair is the 20 mm separation itself --
    a divisor far *larger* than the requested step, which is why the field is named for
    representability rather than for fineness.
    """

    _, settings = _workspace(tmp_path)

    preview = preview_route(_request(settings={"grid_step_nm": 300_000}), settings)

    diagnostic = preview.to_dict()["diagnostic"]
    assert diagnostic["off_grid"] == {
        "pad_id": "pad:kicad:20000000-0000-0000-0000-000000000004",
        "anchor_pad_id": "pad:kicad:20000000-0000-0000-0000-000000000002",
        "grid_step_nm": 300_000,
        "miss_x_nm": -100_000,
        "miss_y_nm": 0,
        "largest_representable_step_nm": 20_000_000,
    }
    assert diagnostic["message"] == (
        "a pad centre does not lie on the requested routing lattice: it misses the nearest "
        "lattice point by (-100000 nm, 0 nm) at grid_step_nm=300000; the largest step that "
        "represents this pad pair is 20000000 nm"
    )


def test_the_published_off_grid_contract_binds_the_evidence_to_its_own_code(
    tmp_path: Path,
) -> None:
    """The MCP response contract accepts the real document and rejects a mismatched one.

    The service and the published contract check the same biconditional independently, so a
    payload assembled by anything other than ``RoutePreview.to_dict`` -- a rewriting transport,
    a replayed artifact -- cannot smuggle lattice geometry into a refusal that measured none,
    and cannot strip it from one that did.
    """

    _, settings = _workspace(tmp_path)
    document = preview_route(_request(settings={"grid_step_nm": 300_000}), settings).to_dict()

    validated = RoutePreviewToolResponse.model_validate(document).model_dump()
    assert validated["diagnostic"]["off_grid"]["miss_x_nm"] == -100_000

    moved = json.loads(json.dumps(document))
    moved["diagnostic"]["code"] = "no_path"
    with pytest.raises(ValidationError):
        RoutePreviewToolResponse.model_validate(moved)

    stripped = json.loads(json.dumps(document))
    stripped["diagnostic"]["off_grid"] = None
    with pytest.raises(ValidationError):
        RoutePreviewToolResponse.model_validate(stripped)

    # Absence is the same refusal as an explicit null, which is the property that lets the
    # field carry a default without weakening anything: a producer cannot omit its way out.
    absent = json.loads(json.dumps(document))
    del absent["diagnostic"]["off_grid"]
    with pytest.raises(ValidationError):
        RoutePreviewToolResponse.model_validate(absent)


def test_a_pre_evidence_diagnostic_of_another_code_still_validates(tmp_path: Path) -> None:
    """``off_grid`` defaults to ``None``, so a payload recorded before ADR-0093 still parses.

    Requiredness would have bought no property the biconditional does not already provide --
    the case above proves an absent key is refused on the ``off_grid`` code -- while
    invalidating every diagnostic of every *other* code a caller had already stored. That is
    the whole argument for the default, and this is the test that would fail without it.
    """

    _, settings = _workspace(tmp_path)
    document = preview_route(_request(net="MISSING"), settings).to_dict()
    legacy = json.loads(json.dumps(document))
    del legacy["diagnostic"]["off_grid"]

    validated = RoutePreviewToolResponse.model_validate(legacy).model_dump()

    assert validated["diagnostic"]["code"] == "invalid_two_pin_net"
    assert validated["diagnostic"]["off_grid"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("anchor_pad_id", "pad:kicad:20000000-0000-0000-0000-000000000004"),
        ("miss_x_nm", 150_001),
        ("miss_y_nm", -150_001),
        ("largest_representable_step_nm", 900_000),
    ],
)
def test_the_published_contract_refuses_a_forged_self_contradicting_measurement(
    tmp_path: Path, field: str, value: object
) -> None:
    """The published schema asserts everything the runtime asserts, not a weaker subset.

    Each forgery is individually well-typed and in range, and each contradicts the refusal
    carrying it: an anchor equal to the pad, a miss wider than half the step it names, a miss
    of zero on both axes, and a divisor the requested step divides -- which would mean the pair
    is *on* the lattice. A schema that accepted these would let a consumer validating against
    the published contract alone accept a payload `copper_mcp` itself refuses to construct.
    """

    _, settings = _workspace(tmp_path)
    document = preview_route(_request(settings={"grid_step_nm": 300_000}), settings).to_dict()
    forged = json.loads(json.dumps(document))
    forged["diagnostic"]["off_grid"][field] = value

    with pytest.raises(ValidationError):
        RoutePreviewToolResponse.model_validate(forged)


def test_the_published_contract_accepts_a_withheld_divisor_but_not_an_unrepresentable_one(
    tmp_path: Path,
) -> None:
    """`null` is the published shape for a divisor too large to carry exactly.

    A pad pair near opposite legal Board IR coordinate extremes has a divisor above the
    JSON-safe range, so the schema has to admit `null` there — and must still refuse an actual
    integer above that range, which no JSON consumer could read back without losing precision.
    Accepting the second would reintroduce the defect one layer out from where it was fixed.
    """

    _, settings = _workspace(tmp_path)
    document = preview_route(_request(settings={"grid_step_nm": 300_000}), settings).to_dict()

    withheld = json.loads(json.dumps(document))
    withheld["diagnostic"]["off_grid"]["largest_representable_step_nm"] = None
    validated = RoutePreviewToolResponse.model_validate(withheld).model_dump()
    assert validated["diagnostic"]["off_grid"]["largest_representable_step_nm"] is None
    # The rest of the evidence is untouched, so the refusal stays actionable without it.
    assert validated["diagnostic"]["off_grid"]["miss_x_nm"] == -100_000

    unrepresentable = json.loads(json.dumps(document))
    unrepresentable["diagnostic"]["off_grid"]["largest_representable_step_nm"] = 2**53
    with pytest.raises(ValidationError):
        RoutePreviewToolResponse.model_validate(unrepresentable)


def test_the_published_contract_refuses_a_miss_of_zero_on_both_axes(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    document = preview_route(_request(settings={"grid_step_nm": 300_000}), settings).to_dict()
    forged = json.loads(json.dumps(document))
    forged["diagnostic"]["off_grid"]["miss_x_nm"] = 0
    forged["diagnostic"]["off_grid"]["miss_y_nm"] = 0

    with pytest.raises(ValidationError):
        RoutePreviewToolResponse.model_validate(forged)


def test_a_diagnostic_that_measured_no_lattice_reports_no_lattice_geometry(tmp_path: Path) -> None:
    """``off_grid`` is ``None`` on every other code, never an empty object or a zero.

    A placeholder would read as a measured miss of zero nanometres, which is the one thing a
    pad that was never measured against a lattice cannot be said to be.
    """

    _, settings = _workspace(tmp_path)

    preview = preview_route(_request(net="MISSING"), settings)

    document = preview.to_dict()
    assert document["diagnostic"]["code"] == "invalid_two_pin_net"
    assert document["diagnostic"]["off_grid"] is None


def test_preview_reports_a_diagnostic_for_an_unknown_net(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    preview = preview_route(_request(net="MISSING"), settings)

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.INVALID_TWO_PIN_NET


def _reference_request(preview: RoutePreview, **overrides: Any) -> dict[str, Any]:
    assert preview.candidate is not None
    assert preview.snapshot_digest is not None
    request = _request()
    del request["net"]
    request.update(
        {
            "net_ref_id": preview.candidate.patch.net_id,
            "expect_board_revision": preview.board_revision,
            "expect_snapshot_digest": preview.snapshot_digest,
        }
    )
    request.update(overrides)
    return request


def test_scene_net_reference_routes_the_same_candidate_as_the_hidden_name(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    named = preview_route(_request(), settings)

    referenced = preview_route(_reference_request(named), settings)

    assert referenced.status is RoutePreviewStatus.ROUTED
    assert referenced.candidate == named.candidate
    assert referenced.board_revision == named.board_revision
    assert referenced.snapshot_digest == named.snapshot_digest
    request_echo = referenced.to_dict()["request"]
    assert request_echo["net_ref_id"] == referenced.candidate.patch.net_id
    assert request_echo["expect_board_revision"] == named.board_revision
    assert request_echo["expect_snapshot_digest"] == named.snapshot_digest
    assert "net" not in request_echo


@pytest.mark.parametrize("precondition", ["board", "snapshot"])
def test_scene_net_reference_refuses_a_stale_observation(tmp_path: Path, precondition: str) -> None:
    board, settings = _workspace(tmp_path)
    named = preview_route(_request(), settings)
    request = _reference_request(named)
    if precondition == "board":
        board.write_bytes(board.read_bytes() + b"\n")
    else:
        request["expect_snapshot_digest"] = f"sha256:{'0' * 64}"

    preview = preview_route(request, settings)

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.candidate is None
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.STALE_REVISION
    assert (preview.snapshot_digest is None) is (precondition == "board")


def test_stale_board_reference_refuses_before_board_ir_conversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, settings = _workspace(tmp_path)
    named = preview_route(_request(), settings)
    board.write_bytes(board.read_bytes() + b"\n")

    def unexpected_conversion(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("stale source bytes must be refused before conversion")

    monkeypatch.setattr(route_preview, "parse_kicad_bytes", unexpected_conversion)
    stale = preview_route(_reference_request(named), settings)

    assert stale.status is RoutePreviewStatus.NOT_ROUTED
    assert stale.snapshot_digest is None
    assert stale.diagnostic is not None
    assert stale.diagnostic.code is RouteFailureCode.STALE_REVISION


def test_scene_net_reference_requires_both_revision_preconditions() -> None:
    for missing in ("expect_board_revision", "expect_snapshot_digest"):
        request = _request()
        del request["net"]
        request.update(
            {
                "net_ref_id": "net:name:0123456789abcdef0123456789abcdef",
                "expect_board_revision": f"sha256:{'1' * 64}",
                "expect_snapshot_digest": f"sha256:{'2' * 64}",
            }
        )
        del request[missing]
        with pytest.raises(RoutePreviewError, match="revision preconditions"):
            parse_route_preview_request(request)


def _live_reference_request(preview: RoutePreview) -> dict[str, Any]:
    request = _reference_request(preview)
    request["board"] = "live"
    return request


def _live_settings(settings: Settings) -> Settings:
    """Live IPC is operator-gated and off by default; these tests are the enabled path.

    tests/test_kicad_ipc.py owns the default-off refusal itself.
    """

    return replace(settings, allow_live_ipc=True)


def test_live_route_proposal_reuses_the_exact_ipc_snapshot_and_candidate(
    tmp_path: Path,
) -> None:
    _, settings = _workspace(tmp_path)
    named = preview_route(_request(), settings)
    assert named.candidate is not None
    source = FIXTURE.read_text(encoding="utf-8")

    live = preview_live_route(
        _live_reference_request(named),
        _live_settings(settings),
        client_factory=lambda **_: _FakeLiveKiCad(source),
    )

    assert live.status is RoutePreviewStatus.ROUTED
    assert live.board_path == "live"
    assert live.candidate == named.candidate
    assert live.board_revision == named.board_revision
    assert live.snapshot_digest == named.snapshot_digest
    assert live.drc_evidence is None
    assert live.fill_authority is None
    assert live.apply_token is None
    assert live.to_dict()["request"]["net_ref_id"] == named.candidate.patch.net_id


def test_live_route_passes_remaining_preview_budget_to_ipc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, settings = _workspace(tmp_path)
    named = preview_route(_request(), settings)
    assert named.candidate is not None
    observed: dict[str, object] = {}
    real_capture = route_preview.capture_live_board

    def capture_with_observation(*args: object, **kwargs: object) -> object:
        observed.update(kwargs)
        return real_capture(*args, **kwargs)

    monkeypatch.setattr(route_preview, "capture_live_board", capture_with_observation)
    preview_live_route(
        _live_reference_request(named),
        replace(_live_settings(settings), max_route_preview_seconds=1),
        client_factory=lambda **_: _FakeLiveKiCad(FIXTURE.read_text(encoding="utf-8")),
    )

    timeout_ms = observed["timeout_ms"]
    assert isinstance(timeout_ms, int)
    assert 1 <= timeout_ms <= 1_000
    assert isinstance(observed["deadline"], float)


def test_live_route_refuses_stale_board_before_conversion(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    named = preview_route(_request(), settings)
    assert named.candidate is not None
    calls = 0

    def factory(**_: Any) -> _FakeLiveKiCad:
        nonlocal calls
        calls += 1
        return _FakeLiveKiCad(FIXTURE.read_text(encoding="utf-8"))

    request = _live_reference_request(named)
    request["expect_board_revision"] = f"sha256:{'0' * 64}"
    stale = preview_live_route(request, _live_settings(settings), client_factory=factory)

    assert stale.status is RoutePreviewStatus.NOT_ROUTED
    assert stale.snapshot_digest is None
    assert stale.diagnostic is not None
    assert stale.diagnostic.code is RouteFailureCode.STALE_REVISION
    assert calls == 1


@pytest.mark.parametrize("field", ["include_drc", "include_fill_authority", "include_apply_token"])
def test_live_route_rejects_action_authority_before_ipc_capture(tmp_path: Path, field: str) -> None:
    _, settings = _workspace(tmp_path)
    named = preview_route(_request(), settings)
    calls = 0

    def factory(**_: Any) -> _FakeLiveKiCad:
        nonlocal calls
        calls += 1
        return _FakeLiveKiCad(FIXTURE.read_text(encoding="utf-8"))

    request = _live_reference_request(named)
    request[field] = True
    with pytest.raises(RoutePreviewError, match="read-only"):
        preview_live_route(request, _live_settings(settings), client_factory=factory)
    assert calls == 0


def test_route_request_requires_exactly_one_name_or_scene_reference() -> None:
    without_selector = _request()
    del without_selector["net"]
    with pytest.raises(RoutePreviewError, match="exactly one"):
        parse_route_preview_request(without_selector)

    with pytest.raises(RoutePreviewError, match="exactly one"):
        parse_route_preview_request(
            _request(net_ref_id="net:name:0123456789abcdef0123456789abcdef")
        )


@pytest.mark.parametrize(
    "net_ref_id",
    [
        "AUDIO",
        "net:",
        "net:x",
        "pad:kicad:01234567-89ab-cdef-0123-456789abcdef",
        "net:name:x\n",
        "net:name:audio",
    ],
)
def test_route_request_rejects_malformed_scene_net_references(net_ref_id: str) -> None:
    request = _request()
    del request["net"]
    request.update(
        {
            "net_ref_id": net_ref_id,
            "expect_board_revision": f"sha256:{'1' * 64}",
            "expect_snapshot_digest": f"sha256:{'2' * 64}",
        }
    )

    with pytest.raises(RoutePreviewError):
        parse_route_preview_request(request)


class _AdvancingClock:
    """A monotonic clock that always advances past any preview deadline."""

    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        self.value += 10.0
        return self.value


def test_preview_cancels_deterministically_at_the_configured_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, settings = _workspace(tmp_path)
    monkeypatch.setattr(route_preview, "time", _AdvancingClock())

    preview = preview_route(_request(), replace(settings, max_route_preview_seconds=1))

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.CANCELLED


def test_preview_deadline_covers_conversion_not_only_the_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, settings = _workspace(tmp_path)
    monkeypatch.setattr(route_preview, "time", _AdvancingClock())

    preview = preview_route(_request(), replace(settings, max_route_preview_seconds=1))

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.CANCELLED
    assert "conversion" in preview.diagnostic.message


def test_preview_clamps_the_kicad_timeout_to_the_remaining_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, settings = _workspace(tmp_path)
    observed: dict[str, int] = {}

    def capture(path: str, candidate: object, profile: object, drc_settings: Settings) -> object:
        observed["timeout"] = drc_settings.kicad_timeout_seconds
        raise KiCadCliError("stop after capturing the clamped budget")

    monkeypatch.setattr(route_preview, "run_route_candidate_drc", capture)

    with pytest.raises(KiCadCliError):
        preview_route(
            _request(include_drc=True),
            replace(settings, kicad_timeout_seconds=120, max_route_preview_seconds=5),
        )

    assert 1 <= observed["timeout"] <= 5


def test_drc_budget_refuses_to_start_once_the_deadline_is_spent(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    spent = time.monotonic() - 1.0

    with pytest.raises(RoutePreviewError, match="deadline expired"):
        route_preview._drc_settings(settings, spent)


def test_drc_budget_never_exceeds_the_configured_kicad_timeout(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    generous = time.monotonic() + 10_000.0

    clamped = route_preview._drc_settings(replace(settings, kicad_timeout_seconds=30), generous)

    assert clamped.kicad_timeout_seconds == 30


def test_preview_errors_never_echo_caller_supplied_field_names() -> None:
    secret = "x" * 5000 + "-corporate-secret-net"

    with pytest.raises(RoutePreviewError) as raised:
        parse_route_preview_request(_request(**{secret: 1}))

    message = str(raised.value)
    assert secret not in message
    assert "corporate-secret" not in message
    assert len(message) < 500
    assert "1 unsupported field" in message


def test_preview_fails_closed_on_a_board_outside_the_supported_subset(tmp_path: Path) -> None:
    unsupported = FIXTURE.read_bytes().replace(b'(layer "Edge.Cuts")', b'(layer "F.SilkS")')
    _, settings = _workspace(tmp_path, source=unsupported)

    preview = preview_route(_request(), settings)

    assert preview.status is RoutePreviewStatus.UNSUPPORTED_BOARD
    assert preview.candidate is None
    assert preview.diagnostic is None
    assert preview.snapshot_digest is None
    assert dict(preview.conversion_diagnostic_counts) == {"geometry.missing": 1}


def test_preview_rejects_boards_outside_the_workspace(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    with pytest.raises(ValueError, match="workspace"):
        preview_route(_request(board="../two-pad.kicad_pcb"), settings)


@pytest.mark.parametrize(
    "payload",
    [
        "not-an-object",
        {"net": "AUDIO", "layer": "F.Cu", "constraints": {}},
        _request(unexpected=1),
        _request(board=""),
        _request(board="bad\ud800board.kicad_pcb"),
        _request(net="AUD\x00IO"),
        _request(net="bad\ud800net"),
        _request(layer="F.Silkscreen"),
        _request(layer="F.Cu\n"),
        _request(seed=-1),
        _request(seed=True),
        _request(seed="23"),
        _request(include_drc="yes"),
        _request(constraints={"clearance_nm": 1}),
        _request(constraints={"clearance_nm": 1, "extra": 2}),
        _request(
            constraints={
                "clearance_nm": 250_000,
                "track_width_nm": 0,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            }
        ),
        _request(
            constraints={
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 400_000,
                "via_drill_nm": 400_000,
            }
        ),
        _request(settings={"unknown_budget": 1}),
        _request(settings={"grid_step_nm": 0}),
        _request(settings={"max_expansions": 1 << 40}),
        _request(settings={"grid_step_nm": True}),
        _request(settings=[]),
    ],
)
def test_preview_rejects_malformed_requests(payload: Any) -> None:
    with pytest.raises(RoutePreviewError):
        parse_route_preview_request(payload)


def test_request_normalization_exposes_only_validated_fields() -> None:
    request = parse_route_preview_request(_request())

    assert request.layer_id == "layer:F.Cu"
    assert request.net_id.startswith("net:name:")
    assert request.profile().default_net_class_id == request_boundary.NET_CLASS_ID
    assert set(request.to_dict()) == {
        "board",
        "net",
        "layer",
        "seed",
        "include_drc",
        "include_fill_authority",
        "include_apply_token",
        "constraints",
        "settings",
    }


def test_preview_binds_optional_authoritative_drc_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, settings = _workspace(tmp_path)
    capture: dict[str, Any] = {}
    _install_fake_kicad(monkeypatch, capture)
    before = _entries(tmp_path)

    preview = preview_route(_request(include_drc=True), settings)

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.candidate_id == preview.candidate.candidate_id
    assert preview.drc_evidence.source_revision == preview.board_revision
    assert preview.drc_evidence.summary.passed is True
    assert capture["snapshot_bytes"] != board.read_bytes()
    assert capture["temporary_mode"] == 0o700
    assert _entries(tmp_path) == before


def test_preview_fails_closed_when_authoritative_drc_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, settings = _workspace(tmp_path)
    monkeypatch.setattr(
        kicad_cli,
        "discover_kicad_cli",
        _raise_missing_kicad,
    )

    with pytest.raises(KiCadCliError):
        preview_route(_request(include_drc=True), settings)


def _raise_missing_kicad(settings: Settings) -> Path:
    raise KiCadCliError("KiCad CLI was not found; set COPPER_MCP_KICAD_CLI")


def test_preview_record_rejects_inconsistent_bindings(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    preview = preview_route(_request(), settings)
    assert preview.candidate is not None

    with pytest.raises(RoutePreviewError, match="Board IR snapshot"):
        RoutePreview(
            status=RoutePreviewStatus.ROUTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.board_revision,
            candidate=preview.candidate,
        )
    with pytest.raises(RoutePreviewError, match="exactly one candidate"):
        RoutePreview(
            status=RoutePreviewStatus.ROUTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
        )
    with pytest.raises(RoutePreviewError, match="conversion diagnostics"):
        RoutePreview(
            status=RoutePreviewStatus.UNSUPPORTED_BOARD,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
        )
    with pytest.raises(RoutePreviewError, match="routed candidate"):
        RoutePreview(
            status=RoutePreviewStatus.NOT_ROUTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
            diagnostic=RouteDiagnostic(code=RouteFailureCode.NO_PATH, message="no path"),
            drc_evidence=_evidence(preview),
        )


def test_preview_record_rejects_inconsistent_connection_bindings(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    preview = preview_route(_request(), settings)
    assert preview.candidate is not None
    assert preview.snapshot_digest is not None
    connection = RouteConnection(
        base_revision=preview.snapshot_digest,
        start_pad_id=preview.candidate.start_pad_id,
        end_pad_id=preview.candidate.end_pad_id,
        attachment_segments=1,
        component_objects=3,
    )

    with pytest.raises(RoutePreviewError, match="exactly one connection"):
        RoutePreview(
            status=RoutePreviewStatus.ALREADY_CONNECTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
            connection=connection,
            candidate=preview.candidate,
        )
    with pytest.raises(RoutePreviewError, match="exactly one connection"):
        RoutePreview(
            status=RoutePreviewStatus.ALREADY_CONNECTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
            connection=connection,
            diagnostic=RouteDiagnostic(code=RouteFailureCode.NO_PATH, message="no path"),
        )
    with pytest.raises(RoutePreviewError, match="previewed Board IR snapshot"):
        RoutePreview(
            status=RoutePreviewStatus.ALREADY_CONNECTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
            connection=replace(connection, base_revision=preview.board_revision),
        )
    with pytest.raises(RoutePreviewError, match="exactly one candidate"):
        RoutePreview(
            status=RoutePreviewStatus.ROUTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
            candidate=preview.candidate,
            connection=connection,
        )
    with pytest.raises(RoutePreviewError, match="routing outcome"):
        RoutePreview(
            status=RoutePreviewStatus.UNSUPPORTED_BOARD,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            connection=connection,
            conversion_diagnostic_counts={"geometry.missing": 1},
        )


def _evidence(preview: RoutePreview) -> Any:
    assert preview.candidate is not None
    return RouteCandidateDrcEvidence(
        candidate_id=preview.candidate.candidate_id,
        candidate_base_revision=preview.candidate.base_revision,
        source_revision=preview.board_revision,
        patched_board_revision=preview.board_revision,
        patched_drc_context_revision=preview.board_revision,
        summary=DrcSummary(
            base_revision=preview.board_revision,
            drc_context_revision=preview.board_revision,
            kicad_version="10.0.5",
            drc_schema="https://schemas.kicad.org/drc.v1.json",
            coordinate_units="mm",
            error_count=0,
            warning_count=0,
            exclusion_count=0,
            ignored_check_count=0,
            unconnected_count=0,
            violation_type_counts={},
            passed=True,
        ),
    )


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_the_previewed_candidate_without_mutating_the_source(
    tmp_path: Path,
) -> None:
    _, settings = _workspace(tmp_path)
    settings = replace(settings, kicad_cli=REAL_KICAD_CLI, max_drc_report_bytes=8 * 1024 * 1024)
    before = _entries(tmp_path)

    preview = preview_route(_request(include_drc=True), settings)

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_route_detoured_around_existing_copper(tmp_path: Path) -> None:
    board = tmp_path / "blocked-pad.kicad_pcb"
    board.write_bytes((FIXTURE.parent / "blocked-pad.kicad_pcb").read_bytes())
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="blocked-pad.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    # A straight route would cross the 2 mm x 8 mm POWER pad centred between the endpoints.
    assert preview.candidate.cost.bend_count > 0
    assert all(
        not (18_625_000 < point.x < 21_375_000 and 10_625_000 < point.y < 19_375_000)
        for point in preview.candidate.patch.paths[0].vertices
    )
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_route_detoured_around_a_foreign_diagonal(
    tmp_path: Path,
) -> None:
    settings = replace(
        _copy_fixture(tmp_path, "diagonal-blocker.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="diagonal-blocker.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.candidate.cost.bend_count > 0
    # A straight route across this board makes KiCad report `tracks_crossing` as an error, so
    # a clean report is evidence the conservative envelope really did divert the route.
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.warning_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_route_detoured_around_a_foreign_arc(
    tmp_path: Path,
) -> None:
    settings = replace(
        _copy_fixture(tmp_path, "arc-blocker.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="arc-blocker.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.candidate.cost.bend_count > 0
    # The companion test below proves a straight route across this board makes KiCad report
    # `shorting_items` as an error, so a clean report here is evidence the conservative
    # envelope really did divert the route rather than the fixture being undemanding.
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.warning_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_reports_a_short_when_the_arc_is_routed_straight_through(
    tmp_path: Path,
) -> None:
    """Guard the guard: the arc fixture must be able to fail, or a clean DRC proves nothing."""

    source = (FIXTURE.parent / "arc-blocker.kicad_pcb").read_text(encoding="utf-8")
    straight = (
        "  (segment\n    (start 10 15)\n    (end 30 15)\n    (width 0.25)\n"
        '    (layer "F.Cu")\n    (net "AUDIO")\n'
        '    (uuid "3a000000-0000-0000-0000-0000000000bb")\n  )\n'
    )
    board = tmp_path / "arc-shorted.kicad_pcb"
    board.write_text(source.replace("  (gr_rect", straight + "  (gr_rect"), encoding="utf-8")
    report = tmp_path / "drc.json"

    completed = subprocess.run(  # noqa: S603 - fixed local argv, trusted discovered CLI
        [
            str(REAL_KICAD_CLI),
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--output",
            str(report),
            str(board),
        ],
        check=False,
        capture_output=True,
        timeout=120,
    )

    assert completed.returncode == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    # Straight from pad to pad, the AUDIO track runs through the POWER arc's copper.
    assert [violation["type"] for violation in payload["violations"]] == ["shorting_items"]


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_route_detoured_around_an_octagonal_keepout(
    tmp_path: Path,
) -> None:
    settings = replace(
        _copy_fixture(tmp_path, "octagon-keepout.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="octagon-keepout.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.candidate.cost.bend_count > 0
    # A straight route through this rule area makes KiCad report `items_not_allowed` as an
    # error, so a clean report here is evidence the detour is real and not self-graded.
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.warning_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_route_completed_off_a_diagonal_stub(tmp_path: Path) -> None:
    settings = replace(
        _copy_fixture(tmp_path, "diagonal-stub.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="diagonal-stub.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.candidate.patch.paths[0].vertices[0] == PointNM(16_000_000, 19_000_000)
    # This is the check that the under-approximating diagonal core is sound at the attachment
    # point: displacing the same proposal by 0.5 mm so it misses the stub end makes KiCad
    # report two `track_dangling` warnings and one unconnected item, so a clean report here is
    # evidence the chosen start really does sit on existing copper.
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.warning_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_completed_partial_route(tmp_path: Path) -> None:
    settings = replace(
        _copy_fixture(tmp_path, "partial-route.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="partial-route.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.candidate.patch.paths[0].vertices == (
        PointNM(20_000_000, 15_000_000),
        PointNM(30_000_000, 15_000_000),
    )
    assert preview.drc_evidence is not None
    # New copper overlapping same-net copper is legal, and attaching at the stub's own
    # endpoint leaves no dangling tail, so KiCad reports nothing at all.
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.warning_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


COPPERTONE_BOARD = (
    Path(__file__).parent.parent / "hardware" / "coppertone-buffer" / "coppertone-buffer.kicad_pcb"
)


# Every `F.Cu` net the router can currently resolve, as (pad count, same-net segment count).
# The RAW nets are the ones whose copper includes diagonals; the wider ones only became
# answerable once connectivity stopped being a two-pin-only question.
COPPERTONE_CONNECTED_NETS = {
    "9V_RAW": (2, 2, 0),
    "L_IN_RAW": (2, 3, 0),
    "R_IN_RAW": (2, 2, 0),
    "L_ISO": (2, 1, 0),
    "R_ISO": (2, 1, 0),
    "L_BUF": (3, 3, 0),
    "R_BUF": (3, 3, 0),
    "L_IN_BIASED": (3, 3, 0),
    "R_IN_BIASED": (3, 3, 0),
    "R_OUT": (3, 4, 0),
    "VREF": (7, 10, 0),
    # These two are joined across the back layer through their vias.
    "L_OUT": (3, 7, 1),
    "VCC": (6, 11, 2),
}
# Refused because it carries a same-net zone, whose fill this model does not trust.
COPPERTONE_VIA_NETS = ("GND",)


@pytest.mark.parametrize(("net_name", "shape"), sorted(COPPERTONE_CONNECTED_NETS.items()))
def test_coppertone_connected_nets_report_already_connected(
    net_name: str, shape: tuple[int, int, int], tmp_path: Path
) -> None:
    pad_count, segments, vias = shape
    board = tmp_path / COPPERTONE_BOARD.name
    board.write_bytes(COPPERTONE_BOARD.read_bytes())
    settings = Settings(workspace=tmp_path, max_drc_report_bytes=4096)
    request = _request(board=COPPERTONE_BOARD.name, net=net_name)

    first = preview_route(request, settings)
    second = preview_route(request, settings)

    assert first.status is RoutePreviewStatus.ALREADY_CONNECTED
    assert first.to_dict() == second.to_dict()
    assert first.connection is not None
    assert first.connection.pad_count == pad_count
    assert first.connection.attachment_segments == segments
    assert first.connection.vias == vias
    assert first.connection.component_objects == segments + pad_count + vias


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_corroborates_the_coppertone_already_connected_nets(tmp_path: Path) -> None:
    """KiCad's own connectivity report is the evidence behind the already-connected claim.

    An already-connected preview emits no candidate, so there is nothing for candidate-bound
    DRC to replay. The authoritative check available is the board-level one: KiCad reporting
    zero unconnected items means every net on this board, including both ISO nets, is fully
    connected — which is exactly what the preview claims for them.
    """

    board = tmp_path / COPPERTONE_BOARD.name
    board.write_bytes(COPPERTONE_BOARD.read_bytes())
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )

    summary = kicad_cli.run_board_drc(COPPERTONE_BOARD.name, settings)

    assert summary.unconnected_count == 0
    assert summary.error_count == 0
    for net_name in COPPERTONE_CONNECTED_NETS:
        preview = preview_route(_request(board=COPPERTONE_BOARD.name, net=net_name), settings)
        assert preview.status is RoutePreviewStatus.ALREADY_CONNECTED


@pytest.mark.parametrize("net_name", COPPERTONE_VIA_NETS)
def test_coppertone_via_carrying_nets_stay_refused(net_name: str, tmp_path: Path) -> None:
    """A same-net zone cannot prove connectivity, so that net is never claimed connected."""

    board = tmp_path / COPPERTONE_BOARD.name
    board.write_bytes(COPPERTONE_BOARD.read_bytes())
    settings = Settings(workspace=tmp_path, max_drc_report_bytes=4096)

    preview = preview_route(_request(board=COPPERTONE_BOARD.name, net=net_name), settings)

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.connection is None
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.INVALID_TWO_PIN_NET


def test_preview_routes_a_four_pad_net_as_a_tree(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "tree-star.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="tree-star.kicad_pcb"), settings)
    second = preview_route(_request(board="tree-star.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.candidate is not None
    assert first.candidate.pad_count == 4
    assert first.candidate.ordering_policy == "batched-1-steiner-v1"
    # The topology guide shortens this four-pad tree from the recorded component-MST baseline
    # (48 mm) to 42 mm while the real KiCad DRC oracle still accepts it.
    assert first.candidate.cost.length_nm == 42_000_000
    # Four isolated pads are four components, so a spanning tree is exactly three merges.
    assert len(first.candidate.patch.paths) == 3
    document = first.to_dict()
    assert len(document["candidate"]["patch"]["paths"]) == 3
    assert document["candidate"]["pad_count"] == 4
    assert first.to_dict() == second.to_dict()
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_multi_pin_tree(tmp_path: Path) -> None:
    settings = replace(
        _copy_fixture(tmp_path, "tree-star.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="tree-star.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert len(preview.candidate.patch.paths) == 3
    # Discriminating: rendering the same board with any one leg removed makes KiCad report an
    # unconnected item, so a clean report here is evidence the tree really does connect the
    # net rather than evidence that little copper was added.
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.warning_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


def test_preview_recognises_a_net_joined_across_layers_through_vias(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "via-joint.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="via-joint.kicad_pcb"), settings)
    second = preview_route(_request(board="via-joint.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ALREADY_CONNECTED
    assert first.connection is not None
    # Front stub, back-layer detour, front stub: the two vias are what join them.
    assert first.connection.vias == 2
    assert first.connection.attachment_segments == 3
    assert first.connection.pad_count == 2
    assert first.to_dict()["connection"]["vias"] == 2
    assert first.to_dict() == second.to_dict()
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_corroborates_the_cross_layer_connection(tmp_path: Path) -> None:
    """KiCad's connectivity engine is the evidence behind a multilayer claim.

    Removing either the via or the back-layer stub from a scratch copy makes KiCad report an
    unconnected item, so a zero-unconnected report on the intact board is positive evidence
    that the via really is carrying the connection rather than the claim being vacuous.
    """

    settings = replace(
        _copy_fixture(tmp_path, "via-joint.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )

    summary = kicad_cli.run_board_drc("via-joint.kicad_pcb", settings)
    preview = preview_route(_request(board="via-joint.kicad_pcb"), settings)

    assert summary.unconnected_count == 0
    assert summary.error_count == 0
    assert preview.status is RoutePreviewStatus.ALREADY_CONNECTED


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        # Poured copper joins the two pads, but only once KiCad has confirmed the cache.
        ("zone-fill-fresh", "connected"),
        # The cache was left behind after the board changed, so nothing may be believed.
        ("zone-fill-stale", "stale"),
        # Two disjoint islands: touching different pours proves nothing.
        ("zone-fill-islands", "refused"),
    ],
)
def test_real_kicad_zone_fill_authority_outcomes(
    fixture: str, expected: str, tmp_path: Path
) -> None:
    settings = replace(
        _copy_fixture(tmp_path, f"{fixture}.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board=f"{fixture}.kicad_pcb", net="GND", include_fill_authority=True),
        settings,
    )

    if expected == "connected":
        assert preview.status is RoutePreviewStatus.ALREADY_CONNECTED
        assert preview.connection is not None
        assert preview.connection.fill_polygons == 1
        assert preview.fill_authority is not None
        assert preview.fill_authority.kicad_version.startswith("10.")
        assert preview.to_dict()["fill_authority"]["fill_vertex_count"] == 148
    elif expected == "stale":
        assert preview.status is RoutePreviewStatus.NOT_ROUTED
        assert preview.diagnostic is not None
        assert preview.diagnostic.code is RouteFailureCode.STALE_FILL
        assert preview.fill_authority is None
    else:
        assert preview.status is RoutePreviewStatus.NOT_ROUTED
        assert preview.connection is None
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_zone_fill_authority_is_opt_in(tmp_path: Path) -> None:
    """No implicit KiCad execution: without the flag the zone still vetoes the claim."""

    settings = replace(
        _copy_fixture(tmp_path, "zone-fill-fresh.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )

    preview = preview_route(_request(board="zone-fill-fresh.kicad_pcb", net="GND"), settings)

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.connection is None
    assert preview.fill_authority is None


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_resolves_coppertone_gnd_only_with_fill_authority(tmp_path: Path) -> None:
    board = tmp_path / COPPERTONE_BOARD.name
    board.write_bytes(COPPERTONE_BOARD.read_bytes())
    before = board.read_bytes()
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )

    without = preview_route(_request(board=COPPERTONE_BOARD.name, net="GND"), settings)
    with_authority = preview_route(
        _request(board=COPPERTONE_BOARD.name, net="GND", include_fill_authority=True),
        settings,
    )

    assert without.status is RoutePreviewStatus.NOT_ROUTED
    assert with_authority.status is RoutePreviewStatus.ALREADY_CONNECTED
    assert with_authority.connection is not None
    assert with_authority.connection.pad_count == 12
    assert with_authority.connection.vias == 6
    assert with_authority.connection.fill_polygons == 2
    assert with_authority.fill_authority is not None
    assert with_authority.fill_authority.fill_vertex_count == 4_314
    assert board.read_bytes() == before


def test_a_fill_routed_candidate_is_returned_without_an_apply_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token is a capability, and this one could only ever refuse (ADR-0103, issue #163).

    Apply runs in a later process than the preview that established the fill, so it can only
    replay against the conservative envelope - which is not the model that produced this route.
    The preview used to mint the token anyway and the apply used to fail on it. The same board
    routed without fill authority still gets one, so this withholds a token rather than breaking
    the surface.
    """

    fixture = FIXTURE.parent / "blocked-zone.kicad_pcb"
    board = tmp_path / fixture.name
    board.write_bytes(fixture.read_bytes())
    settings = replace(
        Settings(workspace=tmp_path, max_drc_report_bytes=4096),
        allow_apply=True,
    )
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
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
        _request(board=fixture.name, include_fill_authority=True, include_apply_token=True),
        settings,
        token_authority,
    )
    envelope = preview_route(
        _request(board=fixture.name, include_apply_token=True),
        settings,
        token_authority,
    )

    assert fill_routed.status is RoutePreviewStatus.ROUTED
    assert fill_routed.candidate is not None
    assert fill_routed.candidate.fill_binding is not None
    assert fill_routed.apply_token is None
    assert fill_routed.to_dict()["candidate"]["fill_binding"] == fill_routed.candidate.fill_binding
    # Not vacuous: the same request without fill authority still mints a token on this board,
    # and its candidate document carries no fill key at all.
    assert envelope.candidate is not None
    assert envelope.candidate.fill_binding is None
    assert envelope.apply_token is not None
    assert envelope.to_dict()["candidate"]["fill_binding"] is None


def test_candidate_drc_receives_the_fill_the_candidate_was_routed_under(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview holds the evidence, so it is the only caller that can supply it."""

    fixture = FIXTURE.parent / "blocked-zone.kicad_pcb"
    board = tmp_path / fixture.name
    board.write_bytes(fixture.read_bytes())
    settings = Settings(workspace=tmp_path, max_drc_report_bytes=4096)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
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
    observed: dict[str, Any] = {}

    def capture(
        path: str,
        candidate: Any,
        profile: Any,
        drc_settings: Settings,
        *,
        verified_fill: tuple[Any, ...] = (),
    ) -> Any:
        observed["fill"] = verified_fill
        observed["candidate"] = candidate
        raise KiCadCliError("stop after capturing the forwarded evidence")

    monkeypatch.setattr(route_preview, "run_route_candidate_drc", capture)

    with pytest.raises(KiCadCliError):
        preview_route(
            _request(board=fixture.name, include_fill_authority=True, include_drc=True),
            settings,
        )

    # The forwarded evidence has to be exactly what the candidate recorded, or the replay inside
    # the serialization boundary refuses it.
    assert observed["candidate"].fill_binding is not None
    assert fill_binding_for(observed["fill"]) == observed["candidate"].fill_binding
