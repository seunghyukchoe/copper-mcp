from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

import copper_mcp.routing.repair_provenance as provenance_module
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
    _TREE_PROVENANCE_CAPABILITY,
    _TREE_SELECTION_CAPABILITY,
    CoordinatorTreeRepairProvenance,
    CoordinatorTreeRepairSelection,
    _candidate_bounds,
    _TreeRepairProvenanceError,
    derive_repair_provenance,
    derive_tree_repair_provenance,
    derive_tree_repair_selection,
)
from scripts import exact_local_repair_gate_fixture as fixture
from tests.test_routing_congestion import _multipin_requests, _multipin_shifted_snapshot


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


def _tree_inputs() -> tuple[object, object, RouteCandidate, RouteCandidate]:
    snapshot = _multipin_shifted_snapshot()
    target_request, conflict_request = _multipin_requests(snapshot)
    router = AStarRouter()
    target = router.propose(snapshot, target_request).candidate
    conflict = router.propose(snapshot, conflict_request).candidate
    assert target is not None and conflict is not None
    assert target.pad_count == 3 and len(target.patch.paths) == 2
    assert conflict.pad_count == 2 and len(conflict.patch.paths) == 1
    return snapshot, target_request, target, conflict


def _tree_selection_and_provenance() -> tuple[
    object,
    object,
    RouteCandidate,
    RouteCandidate,
    CoordinatorTreeRepairSelection,
    CoordinatorTreeRepairProvenance,
]:
    snapshot, request, target, conflict = _tree_inputs()
    selection = derive_tree_repair_selection(
        snapshot,
        request,
        target,
        conflict,
        envelope_digest=f"sha256:{'1' * 64}",
        iteration=1,
        target_path_index=0,
        conflict_path_index=0,
        responsibility_digest=f"sha256:{'2' * 64}",
        responsibility_checks=1,
    )
    provenance = derive_tree_repair_provenance(
        snapshot,
        request,
        target,
        (conflict,),
        selection,
        envelope_digest=f"sha256:{'1' * 64}",
        iteration=1,
        settings=RepairTransactionSettings(max_projection_cells=512),
    )
    return snapshot, request, target, conflict, selection, provenance


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


def test_tree_repair_selection_and_provenance_are_deterministic_and_complete() -> None:
    snapshot, request, target, conflict = _tree_inputs()
    settings = RepairTransactionSettings(max_projection_cells=512)
    selections = tuple(
        derive_tree_repair_selection(
            snapshot,
            request,
            target,
            conflict,
            envelope_digest=f"sha256:{'1' * 64}",
            iteration=1,
            target_path_index=0,
            conflict_path_index=0,
            responsibility_digest=f"sha256:{'2' * 64}",
            responsibility_checks=1,
        )
        for _ in range(10)
    )
    selection = selections[0]
    assert selections == (selection,) * 10
    assert selection.digest == (
        "sha256:4055d95d5768f0e77cacd5961fc2188bdb80e2081f7ced6b070fb5050a4cbe51"
    )
    assert selection.target_candidate_id == target.candidate_id
    assert selection.target_path_count == 2
    assert selection.target_path_index == 0
    assert selection.target_path_start == target.patch.paths[0].vertices[0]
    assert selection.target_path_end == target.patch.paths[0].vertices[-1]
    assert selection.conflict_candidate_id == conflict.candidate_id
    assert selection.conflict_path_count == 1
    assert selection.conflict_path_index == 0
    assert selection.responsibility_digest == f"sha256:{'2' * 64}"
    assert selection.responsibility_checks == 1

    provenances = tuple(
        derive_tree_repair_provenance(
            snapshot,
            request,
            target,
            (conflict,),
            selection,
            envelope_digest=f"sha256:{'1' * 64}",
            iteration=1,
            settings=settings,
        )
        for _ in range(10)
    )
    provenance = provenances[0]
    assert provenances == (provenance,) * 10
    assert provenance.digest == (
        "sha256:ed3bc11f31713aec9c3ef11bceb71eb1c5f28e59a19c88f18c609953b6a4c92a"
    )
    assert provenance.selection == selection
    assert provenance.target_candidate_id == target.candidate_id
    assert provenance.conflicting_candidate_ids == (conflict.candidate_id,)
    assert provenance.target_path_count == 2
    assert provenance.target_path_index == 0
    assert provenance.target_path_digest == selection.target_path_digest
    assert provenance.target_path_start == selection.target_path_start
    assert provenance.target_path_end == selection.target_path_end
    assert provenance.responsibility_digest == selection.responsibility_digest
    assert provenance.responsibility_checks == selection.responsibility_checks
    local = provenance.local_request(settings)
    assert local.start == provenance.start
    assert local.end == provenance.end
    assert local.blocked_cells == provenance.blocked_cells
    assert {(-4, 0), (-3, 0), (-2, 0), (-1, 0)}.issubset(provenance.blocked_cells)
    assert provenance.start not in provenance.blocked_cells
    assert provenance.end not in provenance.blocked_cells


