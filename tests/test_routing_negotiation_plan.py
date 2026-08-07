"""Tests for the three declared negotiation policy slots and their digest binding."""

from __future__ import annotations

from dataclasses import replace

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
    LEGACY_EQUIVALENT_PLAN,
    PLAN_NEGOTIATED_ROUTING_POLICY,
    REFERENCE_POLICY_PROFILE,
    AStarRouter,
    AStarSettings,
    CostUpdateRule,
    CostUpdateSlot,
    NegotiatedRoutingRequest,
    NegotiatedRoutingResult,
    NegotiatedRoutingStatus,
    NegotiationPlan,
    NegotiationPlanEvidence,
    NetOrderRule,
    NetOrderSlot,
    PlanNegotiatedRoutingResult,
    RipUpRule,
    RipUpSlot,
    RouteRequest,
    RouteResult,
    canonical_candidate_bytes,
    negotiate_routes,
)
from copper_mcp.routing.negotiation_plan import (
    next_history_value,
    next_present_penalty,
    ordered_net_ids,
    ripup_net_ids,
)
from copper_mcp.routing.physical_clearance import (
    PhysicalClearanceFailure,
    PhysicalClearanceVerificationResult,
)

BOARD_SOURCE = f"sha256:{'d' * 64}"
LAYER = "layer:F.Cu"
MM = 1_000_000

CROSS_A = "net:cross-a"
CROSS_B = "net:cross-b"
CROSS_C = "net:cross-c"
CROSS_D = "net:cross-d"
FAR_E = "net:far-e"
FAR_F = "net:far-f"
BLOCKER = "net:blocker"

# One long horizontal net crossed by three vertical nets, plus two nets far enough away that they
# can never conflict.  The horizontal net therefore accumulates three lattice conflicts while the
# far pair accumulates none, which is what makes a partial rip-up rule observably different from
# ripping up everything.
_CONGESTED_NETS: tuple[tuple[str, tuple[int, int], tuple[int, int]], ...] = (
    (CROSS_A, (2, 5), (12, 5)),
    (CROSS_B, (4, 1), (4, 9)),
    (CROSS_C, (6, 1), (6, 9)),
    (CROSS_D, (8, 1), (8, 9)),
    (FAR_E, (16, 2), (21, 2)),
    (FAR_F, (16, 8), (21, 8)),
)

_DETOUR_NETS: tuple[tuple[str, tuple[int, int], tuple[int, int]], ...] = (
    (CROSS_A, (2, 5), (12, 5)),
    (FAR_E, (16, 2), (21, 2)),
)


