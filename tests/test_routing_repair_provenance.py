from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from copper_mcp.board_ir import Keepout, PointNM, Ring, make_snapshot
from copper_mcp.routing import AStarRouter, RouteCandidate, RoutePatch, RoutePath
from copper_mcp.routing.astar import canonical_candidate_bytes
from copper_mcp.routing.contracts import RouteFailureCode
from copper_mcp.routing.repair import (
    LocalRepairStatus,
    RepairTransactionSettings,
    exact_local_repair,
)
from copper_mcp.routing.repair_provenance import (
    _CAPABILITY,
    _candidate_bounds,
    derive_repair_provenance,
)
from scripts import exact_local_repair_gate_fixture as fixture


def _inputs() -> tuple[object, object, object, object]:
    snapshot = fixture.build_snapshot()
    horizontal, vertical = fixture.build_requests(snapshot)
    router = AStarRouter()
    target = router.propose(snapshot, horizontal).candidate
    conflict = router.propose(snapshot, vertical).candidate
    assert target is not None and conflict is not None
    return snapshot, horizontal, target, conflict


def _rebase_candidate(candidate: RouteCandidate, revision: str) -> RouteCandidate:
    unsigned = replace(candidate, candidate_id=f"sha256:{'0' * 64}", base_revision=revision)
    return replace(
        unsigned,
        candidate_id=f"sha256:{hashlib.sha256(canonical_candidate_bytes(unsigned)).hexdigest()}",
    )


def test_shifted_phase_conflict_is_conservatively_projected_onto_target_lattice() -> None:
    base = fixture.build_snapshot()
    base_request, _ = fixture.build_requests(base)
    step = base_request.settings.grid_step_nm
    shift = step // 2
    shifted_pads = tuple(
        replace(
            item,
            center=PointNM(item.center.x + shift, item.center.y + shift),
        )
        if item.net_id == fixture.VERTICAL_NET
        else item
        for item in base.content.pads
    )
    shifted_footprints = tuple(
        replace(
            item,
            origin=PointNM(item.origin.x + shift, item.origin.y + shift),
        )
        if item.id == "footprint:v"
        else item
        for item in base.content.footprints
    )
    snapshot = make_snapshot(
        replace(base.content, pads=shifted_pads, footprints=shifted_footprints)
    )
    request, conflict_request = fixture.build_requests(snapshot)
    router = AStarRouter()
    target = router.propose(snapshot, request).candidate
    conflict = router.propose(snapshot, conflict_request).candidate
    assert target is not None and conflict is not None
    step = request.settings.grid_step_nm
    target_path = target.patch.paths[0]
    shifted_path = conflict.patch.paths[0]
    assert target_path.vertices[0].y == target_path.vertices[-1].y
    assert shifted_path.vertices[0].x == shifted_path.vertices[-1].x
    assert (
        min(point.x for point in target_path.vertices)
        < shifted_path.vertices[0].x
        < max(point.x for point in target_path.vertices)
    )
    assert (
        min(point.y for point in shifted_path.vertices)
        < target_path.vertices[0].y
        < max(point.y for point in shifted_path.vertices)
    )

    provenance = derive_repair_provenance(
        snapshot,
        request,
        target,
        (conflict,),
        envelope_digest=f"sha256:{'1' * 64}",
        iteration=1,
        settings=RepairTransactionSettings(max_projection_cells=256),
    )

    origin = target_path.vertices[0]
    crossing_delta = shifted_path.vertices[0].x - origin.x
    lower_crossing_cell = crossing_delta // step
    assert crossing_delta % step
    assert (shifted_path.vertices[0].y - origin.y) % step
    assert _candidate_bounds(conflict, origin, step) == (4, -4, 5, 5)
    assert {
        (lower_crossing_cell, 0),
        (lower_crossing_cell + 1, 0),
    }.issubset(provenance.blocked_cells)
    provenance.local_request(RepairTransactionSettings(max_projection_cells=256))
    bounds = provenance.window.bounds
    projection_area = (bounds.max_x - bounds.min_x + 1) * (bounds.max_y - bounds.min_y + 1)
    with pytest.raises(ValueError, match="repair provenance") as budget_error:
        derive_repair_provenance(
            snapshot,
            request,
            target,
            (conflict,),
            envelope_digest=f"sha256:{'1' * 64}",
            iteration=1,
            settings=RepairTransactionSettings(max_projection_cells=projection_area - 1),
        )
    assert budget_error.value.__cause__ is not None
    assert str(budget_error.value.__cause__) == (
        "repair provenance projection exceeds its cell budget"
    )


