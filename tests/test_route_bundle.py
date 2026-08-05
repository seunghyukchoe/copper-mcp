from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, net_id_for_name, parse_kicad_bytes
from copper_mcp.adapters.kicad_route_bundle_patch import render_kicad_route_bundle_board
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.route_bundle import RouteBundleError, RouteBundleStatus, preview_route_bundle

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks/audio/fixtures/negotiated-crossing-v1.kicad_pcb"


def _constraints() -> dict[str, int]:
    return {
        "clearance_nm": 100_000,
        "track_width_nm": 200_000,
        "via_diameter_nm": 600_000,
        "via_drill_nm": 300_000,
    }


def _payload(board: str, source: bytes) -> dict[str, object]:
    net_class = NetClass(id="class:request", name="Request", **_constraints())
    converted = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert converted.snapshot is not None
    assert not converted.diagnostics
    return {
        "board": board,
        "layer": "F.Cu",
        "constraints": _constraints(),
        "net_ref_ids": [net_id_for_name("HORIZONTAL"), net_id_for_name("VERTICAL")],
        "expect_board_revision": f"sha256:{hashlib.sha256(source).hexdigest()}",
        "expect_snapshot_digest": converted.snapshot.snapshot_digest,
        "seed": 7,
        "settings": {
            "grid_step_nm": 1_000_000,
            "bend_penalty_nm": 500_000,
            "proximity_penalty_nm": 0,
            "max_grid_nodes": 512,
            "max_expansions": 20_000,
            "max_obstacles": 128,
            "max_obstacle_checks": 200_000,
        },
    }


def test_preview_composes_a_revision_bound_physical_clearance_checked_bundle(
    tmp_path: Path,
) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    before = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)

    first = preview_route_bundle(_payload(board.name, source), Settings(workspace=tmp_path))
    second = preview_route_bundle(_payload(board.name, source), Settings(workspace=tmp_path))

    assert first == second
    assert first.status is RouteBundleStatus.ROUTED
    assert first.plan is not None
    assert first.plan.base_revision == first.snapshot_digest
    assert first.plan.net_ref_ids == (
        net_id_for_name("HORIZONTAL"),
        net_id_for_name("VERTICAL"),
    )
    assert len(first.plan.candidates) == 2
    assert first.plan.core_replays == 1
    assert first.plan.physical_pair_checks > 0
    assert first.plan.total_wire_length_nm == 26_000_000
    net_class = NetClass(id="class:request", name="Request", **_constraints())
    converted = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert converted.snapshot is not None
    rendered = render_kicad_route_bundle_board(
        source,
        converted.snapshot,
        first.plan,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert rendered == render_kicad_route_bundle_board(
        source,
        converted.snapshot,
        first.plan,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    patched = parse_kicad_bytes(
        rendered,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert patched.snapshot is not None
    assert len(patched.snapshot.content.segments) == 4
    assert before == (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)


def test_preview_refuses_stale_or_duplicate_reference_bundle_without_a_partial_plan(
    tmp_path: Path,
) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    payload = _payload(board.name, source)

    stale = dict(payload, expect_board_revision=f"sha256:{'0' * 64}")
    stale_result = preview_route_bundle(stale, Settings(workspace=tmp_path))
    assert stale_result.status is RouteBundleStatus.NOT_ROUTED
    assert stale_result.plan is None
    assert stale_result.snapshot_digest is None

    duplicate = dict(payload, net_ref_ids=[net_id_for_name("HORIZONTAL")] * 2)
    with pytest.raises(RouteBundleError, match="distinct"):
        preview_route_bundle(duplicate, Settings(workspace=tmp_path))


def test_preview_preserves_request_order_while_canonicalizing_plan_candidates(
    tmp_path: Path,
) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    payload = _payload(board.name, source)
    requested = [net_id_for_name("VERTICAL"), net_id_for_name("HORIZONTAL")]

    result = preview_route_bundle(
        dict(payload, net_ref_ids=requested), Settings(workspace=tmp_path)
    )

    assert result.status is RouteBundleStatus.ROUTED
    assert result.plan is not None
    assert result.plan.net_ref_ids == tuple(requested)
    assert [candidate.patch.net_id for candidate in result.plan.candidates] == sorted(requested)