def _pad(identifier: str, net_id: str, center: tuple[int, int]) -> Pad:
    return Pad(
        id=identifier,
        net_id=net_id,
        center=PointNM(center[0] * MM, center[1] * MM),
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


def _snapshot(
    specs: tuple[tuple[str, tuple[int, int], tuple[int, int]], ...],
    *,
    blocker: tuple[tuple[int, int], tuple[int, int]] | None = None,
) -> object:
    pads = tuple(
        pad
        for net_id, start, end in specs
        for pad in (
            _pad(f"pad:{net_id.removeprefix('net:')}:a", net_id, start),
            _pad(f"pad:{net_id.removeprefix('net:')}:b", net_id, end),
        )
    )
    net_class = NetClass(
        id="class:signal",
        name="Signal",
        clearance_nm=100_000,
        track_width_nm=200_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    net_ids = [net_id for net_id, _start, _end in specs]
    segments: tuple[Segment, ...] = ()
    if blocker is not None:
        net_ids.append(BLOCKER)
        segments = (
            Segment(
                id="segment:blocker",
                net_id=BLOCKER,
                layer_id=LAYER,
                start=PointNM(blocker[0][0] * MM, blocker[0][1] * MM),
                end=PointNM(blocker[1][0] * MM, blocker[1][1] * MM),
                width_nm=200_000,
            ),
        )
    content = make_content(
        source=SourceInfo(
            format="test",
            revision=BOARD_SOURCE,
            format_version="1",
            generator="negotiation-plan-test",
        ),
        outline=(
            OutlineContour(
                id="contour:board",
                outer=Ring(
                    (
                        PointNM(0, 0),
                        PointNM(23 * MM, 0),
                        PointNM(23 * MM, 11 * MM),
                        PointNM(0, 11 * MM),
                    )
                ),
            ),
        ),
        copper_layers=(Layer(id=LAYER, name="F.Cu", index=0, kind="signal"),),
        nets=tuple(Net(id=net_id, name=net_id.removeprefix("net:").upper()) for net_id in net_ids),
        constraints=ConstraintSet(
            net_classes=(net_class,),
            assignments=tuple(
                NetClassAssignment(net_id=net_id, net_class_id=net_class.id) for net_id in net_ids
            ),
        ),
        footprints=tuple(
            Footprint(
                id=f"footprint:{net_id.removeprefix('net:')}",
                origin=pads[index * 2].center,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=(pads[index * 2].id, pads[index * 2 + 1].id),
            )
            for index, (net_id, _start, _end) in enumerate(specs)
        ),
        pads=pads,
        segments=segments,
    )
    return make_snapshot(content)


def _settings() -> AStarSettings:
    return AStarSettings(
        grid_step_nm=MM,
        bend_penalty_nm=500_000,
        proximity_penalty_nm=0,
        max_grid_nodes=4_096,
        max_expansions=20_000,
        max_obstacles=256,
        max_obstacle_checks=400_000,
    )


def _envelope(
    snapshot: object,
    specs: tuple[tuple[str, tuple[int, int], tuple[int, int]], ...],
    **overrides: int,
) -> NegotiatedRoutingRequest:
    assert hasattr(snapshot, "snapshot_digest")
    settings = _settings()
    requests = tuple(
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net_id,
            layer_id=LAYER,
            seed=index + 1,
            settings=settings,
        )
        for index, (net_id, _start, _end) in enumerate(specs)
    )
    values: dict[str, int] = {
        "max_iterations": 2,
        "present_penalty_nm": 0,
        "history_penalty_nm": 0,
        "max_total_expansions": 2_000_000,
        "max_total_obstacle_checks": 10_000_000,
        "max_total_physical_checks": 2_000_000,
    }
    values.update(overrides)
    return NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest, requests=requests, **values
    )


