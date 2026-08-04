"""Internal, bounded A* search over a two-layer integer lattice.

This module deliberately does not produce :mod:`copper_mcp.routing.contracts` objects.  It is a
small algorithmic seam for evaluating multilayer search before it is wired to board geometry or a
public MCP contract.  All state is integer ``(x, y, layer)`` coordinates and all outcomes are
immutable, deterministic and fail closed.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import TypeAlias, cast

_Node: TypeAlias = tuple[int, int, int]
_Bounds: TypeAlias = tuple[int, int, int, int]
CancellationCheck: TypeAlias = Callable[[], bool]

_CARDINALS: tuple[tuple[int, int], ...] = (
    (1, 0),
    (0, 1),
    (-1, 0),
    (0, -1),
)
_MAX_COST = 1_000_000_000
_MAX_EXPANSIONS = 1_000_000
_MAX_NODES = 500_000
_MAX_OBSTACLES = 4_096
_MAX_OBSTACLE_CHECKS = 10_000_000


@dataclass(frozen=True, slots=True)
class LayeredPoint:
    """One integer lattice coordinate and its signal layer."""

    x: int
    y: int
    layer: int


@dataclass(frozen=True, slots=True)
class LayeredObstacle:
    """An inclusive, axis-aligned rectangular obstacle on one signal layer."""

    layer: int
    min_x: int
    min_y: int
    max_x: int
    max_y: int


@dataclass(frozen=True, slots=True)
class LayeredAStarSettings:
    """Integer costs and hard resource limits for one bounded search.

    ``max_nodes`` caps states actually discovered by the lazy search; the full lattice is never
    materialized or multiplied into an unbounded allocation.
    """

    move_cost: int = 1
    via_cost: int = 10
    max_expansions: int = 100_000
    max_nodes: int = 250_000
    max_obstacles: int = 256
    max_obstacle_checks: int = 2_000_000


@dataclass(frozen=True, slots=True)
class LayeredAStarRequest:
    """A two-signal-layer search request against one caller-owned revision.

    ``expected_revision`` is optional so callers can use the algorithm as a pure planner.  When
    present, it is an optimistic concurrency precondition and a mismatch returns ``STALE_REVISION``
    without expanding any state.
    """

    board_revision: str
    bounds: _Bounds
    start: LayeredPoint
    goal: LayeredPoint
    obstacles: tuple[LayeredObstacle, ...] = ()
    layers: tuple[int, int] = (0, 1)
    expected_revision: str | None = None
    settings: LayeredAStarSettings = LayeredAStarSettings()

    @property
    def revision(self) -> str:
        """Compatibility alias for algorithm callers that call this a revision."""

        return self.board_revision

    @property
    def expected_board_revision(self) -> str | None:
        """Compatibility alias for adapters that name the precondition explicitly."""

        return self.expected_revision


class LayeredFailureCode(StrEnum):
    """Stable, non-throwing failure taxonomy for the internal search."""

    INVALID_REQUEST = "invalid_request"
    STALE_REVISION = "stale_revision"
    GRID_BUDGET_EXCEEDED = "grid_budget_exceeded"
    OBSTACLE_BUDGET_EXCEEDED = "obstacle_budget_exceeded"
    SEARCH_BUDGET_EXCEEDED = "search_budget_exceeded"
    CANCELLED = "cancelled"
    NO_PATH = "no_path"


@dataclass(frozen=True, slots=True)
class LayeredStep:
    """One path state; ``kind`` is ``start``, ``move`` or an explicit ``via`` transition."""

    x: int
    y: int
    layer: int
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in {"start", "move", "via"}:
            raise ValueError("layered step kind is unsupported")

    @property
    def action(self) -> str:
        """Alias for consumers that call the step kind an action."""

        return self.kind


@dataclass(frozen=True, slots=True)
class LayeredAStarMetrics:
    """Deterministic search metrics, with no wall-clock or host-specific telemetry."""

    expanded_nodes: int = 0
    discovered_nodes: int = 0
    peak_frontier_nodes: int = 0
    obstacle_checks: int = 0
    move_steps: int = 0
    via_steps: int = 0
    path_cost: int = 0

    def __post_init__(self) -> None:
        for value in (
            self.expanded_nodes,
            self.discovered_nodes,
            self.peak_frontier_nodes,
            self.obstacle_checks,
            self.move_steps,
            self.via_steps,
            self.path_cost,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("layered metrics must be non-negative integers")

    @property
    def expanded_states(self) -> int:
        """Alias matching the existing single-layer router's terminology."""

        return self.expanded_nodes

    @property
    def via_count(self) -> int:
        """Return the number of explicit layer transitions in the path."""

        return self.via_steps


