from __future__ import annotations

import hashlib
import sys
from dataclasses import asdict, fields, replace
from types import MappingProxyType

import pytest

import copper_mcp.routing.congestion as congestion_module
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
from copper_mcp.routing import (
    COMPONENT_MST_ORDERING,
    REFERENCE_POLICY_PROFILE,
    AStarRouter,
    AStarSettings,
    CongestionLedger,
    NegotiatedRoutingRequest,
    NegotiatedRoutingResult,
    NegotiatedRoutingStatus,
    PolicyNegotiatedRoutingResult,
    RouteCandidate,
    RouteConnection,
    RouteCost,
    RouteDiagnostic,
    RouteFailureCode,
    RouteMetrics,
    RoutePatch,
    RoutePath,
    RouteRequest,
    RouteResult,
    canonical_candidate_bytes,
    negotiate_routes,
)
from copper_mcp.routing import policy_worker as policy_worker_module
from copper_mcp.routing.congestion import ISOLATED_REFERENCE_POLICY_PROFILE
from copper_mcp.routing.physical_clearance import (
    PhysicalClearanceFailure,
    PhysicalClearanceVerificationResult,
    verify_negotiated_physical_clearance,
)
from copper_mcp.routing.policy import (
    REFERENCE_POLICY_ID,
    CorridorCandidate,
    PolicyBounds,
    RepairWindowCandidate,
    RoutingPolicyDecision,
    RoutingPolicyInput,
    policy_input_digest,
)
from copper_mcp.routing.repair import RepairTransactionSettings
from scripts import exact_local_repair_gate_fixture as predeclared_fixture

BOARD_SOURCE = f"sha256:{'c' * 64}"
LAYER = "layer:F.Cu"
H_NET = "net:horizontal"
V_NET = "net:vertical"
C_NET = "net:connected"


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