class _RecordingRouter:
    """A pass-through backend that records the exact per-iteration router call order."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._reference = AStarRouter()

    def propose(self, snapshot: object, request: RouteRequest, **kwargs: object) -> RouteResult:
        self.calls.append(request.net_id)
        return self._reference.propose(snapshot, request, **kwargs)


class _DivergentRouter:
    """A backend whose first proposal is legal but is not what the reference core produces."""

    def __init__(self) -> None:
        self.calls = 0
        self._reference = AStarRouter()

    def propose(self, snapshot: object, request: RouteRequest, **kwargs: object) -> RouteResult:
        self.calls += 1
        if self.calls != 1:
            return self._reference.propose(snapshot, request, **kwargs)
        biased = dict(kwargs)
        biased["congestion_penalty"] = lambda start, end: (
            50_000_000 if start.y == 5 * MM and end.y == 5 * MM else 0
        )
        return self._reference.propose(snapshot, request, **biased)


# --------------------------------------------------------------------------------------------
# Slot declaration, digest composition, and the no-inert-parameter invariant
# --------------------------------------------------------------------------------------------


def test_each_slot_carries_its_own_digest_and_they_are_mutually_distinct() -> None:
    plan = NegotiationPlan()

    digests = {
        plan.net_order.slot_digest,
        plan.cost_update.slot_digest,
        plan.rip_up.slot_digest,
        plan.plan_digest,
    }

    assert len(digests) == 4
    assert all(digest.startswith("sha256:") for digest in digests)


@pytest.mark.parametrize(
    "plan",
    [
        NegotiationPlan(net_order=NetOrderSlot(rule=NetOrderRule.STABLE_IDENTIFIER)),
        NegotiationPlan(net_order=NetOrderSlot(rule=NetOrderRule.DEMAND_DESCENDING)),
        NegotiationPlan(cost_update=CostUpdateSlot(rule=CostUpdateRule.SCALED_ACCUMULATION)),
        NegotiationPlan(
            cost_update=CostUpdateSlot(present_growth_numerator=13, present_growth_denominator=10)
        ),
        NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY)),
        NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.TOP_CONFLICT_ONLY, max_ripup_nets=2)),
    ],
)
def test_changing_any_one_slot_changes_the_plan_digest(plan: NegotiationPlan) -> None:
    assert plan.plan_digest != NegotiationPlan().plan_digest


def test_the_plan_digest_is_composed_of_exactly_the_three_slot_digests() -> None:
    left = NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY))
    right = NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY))

    assert left.as_json() == {
        "cost_update_slot_digest": left.cost_update.slot_digest,
        "net_order_slot_digest": left.net_order.slot_digest,
        "rip_up_slot_digest": left.rip_up.slot_digest,
        "schema": "copper-mcp.negotiation-plan.v1",
    }
    assert left.plan_digest == right.plan_digest


@pytest.mark.parametrize(
    "build",
    [
        # A weight the declared rule never reads must not be able to vary a digest.
        lambda: CostUpdateSlot(rule=CostUpdateRule.ACCUMULATED_OVERUSE, accumulation_weight=4),
        lambda: CostUpdateSlot(rule=CostUpdateRule.SCALED_ACCUMULATION, decay_numerator=0),
        lambda: CostUpdateSlot(rule=CostUpdateRule.ACCUMULATED_OVERUSE, decay_denominator=2),
        lambda: RipUpSlot(rule=RipUpRule.ALL_NETS, max_ripup_nets=2),
        lambda: RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY, max_ripup_nets=1),
        # Amplifying history through the "decay" ratio, or letting present pressure weaken,
        # would break the negotiation's monotonic-pressure argument.
        lambda: CostUpdateSlot(
            rule=CostUpdateRule.SATURATING_DECAY, decay_numerator=3, decay_denominator=2
        ),
        lambda: CostUpdateSlot(present_growth_numerator=1, present_growth_denominator=2),
        # Out-of-range and non-literal declarations.
        lambda: CostUpdateSlot(rule=CostUpdateRule.SCALED_ACCUMULATION, accumulation_weight=0),
        lambda: CostUpdateSlot(rule=CostUpdateRule.SCALED_ACCUMULATION, accumulation_weight=2048),
        lambda: RipUpSlot(rule=RipUpRule.TOP_CONFLICT_ONLY, max_ripup_nets=0),
        lambda: RipUpSlot(rule=RipUpRule.TOP_CONFLICT_ONLY, max_ripup_nets=33),
        lambda: NetOrderSlot(rule="stable-identifier-v1"),  # type: ignore[arg-type]
        lambda: NegotiationPlan(net_order=CostUpdateSlot()),  # type: ignore[arg-type]
        lambda: NegotiationPlan(schema="copper-mcp.negotiation-plan.v2"),
    ],
)
def test_declared_slots_reject_inert_amplifying_and_undeclared_values(build: object) -> None:
    with pytest.raises(ValueError):
        build()  # type: ignore[operator]


def test_declared_slot_rules_are_closed_enumerations() -> None:
    assert set(NetOrderRule) == {
        NetOrderRule.STABLE_IDENTIFIER,
        NetOrderRule.CONFLICT_DESCENDING,
        NetOrderRule.DEMAND_DESCENDING,
        NetOrderRule.DEMAND_ASCENDING,
    }
    assert set(CostUpdateRule) == {
        CostUpdateRule.ACCUMULATED_OVERUSE,
        CostUpdateRule.SCALED_ACCUMULATION,
        CostUpdateRule.SATURATING_DECAY,
    }
    assert set(RipUpRule) == {
        RipUpRule.ALL_NETS,
        RipUpRule.CONFLICTED_ONLY,
        RipUpRule.TOP_CONFLICT_ONLY,
        RipUpRule.CONFLICT_WINDOW,
    }


# --------------------------------------------------------------------------------------------
# Pure slot behavior
# --------------------------------------------------------------------------------------------


def test_net_order_rules_produce_the_declared_permutations() -> None:
    nets = (("net:b", 2), ("net:a", 1), ("net:c", 3))
    scores = {"net:a": 1, "net:c": 5}
    demand = {"net:a": 10, "net:b": 30, "net:c": 20}

    def order(rule: NetOrderRule, iteration: int) -> tuple[str, ...]:
        return ordered_net_ids(
            NetOrderSlot(rule=rule),
            nets=nets,
            iteration=iteration,
            conflict_scores=scores,
            demand_cells=demand,
        )

    assert order(NetOrderRule.STABLE_IDENTIFIER, 1) == ("net:a", "net:b", "net:c")
    assert order(NetOrderRule.STABLE_IDENTIFIER, 4) == ("net:a", "net:b", "net:c")
    assert order(NetOrderRule.CONFLICT_DESCENDING, 1) == ("net:a", "net:b", "net:c")
    assert order(NetOrderRule.CONFLICT_DESCENDING, 2) == ("net:c", "net:a", "net:b")
    assert order(NetOrderRule.DEMAND_DESCENDING, 1) == ("net:b", "net:c", "net:a")
    assert order(NetOrderRule.DEMAND_ASCENDING, 1) == ("net:a", "net:c", "net:b")
    with pytest.raises(ValueError):
        order(NetOrderRule.STABLE_IDENTIFIER, 0)


def test_cost_update_rules_move_counters_exactly_as_declared() -> None:
    additive = CostUpdateSlot()
    scaled = CostUpdateSlot(rule=CostUpdateRule.SCALED_ACCUMULATION, accumulation_weight=4)
    decaying = CostUpdateSlot(
        rule=CostUpdateRule.SATURATING_DECAY, decay_numerator=1, decay_denominator=2
    )

    assert next_history_value(additive, previous=3, overuse=2, cap=100) == 5
    assert next_history_value(scaled, previous=3, overuse=2, cap=100) == 11
    assert next_history_value(decaying, previous=10, overuse=0, cap=100) == 5
    assert next_history_value(decaying, previous=10, overuse=3, cap=100) == 8
    # Every rule saturates at the ledger cap rather than growing without bound.
    assert next_history_value(scaled, previous=99, overuse=9, cap=100) == 100
    assert additive.decays_unused_resources is False
    assert decaying.decays_unused_resources is True

    growing = CostUpdateSlot(present_growth_numerator=13, present_growth_denominator=10)
    assert next_present_penalty(CostUpdateSlot(), previous=1_000, cap=10_000) == 1_000
    assert next_present_penalty(growing, previous=1_000, cap=10_000) == 1_300
    assert next_present_penalty(growing, previous=9_000, cap=10_000) == 10_000
    # Integer floor division is deliberate; a zero penalty has nothing to grow from.
    assert next_present_penalty(growing, previous=0, cap=10_000) == 0


def test_rip_up_rules_always_retry_a_net_that_has_nothing_retained() -> None:
    nets = (("net:a", 1), ("net:b", 2), ("net:c", 3), ("net:d", 4))
    scores = {"net:b": 7, "net:c": 3}

    def selection(rule: RipUpRule, retained: frozenset[str], ceiling: int = 32) -> frozenset[str]:
        slot = (
            RipUpSlot(rule=rule, max_ripup_nets=ceiling)
            if rule is RipUpRule.TOP_CONFLICT_ONLY
            else RipUpSlot(rule=rule)
        )
        return ripup_net_ids(slot, nets=nets, conflict_scores=scores, retained=retained)

    held = frozenset({"net:b", "net:c", "net:d"})
    assert selection(RipUpRule.ALL_NETS, held) == frozenset({"net:a", "net:b", "net:c", "net:d"})
    assert selection(RipUpRule.CONFLICTED_ONLY, held) == frozenset({"net:a", "net:b", "net:c"})
    assert selection(RipUpRule.TOP_CONFLICT_ONLY, held, 1) == frozenset({"net:a", "net:b"})
    # A net with nothing retained is re-routed under every rule and is never capped away.
    assert "net:a" in selection(RipUpRule.TOP_CONFLICT_ONLY, held, 1)
    assert selection(RipUpRule.CONFLICTED_ONLY, frozenset()) == frozenset(
        {"net:a", "net:b", "net:c", "net:d"}
    )
    with pytest.raises(ValueError):
        ripup_net_ids(RipUpSlot(), nets=nets, conflict_scores=scores, retained=frozenset({"net:z"}))


# --------------------------------------------------------------------------------------------
# Coordinator integration
# --------------------------------------------------------------------------------------------


def _plan_run(plan: NegotiationPlan | None, **overrides: int) -> NegotiatedRoutingResult:
    snapshot = _snapshot(_CONGESTED_NETS)
    envelope = _envelope(snapshot, _CONGESTED_NETS, **overrides)
    return negotiate_routes(snapshot, envelope, plan=plan)


def test_a_declared_plan_replays_to_identical_candidate_bytes() -> None:
    plan = NegotiationPlan(
        net_order=NetOrderSlot(rule=NetOrderRule.DEMAND_DESCENDING),
        cost_update=CostUpdateSlot(rule=CostUpdateRule.SCALED_ACCUMULATION, accumulation_weight=3),
        rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY),
    )

    first = _plan_run(plan)
    second = _plan_run(plan)

    assert isinstance(first, PlanNegotiatedRoutingResult)
    assert [canonical_candidate_bytes(item) for item in first.candidates] == [
        canonical_candidate_bytes(item) for item in second.candidates
    ]
    assert first.candidates == second.candidates
    assert first.status is second.status
    assert first.iterations == second.iterations
    assert first.ripups == second.ripups
    assert first.total_wire_length_nm == second.total_wire_length_nm
    assert first.plan_evidence == second.plan_evidence


def test_the_legacy_equivalent_plan_reproduces_no_plan_geometry_under_a_new_identity() -> None:
    snapshot = _snapshot(_DETOUR_NETS)
    envelope = _envelope(
        snapshot, _DETOUR_NETS, present_penalty_nm=20_000_000, history_penalty_nm=5_000_000
    )

    baseline = negotiate_routes(snapshot, envelope)
    planned = negotiate_routes(snapshot, envelope, plan=LEGACY_EQUIVALENT_PLAN)

    assert type(baseline) is NegotiatedRoutingResult
    assert isinstance(planned, PlanNegotiatedRoutingResult)
    assert baseline.status is planned.status is NegotiatedRoutingStatus.COMPLETED
    assert baseline.total_wire_length_nm == planned.total_wire_length_nm
    assert [item.patch for item in baseline.candidates] == [
        item.patch for item in planned.candidates
    ]
    # Same copper, deliberately different identity: the plan is part of what produced it.
    assert all(item.policy.startswith("negotiated-congestion-v2-") for item in baseline.candidates)
    assert all(
        item.policy.startswith(f"{PLAN_NEGOTIATED_ROUTING_POLICY}-") for item in planned.candidates
    )
    assert {item.candidate_id for item in baseline.candidates}.isdisjoint(
        {item.candidate_id for item in planned.candidates}
    )


def test_changing_one_policy_slot_changes_every_published_candidate_identity() -> None:
    snapshot = _snapshot(_DETOUR_NETS)
    envelope = _envelope(
        snapshot, _DETOUR_NETS, present_penalty_nm=20_000_000, history_penalty_nm=5_000_000
    )
    variants = (
        LEGACY_EQUIVALENT_PLAN,
        NegotiationPlan(net_order=NetOrderSlot(rule=NetOrderRule.DEMAND_ASCENDING)),
        NegotiationPlan(cost_update=CostUpdateSlot(rule=CostUpdateRule.SCALED_ACCUMULATION)),
        NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY)),
    )

    results = [negotiate_routes(snapshot, envelope, plan=item) for item in variants]

    identities = [tuple(item.candidate_id for item in result.candidates) for result in results]
    assert all(result.status is NegotiatedRoutingStatus.COMPLETED for result in results)
    assert len(set(identities)) == len(variants)
    assert len({result.plan_evidence.plan_digest for result in results}) == len(variants)  # type: ignore[attr-defined]
    assert len({result.plan_evidence.composite_digest for result in results}) == len(variants)  # type: ignore[attr-defined]
    # The envelope is shared, so only the plan can be what moved the identity.
    assert len({result.policy_digest for result in results}) == 1


def test_plan_evidence_publishes_slot_digests_that_recompose_into_the_plan_digest() -> None:
    result = _plan_run(NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY)))

    assert isinstance(result, PlanNegotiatedRoutingResult)
    evidence = result.plan_evidence
    assert evidence is not None
    assert evidence.envelope_digest == result.policy_digest
    assert evidence.schema == "copper-mcp.negotiation-plan-evidence.v1"

    with pytest.raises(ValueError):
        replace(evidence, rip_up_slot_digest=evidence.net_order_slot_digest)
    with pytest.raises(ValueError):
        replace(evidence, plan_digest=evidence.composite_digest)
    with pytest.raises(ValueError):
        replace(evidence, composite_digest=evidence.plan_digest)


def test_a_plan_enabled_result_refuses_evidence_bound_to_another_envelope() -> None:
    result = _plan_run(LEGACY_EQUIVALENT_PLAN)
    assert isinstance(result, PlanNegotiatedRoutingResult)
    other = _plan_run(LEGACY_EQUIVALENT_PLAN, max_iterations=3)
    assert isinstance(other, PlanNegotiatedRoutingResult)
    assert other.plan_evidence != result.plan_evidence

    with pytest.raises(ValueError):
        replace(result, plan_evidence=other.plan_evidence)
    with pytest.raises(ValueError):
        replace(result, plan_evidence=None)


def test_the_rip_up_slot_decides_how_many_nets_are_re_routed() -> None:
    def calls(plan: NegotiationPlan) -> list[str]:
        snapshot = _snapshot(_CONGESTED_NETS)
        envelope = _envelope(snapshot, _CONGESTED_NETS)
        router = _RecordingRouter()
        negotiate_routes(snapshot, envelope, router=router, plan=plan)
        return router.calls

    every_net = calls(NegotiationPlan())
    conflicted = calls(NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY)))
    capped = calls(
        NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.TOP_CONFLICT_ONLY, max_ripup_nets=1))
    )

    # Every rule routes all six nets on the first pass; they differ only in what is retained.
    assert every_net[:6] == conflicted[:6] == capped[:6]
    assert len(every_net) == 12
    assert set(every_net[6:]) == {CROSS_A, CROSS_B, CROSS_C, CROSS_D, FAR_E, FAR_F}
    # The two far nets never conflict, so a conflict-driven rule keeps them.
    assert set(conflicted[6:]) == {CROSS_A, CROSS_B, CROSS_C, CROSS_D}
    assert len(capped) < len(conflicted) < len(every_net)


def test_the_net_order_slot_decides_the_first_pass_router_order() -> None:
    def first_pass(plan: NegotiationPlan) -> list[str]:
        snapshot = _snapshot(_CONGESTED_NETS)
        envelope = _envelope(snapshot, _CONGESTED_NETS, max_iterations=1)
        router = _RecordingRouter()
        negotiate_routes(snapshot, envelope, router=router, plan=plan)
        return router.calls

    stable = first_pass(
        NegotiationPlan(net_order=NetOrderSlot(rule=NetOrderRule.STABLE_IDENTIFIER))
    )
    longest = first_pass(
        NegotiationPlan(net_order=NetOrderSlot(rule=NetOrderRule.DEMAND_DESCENDING))
    )
    shortest = first_pass(
        NegotiationPlan(net_order=NetOrderSlot(rule=NetOrderRule.DEMAND_ASCENDING))
    )

    # Demand is the exact Manhattan pad separation in lattice cells: 10, 8, 8, 8, 5, 5.
    assert stable == [CROSS_A, CROSS_B, CROSS_C, CROSS_D, FAR_E, FAR_F]
    assert longest == [CROSS_A, CROSS_B, CROSS_C, CROSS_D, FAR_E, FAR_F]
    assert shortest == [FAR_E, FAR_F, CROSS_B, CROSS_C, CROSS_D, CROSS_A]
    # Both demand rules tie-break on `(net_id, seed)` ascending, so neither is the other reversed.
    assert longest != list(reversed(shortest))


def test_an_unattributed_clearance_refusal_retains_nothing_for_the_next_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal that blames no pair in particular has to blame the whole allocation.

    A budget-exhausted or malformed-candidate verdict says nothing about *which* nets are at
    fault, so a partial rip-up rule may not keep any of them: retaining copper the gate never
    cleared would let an unchecked allocation survive into the next pass.
    """

    def unattributed(*_args: object, **_kwargs: object) -> PhysicalClearanceVerificationResult:
        return PhysicalClearanceVerificationResult(
            pair_checks=0, failure=PhysicalClearanceFailure.BUDGET_EXHAUSTED
        )

    snapshot = _snapshot(_CONGESTED_NETS)
    envelope = _envelope(snapshot, _CONGESTED_NETS)
    plan = NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY))

    attributed_router = _RecordingRouter()
    negotiate_routes(snapshot, envelope, router=attributed_router, plan=plan)

    monkeypatch.setattr(congestion_module, "verify_negotiated_physical_clearance", unattributed)
    unattributed_router = _RecordingRouter()
    result = negotiate_routes(snapshot, envelope, router=unattributed_router, plan=plan)

    every_net = {net_id for net_id, _start, _end in _CONGESTED_NETS}
    # The attributed run keeps the two far nets; the unattributed one keeps nothing at all.
    assert set(attributed_router.calls[6:]) < every_net
    assert unattributed_router.calls[6:] and set(unattributed_router.calls[6:]) == every_net
    assert len(unattributed_router.calls) > len(attributed_router.calls)
    assert result.status is not NegotiatedRoutingStatus.COMPLETED
    assert result.candidates == ()


