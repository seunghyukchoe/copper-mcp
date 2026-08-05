#!/usr/bin/env python3
"""Replay an opt-in route-aware placement ranking on an original audio fixture.

The benchmark records three separate measurements.  They are not interchangeable and the difference
between the first two is the whole point.

1. ``search_comparison`` runs **two different bounded searches** over the same intent, fixture, and
   work ceilings.  The baseline orders its beam by the historical same-net Manhattan proxy; the
   opt-in policy orders its beam by one independent candidate-only A* probe.  Because the score
   feeds ``solver._state_key``, it decides which successors are ever generated, so the two searches
   explore and retain *different* candidate sets - measured on this fixture, the two retained sets
   are entirely disjoint at the committed ``max_ranked``.  This measurement therefore answers "does
   searching under route-aware evidence end somewhere better", not "does re-ranking one shared set
   pick better".
2. ``rerank_comparison`` answers the second question directly, and is the weaker of the two.  One
   fixed candidate set - the union of what both searches retained - is scored under both policies
   and the argmin of each is compared.  Here, and only here, the candidate set really is shared.
3. ``multi_probe_observation`` re-measures the two chosen candidates against *every* probeable net
   on the fixture rather than the single net the ranked search used, so the report cannot imply
   that "zero unrouted probes" was a whole-board statement.

No board derivative, route patch, or placement candidate is applied.  A failed criterion is
recorded as a negative result rather than raised.
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
from copper_mcp.placement.route_scoring import (
    ROUTE_AWARE_ESTIMATOR_ID,
    RouteProbeSettings,
    score_route_aware_candidate,
)
from copper_mcp.placement.solver import (
    PlacementScoringPolicy,
    PlacementSolverSettings,
    score_placement_candidate,
    solve_placement,
)
from copper_mcp.routing.contracts import AStarSettings

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks/audio/fixtures/ne5532-stereo-summing-routing-v1.kicad_pcb"
PROVENANCE = FIXTURE.with_suffix(".provenance.json")
# The v1 artifact is B-078's immutable evidence and is never regenerated.  The corrected
# measurement is a new artifact so the overstated one stays auditable next to it.
DEFAULT_OUTPUT = ROOT / "benchmarks/results/placement/2026-08-06-route-aware-placement-v2.json"
CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}
ASTAR_SETTINGS = AStarSettings(
    grid_step_nm=1_000_000,
    max_grid_nodes=100_000,
    max_expansions=10_000,
    max_obstacles=256,
    max_obstacle_checks=100_000,
)
#: The ranked search probes exactly one net per candidate.  Every number derived from it is a
#: one-net statement and the report says so.
PROBE_SETTINGS = RouteProbeSettings(
    max_probes=1, max_total_probes=128, astar_settings=ASTAR_SETTINGS
)
#: Above the fixture's probeable-net count, so this observes every probeable net rather than a
#: prefix of them.  Used only to re-measure already chosen candidates, never to rank.
MULTI_PROBE_SETTINGS = RouteProbeSettings(
    max_probes=32, max_total_probes=4_096, astar_settings=ASTAR_SETTINGS
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


def _evidence(
    candidate: object, snapshot: object, view: object, settings: RouteProbeSettings = PROBE_SETTINGS
) -> Any:
    evidence, status = score_route_aware_candidate(
        candidate,  # type: ignore[arg-type]
        snapshot,  # type: ignore[arg-type]
        view,  # type: ignore[arg-type]
        settings=settings,
        stopped=lambda: None,
    )
    if status is not None or evidence is None:
        raise RouteAwarePlacementBenchmarkError("route probe did not complete deterministically")
    return evidence


def _probe_observation(candidate: object, snapshot: object, view: object) -> dict[str, Any]:
    """Re-measure one already-chosen candidate against every probeable net on the fixture."""

    evidence = _evidence(candidate, snapshot, view, MULTI_PROBE_SETTINGS)
    return {
        "attempted_probes": evidence.attempted_probes,
        "completed_probes": evidence.completed_probes,
        "unrouted_probes": evidence.unrouted_probes,
        "refused_probes": evidence.refused_probes,
        "wire_length_nm": evidence.wire_length_nm,
    }


def _rerank_comparison(
    candidates: dict[str, Any], snapshot: object, view: object
) -> dict[str, Any]:
    """Apply both scores to one fixed candidate set and compare the two argmins.

    This is the re-ranking measurement.  Unlike ``search_comparison`` the candidate set really is
    shared here, because nothing in this function generates a successor: every candidate was already
    issued by the legalizer during one of the two searches.
    """

    baseline_settings = PlacementSolverSettings(**SEARCH_SETTINGS)
    aware_settings = PlacementSolverSettings(
        **SEARCH_SETTINGS,
        scoring_policy=PlacementScoringPolicy.ROUTE_AWARE_ASTAR,
        route_probe_settings=PROBE_SETTINGS,
    )
    manhattan: list[tuple[Any, str]] = []
    route_aware: list[tuple[Any, str]] = []
    lengths: dict[str, int] = {}
    for candidate_id in sorted(candidates):
        candidate = candidates[candidate_id]
        plain_score, _plain_evidence, plain_status = score_placement_candidate(
            candidate, snapshot, view, settings=baseline_settings
        )
        aware_score, aware_evidence, aware_status = score_placement_candidate(
            candidate, snapshot, view, settings=aware_settings
        )
        if plain_status is not None or aware_status is not None:
            raise RouteAwarePlacementBenchmarkError("re-ranking score did not complete")
        assert plain_score is not None and aware_score is not None and aware_evidence is not None
        manhattan.append((plain_score, candidate_id))
        route_aware.append((aware_score, candidate_id))
        lengths[candidate_id] = aware_evidence.wire_length_nm
    manhattan_choice = min(manhattan)[1]
    route_aware_choice = min(route_aware)[1]
    manhattan_length = lengths[manhattan_choice]
    route_aware_length = lengths[route_aware_choice]
    improvement = (
        (manhattan_length - route_aware_length) * 100 / manhattan_length
        if manhattan_length > 0
        else 0.0
    )
    return {
        "shared_candidate_set_size": len(candidates),
        "manhattan_choice_id": manhattan_choice,
        "manhattan_route_wire_length_nm": manhattan_length,
        "route_aware_choice_id": route_aware_choice,
        "route_aware_route_wire_length_nm": route_aware_length,
        "route_length_improvement_percent": improvement,
        "same_choice": manhattan_choice == route_aware_choice,
    }


def run_benchmark(repetitions: int = 3) -> dict[str, Any]:
    """Return fixed-fixture replay evidence and evaluate the predeclared 10% criterion.

    Harness integrity - fixture provenance, replay determinism, retained-candidate legality - still
    raises, because a broken harness measures nothing.  The predeclared criterion does not: a
    replay that fails it is recorded as a negative result with ``criterion.passed`` false, as
    ADR-0067 promised.
    """

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
        baseline_length = baseline_evidence.wire_length_nm
        aware_length = aware_evidence.wire_length_nm
        if baseline_length <= 0:
            raise RouteAwarePlacementBenchmarkError("baseline route probe has no routed length")
        improvement_percent = (baseline_length - aware_length) * 100 / baseline_length
        retained = {item.candidate.candidate_id for item in baseline.ranked} & {
            item.candidate.candidate_id for item in aware.ranked
        }
        shared = {
            item.candidate.candidate_id: item.candidate
            for item in (*baseline.ranked, *aware.ranked)
        }
        metrics = {
            "all_retained_candidates_legal": legal,
            "estimator_id": aware_evidence.estimator_id,
            "route_probe_settings_digest": aware_evidence.settings_digest,
            "search_comparison": {
                "comparison_kind": (
                    "two different bounded searches over one intent, each ordering its own beam by "
                    "its own score; not one shared candidate set re-ranked"
                ),
                "baseline_candidate_id": baseline_choice.candidate.candidate_id,
                "baseline_route_wire_length_nm": baseline_length,
                "baseline_unrouted_probes": baseline_evidence.unrouted_probes,
                "baseline_refused_probes": baseline_evidence.refused_probes,
                "route_aware_candidate_id": aware_choice.candidate.candidate_id,
                "route_aware_route_wire_length_nm": aware_length,
                "route_length_improvement_percent": improvement_percent,
                "probes_per_candidate": PROBE_SETTINGS.max_probes,
                "route_aware_completed_probes": aware_evidence.completed_probes,
                "route_aware_unrouted_probes": aware_evidence.unrouted_probes,
                "route_aware_refused_probes": aware_evidence.refused_probes,
                "baseline_retained_candidates": len(baseline.ranked),
                "route_aware_retained_candidates": len(aware.ranked),
                "shared_retained_candidates": len(retained),
                "baseline_solver_status": baseline.status,
                "route_aware_solver_status": aware.status,
                "baseline_scoring_policy": str(baseline.scoring_policy),
                "route_aware_scoring_policy": str(aware.scoring_policy),
                "baseline_evaluations": baseline.evaluations,
                "route_aware_evaluations": aware.evaluations,
                "route_aware_operation_probes_used": aware.route_probes_used,
                "route_aware_operation_probe_limit": aware.route_probe_limit,
            },
            "rerank_comparison": _rerank_comparison(shared, snapshot, view),
            "multi_probe_observation": {
                "comparison_kind": (
                    "the two chosen candidates re-measured against every probeable net, so the "
                    "one-probe numbers above are never read as a whole-board result"
                ),
                "baseline_choice": _probe_observation(baseline_choice.candidate, snapshot, view),
                "route_aware_choice": _probe_observation(aware_choice.candidate, snapshot, view),
            },
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
    search = first_metrics["search_comparison"]
    passed = bool(
        search["route_length_improvement_percent"] >= MINIMUM_IMPROVEMENT_PERCENT
        or search["route_aware_unrouted_probes"] < search["baseline_unrouted_probes"]
    )
    return {
        **first_metrics,
        "repetitions": repetitions,
        "deterministic_replays": all(item == signatures[0] for item in signatures[1:]),
        "criterion": {
            "metric": (
                "independent bounded A* routed wire length of the candidate each bounded search "
                "selected, at one probe per candidate"
            ),
            "minimum_improvement_percent": MINIMUM_IMPROVEMENT_PERCENT,
            "passed": passed,
        },
    }


def _configuration() -> dict[str, Any]:
    """Record every setting that can change a recorded number, so ``run_id`` binds to it.

    Without this a one-probe report and an eleven-probe report of the same fixture could carry
    indistinguishable identities.
    """

    return {
        "estimator_id": ROUTE_AWARE_ESTIMATOR_ID,
        "search_settings": dict(SEARCH_SETTINGS),
        "ranking_probe_settings_digest": PROBE_SETTINGS.digest(),
        "ranking_max_probes": PROBE_SETTINGS.max_probes,
        "ranking_max_total_probes": PROBE_SETTINGS.max_total_probes,
        "observation_probe_settings_digest": MULTI_PROBE_SETTINGS.digest(),
        "observation_max_probes": MULTI_PROBE_SETTINGS.max_probes,
        "constraints": dict(CONSTRAINTS),
    }


def build_report(repetitions: int = 3) -> dict[str, Any]:
    """Build a canonical, auditable benchmark report without writing it."""

    report: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/route-aware-placement/v2",
        "date_utc": "2026-08-06",
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "fixture_origin": "coppermcp-original",
        "fixture_license_spdx": "Apache-2.0",
        "configuration": _configuration(),
        "metrics": run_benchmark(repetitions),
        "not_claimed": [
            "combined-net routing, congestion allocation, overflow, or a whole-board "
            "completion result",
            "KiCad DRC, electrical, timing, signal-integrity, thermal, or fabrication readiness",
            "KiCad file mutation, editor mutation, placement apply, route apply, or "
            "live-editor behavior",
            "placement optimality, a general improvement guarantee, or a comparison with "
            "external routers",
            "that the two ranked searches explored one shared candidate set; they did not, and "
            "`rerank_comparison` is the separate measurement that does",
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
    # A negative result is written before it is reported.  The artifact is the record; the exit
    # status only tells a caller which way the predeclared criterion went.
    return 0 if report["metrics"]["criterion"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
