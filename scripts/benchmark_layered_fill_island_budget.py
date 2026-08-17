#!/usr/bin/env python3
"""Calibrate the layered verified-fill per-island boundary including identity hashing."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

from copper_mcp.board_ir import PointNM
from copper_mcp.routing import LayeredBoardRouter, LayeredRouteFailureCode, VerifiedFill
from copper_mcp.routing import layered_board_adapter as adapter
from scripts.benchmark_layered_fill_obstacles import (
    LAYER_ID,
    OTHER_NET_ID,
    SOURCE_REVISION,
    _request,
    _snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts/benchmark_layered_fill_island_budget.py")
OUTPUT = ROOT / "benchmarks/results/routing/2026-08-17-layered-fill-island-budget-v1.json"
SCHEMA = "copper-mcp/benchmark/layered-fill-island-budget/v1"
BOUND_IMPLEMENTATION_FILES = (
    "src/copper_mcp/routing/layered_board_adapter.py",
    "src/copper_mcp/routing/astar.py",
    "src/copper_mcp/routing/layered_astar.py",
)
PROBE_CAP = 1_000_000
SELECTED_CAP = 500_000
AGGREGATE_CAP = 10_000_000
CASE_SPECS = (
    ("old_boundary", 4_096, 1, PROBE_CAP),
    ("widest_recorded_corpus_island", 43_889, 1, PROBE_CAP),
    ("shipped_fill_default", 500_000, 1, PROBE_CAP),
    ("fill_domain_ceiling", 1_000_000, 1, PROBE_CAP),
    ("equal_total_split", 100_000, 10, PROBE_CAP),
    ("selected_cap_overflow", 500_001, 1, SELECTED_CAP),
    ("aggregate_overflow", 1_000_000, 11, PROBE_CAP),
)
WIDEST_CASE_SECONDS = 5.0
WIDEST_CASE_TRACED_BYTES = 256_000_000
SELECTED_CASE_SECONDS = 12.0
SELECTED_CASE_TRACED_BYTES = 512_000_000
DOMAIN_CASE_SECONDS = 20.0
DOMAIN_CASE_TRACED_BYTES = 1_500_000_000


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical_digest(value: object) -> str:
    return _digest_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )


def _points(size: int, *, offset: int) -> tuple[PointNM, ...]:
    if size < 4:
        raise ValueError("a calibration island needs at least four points")
    corners = (
        PointNM(3_000, 6_000 + offset),
        PointNM(7_000, 6_000 + offset),
        PointNM(7_000, 7_000 - offset),
        PointNM(3_000, 7_000 - offset),
    )
    return corners + (corners[0],) * (size - len(corners))


def _islands(size: int, count: int) -> tuple[VerifiedFill, ...]:
    if count == 11:
        # This case exercises the O(islands) aggregate preflight without allocating 11M points.
        island = VerifiedFill(
            net_id=OTHER_NET_ID,
            layer_id=LAYER_ID,
            points=_points(size, offset=0),
            source_revision=SOURCE_REVISION,
        )
        return (island,) * count
    return tuple(
        VerifiedFill(
            net_id=OTHER_NET_ID,
            layer_id=LAYER_ID,
            points=_points(size, offset=index % 2),
            source_revision=SOURCE_REVISION,
        )
        for index in range(count)
    )


def _status(result: object) -> tuple[str, str | None, str | None, int | None]:
    candidate = getattr(result, "candidate", None)
    diagnostic = getattr(result, "diagnostic", None)
    if candidate is not None:
        return "accepted", None, candidate.candidate_id, candidate.metrics.obstacle_checks
    if diagnostic is None:
        raise RuntimeError("layered result has neither candidate nor diagnostic")
    return "refused", diagnostic.code.value, None, None


def _worker(name: str, size: int, count: int, counterfactual_cap: int) -> dict[str, Any]:
    original_cap = adapter._MAX_FILL_VERTICES
    adapter._MAX_FILL_VERTICES = counterfactual_cap
    try:
        snapshot = _snapshot()
        fill = _islands(size, count)
        request = _request(snapshot, fill)
        router = LayeredBoardRouter()
        tracemalloc.start()
        baseline, _ = tracemalloc.get_traced_memory()
        started = time.perf_counter_ns()
        proposed = router.propose(snapshot, request)
        proposed_ns = time.perf_counter_ns() - started
        status, code, candidate_id, obstacle_checks = _status(proposed)
        replay_ns: int | None = None
        replay_status: str | None = None
        replay_code: str | None = None
        replay_identity_matches: bool | None = None
        if proposed.candidate is not None:
            started = time.perf_counter_ns()
            replayed = router.replay(snapshot, proposed.candidate, request)
            replay_ns = time.perf_counter_ns() - started
            replay_status, replay_code, replay_id, _ = _status(replayed)
            replay_identity_matches = replay_id == candidate_id
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return {
            "name": name,
            "vertices_per_island": size,
            "island_count": count,
            "total_vertices": size * count,
            "propose_status": status,
            "propose_code": code,
            "propose_ns": proposed_ns,
            "replay_status": replay_status,
            "replay_code": replay_code,
            "replay_ns": replay_ns,
            "replay_identity_matches": replay_identity_matches,
            "obstacle_checks": obstacle_checks,
            "incremental_traced_peak_bytes": peak - baseline,
            "process_max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "source_per_island_cap": original_cap,
            "counterfactual_per_island_cap": counterfactual_cap,
        }
    finally:
        adapter._MAX_FILL_VERTICES = original_cap


def _run_case(name: str, size: int, count: int, cap: int) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "scripts.benchmark_layered_fill_island_budget",
        "--worker",
        name,
        str(size),
        str(count),
        str(cap),
    ]
    completed = subprocess.run(  # noqa: S603 - fixed interpreter/module argv
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise RuntimeError("worker did not return an object")
    return result


def run_benchmark() -> dict[str, Any]:
    cases = [_run_case(*spec) for spec in CASE_SPECS]
    by_name = {case["name"]: case for case in cases}
    for name in (
        "old_boundary",
        "widest_recorded_corpus_island",
        "shipped_fill_default",
        "fill_domain_ceiling",
        "equal_total_split",
    ):
        case = by_name[name]
        elapsed = case["propose_ns"] + (case["replay_ns"] or 0)
        if case["propose_status"] != "accepted" or case["replay_status"] != "accepted":
            raise RuntimeError(f"predeclared accepted case refused: {name}")
        if case["replay_identity_matches"] is not True:
            raise RuntimeError(f"candidate identity drifted on replay: {name}")
        case["propose_plus_replay_ns"] = elapsed
    widest_gate = (
        by_name["widest_recorded_corpus_island"]["propose_plus_replay_ns"]
        <= int(WIDEST_CASE_SECONDS * 1_000_000_000)
        and by_name["widest_recorded_corpus_island"]["incremental_traced_peak_bytes"]
        <= WIDEST_CASE_TRACED_BYTES
    )
    selected_gate = (
        by_name["shipped_fill_default"]["propose_plus_replay_ns"]
        <= int(SELECTED_CASE_SECONDS * 1_000_000_000)
        and by_name["shipped_fill_default"]["incremental_traced_peak_bytes"]
        <= SELECTED_CASE_TRACED_BYTES
    )
    domain_gate = all(
        by_name[name]["propose_plus_replay_ns"] <= int(DOMAIN_CASE_SECONDS * 1_000_000_000)
        and by_name[name]["incremental_traced_peak_bytes"] <= DOMAIN_CASE_TRACED_BYTES
        for name in ("fill_domain_ceiling", "equal_total_split")
    )
    if not widest_gate or not selected_gate:
        raise RuntimeError("the 500,000-vertex fallback failed its predeclared resource gate")
    if domain_gate:
        raise RuntimeError("the 1,000,000-vertex gate unexpectedly passed; revisit the selection")
    if by_name["selected_cap_overflow"]["propose_code"] != "invalid_request":
        raise RuntimeError("per-island overflow did not remain a typed invalid request")
    if (
        by_name["aggregate_overflow"]["propose_code"]
        != LayeredRouteFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED.value
    ):
        raise RuntimeError("aggregate overflow did not retain its budget refusal")
    return {
        "predeclared_limits": {
            "widest_case_seconds": WIDEST_CASE_SECONDS,
            "widest_case_traced_bytes": WIDEST_CASE_TRACED_BYTES,
            "selected_case_seconds": SELECTED_CASE_SECONDS,
            "selected_case_traced_bytes": SELECTED_CASE_TRACED_BYTES,
            "domain_case_seconds": DOMAIN_CASE_SECONDS,
            "domain_case_traced_bytes": DOMAIN_CASE_TRACED_BYTES,
        },
        "gates": {
            "widest_recorded_corpus_island": widest_gate,
            "shipped_fill_default": selected_gate,
            "fill_domain_ceiling_single_and_split": domain_gate,
        },
        "source_per_island_cap": adapter._MAX_FILL_VERTICES,
        "counterfactual_per_island_cap": PROBE_CAP,
        "selected_per_island_cap": SELECTED_CAP,
        "selection_reason": "the 1,000,000-vertex split-island case exceeded the 20 second gate",
        "aggregate_cap": AGGREGATE_CAP,
        "cases": cases,
    }


def build_report() -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "date_utc": "2026-08-17",
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "configuration": {
            "script": str(SCRIPT),
            "script_sha256": _digest_bytes((ROOT / SCRIPT).read_bytes()),
            "fixture_script_sha256": _digest_bytes(
                (ROOT / "scripts/benchmark_layered_fill_obstacles.py").read_bytes()
            ),
            "implementation_sha256": {
                name: _digest_bytes((ROOT / name).read_bytes())
                for name in BOUND_IMPLEMENTATION_FILES
            },
            "kicad_invoked": False,
            "network_invoked": False,
        },
        "metrics": run_benchmark(),
        "claims": {
            "route_quality": "not_measured",
            "physical_validation": "not_run",
            "cross_machine_performance": "not_claimed",
        },
    }
    report["run_id"] = _canonical_digest(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=4, metavar=("NAME", "SIZE", "COUNT", "CAP"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.worker is not None:
        name, size, count, cap = args.worker
        print(json.dumps(_worker(name, int(size), int(count), int(cap)), sort_keys=True))
        return
    report = build_report()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