def test_the_clearance_gate_attributes_the_pair_it_refused() -> None:
    result = _plan_run(NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY)))

    assert result.status is not NegotiatedRoutingStatus.COMPLETED
    assert PhysicalClearanceVerificationResult(
        pair_checks=1,
        failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION,
        violating_nets=(CROSS_A, CROSS_B),
    ).violating_nets == (CROSS_A, CROSS_B)
    with pytest.raises(ValueError):
        PhysicalClearanceVerificationResult(pair_checks=1, violating_nets=(CROSS_A, CROSS_B))
    with pytest.raises(ValueError):
        PhysicalClearanceVerificationResult(
            pair_checks=1,
            failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION,
            violating_nets=(CROSS_B, CROSS_A),
        )


# --------------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plan",
    [
        "legacy-equivalent",
        {"net_order": "stable-identifier-v1"},
        object(),
        NetOrderSlot(),
    ],
)
def test_a_plan_that_is_not_a_declared_plan_is_refused(plan: object) -> None:
    snapshot = _snapshot(_DETOUR_NETS)
    envelope = _envelope(snapshot, _DETOUR_NETS)

    result = negotiate_routes(snapshot, envelope, plan=plan)

    assert type(result) is NegotiatedRoutingResult
    assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert result.candidates == ()
    assert result.diagnostic == "the declared negotiation plan was rejected"


