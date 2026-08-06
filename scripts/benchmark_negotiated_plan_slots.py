#!/usr/bin/env python3
"""Sweep the declared negotiation policy slots against the ADR-0055 coordinator baseline.

This is candidate-only routing evidence.  It does not invoke KiCad, apply a candidate, or turn
the coordinator's bounded physical-clearance check into a KiCad-DRC or manufacturing claim.

It is deliberately an *exploratory* sweep.  Every plan below is measured on the same fixtures
under the same envelopes, and the report classifies the outcome without asserting that any slot
combination is better in general: the fixtures are small and synthetic, the sweep was not
predeclared, and there is no held-out corpus behind it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict
from unittest.mock import patch

from copper_mcp.board_ir import (
    BoardIRSnapshot,
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
    CancellationCheck,
    CongestionPenalty,
    CostUpdateRule,
    CostUpdateSlot,
    NegotiatedRoutingRequest,
    NegotiatedRoutingResult,
    NegotiationPlan,
    NetOrderRule,
    NetOrderSlot,
    PlanNegotiatedRoutingResult,
    RipUpRule,
    RipUpSlot,
    RouteRequest,
    RouteResult,
    VerifiedFill,
    negotiate_routes,
)

SCRIPT_FILE = Path(__file__).resolve()
ROOT = SCRIPT_FILE.parents[1]
SCRIPT_PATH = SCRIPT_FILE.relative_to(ROOT)
REPLAY_MINIMUM = 10
REPLAY_MAXIMUM = 32
LAYER_ID = "layer:F.Cu"
MM = 1_000_000
_SOURCE_REVISION = f"sha256:{'e' * 64}"


@dataclass(frozen=True, slots=True)
class _NetSpec:
    net_id: str
    start: tuple[int, int]
    end: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _FixtureSpec:
    name: str
    purpose: str
    present_penalty_nm: int
    history_penalty_nm: int
    max_iterations: int
    nets: tuple[_NetSpec, ...]


@dataclass(frozen=True, slots=True)
class _PlanSpec:
    name: str
    rationale: str
    plan: NegotiationPlan | None


class _GeometryValue(TypedDict):
    candidates: list[dict[str, object]]
    unrouted_nets: list[str]


class _RunMetrics(TypedDict):
    candidate_digests: list[str]
    completion: bool
    cost_update_slot_digest: str | None
    geometry_sha256: str
    iterations: int
    net_order_slot_digest: str | None
    observed_iteration_orders: list[list[str]]
    overflow_units: int
    physical_checks: int
    plan_composite_digest: str | None
    plan_digest: str | None
    rip_up_slot_digest: str | None
    ripups: int
    routed_nets: int
    router_call_count: int
    router_expansions: int
    router_obstacle_checks: int
    status: str
    total_vias: int
    total_wire_length_nm: int
    unrouted_nets: list[str]


class _CaseReport(TypedDict):
    envelope_digest: str
    fixture: str
    fixture_purpose: str
    replay_count: int
    replay_deterministic: bool
    runs: dict[str, _RunMetrics]
    snapshot_digest: str


# The neutral control reproduces the two-net crossing topology ADR-0055 and B-036 already
# measured.  The congested channel is the smallest fixture on which the coordinator genuinely
# iterates: one long horizontal net crossed by three verticals, plus two nets far enough away
# that they can never conflict and can therefore be *retained* by a partial rip-up rule.
_FIXTURES = (
    _FixtureSpec(
        name="crossing-neutral-control",
        purpose="the ADR-0055 two-net crossing topology, reproduced as a neutral control",
        present_penalty_nm=20_000_000,
        history_penalty_nm=5_000_000,
        max_iterations=8,
        nets=(
            _NetSpec("net:horizontal", (2, 5), (10, 5)),
            _NetSpec("net:vertical", (6, 1), (6, 9)),
        ),
    ),
    _FixtureSpec(
        name="congested-channel-negotiating",
        purpose="six nets under a penalty that forces multi-iteration negotiation",
        present_penalty_nm=8_000_000,
        history_penalty_nm=5_000_000,
        max_iterations=8,
        nets=(
            _NetSpec("net:cross-a", (2, 5), (12, 5)),
            _NetSpec("net:cross-b", (4, 1), (4, 9)),
            _NetSpec("net:cross-c", (6, 1), (6, 9)),
            _NetSpec("net:cross-d", (8, 1), (8, 9)),
            _NetSpec("net:far-e", (16, 2), (21, 2)),
            _NetSpec("net:far-f", (16, 8), (21, 8)),
        ),
    ),
    _FixtureSpec(
        name="congested-channel-first-pass",
        purpose="the same six nets under a penalty the first pass already resolves",
        present_penalty_nm=20_000_000,
        history_penalty_nm=5_000_000,
        max_iterations=8,
        nets=(
            _NetSpec("net:cross-a", (2, 5), (12, 5)),
            _NetSpec("net:cross-b", (4, 1), (4, 9)),
            _NetSpec("net:cross-c", (6, 1), (6, 9)),
            _NetSpec("net:cross-d", (8, 1), (8, 9)),
            _NetSpec("net:far-e", (16, 2), (21, 2)),
            _NetSpec("net:far-f", (16, 8), (21, 8)),
        ),
    ),
)

_PLANS = (
    _PlanSpec(
        name="no-plan-baseline",
        rationale="the ADR-0055 coordinator with no declared plan; the comparison baseline",
        plan=None,
    ),
    _PlanSpec(
        name="legacy-equivalent",
        rationale="the default plan, declared to reproduce the ADR-0055 strategy",
        plan=NegotiationPlan(),
    ),
    _PlanSpec(
        name="net-order/stable-identifier",
        rationale="a fixed `(net_id, seed)` order on every pass, ignoring conflict feedback",
        plan=NegotiationPlan(net_order=NetOrderSlot(rule=NetOrderRule.STABLE_IDENTIFIER)),
    ),
    _PlanSpec(
        name="net-order/demand-descending",
        rationale="longest-demand-first, the classic 'route the hard nets first' heuristic",
        plan=NegotiationPlan(net_order=NetOrderSlot(rule=NetOrderRule.DEMAND_DESCENDING)),
    ),
    _PlanSpec(
        name="net-order/demand-ascending",
        rationale="shortest-demand-first, the opposite heuristic",
        plan=NegotiationPlan(net_order=NetOrderSlot(rule=NetOrderRule.DEMAND_ASCENDING)),
    ),
    _PlanSpec(
        name="cost-update/scaled-accumulation-4",
        rationale="history accumulates four integer units per overuse instead of one",
        plan=NegotiationPlan(
            cost_update=CostUpdateSlot(
                rule=CostUpdateRule.SCALED_ACCUMULATION, accumulation_weight=4
            )
        ),
    ),
    _PlanSpec(
        name="cost-update/saturating-decay-half",
        rationale="history halves each pass before accumulating, so stale congestion ages out",
        plan=NegotiationPlan(
            cost_update=CostUpdateSlot(
                rule=CostUpdateRule.SATURATING_DECAY, decay_numerator=1, decay_denominator=2
            )
        ),
    ),
    _PlanSpec(
        name="cost-update/present-growth-13-10",
        rationale="a growing present-sharing factor, the shape of a VPR-style pres_fac schedule",
        plan=NegotiationPlan(
            cost_update=CostUpdateSlot(present_growth_numerator=13, present_growth_denominator=10)
        ),
    ),
    _PlanSpec(
        name="rip-up/conflicted-only",
        rationale="retain every net that holds no conflict instead of ripping up everything",
        plan=NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY)),
    ),
    _PlanSpec(
        name="rip-up/top-conflict-2",
        rationale="rip up at most the two most conflicted retained nets",
        plan=NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.TOP_CONFLICT_ONLY, max_ripup_nets=2)),
    ),
    _PlanSpec(
        name="composed/conflicted-only+present-growth",
        rationale="two slots moved at once, to show the composition is not additive",
        plan=NegotiationPlan(
            rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY),
            cost_update=CostUpdateSlot(present_growth_numerator=13, present_growth_denominator=10),
        ),
    ),
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _pad(identifier: str, net_id: str, point: tuple[int, int]) -> Pad:
    return Pad(
        id=identifier,
        net_id=net_id,
        center=PointNM(point[0] * MM, point[1] * MM),
        rotation_udeg=0,
        shape=PadShape.RECT,
        kind=PadKind.SMD,
        size_x_nm=400_000,
        size_y_nm=400_000,
        roundrect_radius_nm=None,
        drill_x_nm=None,
        drill_y_nm=None,
        layer_ids=(LAYER_ID,),
    )


def _snapshot(spec: _FixtureSpec) -> BoardIRSnapshot:
    pads = tuple(
        pad
        for net in spec.nets
        for pad in (
            _pad(f"pad:{net.net_id.removeprefix('net:')}:a", net.net_id, net.start),
            _pad(f"pad:{net.net_id.removeprefix('net:')}:b", net.net_id, net.end),
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
    return make_snapshot(
        make_content(
            source=SourceInfo(
                format="synthetic-negotiation-plan-benchmark",
                revision=_SOURCE_REVISION,
                format_version="1",
                generator="negotiated-plan-slots-v1",
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
            copper_layers=(Layer(id=LAYER_ID, name="F.Cu", index=0, kind="signal"),),
            nets=tuple(
                Net(id=net.net_id, name=net.net_id.removeprefix("net:").upper())
                for net in spec.nets
            ),
            constraints=ConstraintSet(
                net_classes=(net_class,),
                assignments=tuple(
                    NetClassAssignment(net_id=net.net_id, net_class_id=net_class.id)
                    for net in spec.nets
                ),
            ),
            footprints=tuple(
                Footprint(
                    id=f"footprint:{net.net_id.removeprefix('net:')}",
                    origin=pads[index * 2].center,
                    rotation_udeg=0,
                    side=FootprintSide.FRONT,
                    pad_ids=(pads[index * 2].id, pads[index * 2 + 1].id),
                )
                for index, net in enumerate(spec.nets)
            ),
            pads=pads,
        )
    )


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


def _envelope(snapshot: BoardIRSnapshot, spec: _FixtureSpec) -> NegotiatedRoutingRequest:
    settings = _settings()
    requests = tuple(
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net.net_id,
            layer_id=LAYER_ID,
            seed=index + 1,
            settings=settings,
        )
        for index, net in enumerate(spec.nets)
    )
    return NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=requests,
        max_iterations=spec.max_iterations,
        present_penalty_nm=spec.present_penalty_nm,
        history_penalty_nm=spec.history_penalty_nm,
        max_total_expansions=500_000,
        max_total_obstacle_checks=5_000_000,
        max_total_physical_checks=500_000,
    )


def _work(result: RouteResult) -> tuple[int, int]:
    if result.candidate is not None:
        return result.candidate.metrics.expanded_states, result.candidate.metrics.obstacle_checks
    if result.connected is not None:
        return 0, result.connected.obstacle_checks
    assert result.diagnostic is not None
    return result.diagnostic.expanded_states, result.diagnostic.obstacle_checks


@contextmanager
def _record_reference_router_calls() -> Iterator[list[tuple[str, int, int]]]:
    """Record calls without changing the exact AStarRouter method identity check."""

    original = AStarRouter.propose
    calls: list[tuple[str, int, int]] = []

    def recorded(
        self: AStarRouter,
        snapshot: BoardIRSnapshot,
        request: RouteRequest,
        *,
        cancelled: CancellationCheck | None = None,
        verified_fill: tuple[VerifiedFill, ...] = (),
        congestion_penalty: CongestionPenalty | None = None,
    ) -> RouteResult:
        result = original(
            self,
            snapshot,
            request,
            cancelled=cancelled,
            verified_fill=verified_fill,
            congestion_penalty=congestion_penalty,
        )
        expansions, obstacle_checks = _work(result)
        calls.append((request.net_id, expansions, obstacle_checks))
        return result

    with patch.object(AStarRouter, "propose", recorded):
        yield calls


def _observed_orders(calls: list[tuple[str, int, int]]) -> list[list[str]]:
    """Group recorded calls into passes, splitting whenever a net repeats."""

    passes: list[list[str]] = []
    current: list[str] = []
    for net_id, _expansions, _checks in calls:
        if net_id in current:
            passes.append(current)
            current = []
        current.append(net_id)
    if current:
        passes.append(current)
    return passes


def _geometry(result: NegotiatedRoutingResult) -> _GeometryValue:
    return {
        "candidates": [
            {
                "layer_id": candidate.patch.layer_id,
                "net_id": candidate.patch.net_id,
                "paths": [
                    [[point.x, point.y] for point in path.vertices]
                    for path in candidate.patch.paths
                ],
                "width_nm": candidate.patch.width_nm,
            }
            for candidate in result.candidates
        ],
        "unrouted_nets": list(result.unrouted_nets),
    }


def _one_run(
    snapshot: BoardIRSnapshot, envelope: NegotiatedRoutingRequest, spec: _PlanSpec
) -> _RunMetrics:
    with _record_reference_router_calls() as calls:
        result = negotiate_routes(snapshot, envelope, plan=spec.plan)
    if spec.plan is None and type(result) is not NegotiatedRoutingResult:
        raise RuntimeError("the no-plan replay did not retain the legacy result shape")
    if spec.plan is not None and not isinstance(result, PlanNegotiatedRoutingResult):
        raise RuntimeError(f"{spec.name} did not retain declared negotiation plan evidence")
    evidence = getattr(result, "plan_evidence", None)
    return {
        "candidate_digests": [candidate.candidate_id for candidate in result.candidates],
        "completion": result.ok,
        "cost_update_slot_digest": None if evidence is None else evidence.cost_update_slot_digest,
        "geometry_sha256": _digest(_geometry(result)),
        "iterations": result.iterations,
        "net_order_slot_digest": None if evidence is None else evidence.net_order_slot_digest,
        "observed_iteration_orders": _observed_orders(calls),
        "overflow_units": result.overflow_units,
        "physical_checks": result.total_physical_checks,
        "plan_composite_digest": None if evidence is None else evidence.composite_digest,
        "plan_digest": None if evidence is None else evidence.plan_digest,
        "rip_up_slot_digest": None if evidence is None else evidence.rip_up_slot_digest,
        "ripups": result.ripups,
        "routed_nets": len(result.candidates) + len(result.connections),
        "router_call_count": len(calls),
        "router_expansions": sum(expansions for _net, expansions, _checks in calls),
        "router_obstacle_checks": sum(checks for _net, _expansions, checks in calls),
        "status": result.status.value,
        # The negotiated coordinator is single-layer by contract, so this is structurally zero.
        # It is recorded rather than omitted so the artifact states the limit instead of hiding it.
        "total_vias": sum(candidate.metrics.vias for candidate in result.candidates),
        "total_wire_length_nm": result.total_wire_length_nm,
        "unrouted_nets": list(result.unrouted_nets),
    }


def _replayed_case(spec: _FixtureSpec, *, replays: int) -> _CaseReport:
    snapshot = _snapshot(spec)
    envelope = _envelope(snapshot, spec)
    runs: dict[str, _RunMetrics] = {}
    for plan_spec in _PLANS:
        repeated = tuple(_one_run(snapshot, envelope, plan_spec) for _ in range(replays))
        if len({json.dumps(run, sort_keys=True) for run in repeated}) != 1:
            raise RuntimeError(f"{spec.name}/{plan_spec.name} replays were not deterministic")
        runs[plan_spec.name] = repeated[0]
    return {
        "envelope_digest": envelope.policy_digest,
        "fixture": spec.name,
        "fixture_purpose": spec.purpose,
        "replay_count": replays,
        "replay_deterministic": True,
        "runs": runs,
        "snapshot_digest": snapshot.snapshot_digest,
    }


def _observations(cases: list[_CaseReport]) -> list[dict[str, object]]:
    """Report each plan's outcome relative to the no-plan baseline, without ranking them."""

    observations: list[dict[str, object]] = []
    for case in cases:
        baseline = case["runs"]["no-plan-baseline"]
        for name, run in case["runs"].items():
            if name == "no-plan-baseline":
                continue
            observations.append(
                {
                    "completes": run["completion"],
                    "baseline_completes": baseline["completion"],
                    "fixture": case["fixture"],
                    "iteration_delta": run["iterations"] - baseline["iterations"],
                    "plan": name,
                    "router_call_delta": run["router_call_count"] - baseline["router_call_count"],
                    "wire_length_delta_nm": (
                        run["total_wire_length_nm"] - baseline["total_wire_length_nm"]
                    ),
                }
            )
    return observations