def _multipin_shifted_snapshot(
    horizontal_pad_count: int = 3,
    vertical_pad_count: int = 2,
    *,
    connected_horizontal: bool = False,
    horizontal_centres_override: tuple[PointNM, ...] | None = None,
) -> object:
    """Build two disjoint nets whose local one-millimetre lattices have different origins."""

    def pad(identifier: str, net_id: str, center: PointNM) -> Pad:
        return Pad(
            id=identifier,
            net_id=net_id,
            center=center,
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

    if horizontal_centres_override is None:
        horizontal_centres = (
            PointNM(2_000_000, 10_000_000),
            PointNM(6_000_000, 10_000_000),
            PointNM(6_000_000, 13_000_000),
            *(
                PointNM(
                    9_000_000 + (index % 8) * 3_000_000,
                    10_000_000 + (index // 8) * 3_000_000,
                )
                for index in range(max(0, horizontal_pad_count - 3))
            ),
        )
    else:
        assert len(horizontal_centres_override) == horizontal_pad_count
        horizontal_centres = horizontal_centres_override
    vertical_centres = (
        PointNM(25_500_000, 2_500_000),
        PointNM(30_500_000, 2_500_000),
        PointNM(30_500_000, 5_500_000),
        *(
            PointNM(
                25_500_000 + (index % 4) * 2_000_000,
                7_500_000 + (index // 4) * 2_000_000,
            )
            for index in range(max(0, vertical_pad_count - 3))
        ),
    )
    horizontal_pads = tuple(
        pad(f"pad:h{index:02d}", H_NET, center)
        for index, center in enumerate(horizontal_centres[:horizontal_pad_count])
    )
    vertical_pads = tuple(
        pad(f"pad:v{index:02d}", V_NET, center)
        for index, center in enumerate(vertical_centres[:vertical_pad_count])
    )
    pads = horizontal_pads + vertical_pads
    net_class = NetClass(
        id="class:signal",
        name="Signal",
        clearance_nm=100_000,
        track_width_nm=200_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    segments: tuple[Segment, ...] = ()
    if connected_horizontal:
        assert horizontal_pad_count == 3
        segments = (
            Segment(
                id="segment:h01",
                net_id=H_NET,
                layer_id=LAYER,
                start=horizontal_pads[0].center,
                end=horizontal_pads[1].center,
                width_nm=net_class.track_width_nm,
            ),
            Segment(
                id="segment:h12",
                net_id=H_NET,
                layer_id=LAYER,
                start=horizontal_pads[1].center,
                end=horizontal_pads[2].center,
                width_nm=net_class.track_width_nm,
            ),
        )
    content = make_content(
        source=SourceInfo(
            format="test",
            revision=BOARD_SOURCE,
            format_version="1",
            generator="negotiated-multipin-test",
        ),
        outline=(
            OutlineContour(
                id="contour:board",
                outer=Ring(
                    (
                        PointNM(0, 0),
                        PointNM(40_000_000, 0),
                        PointNM(40_000_000, 25_000_000),
                        PointNM(0, 25_000_000),
                    )
                ),
            ),
        ),
        copper_layers=(Layer(id=LAYER, name="F.Cu", index=0, kind="signal"),),
        nets=(Net(id=H_NET, name="HORIZONTAL"), Net(id=V_NET, name="VERTICAL")),
        constraints=ConstraintSet(
            net_classes=(net_class,),
            assignments=(
                NetClassAssignment(net_id=H_NET, net_class_id=net_class.id),
                NetClassAssignment(net_id=V_NET, net_class_id=net_class.id),
            ),
        ),
        footprints=(
            Footprint(
                id="footprint:h",
                origin=horizontal_pads[0].center,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=tuple(item.id for item in horizontal_pads),
            ),
            Footprint(
                id="footprint:v",
                origin=vertical_pads[0].center,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=tuple(item.id for item in vertical_pads),
            ),
        ),
        pads=pads,
        segments=segments,
    )
    return make_snapshot(content)


def _multipin_settings() -> AStarSettings:
    return AStarSettings(
        grid_step_nm=1_000_000,
        bend_penalty_nm=500_000,
        proximity_penalty_nm=0,
        max_grid_nodes=4_096,
        max_expansions=100_000,
        max_obstacles=128,
        max_net_objects=1_024,
        max_obstacle_checks=500_000,
    )


def _multipin_requests(snapshot: object) -> tuple[RouteRequest, RouteRequest]:
    assert hasattr(snapshot, "snapshot_digest")
    return (
        RouteRequest(snapshot.snapshot_digest, H_NET, LAYER, 7, _multipin_settings()),
        RouteRequest(snapshot.snapshot_digest, V_NET, LAYER, 11, _multipin_settings()),
    )


def test_predeclared_repair_gate_helper_is_semantically_equivalent_to_original_builder() -> None:
    original = _crossing_snapshot()
    fixture = predeclared_fixture.build_snapshot()

    assert original.snapshot_digest == predeclared_fixture.EXPECTED_SNAPSHOT_DIGEST
    assert fixture.snapshot_digest == original.snapshot_digest
    assert fixture.content == original.content
    assert predeclared_fixture.settings() == _settings()
    assert predeclared_fixture.build_requests(fixture) == _requests(original)


def _resign(candidate: RouteCandidate) -> RouteCandidate:
    """Return test-only candidate metadata with an honest canonical identity."""

    return replace(
        candidate,
        candidate_id=f"sha256:{hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()}",
    )


def _assert_unbound_router_result(result: object) -> None:
    assert hasattr(result, "status")
    assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert not result.ok
    assert result.candidates == ()
    assert result.connections == ()
    assert result.unrouted_nets == (H_NET, V_NET)
    assert result.overflow_resources == ()
    assert result.total_wire_length_nm == 0
    assert result.diagnostic == "the negotiated router result failed identity validation"


class _OrderedPolicy:
    policy_id = "negotiated-policy-test-v1"

    def __init__(self, net_order: tuple[str, ...]) -> None:
        self.net_order = net_order
        self.calls = 0
        self.inputs: list[RoutingPolicyInput] = []

    def propose(self, policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
        self.calls += 1
        self.inputs.append(policy_input)
        return RoutingPolicyDecision(
            policy_id=self.policy_id,
            input_digest=policy_input_digest(policy_input),
            net_order=self.net_order,
        )


class _RecordingRouter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._reference = AStarRouter()

    def propose(self, snapshot: object, request: RouteRequest, **kwargs: object) -> RouteResult:
        self.calls.append(request.net_id)
        return self._reference.propose(snapshot, request, **kwargs)


def _install_policy_profile(
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    factory: object,
) -> None:
    monkeypatch.setattr(
        congestion_module,
        "_POLICY_PROFILE_REGISTRY",
        MappingProxyType({profile: factory}),
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


def test_legacy_two_pin_policy_and_candidate_identities_remain_exact() -> None:
    snapshot = _crossing_snapshot()
    horizontal, vertical = _requests(snapshot)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=(vertical, horizontal),
        max_iterations=4,
    )

    result = negotiate_routes(snapshot, envelope)

    assert snapshot.snapshot_digest == (
        "sha256:9ad048f6f439a7e71be4c1f115d8a205f00c92f0853e0c140725906c1acdb245"
    )
    assert envelope.policy_digest == (
        "sha256:0581cf0a36595f9a4bc3877ef69e21b106e19e2450a25b8f99bd50311924baeb"
    )
    assert result.status is NegotiatedRoutingStatus.COMPLETED
    assert {item.patch.net_id: item.candidate_id for item in result.candidates} == {
        H_NET: "sha256:9a6af627be11bd6c9938f4f0c9b3e00918ff63323af6ecaf088ffe9775b8142e",
        V_NET: "sha256:04d23efc8616af3e06f97966d27f35660ab10c176d0512734495422c6959b2ff",
    }
    assert all(
        candidate.candidate_id
        == f"sha256:{hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()}"
        for candidate in result.candidates
    )


@pytest.mark.parametrize("pad_count", (2, 32))
def test_negotiated_pad_census_routes_both_boundary_counts(pad_count: int) -> None:
    snapshot = _multipin_shifted_snapshot(pad_count)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_multipin_requests(snapshot),
        max_iterations=1,
    )

    assert congestion_module._validate_snapshot_requests(snapshot, envelope) is None
    result = negotiate_routes(snapshot, envelope)
    assert result.status is NegotiatedRoutingStatus.COMPLETED
    assert {item.patch.net_id: item.pad_count for item in result.candidates} == {
        H_NET: pad_count,
        V_NET: 2,
    }


@pytest.mark.parametrize("pad_count", (1, 33))
def test_negotiated_pad_census_refuses_outside_bounds_before_router(
    pad_count: int,
) -> None:
    snapshot = _multipin_shifted_snapshot(pad_count)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_multipin_requests(snapshot),
    )

    class RouterSpy:
        def __init__(self) -> None:
            self.calls = 0

        def propose(self, *_args: object, **_kwargs: object) -> RouteResult:
            self.calls += 1
            return RouteResult(diagnostic=RouteDiagnostic(RouteFailureCode.NO_PATH, "must not run"))

    router = RouterSpy()
    result = negotiate_routes(snapshot, envelope, router=router)

    assert router.calls == 0
    assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert result.diagnostic == "each negotiated net must expose 2 to 32 pads on the selected layer"


def test_negotiated_multipin_routes_complete_on_request_local_shifted_lattices() -> None:
    snapshot = _multipin_shifted_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_multipin_requests(snapshot),
        max_iterations=2,
    )

    result = negotiate_routes(snapshot, envelope)
    replay = negotiate_routes(snapshot, envelope)

    assert result.status is NegotiatedRoutingStatus.COMPLETED
    assert result == replay
    assert result.connections == ()
    candidates = {item.patch.net_id: item for item in result.candidates}
    assert candidates[H_NET].pad_count == 3
    assert candidates[H_NET].start_pad_id == "pad:h00"
    assert candidates[H_NET].end_pad_id == "pad:h02"
    assert candidates[H_NET].ordering_policy != "single-path"
    assert candidates[V_NET].pad_count == 2
    assert (25_500_000 - 2_000_000) % envelope.grid_step_nm != 0


def test_negotiated_multipin_connected_evidence_binds_all_selected_layer_pads() -> None:
    snapshot = _multipin_shifted_snapshot(connected_horizontal=True)
    horizontal, vertical = _multipin_requests(snapshot)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=(horizontal, vertical),
        max_iterations=1,
    )

    direct = AStarRouter().propose(snapshot, horizontal)
    assert direct.connected is not None
    assert congestion_module._connection_is_bound(direct.connected, snapshot, horizontal)
    result = negotiate_routes(snapshot, envelope)

    assert result.status is NegotiatedRoutingStatus.COMPLETED
    assert len(result.candidates) == 1
    assert result.candidates[0].patch.net_id == V_NET
    assert result.connections == (direct.connected,)
    assert result.connections[0].start_pad_id == "pad:h00"
    assert result.connections[0].end_pad_id == "pad:h02"
    assert result.connections[0].pad_count == 3
    assert result.connections[0].attachment_segments == 2
    assert result.connections[0].component_objects == 5


def test_negotiated_multipin_custom_router_cannot_forge_set_binding_or_order() -> None:
    snapshot = _multipin_shifted_snapshot()
    horizontal, vertical = _multipin_requests(snapshot)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=(horizontal, vertical),
        max_iterations=1,
    )
    reference = AStarRouter().propose(snapshot, horizontal)
    assert reference.candidate is not None
    candidate = reference.candidate
    assert candidate.ordering_policy != COMPONENT_MST_ORDERING
    forged_fill_binding = _resign(replace(candidate, fill_binding=f"sha256:{'f' * 64}"))
    forged = (
        _resign(replace(candidate, end_pad_id="pad:h01")),
        _resign(replace(candidate, pad_count=4)),
        _resign(replace(candidate, ordering_policy=COMPONENT_MST_ORDERING)),
        forged_fill_binding,
    )
    assert forged_fill_binding.candidate_id == (
        f"sha256:{hashlib.sha256(canonical_candidate_bytes(forged_fill_binding)).hexdigest()}"
    )
    assert not congestion_module._candidate_is_bound(forged_fill_binding, snapshot, horizontal)
    assert not congestion_module._results_are_semantically_equal(
        RouteResult(candidate=forged_fill_binding),
        reference,
    )

    class ForgedMultipinRouter:
        def __init__(self, value: RouteCandidate) -> None:
            self.value = value
            self.reference = AStarRouter()

        def propose(
            self, router_snapshot: object, request: RouteRequest, **kwargs: object
        ) -> RouteResult:
            if request.net_id == H_NET:
                return RouteResult(candidate=self.value)
            return self.reference.propose(router_snapshot, request, **kwargs)

    for value in forged:
        _assert_unbound_router_result(
            negotiate_routes(snapshot, envelope, router=ForgedMultipinRouter(value))
        )