def test_a_plan_and_a_policy_profile_cannot_be_declared_together() -> None:
    snapshot = _snapshot(_DETOUR_NETS)
    envelope = _envelope(snapshot, _DETOUR_NETS)

    result = negotiate_routes(
        snapshot, envelope, plan=LEGACY_EQUIVALENT_PLAN, policy_profile=REFERENCE_POLICY_PROFILE
    )

    assert type(result) is NegotiatedRoutingResult
    assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert result.candidates == ()
    assert result.diagnostic == (
        "a negotiated run declares either a policy profile or a negotiation plan"
    )


def test_a_plan_run_refuses_when_the_coordinator_work_budget_is_exhausted() -> None:
    snapshot = _snapshot(_CONGESTED_NETS)
    envelope = _envelope(snapshot, _CONGESTED_NETS, max_total_expansions=1)

    result = negotiate_routes(snapshot, envelope, plan=LEGACY_EQUIVALENT_PLAN)

    assert result.status is NegotiatedRoutingStatus.NO_PATH
    assert result.candidates == ()
    assert result.connections == ()
    assert result.diagnostic == "the negotiated routing budget was exhausted"
    assert set(result.unrouted_nets) == {net_id for net_id, _s, _e in _CONGESTED_NETS}


def test_a_plan_run_refuses_a_backend_whose_proposal_the_reference_core_does_not_reproduce() -> (
    None
):
    snapshot = _snapshot(_DETOUR_NETS)
    envelope = _envelope(
        snapshot, _DETOUR_NETS, present_penalty_nm=20_000_000, history_penalty_nm=5_000_000
    )
    router = _DivergentRouter()

    result = negotiate_routes(snapshot, envelope, router=router, plan=LEGACY_EQUIVALENT_PLAN)

    assert isinstance(result, PlanNegotiatedRoutingResult)
    assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert result.candidates == ()
    assert result.connections == ()
    assert result.diagnostic == "the negotiated router result failed identity validation"
    # The refusal is a replay disagreement, not a backend that never ran.
    assert router.calls >= 1


