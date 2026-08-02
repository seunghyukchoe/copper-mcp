"""Deterministic, bounded, integer-only two-pin A* reference router."""

from __future__ import annotations

import hashlib
import heapq
import json
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import TypeAlias, cast

from copper_mcp.board_ir import (
    BoardIRSnapshot,
    BoardIRValidationError,
    NetClass,
    Pad,
    PointNM,
    Ring,
    verify_snapshot,
)
from copper_mcp.routing.contracts import (
    AStarSettings,
    CancellationCheck,
    RouteCandidate,
    RouteCost,
    RouteDiagnostic,
    RouteFailureCode,
    RouteMetrics,
    RoutePatch,
    RouteRequest,
    RouteResult,
)

ROUTER_VERSION = "astar-grid/0.1.0"
ROUTING_POLICY = "orthogonal-a-star-v1"
_EMPTY_DIGEST = f"sha256:{'0' * 64}"

_Rect: TypeAlias = tuple[int, int, int, int]
_State: TypeAlias = tuple[int, int, int]
_Score: TypeAlias = tuple[int, int, int]
_DIRECTIONS: tuple[tuple[int, int], ...] = (
    (1, 0),  # east
    (0, -1),  # north
    (-1, 0),  # west
    (0, 1),  # south
)
_NO_DIRECTION = len(_DIRECTIONS)


@dataclass(frozen=True, slots=True)
class _Problem:
    snapshot: BoardIRSnapshot
    request: RouteRequest
    start_pad: Pad
    end_pad: Pad
    width_nm: int
    clearance_nm: int
    safe_board: _Rect
    obstacles: tuple[_Rect, ...]
    min_ix: int
    max_ix: int
    min_iy: int
    max_iy: int
    goal_ix: int
    goal_iy: int


@dataclass(frozen=True, slots=True)
class _ExpectedFailureError(Exception):
    code: RouteFailureCode
    message: str
    expanded_states: int = 0
    obstacle_checks: int = 0


@dataclass(slots=True)
class _WorkBudget:
    settings: AStarSettings
    cancelled: CancellationCheck | None
    expanded_states: int = 0
    obstacle_checks: int = 0

    def checkpoint(self) -> None:
        if self.cancelled is not None and self.cancelled():
            raise _fail(
                RouteFailureCode.CANCELLED,
                "the routing search was cancelled",
                expanded_states=self.expanded_states,
                obstacle_checks=self.obstacle_checks,
            )

    def obstacle_check(self) -> None:
        if self.obstacle_checks >= self.settings.max_obstacle_checks:
            raise _fail(
                RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                "the routing search reached its obstacle-check budget",
                expanded_states=self.expanded_states,
                obstacle_checks=self.obstacle_checks,
            )
        self.obstacle_checks += 1
        if self.obstacle_checks % 64 == 0:
            self.checkpoint()


def _fail(
    code: RouteFailureCode,
    message: str,
    *,
    expanded_states: int = 0,
    obstacle_checks: int = 0,
) -> _ExpectedFailureError:
    return _ExpectedFailureError(code, message, expanded_states, obstacle_checks)


def _result_failure(failure: _ExpectedFailureError) -> RouteResult:
    return RouteResult(
        diagnostic=RouteDiagnostic(
            code=failure.code,
            message=failure.message,
            expanded_states=failure.expanded_states,
            obstacle_checks=failure.obstacle_checks,
        )
    )