def test_negotiated_multipin_honours_coordinator_budget_and_cancellation() -> None:
    snapshot = _multipin_shifted_snapshot()
    requests = _multipin_requests(snapshot)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=requests,
        max_iterations=1,
        max_total_expansions=1,
    )

    budgeted = negotiate_routes(snapshot, envelope)
    cancelled = negotiate_routes(
        snapshot,
        replace(envelope, max_total_expansions=100_000),
        cancelled=lambda: True,
    )

    assert budgeted.status is NegotiatedRoutingStatus.NO_PATH
    assert budgeted.candidates == ()
    assert budgeted.connections == ()
    assert budgeted.unrouted_nets == (H_NET, V_NET)
    assert cancelled.status is NegotiatedRoutingStatus.CANCELLED
    assert cancelled.iterations == 0
    assert cancelled.candidates == ()


def test_multipin_bounding_box_demand_is_deterministic_and_preserves_two_pin_values() -> None:
    # The first and last pad IDs are intentionally close.  Only the middle ID exposes the true
    # envelope (both minimum x and maximum y), and its 16.9-cell span requires ceiling division.
    points_by_pad_id = (
        PointNM(10_000_000, 10_000_000),
        PointNM(2_200_000, 18_600_000),
        PointNM(10_500_000, 10_250_000),
    )
    assert congestion_module._pad_demand_cells(points_by_pad_id, 1_000_000) == 17
    assert congestion_module._pad_demand_cells(tuple(reversed(points_by_pad_id)), 1_000_000) == 17
    assert (
        congestion_module._pad_demand_cells(
            (PointNM(2_000_000, 5_000_000), PointNM(10_000_000, 5_000_000)),
            1_000_000,
        )
        == 8
    )

    snapshot = _multipin_shifted_snapshot(
        horizontal_centres_override=points_by_pad_id,
    )
    horizontal, vertical = _multipin_requests(snapshot)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=(horizontal, vertical),
    )
    pads = congestion_module._request_pads(snapshot, horizontal)
    assert pads is not None
    assert tuple(item.id for item in pads) == ("pad:h00", "pad:h01", "pad:h02")
    assert (
        abs(pads[0].center.x - pads[-1].center.x) + abs(pads[0].center.y - pads[-1].center.y)
        == 750_000
    )
    assert pads[1].center.x == min(item.center.x for item in pads)
    assert pads[1].center.y == max(item.center.y for item in pads)
    raw_span_nm = (
        max(item.center.x for item in pads)
        - min(item.center.x for item in pads)
        + max(item.center.y for item in pads)
        - min(item.center.y for item in pads)
    )
    assert raw_span_nm == 16_900_000
    assert raw_span_nm % envelope.grid_step_nm != 0
    assert congestion_module._net_demand_cells(snapshot, envelope) == {H_NET: 17, V_NET: 5}
    policy_input = congestion_module._derive_policy_input(snapshot, envelope)
    assert [item.demand_cells for item in policy_input.nets] == [17, 5]


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


