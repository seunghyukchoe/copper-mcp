#!/usr/bin/env python3
"""Measure the internal ordered-layer A* oracle against an exact Dijkstra differential.

Two differentials are recorded.  The fixed 5x5 cases are the historical two-layer oracle and are
kept byte-for-byte comparable.  The randomized suite covers the generalized seam this benchmark
exists to defend: two through five ordered layers, full-stack transitions to any other layer,
layer-scoped track and via keepouts, and finite via caps.  Its differential is an exact
``(x, y, layer, vias_used)`` Dijkstra, because a capped search is not a shortest-path problem over
coordinates alone -- a cheaper arrival can have spent the budget a more expensive one still holds.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import platform
import random
import shutil
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from copper_mcp.routing.layered_astar import (
    LayeredAStarRequest,
    LayeredAStarSettings,
    LayeredObstacle,
    LayeredPoint,
    effective_max_vias,
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


def _blocked_cells(obstacles: tuple[LayeredObstacle, ...]) -> set[tuple[int, int, int]]:
    return {
        (x, y, obstacle.layer)
        for obstacle in obstacles
        for x in range(obstacle.min_x, obstacle.max_x + 1)
        for y in range(obstacle.min_y, obstacle.max_y + 1)
    }


def _capped_dijkstra_cost(request: LayeredAStarRequest) -> int | None:
    """Exact `(x, y, layer, vias_used)` differential for the generalized capped lattice.

    This is deliberately an independent implementation rather than a reuse of the search under
    test: it is uniform-cost (no heuristic), it enumerates the augmented state space directly with
    no Pareto pruning, and it re-derives the effective cap from the public policy helper.  A
    dominance key of coordinates alone is unsound here, so the via count is part of the state.
    """

    layers = tuple(sorted(request.layers))
    via_limit = effective_max_vias(request.settings, len(layers))
    if via_limit is None:
        via_limit = (
            len(layers)
            * (request.bounds[2] - request.bounds[0] + 1)
            * (request.bounds[3] - request.bounds[1] + 1)
        )
    blocked = _blocked_cells(request.obstacles)
    via_blocked = _blocked_cells(request.via_obstacles)
    start = (request.start.x, request.start.y, request.start.layer)
    goal = (request.goal.x, request.goal.y, request.goal.layer)
    if start in blocked or goal in blocked:
        return None
    min_x, min_y, max_x, max_y = request.bounds
    move_cost = request.settings.move_cost
    via_cost = request.settings.via_cost
    start_state = (start, 0)
    costs: dict[tuple[tuple[int, int, int], int], int] = {start_state: 0}
    frontier: list[tuple[int, tuple[int, int, int], int]] = [(0, start, 0)]
    while frontier:
        cost, node, used = heapq.heappop(frontier)
        if cost != costs.get((node, used)):
            continue
        if node == goal:
            return cost
        x, y, layer = node
        # A transition is refused when the coordinate lies in a via keepout on ANY layer of the
        # stack, matching the full-stack barrel the adapter records.
        transition_allowed = not any((x, y, other) in via_blocked for other in layers)
        moves: list[tuple[tuple[int, int, int], int]] = [
            ((x + dx, y + dy, layer), move_cost) for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1))
        ]
        transitions: list[tuple[tuple[int, int, int], int]] = (
            [((x, y, other), via_cost) for other in layers if other != layer]
            if transition_allowed
            else []
        )
        for next_node, step in (*moves, *transitions):
            nx, ny, next_layer = next_node
            if not (min_x <= nx <= max_x and min_y <= ny <= max_y) or next_node in blocked:
                continue
            next_used = used + int(next_layer != layer)
            if next_used > via_limit:
                continue
            next_cost = cost + step
            if next_cost < costs.get((next_node, next_used), 1 << 60):
                costs[(next_node, next_used)] = next_cost
                heapq.heappush(frontier, (next_cost, next_node, next_used))
    return None


def _random_case(rng: random.Random, index: int) -> tuple[str, LayeredAStarRequest]:
    """Build one seeded capped multilayer lattice with layer-scoped keepouts."""

    layer_count = rng.randint(2, 5)
    width = rng.randint(3, 6)
    height = rng.randint(3, 6)
    layers = tuple(range(layer_count))

    def _obstacle(max_span_x: int, max_span_y: int) -> LayeredObstacle:
        low_x = rng.randint(0, width)
        low_y = rng.randint(0, height)
        return LayeredObstacle(
            layer=rng.randrange(layer_count),
            min_x=low_x,
            min_y=low_y,
            max_x=min(width, low_x + rng.randint(0, max_span_x)),
            max_y=min(height, low_y + rng.randint(0, max_span_y)),
        )

    def _wall() -> LayeredObstacle:
        """A full-height wall forces the route off its layer, producing deep via chains."""

        column = rng.randint(1, max(1, width - 1))
        return LayeredObstacle(
            layer=rng.randrange(layer_count),
            min_x=column,
            min_y=0,
            max_x=column,
            max_y=height,
        )

    obstacles = (
        *(_wall() for _ in range(rng.randint(0, layer_count + 1))),
        *(_obstacle(2, height) for _ in range(rng.randint(0, 3))),
    )
    via_obstacles = tuple(_obstacle(1, 1) for _ in range(rng.randint(0, 2)))
    # ``None`` exercises the omitted-policy path: unchanged for two layers, the deterministic
    # effective cap from three layers up.
    max_vias = rng.choice((None, 0, 1, 2, 3, 6))
    return (
        f"random_{index:05d}_l{layer_count}_v{'none' if max_vias is None else max_vias}",
        LayeredAStarRequest(
            board_revision="sha256:" + "1" * 64,
            expected_revision="sha256:" + "1" * 64,
            bounds=(0, 0, width, height),
            start=LayeredPoint(0, 0, rng.randrange(layer_count)),
            goal=LayeredPoint(width, height, rng.randrange(layer_count)),
            obstacles=obstacles,
            via_obstacles=via_obstacles,
            layers=layers,
            settings=LayeredAStarSettings(
                move_cost=rng.choice((1, 2)),
                via_cost=rng.choice((1, 3, 7)),
                max_vias=max_vias,
            ),
        ),
    )


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


def _run_randomized(lattices: int, seed: int, replays: int) -> dict[str, Any]:
    """Differential the capped multilayer kernel against the exact augmented Dijkstra oracle.

    Every disagreement is fatal.  The signature digest folds in each lattice's exact outcome, so
    an equal count with different routes cannot be mistaken for an equal result.
    """

    # Deterministic reproducibility, not cryptography: the seed and the resulting outcome
    # signature are both recorded so the suite replays exactly.
    rng = random.Random(seed)  # noqa: S311
    signature = hashlib.sha256()
    matches = 0
    replay_checks = 0
    illegal_paths = 0
    layer_histogram: dict[str, int] = {}
    capped_cases = 0
    routed_cases = 0
    max_via_steps = 0
    for index in range(lattices):
        name, request = _random_case(rng, index)
        result = route_layered(request)
        expected = _capped_dijkstra_cost(request)
        observed = result.cost if result.ok else None
        if observed != expected:
            raise BenchmarkError(f"capped multilayer Dijkstra mismatch for {name}")
        matches += 1
        layers = tuple(sorted(request.layers))
        key = f"{len(layers)}_layers"
        layer_histogram[key] = layer_histogram.get(key, 0) + 1
        if effective_max_vias(request.settings, len(layers)) is not None:
            capped_cases += 1
        if result.ok:
            routed_cases += 1
            if not _path_is_legal(request, result):
                illegal_paths += 1
            max_via_steps = max(max_via_steps, result.metrics.via_steps)
        for _ in range(replays):
            if route_layered(request) != result:
                raise BenchmarkError(f"non-deterministic replay for {name}")
            replay_checks += 1
        signature.update(
            _canonical_bytes(
                {
                    "name": name,
                    "cost": observed,
                    "diagnostic": result.diagnostic.code if result.diagnostic else None,
                    "via_steps": result.metrics.via_steps,
                }
            )
        )
    if illegal_paths:
        raise BenchmarkError("a routed multilayer path violated its own lattice rules")
    if capped_cases == 0 or routed_cases == 0 or max_via_steps == 0:
        raise BenchmarkError("the randomized suite did not exercise capped multilayer transitions")
    return {
        "lattices": lattices,
        "seed": seed,
        "replays_per_lattice": replays,
        "differential_matches": matches,
        "deterministic_replays": replay_checks,
        "illegal_paths": illegal_paths,
        "capped_lattices": capped_cases,
        "routed_lattices": routed_cases,
        "max_via_steps": max_via_steps,
        "layer_histogram": layer_histogram,
        "outcome_signature": "sha256:" + signature.hexdigest(),
    }


def _alternating_via_request(
    via_count: int, *, layers: tuple[int, ...], settings: LayeredAStarSettings
) -> LayeredAStarRequest:
    """A 1xN corridor forcing exactly one transition per two cells."""

    obstacles = [
        LayeredObstacle(index % 2, index * 2 + 1, 0, index * 2 + 1, 0) for index in range(via_count)
    ]
    obstacles.extend(
        LayeredObstacle(layer, x, 0, x, 0) for layer in layers[2:] for x in range(via_count * 2 + 1)
    )
    return LayeredAStarRequest(
        board_revision="sha256:" + "1" * 64,
        expected_revision="sha256:" + "1" * 64,
        bounds=(0, 0, via_count * 2, 0),
        start=LayeredPoint(0, 0, 0),
        goal=LayeredPoint(via_count * 2, 0, via_count % 2),
        obstacles=tuple(obstacles),
        layers=layers,
        settings=settings,
    )


def _run_policy_boundary() -> dict[str, Any]:
    """Pin the declared via policy itself, which a shared-constant differential cannot see.

    ``_capped_dijkstra_cost`` derives its cap from the same public helper as the search, so the two
    agree by construction on whatever the constant happens to be.  These cases fix the constant to
    the number D-137/D-138 and ADR-0066 declare, by observing the routed/refused boundary either
    side of it.
    """

    settings = LayeredAStarSettings(move_cost=1, via_cost=1)
    declared_default = effective_max_vias(settings, 3)
    legacy_two_layer = effective_max_vias(settings, 2)
    at_default = route_layered(_alternating_via_request(64, layers=(0, 1, 2), settings=settings))
    over_default = route_layered(_alternating_via_request(65, layers=(0, 1, 2), settings=settings))
    legacy_over = route_layered(_alternating_via_request(65, layers=(0, 1), settings=settings))
    if declared_default != 64 or legacy_two_layer is not None:
        raise BenchmarkError("the declared ordered-layer via policy changed")
    if not at_default.ok or at_default.metrics.via_steps != 64:
        raise BenchmarkError("an omitted three-layer cap refused its declared 64th via")
    if over_default.ok:
        raise BenchmarkError("an omitted three-layer cap admitted a 65th via")
    if not legacy_over.ok or legacy_over.metrics.via_steps != 65:
        raise BenchmarkError("an omitted two-layer cap stopped being unbounded")
    return {
        "declared_generalized_default": declared_default,
        "legacy_two_layer_default": legacy_two_layer,
        "generalized_at_default_via_steps": at_default.metrics.via_steps,
        "generalized_over_default": over_default.diagnostic.code
        if over_default.diagnostic
        else None,
        "legacy_two_layer_via_steps": legacy_over.metrics.via_steps,
    }


def _path_is_legal(request: LayeredAStarRequest, result: Any) -> bool:
    """Re-check a returned path against the lattice rules, independently of the search."""

    steps = result.path
    if steps is None or steps[0].kind != "start":
        return False
    layers = set(request.layers)
    blocked = _blocked_cells(request.obstacles)
    via_blocked = _blocked_cells(request.via_obstacles)
    min_x, min_y, max_x, max_y = request.bounds
    via_limit = effective_max_vias(request.settings, len(layers))
    if (steps[0].x, steps[0].y, steps[0].layer) != (
        request.start.x,
        request.start.y,
        request.start.layer,
    ) or (steps[-1].x, steps[-1].y, steps[-1].layer) != (
        request.goal.x,
        request.goal.y,
        request.goal.layer,
    ):
        return False
    used_vias = 0
    cost = 0
    for previous, current in pairwise(steps):
        cell = (current.x, current.y, current.layer)
        if current.layer not in layers or cell in blocked:
            return False
        if not (min_x <= current.x <= max_x and min_y <= current.y <= max_y):
            return False
        if current.kind == "via":
            if (previous.x, previous.y) != (current.x, current.y):
                return False
            if any((current.x, current.y, layer) in via_blocked for layer in layers):
                return False
            used_vias += 1
            cost += request.settings.via_cost
        elif current.kind == "move":
            if current.layer != previous.layer:
                return False
            if abs(current.x - previous.x) + abs(current.y - previous.y) != 1:
                return False
            cost += request.settings.move_cost
        else:
            return False
    if via_limit is not None and used_vias > via_limit:
        return False
    return cost == result.cost and used_vias == result.metrics.via_steps


def _run(repetitions: int, *, lattices: int, seed: int, replays: int) -> dict[str, Any]:
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
    randomized = _run_randomized(lattices, seed, replays)
    return {
        "benchmark": "layered-astar-oracle-v2",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "configuration": {
            "script_sha256": hashlib.sha256((ROOT / SCRIPT_PATH).read_bytes()).hexdigest(),
            "repetitions_per_case": repetitions,
            "case_count": len(cases),
            "differential": "dijkstra-v1",
            "fixture_set": "four-fixed-5x5-v1",
            "randomized_differential": "capped-node-vias-dijkstra-v1",
            "randomized_fixture_set": "seeded-2-to-5-layer-capped-lattices-v1",
        },
        "metrics": {
            "case_results": cases,
            "deterministic_replays": deterministic_replays,
            "differential_matches": differential_matches,
            "differential_cases": len(cases),
            "via_required_success": via_required_success,
            "randomized_multilayer": randomized,
            "policy_boundary": _run_policy_boundary(),
        },
        "not_claimed": [
            "Board IR mapping",
            "trace width or clearance",
            "via annulus, drill, keepout, or net-class rules",
            "KiCad serialization or DRC",
            "multi-net congestion, rip-up, or FreeRouting parity",
            "blind, buried, or microvia spans",
            "six through eight layer stacks",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=50)
    parser.add_argument("--lattices", type=int, default=2_000)
    parser.add_argument("--lattice-seed", type=int, default=20260805)
    parser.add_argument("--lattice-replays", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.repetitions <= 50:
        raise SystemExit("repetitions must be between 2 and 50")
    if not 1 <= args.lattices <= 50_000:
        raise SystemExit("lattices must be between 1 and 50000")
    if not 1 <= args.lattice_replays <= 10:
        raise SystemExit("lattice replays must be between 1 and 10")
    if not 0 <= args.lattice_seed <= (1 << 53) - 1:
        raise SystemExit("lattice seed must be a non-negative safe integer")
    document = _run(
        args.repetitions,
        lattices=args.lattices,
        seed=args.lattice_seed,
        replays=args.lattice_replays,
    )
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