@dataclass(frozen=True, slots=True)
class LayeredDiagnostic:
    """A bounded, non-echoing explanation for a failed search."""

    code: LayeredFailureCode
    message: str
    expanded_nodes: int = 0
    discovered_nodes: int = 0
    obstacle_checks: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.code, LayeredFailureCode):
            raise ValueError("layered diagnostic code is unsupported")
        if not isinstance(self.message, str) or not 1 <= len(self.message) <= 256:
            raise ValueError("layered diagnostic message is malformed")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (self.expanded_nodes, self.discovered_nodes, self.obstacle_checks)
        ):
            raise ValueError("layered diagnostic metrics must be non-negative integers")


@dataclass(frozen=True, slots=True)
class LayeredAStarResult:
    """Exactly one path or one diagnostic, always accompanied by deterministic metrics."""

    path: tuple[LayeredStep, ...] | None
    metrics: LayeredAStarMetrics
    diagnostic: LayeredDiagnostic | None = None

    def __post_init__(self) -> None:
        if self.metrics.__class__ is not LayeredAStarMetrics:
            raise ValueError("layered result metrics are malformed")
        if (self.path is None) == (self.diagnostic is None):
            raise ValueError("layered result must contain exactly one path or diagnostic")
        if self.path is not None:
            if not isinstance(self.path, tuple) or not self.path:
                raise ValueError("layered result path must be a non-empty tuple")
            if not all(isinstance(step, LayeredStep) for step in self.path):
                raise ValueError("layered result path contains a malformed step")
            if self.path[0].kind != "start":
                raise ValueError("layered result path must begin with a start step")

    @property
    def ok(self) -> bool:
        """Return true only when a complete path was found."""

        return self.path is not None and self.diagnostic is None

    @property
    def steps(self) -> tuple[LayeredStep, ...] | None:
        """Alias for callers that describe the path as a sequence of steps."""

        return self.path

    @property
    def cost(self) -> int | None:
        """Return the exact integer route cost when a path exists."""

        return self.metrics.path_cost if self.path is not None else None


@dataclass(slots=True)
class _Work:
    expanded_nodes: int = 0
    discovered_nodes: int = 0
    peak_frontier_nodes: int = 0
    obstacle_checks: int = 0


def _invalid(message: str) -> LayeredAStarResult:
    return LayeredAStarResult(
        path=None,
        metrics=LayeredAStarMetrics(),
        diagnostic=LayeredDiagnostic(LayeredFailureCode.INVALID_REQUEST, message),
    )


