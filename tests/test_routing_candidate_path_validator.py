from __future__ import annotations

import hashlib
from dataclasses import replace

import copper_mcp.routing.candidate_path_validator as validator_module
from copper_mcp.board_ir import (
    ConstraintSet,
    Footprint,
    FootprintSide,
    Layer,
    Net,
    NetClass,
    NetClassAssignment,
    OutlineContour,
    Pad,
    PadKind,
    PadShape,
    PointNM,
    Ring,
    Segment,
    SourceInfo,
    make_content,
    make_snapshot,
)
from copper_mcp.routing.astar import canonical_candidate_bytes
from copper_mcp.routing.candidate_path_validator import (
    EXTERNAL_PATCH_TREE_ORDERING,
    CandidatePathValidationFailure,
    validate_candidate_patch,
    validate_candidate_path,
    validate_candidate_path_with_exact_off_grid_obstacle_fallback,
)
from copper_mcp.routing.contracts import (
    BATCHED_ONE_STEINER_ORDERING,
    SINGLE_PATH_ORDERING,
    AStarSettings,
    RouteCandidate,
    RouteCost,
    RouteMetrics,
    RoutePatch,
    RoutePath,
    RouteRequest,
)

LAYER = "layer:F.Cu"
ROUTE_NET = "net:route"
FOREIGN_NET = "net:foreign"
SOURCE = f"sha256:{'d' * 64}"


def _settings() -> AStarSettings:
    return AStarSettings(
        grid_step_nm=1_000_000,
        bend_penalty_nm=500_000,
        proximity_penalty_nm=0,
        max_grid_nodes=256,
        max_expansions=1_000,
        max_obstacles=32,
        max_obstacle_checks=10_000,
    )


def _pad(identifier: str, net_id: str, point: PointNM) -> Pad:
    return Pad(
        id=identifier,
        net_id=net_id,
        center=point,
        rotation_udeg=0,
        shape=PadShape.RECT,
        kind=PadKind.SMD,
        size_x_nm=200_000,
        size_y_nm=200_000,
        roundrect_radius_nm=None,
        drill_x_nm=None,
        drill_y_nm=None,
        layer_ids=(LAYER,),
    )


def _snapshot(
    *,
    foreign_segment: bool = False,
    same_net_stub: bool = False,
    multipin: bool = False,
) -> object:
    left = _pad("pad:route-left", ROUTE_NET, PointNM(1_000_000, 5_000_000))
    middle = _pad("pad:route-middle", ROUTE_NET, PointNM(5_000_000, 5_000_000))
    right = _pad("pad:route-right", ROUTE_NET, PointNM(9_000_000, 5_000_000))
    route_pads = (left, middle, right) if multipin else (left, right)
    signal = NetClass(
        id="class:signal",
        name="Signal",
        clearance_nm=100_000,
        track_width_nm=200_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    segments: tuple[Segment, ...] = ()
    if same_net_stub:
        segments += (
            Segment(
                id="segment:route-stub",
                net_id=ROUTE_NET,
                layer_id=LAYER,
                start=left.center,
                end=PointNM(4_000_000, 5_000_000),
                width_nm=200_000,
            ),
        )
    if foreign_segment:
        segments += (
            Segment(
                id="segment:foreign-wall",
                net_id=FOREIGN_NET,
                layer_id=LAYER,
                start=PointNM(5_000_000, 3_000_000),
                end=PointNM(5_000_000, 7_000_000),
                width_nm=200_000,
            ),
        )
    content = make_content(
        source=SourceInfo(format="test", revision=SOURCE, format_version="1", generator="test"),
        outline=(
            OutlineContour(
                id="contour:board",
                outer=Ring(
                    (
                        PointNM(0, 0),
                        PointNM(10_000_000, 0),
                        PointNM(10_000_000, 10_000_000),
                        PointNM(0, 10_000_000),
                    )
                ),
            ),
        ),
        copper_layers=(Layer(id=LAYER, name="F.Cu", index=0, kind="signal"),),
        nets=(Net(id=ROUTE_NET, name="ROUTE"), Net(id=FOREIGN_NET, name="FOREIGN")),
        constraints=ConstraintSet(
            net_classes=(signal,),
            assignments=(
                NetClassAssignment(net_id=ROUTE_NET, net_class_id=signal.id),
                NetClassAssignment(net_id=FOREIGN_NET, net_class_id=signal.id),
            ),
        ),
        footprints=(
            Footprint(
                id="footprint:route",
                origin=left.center,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=tuple(pad.id for pad in route_pads),
            ),
        ),
        pads=route_pads,
        segments=segments,
    )
    return make_snapshot(content)


def _request(snapshot: object) -> RouteRequest:
    assert hasattr(snapshot, "snapshot_digest")
    return RouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=ROUTE_NET,
        layer_id=LAYER,
        seed=7,
        settings=_settings(),
    )