def test_negotiated_router_rejects_unbound_candidate_identities_before_accounting() -> None:
    """A generic router cannot relabel, replay, or overspend a clipped request."""

    snapshot = _crossing_snapshot()
    horizontal, vertical = _requests(snapshot)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=(horizontal, vertical),
    )
    reference = AStarRouter().propose(snapshot, horizontal)
    assert reference.candidate is not None
    candidate = reference.candidate
    expanded_settings = replace(
        candidate.settings,
        max_expansions=candidate.settings.max_expansions + 1,
    )
    invalid_candidates = (
        _resign(replace(candidate, patch=replace(candidate.patch, net_id=V_NET))),
        _resign(replace(candidate, base_revision=f"sha256:{'a' * 64}")),
        _resign(replace(candidate, patch=replace(candidate.patch, layer_id="layer:B.Cu"))),
        _resign(replace(candidate, start_pad_id="pad:v1", end_pad_id="pad:v2")),
        _resign(replace(candidate, start_pad_id="pad:h2", end_pad_id="pad:h1")),
        _resign(replace(candidate, seed=candidate.seed + 1)),
        _resign(
            replace(
                candidate,
                settings=replace(
                    candidate.settings,
                    max_grid_nodes=candidate.settings.max_grid_nodes + 1,
                ),
            )
        ),
        replace(candidate, candidate_id=f"sha256:{'0' * 64}"),
        _resign(
            replace(
                candidate,
                pad_count=3,
                ordering_policy=COMPONENT_MST_ORDERING,
            )
        ),
        _resign(
            replace(
                candidate,
                settings=expanded_settings,
                metrics=replace(
                    candidate.metrics,
                    expanded_states=expanded_settings.max_expansions,
                ),
            )
        ),
    )

    class FirstUnboundCandidateRouter:
        def __init__(self, unbound: RouteCandidate) -> None:
            self.unbound = unbound
            self.reference = AStarRouter()

        def propose(
            self, router_snapshot: object, request: RouteRequest, **kwargs: object
        ) -> RouteResult:
            if request.net_id == H_NET:
                return RouteResult(candidate=self.unbound)
            return self.reference.propose(router_snapshot, request, **kwargs)

    for invalid in invalid_candidates:
        _assert_unbound_router_result(
            negotiate_routes(snapshot, envelope, router=FirstUnboundCandidateRouter(invalid))
        )


def test_negotiated_router_rejects_self_hashed_illegal_geometry_from_custom_router() -> None:
    snapshot = _crossing_snapshot()
    horizontal, vertical = _requests(snapshot)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=(horizontal, vertical),
    )
    reference = AStarRouter().propose(snapshot, horizontal)
    assert reference.candidate is not None
    candidate = reference.candidate

    def self_hashed_path(vertices: tuple[PointNM, ...]) -> RouteCandidate:
        path = RoutePath(vertices)
        patch = replace(candidate.patch, paths=(path,))
        cost = RouteCost(
            length_nm=patch.length_nm,
            bend_count=path.bend_count,
            bend_cost_nm=path.bend_count * candidate.settings.bend_penalty_nm,
            proximity_steps=0,
            proximity_cost_nm=0,
            via_cost_nm=0,
            total_cost_nm=patch.length_nm + path.bend_count * candidate.settings.bend_penalty_nm,
        )
        return _resign(
            replace(
                candidate,
                patch=patch,
                cost=cost,
                metrics=replace(
                    candidate.metrics,
                    wire_length_nm=patch.length_nm,
                    expanded_states=1,
                    peak_frontier_states=1,
                    obstacle_checks=0,
                ),
            )
        )

    # The first path crosses V's selected-layer pad; the second does not start on H's pad.
    # Both have authentic self-hashes and request metadata, so only independent core replay can
    # distinguish them from a legal proposal.
    invalid_candidates = (
        self_hashed_path(
            (
                PointNM(2_000_000, 5_000_000),
                PointNM(6_000_000, 5_000_000),
                PointNM(6_000_000, 1_000_000),
                PointNM(10_000_000, 1_000_000),
                PointNM(10_000_000, 5_000_000),
            )
        ),
        self_hashed_path(
            (
                PointNM(2_000_000, 4_000_000),
                PointNM(10_000_000, 4_000_000),
                PointNM(10_000_000, 5_000_000),
            )
        ),
    )

    class SelfHashedGeometryRouter:
        def __init__(self, invalid: RouteCandidate) -> None:
            self.invalid = invalid
            self.reference = AStarRouter()

        def propose(
            self, router_snapshot: object, request: RouteRequest, **kwargs: object
        ) -> RouteResult:
            if request.net_id == H_NET:
                return RouteResult(candidate=self.invalid)
            return self.reference.propose(router_snapshot, request, **kwargs)

    for invalid in invalid_candidates:
        _assert_unbound_router_result(
            negotiate_routes(snapshot, envelope, router=SelfHashedGeometryRouter(invalid))
        )


def test_negotiated_router_accepts_reference_equivalent_custom_router_after_replay() -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )

    class ReferenceEquivalentRouter:
        def __init__(self) -> None:
            self.reference = AStarRouter()

        def propose(
            self, router_snapshot: object, request: RouteRequest, **kwargs: object
        ) -> RouteResult:
            return self.reference.propose(router_snapshot, request, **kwargs)

    result = negotiate_routes(
        snapshot,
        envelope,
        router=ReferenceEquivalentRouter(),
        policy_profile=REFERENCE_POLICY_PROFILE,
    )

    assert result.status is NegotiatedRoutingStatus.COMPLETED
    assert result.ok
    assert len(result.candidates) == 2
    assert isinstance(result, PolicyNegotiatedRoutingResult)
    assert result.policy_evidence.policy_id == REFERENCE_POLICY_ID
    assert result.policy_evidence.policy_profile == REFERENCE_POLICY_PROFILE


def test_policy_enabled_physical_failure_retains_only_redacted_policy_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
        max_iterations=1,
    )
    monkeypatch.setattr(
        congestion_module,
        "verify_negotiated_physical_clearance",
        lambda *_args, **_kwargs: PhysicalClearanceVerificationResult(
            pair_checks=1,
            failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION,
        ),
    )

    result = negotiate_routes(snapshot, envelope, policy_profile=REFERENCE_POLICY_PROFILE)

    assert isinstance(result, PolicyNegotiatedRoutingResult)
    assert result.status is NegotiatedRoutingStatus.NO_PATH
    assert result.candidates == ()
    assert result.connections == ()
    assert result.policy_evidence.policy_id == REFERENCE_POLICY_ID
    assert result.policy_evidence.policy_profile == REFERENCE_POLICY_PROFILE