def test_target_interior_vertices_remain_strictly_bound_to_its_authoritative_lattice() -> None:
    snapshot, request, target, conflict = _inputs()
    path = target.patch.paths[0]
    start, end = path.vertices[0], path.vertices[-1]
    shift = request.settings.grid_step_nm // 2
    off_grid_path = RoutePath(
        (
            start,
            PointNM(start.x, start.y + shift),
            PointNM(end.x, end.y + shift),
            end,
        )
    )
    patch = replace(target.patch, paths=(off_grid_path,))
    bend_cost_nm = patch.bend_count * request.settings.bend_penalty_nm
    cost = replace(
        target.cost,
        length_nm=patch.length_nm,
        bend_count=patch.bend_count,
        bend_cost_nm=bend_cost_nm,
        total_cost_nm=(
            patch.length_nm + bend_cost_nm + target.cost.proximity_cost_nm + target.cost.via_cost_nm
        ),
    )
    unsigned = replace(
        target,
        candidate_id=f"sha256:{'0' * 64}",
        patch=patch,
        cost=cost,
        metrics=replace(target.metrics, wire_length_nm=patch.length_nm),
    )
    off_grid_target = replace(
        unsigned,
        candidate_id=f"sha256:{hashlib.sha256(canonical_candidate_bytes(unsigned)).hexdigest()}",
    )

    with pytest.raises(ValueError, match="repair provenance is invalid") as error:
        derive_repair_provenance(
            snapshot,
            request,
            off_grid_target,
            (conflict,),
            envelope_digest=f"sha256:{'1' * 64}",
            iteration=1,
            settings=RepairTransactionSettings(max_projection_cells=256),
        )
    assert error.value.__cause__ is not None
    assert str(error.value.__cause__) == "candidate geometry is not on the coordinator grid"


def test_coordinator_provenance_is_repeatable_and_builds_only_a_bounded_local_request() -> None:
    snapshot, request, target, conflict = _inputs()
    settings = RepairTransactionSettings(max_projection_cells=256)
    values = tuple(
        derive_repair_provenance(
            snapshot,
            request,
            target,
            (conflict,),
            envelope_digest=f"sha256:{'1' * 64}",
            iteration=1,
            settings=settings,
        )
        for _ in range(10)
    )

    provenance = values[0]
    assert values == (provenance,) * 10
    assert provenance.target_candidate_id == target.candidate_id
    assert provenance.conflicting_candidate_ids == (conflict.candidate_id,)
    assert provenance.window.net_id == request.net_id
    assert provenance.digest == (
        "sha256:7a2aedc596c54c0c00d44987a24094e3ec475636f4c8acd8c69e2cf903a5b14c"
    )
    assert provenance.projection_obstacle_checks > 0
    local = provenance.local_request(settings)
    assert local.repair_window == provenance.window
    assert exact_local_repair(local).input_digest == local.input_digest

    with pytest.raises(ValueError, match="coordinator-derived"):
        replace(provenance, _capability=object()).local_request(settings)
    # Python-level frozen dataclasses are replaceable, so capability recovery alone must not
    # restore authority after any provenance field is altered.
    with pytest.raises(ValueError, match="coordinator-derived"):
        replace(provenance, _capability=_CAPABILITY, iteration=2).local_request(settings)


def test_provenance_refuses_stale_or_unbound_candidate_inputs() -> None:
    snapshot, request, target, conflict = _inputs()
    settings = RepairTransactionSettings(max_projection_cells=256)
    stale = replace(target, base_revision=f"sha256:{'2' * 64}")
    forged = replace(conflict, candidate_id=f"sha256:{'3' * 64}")

    for bad_target, bad_conflicts in ((stale, (conflict,)), (target, (forged,)), (target, ())):
        with pytest.raises(ValueError, match="repair provenance"):
            derive_repair_provenance(
                snapshot,
                request,
                bad_target,
                bad_conflicts,
                envelope_digest=f"sha256:{'1' * 64}",
                iteration=1,
                settings=settings,
            )


def test_provenance_obeys_its_projection_budget_before_exposing_a_window() -> None:
    snapshot, request, target, conflict = _inputs()
    with pytest.raises(ValueError, match="repair provenance"):
        derive_repair_provenance(
            snapshot,
            request,
            target,
            (conflict,),
            envelope_digest=f"sha256:{'1' * 64}",
            iteration=1,
            settings=RepairTransactionSettings(max_projection_cells=1),
        )


