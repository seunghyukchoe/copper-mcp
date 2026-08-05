from __future__ import annotations

import hashlib
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