def test_negotiated_router_rejects_unbound_connections_and_router_failures_atomically() -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )
    wrong_pads = RouteConnection(
        base_revision=snapshot.snapshot_digest,
        start_pad_id="pad:v1",
        end_pad_id="pad:v2",
        attachment_segments=0,
        component_objects=2,
    )
    oversized_work = RouteConnection(
        base_revision=snapshot.snapshot_digest,
        start_pad_id="pad:h1",
        end_pad_id="pad:h2",
        attachment_segments=0,
        component_objects=2,
        obstacle_checks=100_001,
    )

    class FirstUnboundConnectionRouter:
        def __init__(self, unbound: RouteConnection) -> None:
            self.unbound = unbound

        def propose(
            self, _snapshot: object, _request: RouteRequest, **_kwargs: object
        ) -> RouteResult:
            return RouteResult(connected=self.unbound)

    for unbound in (wrong_pads, oversized_work):
        _assert_unbound_router_result(
            negotiate_routes(snapshot, envelope, router=FirstUnboundConnectionRouter(unbound))
        )

    class SecondRouterFailure:
        def __init__(self) -> None:
            self.calls = 0
            self.reference = AStarRouter()

        def propose(
            self, router_snapshot: object, request: RouteRequest, **kwargs: object
        ) -> RouteResult:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("untrusted router transport failed")
            return self.reference.propose(router_snapshot, request, **kwargs)

    _assert_unbound_router_result(
        negotiate_routes(snapshot, envelope, router=SecondRouterFailure())
    )

    class MalformedRouter:
        def propose(self, _snapshot: object, _request: RouteRequest, **_kwargs: object) -> object:
            return object()

    _assert_unbound_router_result(negotiate_routes(snapshot, envelope, router=MalformedRouter()))