def _candidate(request: RouteRequest, vertices: tuple[PointNM, ...]) -> RouteCandidate:
    path = RoutePath(vertices=vertices)
    patch = RoutePatch(net_id=ROUTE_NET, layer_id=LAYER, width_nm=200_000, paths=(path,))
    cost = RouteCost(
        length_nm=patch.length_nm,
        bend_count=patch.bend_count,
        bend_cost_nm=patch.bend_count * request.settings.bend_penalty_nm,
        proximity_steps=0,
        proximity_cost_nm=0,
        via_cost_nm=0,
        total_cost_nm=patch.length_nm + patch.bend_count * request.settings.bend_penalty_nm,
    )
    candidate = RouteCandidate(
        candidate_id=f"sha256:{'0' * 64}",
        base_revision=request.board_revision,
        start_pad_id="pad:route-left",
        end_pad_id="pad:route-right",
        patch=patch,
        cost=cost,
        metrics=RouteMetrics(
            hard_internal_violations=0,
            unrouted_connections=0,
            vias=0,
            wire_length_nm=patch.length_nm,
            expanded_states=0,
            peak_frontier_states=1,
            obstacle_checks=0,
        ),
        settings=request.settings,
        router_version="candidate-path-validator-test-v1",
        policy="coordinator-derived-test-v1",
        seed=request.seed,
        pad_count=2,
        ordering_policy=SINGLE_PATH_ORDERING,
    )
    return replace(
        candidate,
        candidate_id=f"sha256:{hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()}",
    )


def _tree_candidate(
    request: RouteRequest,
    paths: tuple[RoutePath, ...],
    *,
    ordering_policy: str = BATCHED_ONE_STEINER_ORDERING,
) -> RouteCandidate:
    patch = RoutePatch(net_id=ROUTE_NET, layer_id=LAYER, width_nm=200_000, paths=paths)
    cost = RouteCost(
        length_nm=patch.length_nm,
        bend_count=patch.bend_count,
        bend_cost_nm=patch.bend_count * request.settings.bend_penalty_nm,
        proximity_steps=0,
        proximity_cost_nm=0,
        via_cost_nm=0,
        total_cost_nm=patch.length_nm + patch.bend_count * request.settings.bend_penalty_nm,
    )
    candidate = RouteCandidate(
        candidate_id=f"sha256:{'0' * 64}",
        base_revision=request.board_revision,
        start_pad_id="pad:route-left",
        end_pad_id="pad:route-right",
        patch=patch,
        cost=cost,
        metrics=RouteMetrics(
            hard_internal_violations=0,
            unrouted_connections=0,
            vias=0,
            wire_length_nm=patch.length_nm,
            expanded_states=8,
            peak_frontier_states=4,
            obstacle_checks=0,
        ),
        settings=request.settings,
        router_version="candidate-tree-validator-test-v1",
        policy="coordinator-derived-tree-test-v1",
        seed=request.seed,
        pad_count=3,
        ordering_policy=ordering_policy,
    )
    return _with_candidate_identity(candidate)


def _rebuild_tree(
    candidate: RouteCandidate,
    paths: tuple[RoutePath, ...],
) -> RouteCandidate:
    patch = replace(candidate.patch, paths=paths)
    cost = replace(
        candidate.cost,
        length_nm=patch.length_nm,
        bend_count=patch.bend_count,
        bend_cost_nm=patch.bend_count * candidate.settings.bend_penalty_nm,
        total_cost_nm=patch.length_nm + patch.bend_count * candidate.settings.bend_penalty_nm,
    )
    return _with_candidate_identity(
        replace(
            candidate,
            patch=patch,
            cost=cost,
            metrics=replace(candidate.metrics, wire_length_nm=patch.length_nm),
        )
    )


def _with_candidate_identity(candidate: RouteCandidate) -> RouteCandidate:
    unsigned = replace(candidate, candidate_id=f"sha256:{'0' * 64}")
    return replace(
        unsigned,
        candidate_id=f"sha256:{hashlib.sha256(canonical_candidate_bytes(unsigned)).hexdigest()}",
    )


