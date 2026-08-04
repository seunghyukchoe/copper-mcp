#!/usr/bin/env python3
"""Measure the internal two-layer A* oracle against a tiny Dijkstra differential."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.routing.layered_astar import (
    LayeredAStarRequest,
    LayeredAStarSettings,
    LayeredObstacle,
    LayeredPoint,
    route_layered,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path("scripts/benchmark_layered_astar.py")


class BenchmarkError(RuntimeError):
    """The layered oracle was not deterministic or disagreed with the differential."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - executable is discovered from PATH
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _request(
    *,
    start: LayeredPoint,
    goal: LayeredPoint,
    obstacles: tuple[LayeredObstacle, ...] = (),
    via_cost: int = 3,
) -> LayeredAStarRequest:
    return LayeredAStarRequest(
        board_revision="sha256:" + "1" * 64,
        expected_revision="sha256:" + "1" * 64,
        bounds=(0, 0, 4, 4),
        start=start,
        goal=goal,
        obstacles=obstacles,
        settings=LayeredAStarSettings(via_cost=via_cost),
    )


def _result_payload(result: Any) -> dict[str, Any]:
    return asdict(result)


def _dijkstra_cost(request: LayeredAStarRequest) -> int | None:
    """Tiny zero-heuristic differential over the same abstract cell semantics."""

    import heapq

    start = (request.start.x, request.start.y, request.start.layer)
    goal = (request.goal.x, request.goal.y, request.goal.layer)
    layers = tuple(sorted(request.layers))
    blocked = {
        (x, y, obstacle.layer)
        for obstacle in request.obstacles
        for x in range(obstacle.min_x, obstacle.max_x + 1)
        for y in range(obstacle.min_y, obstacle.max_y + 1)
    }
    if start in blocked or goal in blocked:
        return None
    costs: dict[tuple[int, int, int], int] = {start: 0}
    frontier = [(0, start)]
    min_x, min_y, max_x, max_y = request.bounds
    while frontier:
        cost, node = heapq.heappop(frontier)
        if cost != costs.get(node):
            continue
        if node == goal:
            return cost
        x, y, layer = node
        neighbors = tuple((x + dx, y + dy, layer) for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)))
        other = layers[1] if layer == layers[0] else layers[0]
        neighbors += ((x, y, other),)
        for next_node in neighbors:
            nx, ny, next_layer = next_node
            if not (min_x <= nx <= max_x and min_y <= ny <= max_y) or next_node in blocked:
                continue
            step = request.settings.via_cost if next_layer != layer else request.settings.move_cost
            next_cost = cost + step
            if next_cost < costs.get(next_node, 1 << 60):
                costs[next_node] = next_cost
                heapq.heappush(frontier, (next_cost, next_node))
    return None


def _cases() -> tuple[tuple[str, LayeredAStarRequest], ...]:
    return (
        (
            "via_required",
            _request(
                start=LayeredPoint(0, 1, 0),
                goal=LayeredPoint(4, 1, 0),
                obstacles=(LayeredObstacle(0, 1, 0, 3, 4),),
                via_cost=2,
            ),
        ),
        (
            "via_cost_choice",
            _request(
                start=LayeredPoint(0, 2, 0),
                goal=LayeredPoint(4, 2, 0),
                obstacles=(LayeredObstacle(0, 1, 1, 3, 3),),
                via_cost=1,
            ),
        ),
        (
            "single_layer_direct",
            _request(start=LayeredPoint(0, 0, 0), goal=LayeredPoint(4, 0, 0)),
        ),
        (
            "both_layers_blocked",
            _request(
                start=LayeredPoint(0, 1, 0),
                goal=LayeredPoint(4, 1, 0),
                obstacles=(
                    LayeredObstacle(0, 1, 0, 3, 4),
                    LayeredObstacle(1, 1, 0, 3, 4),
                ),
            ),
        ),
    )


def _run(repetitions: int) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    deterministic_replays = 0
    differential_matches = 0
    via_required_success = False
    for name, request in _cases():
        first = route_layered(request)
        expected_cost = _dijkstra_cost(request)
        payload = _result_payload(first)
        digest = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        for _ in range(repetitions):
            replay = route_layered(request)
            if replay != first:
                raise BenchmarkError(f"non-deterministic replay for {name}")
            deterministic_replays += 1
        observed_cost = first.cost if first.ok else None
        if observed_cost != expected_cost:
            raise BenchmarkError(f"Dijkstra mismatch for {name}")
        differential_matches += 1
        if name == "via_required":
            via_required_success = first.ok and first.metrics.via_steps == 2
        cases.append(
            {
                "name": name,
                "result_digest": digest,
                "ok": first.ok,
                "diagnostic": first.diagnostic.code if first.diagnostic else None,
                "cost": observed_cost,
                "via_steps": first.metrics.via_steps,
                "expanded_nodes": first.metrics.expanded_nodes,
            }
        )
    if not via_required_success:
        raise BenchmarkError("via-required case did not use two explicit transitions")
    return {
        "benchmark": "layered-astar-oracle-v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "configuration": {
            "script_sha256": hashlib.sha256((ROOT / SCRIPT_PATH).read_bytes()).hexdigest(),
            "repetitions_per_case": repetitions,
            "case_count": len(cases),
            "seeded_differential": "dijkstra-v1",
            "fixture_set": "four-fixed-5x5-v1",
        },
        "metrics": {
            "case_results": cases,
            "deterministic_replays": deterministic_replays,
            "differential_matches": differential_matches,
            "differential_cases": len(cases),
            "via_required_success": via_required_success,
        },
        "not_claimed": [
            "Board IR mapping",
            "trace width or clearance",
            "via annulus, drill, keepout, or net-class rules",
            "KiCad serialization or DRC",
            "multi-net congestion, rip-up, or FreeRouting parity",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=50)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.repetitions <= 50:
        raise SystemExit("repetitions must be between 2 and 50")
    document = _run(args.repetitions)
    document = {
        "run_id": "sha256:" + hashlib.sha256(_canonical_bytes(document)).hexdigest(),
        **document,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical_bytes(document) + b"\n")
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