def test_conflict_projection_includes_track_width_and_pairwise_clearance() -> None:
    snapshot, request, target, conflict = _inputs()
    fine_settings = replace(request.settings, grid_step_nm=100_000, max_grid_nodes=16_384)
    request = replace(request, settings=fine_settings)
    unsigned_target = replace(target, candidate_id=f"sha256:{'0' * 64}", settings=fine_settings)
    target = replace(
        unsigned_target,
        candidate_id=(
            f"sha256:{hashlib.sha256(canonical_candidate_bytes(unsigned_target)).hexdigest()}"
        ),
    )
    target_path = target.patch.paths[0].vertices
    assert target_path[0].y == target_path[-1].y

    # The two centrelines are distinct grid rows (so a centreline-only projection misses them),
    # but 200 nm apart. Their 200 nm widths plus the 100 nm class clearance require 300 nm.
    shifted_path = RoutePath(
        (
            PointNM(target_path[0].x + 1_000_000, target_path[0].y + 200_000),
            PointNM(target_path[-1].x - 1_000_000, target_path[-1].y + 200_000),
        )
    )
    conflict_patch = RoutePatch(
        net_id=conflict.patch.net_id,
        layer_id=conflict.patch.layer_id,
        width_nm=conflict.patch.width_nm,
        paths=(shifted_path,),
    )
    unsigned = replace(
        conflict,
        candidate_id=f"sha256:{'0' * 64}",
        patch=conflict_patch,
        cost=replace(
            conflict.cost,
            length_nm=conflict_patch.length_nm,
            bend_count=0,
            bend_cost_nm=0,
            total_cost_nm=conflict_patch.length_nm,
        ),
        metrics=replace(conflict.metrics, wire_length_nm=conflict_patch.length_nm),
        settings=fine_settings,
    )
    separated_conflict = replace(
        unsigned,
        candidate_id=f"sha256:{hashlib.sha256(canonical_candidate_bytes(unsigned)).hexdigest()}",
    )

    provenance = derive_repair_provenance(
        snapshot,
        request,
        target,
        (separated_conflict,),
        envelope_digest=f"sha256:{'1' * 64}",
        iteration=1,
        settings=RepairTransactionSettings(max_projection_cells=4_096),
    )

    origin = target_path[0]
    interior = ((target_path[0].x + 1_000_000 - origin.x) // 100_000, 0)
    assert interior in provenance.blocked_cells


def test_board_ir_projection_matches_reference_router_keepout_refusal() -> None:
    snapshot, request, target, conflict = _inputs()
    barrier = Keepout(
        id="keepout:repair-parity-barrier",
        layer_ids=(request.layer_id,),
        boundary=Ring(
            (
                PointNM(5_500_000, 0),
                PointNM(6_500_000, 0),
                PointNM(6_500_000, 10_000_000),
                PointNM(5_500_000, 10_000_000),
            )
        ),
        prohibit_tracks=True,
        prohibit_vias=True,
        prohibit_pads=False,
        prohibit_zones=False,
        prohibit_footprints=False,
    )
    blocked_snapshot = make_snapshot(
        replace(snapshot.content, keepouts=(*snapshot.content.keepouts, barrier))
    )
    blocked_request = replace(request, board_revision=blocked_snapshot.snapshot_digest)
    blocked_target = _rebase_candidate(target, blocked_snapshot.snapshot_digest)
    blocked_conflict = _rebase_candidate(conflict, blocked_snapshot.snapshot_digest)

    reference = AStarRouter().propose(blocked_snapshot, blocked_request)
    assert reference.candidate is None
    assert reference.diagnostic is not None
    assert reference.diagnostic.code is RouteFailureCode.NO_PATH

    provenance = derive_repair_provenance(
        blocked_snapshot,
        blocked_request,
        blocked_target,
        (blocked_conflict,),
        envelope_digest=f"sha256:{'1' * 64}",
        iteration=1,
        settings=RepairTransactionSettings(max_projection_cells=256),
    )
    local = provenance.local_request(RepairTransactionSettings(max_projection_cells=256))
    assert exact_local_repair(local).status is LocalRepairStatus.NO_PATH


@pytest.mark.parametrize("value", (0, 2, True, "1"))
def test_transaction_settings_are_strictly_bounded(value: object) -> None:
    with pytest.raises(ValueError):
        RepairTransactionSettings(max_attempts=value)  # type: ignore[arg-type]