def _validate(snapshot: object, request: RouteRequest, candidate: RouteCandidate, **kwargs: object):
    return validate_candidate_path(
        snapshot,
        request,
        candidate,
        max_obstacle_checks=10_000,
        max_path_edges=64,
        **kwargs,
    )


def _tree_inputs() -> tuple[object, RouteRequest, RouteCandidate, RouteCandidate]:
    snapshot = _snapshot(multipin=True)
    request = _request(snapshot)
    middle = PointNM(5_000_000, 5_000_000)
    untouched = RoutePath((middle, PointNM(9_000_000, 5_000_000)))
    original = _tree_candidate(
        request,
        (
            RoutePath((PointNM(1_000_000, 5_000_000), middle)),
            untouched,
        ),
    )
    reconstructed = _rebuild_tree(
        original,
        (
            RoutePath(
                (
                    PointNM(1_000_000, 5_000_000),
                    PointNM(1_000_000, 4_000_000),
                    PointNM(5_000_000, 4_000_000),
                    middle,
                )
            ),
            untouched,
        ),
    )
    return snapshot, request, original, reconstructed


def _validate_tree(
    snapshot: object,
    request: RouteRequest,
    original: RouteCandidate,
    reconstructed: RouteCandidate,
    **kwargs: object,
):
    return validator_module._validate_negotiated_candidate_patch(
        snapshot,
        request,
        reconstructed,
        original,
        selected_path_index=0,
        max_obstacle_checks=10_000,
        max_path_edges=64,
        **kwargs,
    )


def test_public_candidate_patch_contract_remains_external_ordering_only() -> None:
    snapshot, request, original, _ = _tree_inputs()
    external = _with_candidate_identity(
        replace(original, ordering_policy=EXTERNAL_PATCH_TREE_ORDERING)
    )

    accepted = validate_candidate_patch(
        snapshot,
        request,
        external,
        max_obstacle_checks=10_000,
        max_path_edges=64,
    )
    internal = validate_candidate_patch(
        snapshot,
        request,
        original,
        max_obstacle_checks=10_000,
        max_path_edges=64,
    )

    assert accepted.accepted
    assert accepted.edge_checks == 8
    assert internal.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
    assert internal.edge_checks == internal.obstacle_checks == 0


def test_private_negotiated_tree_accepts_one_bound_path_replacement_repeatably() -> None:
    snapshot, request, original, reconstructed = _tree_inputs()

    results = tuple(_validate_tree(snapshot, request, original, reconstructed) for _ in range(3))

    assert results == (results[0],) * 3
    assert results[0].accepted
    assert results[0].edge_checks == 28
    assert reconstructed.patch.paths[1] == original.patch.paths[1]
    assert reconstructed.patch.paths[0] != original.patch.paths[0]


def test_private_negotiated_tree_preflights_one_shared_exact_edge_ledger(
    monkeypatch,
) -> None:
    snapshot, request, original, reconstructed = _tree_inputs()
    topology_expansions = 0
    original_unit_edge_core = validator_module._unit_edge_core

    def counted_unit_edge_core(*args: object, **kwargs: object):
        nonlocal topology_expansions
        topology_expansions += 1
        return original_unit_edge_core(*args, **kwargs)

    monkeypatch.setattr(validator_module, "_unit_edge_core", counted_unit_edge_core)
    refused = validator_module._validate_negotiated_candidate_patch(
        snapshot,
        request,
        reconstructed,
        original,
        selected_path_index=0,
        max_obstacle_checks=10_000,
        max_path_edges=27,
    )

    assert refused.failure is CandidatePathValidationFailure.BUDGET_EXHAUSTED
    assert refused.edge_checks == refused.obstacle_checks == 0
    assert topology_expansions == 0

    accepted = validator_module._validate_negotiated_candidate_patch(
        snapshot,
        request,
        reconstructed,
        original,
        selected_path_index=0,
        max_obstacle_checks=10_000,
        max_path_edges=28,
    )

    assert accepted.accepted
    assert accepted.edge_checks == 28
    assert topology_expansions == 18


