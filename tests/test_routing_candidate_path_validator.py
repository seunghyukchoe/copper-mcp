from __future__ import annotations

import hashlib
from dataclasses import replace

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
    CandidatePathValidationFailure,
    validate_candidate_path,
)
from copper_mcp.routing.contracts import (
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


def _snapshot(*, foreign_segment: bool = False) -> object:
    left = _pad("pad:route-left", ROUTE_NET, PointNM(1_000_000, 5_000_000))
    right = _pad("pad:route-right", ROUTE_NET, PointNM(9_000_000, 5_000_000))
    signal = NetClass(
        id="class:signal",
        name="Signal",
        clearance_nm=100_000,
        track_width_nm=200_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    segments: tuple[Segment, ...] = ()
    if foreign_segment:
        segments = (
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
                pad_ids=(left.id, right.id),
            ),
        ),
        pads=(left, right),
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


def _validate(snapshot: object, request: RouteRequest, candidate: RouteCandidate, **kwargs: object):
    return validate_candidate_path(
        snapshot,
        request,
        candidate,
        max_obstacle_checks=10_000,
        max_path_edges=64,
        **kwargs,
    )


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


def test_candidate_path_validator_distinguishes_stale_revision_from_infeasible_and_budget() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    direct = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )
    stale_request = replace(request, board_revision=f"sha256:{'e' * 64}")

    stale = _validate(snapshot, stale_request, direct)
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


def test_candidate_path_validator_honours_cancellation_and_deadline_before_geometry() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    direct = _candidate(
        request,
        (PointNM(1_000_000, 5_000_000), PointNM(9_000_000, 5_000_000)),
    )

    cancelled = _validate(snapshot, request, direct, cancelled=lambda: True)
    deadline = _validate(snapshot, request, direct, deadline_check=lambda: True)

    assert cancelled.failure is CandidatePathValidationFailure.CANCELLED
    assert deadline.failure is CandidatePathValidationFailure.DEADLINE_EXCEEDED
    assert cancelled.edge_checks == deadline.edge_checks == 0
    assert cancelled.obstacle_checks == deadline.obstacle_checks == 0


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