# --------------------------------------------------------------------------------------------
# Metamorphic relation
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plan",
    [
        None,
        LEGACY_EQUIVALENT_PLAN,
        NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY)),
        NegotiationPlan(net_order=NetOrderSlot(rule=NetOrderRule.DEMAND_DESCENDING)),
    ],
)
def test_adding_an_obstacle_never_decreases_total_wire_length(
    plan: NegotiationPlan | None,
) -> None:
    clear = _snapshot(_DETOUR_NETS)
    # A foreign-net stub planted across the straight route; obstacles only ever remove freedom.
    blocked = _snapshot(_DETOUR_NETS, blocker=((7, 3), (7, 7)))
    settings = {"present_penalty_nm": 20_000_000, "history_penalty_nm": 5_000_000}

    without = negotiate_routes(clear, _envelope(clear, _DETOUR_NETS, **settings), plan=plan)
    with_obstacle = negotiate_routes(
        blocked, _envelope(blocked, _DETOUR_NETS, **settings), plan=plan
    )

    assert without.status is NegotiatedRoutingStatus.COMPLETED
    if with_obstacle.status is NegotiatedRoutingStatus.COMPLETED:
        assert with_obstacle.total_wire_length_nm >= without.total_wire_length_nm
    else:
        # The honest alternative to a longer route is no route at all, never a shorter one.
        assert with_obstacle.total_wire_length_nm == 0


def test_the_planted_obstacle_actually_forces_a_longer_route() -> None:
    clear = _snapshot(_DETOUR_NETS)
    blocked = _snapshot(_DETOUR_NETS, blocker=((7, 3), (7, 7)))
    settings = {"present_penalty_nm": 20_000_000, "history_penalty_nm": 5_000_000}

    without = negotiate_routes(clear, _envelope(clear, _DETOUR_NETS, **settings))
    with_obstacle = negotiate_routes(blocked, _envelope(blocked, _DETOUR_NETS, **settings))

    assert with_obstacle.status is NegotiatedRoutingStatus.COMPLETED
    assert with_obstacle.total_wire_length_nm > without.total_wire_length_nm


def test_plan_evidence_is_rejected_when_its_digests_are_malformed() -> None:
    valid = f"sha256:{'a' * 64}"
    with pytest.raises(ValueError):
        NegotiationPlanEvidence(
            envelope_digest="not-a-digest",
            plan_digest=valid,
            net_order_slot_digest=valid,
            cost_update_slot_digest=valid,
            rip_up_slot_digest=valid,
            composite_digest=valid,
        )