def test_private_negotiated_tree_charges_contacts_and_stops_mid_scan(monkeypatch) -> None:
    snapshot, request, original, reconstructed = _tree_inputs()
    contact_checks = 0
    cancel_after_first = False
    cancel_now = False
    original_charge = validator_module._charge_tree_contact_predicate

    def counted_charge(work: object) -> None:
        nonlocal contact_checks, cancel_now
        contact_checks += 1
        original_charge(work)
        if cancel_after_first and contact_checks == 1:
            cancel_now = True

    monkeypatch.setattr(
        validator_module,
        "_charge_tree_contact_predicate",
        counted_charge,
    )
    accepted = _validate_tree(snapshot, request, original, reconstructed)

    assert accepted.accepted
    assert contact_checks > 0
    assert accepted.obstacle_checks >= contact_checks
    full_contact_checks = contact_checks

    contact_checks = 0
    cancel_after_first = True
    cancelled = _validate_tree(
        snapshot,
        request,
        original,
        reconstructed,
        cancelled=lambda: cancel_now,
    )

    assert cancelled.failure is CandidatePathValidationFailure.CANCELLED
    assert cancelled.edge_checks == 18
    assert cancelled.obstacle_checks > 0
    assert contact_checks == 2
    assert contact_checks < full_contact_checks


def test_private_negotiated_tree_rejects_a_new_contact_cycle_with_an_untouched_path() -> None:
    snapshot, request, original, _ = _tree_inputs()
    looped = _rebuild_tree(
        original,
        (
            RoutePath(
                (
                    PointNM(1_000_000, 5_000_000),
                    PointNM(1_000_000, 6_000_000),
                    PointNM(7_000_000, 6_000_000),
                    PointNM(7_000_000, 4_000_000),
                    PointNM(5_000_000, 4_000_000),
                    PointNM(5_000_000, 5_000_000),
                )
            ),
            original.patch.paths[1],
        ),
    )

    result = _validate_tree(snapshot, request, original, looped)

    assert result.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
    assert result.edge_checks == 22
    assert result.obstacle_checks > 0


def test_private_negotiated_tree_binds_all_non_derived_candidate_state() -> None:
    snapshot, request, original, reconstructed = _tree_inputs()
    altered_untouched = _rebuild_tree(
        reconstructed,
        (
            reconstructed.patch.paths[0],
            RoutePath(
                (
                    PointNM(5_000_000, 5_000_000),
                    PointNM(5_000_000, 6_000_000),
                    PointNM(9_000_000, 6_000_000),
                    PointNM(9_000_000, 5_000_000),
                )
            ),
        ),
    )
    dropped_untouched = _rebuild_tree(reconstructed, (reconstructed.patch.paths[0],))
    mutations = (
        _with_candidate_identity(replace(reconstructed, base_revision=f"sha256:{'e' * 64}")),
        _with_candidate_identity(
            replace(reconstructed, patch=replace(reconstructed.patch, net_id=FOREIGN_NET))
        ),
        _with_candidate_identity(
            replace(reconstructed, patch=replace(reconstructed.patch, layer_id="layer:B.Cu"))
        ),
        _with_candidate_identity(
            replace(reconstructed, patch=replace(reconstructed.patch, width_nm=300_000))
        ),
        _with_candidate_identity(replace(reconstructed, start_pad_id="pad:route-middle")),
        _with_candidate_identity(replace(reconstructed, end_pad_id="pad:route-middle")),
        _with_candidate_identity(replace(reconstructed, pad_count=4)),
        _with_candidate_identity(
            replace(reconstructed, settings=replace(reconstructed.settings, max_expansions=999))
        ),
        _with_candidate_identity(replace(reconstructed, seed=8)),
        _with_candidate_identity(replace(reconstructed, router_version="forged-router-v1")),
        _with_candidate_identity(replace(reconstructed, policy="forged-policy-v1")),
        _with_candidate_identity(
            replace(
                reconstructed,
                cost=replace(
                    reconstructed.cost,
                    proximity_steps=reconstructed.cost.proximity_steps + 1,
                ),
            )
        ),
        _with_candidate_identity(
            replace(
                reconstructed,
                metrics=replace(
                    reconstructed.metrics,
                    expanded_states=reconstructed.metrics.expanded_states + 1,
                ),
            )
        ),
        _with_candidate_identity(
            replace(reconstructed, ordering_policy=EXTERNAL_PATCH_TREE_ORDERING)
        ),
        _with_candidate_identity(replace(reconstructed, fill_binding=f"sha256:{'c' * 64}")),
        dropped_untouched,
        altered_untouched,
    )

    for mutation in mutations:
        result = _validate_tree(snapshot, request, original, mutation)
        assert result.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
        assert result.edge_checks == result.obstacle_checks == 0