def _evidence_harness_command(*, replays: int, harness_commit: str) -> str:
    return (
        "PYTHONPATH=src python3 scripts/benchmark_negotiated_plan_slots.py "
        f"--replays {replays} --evidence-harness-commit {harness_commit} "
        "--output benchmarks/results/routing/2026-08-06-negotiated-plan-slots.json"
    )


def _validate_evidence_harness_commit(harness_commit: str) -> None:
    is_lowercase_sha = len(harness_commit) == 40 and all(
        character in "0123456789abcdef" for character in harness_commit
    )
    if not is_lowercase_sha:
        raise ValueError("evidence_harness_commit must be a lowercase 40-character Git commit")


def build_report(*, replays: int = REPLAY_MINIMUM, evidence_harness_commit: str) -> dict[str, Any]:
    """Build deterministic, candidate-only evidence for the declared negotiation slots."""

    if not REPLAY_MINIMUM <= replays <= REPLAY_MAXIMUM:
        raise ValueError(f"replays must be between {REPLAY_MINIMUM} and {REPLAY_MAXIMUM}")
    _validate_evidence_harness_commit(evidence_harness_commit)
    cases = [_replayed_case(spec, replays=replays) for spec in _FIXTURES]
    report: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/negotiated-plan-slots/v1",
        "evidence_harness_commit": evidence_harness_commit,
        "evidence_harness_command": _evidence_harness_command(
            replays=replays, harness_commit=evidence_harness_commit
        ),
        "script": SCRIPT_PATH.as_posix(),
        "script_sha256": hashlib.sha256(SCRIPT_FILE.read_bytes()).hexdigest(),
        "replay_minimum": REPLAY_MINIMUM,
        "declared_plans": [{"name": item.name, "rationale": item.rationale} for item in _PLANS],
        "cases": cases,
        "observations": _observations(cases),
        "claim": {
            "classification": "exploratory sweep / no quality claim",
            "quality_claim": False,
            "rule": (
                "no slot combination is asserted better than the ADR-0055 default. The sweep was "
                "not predeclared, the three fixtures are small and synthetic, and no held-out "
                "corpus was used. A quality claim requires a criterion declared before "
                "measurement on fixtures reserved for it."
            ),
        },
        "kicad_drc": "not_run",
        "apply": "not_invoked",
        "non_claims": [
            "candidate geometry is not applied to a board",
            "no model output or model-generated copper is used",
            "the bounded physical-clearance count is not KiCad DRC",
            "no manufacturing, fabrication, or board-mutation claim",
            "via counts are structurally zero because the coordinator is single-layer",
            "no general-board, corpus, or scaling result",
        ],
    }
    report["run_id"] = _digest(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replays", type=int, default=REPLAY_MINIMUM)
    parser.add_argument("--evidence-harness-commit", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = build_report(
            replays=args.replays, evidence_harness_commit=args.evidence_harness_commit
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