def test_tree_repair_factories_refuse_forged_index_candidate_and_revision() -> None:
    snapshot, request, target, conflict = _tree_inputs()
    common = {
        "envelope_digest": f"sha256:{'1' * 64}",
        "iteration": 1,
        "target_path_index": 0,
        "conflict_path_index": 0,
        "responsibility_digest": f"sha256:{'2' * 64}",
        "responsibility_checks": 1,
    }
    for overrides in (
        {"target_path_index": True},
        {"target_path_index": len(target.patch.paths)},
        {"conflict_path_index": -1},
        {"responsibility_checks": True},
        {"responsibility_checks": 0},
        {"responsibility_digest": f"sha256:{'Z' * 64}"},
    ):
        with pytest.raises(ValueError, match="tree repair selection"):
            derive_tree_repair_selection(
                snapshot,
                request,
                target,
                conflict,
                **(common | overrides),
            )

    forged_target = replace(target, candidate_id=f"sha256:{'3' * 64}")
    stale_target = _rebase_candidate(target, f"sha256:{'4' * 64}")
    stale_request = replace(request, board_revision=f"sha256:{'5' * 64}")
    for checked_request, checked_target in (
        (request, forged_target),
        (request, stale_target),
        (stale_request, target),
    ):
        with pytest.raises(ValueError, match="tree repair selection"):
            derive_tree_repair_selection(
                snapshot,
                checked_request,
                checked_target,
                conflict,
                **common,
            )


def test_tree_repair_path_endpoint_and_capability_tampering_cannot_build_a_request() -> None:
    snapshot, request, target, conflict, selection, provenance = _tree_selection_and_provenance()
    forged_selections = (
        replace(
            selection,
            target_path_index=1,
            _capability=_TREE_SELECTION_CAPABILITY,
        ),
        replace(
            selection,
            target_path_digest=f"sha256:{'6' * 64}",
            _capability=_TREE_SELECTION_CAPABILITY,
        ),
        replace(
            selection,
            target_path_start=PointNM(
                selection.target_path_start.x + request.settings.grid_step_nm,
                selection.target_path_start.y,
            ),
            _capability=_TREE_SELECTION_CAPABILITY,
        ),
        replace(
            selection,
            target_candidate_id=f"sha256:{'7' * 64}",
            _capability=_TREE_SELECTION_CAPABILITY,
        ),
        replace(
            selection,
            conflict_path_index=1,
            _capability=_TREE_SELECTION_CAPABILITY,
        ),
        replace(
            selection,
            conflict_path_digest=f"sha256:{'a' * 64}",
            _capability=_TREE_SELECTION_CAPABILITY,
        ),
        replace(
            selection,
            conflict_candidate_id=f"sha256:{'b' * 64}",
            _capability=_TREE_SELECTION_CAPABILITY,
        ),
        replace(
            selection,
            responsibility_digest=f"sha256:{'d' * 64}",
            _capability=_TREE_SELECTION_CAPABILITY,
        ),
        replace(
            selection,
            responsibility_checks=2,
            _capability=_TREE_SELECTION_CAPABILITY,
        ),
        replace(
            selection,
            snapshot_digest=f"sha256:{'8' * 64}",
            _capability=_TREE_SELECTION_CAPABILITY,
        ),
        replace(selection, _capability=object()),
    )
    for forged in forged_selections:
        with pytest.raises(ValueError, match="tree repair provenance"):
            derive_tree_repair_provenance(
                snapshot,
                request,
                target,
                (conflict,),
                forged,
                envelope_digest=f"sha256:{'1' * 64}",
                iteration=1,
                settings=RepairTransactionSettings(max_projection_cells=512),
            )
        with pytest.raises(ValueError, match="coordinator-derived"):
            replace(
                provenance,
                selection=forged,
                _capability=_TREE_PROVENANCE_CAPABILITY,
            ).local_request(RepairTransactionSettings(max_projection_cells=512))

    with pytest.raises(ValueError, match="coordinator-derived"):
        replace(provenance, _capability=object()).local_request(
            RepairTransactionSettings(max_projection_cells=512)
        )
    with pytest.raises(ValueError, match="coordinator-derived"):
        replace(
            provenance,
            target_candidate_id=f"sha256:{'9' * 64}",
            _capability=_TREE_PROVENANCE_CAPABILITY,
        ).local_request(RepairTransactionSettings(max_projection_cells=512))


