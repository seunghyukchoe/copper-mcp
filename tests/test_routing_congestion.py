from __future__ import annotations

import pytest

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
    SourceInfo,
    make_content,
    make_snapshot,
)
from copper_mcp.routing import (
    COMPONENT_MST_ORDERING,
    AStarRouter,
    AStarSettings,
    CongestionLedger,
    NegotiatedRoutingRequest,
    NegotiatedRoutingStatus,
    RouteCandidate,
    RouteCost,
    RouteDiagnostic,
    RouteFailureCode,
    RouteMetrics,
    RoutePatch,
    RoutePath,
    RouteRequest,
    RouteResult,
    negotiate_routes,
)
from copper_mcp.routing.physical_clearance import (
    PhysicalClearanceFailure,
    verify_negotiated_physical_clearance,
)

BOARD_SOURCE = f"sha256:{'c' * 64}"
LAYER = "layer:F.Cu"
H_NET = "net:horizontal"
V_NET = "net:vertical"


def _crossing_snapshot() -> object:
    def pad(identifier: str, net_id: str, center: tuple[int, int]) -> Pad:
        return Pad(
            id=identifier,
            net_id=net_id,
            center=PointNM(*center),
            rotation_udeg=0,
            shape=PadShape.RECT,
            kind=PadKind.SMD,
            size_x_nm=400_000,
            size_y_nm=400_000,
            roundrect_radius_nm=None,
            drill_x_nm=None,
            drill_y_nm=None,
            layer_ids=(LAYER,),
        )

    pads = (
        pad("pad:h1", H_NET, (2_000_000, 5_000_000)),
        pad("pad:h2", H_NET, (10_000_000, 5_000_000)),
        pad("pad:v1", V_NET, (6_000_000, 1_000_000)),
        pad("pad:v2", V_NET, (6_000_000, 9_000_000)),
    )
    classes = (
        NetClass(
            id="class:signal",
            name="Signal",
            clearance_nm=100_000,
            track_width_nm=200_000,
            via_diameter_nm=600_000,
            via_drill_nm=300_000,
        ),
    )
    content = make_content(
        source=SourceInfo(
            format="test",
            revision=BOARD_SOURCE,
            format_version="1",
            generator="negotiated-routing-test",
        ),
        outline=(
            OutlineContour(
                id="contour:board",
                outer=Ring(
                    (
                        PointNM(0, 0),
                        PointNM(12_000_000, 0),
                        PointNM(12_000_000, 10_000_000),
                        PointNM(0, 10_000_000),
                    )
                ),
            ),
        ),
        copper_layers=(Layer(id=LAYER, name="F.Cu", index=0, kind="signal"),),
        nets=(Net(id=H_NET, name="HORIZONTAL"), Net(id=V_NET, name="VERTICAL")),
        constraints=ConstraintSet(
            net_classes=classes,
            assignments=(
                NetClassAssignment(net_id=H_NET, net_class_id=classes[0].id),
                NetClassAssignment(net_id=V_NET, net_class_id=classes[0].id),
            ),
        ),
        footprints=(
            Footprint(
                id="footprint:h",
                origin=pads[0].center,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=(pads[0].id, pads[1].id),
            ),
            Footprint(
                id="footprint:v",
                origin=pads[2].center,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=(pads[2].id, pads[3].id),
            ),
        ),
        pads=pads,
    )
    return make_snapshot(content)


def _settings() -> AStarSettings:
    return AStarSettings(
        grid_step_nm=1_000_000,
        bend_penalty_nm=500_000,
        proximity_penalty_nm=0,
        max_grid_nodes=256,
        max_expansions=5_000,
        max_obstacles=64,
        max_obstacle_checks=100_000,
    )


def _requests(snapshot: object) -> tuple[RouteRequest, RouteRequest]:
    assert hasattr(snapshot, "snapshot_digest")
    revision = snapshot.snapshot_digest
    settings = _settings()
    return (
        RouteRequest(
            board_revision=revision,
            net_id=H_NET,
            layer_id=LAYER,
            seed=7,
            settings=settings,
        ),
        RouteRequest(
            board_revision=revision,
            net_id=V_NET,
            layer_id=LAYER,
            seed=11,
            settings=settings,
        ),
    )


