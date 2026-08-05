#!/usr/bin/env python3
"""Replay an opt-in route-aware placement ranking on an original audio fixture.

The benchmark compares two bounded searches over the same legalizer-issued candidate set.  The
baseline uses the historical same-net Manhattan proxy; the opt-in policy ranks with one independent
candidate-only A* probe.  No board derivative, route patch, or placement candidate is applied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.placement import build_placement_view, parse_placement_intent
from copper_mcp.placement.route_scoring import RouteProbeSettings, score_route_aware_candidate
from copper_mcp.placement.solver import (
    PlacementScoringPolicy,
    PlacementSolverSettings,
    solve_placement,
)
from copper_mcp.routing.contracts import AStarSettings

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks/audio/fixtures/ne5532-stereo-summing-routing-v1.kicad_pcb"
PROVENANCE = FIXTURE.with_suffix(".provenance.json")
DEFAULT_OUTPUT = ROOT / "benchmarks/results/placement/2026-08-05-route-aware-placement-v1.json"
CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}
PROBE_SETTINGS = RouteProbeSettings(
    max_probes=1,
    max_total_probes=128,
    astar_settings=AStarSettings(
        grid_step_nm=1_000_000,
        max_grid_nodes=100_000,
        max_expansions=10_000,
        max_obstacles=256,
        max_obstacle_checks=100_000,
    ),
)
SEARCH_SETTINGS = {
    "max_evaluations": 128,
    "max_rounds": 4,
    "beam_width": 4,
    "max_ranked": 8,
    "step_nm": 5_000_000,
    "deadline_seconds": 30.0,
    "legalizer_max_checks": 200_000,
    "legalizer_deadline_seconds": 3.0,
}
MINIMUM_IMPROVEMENT_PERCENT = 10


class RouteAwarePlacementBenchmarkError(RuntimeError):
    """Raised when fixture provenance, determinism, or the declared criterion drifts."""


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - fixed local Git executable and argv
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:route-aware-placement", name="Route-aware placement", **CONSTRAINTS
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _fixture() -> tuple[bytes, object, object, object]:
    source = FIXTURE.read_bytes()
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    if (
        provenance.get("origin") != "coppermcp-original"
        or provenance.get("license_spdx") != "Apache-2.0"
        or provenance.get("third_party_content_included") is not False
        or provenance.get("artifact_sha256") != hashlib.sha256(source).hexdigest()
    ):
        raise RouteAwarePlacementBenchmarkError(
            "audio fixture provenance is not the reviewed original"
        )
    conversion = parse_kicad_bytes(source, _profile())
    if conversion.snapshot is None or conversion.diagnostics:
        raise RouteAwarePlacementBenchmarkError("audio fixture is outside the Board IR subset")
    view = build_placement_view(source, conversion.snapshot)
    intent = parse_placement_intent(
        {
            "board": FIXTURE.name,
            "constraints": CONSTRAINTS,
            "subjects": sorted(view.footprints),
            "placement_grid_nm": SEARCH_SETTINGS["step_nm"],
        }
    )
    return source, conversion.snapshot, view, intent


def _evidence(candidate: object, snapshot: object, view: object) -> object:
    evidence, status = score_route_aware_candidate(
        candidate,  # type: ignore[arg-type]
        snapshot,  # type: ignore[arg-type]
        view,  # type: ignore[arg-type]
        settings=PROBE_SETTINGS,
        stopped=lambda: None,
    )
    if status is not None or evidence is None:
        raise RouteAwarePlacementBenchmarkError("route probe did not complete deterministically")
    return evidence


def run_benchmark(repetitions: int = 3) -> dict[str, Any]:
    """Return fixed-fixture replay evidence and enforce the predeclared 10% criterion."""

    if not 3 <= repetitions <= 16:
        raise ValueError("repetitions must be between 3 and 16")
    _source, snapshot, view, intent = _fixture()
    baseline_settings = PlacementSolverSettings(**SEARCH_SETTINGS)
    aware_settings = PlacementSolverSettings(
        **SEARCH_SETTINGS,
        scoring_policy=PlacementScoringPolicy.ROUTE_AWARE_ASTAR,
        route_probe_settings=PROBE_SETTINGS,
    )
    signatures: list[tuple[object, ...]] = []
    first_metrics: dict[str, Any] | None = None
    for _ in range(repetitions):
        baseline = solve_placement(intent, snapshot, view, settings=baseline_settings)
        aware = solve_placement(intent, snapshot, view, settings=aware_settings)
        if not baseline.ranked or not aware.ranked:
            raise RouteAwarePlacementBenchmarkError("solver retained no legal candidate")
        baseline_choice = baseline.ranked[0]
        aware_choice = aware.ranked[0]
        baseline_evidence = _evidence(baseline_choice.candidate, snapshot, view)
        aware_evidence = aware_choice.route_evidence
        if aware_evidence is None:
            raise RouteAwarePlacementBenchmarkError("route-aware candidate lacks route evidence")
        legal = all(
            item.candidate.evidence.legality.legal for item in (*baseline.ranked, *aware.ranked)
        )
        if not legal:
            raise RouteAwarePlacementBenchmarkError(
                "a retained candidate escaped placement legality"
            )
        if baseline_evidence.unrouted_probes or aware_evidence.unrouted_probes:
            raise RouteAwarePlacementBenchmarkError("fixture probe unexpectedly became unrouted")
        baseline_length = baseline_evidence.wire_length_nm
        aware_length = aware_evidence.wire_length_nm
        if baseline_length <= 0:
            raise RouteAwarePlacementBenchmarkError("baseline route probe has no routed length")
        improvement_percent = (baseline_length - aware_length) * 100 / baseline_length
        metrics = {
            "all_retained_candidates_legal": legal,
            "baseline_candidate_id": baseline_choice.candidate.candidate_id,
            "baseline_route_wire_length_nm": baseline_length,
            "route_aware_candidate_id": aware_choice.candidate.candidate_id,
            "route_aware_route_wire_length_nm": aware_length,
            "route_aware_completed_probes": aware_evidence.completed_probes,
            "route_aware_unrouted_probes": aware_evidence.unrouted_probes,
            "route_length_improvement_percent": improvement_percent,
            "baseline_solver_status": baseline.status,
            "route_aware_solver_status": aware.status,
            "baseline_evaluations": baseline.evaluations,
            "route_aware_evaluations": aware.evaluations,
            "route_aware_operation_probes_used": aware.route_probes_used,
            "route_aware_operation_probe_limit": aware.route_probe_limit,
        }
        signature = (
            baseline.status,
            aware.status,
            baseline.evaluations,
            aware.evaluations,
            baseline_choice.candidate.candidate_id,
            aware_choice.candidate.candidate_id,
            baseline_length,
            aware_length,
        )
        signatures.append(signature)
        if first_metrics is None:
            first_metrics = metrics
        elif metrics != first_metrics:
            raise RouteAwarePlacementBenchmarkError("deterministic replay metrics diverged")
    assert first_metrics is not None
    if first_metrics["route_length_improvement_percent"] < MINIMUM_IMPROVEMENT_PERCENT:
        raise RouteAwarePlacementBenchmarkError(
            "predeclared route-aware improvement criterion failed"
        )
    return {
        **first_metrics,
        "repetitions": repetitions,
        "deterministic_replays": all(item == signatures[0] for item in signatures[1:]),
        "criterion": {
            "metric": "independent bounded A* routed wire length",
            "minimum_improvement_percent": MINIMUM_IMPROVEMENT_PERCENT,
            "passed": True,
        },
    }


def build_report(repetitions: int = 3) -> dict[str, Any]:
    """Build a canonical, auditable benchmark report without writing it."""

    report: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/route-aware-placement/v1",
        "date_utc": "2026-08-05",
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "fixture_origin": "coppermcp-original",
        "fixture_license_spdx": "Apache-2.0",
        "metrics": run_benchmark(repetitions),
        "not_claimed": [
            "combined-net routing, congestion allocation, overflow, or a whole-board "
            "completion result",
            "KiCad DRC, electrical, timing, signal-integrity, thermal, or fabrication readiness",
            "KiCad file mutation, editor mutation, placement apply, route apply, or "
            "live-editor behavior",
            "placement optimality, a general improvement guarantee, or a comparison with "
            "external routers",
        ],
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    report["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    report = build_report(args.repetitions)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