def test_negotiated_router_discards_prior_partial_pass_when_replay_is_cancelled() -> None:
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

    # The candidate-producing call is never published when the independent reference replay is
    # cancelled before the next custom-router invocation.
    assert router.calls == 1
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
        pad("pad:c1", C_NET, (1_000_000, 6_000_000)),
        pad("pad:c2", C_NET, (9_000_000, 6_000_000)),
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
        nets=(
            Net(id=H_NET, name="HORIZONTAL"),
            Net(id=V_NET, name="VERTICAL"),
            Net(id=C_NET, name="CONNECTED"),
        ),
        constraints=ConstraintSet(
            net_classes=(horizontal_class, vertical_class),
            assignments=(
                NetClassAssignment(net_id=H_NET, net_class_id=horizontal_class.id),
                NetClassAssignment(net_id=V_NET, net_class_id=vertical_class.id),
                NetClassAssignment(net_id=C_NET, net_class_id=horizontal_class.id),
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
            Footprint(
                id="footprint:c",
                origin=pads[4].center,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=(pads[4].id, pads[5].id),
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
    candidate = RouteCandidate(
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
        seed={H_NET: 1, V_NET: 2, C_NET: 3}[net_id],
        pad_count=pad_count,
        ordering_policy=COMPONENT_MST_ORDERING if pad_count > 2 else "single-path",
    )
    return replace(
        candidate,
        candidate_id=f"sha256:{hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()}",
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

    result = verify_negotiated_physical_clearance(
        snapshot,
        (horizontal, vertical),
        layer_id=LAYER,
        max_pair_checks=2,
    )

    assert result.failure is PhysicalClearanceFailure.CLEARANCE_VIOLATION
    assert result.pair_checks == 1
    assert result.diagnostic == "negotiated candidates violate pairwise physical clearance"


def test_shifted_lattice_crossing_has_no_exact_resource_collision_but_fails_physical_gate() -> None:
    snapshot = _physical_clearance_snapshot()
    horizontal = _physical_candidate(
        snapshot,
        H_NET,
        (RoutePath((PointNM(1_000_000, 3_000_000), PointNM(9_000_000, 3_000_000))),),
    )
    shifted_vertical = _physical_candidate(
        snapshot,
        V_NET,
        (RoutePath((PointNM(1_050_000, 1_000_000), PointNM(1_050_000, 5_000_000))),),
    )
    assert congestion_module._candidate_resources(horizontal, 100_000).isdisjoint(
        congestion_module._candidate_resources(shifted_vertical, 100_000)
    )

    ledger = CongestionLedger(
        grid_step_nm=100_000,
        present_penalty_nm=0,
        history_penalty_nm=0,
    )
    ledger.add_candidate(horizontal)
    ledger.add_candidate(shifted_vertical)
    assert ledger.overflow_resources() == ()

    physical = verify_negotiated_physical_clearance(
        snapshot,
        (horizontal, shifted_vertical),
        layer_id=LAYER,
        max_pair_checks=1,
    )
    assert physical.failure is PhysicalClearanceFailure.CLEARANCE_VIOLATION
    assert physical.pair_checks == 1


def test_two_pin_local_repair_runs_against_shifted_phase_multipin_conflict() -> None:
    snapshot = _multipin_shifted_snapshot(
        horizontal_centres_override=(
            PointNM(27_000_000, 1_000_000),
            PointNM(27_000_000, 6_000_000),
            PointNM(34_000_000, 6_000_000),
        ),
    )
    horizontal, vertical = _multipin_requests(snapshot)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=(horizontal, vertical),
        max_iterations=1,
    )
    baseline = tuple(
        result.candidate
        for request in envelope.requests
        if (result := AStarRouter().propose(snapshot, request)).candidate is not None
    )

    assert tuple(candidate.pad_count for candidate in baseline) == (3, 2)
    target_origin = baseline[1].patch.paths[0].vertices[0]
    assert any(
        (point.x - target_origin.x) % envelope.grid_step_nm
        or (point.y - target_origin.y) % envelope.grid_step_nm
        for path in baseline[0].patch.paths
        for point in path.vertices
    )
    physical = verify_negotiated_physical_clearance(
        snapshot,
        baseline,
        layer_id=LAYER,
        max_pair_checks=1,
    )
    assert physical.failure is PhysicalClearanceFailure.CLEARANCE_VIOLATION
    assert physical.violating_nets == (H_NET, V_NET)

    result = negotiate_routes(
        snapshot,
        envelope,
        repair_settings=RepairTransactionSettings(),
    )

    assert isinstance(result, congestion_module.RepairNegotiatedRoutingResult)
    assert result.status is NegotiatedRoutingStatus.COMPLETED
    assert len(result.candidates) == 2
    assert result.repair_evidence is not None
    assert result.repair_evidence.projection_obstacle_checks > 0
    assert result.repair_evidence.local_expanded_states > 0
    assert result.repair_evidence.validator_edge_checks > 0
    assert result.repair_evidence.validator_obstacle_checks > 0


def test_local_repair_skips_every_non_two_pin_target(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = _multipin_shifted_snapshot(vertical_pad_count=3)
    horizontal, vertical = _multipin_requests(snapshot)
    candidates: list[RouteCandidate] = []
    for request in (horizontal, vertical):
        result = AStarRouter().propose(snapshot, request)
        assert result.candidate is not None
        assert result.candidate.pad_count == 3
        candidates.append(result.candidate)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=(horizontal, vertical),
        max_iterations=1,
    )

    def forbidden_provenance(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("multi-pin candidates must not enter the two-pin repair derivation")

    monkeypatch.setattr(congestion_module, "derive_repair_provenance", forbidden_provenance)
    repair = congestion_module._attempt_local_repair(
        snapshot,
        envelope,
        tuple(candidates),
        iteration=1,
        violating_nets=(H_NET, V_NET),
        settings=RepairTransactionSettings(),
        policy_profile=None,
        cancelled=None,
        total_expansions=0,
        total_obstacle_checks=0,
    )

    assert repair == (None, None, 0, 0, False)


def test_replay_failure_discards_prior_candidate_and_connection_evidence_atomically() -> None:
    snapshot = _physical_clearance_snapshot()
    connection = RouteConnection(
        base_revision=snapshot.snapshot_digest,
        start_pad_id="pad:c1",
        end_pad_id="pad:c2",
        attachment_segments=0,
        component_objects=2,
    )

    class CandidateThenUnboundConnectionRouter:
        def __init__(self) -> None:
            self.reference = AStarRouter()

        def propose(
            self, router_snapshot: object, request: RouteRequest, **kwargs: object
        ) -> RouteResult:
            if request.net_id == C_NET:
                return RouteResult(connected=connection)
            return self.reference.propose(router_snapshot, request, **kwargs)

    requests = (
        RouteRequest(snapshot.snapshot_digest, H_NET, LAYER, 1, _physical_settings()),
        RouteRequest(snapshot.snapshot_digest, V_NET, LAYER, 2, _physical_settings()),
        RouteRequest(snapshot.snapshot_digest, C_NET, LAYER, 3, _physical_settings()),
    )
    result = negotiate_routes(
        snapshot,
        NegotiatedRoutingRequest(
            board_revision=snapshot.snapshot_digest,
            requests=requests,
            max_iterations=1,
        ),
        router=CandidateThenUnboundConnectionRouter(),
    )

    assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert result.candidates == ()
    assert result.connections == ()
    assert result.overflow_resources == ()
    assert result.total_wire_length_nm == 0
    assert result.unrouted_nets == (C_NET, H_NET, V_NET)


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


def test_negotiated_policy_only_changes_first_pass_order_and_is_evaluated_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
        max_iterations=2,
    )
    policy = _OrderedPolicy((V_NET, H_NET))
    _install_policy_profile(monkeypatch, "test-reverse", lambda: policy)
    router = _RecordingRouter()

    result = negotiate_routes(snapshot, envelope, router=router, policy_profile="test-reverse")

    assert policy.calls == 1
    assert isinstance(result, PolicyNegotiatedRoutingResult)
    assert router.calls[:2] == [V_NET, H_NET]
    # The crossing has equal exact congestion scores.  The coordinator's retry tie-break is
    # independent of the policy's first-pass permutation.
    assert router.calls[2:] == [H_NET, V_NET]
    assert result.policy_digest == envelope.policy_digest
    assert result.policy_evidence is not None
    assert result.policy_evidence.policy_id == policy.policy_id
    assert result.policy_evidence.policy_profile == "test-reverse"
    assert result.policy_evidence.input_digest == policy_input_digest(policy.inputs[0])
    assert policy.inputs[0].bounds == PolicyBounds(0, 0, 0, 0)
    assert policy.inputs[0].corridor_candidates == ()
    assert policy.inputs[0].repair_candidates == ()
    assert [(net.criticality, net.congestion_score) for net in policy.inputs[0].nets] == [
        (0, 0),
        (0, 0),
    ]
    assert [net.demand_cells for net in policy.inputs[0].nets] == [8, 8]
    expected_label = (
        "negotiated-congestion-policy-order-v3-"
        f"{result.policy_evidence.composite_digest.removeprefix('sha256:')}"
    )
    assert all(candidate.policy == expected_label for candidate in result.candidates)


def test_negotiated_policy_binding_is_deterministic_and_preserves_no_policy_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
        max_iterations=1,
    )
    _install_policy_profile(
        monkeypatch,
        "test-reverse",
        lambda: _OrderedPolicy((V_NET, H_NET)),
    )
    first = negotiate_routes(snapshot, envelope, policy_profile="test-reverse")
    second = negotiate_routes(snapshot, envelope, policy_profile="test-reverse")
    _install_policy_profile(
        monkeypatch,
        "test-forward",
        lambda: _OrderedPolicy((H_NET, V_NET)),
    )
    forward = negotiate_routes(snapshot, envelope, policy_profile="test-forward")
    legacy = negotiate_routes(snapshot, envelope)
    legacy_replays = tuple(negotiate_routes(snapshot, envelope) for _ in range(10))

    assert isinstance(first, PolicyNegotiatedRoutingResult)
    assert isinstance(second, PolicyNegotiatedRoutingResult)
    assert isinstance(forward, PolicyNegotiatedRoutingResult)
    assert first.policy_evidence == second.policy_evidence
    assert first.policy_evidence.composite_digest != forward.policy_evidence.composite_digest
    assert tuple(candidate.candidate_id for candidate in first.candidates) == tuple(
        candidate.candidate_id for candidate in second.candidates
    )
    assert tuple(candidate.candidate_id for candidate in first.candidates) != tuple(
        candidate.candidate_id for candidate in forward.candidates
    )
    assert type(legacy) is NegotiatedRoutingResult
    assert not hasattr(legacy, "policy_evidence")
    assert all(replay == legacy for replay in legacy_replays)
    assert all(
        candidate.policy
        == f"negotiated-congestion-v2-{envelope.policy_digest.removeprefix('sha256:')[:16]}"
        for candidate in legacy.candidates
    )


def test_policy_binding_digest_has_a_versioned_candidate_identity_discriminator() -> None:
    assert (
        congestion_module._policy_binding_digest(
            f"sha256:{'a' * 64}",
            f"sha256:{'b' * 64}",
        )
        == "sha256:7c28a8b5159ec949b8fd8885f48df981c8cff63256f95d79fe54b392a06df882"
    )


def test_policy_id_is_not_an_admitted_profile_selector() -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )
    router = _RecordingRouter()

    result = negotiate_routes(
        snapshot,
        envelope,
        router=router,
        policy_profile=REFERENCE_POLICY_ID,
    )

    assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert result.diagnostic == "the negotiated routing policy was rejected"
    assert router.calls == []


def test_no_policy_result_preserves_legacy_dataclass_wire_and_repr_shape() -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
        max_iterations=1,
    )

    result = negotiate_routes(snapshot, envelope)

    assert type(result) is NegotiatedRoutingResult
    assert tuple(field.name for field in fields(result)) == (
        "status",
        "board_revision",
        "candidates",
        "connections",
        "unrouted_nets",
        "iterations",
        "ripups",
        "overflow_resources",
        "overflow_units",
        "total_wire_length_nm",
        "total_physical_checks",
        "diagnostic",
        "policy_digest",
    )
    assert tuple(asdict(result)) == tuple(field.name for field in fields(result))
    assert repr(result).startswith("NegotiatedRoutingResult(")


