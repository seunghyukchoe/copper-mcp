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
from copper_mcp.routing.repair_provenance import _CAPABILITY, derive_repair_provenance
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
    assert provenance.digest.startswith("sha256:")
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