def _failure(code: LayeredFailureCode, message: str, work: _Work) -> LayeredAStarResult:
    return LayeredAStarResult(
        path=None,
        metrics=LayeredAStarMetrics(
            expanded_nodes=work.expanded_nodes,
            discovered_nodes=work.discovered_nodes,
            peak_frontier_nodes=work.peak_frontier_nodes,
            obstacle_checks=work.obstacle_checks,
        ),
        diagnostic=LayeredDiagnostic(
            code,
            message,
            expanded_nodes=work.expanded_nodes,
            discovered_nodes=work.discovered_nodes,
            obstacle_checks=work.obstacle_checks,
        ),
    )


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(request: object) -> tuple[LayeredAStarRequest, tuple[int, int], _Bounds] | str:
    if not isinstance(request, LayeredAStarRequest):
        return "request must be a LayeredAStarRequest"
    if not isinstance(request.board_revision, str) or not 1 <= len(request.board_revision) <= 256:
        return "board revision is malformed"
    if request.expected_revision is not None and (
        not isinstance(request.expected_revision, str)
        or not 1 <= len(request.expected_revision) <= 256
    ):
        return "expected revision is malformed"
    if request.expected_revision is not None and (
        request.expected_revision != request.board_revision
    ):
        return "board revision does not match the expected revision"
    if (
        not isinstance(request.bounds, tuple)
        or len(request.bounds) != 4
        or not all(_integer(value) for value in request.bounds)
    ):
        return "bounds must be an integer (min_x, min_y, max_x, max_y) tuple"
    min_x, min_y, max_x, max_y = request.bounds
    if min_x > max_x or min_y > max_y:
        return "bounds must be ordered"
    if (
        not isinstance(request.layers, tuple)
        or len(request.layers) != 2
        or not all(_integer(layer) for layer in request.layers)
    ):
        return "exactly two integer signal layers are required"
    if request.layers[0] == request.layers[1]:
        return "signal layers must be distinct"
    layers = cast(tuple[int, int], tuple(sorted(request.layers)))
    start_obj: object = request.start
    goal_obj: object = request.goal
    if not isinstance(start_obj, LayeredPoint) or not isinstance(goal_obj, LayeredPoint):
        return "start and goal must be LayeredPoint values"
    start = start_obj
    goal = goal_obj
    for name, point in (("start", start), ("goal", goal)):
        if not all(_integer(value) for value in (point.x, point.y, point.layer)):
            return f"{name} coordinates must be integers"
        if point.layer not in layers:
            return f"{name} layer is not one of the signal layers"
        if not min_x <= point.x <= max_x or not min_y <= point.y <= max_y:
            return f"{name} is outside the search bounds"
    obstacles_obj: object = request.obstacles
    if not isinstance(obstacles_obj, tuple):
        return "obstacles must be an immutable tuple"
    obstacles = obstacles_obj
    settings_obj: object = request.settings
    if not isinstance(settings_obj, LayeredAStarSettings):
        return "settings must be a LayeredAStarSettings value"
    settings = settings_obj
    for name, value, maximum in (
        ("move cost", settings.move_cost, _MAX_COST),
        ("via cost", settings.via_cost, _MAX_COST),
        ("expansion budget", settings.max_expansions, _MAX_EXPANSIONS),
        ("node budget", settings.max_nodes, _MAX_NODES),
        ("obstacle budget", settings.max_obstacles, _MAX_OBSTACLES),
        ("obstacle-check budget", settings.max_obstacle_checks, _MAX_OBSTACLE_CHECKS),
    ):
        if not _integer(value) or not 1 <= value <= maximum:
            return f"{name} must be a positive integer"
    if len(obstacles) > settings.max_obstacles:
        return "obstacle count exceeds the configured obstacle budget"
    for obstacle_obj in obstacles:
        obstacle: object = obstacle_obj
        if not isinstance(obstacle, LayeredObstacle):
            return "obstacles must be LayeredObstacle values"
        if not all(
            _integer(value)
            for value in (
                obstacle.layer,
                obstacle.min_x,
                obstacle.min_y,
                obstacle.max_x,
                obstacle.max_y,
            )
        ):
            return "obstacle coordinates must be integers"
        if obstacle.layer not in layers:
            return "obstacle layer is not one of the signal layers"
        if obstacle.min_x > obstacle.max_x or obstacle.min_y > obstacle.max_y:
            return "obstacle bounds must be ordered"
    return request, layers, (min_x, min_y, max_x, max_y)


def _is_cancelled(cancelled: object) -> bool:
    if cancelled is None:
        return False
    if not callable(cancelled):
        return True
    try:
        return bool(cancelled())
    except Exception:
        # A broken external cancellation source must fail closed rather than escape the pure
        # planner's bounded result contract.
        return True