def _ceil_div(numerator: int, denominator: int) -> int:
    return -((-numerator) // denominator)


def _rectangle(ring: Ring) -> _Rect | None:
    points = ring.points
    if len(points) != 4:
        return None
    xs = sorted({point.x for point in points})
    ys = sorted({point.y for point in points})
    if len(xs) != 2 or len(ys) != 2:
        return None
    if {(point.x, point.y) for point in points} != {
        (xs[0], ys[0]),
        (xs[0], ys[1]),
        (xs[1], ys[0]),
        (xs[1], ys[1]),
    }:
        return None
    if any(
        start.x != end.x and start.y != end.y
        for start, end in zip(points, points[1:] + points[:1], strict=True)
    ):
        return None
    return xs[0], ys[0], xs[1], ys[1]


def _inside_open(point: PointNM, rectangle: _Rect) -> bool:
    min_x, min_y, max_x, max_y = rectangle
    return min_x < point.x < max_x and min_y < point.y < max_y


def _inside_closed(point: PointNM, rectangle: _Rect) -> bool:
    min_x, min_y, max_x, max_y = rectangle
    return min_x <= point.x <= max_x and min_y <= point.y <= max_y


def _edge_enters_open_rectangle(start: PointNM, end: PointNM, rectangle: _Rect) -> bool:
    min_x, min_y, max_x, max_y = rectangle
    if start.y == end.y:
        return (
            min_y < start.y < max_y and max(start.x, end.x) > min_x and min(start.x, end.x) < max_x
        )
    return min_x < start.x < max_x and max(start.y, end.y) > min_y and min(start.y, end.y) < max_y


def _edge_is_legal(
    start: PointNM,
    end: PointNM,
    problem: _Problem,
    work: _WorkBudget,
) -> bool:
    if not _inside_closed(start, problem.safe_board) or not _inside_closed(end, problem.safe_board):
        return False
    for obstacle in problem.obstacles:
        work.obstacle_check()
        if _edge_enters_open_rectangle(start, end, obstacle):
            return False
    return True


def _proximity_step(point: PointNM, problem: _Problem, work: _WorkBudget) -> int:
    step = problem.request.settings.grid_step_nm
    min_x, min_y, max_x, max_y = problem.safe_board
    if min(point.x - min_x, max_x - point.x, point.y - min_y, max_y - point.y) < step:
        return 1
    for obstacle in problem.obstacles:
        work.obstacle_check()
        obstacle_min_x, obstacle_min_y, obstacle_max_x, obstacle_max_y = obstacle
        dx = max(obstacle_min_x - point.x, 0, point.x - obstacle_max_x)
        dy = max(obstacle_min_y - point.y, 0, point.y - obstacle_max_y)
        if max(dx, dy) < step:
            return 1
    return 0


def _resolve_net_class(snapshot: BoardIRSnapshot, net_id: str, work: _WorkBudget) -> NetClass:
    assignment = None
    for index, assignment_item in enumerate(snapshot.content.constraints.assignments):
        if index % 64 == 0:
            work.checkpoint()
        if assignment_item.net_id == net_id:
            assignment = assignment_item
            break
    if assignment is None:
        raise _fail(
            RouteFailureCode.INVALID_TWO_PIN_NET,
            "the selected net has no exact net-class assignment",
        )
    net_class = None
    for index, net_class_item in enumerate(snapshot.content.constraints.net_classes):
        if index % 64 == 0:
            work.checkpoint()
        if net_class_item.id == assignment.net_class_id:
            net_class = net_class_item
            break
    if net_class is None:
        raise _fail(
            RouteFailureCode.INVALID_TWO_PIN_NET,
            "the selected net class cannot be resolved",
        )
    return net_class


def _prepare(
    snapshot: BoardIRSnapshot,
    request: RouteRequest,
    work: _WorkBudget,
) -> _Problem:
    work.checkpoint()
    try:
        verify_snapshot(snapshot)
    except (BoardIRValidationError, TypeError, ValueError) as error:
        raise _fail(
            RouteFailureCode.INVALID_SNAPSHOT,
            "the Board IR snapshot failed canonical verification",
        ) from error
    work.checkpoint()
    if request.board_revision != snapshot.snapshot_digest:
        raise _fail(
            RouteFailureCode.STALE_REVISION,
            "the routing request does not match the immutable board revision",
        )

    content = snapshot.content
    selected_layer = None
    for index, layer in enumerate(content.copper_layers):
        if index % 64 == 0:
            work.checkpoint()
        if layer.id == request.layer_id:
            selected_layer = layer
            break
    if selected_layer is None:
        raise _fail(
            RouteFailureCode.UNSUPPORTED_GEOMETRY,
            "the selected copper layer does not exist in the snapshot",
        )
    if selected_layer.kind != "signal":
        raise _fail(
            RouteFailureCode.UNSUPPORTED_GEOMETRY,
            "the first routing slice supports signal layers only",
        )
    net_exists = False
    for index, net in enumerate(content.nets):
        if index % 64 == 0:
            work.checkpoint()
        if net.id == request.net_id:
            net_exists = True
            break
    if not net_exists:
        raise _fail(
            RouteFailureCode.INVALID_TWO_PIN_NET,
            "the selected net does not exist in the snapshot",
        )
    for index, pair_rule in enumerate(content.constraints.differential_pairs):
        if index % 64 == 0:
            work.checkpoint()
        if request.net_id in {pair_rule.positive_net_id, pair_rule.negative_net_id}:
            raise _fail(
                RouteFailureCode.UNSUPPORTED_CONSTRAINT,
                "the selected net carries a constraint not modeled by the first routing slice",
            )
    for index, length_rule in enumerate(content.constraints.length_rules):
        if index % 64 == 0:
            work.checkpoint()
        if length_rule.net_id == request.net_id:
            raise _fail(
                RouteFailureCode.UNSUPPORTED_CONSTRAINT,
                "the selected net carries a constraint not modeled by the first routing slice",
            )

    target_pads: list[Pad] = []
    has_additional_layer_pad = False
    for index, pad in enumerate(content.pads):
        if index % 64 == 0:
            work.checkpoint()
        if pad.net_id == request.net_id:
            target_pads.append(pad)
        elif request.layer_id in pad.layer_ids:
            has_additional_layer_pad = True
    pads = tuple(sorted(target_pads, key=lambda pad: pad.id))
    if len(pads) != 2 or any(request.layer_id not in pad.layer_ids for pad in pads):
        raise _fail(
            RouteFailureCode.INVALID_TWO_PIN_NET,
            "the selected net must resolve to exactly two pads on the selected layer",
        )
    start_pad, end_pad = pads
    if start_pad.center == end_pad.center:
        raise _fail(
            RouteFailureCode.UNSUPPORTED_GEOMETRY,
            "coincident route endpoints are outside the first-slice contract",
        )

    if has_additional_layer_pad:
        raise _fail(
            RouteFailureCode.UNSUPPORTED_GEOMETRY,
            "additional pads on the selected layer require a future obstacle model",
        )
    has_existing_copper = bool(content.vias)
    for index, segment in enumerate(content.segments):
        if index % 64 == 0:
            work.checkpoint()
        if segment.layer_id == request.layer_id:
            has_existing_copper = True
            break
    for index, arc in enumerate(content.arcs):
        if has_existing_copper:
            break
        if index % 64 == 0:
            work.checkpoint()
        if arc.layer_id == request.layer_id:
            has_existing_copper = True
            break
    for index, zone in enumerate(content.zones):
        if has_existing_copper:
            break
        if index % 64 == 0:
            work.checkpoint()
        if zone.layer_id == request.layer_id:
            has_existing_copper = True
            break
    if has_existing_copper:
        raise _fail(
            RouteFailureCode.UNSUPPORTED_GEOMETRY,
            "existing selected-layer copper requires a future obstacle model",
        )

    if len(content.outline) != 1 or content.outline[0].holes:
        raise _fail(
            RouteFailureCode.UNSUPPORTED_GEOMETRY,
            "the first routing slice requires one hole-free outline",
        )
    board = _rectangle(content.outline[0].outer)
    if board is None:
        raise _fail(
            RouteFailureCode.UNSUPPORTED_GEOMETRY,
            "the first routing slice requires an axis-aligned rectangular outline",
        )

    net_class = _resolve_net_class(snapshot, request.net_id, work)
    half_width_nm = (net_class.track_width_nm + 1) // 2
    board_min_x, board_min_y, board_max_x, board_max_y = board
    safe_board = (
        board_min_x + half_width_nm,
        board_min_y + half_width_nm,
        board_max_x - half_width_nm,
        board_max_y - half_width_nm,
    )
    if safe_board[0] > safe_board[2] or safe_board[1] > safe_board[3]:
        raise _fail(RouteFailureCode.NO_PATH, "the routed width does not fit inside the board")

    inflation_nm = half_width_nm + net_class.clearance_nm
    obstacles: list[_Rect] = []
    for index, keepout in enumerate(content.keepouts):
        if index % 64 == 0:
            work.checkpoint()
        if request.layer_id not in keepout.layer_ids or not keepout.prohibit_tracks:
            continue
        if len(obstacles) >= request.settings.max_obstacles:
            raise _fail(
                RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                "the selected-layer keepout count exceeds the configured obstacle budget",
            )
        rectangle = _rectangle(keepout.boundary)
        if rectangle is None:
            raise _fail(
                RouteFailureCode.UNSUPPORTED_GEOMETRY,
                "a selected-layer track keepout is not an axis-aligned rectangle",
            )
        min_x, min_y, max_x, max_y = rectangle
        obstacles.append(
            (
                min_x - inflation_nm,
                min_y - inflation_nm,
                max_x + inflation_nm,
                max_y + inflation_nm,
            )
        )

    step = request.settings.grid_step_nm
    delta_x = end_pad.center.x - start_pad.center.x
    delta_y = end_pad.center.y - start_pad.center.y
    if delta_x % step != 0 or delta_y % step != 0:
        raise _fail(
            RouteFailureCode.OFF_GRID,
            "the pad-center delta is not divisible by the requested grid step",
        )
    min_ix = _ceil_div(safe_board[0] - start_pad.center.x, step)
    max_ix = (safe_board[2] - start_pad.center.x) // step
    min_iy = _ceil_div(safe_board[1] - start_pad.center.y, step)
    max_iy = (safe_board[3] - start_pad.center.y) // step
    goal_ix = delta_x // step
    goal_iy = delta_y // step
    if not (min_ix <= 0 <= max_ix and min_iy <= 0 <= max_iy):
        raise _fail(RouteFailureCode.NO_PATH, "the start pad cannot contain the routed width")
    if not (min_ix <= goal_ix <= max_ix and min_iy <= goal_iy <= max_iy):
        raise _fail(RouteFailureCode.NO_PATH, "the end pad cannot contain the routed width")

    grid_nodes = (max_ix - min_ix + 1) * (max_iy - min_iy + 1)
    if grid_nodes > request.settings.max_grid_nodes:
        raise _fail(
            RouteFailureCode.GRID_BUDGET_EXCEEDED,
            "the bounded routing lattice exceeds the configured node budget",
        )

    problem = _Problem(
        snapshot=snapshot,
        request=request,
        start_pad=start_pad,
        end_pad=end_pad,
        width_nm=net_class.track_width_nm,
        clearance_nm=net_class.clearance_nm,
        safe_board=safe_board,
        obstacles=tuple(sorted(obstacles)),
        min_ix=min_ix,
        max_ix=max_ix,
        min_iy=min_iy,
        max_iy=max_iy,
        goal_ix=goal_ix,
        goal_iy=goal_iy,
    )
    for obstacle in problem.obstacles:
        work.obstacle_check()
        if _inside_open(start_pad.center, obstacle) or _inside_open(end_pad.center, obstacle):
            raise _fail(
                RouteFailureCode.NO_PATH,
                "a route endpoint is blocked by a track keepout",
                obstacle_checks=work.obstacle_checks,
            )
    work.checkpoint()
    return problem


def _point(problem: _Problem, ix: int, iy: int) -> PointNM:
    step = problem.request.settings.grid_step_nm
    return PointNM(
        problem.start_pad.center.x + ix * step,
        problem.start_pad.center.y + iy * step,
    )


def _heuristic(problem: _Problem, ix: int, iy: int) -> int:
    return (
        abs(problem.goal_ix - ix) + abs(problem.goal_iy - iy)
    ) * problem.request.settings.grid_step_nm


def _compress(points: tuple[PointNM, ...]) -> tuple[PointNM, ...]:
    compressed: list[PointNM] = []
    for point in points:
        if len(compressed) >= 2:
            first, middle = compressed[-2:]
            if (first.x == middle.x == point.x) or (first.y == middle.y == point.y):
                compressed[-1] = point
                continue
        compressed.append(point)
    return tuple(compressed)


def _candidate_payload(candidate: RouteCandidate) -> dict[str, object]:
    return {
        "base_revision": candidate.base_revision,
        "cost": {
            "bend_cost_nm": candidate.cost.bend_cost_nm,
            "bend_count": candidate.cost.bend_count,
            "length_nm": candidate.cost.length_nm,
            "proximity_cost_nm": candidate.cost.proximity_cost_nm,
            "proximity_steps": candidate.cost.proximity_steps,
            "total_cost_nm": candidate.cost.total_cost_nm,
            "via_cost_nm": candidate.cost.via_cost_nm,
        },
        "end_pad_id": candidate.end_pad_id,
        "metrics": {
            "expanded_states": candidate.metrics.expanded_states,
            "hard_internal_violations": candidate.metrics.hard_internal_violations,
            "obstacle_checks": candidate.metrics.obstacle_checks,
            "peak_frontier_states": candidate.metrics.peak_frontier_states,
            "unrouted_connections": candidate.metrics.unrouted_connections,
            "vias": candidate.metrics.vias,
            "wire_length_nm": candidate.metrics.wire_length_nm,
        },
        "patch": {
            "layer_id": candidate.patch.layer_id,
            "net_id": candidate.patch.net_id,
            "vertices": [{"x_nm": point.x, "y_nm": point.y} for point in candidate.patch.vertices],
            "width_nm": candidate.patch.width_nm,
        },
        "policy": candidate.policy,
        "router_version": candidate.router_version,
        "seed": candidate.seed,
        "settings": {
            "bend_penalty_nm": candidate.settings.bend_penalty_nm,
            "grid_step_nm": candidate.settings.grid_step_nm,
            "max_expansions": candidate.settings.max_expansions,
            "max_grid_nodes": candidate.settings.max_grid_nodes,
            "max_obstacle_checks": candidate.settings.max_obstacle_checks,
            "max_obstacles": candidate.settings.max_obstacles,
            "proximity_penalty_nm": candidate.settings.proximity_penalty_nm,
        },
        "start_pad_id": candidate.start_pad_id,
    }


def canonical_candidate_bytes(candidate: RouteCandidate) -> bytes:
    """Return stable identity bytes; the circular ``candidate_id`` field is omitted."""

    rendered = json.dumps(
        _candidate_payload(candidate),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return rendered.encode("utf-8", errors="strict") + b"\n"


def verify_candidate_id(candidate: RouteCandidate) -> bool:
    """Raise when a route candidate ID does not match its canonical identity bytes."""

    expected = f"sha256:{hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()}"
    if candidate.candidate_id != expected:
        raise ValueError("candidate ID does not match canonical route content")
    return True


def _build_candidate(
    problem: _Problem,
    points: tuple[PointNM, ...],
    *,
    bend_count: int,
    proximity_steps: int,
    expanded_states: int,
    peak_frontier_states: int,
    work: _WorkBudget,
) -> RouteCandidate:
    compressed = _compress(points)
    if compressed[0] != problem.start_pad.center or compressed[-1] != problem.end_pad.center:
        raise RuntimeError("internal route reconstruction changed its endpoints")
    for start, end in pairwise(compressed):
        if not _edge_is_legal(start, end, problem, work):
            raise RuntimeError("internal route post-validation rejected generated geometry")

    length_nm = sum(
        abs(start.x - end.x) + abs(start.y - end.y) for start, end in pairwise(compressed)
    )
    if bend_count != len(compressed) - 2:
        raise RuntimeError("internal route bend accounting is inconsistent")
    settings = problem.request.settings
    cost = RouteCost(
        length_nm=length_nm,
        bend_count=bend_count,
        bend_cost_nm=bend_count * settings.bend_penalty_nm,
        proximity_steps=proximity_steps,
        proximity_cost_nm=proximity_steps * settings.proximity_penalty_nm,
        via_cost_nm=0,
        total_cost_nm=(
            length_nm
            + bend_count * settings.bend_penalty_nm
            + proximity_steps * settings.proximity_penalty_nm
        ),
    )
    candidate = RouteCandidate(
        candidate_id=_EMPTY_DIGEST,
        base_revision=problem.snapshot.snapshot_digest,
        start_pad_id=problem.start_pad.id,
        end_pad_id=problem.end_pad.id,
        patch=RoutePatch(
            net_id=problem.request.net_id,
            layer_id=problem.request.layer_id,
            width_nm=problem.width_nm,
            vertices=compressed,
        ),
        cost=cost,
        metrics=RouteMetrics(
            hard_internal_violations=0,
            unrouted_connections=0,
            vias=0,
            wire_length_nm=length_nm,
            expanded_states=expanded_states,
            peak_frontier_states=peak_frontier_states,
            obstacle_checks=work.obstacle_checks,
        ),
        settings=settings,
        router_version=ROUTER_VERSION,
        policy=ROUTING_POLICY,
        seed=problem.request.seed,
    )
    digest = f"sha256:{hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()}"
    candidate = replace(candidate, candidate_id=digest)
    verify_candidate_id(candidate)
    return candidate


def _search(problem: _Problem, work: _WorkBudget) -> RouteCandidate:
    settings = problem.request.settings
    start: _State = (0, 0, _NO_DIRECTION)
    start_score: _Score = (0, 0, 0)
    best: dict[_State, _Score] = {start: start_score}
    parents: dict[_State, _State] = {}
    frontier: list[tuple[int, int, int, int, int, int, int, int, _State]] = []
    counter = 0
    heapq.heappush(
        frontier,
        (_heuristic(problem, 0, 0), 0, 0, 0, 0, 0, _NO_DIRECTION, counter, start),
    )
    expanded_states = 0
    peak_frontier_states = 1

    while frontier:
        work.checkpoint()
        _, g_cost, bends, proximity_steps, iy, ix, direction, _, state = heapq.heappop(frontier)
        if best.get(state) != (g_cost, bends, proximity_steps):
            continue
        if (ix, iy) == (problem.goal_ix, problem.goal_iy):
            route_states = [state]
            while route_states[-1] != start:
                route_states.append(parents[route_states[-1]])
            route_states.reverse()
            points = tuple(_point(problem, item[0], item[1]) for item in route_states)
            return _build_candidate(
                problem,
                points,
                bend_count=bends,
                proximity_steps=proximity_steps,
                expanded_states=expanded_states,
                peak_frontier_states=peak_frontier_states,
                work=work,
            )
        if expanded_states >= settings.max_expansions:
            raise _fail(
                RouteFailureCode.SEARCH_BUDGET_EXCEEDED,
                "the A* search reached its configured expansion budget",
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
            parents[next_state] = state
            counter += 1
            heapq.heappush(
                frontier,
                (
                    next_cost + _heuristic(problem, next_ix, next_iy),
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


def _validate_public_inputs(
    snapshot: object,
    request: object,
    cancelled: object,
) -> tuple[BoardIRSnapshot, RouteRequest, CancellationCheck | None] | RouteResult:
    if not isinstance(snapshot, BoardIRSnapshot):
        return _result_failure(
            _fail(
                RouteFailureCode.INVALID_SNAPSHOT,
                "the Board IR snapshot type is invalid",
            )
        )
    if not isinstance(request, RouteRequest) or (cancelled is not None and not callable(cancelled)):
        return _result_failure(
            _fail(
                RouteFailureCode.INVALID_REQUEST,
                "the routing request type is invalid",
            )
        )
    return snapshot, request, cast("CancellationCheck | None", cancelled)


class AStarRouter:
    """Pure CPU reference backend for one exact two-pin route candidate."""

    @property
    def name(self) -> str:
        return ROUTING_POLICY

    def propose(
        self,
        snapshot: BoardIRSnapshot,
        request: RouteRequest,
        *,
        cancelled: CancellationCheck | None = None,
    ) -> RouteResult:
        """Return an unapplied candidate or a stable, non-echoing expected failure."""

        validated = _validate_public_inputs(snapshot, request, cancelled)
        if isinstance(validated, RouteResult):
            return validated
        checked_snapshot, checked_request, cancellation_check = validated
        work = _WorkBudget(settings=checked_request.settings, cancelled=cancellation_check)
        try:
            problem = _prepare(checked_snapshot, checked_request, work)
            return RouteResult(candidate=_search(problem, work))
        except _ExpectedFailureError as failure:
            return _result_failure(failure)