def test_negotiated_crossing_replay_removes_baseline_lattice_overflow() -> None:
    snapshot = _crossing_snapshot()
    horizontal, vertical = _requests(snapshot)
    router = AStarRouter()

    baseline_ledger = CongestionLedger(
        grid_step_nm=horizontal.settings.grid_step_nm,
        present_penalty_nm=0,
        history_penalty_nm=0,
    )
    first = router.propose(snapshot, horizontal, congestion_penalty=baseline_ledger.penalty)
    assert first.candidate is not None
    baseline_ledger.add_candidate(first.candidate)
    second = router.propose(snapshot, vertical, congestion_penalty=baseline_ledger.penalty)
    assert second.candidate is not None
    baseline_ledger.add_candidate(second.candidate)
    assert baseline_ledger.overflow_resources()

    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=(vertical, horizontal),
        max_iterations=4,
    )
    result = negotiate_routes(snapshot, envelope)

    assert result.status is NegotiatedRoutingStatus.COMPLETED
    assert result.ok
    assert result.overflow_resources == ()
    assert len(result.candidates) == 2
    assert {candidate.patch.net_id for candidate in result.candidates} == {H_NET, V_NET}
    assert all(
        candidate.policy.startswith("negotiated-congestion-v2-") for candidate in result.candidates
    )
    assert all(
        candidate.router_version == "negotiated-grid-0.2.0" for candidate in result.candidates
    )
    assert result.candidates == tuple(sorted(result.candidates, key=lambda item: item.patch.net_id))
    assert result == negotiate_routes(snapshot, envelope)


def test_negotiated_request_rejects_mixed_lattice_and_duplicate_nets() -> None:
    snapshot = _crossing_snapshot()
    horizontal, _vertical = _requests(snapshot)
    with pytest.raises(ValueError, match="distinct nets"):
        NegotiatedRoutingRequest(
            board_revision=snapshot.snapshot_digest,
            requests=(horizontal, horizontal),
        )
    mixed = RouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=V_NET,
        layer_id=LAYER,
        seed=11,
        settings=AStarSettings(
            grid_step_nm=500_000,
            bend_penalty_nm=500_000,
            proximity_penalty_nm=0,
            max_grid_nodes=256,
            max_expansions=5_000,
            max_obstacles=64,
            max_obstacle_checks=100_000,
        ),
    )
    with pytest.raises(ValueError, match="one layer and one grid step"):
        NegotiatedRoutingRequest(
            board_revision=snapshot.snapshot_digest,
            requests=(horizontal, mixed),
        )


def test_negotiated_router_fails_closed_on_bad_penalty_and_cancellation() -> None:
    snapshot = _crossing_snapshot()
    horizontal, _ = _requests(snapshot)
    bad = AStarRouter().propose(snapshot, horizontal, congestion_penalty=lambda _a, _b: -1)
    assert bad.diagnostic is not None
    assert bad.diagnostic.code is RouteFailureCode.UNSUPPORTED_CONSTRAINT

    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )
    cancelled = negotiate_routes(snapshot, envelope, cancelled=lambda: True)
    assert cancelled.status is NegotiatedRoutingStatus.CANCELLED
    assert cancelled.iterations == 0


def test_negotiated_router_fails_closed_when_cancellation_callback_raises() -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        if checks == 3:
            raise RuntimeError("cancellation transport failed")
        return False

    result = negotiate_routes(snapshot, envelope, cancelled=cancelled)

    assert result.status is NegotiatedRoutingStatus.CANCELLED
    assert result.iterations == 1
    assert result.candidates == ()
    assert result.diagnostic == "negotiated routing was cancelled during a bounded iteration"


def test_negotiated_router_discards_current_iteration_when_later_net_cancels() -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )

    class CancelSecondRouter:
        def __init__(self) -> None:
            self.calls = 0
            self.router = AStarRouter()

        def propose(self, snapshot: object, request: RouteRequest, **kwargs: object) -> object:
            self.calls += 1
            if self.calls == 2:
                return self.router.propose(snapshot, request, cancelled=lambda: True)
            return self.router.propose(snapshot, request, **kwargs)

    result = negotiate_routes(snapshot, envelope, router=CancelSecondRouter())

    assert result.status is NegotiatedRoutingStatus.CANCELLED
    assert result.iterations == 1
    assert result.candidates == ()
    assert result.connections == ()
    assert result.unrouted_nets == (H_NET, V_NET)
    assert result.overflow_resources == ()
    assert result.total_wire_length_nm == 0