def test_private_negotiated_tree_rejects_disconnected_complete_topology() -> None:
    snapshot, request, original, reconstructed = _tree_inputs()
    disconnected = _rebuild_tree(
        reconstructed,
        (
            RoutePath((PointNM(2_000_000, 2_000_000), PointNM(3_000_000, 2_000_000))),
            reconstructed.patch.paths[1],
        ),
    )

    result = _validate_tree(snapshot, request, original, disconnected)

    assert result.failure is CandidatePathValidationFailure.INFEASIBLE
    assert result.edge_checks == 18


def test_private_negotiated_tree_refuses_forged_or_stale_identities() -> None:
    snapshot, request, original, reconstructed = _tree_inputs()
    forged_original = replace(original, candidate_id=f"sha256:{'f' * 64}")
    forged_reconstructed = replace(reconstructed, candidate_id=f"sha256:{'f' * 64}")

    for checked_original, checked_reconstructed in (
        (forged_original, reconstructed),
        (original, forged_reconstructed),
    ):
        forged = _validate_tree(
            snapshot,
            request,
            checked_original,
            checked_reconstructed,
        )
        assert forged.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
        assert forged.edge_checks == forged.obstacle_checks == 0

    stale_revision = f"sha256:{'e' * 64}"
    stale_request = replace(request, board_revision=stale_revision)
    stale_original = _with_candidate_identity(replace(original, base_revision=stale_revision))
    stale_reconstructed = _with_candidate_identity(
        replace(reconstructed, base_revision=stale_revision)
    )
    stale = _validate_tree(
        snapshot,
        stale_request,
        stale_original,
        stale_reconstructed,
    )
    assert stale.failure is CandidatePathValidationFailure.STALE_REVISION
    assert stale.edge_checks == stale.obstacle_checks == 0


def test_private_negotiated_tree_honours_budget_and_cancellation_before_identity(
    monkeypatch,
) -> None:
    snapshot, request, original, reconstructed = _tree_inputs()

    def identity_must_not_run(_: RouteCandidate) -> None:
        raise AssertionError("preflight refusals must not hash candidate trees")

    monkeypatch.setattr(validator_module, "verify_candidate_id", identity_must_not_run)
    budget = validator_module._validate_negotiated_candidate_patch(
        snapshot,
        request,
        reconstructed,
        original,
        selected_path_index=0,
        max_obstacle_checks=10_000,
        max_path_edges=1,
    )
    cancelled = _validate_tree(
        snapshot,
        request,
        original,
        reconstructed,
        cancelled=lambda: True,
    )

    assert budget.failure is CandidatePathValidationFailure.BUDGET_EXHAUSTED
    assert budget.edge_checks == budget.obstacle_checks == 0
    assert cancelled.failure is CandidatePathValidationFailure.CANCELLED
    assert cancelled.edge_checks == cancelled.obstacle_checks == 0

    for selected_path_index in (True, -1, 2):
        invalid_index = validator_module._validate_negotiated_candidate_patch(
            snapshot,
            request,
            reconstructed,
            original,
            selected_path_index=selected_path_index,
            max_obstacle_checks=10_000,
            max_path_edges=64,
        )
        assert invalid_index.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
        assert invalid_index.edge_checks == invalid_index.obstacle_checks == 0


def test_candidate_path_validator_accepts_a_valid_board_ir_detour_repeatably() -> None:
    snapshot = _snapshot(foreign_segment=True)
    request = _request(snapshot)
    candidate = _candidate(
        request,
        (
            PointNM(1_000_000, 5_000_000),
            PointNM(1_000_000, 1_000_000),
            PointNM(9_000_000, 1_000_000),
            PointNM(9_000_000, 5_000_000),
        ),
    )

    results = tuple(_validate(snapshot, request, candidate) for _ in range(10))
    result = results[0]

    assert results == (result,) * 10
    assert result.accepted
    assert result.failure is None
    assert result.edge_checks == 16
    assert result.obstacle_checks > 0


def test_candidate_path_validator_rejects_an_adversarial_foreign_copper_crossing() -> None:
    snapshot = _snapshot(foreign_segment=True)
    request = _request(snapshot)
    direct = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )

    result = _validate(snapshot, request, direct)

    assert not result.accepted
    assert result.failure is CandidatePathValidationFailure.OBSTACLE_VIOLATION
    assert result.edge_checks == 4
    assert result.diagnostic == "candidate path violates the Board IR obstacle authority"


