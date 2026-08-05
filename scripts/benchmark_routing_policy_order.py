#!/usr/bin/env python3
"""Replay the negotiated first-pass policy order against the no-profile baseline.

This is candidate-only routing evidence.  It does not invoke KiCad, apply a candidate, or turn
the coordinator's bounded physical-clearance check into a KiCad-DRC or manufacturing claim.
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
    REFERENCE_POLICY_PROFILE,
    AStarRouter,
    AStarSettings,
    CancellationCheck,
    CongestionPenalty,
    NegotiatedRoutingRequest,
    NegotiatedRoutingResult,
    PolicyNegotiatedRoutingResult,
    RouteRequest,
    RouteResult,
    VerifiedFill,
    negotiate_routes,
)
from copper_mcp.routing.policy import REFERENCE_POLICY_ID

SCRIPT_FILE = Path(__file__).resolve()
ROOT = SCRIPT_FILE.parents[1]
SCRIPT_PATH = SCRIPT_FILE.relative_to(ROOT)
IMPLEMENTATION_COMMIT = "cde2f9adc3a6436dbe99a20a12946cc70616f232"
# The generated artifact is replayed from the portable harness finalized in this commit.
# This is the typed-harness base.  Artifact provenance identifies the exact later source commit
# through a caller-supplied value, so a generated artifact never needs to contain its own commit.
TYPED_HARNESS_BASE_COMMIT = "62570d5bcbe4d812028f77380cef8230241a1785"
REPLAY_MINIMUM = 10
REPLAY_MAXIMUM = 32
LAYER_ID = "layer:F.Cu"
_SOURCE_REVISION = f"sha256:{'c' * 64}"


@dataclass(frozen=True, slots=True)
class _NetSpec:
    net_id: str
    start: tuple[int, int]
    end: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _FixtureSpec:
    name: str
    purpose: str
    nets: tuple[_NetSpec, ...]


class _ConnectionGeometry(TypedDict):
    attachment_segments: int
    component_objects: int
    end_pad_id: str
    fill_polygons: int
    obstacle_checks: int
    pad_count: int
    start_pad_id: str
    vias: int


class _GeometryValue(TypedDict):
    candidates: list[dict[str, object]]
    connections: list[_ConnectionGeometry]
    unrouted_nets: list[str]


class _GeometryReport(TypedDict):
    sha256: str
    value: _GeometryValue


class _RunMetrics(TypedDict):
    candidate_digests: list[str]
    completion: bool
    evidence: dict[str, str] | None
    geometry: _GeometryReport
    iterations: int
    observed_iteration_orders: list[list[str]]
    overflow_resource_count: int
    overflow_resources: list[dict[str, object]]
    overflow_units: int
    physical_checks: int
    ripups: int
    routed_nets: int
    router_call_count: int
    router_expansions: int
    router_obstacle_checks: int
    status: str
    total_wire_length_nm: int
    unrouted_nets: list[str]


class _CaseReport(TypedDict):
    baseline: _RunMetrics
    fixture: str
    fixture_purpose: str
    profile: _RunMetrics
    replay_count: int
    replay_deterministic: bool
    shared_envelope_digest: str
    shared_snapshot_digest: str


# This repeats the established crossing topology from ``test_routing_congestion`` as a neutral
# control.  The other two layouts predeclare an ordering-sensitive short-vs-long crossing and an
# independent orthogonal control before any measurements are made.
_FIXTURES = (
    _FixtureSpec(
        name="crossing-neutral-control",
        purpose="existing crossing fixture reproduced as a neutral order control",
        nets=(
            _NetSpec("net:horizontal", (2, 5), (10, 5)),
            _NetSpec("net:vertical", (6, 1), (6, 9)),
        ),
    ),
    _FixtureSpec(
        name="asymmetric-primary",
        purpose="predeclared short-first baseline against long-first reference-policy order",
        nets=(
            _NetSpec("net:alpha", (7, 3), (7, 7)),
            _NetSpec("net:zeta", (1, 5), (13, 5)),
        ),
    ),
    _FixtureSpec(
        name="independent-control",
        purpose="predeclared independent orthogonal control of the asymmetric primary",
        nets=(
            _NetSpec("net:alpha", (5, 5), (9, 5)),
            _NetSpec("net:zeta", (7, 1), (7, 9)),
        ),
    ),
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _ring(max_x: int = 14, max_y: int = 10) -> Ring:
    return Ring(
        (
            PointNM(0, 0),
            PointNM(max_x * 1_000_000, 0),
            PointNM(max_x * 1_000_000, max_y * 1_000_000),
            PointNM(0, max_y * 1_000_000),
        )
    )


def _pad(identifier: str, net_id: str, point: tuple[int, int]) -> Pad:
    return Pad(
        id=identifier,
        net_id=net_id,
        center=PointNM(point[0] * 1_000_000, point[1] * 1_000_000),
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
            _pad(f"pad:{net.net_id.removeprefix('net:')}:start", net.net_id, net.start),
            _pad(f"pad:{net.net_id.removeprefix('net:')}:end", net.net_id, net.end),
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
                format="synthetic-policy-benchmark",
                revision=_SOURCE_REVISION,
                format_version="1",
                generator="negotiated-policy-order-v1",
            ),
            outline=(OutlineContour(id="contour:board", outer=_ring()),),
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
        grid_step_nm=1_000_000,
        bend_penalty_nm=500_000,
        proximity_penalty_nm=0,
        max_grid_nodes=256,
        max_expansions=5_000,
        max_obstacles=64,
        max_obstacle_checks=100_000,
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
        max_iterations=4,
        max_total_expansions=50_000,
        max_total_obstacle_checks=500_000,
        max_total_physical_checks=50_000,
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


def _observed_orders(calls: list[tuple[str, int, int]], net_count: int) -> list[list[str]]:
    if not calls or len(calls) % net_count:
        return []
    return [
        [net_id for net_id, _expansions, _obstacle_checks in calls[index : index + net_count]]
        for index in range(0, len(calls), net_count)
    ]


def _geometry(result: NegotiatedRoutingResult) -> _GeometryReport:
    candidates = [
        {
            "layer_id": candidate.patch.layer_id,
            "net_id": candidate.patch.net_id,
            "paths": [
                [[point.x, point.y] for point in path.vertices] for path in candidate.patch.paths
            ],
            "width_nm": candidate.patch.width_nm,
        }
        for candidate in result.candidates
    ]
    payload: _GeometryValue = {
        "candidates": candidates,
        "connections": sorted(
            (
                {
                    "attachment_segments": connection.attachment_segments,
                    "component_objects": connection.component_objects,
                    "end_pad_id": connection.end_pad_id,
                    "fill_polygons": connection.fill_polygons,
                    "obstacle_checks": connection.obstacle_checks,
                    "pad_count": connection.pad_count,
                    "start_pad_id": connection.start_pad_id,
                    "vias": connection.vias,
                }
                for connection in result.connections
            ),
            key=lambda connection: (connection["start_pad_id"], connection["end_pad_id"]),
        ),
        "unrouted_nets": list(result.unrouted_nets),
    }
    return {"sha256": _digest(payload), "value": payload}


def _evidence(result: NegotiatedRoutingResult) -> dict[str, str] | None:
    if not isinstance(result, PolicyNegotiatedRoutingResult):
        return None
    evidence = result.policy_evidence
    if evidence is None or evidence.policy_id != REFERENCE_POLICY_ID:
        raise RuntimeError("profiled replay did not retain the deterministic policy identity")
    return {
        "composite_digest": evidence.composite_digest,
        "decision_digest": evidence.decision_digest,
        "envelope_digest": evidence.envelope_digest,
        "input_digest": evidence.input_digest,
        "policy_id": evidence.policy_id,
    }


def _overflow_resources(result: NegotiatedRoutingResult) -> list[dict[str, object]]:
    return [
        {
            "end_nm": [resource.end.x, resource.end.y],
            "kind": resource.kind,
            "start_nm": [resource.start.x, resource.start.y],
            "usage": resource.usage,
        }
        for resource in result.overflow_resources
    ]


def _one_run(
    snapshot: BoardIRSnapshot,
    envelope: NegotiatedRoutingRequest,
    *,
    profile: str | None,
) -> _RunMetrics:
    with _record_reference_router_calls() as calls:
        result = negotiate_routes(snapshot, envelope, policy_profile=profile)
    if profile is None and type(result) is not NegotiatedRoutingResult:
        raise RuntimeError("no-profile replay did not retain the legacy result shape")
    if profile is not None and not isinstance(result, PolicyNegotiatedRoutingResult):
        raise RuntimeError("profiled replay did not retain policy evidence")
    orders = _observed_orders(calls, len(envelope.requests))
    return {
        "candidate_digests": [candidate.candidate_id for candidate in result.candidates],
        "completion": result.ok,
        "evidence": _evidence(result),
        "geometry": _geometry(result),
        "iterations": result.iterations,
        "observed_iteration_orders": orders,
        "overflow_resource_count": len(result.overflow_resources),
        "overflow_resources": _overflow_resources(result),
        "overflow_units": result.overflow_units,
        "physical_checks": result.total_physical_checks,
        "ripups": result.ripups,
        "routed_nets": len(result.candidates) + len(result.connections),
        "router_expansions": sum(expansions for _net_id, expansions, _checks in calls),
        "router_obstacle_checks": sum(checks for _net_id, _expansions, checks in calls),
        "router_call_count": len(calls),
        "status": result.status.value,
        "total_wire_length_nm": result.total_wire_length_nm,
        "unrouted_nets": list(result.unrouted_nets),
    }


def _replayed_case(spec: _FixtureSpec, *, replays: int) -> _CaseReport:
    snapshot = _snapshot(spec)
    envelope = _envelope(snapshot, spec)
    baseline = tuple(_one_run(snapshot, envelope, profile=None) for _ in range(replays))
    profiled = tuple(
        _one_run(snapshot, envelope, profile=REFERENCE_POLICY_PROFILE) for _ in range(replays)
    )
    if len({json.dumps(run, sort_keys=True) for run in baseline}) != 1:
        raise RuntimeError(f"{spec.name} no-profile replays were not deterministic")
    if len({json.dumps(run, sort_keys=True) for run in profiled}) != 1:
        raise RuntimeError(f"{spec.name} profiled replays were not deterministic")
    return {
        "baseline": baseline[0],
        "fixture": spec.name,
        "fixture_purpose": spec.purpose,
        "profile": profiled[0],
        "replay_count": replays,
        "replay_deterministic": True,
        "shared_envelope_digest": envelope.policy_digest,
        "shared_snapshot_digest": snapshot.snapshot_digest,
    }


def _quality_improvement(baseline: _RunMetrics, profile: _RunMetrics) -> bool:
    no_regression = (
        profile["completion"] is True
        and baseline["completion"] is True
        and profile["routed_nets"] >= baseline["routed_nets"]
        and len(profile["unrouted_nets"]) <= len(baseline["unrouted_nets"])
        and profile["overflow_units"] <= baseline["overflow_units"]
        and profile["total_wire_length_nm"] <= baseline["total_wire_length_nm"]
    )
    work_reduction = any(
        profile[metric] * 100 <= baseline[metric] * 90
        for metric in ("router_expansions", "router_obstacle_checks")
        if baseline[metric] > 0
    )
    repair_reduction = (
        profile["iterations"] < baseline["iterations"] or profile["ripups"] < baseline["ripups"]
    )
    return no_regression and (work_reduction or repair_reduction)


def _claim(cases: list[_CaseReport]) -> dict[str, object]:
    by_name = {case["fixture"]: case for case in cases}
    qualification_cases = (by_name["asymmetric-primary"], by_name["independent-control"])
    qualifies = all(
        _quality_improvement(case["baseline"], case["profile"]) for case in qualification_cases
    )
    if qualifies:
        return {
            "classification": "quality claim",
            "quality_claim": True,
            "rule": "both predeclared qualification fixtures meet the published threshold",
        }
    return {
        "classification": "order-effect/no quality claim",
        "quality_claim": False,
        "rule": (
            "a quality claim requires both asymmetric-primary and independent-control to show at "
            "least 10% lower router expansions or obstacle checks with no higher wire length, or "
            "fewer iterations/ripups without completion, overflow, or wire-length regression"
        ),
    }


def _evidence_harness_command(*, replays: int, harness_commit: str) -> str:
    return (
        "PYTHONPATH=src python3 scripts/benchmark_routing_policy_order.py "
        f"--replays {replays} --evidence-harness-commit {harness_commit} "
        "--output benchmarks/results/routing/2026-08-05-routing-policy-order.json"
    )


def _validate_evidence_harness_commit(harness_commit: str) -> None:
    is_lowercase_sha = len(harness_commit) == 40 and all(
        character in "0123456789abcdef" for character in harness_commit
    )
    if not is_lowercase_sha:
        raise ValueError("evidence_harness_commit must be a lowercase 40-character Git commit")


def build_report(*, replays: int = REPLAY_MINIMUM, evidence_harness_commit: str) -> dict[str, Any]:
    """Build deterministic, candidate-only evidence for the documented profile selector."""

    if not REPLAY_MINIMUM <= replays <= REPLAY_MAXIMUM:
        raise ValueError(f"replays must be between {REPLAY_MINIMUM} and {REPLAY_MAXIMUM}")
    _validate_evidence_harness_commit(evidence_harness_commit)
    cases = [_replayed_case(spec, replays=replays) for spec in _FIXTURES]
    report: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/routing-policy-order/v1",
        "implementation_commit": IMPLEMENTATION_COMMIT,
        "typed_harness_base_commit": TYPED_HARNESS_BASE_COMMIT,
        "evidence_harness_commit": evidence_harness_commit,
        "evidence_harness_command": _evidence_harness_command(
            replays=replays, harness_commit=evidence_harness_commit
        ),
        "script": SCRIPT_PATH.as_posix(),
        "script_sha256": hashlib.sha256(SCRIPT_FILE.read_bytes()).hexdigest(),
        "policy_profile": REFERENCE_POLICY_PROFILE,
        "policy_id": REFERENCE_POLICY_ID,
        "replay_minimum": REPLAY_MINIMUM,
        "cases": cases,
        "claim": _claim(cases),
        "kicad_drc": "not_run",
        "apply": "not_invoked",
        "non_claims": [
            "candidate geometry is not applied to a board",
            "no model output or model-generated copper is used",
            "the bounded physical-clearance count is not KiCad DRC",
            "no manufacturing, fabrication, or board-mutation claim",
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
