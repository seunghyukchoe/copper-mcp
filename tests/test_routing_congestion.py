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
    AStarRouter,
    AStarSettings,
    CongestionLedger,
    NegotiatedRoutingRequest,
    NegotiatedRoutingStatus,
    RouteFailureCode,
    RouteRequest,
    negotiate_routes,
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
        candidate.policy.startswith("negotiated-congestion-v1-") for candidate in result.candidates
    )
    assert all(
        candidate.router_version == "negotiated-grid-0.1.0" for candidate in result.candidates
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