def _heuristic(node: _Node, goal: _Node, settings: LayeredAStarSettings) -> int:
    distance = abs(node[0] - goal[0]) + abs(node[1] - goal[1])
    return distance * settings.move_cost + (settings.via_cost if node[2] != goal[2] else 0)


def _blocked(
    node: _Node,
    obstacles: tuple[LayeredObstacle, ...],
) -> bool:
    x, y, layer = node
    return any(
        obstacle.layer == layer
        and obstacle.min_x <= x <= obstacle.max_x
        and obstacle.min_y <= y <= obstacle.max_y
        for obstacle in obstacles
    )


def _neighbors(node: _Node, layers: tuple[int, int]) -> tuple[tuple[_Node, bool], ...]:
    x, y, layer = node
    cardinal = tuple(((x + dx, y + dy, layer), False) for dx, dy in _CARDINALS)
    other_layer = layers[1] if layer == layers[0] else layers[0]
    return (*cardinal, ((x, y, other_layer), True))


def _path(
    start: _Node,
    goal: _Node,
    came_from: dict[_Node, _Node],
) -> tuple[LayeredStep, ...]:
    reversed_nodes = [goal]
    current = goal
    while current != start:
        current = came_from[current]
        reversed_nodes.append(current)
    nodes = tuple(reversed(reversed_nodes))
    steps: list[LayeredStep] = [LayeredStep(*nodes[0], kind="start")]
    for previous, current in pairwise(nodes):
        kind = "via" if previous[2] != current[2] else "move"
        steps.append(LayeredStep(*current, kind=kind))
    return tuple(steps)