def test_negotiated_router_discards_prior_partial_pass_before_next_iteration() -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
        max_iterations=2,
    )

    class FirstCandidateThenNoPath:
        def __init__(self) -> None:
            self.calls = 0
            self.router = AStarRouter()

        def propose(self, snapshot: object, request: RouteRequest, **kwargs: object) -> object:
            self.calls += 1
            if self.calls == 1:
                return self.router.propose(snapshot, request)
            return RouteResult(
                diagnostic=RouteDiagnostic(RouteFailureCode.NO_PATH, "synthetic no-path")
            )

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 4

    router = FirstCandidateThenNoPath()
    result = negotiate_routes(snapshot, envelope, router=router, cancelled=cancelled)

    assert router.calls == 2
    assert result.status is NegotiatedRoutingStatus.CANCELLED
    assert result.iterations == 1
    assert result.candidates == ()
    assert result.connections == ()
    assert result.unrouted_nets == (H_NET, V_NET)
    assert result.overflow_resources == ()
    assert result.total_wire_length_nm == 0


def _physical_clearance_snapshot() -> object:
    def pad(identifier: str, net_id: str, center: tuple[int, int]) -> Pad:
        return Pad(
            id=identifier,
            net_id=net_id,
            center=PointNM(*center),
            rotation_udeg=0,
            shape=PadShape.RECT,
            kind=PadKind.SMD,
            size_x_nm=100_000,
            size_y_nm=100_000,
            roundrect_radius_nm=None,
            drill_x_nm=None,
            drill_y_nm=None,
            layer_ids=(LAYER,),
        )

    pads = (
        pad("pad:h1", H_NET, (1_000_000, 3_000_000)),
        pad("pad:h2", H_NET, (9_000_000, 3_000_000)),
        pad("pad:v1", V_NET, (1_000_000, 3_900_000)),
        pad("pad:v2", V_NET, (9_000_000, 3_900_000)),
    )
    horizontal_class = NetClass(
        id="class:physical-low",
        name="Physical low",
        clearance_nm=400_000,
        track_width_nm=600_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    vertical_class = NetClass(
        id="class:physical-high",
        name="Physical high",
        clearance_nm=500_000,
        track_width_nm=600_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    content = make_content(
        source=SourceInfo(
            format="test",
            revision=BOARD_SOURCE,
            format_version="1",
            generator="physical-clearance-test",
        ),
        outline=(
            OutlineContour(
                id="contour:board",
                outer=Ring(
                    (
                        PointNM(0, 0),
                        PointNM(12_000_000, 0),
                        PointNM(12_000_000, 8_000_000),
                        PointNM(0, 8_000_000),
                    )
                ),
            ),
        ),
        copper_layers=(Layer(id=LAYER, name="F.Cu", index=0, kind="signal"),),
        nets=(Net(id=H_NET, name="HORIZONTAL"), Net(id=V_NET, name="VERTICAL")),
        constraints=ConstraintSet(
            net_classes=(horizontal_class, vertical_class),
            assignments=(
                NetClassAssignment(net_id=H_NET, net_class_id=horizontal_class.id),
                NetClassAssignment(net_id=V_NET, net_class_id=vertical_class.id),
            ),
        ),
        footprints=(
            Footprint(
                id="footprint:h",
                origin=pads[0].center,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=(pads[0].id, pads[1].id),
            ),
            Footprint(
                id="footprint:v",
                origin=pads[2].center,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=(pads[2].id, pads[3].id),
            ),
        ),
        pads=pads,
    )
    return make_snapshot(content)


def _physical_settings() -> AStarSettings:
    return AStarSettings(
        grid_step_nm=100_000,
        bend_penalty_nm=0,
        proximity_penalty_nm=0,
        max_grid_nodes=256,
        max_expansions=100,
        max_obstacles=16,
        max_obstacle_checks=100,
    )


def _physical_candidate(
    snapshot: object,
    net_id: str,
    paths: tuple[RoutePath, ...],
    *,
    pad_count: int = 2,
) -> RouteCandidate:
    assert hasattr(snapshot, "snapshot_digest")
    patch = RoutePatch(net_id=net_id, layer_id=LAYER, width_nm=600_000, paths=paths)
    settings = _physical_settings()
    return RouteCandidate(
        candidate_id=f"sha256:{'0' * 64}",
        base_revision=snapshot.snapshot_digest,
        start_pad_id="pad:h1" if net_id == H_NET else "pad:v1",
        end_pad_id="pad:h2" if net_id == H_NET else "pad:v2",
        patch=patch,
        cost=RouteCost(
            length_nm=patch.length_nm,
            bend_count=patch.bend_count,
            bend_cost_nm=0,
            proximity_steps=0,
            proximity_cost_nm=0,
            via_cost_nm=0,
            total_cost_nm=patch.length_nm,
        ),
        metrics=RouteMetrics(
            hard_internal_violations=0,
            unrouted_connections=0,
            vias=0,
            wire_length_nm=patch.length_nm,
            expanded_states=0,
            peak_frontier_states=1,
            obstacle_checks=0,
        ),
        settings=settings,
        router_version="test",
        policy="test",
        seed=1,
        pad_count=pad_count,
        ordering_policy=COMPONENT_MST_ORDERING if pad_count > 2 else "single-path",
    )


def test_negotiated_acceptance_rejects_zero_overflow_physical_clearance_violation() -> None:
    snapshot = _physical_clearance_snapshot()
    horizontal = _physical_candidate(
        snapshot,
        H_NET,
        (RoutePath((PointNM(1_000_000, 3_000_000), PointNM(9_000_000, 3_000_000))),),
    )
    vertical = _physical_candidate(
        snapshot,
        V_NET,
        (RoutePath((PointNM(1_000_000, 3_900_000), PointNM(9_000_000, 3_900_000))),),
    )
    ledger = CongestionLedger(
        grid_step_nm=100_000,
        present_penalty_nm=0,
        history_penalty_nm=0,
    )
    ledger.add_candidate(horizontal)
    ledger.add_candidate(vertical)
    assert ledger.overflow_resources() == ()

    class ParallelRouter:
        def propose(
            self, _snapshot: object, request: RouteRequest, **_kwargs: object
        ) -> RouteResult:
            return RouteResult(candidate=horizontal if request.net_id == H_NET else vertical)

    requests = (
        RouteRequest(snapshot.snapshot_digest, H_NET, LAYER, 1, _physical_settings()),
        RouteRequest(snapshot.snapshot_digest, V_NET, LAYER, 2, _physical_settings()),
    )
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=requests,
        max_iterations=2,
    )
    result = negotiate_routes(snapshot, envelope, router=ParallelRouter())

    assert result.status is NegotiatedRoutingStatus.NO_PATH
    assert result.candidates == ()
    assert result.unrouted_nets == (H_NET, V_NET)
    assert result.overflow_resources == ()
    assert result.total_physical_checks == 2
    assert result.diagnostic == "negotiated candidates violate pairwise physical clearance"
    assert result == negotiate_routes(snapshot, envelope, router=ParallelRouter())


def test_physical_clearance_accepts_exact_boundary_and_bounds_work() -> None:
    snapshot = _physical_clearance_snapshot()
    horizontal = _physical_candidate(
        snapshot,
        H_NET,
        (
            RoutePath((PointNM(1_000_000, 3_000_000), PointNM(9_000_000, 3_000_000))),
            RoutePath((PointNM(1_000_000, 1_000_000), PointNM(9_000_000, 1_000_000))),
        ),
        pad_count=3,
    )
    exact_boundary = _physical_candidate(
        snapshot,
        V_NET,
        (RoutePath((PointNM(1_000_000, 4_100_000), PointNM(9_000_000, 4_100_000))),),
    )
    accepted = verify_negotiated_physical_clearance(
        snapshot,
        (horizontal, exact_boundary),
        layer_id=LAYER,
        max_pair_checks=2,
    )
    assert accepted.accepted
    assert accepted.pair_checks == 2

    budget = verify_negotiated_physical_clearance(
        snapshot,
        (horizontal, exact_boundary),
        layer_id=LAYER,
        max_pair_checks=1,
    )
    assert budget.failure is PhysicalClearanceFailure.BUDGET_EXHAUSTED
    assert budget.pair_checks == 1

    cancelled = verify_negotiated_physical_clearance(
        snapshot,
        (horizontal, exact_boundary),
        layer_id=LAYER,
        max_pair_checks=2,
        cancelled=lambda: True,
    )
    assert cancelled.failure is PhysicalClearanceFailure.CANCELLED
    assert cancelled.pair_checks == 0
