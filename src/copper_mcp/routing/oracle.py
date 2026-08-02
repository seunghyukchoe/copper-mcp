"""Bounded Dijkstra oracle for benchmark-only A* cost comparisons.

This module deliberately reuses the production router's exact preparation and
edge-cost evaluators while removing the heuristic.  It does not emit a route
candidate and is not part of the supported routing API.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import cast

from copper_mcp.board_ir import BoardIRSnapshot
from copper_mcp.routing.astar import (
    _DIRECTIONS,
    _NO_DIRECTION,
    _edge_is_legal,
    _ExpectedFailureError,
    _fail,
    _point,
    _prepare,
    _Problem,
    _proximity_step,
    _Score,
    _State,
    _WorkBudget,
)
from copper_mcp.routing.contracts import (
    CancellationCheck,
    RouteDiagnostic,
    RouteFailureCode,
    RouteRequest,
)

ORACLE_POLICY = "orthogonal-dijkstra-oracle-v1"


def _nonnegative_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class DijkstraResult:
    """Exact optimal cost or one expected, fail-closed oracle diagnostic."""

    total_cost_nm: int | None = None
    bend_count: int | None = None
    proximity_steps: int | None = None
    expanded_states: int = 0
    peak_frontier_states: int = 0
    obstacle_checks: int = 0
    diagnostic: RouteDiagnostic | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("expanded states", self.expanded_states),
            ("peak frontier states", self.peak_frontier_states),
            ("obstacle checks", self.obstacle_checks),
        ):
            _nonnegative_integer(name, value)
        has_cost = all(
            value is not None
            for value in (self.total_cost_nm, self.bend_count, self.proximity_steps)
        )
        if has_cost == (self.diagnostic is not None):
            raise ValueError("Dijkstra result must contain exactly one cost or diagnostic")
        if has_cost:
            assert self.total_cost_nm is not None
            assert self.bend_count is not None
            assert self.proximity_steps is not None
            _nonnegative_integer("total cost", self.total_cost_nm)
            _nonnegative_integer("bend count", self.bend_count)
            _nonnegative_integer("proximity steps", self.proximity_steps)
            if self.peak_frontier_states < 1:
                raise ValueError("a successful Dijkstra result must record a frontier")
        elif not isinstance(self.diagnostic, RouteDiagnostic):
            raise ValueError("Dijkstra diagnostic is malformed")

    @property
    def ok(self) -> bool:
        """Return true only when an exact optimal cost is present."""

        return self.diagnostic is None


def _failure_result(failure: _ExpectedFailureError) -> DijkstraResult:
    return DijkstraResult(
        expanded_states=failure.expanded_states,
        obstacle_checks=failure.obstacle_checks,
        diagnostic=RouteDiagnostic(
            code=failure.code,
            message=failure.message,
            expanded_states=failure.expanded_states,
            obstacle_checks=failure.obstacle_checks,
        ),
    )


def _search_dijkstra(problem: _Problem, work: _WorkBudget) -> DijkstraResult:
    """Run uniform-cost search over the prepared A* state graph."""

    settings = problem.request.settings
    start: _State = (0, 0, _NO_DIRECTION)
    start_score: _Score = (0, 0, 0)
    best: dict[_State, _Score] = {start: start_score}
    frontier: list[tuple[int, int, int, int, int, int, int, _State]] = [
        (0, 0, 0, 0, 0, _NO_DIRECTION, 0, start)
    ]
    counter = 0
    expanded_states = 0
    peak_frontier_states = 1

    while frontier:
        work.checkpoint()
        g_cost, bends, proximity_steps, iy, ix, direction, _, state = heapq.heappop(frontier)
        if best.get(state) != (g_cost, bends, proximity_steps):
            continue
        if (ix, iy) == (problem.goal_ix, problem.goal_iy):
            return DijkstraResult(
                total_cost_nm=g_cost,
                bend_count=bends,
                proximity_steps=proximity_steps,
                expanded_states=expanded_states,
                peak_frontier_states=peak_frontier_states,
                obstacle_checks=work.obstacle_checks,
            )
        if expanded_states >= settings.max_expansions:
            raise _fail(
                RouteFailureCode.SEARCH_BUDGET_EXCEEDED,
                "the Dijkstra oracle reached its configured expansion budget",
                expanded_states=expanded_states,
                obstacle_checks=work.obstacle_checks,
            )
        expanded_states += 1
        work.expanded_states = expanded_states

        current = _point(problem, ix, iy)
        for next_direction, (delta_ix, delta_iy) in enumerate(_DIRECTIONS):
            next_ix = ix + delta_ix
            next_iy = iy + delta_iy
            if not (
                problem.min_ix <= next_ix <= problem.max_ix
                and problem.min_iy <= next_iy <= problem.max_iy
            ):
                continue
            destination = _point(problem, next_ix, next_iy)
            if not _edge_is_legal(current, destination, problem, work):
                continue
            bend_increment = int(direction != _NO_DIRECTION and direction != next_direction)
            proximity_increment = _proximity_step(destination, problem, work)
            next_bends = bends + bend_increment
            next_proximity = proximity_steps + proximity_increment
            next_cost = (
                g_cost
                + settings.grid_step_nm
                + bend_increment * settings.bend_penalty_nm
                + proximity_increment * settings.proximity_penalty_nm
            )
            next_state: _State = (next_ix, next_iy, next_direction)
            next_score: _Score = (next_cost, next_bends, next_proximity)
            if next_score >= best.get(next_state, (1 << 63, 1 << 63, 1 << 63)):
                continue
            best[next_state] = next_score
            counter += 1
            heapq.heappush(
                frontier,
                (
                    next_cost,
                    next_bends,
                    next_proximity,
                    next_iy,
                    next_ix,
                    next_direction,
                    counter,
                    next_state,
                ),
            )
        peak_frontier_states = max(peak_frontier_states, len(frontier))

    raise _fail(
        RouteFailureCode.NO_PATH,
        "no legal orthogonal path exists on the bounded grid",
        expanded_states=expanded_states,
        obstacle_checks=work.obstacle_checks,
    )


def run_dijkstra_oracle(
    snapshot: object,
    request: object,
    *,
    cancelled: object = None,
) -> DijkstraResult:
    """Return a bounded optimal cost for benchmark comparison, never a candidate."""

    if not isinstance(snapshot, BoardIRSnapshot):
        return _failure_result(
            _fail(RouteFailureCode.INVALID_SNAPSHOT, "the Board IR snapshot type is invalid")
        )
    if not isinstance(request, RouteRequest) or (cancelled is not None and not callable(cancelled)):
        return _failure_result(
            _fail(RouteFailureCode.INVALID_REQUEST, "the routing request type is invalid")
        )
    cancellation_check = cast("CancellationCheck | None", cancelled)
    work = _WorkBudget(settings=request.settings, cancelled=cancellation_check)
    try:
        problem = _prepare(snapshot, request, work)
        return _search_dijkstra(problem, work)
    except _ExpectedFailureError as failure:
        return _failure_result(failure)