def test_exact_off_grid_fallback_only_upgrades_a_proven_one_nanometre_incursion() -> None:
    snapshot = _snapshot(foreign_segment=True)
    request = replace(
        _request(snapshot),
        settings=replace(_settings(), grid_step_nm=100_000, max_grid_nodes=20_000),
    )
    boundary_vertices = (
        PointNM(1_000_000, 5_000_000),
        PointNM(1_000_000, 2_700_000),
        PointNM(9_000_000, 2_700_000),
        PointNM(9_000_000, 5_000_000),
    )
    incursion = _candidate(
        request,
        tuple(
            PointNM(point.x, point.y + 1 if point.y == 2_700_000 else point.y)
            for point in boundary_vertices
        ),
    )
    legal_off_grid = _candidate(
        request,
        tuple(
            PointNM(point.x, point.y - 1 if point.y == 2_700_000 else point.y)
            for point in boundary_vertices
        ),
    )

    intruding = validate_candidate_path_with_exact_off_grid_obstacle_fallback(
        snapshot,
        request,
        incursion,
        max_obstacle_checks=10_000,
        max_path_edges=256,
    )
    legal = validate_candidate_path_with_exact_off_grid_obstacle_fallback(
        snapshot,
        request,
        legal_off_grid,
        max_obstacle_checks=10_000,
        max_path_edges=256,
    )

    assert intruding.failure is CandidatePathValidationFailure.OBSTACLE_VIOLATION
    assert legal.failure is CandidatePathValidationFailure.UNSUPPORTED_GEOMETRY
    assert intruding.obstacle_checks <= 10_000
    assert legal.obstacle_checks <= 10_000