def test_negotiated_policy_failures_are_redacted_and_prevent_router_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )

    class ThrowingPolicy:
        policy_id = "throwing-policy-v1"

        def propose(self, _policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
            raise RuntimeError("do not disclose this policy failure")

    class ForeignNetPolicy:
        policy_id = "foreign-policy-v1"

        def propose(self, policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
            return RoutingPolicyDecision(
                policy_id=self.policy_id,
                input_digest=policy_input_digest(policy_input),
                net_order=(H_NET, "net:foreign"),
            )

    class WindowPolicy:
        policy_id = "window-policy-v1"

        def propose(self, policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
            return RoutingPolicyDecision(
                policy_id=self.policy_id,
                input_digest=policy_input_digest(policy_input),
                net_order=(H_NET, V_NET),
                corridor_hints=(CorridorCandidate(H_NET, PolicyBounds(0, 0, 0, 0), 0, 0),),
            )

    class RepairWindowPolicy:
        policy_id = "repair-window-policy-v1"

        def propose(self, policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
            return RoutingPolicyDecision(
                policy_id=self.policy_id,
                input_digest=policy_input_digest(policy_input),
                net_order=(H_NET, V_NET),
                repair_windows=(RepairWindowCandidate(H_NET, PolicyBounds(0, 0, 0, 0), 0),),
            )

    class WrongIdPolicy:
        policy_id = "wrong-id-policy-v1"

        def propose(self, policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
            return RoutingPolicyDecision(
                policy_id="different-policy-v1",
                input_digest=policy_input_digest(policy_input),
                net_order=(H_NET, V_NET),
            )

    class WrongDigestPolicy:
        policy_id = "wrong-digest-policy-v1"

        def propose(self, _policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
            return RoutingPolicyDecision(
                policy_id=self.policy_id,
                input_digest=f"sha256:{'0' * 64}",
                net_order=(H_NET, V_NET),
            )

    class MissingNetPolicy:
        policy_id = "missing-net-policy-v1"

        def propose(self, policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
            return RoutingPolicyDecision(
                policy_id=self.policy_id,
                input_digest=policy_input_digest(policy_input),
                net_order=(H_NET,),
            )

    class RepeatedNetPolicy:
        policy_id = "repeated-policy-v1"

        def propose(self, policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
            # An in-process policy is an untrusted boundary and can deliberately bypass a frozen
            # dataclass constructor.  The coordinator must still reject a repeated ordering.
            decision = object.__new__(RoutingPolicyDecision)
            for name, value in (
                ("policy_id", self.policy_id),
                ("input_digest", policy_input_digest(policy_input)),
                ("net_order", (H_NET, V_NET, H_NET)),
                ("corridor_hints", ()),
                ("repair_windows", ()),
                ("schema", "copper-mcp.routing-policy-decision.v1"),
            ):
                object.__setattr__(decision, name, value)
            return decision

    for supplied in (
        ThrowingPolicy(),
        ForeignNetPolicy(),
        WindowPolicy(),
        RepairWindowPolicy(),
        WrongIdPolicy(),
        WrongDigestPolicy(),
        MissingNetPolicy(),
        RepeatedNetPolicy(),
    ):
        router = _RecordingRouter()
        _install_policy_profile(monkeypatch, "hostile", lambda supplied=supplied: supplied)
        result = negotiate_routes(snapshot, envelope, router=router, policy_profile="hostile")
        assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
        assert result.diagnostic == "the negotiated routing policy was rejected"
        assert result.candidates == ()
        assert result.connections == ()
        assert not hasattr(result, "policy_evidence")
        assert router.calls == []

    factory_calls = 0

    def rejected_factory() -> object:
        nonlocal factory_calls
        factory_calls += 1
        raise RuntimeError("do not disclose factory failure")

    _install_policy_profile(monkeypatch, "throwing-factory", rejected_factory)
    router = _RecordingRouter()
    factory_result = negotiate_routes(
        snapshot,
        envelope,
        router=router,
        policy_profile="throwing-factory",
    )
    assert factory_calls == 1
    assert factory_result.diagnostic == "the negotiated routing policy was rejected"
    assert router.calls == []

    _install_policy_profile(monkeypatch, "malformed-factory", lambda: object())
    router = _RecordingRouter()
    malformed_result = negotiate_routes(
        snapshot,
        envelope,
        router=router,
        policy_profile="malformed-factory",
    )
    assert malformed_result.diagnostic == "the negotiated routing policy was rejected"
    assert router.calls == []

    unknown_router = _RecordingRouter()
    unknown_result = negotiate_routes(
        snapshot,
        envelope,
        router=unknown_router,
        policy_profile="unknown-profile",
    )
    assert unknown_result.diagnostic == "the negotiated routing policy was rejected"
    assert unknown_router.calls == []


def test_negotiated_policy_cancellation_publishes_no_binding_or_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )
    policy = _OrderedPolicy((V_NET, H_NET))
    _install_policy_profile(monkeypatch, "test-cancellation", lambda: policy)
    router = _RecordingRouter()
    before = negotiate_routes(
        snapshot,
        envelope,
        router=router,
        policy_profile="test-cancellation",
        cancelled=lambda: True,
    )

    assert before.status is NegotiatedRoutingStatus.CANCELLED
    assert before.iterations == 0
    assert type(before) is NegotiatedRoutingResult
    assert not hasattr(before, "policy_evidence")
    assert before.candidates == ()
    assert before.connections == ()
    assert policy.calls == 0
    assert router.calls == []

    checks = iter((False, True))
    after_policy = negotiate_routes(
        snapshot,
        envelope,
        router=router,
        policy_profile="test-cancellation",
        cancelled=lambda: next(checks),
    )
    assert after_policy.status is NegotiatedRoutingStatus.CANCELLED
    assert type(after_policy) is NegotiatedRoutingResult
    assert not hasattr(after_policy, "policy_evidence")
    assert after_policy.candidates == ()
    assert after_policy.connections == ()
    assert policy.calls == 1
    assert router.calls == []


def test_isolated_reference_profile_matches_in_process_order_and_result() -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
        max_iterations=1,
    )
    in_process_router = _RecordingRouter()
    isolated_router = _RecordingRouter()

    in_process = negotiate_routes(
        snapshot,
        envelope,
        router=in_process_router,
        policy_profile=REFERENCE_POLICY_PROFILE,
    )
    isolated = negotiate_routes(
        snapshot,
        envelope,
        router=isolated_router,
        policy_profile=ISOLATED_REFERENCE_POLICY_PROFILE,
    )

    assert isinstance(in_process, PolicyNegotiatedRoutingResult)
    assert isinstance(isolated, PolicyNegotiatedRoutingResult)
    assert in_process_router.calls == isolated_router.calls
    assert isolated.candidates == in_process.candidates
    assert isolated.connections == in_process.connections
    assert isolated.unrouted_nets == in_process.unrouted_nets
    assert isolated.iterations == in_process.iterations
    assert isolated.ripups == in_process.ripups
    assert isolated.overflow_resources == in_process.overflow_resources
    assert isolated.total_physical_checks == in_process.total_physical_checks
    assert isolated.policy_evidence is not None
    assert in_process.policy_evidence is not None
    assert isolated.policy_evidence.policy_profile == ISOLATED_REFERENCE_POLICY_PROFILE
    assert in_process.policy_evidence.policy_profile == REFERENCE_POLICY_PROFILE
    assert (
        replace(
            isolated,
            policy_evidence=replace(
                isolated.policy_evidence,
                policy_profile=REFERENCE_POLICY_PROFILE,
            ),
        )
        == in_process
    )


def test_isolated_policy_worker_noncanonical_output_fails_before_router_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )
    router = _RecordingRouter()

    def noncanonical_worker_frame(
        frame: bytes,
        *,
        timeout_seconds: float,
        cancelled: object,
    ) -> bytes:
        del timeout_seconds, cancelled
        return policy_worker_module._serve_reference_once(frame) + b" "

    monkeypatch.setattr(policy_worker_module, "_run_closed_frame", noncanonical_worker_frame)
    result = negotiate_routes(
        snapshot,
        envelope,
        router=router,
        policy_profile=ISOLATED_REFERENCE_POLICY_PROFILE,
    )

    assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert result.diagnostic == "the negotiated routing policy was rejected"
    assert result.candidates == ()
    assert result.connections == ()
    assert router.calls == []


def test_isolated_policy_worker_timeout_or_cancellation_fails_before_router_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )
    timeout_router = _RecordingRouter()
    monkeypatch.setattr(congestion_module, "_ISOLATED_POLICY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        policy_worker_module,
        "_worker_command",
        lambda: (sys.executable, "-I", "-c", "import time; time.sleep(60)"),
    )

    timeout_result = negotiate_routes(
        snapshot,
        envelope,
        router=timeout_router,
        policy_profile=ISOLATED_REFERENCE_POLICY_PROFILE,
    )

    assert timeout_result.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert timeout_result.diagnostic == "the negotiated routing policy was rejected"
    assert timeout_router.calls == []

    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 2

    cancelled_router = _RecordingRouter()
    cancelled_result = negotiate_routes(
        snapshot,
        envelope,
        router=cancelled_router,
        policy_profile=ISOLATED_REFERENCE_POLICY_PROFILE,
        cancelled=cancelled,
    )

    assert cancelled_result.status is NegotiatedRoutingStatus.CANCELLED
    assert cancelled_result.candidates == ()
    assert cancelled_result.connections == ()
    assert cancelled_router.calls == []
    assert calls >= 2


def test_isolated_policy_result_is_revalidated_before_router_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )
    router = _RecordingRouter()

    def wrong_input_binding(
        policy_input: RoutingPolicyInput, **_kwargs: object
    ) -> RoutingPolicyDecision:
        return RoutingPolicyDecision(
            policy_id=REFERENCE_POLICY_ID,
            input_digest=f"sha256:{'0' * 64}",
            net_order=tuple(net.net_id for net in policy_input.nets),
        )

    monkeypatch.setattr(
        congestion_module,
        "evaluate_reference_policy_in_worker",
        wrong_input_binding,
    )
    result = negotiate_routes(
        snapshot,
        envelope,
        router=router,
        policy_profile=ISOLATED_REFERENCE_POLICY_PROFILE,
    )

    assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert result.diagnostic == "the negotiated routing policy was rejected"
    assert result.candidates == ()
    assert result.connections == ()
    assert router.calls == []


def test_isolated_policy_worker_identity_is_revalidated_before_router_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _crossing_snapshot()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_requests(snapshot),
    )
    router = _RecordingRouter()

    def wrong_identity(
        policy_input: RoutingPolicyInput, **_kwargs: object
    ) -> RoutingPolicyDecision:
        return RoutingPolicyDecision(
            policy_id="different-policy-v1",
            input_digest=policy_input_digest(policy_input),
            net_order=tuple(net.net_id for net in policy_input.nets),
        )

    monkeypatch.setattr(
        congestion_module,
        "evaluate_reference_policy_in_worker",
        wrong_identity,
    )
    result = negotiate_routes(
        snapshot,
        envelope,
        router=router,
        policy_profile=ISOLATED_REFERENCE_POLICY_PROFILE,
    )

    assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert result.diagnostic == "the negotiated routing policy was rejected"
    assert result.candidates == ()
    assert result.connections == ()
    assert router.calls == []