def route_layered(
    request: LayeredAStarRequest,
    *,
    cancelled: object | None = None,
) -> LayeredAStarResult:
    """Run deterministic two-layer A* and return a path or a diagnostic.

    Cardinal moves are expanded in east, north, west, south order.  A via transition is expanded
    after those moves and changes only the layer at the same coordinate.  The heap key contains the
    full integer state, making equal-cost replay independent of hash ordering or dictionary order.
    """

    validated = _validate(request)
    if isinstance(validated, str):
        if (
            isinstance(request, LayeredAStarRequest)
            and isinstance(request.board_revision, str)
            and isinstance(request.expected_revision, str)
            and 1 <= len(request.expected_revision) <= 256
            and (request.expected_revision != request.board_revision)
        ):
            return LayeredAStarResult(
                path=None,
                metrics=LayeredAStarMetrics(),
                diagnostic=LayeredDiagnostic(LayeredFailureCode.STALE_REVISION, validated),
            )
        return _invalid(validated)
    checked, layers, bounds = validated
    if cancelled is not None and not callable(cancelled):
        return _invalid("cancellation check must be callable")
    if _is_cancelled(cancelled):
        return _failure(LayeredFailureCode.CANCELLED, "the search was cancelled", _Work())

    settings = checked.settings
    start: _Node = (checked.start.x, checked.start.y, checked.start.layer)
    goal: _Node = (checked.goal.x, checked.goal.y, checked.goal.layer)
    min_x, min_y, max_x, max_y = bounds
    work = _Work(discovered_nodes=1, peak_frontier_nodes=1)
    endpoint_checks = len(checked.obstacles) * (2 if start != goal else 1)
    if endpoint_checks > settings.max_obstacle_checks:
        return _failure(
            LayeredFailureCode.OBSTACLE_BUDGET_EXCEEDED,
            "the search reached its obstacle-check budget",
            work,
        )
    work.obstacle_checks = endpoint_checks
    if _blocked(start, checked.obstacles) or _blocked(goal, checked.obstacles):
        return _failure(LayeredFailureCode.NO_PATH, "a terminal lies inside an obstacle", work)
    if start == goal:
        path = (LayeredStep(*start, kind="start"),)
        return LayeredAStarResult(
            path=path,
            metrics=LayeredAStarMetrics(
                discovered_nodes=1,
                peak_frontier_nodes=1,
                path_cost=0,
            ),
        )

    frontier: list[tuple[int, int, int, int, int, int]] = []
    start_h = _heuristic(start, goal, settings)
    heapq.heappush(frontier, (start_h, 0, 0, start[0], start[1], start[2]))
    g_score: dict[_Node, int] = {start: 0}
    via_score: dict[_Node, int] = {start: 0}
    came_from: dict[_Node, _Node] = {}

    while frontier:
        if _is_cancelled(cancelled):
            return _failure(LayeredFailureCode.CANCELLED, "the search was cancelled", work)
        if work.expanded_nodes >= settings.max_expansions:
            return _failure(
                LayeredFailureCode.SEARCH_BUDGET_EXCEEDED,
                "the search reached its expansion budget",
                work,
            )
        _, queued_g, _, x, y, layer = heapq.heappop(frontier)
        current: _Node = (x, y, layer)
        if queued_g != g_score.get(current):
            continue
        work.expanded_nodes += 1
        if current == goal:
            steps = _path(start, goal, came_from)
            move_steps = sum(step.kind == "move" for step in steps)
            via_steps = sum(step.kind == "via" for step in steps)
            return LayeredAStarResult(
                path=steps,
                metrics=LayeredAStarMetrics(
                    expanded_nodes=work.expanded_nodes,
                    discovered_nodes=work.discovered_nodes,
                    peak_frontier_nodes=work.peak_frontier_nodes,
                    obstacle_checks=work.obstacle_checks,
                    move_steps=move_steps,
                    via_steps=via_steps,
                    path_cost=g_score[current],
                ),
            )
        for neighbor, is_via in _neighbors(current, layers):
            nx, ny, _ = neighbor
            if not min_x <= nx <= max_x or not min_y <= ny <= max_y:
                continue
            relation_checks = len(checked.obstacles)
            if work.obstacle_checks + relation_checks > settings.max_obstacle_checks:
                return _failure(
                    LayeredFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                    "the search reached its obstacle-check budget",
                    work,
                )
            work.obstacle_checks += relation_checks
            if _blocked(neighbor, checked.obstacles):
                continue
            step_cost = settings.via_cost if is_via else settings.move_cost
            tentative_g = g_score[current] + step_cost
            old_g = g_score.get(neighbor)
            old_vias = via_score.get(neighbor, 1 << 60)
            tentative_vias = via_score[current] + int(is_via)
            if old_g is not None and (
                tentative_g > old_g or (tentative_g == old_g and tentative_vias >= old_vias)
            ):
                continue
            if old_g is None:
                if work.discovered_nodes >= settings.max_nodes:
                    return _failure(
                        LayeredFailureCode.GRID_BUDGET_EXCEEDED,
                        "the search reached its node budget",
                        work,
                    )
                work.discovered_nodes += 1
            came_from[neighbor] = current
            g_score[neighbor] = tentative_g
            via_score[neighbor] = tentative_vias
            f_score = tentative_g + _heuristic(neighbor, goal, settings)
            # Coordinates and layer are part of the key; no insertion counter or hash order leaks
            # into replay.  Via count is a stable secondary preference for equal cost paths.
            heapq.heappush(
                frontier,
                (f_score, tentative_g, tentative_vias, neighbor[0], neighbor[1], neighbor[2]),
            )
            work.peak_frontier_nodes = max(work.peak_frontier_nodes, len(frontier))

    return _failure(LayeredFailureCode.NO_PATH, "no bounded path exists", work)


class LayeredAStarRouter:
    """Small internal façade for callers that prefer an object over ``route_layered``."""

    def route(
        self,
        request: LayeredAStarRequest,
        *,
        cancelled: CancellationCheck | None = None,
    ) -> LayeredAStarResult:
        return route_layered(request, cancelled=cancelled)