def test_candidate_path_validator_accepts_a_legal_same_net_attachment_node() -> None:
    snapshot = _snapshot(same_net_stub=True)
    request = _request(snapshot)
    candidate = _candidate(
        request,
        (PointNM(4_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )

    result = _validate(snapshot, request, candidate)

    assert result.accepted
    assert result.edge_checks == 5


def test_candidate_path_validator_rejects_a_foreign_copper_endpoint() -> None:
    snapshot = _snapshot(foreign_segment=True)
    request = _request(snapshot)
    candidate = _candidate(
        request,
        (
            PointNM(5_000_000, 3_000_000),
            PointNM(9_000_000, 3_000_000),
            PointNM(9_000_000, 5_000_000),
        ),
    )

    result = _validate(snapshot, request, candidate)

    assert result.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
    assert result.edge_checks == 0


def test_candidate_path_validator_distinguishes_stale_revision_from_infeasible_and_budget() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    direct = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    stale_request = replace(request, board_revision=f"sha256:{'e' * 64}")
    stale_candidate = _with_candidate_identity(
        replace(direct, base_revision=stale_request.board_revision)
    )

    stale = _validate(snapshot, stale_request, stale_candidate)
    budget = validate_candidate_path(
        snapshot,
        request,
        direct,
        max_obstacle_checks=10_000,
        max_path_edges=2,
    )

    assert stale.failure is CandidatePathValidationFailure.STALE_REVISION
    assert stale.edge_checks == 0
    assert budget.failure is CandidatePathValidationFailure.BUDGET_EXHAUSTED
    assert budget.edge_checks == 0
    assert budget.diagnostic != stale.diagnostic


def test_candidate_path_validator_rejects_stale_candidate_before_board_ir_preparation(
    monkeypatch,
) -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    direct = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    stale_candidate = _with_candidate_identity(replace(direct, base_revision=f"sha256:{'e' * 64}"))

    def prepare_must_not_run(*_: object) -> object:
        raise AssertionError("request-mismatched candidates must not prepare Board IR")

    monkeypatch.setattr(validator_module, "_prepare", prepare_must_not_run)

    result = _validate(snapshot, request, stale_candidate)

    assert result.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
    assert result.edge_checks == result.obstacle_checks == 0


def test_candidate_path_validator_defers_stale_request_until_board_ir_preparation(
    monkeypatch,
) -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    direct = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    stale_request = replace(request, board_revision=f"sha256:{'e' * 64}")
    stale_candidate = _with_candidate_identity(
        replace(direct, base_revision=stale_request.board_revision)
    )

    prepare_calls = 0
    reference_prepare = validator_module._prepare

    def observe_prepare(*args: object) -> object:
        nonlocal prepare_calls
        prepare_calls += 1
        return reference_prepare(*args)

    monkeypatch.setattr(validator_module, "_prepare", observe_prepare)

    result = _validate(snapshot, stale_request, stale_candidate)

    assert prepare_calls == 1
    assert result.failure is CandidatePathValidationFailure.STALE_REVISION
    assert result.edge_checks == result.obstacle_checks == 0


def test_candidate_path_validator_defers_mutated_snapshot_digest_to_canonical_preparation(
    monkeypatch,
) -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    direct = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    object.__setattr__(snapshot, "snapshot_digest", f"sha256:{'e' * 64}")
    prepare_calls = 0
    reference_prepare = validator_module._prepare

    def observe_prepare(*args: object) -> object:
        nonlocal prepare_calls
        prepare_calls += 1
        return reference_prepare(*args)

    monkeypatch.setattr(validator_module, "_prepare", observe_prepare)

    result = _validate(snapshot, request, direct)

    assert prepare_calls == 1
    assert result.failure is CandidatePathValidationFailure.INVALID_REQUEST
    assert result.edge_checks == result.obstacle_checks == 0


def test_candidate_path_validator_rejects_mismatched_candidate_before_board_ir_preparation(
    monkeypatch,
) -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    direct = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    mismatched_candidate = _with_candidate_identity(
        replace(direct, patch=replace(direct.patch, net_id=FOREIGN_NET))
    )

    def prepare_must_not_run(*_: object) -> object:
        raise AssertionError("request-mismatched candidates must not prepare Board IR")

    monkeypatch.setattr(validator_module, "_prepare", prepare_must_not_run)

    result = _validate(snapshot, request, mismatched_candidate)

    assert result.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
    assert result.edge_checks == result.obstacle_checks == 0


def test_candidate_path_validator_preserves_problem_derived_unsupported_diagnostic() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    direct = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    unsupported_request = replace(request, layer_id="layer:missing")
    unsupported_candidate = _with_candidate_identity(
        replace(
            direct,
            patch=replace(direct.patch, layer_id=unsupported_request.layer_id),
        )
    )

    result = _validate(snapshot, unsupported_request, unsupported_candidate)

    assert result.failure is CandidatePathValidationFailure.UNSUPPORTED_GEOMETRY
    assert result.edge_checks == result.obstacle_checks == 0


def test_candidate_path_validator_honours_cancellation_and_deadline_before_geometry(
    monkeypatch,
) -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    direct = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )

    def identity_must_not_run(_: RouteCandidate) -> None:
        raise AssertionError("pre-cancelled candidate must not be canonicalized or hashed")

    monkeypatch.setattr(validator_module, "verify_candidate_id", identity_must_not_run)

    cancelled = _validate(snapshot, request, direct, cancelled=lambda: True)
    deadline = _validate(snapshot, request, direct, deadline_check=lambda: True)

    assert cancelled.failure is CandidatePathValidationFailure.CANCELLED
    assert deadline.failure is CandidatePathValidationFailure.DEADLINE_EXCEEDED
    assert cancelled.edge_checks == deadline.edge_checks == 0
    assert cancelled.obstacle_checks == deadline.obstacle_checks == 0


def test_candidate_path_validator_preflights_oversized_path_before_identity_work(
    monkeypatch,
) -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    candidate = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    oversized_vertices = tuple(
        PointNM(1_000_000 + (index % 2) * 1_000_000, 1_000_000 + index * 1_000_000)
        for index in range(66)
    )
    object.__setattr__(candidate.patch.paths[0], "vertices", oversized_vertices)

    def identity_must_not_run(_: RouteCandidate) -> None:
        raise AssertionError("oversized candidate must not be canonicalized or hashed")

    monkeypatch.setattr(validator_module, "verify_candidate_id", identity_must_not_run)

    result = _validate(snapshot, request, candidate)

    assert result.failure is CandidatePathValidationFailure.BUDGET_EXHAUSTED
    assert result.edge_checks == result.obstacle_checks == 0


def test_candidate_path_validator_preflights_out_of_range_scalars_before_identity_work(
    monkeypatch,
) -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    candidate = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    object.__setattr__(candidate.cost, "length_nm", 1 << 100)

    def identity_must_not_run(_: RouteCandidate) -> None:
        raise AssertionError("out-of-range scalar must not be canonicalized or hashed")

    monkeypatch.setattr(validator_module, "verify_candidate_id", identity_must_not_run)

    result = _validate(snapshot, request, candidate)

    assert result.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
    assert result.edge_checks == result.obstacle_checks == 0


def test_candidate_path_validator_checks_cancellation_before_success_publication(
    monkeypatch,
) -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    candidate = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    cancel_late = False
    checked_edges = 0
    reference_edge_is_legal = validator_module._edge_is_legal

    def cancel_after_final_edge(*args: object) -> bool:
        nonlocal cancel_late, checked_edges
        legal = reference_edge_is_legal(*args)
        checked_edges += 1
        if checked_edges == 8:
            cancel_late = True
        return legal

    monkeypatch.setattr(validator_module, "_edge_is_legal", cancel_after_final_edge)

    result = _validate(snapshot, request, candidate, cancelled=lambda: cancel_late)

    assert checked_edges == 8
    assert result.failure is CandidatePathValidationFailure.CANCELLED
    assert result.edge_checks == 8


def test_candidate_path_validator_rejects_repeated_vertices_and_self_crossing_paths() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    candidate = _candidate(
        request,
        (
            PointNM(1_000_000, 5_000_000),
            PointNM(1_000_000, 4_000_000),
            PointNM(2_000_000, 4_000_000),
            PointNM(2_000_000, 5_000_000),
            PointNM(1_000_000, 5_000_000),
            PointNM(1_000_000, 6_000_000),
            PointNM(9_000_000, 6_000_000),
            PointNM(9_000_000, 5_000_000),
        ),
    )

    result = _validate(snapshot, request, candidate)

    assert result.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
    assert result.edge_checks == 3


def test_candidate_path_validator_rejects_a_hostile_zero_length_edge() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    candidate = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    object.__setattr__(
        candidate.patch.paths[0],
        "vertices",
        (
            PointNM(1_000_000, 5_000_000),
            PointNM(1_000_000, 5_000_000),
            PointNM(9_000_000, 5_000_000),
        ),
    )

    result = _validate(snapshot, request, candidate)

    assert result.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
    assert result.edge_checks == result.obstacle_checks == 0


def test_candidate_path_validator_rejects_tampered_width_and_non_lattice_geometry() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    direct = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    wrong_patch = replace(direct.patch, width_nm=100_000)
    wrong_width = replace(direct, patch=wrong_patch, candidate_id=f"sha256:{'0' * 64}")
    wrong_width = replace(
        wrong_width,
        candidate_id=f"sha256:{hashlib.sha256(canonical_candidate_bytes(wrong_width)).hexdigest()}",
    )
    off_grid = _candidate(
        request,
        (
            PointNM(1_000_000, 5_000_000),
            PointNM(1_000_000, 4_000_000),
            PointNM(1_500_000, 4_000_000),
            PointNM(1_500_000, 3_000_000),
            PointNM(9_000_000, 3_000_000),
            PointNM(9_000_000, 5_000_000),
        ),
    )

    assert (
        _validate(snapshot, request, wrong_width).failure
        is CandidatePathValidationFailure.INVALID_CANDIDATE
    )
    assert (
        _validate(snapshot, request, off_grid).failure
        is CandidatePathValidationFailure.UNSUPPORTED_GEOMETRY
    )


def test_candidate_path_validator_refuses_a_candidate_routed_under_verified_fill() -> None:
    """This validator models zones by their envelope and holds no fill evidence (ADR-0103).

    Accepting a fill-bound candidate would validate a path under an obstacle model stricter
    than the one that produced it, and report the disagreement as the foreign candidate's
    fault - which is issue #163 with the blame moved. The identity is recomputed over the
    binding, so this is a refusal on the recorded model and not on a corrupted digest.
    """

    snapshot = _snapshot(foreign_segment=True)
    request = _request(snapshot)
    legal = (
        PointNM(1_000_000, 5_000_000),
        PointNM(1_000_000, 1_000_000),
        PointNM(9_000_000, 1_000_000),
        PointNM(9_000_000, 5_000_000),
    )
    accepted = _candidate(request, legal)
    fill_bound = _with_candidate_identity(replace(accepted, fill_binding=f"sha256:{'c' * 64}"))

    assert _validate(snapshot, request, accepted).accepted
    result = _validate(snapshot, request, fill_bound)

    assert not result.accepted
    assert result.failure is CandidatePathValidationFailure.INVALID_CANDIDATE
