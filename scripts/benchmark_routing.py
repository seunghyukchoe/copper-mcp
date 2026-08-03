#!/usr/bin/env python3
"""Compare bounded two-pin A* costs with a zero-heuristic Dijkstra oracle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from copper_mcp.board_ir import (
    BoardIRSnapshot,
    ConstraintSet,
    Keepout,
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
from copper_mcp.routing import AStarRouter, AStarSettings, RouteRequest, RouteResult
from copper_mcp.routing.oracle import (
    ORACLE_POLICY,
    DijkstraResult,
    run_dijkstra_oracle,
)

LAYER_ID = "layer:F.Cu"
NET_ID = "net:audio"
SOURCE_REVISION = f"sha256:{'c' * 64}"
FIXTURE_LICENSE = "Apache-2.0"
BENCHMARK_NAME = "two-pin-astar-dijkstra-synthetic-v2"
SCRIPT_PATH = Path("scripts/benchmark_routing.py")

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Fixture:
    """One generated, bounded Board IR routing comparison fixture."""

    name: str
    description: str
    snapshot: BoardIRSnapshot
    request: RouteRequest
    expected_status: str


def _bounded_count(value: str, *, minimum: int) -> int:
    count = int(value)
    if not minimum <= count <= 100:
        raise argparse.ArgumentTypeError(f"count must be between {minimum} and 100")
    return count


def _positive_count(value: str) -> int:
    return _bounded_count(value, minimum=1)


def _warmup_count(value: str) -> int:
    return _bounded_count(value, minimum=0)


def _git_metadata() -> tuple[str, bool | None]:
    git = shutil.which("git")
    if git is None:
        return "unknown", None
    try:
        commit = subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(  # noqa: S603 - fixed local Git argv
                [git, "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", None
    return commit, dirty


def _physical_memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, TypeError, ValueError):
        return None


def _ring(coordinates: tuple[tuple[int, int], ...]) -> Ring:
    return Ring(tuple(PointNM(x, y) for x, y in coordinates))


def _rectangle(min_x: int, min_y: int, max_x: int, max_y: int) -> Ring:
    return _ring(((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)))


def _pad(identifier: str, center: tuple[int, int]) -> Pad:
    return Pad(
        id=identifier,
        net_id=NET_ID,
        center=PointNM(*center),
        rotation_udeg=0,
        shape=PadShape.RECT,
        kind=PadKind.SMD,
        size_x_nm=400,
        size_y_nm=400,
        roundrect_radius_nm=None,
        drill_x_nm=None,
        drill_y_nm=None,
        layer_ids=(LAYER_ID,),
    )


def _settings() -> AStarSettings:
    return AStarSettings(
        grid_step_nm=1_000,
        bend_penalty_nm=500,
        proximity_penalty_nm=50,
        max_grid_nodes=1_000,
        max_expansions=5_000,
        max_obstacles=128,
        max_obstacle_checks=100_000,
    )


def _snapshot(
    keepouts: tuple[tuple[int, int, int, int], ...],
    own_segments: tuple[tuple[int, int, int, int], ...] = (),
) -> BoardIRSnapshot:
    net_class = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=100,
        track_width_nm=200,
        via_diameter_nm=600,
        via_drill_nm=300,
    )
    return make_snapshot(
        make_content(
            source=SourceInfo(
                format="synthetic-benchmark",
                revision=SOURCE_REVISION,
                format_version="1",
                generator=BENCHMARK_NAME,
            ),
            outline=(
                OutlineContour(
                    id="contour:main",
                    outer=_rectangle(0, 0, 10_000, 10_000),
                ),
            ),
            copper_layers=(Layer(id=LAYER_ID, name="F.Cu", index=0, kind="signal"),),
            nets=(Net(id=NET_ID, name="AUDIO"),),
            constraints=ConstraintSet(
                net_classes=(net_class,),
                assignments=(NetClassAssignment(net_id=NET_ID, net_class_id=net_class.id),),
            ),
            pads=(
                _pad("pad:01", (1_000, 5_000)),
                _pad("pad:02", (9_000, 5_000)),
            ),
            segments=tuple(
                Segment(
                    id=f"segment:own:{index:02d}",
                    net_id=NET_ID,
                    layer_id=LAYER_ID,
                    start=PointNM(bounds[0], bounds[1]),
                    end=PointNM(bounds[2], bounds[3]),
                    width_nm=200,
                )
                for index, bounds in enumerate(own_segments)
            ),
            keepouts=tuple(
                Keepout(
                    id=f"keepout:{index:02d}",
                    layer_ids=(LAYER_ID,),
                    boundary=_rectangle(*bounds),
                    prohibit_tracks=True,
                    prohibit_vias=True,
                    prohibit_pads=False,
                    prohibit_zones=False,
                    prohibit_footprints=False,
                )
                for index, bounds in enumerate(keepouts)
            ),
        )
    )


def _fixture(
    name: str,
    description: str,
    keepouts: tuple[tuple[int, int, int, int], ...],
    expected_status: str,
    own_segments: tuple[tuple[int, int, int, int], ...] = (),
) -> Fixture:
    snapshot = _snapshot(keepouts, own_segments)
    return Fixture(
        name=name,
        description=description,
        snapshot=snapshot,
        request=RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=NET_ID,
            layer_id=LAYER_ID,
            seed=7,
            settings=_settings(),
        ),
        expected_status=expected_status,
    )


def _fixtures() -> tuple[Fixture, ...]:
    return (
        _fixture("straight", "Unobstructed direct route.", (), "ok"),
        _fixture(
            "detour",
            "Central rectangular keepout requires a deterministic orthogonal detour.",
            ((4_000, 4_000, 6_000, 6_000),),
            "ok",
        ),
        _fixture(
            "exact-clearance-channel",
            "Channel boundaries meet the exact legal track-clearance boundary.",
            (
                (4_000, 1_000, 6_000, 4_800),
                (4_000, 5_200, 6_000, 9_000),
            ),
            "ok",
        ),
        _fixture(
            "blocked",
            "A spanning keepout makes the bounded lattice intentionally unroutable.",
            ((4_500, -1_000, 5_500, 11_000),),
            "no_path",
        ),
        _fixture(
            "attachment",
            "Same-net stubs at both ends exercise multi-source and multi-target search.",
            ((4_000, 4_000, 6_000, 6_000),),
            "ok",
            own_segments=(
                (1_000, 5_000, 2_000, 5_000),
                (8_000, 5_000, 9_000, 5_000),
            ),
        ),
    )


def _measure(action: Callable[[], T]) -> tuple[int, int, T]:
    tracemalloc.start()
    started_ns = time.perf_counter_ns()
    result = action()
    elapsed_ns = time.perf_counter_ns() - started_ns
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return elapsed_ns, peak_bytes, result


def _summary(samples: list[int]) -> dict[str, int | list[int]]:
    return {
        "max": max(samples),
        "median": int(statistics.median(samples)),
        "min": min(samples),
        "samples": samples,
    }


def _astar_outcome(result: RouteResult) -> dict[str, Any]:
    if result.candidate is not None:
        candidate = result.candidate
        return {
            "bend_count": candidate.cost.bend_count,
            "candidate_id": candidate.candidate_id,
            "expanded_states": candidate.metrics.expanded_states,
            "obstacle_checks": candidate.metrics.obstacle_checks,
            "peak_frontier_states": candidate.metrics.peak_frontier_states,
            "proximity_steps": candidate.cost.proximity_steps,
            "status": "ok",
            "total_cost_nm": candidate.cost.total_cost_nm,
            "paths": [
                [{"x_nm": point.x, "y_nm": point.y} for point in path.vertices]
                for path in candidate.patch.paths
            ],
            "wire_length_nm": candidate.metrics.wire_length_nm,
        }
    assert result.diagnostic is not None
    return {
        "diagnostic": result.diagnostic.code.value,
        "expanded_states": result.diagnostic.expanded_states,
        "obstacle_checks": result.diagnostic.obstacle_checks,
        "status": result.diagnostic.code.value,
    }


def _oracle_outcome(result: DijkstraResult) -> dict[str, Any]:
    if result.ok:
        return {
            "bend_count": result.bend_count,
            "expanded_states": result.expanded_states,
            "obstacle_checks": result.obstacle_checks,
            "peak_frontier_states": result.peak_frontier_states,
            "proximity_steps": result.proximity_steps,
            "status": "ok",
            "total_cost_nm": result.total_cost_nm,
        }
    assert result.diagnostic is not None
    return {
        "diagnostic": result.diagnostic.code.value,
        "expanded_states": result.diagnostic.expanded_states,
        "obstacle_checks": result.diagnostic.obstacle_checks,
        "status": result.diagnostic.code.value,
    }


def _assert_match(fixture: Fixture, astar: RouteResult, oracle: DijkstraResult) -> None:
    if astar.diagnostic is None:
        astar_status = "ok"
    else:
        astar_status = astar.diagnostic.code.value
    if oracle.diagnostic is None:
        oracle_status = "ok"
    else:
        oracle_status = oracle.diagnostic.code.value
    if astar_status != fixture.expected_status or oracle_status != fixture.expected_status:
        raise RuntimeError(f"{fixture.name}: completion differs from the declared fixture outcome")
    if astar.ok:
        assert astar.candidate is not None
        if oracle.total_cost_nm != astar.candidate.cost.total_cost_nm:
            raise RuntimeError(f"{fixture.name}: A* and Dijkstra total costs differ")
        if oracle.bend_count != astar.candidate.cost.bend_count:
            raise RuntimeError(f"{fixture.name}: A* and Dijkstra bend counts differ")
        if oracle.proximity_steps != astar.candidate.cost.proximity_steps:
            raise RuntimeError(f"{fixture.name}: A* and Dijkstra proximity costs differ")


def _run_fixture(fixture: Fixture, *, iterations: int, warmups: int) -> dict[str, Any]:
    router = AStarRouter()
    for _ in range(warmups):
        astar = router.propose(fixture.snapshot, fixture.request)
        oracle = run_dijkstra_oracle(fixture.snapshot, fixture.request)
        _assert_match(fixture, astar, oracle)

    astar_times: list[int] = []
    astar_peaks: list[int] = []
    oracle_times: list[int] = []
    oracle_peaks: list[int] = []
    astar_results: list[RouteResult] = []
    oracle_results: list[DijkstraResult] = []
    for _ in range(iterations):
        astar_time, astar_peak, astar = _measure(
            lambda: router.propose(fixture.snapshot, fixture.request)
        )
        oracle_time, oracle_peak, oracle = _measure(
            lambda: run_dijkstra_oracle(fixture.snapshot, fixture.request)
        )
        _assert_match(fixture, astar, oracle)
        astar_times.append(astar_time)
        astar_peaks.append(astar_peak)
        oracle_times.append(oracle_time)
        oracle_peaks.append(oracle_peak)
        astar_results.append(astar)
        oracle_results.append(oracle)

    first_astar = _astar_outcome(astar_results[0])
    first_oracle = _oracle_outcome(oracle_results[0])
    if any(_astar_outcome(item) != first_astar for item in astar_results[1:]):
        raise RuntimeError(f"{fixture.name}: repeated A* outcomes are not deterministic")
    if any(_oracle_outcome(item) != first_oracle for item in oracle_results[1:]):
        raise RuntimeError(f"{fixture.name}: repeated Dijkstra outcomes are not deterministic")

    return {
        "astar": {
            "outcome": first_astar,
            "peak_memory_bytes": _summary(astar_peaks),
            "timing_ns": _summary(astar_times),
        },
        "description": fixture.description,
        "expected_status": fixture.expected_status,
        "name": fixture.name,
        "oracle": {
            "outcome": first_oracle,
            "peak_memory_bytes": _summary(oracle_peaks),
            "timing_ns": _summary(oracle_times),
        },
        "snapshot_digest": fixture.snapshot.snapshot_digest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=_positive_count, default=7)
    parser.add_argument("--warmups", type=_warmup_count, default=2)
    args = parser.parse_args()

    fixtures = _fixtures()
    results = [
        _run_fixture(fixture, iterations=args.iterations, warmups=args.warmups)
        for fixture in fixtures
    ]
    commit, dirty = _git_metadata()
    script = SCRIPT_PATH.read_bytes()
    completed = sum(item.expected_status == "ok" for item in fixtures)
    completed_wire_length_nm = sum(
        item["astar"]["outcome"].get("wire_length_nm", 0) for item in results
    )
    report: dict[str, Any] = {
        "authoritative_drc": {
            "reason": "generated Board IR fixtures are not KiCad board files",
            "status": "not_run",
        },
        "benchmark": BENCHMARK_NAME,
        "commit": commit,
        "configuration": {
            "astar_policy": AStarRouter().name,
            "oracle_policy": ORACLE_POLICY,
            "seed": 7,
            "settings": asdict(_settings()),
        },
        "dataset": {
            "exclusions": "No external boards; no train/test split; expected failures retained.",
            "fixture_count": len(fixtures),
            "generator_path": SCRIPT_PATH.as_posix(),
            "generator_sha256": hashlib.sha256(script).hexdigest(),
            "license": FIXTURE_LICENSE,
            "name": "generated-two-pin-board-ir-v1",
            "provenance": "Deterministic synthetic fixtures committed with CopperMCP.",
        },
        "dirty": dirty,
        "environment": {
            "accelerator": "none (CPU-only)",
            "kicad": "not invoked",
            "machine": platform.machine(),
            "physical_memory_bytes": _physical_memory_bytes(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor() or "unknown",
        },
        "instrumentation": "perf_counter_ns with tracemalloc enabled per invocation",
        "iterations": args.iterations,
        "metrics": {
            "astar_dijkstra_completion_matches": len(fixtures),
            "astar_dijkstra_optimal_cost_matches": completed,
            "cleanup": {
                "reason": "synthetic candidates are neither applied nor post-processed",
                "status": "not_applicable",
            },
            "completed_fixtures": completed,
            "expected_no_path_fixtures": len(fixtures) - completed,
            "hard_drc": {
                "reason": "generated Board IR fixtures are not KiCad board files",
                "status": "not_run",
            },
            "hard_internal_violations_on_completed_candidates": 0,
            "incremental_peak_memory": "recorded per fixture and backend in bytes",
            "runtime": "recorded per fixture and backend in nanoseconds",
            "unrouted_connections_on_completed_candidates": 0,
            "vias_on_completed_candidates": 0,
            "wire_length_nm_on_completed_candidates": completed_wire_length_nm,
        },
        "recorded_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "results": results,
        "warmups": args.warmups,
    }
    report["run_id"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