def test_tree_conflict_projection_preflight_is_cumulative_and_precedes_enumeration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, target, conflict = _tree_inputs()
    remaining_limits: list[int] = []

    def bounded_probe(
        candidate: RouteCandidate,
        *,
        origin: PointNM,
        step: int,
        maximum: int,
    ) -> int:
        del candidate, origin, step
        remaining_limits.append(maximum)
        if maximum < 3:
            raise ValueError("tree repair projection exceeds its cell budget")
        return 3

    monkeypatch.setattr(
        provenance_module,
        "_tree_projection_cell_upper_bound",
        bounded_probe,
    )
    with pytest.raises(ValueError, match="cell budget"):
        provenance_module._preflight_tree_conflict_projection(
            (target, conflict),
            origin=target.patch.paths[0].vertices[0],
            step=target.settings.grid_step_nm,
            maximum=5,
        )
    assert remaining_limits == [5, 2]

    snapshot, request, target, conflict, selection, _ = _tree_selection_and_provenance()
    events: list[str] = []

    def refuse_preflight(*args: object, **kwargs: object) -> int:
        del args, kwargs
        events.append("preflight")
        raise ValueError("tree repair projection exceeds its cell budget")

    def forbidden_enumeration(*args: object, **kwargs: object) -> set[tuple[int, int]]:
        del args, kwargs
        events.append("enumeration")
        return set()

    monkeypatch.setattr(
        provenance_module,
        "_preflight_tree_conflict_projection",
        refuse_preflight,
    )
    monkeypatch.setattr(provenance_module, "_expanded_conflict_cells", forbidden_enumeration)
    with pytest.raises(ValueError, match="tree repair provenance"):
        derive_tree_repair_provenance(
            snapshot,
            request,
            target,
            (conflict,),
            selection,
            envelope_digest=f"sha256:{'1' * 64}",
            iteration=1,
            settings=RepairTransactionSettings(max_projection_cells=512),
        )
    assert events == ["preflight"]


def test_tree_board_ir_projection_refusal_preserves_consumed_work_and_cancellation() -> None:
    snapshot, request, target, conflict, selection, _ = _tree_selection_and_provenance()
    with pytest.raises(_TreeRepairProvenanceError) as budget_refusal:
        derive_tree_repair_provenance(
            snapshot,
            request,
            target,
            (conflict,),
            selection,
            envelope_digest=f"sha256:{'1' * 64}",
            iteration=1,
            settings=RepairTransactionSettings(
                max_projection_cells=512,
                max_validator_obstacle_checks=1,
            ),
        )
    assert budget_refusal.value.obstacle_checks == 1
    assert not budget_refusal.value.cancelled
    assert str(budget_refusal.value) == "tree repair provenance is invalid"

    cancellation_calls = 0

    def cancelled() -> bool:
        nonlocal cancellation_calls
        cancellation_calls += 1
        return cancellation_calls >= 8

    with pytest.raises(_TreeRepairProvenanceError) as cancellation_refusal:
        derive_tree_repair_provenance(
            snapshot,
            request,
            target,
            (conflict,),
            selection,
            envelope_digest=f"sha256:{'1' * 64}",
            iteration=1,
            settings=RepairTransactionSettings(max_projection_cells=512),
            cancelled=cancelled,
        )
    assert cancellation_calls == 8
    assert cancellation_refusal.value.obstacle_checks == 3
    assert cancellation_refusal.value.cancelled
    assert str(cancellation_refusal.value) == "tree repair provenance is invalid"


def test_tree_post_projection_refusal_preserves_successfully_consumed_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, request, target, conflict, selection, _ = _tree_selection_and_provenance()

    def refuse_after_projection(**_kwargs: object) -> str:
        raise RuntimeError("do not disclose late provenance failure")

    monkeypatch.setattr(provenance_module, "_tree_provenance_digest", refuse_after_projection)
    with pytest.raises(_TreeRepairProvenanceError) as refusal:
        derive_tree_repair_provenance(
            snapshot,
            request,
            target,
            (conflict,),
            selection,
            envelope_digest=f"sha256:{'1' * 64}",
            iteration=1,
            settings=RepairTransactionSettings(max_projection_cells=512),
        )

    assert refusal.value.obstacle_checks == 3
    assert not refusal.value.cancelled
    assert str(refusal.value) == "tree repair provenance is invalid"


def test_tree_projection_refusal_accounting_contract_is_closed() -> None:
    for obstacle_checks, cancelled in (
        (-1, False),
        (True, False),
        (4_097, False),
        (0, 0),
    ):
        with pytest.raises(ValueError, match="refusal accounting"):
            _TreeRepairProvenanceError(obstacle_checks, cancelled=cancelled)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", (0, 2, True, "1"))
def test_transaction_settings_are_strictly_bounded(value: object) -> None:
    with pytest.raises(ValueError):
        RepairTransactionSettings(max_attempts=value)  # type: ignore[arg-type]
