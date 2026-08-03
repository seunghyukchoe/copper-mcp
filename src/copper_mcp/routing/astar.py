"""Deterministic, bounded, integer-only two-pin A* reference router."""

from __future__ import annotations

import hashlib
import heapq
import json
from dataclasses import dataclass, replace
from itertools import pairwise
from math import isqrt
from typing import TypeAlias, cast

from copper_mcp.board_ir import (
    UDEG_PER_DEGREE,
    BoardIRSnapshot,
    BoardIRValidationError,
    NetClass,
    Pad,
    PadShape,
    PointNM,
    Ring,
    Segment,
    Via,
    Zone,
    verify_snapshot,
)
from copper_mcp.routing.contracts import (
    COMPONENT_MST_ORDERING,
    SINGLE_PATH_ORDERING,
    AStarSettings,
    CancellationCheck,
    RouteCandidate,
    RouteConnection,
    RouteCost,
    RouteDiagnostic,
    RouteFailureCode,
    RouteMetrics,
    RoutePatch,
    RoutePath,
    RouteRequest,
    RouteResult,
)

ROUTER_VERSION = "astar-grid/0.4.0"
ROUTING_POLICY = "orthogonal-a-star-v1"
_EMPTY_DIGEST = f"sha256:{'0' * 64}"

_Rect: TypeAlias = tuple[int, int, int, int]
_Node: TypeAlias = tuple[int, int]
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
class _PolygonObstacle:
    """One conservative polygon envelope, from a solid zone or a track keepout."""

    source_id: str
    points: tuple[PointNM, ...]
    bounds: _Rect
    margin_nm: int


@dataclass(frozen=True, slots=True)
class _Problem:
    snapshot: BoardIRSnapshot
    request: RouteRequest
    start_pad: Pad
    end_pad: Pad
    width_nm: int
    clearance_nm: int
    safe_board: _Rect
    rect_obstacles: tuple[_Rect, ...]
    polygon_obstacles: tuple[_PolygonObstacle, ...]
    min_ix: int
    max_ix: int
    min_iy: int
    max_iy: int
    goal_ix: int
    goal_iy: int
    pad_count: int
    components: tuple[tuple[_Rect, ...], ...]
    source_nodes: frozenset[_Node]
    target_nodes: frozenset[_Node]
    target_min_ix: int
    target_max_ix: int
    target_min_iy: int
    target_max_iy: int


@dataclass(frozen=True, slots=True)
class _ExpectedFailureError(Exception):
    code: RouteFailureCode
    message: str
    expanded_states: int = 0
    obstacle_checks: int = 0


@dataclass(frozen=True, slots=True)
class _AlreadyConnectedError(Exception):
    """Internal signal that the two pads already share one selected-layer component."""

    start_pad_id: str
    end_pad_id: str
    attachment_segments: int
    pad_count: int = 2
    vias: int = 0
    fill_polygons: int = 0
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


def _inflate_rectangle(rectangle: _Rect, margin_nm: int) -> _Rect:
    min_x, min_y, max_x, max_y = rectangle
    return (
        min_x - margin_nm,
        min_y - margin_nm,
        max_x + margin_nm,
        max_y + margin_nm,
    )


def _cross(first: PointNM, second: PointNM, third: PointNM) -> int:
    return (second.x - first.x) * (third.y - first.y) - (second.y - first.y) * (third.x - first.x)


def _on_segment(first: PointNM, point: PointNM, second: PointNM) -> bool:
    return min(first.x, second.x) <= point.x <= max(first.x, second.x) and min(
        first.y, second.y
    ) <= point.y <= max(first.y, second.y)


def _segments_intersect(
    first_start: PointNM,
    first_end: PointNM,
    second_start: PointNM,
    second_end: PointNM,
) -> bool:
    first_side_start = _cross(first_start, first_end, second_start)
    first_side_end = _cross(first_start, first_end, second_end)
    second_side_start = _cross(second_start, second_end, first_start)
    second_side_end = _cross(second_start, second_end, first_end)
    if first_side_start == 0 and _on_segment(first_start, second_start, first_end):
        return True
    if first_side_end == 0 and _on_segment(first_start, second_end, first_end):
        return True
    if second_side_start == 0 and _on_segment(second_start, first_start, second_end):
        return True
    if second_side_end == 0 and _on_segment(second_start, first_end, second_end):
        return True
    first_straddles = (first_side_start > 0 and first_side_end < 0) or (
        first_side_start < 0 and first_side_end > 0
    )
    second_straddles = (second_side_start > 0 and second_side_end < 0) or (
        second_side_start < 0 and second_side_end > 0
    )
    return first_straddles and second_straddles


def _point_segment_distance_lt(
    point: PointNM,
    start: PointNM,
    end: PointNM,
    distance_nm: int,
) -> bool:
    """Compare exact squared distance without division, roots, or floating point."""

    edge_x = end.x - start.x
    edge_y = end.y - start.y
    point_x = point.x - start.x
    point_y = point.y - start.y
    edge_length_sq = edge_x * edge_x + edge_y * edge_y
    distance_sq = distance_nm * distance_nm
    if edge_length_sq == 0:
        return point_x * point_x + point_y * point_y < distance_sq
    projection = point_x * edge_x + point_y * edge_y
    if projection <= 0:
        return point_x * point_x + point_y * point_y < distance_sq
    if projection >= edge_length_sq:
        end_x = point.x - end.x
        end_y = point.y - end.y
        return end_x * end_x + end_y * end_y < distance_sq
    area = edge_x * point_y - edge_y * point_x
    return area * area < distance_sq * edge_length_sq


def _segments_distance_lt(
    first_start: PointNM,
    first_end: PointNM,
    second_start: PointNM,
    second_end: PointNM,
    distance_nm: int,
) -> bool:
    if _segments_intersect(first_start, first_end, second_start, second_end):
        return True
    return (
        _point_segment_distance_lt(first_start, second_start, second_end, distance_nm)
        or _point_segment_distance_lt(first_end, second_start, second_end, distance_nm)
        or _point_segment_distance_lt(second_start, first_start, first_end, distance_nm)
        or _point_segment_distance_lt(second_end, first_start, first_end, distance_nm)
    )


def _ray_crosses_right(point: PointNM, start: PointNM, end: PointNM) -> bool:
    if (start.y > point.y) == (end.y > point.y):
        return False
    return (_cross(start, end, point) > 0) == (end.y > start.y)


def _polygon_bounds(points: tuple[PointNM, ...], work: _WorkBudget) -> _Rect:
    min_x = max_x = points[0].x
    min_y = max_y = points[0].y
    for point in points:
        work.obstacle_check()
        min_x = min(min_x, point.x)
        min_y = min(min_y, point.y)
        max_x = max(max_x, point.x)
        max_y = max(max_y, point.y)
    return min_x, min_y, max_x, max_y


def _polygon_obstacle_sort_key(
    obstacle: _PolygonObstacle,
) -> tuple[_Rect, int, str]:
    return obstacle.bounds, obstacle.margin_nm, obstacle.source_id


def _edge_within_polygon_offset(
    start: PointNM,
    end: PointNM,
    obstacle: _PolygonObstacle,
    work: _WorkBudget,
) -> bool:
    work.obstacle_check()
    if not _edge_enters_open_rectangle(
        start,
        end,
        _inflate_rectangle(obstacle.bounds, obstacle.margin_nm),
    ):
        return False
    start_inside = False
    for index, edge_start in enumerate(obstacle.points):
        work.obstacle_check()
        edge_end = obstacle.points[(index + 1) % len(obstacle.points)]
        if _segments_distance_lt(
            start,
            end,
            edge_start,
            edge_end,
            obstacle.margin_nm,
        ):
            return True
        if _ray_crosses_right(start, edge_start, edge_end):
            start_inside = not start_inside
    return start_inside


def _point_within_polygon_offset(
    point: PointNM,
    obstacle: _PolygonObstacle,
    distance_nm: int,
    work: _WorkBudget,
) -> bool:
    work.obstacle_check()
    if not _inside_open(point, _inflate_rectangle(obstacle.bounds, distance_nm)):
        return False
    inside = False
    for index, edge_start in enumerate(obstacle.points):
        work.obstacle_check()
        edge_end = obstacle.points[(index + 1) % len(obstacle.points)]
        if _point_segment_distance_lt(point, edge_start, edge_end, distance_nm):
            return True
        if _ray_crosses_right(point, edge_start, edge_end):
            inside = not inside
    return inside


def _edge_is_legal(
    start: PointNM,
    end: PointNM,
    problem: _Problem,
    work: _WorkBudget,
) -> bool:
    if not _inside_closed(start, problem.safe_board) or not _inside_closed(end, problem.safe_board):
        return False
    for obstacle in problem.rect_obstacles:
        work.obstacle_check()
        if _edge_enters_open_rectangle(start, end, obstacle):
            return False
    for polygon in problem.polygon_obstacles:
        if _edge_within_polygon_offset(start, end, polygon, work):
            return False
    return True


def _proximity_step(point: PointNM, problem: _Problem, work: _WorkBudget) -> int:
    step = problem.request.settings.grid_step_nm
    min_x, min_y, max_x, max_y = problem.safe_board
    if min(point.x - min_x, max_x - point.x, point.y - min_y, max_y - point.y) < step:
        return 1
    for obstacle in problem.rect_obstacles:
        work.obstacle_check()
        obstacle_min_x, obstacle_min_y, obstacle_max_x, obstacle_max_y = obstacle
        dx = max(obstacle_min_x - point.x, 0, point.x - obstacle_max_x)
        dy = max(obstacle_min_y - point.y, 0, point.y - obstacle_max_y)
        if max(dx, dy) < step:
            return 1
    for polygon in problem.polygon_obstacles:
        if _point_within_polygon_offset(
            point,
            polygon,
            polygon.margin_nm + step,
            work,
        ):
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


_QUARTER_ROTATION_UDEG = 90 * UDEG_PER_DEGREE
_AXIS_ALIGNED_ROTATIONS_UDEG = frozenset(
    {0, _QUARTER_ROTATION_UDEG, 2 * _QUARTER_ROTATION_UDEG, 3 * _QUARTER_ROTATION_UDEG}
)
# Pad shapes with an exact inscribed rectangle. Naming the set keeps the connectivity model
# fail-closed for any shape Board IR gains later.
_CORE_MODELED_PAD_SHAPES: frozenset[PadShape] = frozenset(
    {PadShape.RECT, PadShape.ROUNDRECT, PadShape.CIRCLE, PadShape.OVAL}
)
# Flooring a diagonal core centre onto the integer lattice moves it less than one nanometre on
# each axis, so it stays within sqrt(2) of the exact centreline point. Two nanometres is the
# smallest integer that covers that, and it is subtracted from the usable half width.
_CORE_CENTRE_TOLERANCE_NM = 2


def _net_clearance_nm(
    snapshot: BoardIRSnapshot, net_id: str | None, work: _WorkBudget
) -> int | None:
    """Return one net's exact class clearance, or None when it has no assignment."""

    if net_id is None:
        return None
    try:
        return _resolve_net_class(snapshot, net_id, work).clearance_nm
    except _ExpectedFailureError as failure:
        if failure.code is RouteFailureCode.INVALID_TWO_PIN_NET:
            return None
        raise


def _pad_extent(pad: Pad) -> tuple[int, int] | None:
    """Return a pad's axis-aligned half extents, or None when it is not modeled exactly."""

    if pad.rotation_udeg not in _AXIS_ALIGNED_ROTATIONS_UDEG:
        return None
    size_x, size_y = pad.size_x_nm, pad.size_y_nm
    quarter_turns = pad.rotation_udeg // _QUARTER_ROTATION_UDEG
    if quarter_turns % 2 == 1:
        size_x, size_y = size_y, size_x
    return (size_x + 1) // 2, (size_y + 1) // 2


def _segment_extent(segment: Segment) -> _Rect | None:
    """Return an orthogonal segment's covered rectangle, or None when it is diagonal."""

    if segment.start.x != segment.end.x and segment.start.y != segment.end.y:
        return None
    half_width_nm = (segment.width_nm + 1) // 2
    return (
        min(segment.start.x, segment.end.x) - half_width_nm,
        min(segment.start.y, segment.end.y) - half_width_nm,
        max(segment.start.x, segment.end.x) + half_width_nm,
        max(segment.start.y, segment.end.y) + half_width_nm,
    )


def _segment_envelope(segment: Segment) -> tuple[PointNM, ...] | None:
    """Return a conservative integer envelope of a diagonal track, or None when orthogonal.

    A track is a stadium: every point within half its width of the centreline. Sweeping an
    axis-aligned square of that half width along the centreline instead of a disc gives the
    convex hull of the two squares at the endpoints, which contains the stadium because the
    disc is inscribed in the square. Every vertex is therefore an exact integer with no
    rounding step at all, and the envelope is provably a superset rather than an approximation
    that happens to be close. The cost is over-approximating the perpendicular extent by at
    most (sqrt(2) - 1) half widths, which can only refuse a route, never permit a violation.
    """

    start, end = segment.start, segment.end
    if start.x == end.x or start.y == end.y:
        return None
    # Sweeping is symmetric, so orienting left-to-right leaves only two sign cases.
    if start.x > end.x:
        start, end = end, start
    radius_nm = (segment.width_nm + 1) // 2
    if start.y < end.y:
        return (
            PointNM(start.x - radius_nm, start.y - radius_nm),
            PointNM(start.x + radius_nm, start.y - radius_nm),
            PointNM(end.x + radius_nm, end.y - radius_nm),
            PointNM(end.x + radius_nm, end.y + radius_nm),
            PointNM(end.x - radius_nm, end.y + radius_nm),
            PointNM(start.x - radius_nm, start.y + radius_nm),
        )
    return (
        PointNM(start.x - radius_nm, start.y - radius_nm),
        PointNM(end.x - radius_nm, end.y - radius_nm),
        PointNM(end.x + radius_nm, end.y - radius_nm),
        PointNM(end.x + radius_nm, end.y + radius_nm),
        PointNM(start.x + radius_nm, start.y + radius_nm),
        PointNM(start.x - radius_nm, start.y + radius_nm),
    )


def _segment_core_extent(segment: Segment) -> _Rect | None:
    """Return a rectangle strictly inside an orthogonal track, or None when it is diagonal.

    Obstacle rectangles over-approximate copper so a clearance can never be understated.
    Connectivity must err the other way: claiming copper that is not there would assert an
    electrical connection the board does not have. The centreline caps are therefore dropped
    and the half width is floored, so the result is always a subset of the real stadium.
    """

    if segment.start.x != segment.end.x and segment.start.y != segment.end.y:
        return None
    half_width_nm = segment.width_nm // 2
    if segment.start.y == segment.end.y:
        return (
            min(segment.start.x, segment.end.x),
            segment.start.y - half_width_nm,
            max(segment.start.x, segment.end.x),
            segment.start.y + half_width_nm,
        )
    return (
        segment.start.x - half_width_nm,
        min(segment.start.y, segment.end.y),
        segment.start.x + half_width_nm,
        max(segment.start.y, segment.end.y),
    )


def _diagonal_segment_cores(segment: Segment, work: _WorkBudget) -> tuple[_Rect, ...] | None:
    """Return a connected chain of squares strictly inside a diagonal track, or None.

    The component model is axis-aligned, and a diagonal track is not; the answer is to cover it
    with axis-aligned squares that are each provably inside it and that provably overlap their
    neighbour, so the chain is one connected piece of real copper. Everything below is exact
    integer arithmetic, and every rounding step is toward the track's interior.

    Let ``radius`` be the floored half width, so the real copper contains every point within
    ``radius`` of the centreline. Centres are placed at ``start + (delta * i) // steps`` for
    ``i`` in ``0..steps``. Flooring each axis moves a centre less than one nanometre per axis
    off the exact point of the segment it approximates, so a centre is within ``sqrt(2) < 2``
    of the segment; ``_CORE_CENTRE_TOLERANCE_NM`` absorbs that. A square of half side ``s``
    centred there reaches at most ``s * sqrt(2)`` further, and distance to a set obeys the
    triangle inequality, so every point of the square is within ``2 + s * sqrt(2)`` of the
    segment. Choosing ``s`` with ``2 * s^2 <= (radius - 2)^2`` makes that at most ``radius``,
    which is the subset property. ``steps`` is chosen so consecutive centres differ by at most
    ``2 * s`` on each axis, which is exactly when two closed squares of half side ``s`` still
    touch, giving the overlap property. ``i = 0`` and ``i = steps`` land exactly on the
    endpoints, so the chain reaches the pads a diagonal stub is soldered to.

    Endpoints are canonically ordered first, so a segment recorded in either direction yields
    the identical chain, and squares are returned in ascending ``i``.
    """

    start, end = segment.start, segment.end
    if start.x == end.x or start.y == end.y:
        return None
    if (start.x, start.y) > (end.x, end.y):
        start, end = end, start
    radius_nm = segment.width_nm // 2
    if radius_nm <= _CORE_CENTRE_TOLERANCE_NM:
        return None
    reach_nm = radius_nm - _CORE_CENTRE_TOLERANCE_NM
    half_side_nm = isqrt(reach_nm * reach_nm // 2)
    if half_side_nm < 1:
        return None
    delta_x = end.x - start.x
    delta_y = end.y - start.y
    span_nm = 2 * half_side_nm
    steps = max(
        _ceil_div(abs(delta_x), span_nm),
        _ceil_div(abs(delta_y), span_nm),
        1,
    )
    cores: list[_Rect] = []
    for index in range(steps + 1):
        work.obstacle_check()
        centre_x = start.x + (delta_x * index) // steps
        centre_y = start.y + (delta_y * index) // steps
        cores.append(
            (
                centre_x - half_side_nm,
                centre_y - half_side_nm,
                centre_x + half_side_nm,
                centre_y + half_side_nm,
            )
        )
    return tuple(cores)


def _pad_core_extent(pad: Pad) -> tuple[int, int] | None:
    """Return half extents of a rectangle strictly inside the pad, or None when unmodeled."""

    if pad.rotation_udeg not in _AXIS_ALIGNED_ROTATIONS_UDEG:
        return None
    # The supported shapes are screened once, against a named set rather than inline members,
    # so a shape added to PadShape later fails closed here instead of silently reaching one of
    # the formulas below. Keeping this guard first also leaves the final branch unconditional,
    # which is what stops the tail being either an unreachable statement or a missing return
    # depending on how exhaustively the checker narrows the enum.
    if pad.shape not in _CORE_MODELED_PAD_SHAPES:
        return None
    size_x, size_y = pad.size_x_nm, pad.size_y_nm
    if pad.rotation_udeg // _QUARTER_ROTATION_UDEG % 2 == 1:
        size_x, size_y = size_y, size_x
    half_x_nm, half_y_nm = size_x // 2, size_y // 2
    if pad.shape is PadShape.RECT:
        return half_x_nm, half_y_nm
    if pad.shape is PadShape.ROUNDRECT:
        # Board IR guarantees 2 * radius <= min(size_x, size_y), so this stays non-negative
        # and spans the full pad width across its middle band.
        radius_nm = pad.roundrect_radius_nm
        if radius_nm is None:
            return None
        return half_x_nm, half_y_nm - radius_nm
    # CIRCLE and OVAL: a stadium contains its central rectangle, and a circle degenerates to
    # a centre line, both of which are strictly inside the pad.
    if half_x_nm >= half_y_nm:
        return half_x_nm - half_y_nm, half_y_nm
    return half_x_nm, half_y_nm - half_x_nm


def _pad_cores(pad: Pad) -> tuple[_Rect, ...] | None:
    """Return every rectangle used to model one pad's copper, or None when unmodeled.

    Most shapes need a single rectangle. A round pad is the exception: its central rectangle
    collapses to a zero-width bar through the centre, which is a legitimate subset of the
    copper but covers a lattice node only when the pad centre happens to land on one. That is
    harmless while pads are only contact-tested against neighbouring copper, and useless the
    moment a pad has to *offer* attachment points to a search. A round pad therefore also
    contributes its largest inscribed axis-aligned square, of half side ``isqrt(r^2 // 2)``,
    which satisfies ``2 * s^2 <= r^2`` and so lies inside the disc by exact integer arithmetic.

    The bar is kept alongside the square rather than replaced by it. The square is wider but
    shorter, so replacing would discard the pad's extreme top and bottom and could lose a
    contact the previous model found. Emitting both is a strict enlargement of what the pad
    offers, and since every rectangle here contains the pad centre they all share one
    component, so a pad is never split by its own decomposition.
    """

    extent = _pad_core_extent(pad)
    if extent is None:
        return None
    half_x_nm, half_y_nm = extent
    centre = pad.center
    core = (
        centre.x - half_x_nm,
        centre.y - half_y_nm,
        centre.x + half_x_nm,
        centre.y + half_y_nm,
    )
    if half_x_nm != 0 and half_y_nm != 0:
        return (core,)
    # A degenerate bar means a round pad; give it real area in both axes as well. The bar runs
    # along whichever axis is non-zero, so the perpendicular bar simply swaps the half extents.
    radius_nm = max(half_x_nm, half_y_nm)
    inscribed_nm = isqrt(radius_nm * radius_nm // 2)
    if inscribed_nm < 1:
        return (core,)
    return (
        core,
        (
            centre.x - half_y_nm,
            centre.y - half_x_nm,
            centre.x + half_y_nm,
            centre.y + half_x_nm,
        ),
        (
            centre.x - inscribed_nm,
            centre.y - inscribed_nm,
            centre.x + inscribed_nm,
            centre.y + inscribed_nm,
        ),
    )


def _rectangles_touch(first: _Rect, second: _Rect) -> bool:
    """Closed rectangle intersection; exact contact is an electrical connection."""

    return (
        first[0] <= second[2]
        and second[0] <= first[2]
        and first[1] <= second[3]
        and second[1] <= first[3]
    )


def _via_cores(via: Via) -> tuple[_Rect, ...] | None:
    """Return rectangles provably inside a through via's annulus, or None when degenerate.

    A via's copper is a ring: the drill hole in the middle is not copper, so the square
    inscribed in the outer circle would claim the one region that certainly is not there.
    The ring is covered instead by four axis-aligned rectangles, one on each side of the hole.

    With outer radius ``R`` floored from the diameter and hole radius ``r`` taken as the
    *ceiling* of half the drill, so the hole is over-stated and never encroached, the right
    rectangle spans ``x`` in ``[r, a]`` and ``y`` in ``[-b, b]``. Its far corners are the only
    points that can leave the disc, so ``a`` is chosen as ``isqrt(R^2 - b^2)``, which makes
    ``a^2 + b^2 <= R^2`` by exact integer arithmetic. The other three are the same rectangle
    rotated a quarter turn at a time.

    The four are mutually disjoint for a real via, and that is expected: the annulus is one
    piece of physical copper joined by a plated barrel, so a via's rectangles are unioned as an
    atomic object rather than by rectangle overlap. Board IR admits through vias only and
    validates that they span the complete copper stack, so a via joins every copper layer.
    """

    outer_nm = via.diameter_nm // 2
    hole_nm = (via.drill_nm + 1) // 2
    ring_nm = outer_nm - hole_nm
    if ring_nm < 2:
        return None
    half_span_nm = ring_nm // 2
    reach_nm = isqrt(outer_nm * outer_nm - half_span_nm * half_span_nm)
    if reach_nm <= hole_nm:
        return None
    centre = via.center
    return (
        (centre.x + hole_nm, centre.y - half_span_nm, centre.x + reach_nm, centre.y + half_span_nm),
        (centre.x - reach_nm, centre.y - half_span_nm, centre.x - hole_nm, centre.y + half_span_nm),
        (centre.x - half_span_nm, centre.y + hole_nm, centre.x + half_span_nm, centre.y + reach_nm),
        (centre.x - half_span_nm, centre.y - reach_nm, centre.x + half_span_nm, centre.y - hole_nm),
    )


@dataclass(frozen=True, slots=True)
class VerifiedFill:
    """One island of poured copper the caller has already bound to freshness evidence.

    The router never reads or refills a board; it accepts fill only as evidence someone else
    proved current, which keeps KiCad execution out of the search and out of Board IR.
    """

    net_id: str
    layer_id: str
    points: tuple[PointNM, ...]


@dataclass(frozen=True, slots=True)
class _CopperObject:
    """One same-net copper object, with the layers it occupies and the shape it offers.

    Most objects are covered by under-approximating rectangles. A verified fill island is
    carried as its exact polygon instead, because once freshness-bound it is KiCad's own
    authority on where that copper is rather than something this module has to approximate.
    """

    layer_ids: frozenset[str]
    cores: tuple[_Rect, ...] = ()
    polygon: tuple[PointNM, ...] = ()


def _polygon_touches_rect(points: tuple[PointNM, ...], rectangle: _Rect, work: _WorkBudget) -> bool:
    """Exact integer contact between a filled polygon and one axis-aligned rectangle."""

    minimum_x, minimum_y, maximum_x, maximum_y = rectangle
    corners = (
        PointNM(minimum_x, minimum_y),
        PointNM(maximum_x, minimum_y),
        PointNM(maximum_x, maximum_y),
        PointNM(minimum_x, maximum_y),
    )
    for index, edge_start in enumerate(points):
        work.obstacle_check()
        edge_end = points[(index + 1) % len(points)]
        if _inside_closed(edge_start, rectangle):
            return True
        for corner_index in range(4):
            if _segments_intersect(
                corners[corner_index], corners[(corner_index + 1) % 4], edge_start, edge_end
            ):
                return True
    # No edge crossed and no vertex landed inside, so containment is the only way left.
    inside = False
    for index, edge_start in enumerate(points):
        work.obstacle_check()
        if _ray_crosses_right(corners[0], edge_start, points[(index + 1) % len(points)]):
            inside = not inside
    return inside


def _multilayer_via_count(
    snapshot: BoardIRSnapshot,
    request: RouteRequest,
    pads: tuple[Pad, ...],
    work: _WorkBudget,
    verified_fill: tuple[VerifiedFill, ...] = (),
) -> tuple[int, int] | None:
    """Return via and fill-island counts when every pad is provably joined, else None.

    Routing stays a single-layer contract, but *recognising* an existing connection does not
    have to: a net whose pads meet through a back-layer detour is connected whether or not this
    router could have built that detour. Objects are matched only when they share a layer, and
    a through via shares every layer, which is exactly what makes it a joint.
    """

    content = snapshot.content
    layer_ids = frozenset(layer.id for layer in content.copper_layers)
    objects: list[_CopperObject] = []
    pad_indices: list[int] = []
    for pad in pads:
        cores = _pad_cores(pad)
        if cores is None:
            return None
        pad_indices.append(len(objects))
        objects.append(_CopperObject(layer_ids=frozenset(pad.layer_ids), cores=cores))
    for index, segment in enumerate(content.segments):
        if index % 64 == 0:
            work.checkpoint()
        if segment.net_id != request.net_id:
            continue
        orthogonal = _segment_core_extent(segment)
        cores = (orthogonal,) if orthogonal is not None else _diagonal_segment_cores(segment, work)
        if cores is None:
            return None
        objects.append(_CopperObject(layer_ids=frozenset({segment.layer_id}), cores=tuple(cores)))
    via_count = 0
    for index, via in enumerate(content.vias):
        if index % 64 == 0:
            work.checkpoint()
        if via.net_id != request.net_id:
            continue
        cores = _via_cores(via)
        if cores is None:
            return None
        via_count += 1
        objects.append(_CopperObject(layer_ids=layer_ids, cores=cores))
    fill_count = 0
    for island in verified_fill:
        work.checkpoint()
        if island.net_id != request.net_id:
            continue
        fill_count += 1
        objects.append(_CopperObject(layer_ids=frozenset({island.layer_id}), polygon=island.points))

    def touching(left: _CopperObject, right: _CopperObject) -> bool:
        if left.polygon and right.polygon:
            # KiCad emits one node per connected region, so two distinct islands of the same
            # net on the same layer are disjoint by construction.
            return False
        if left.polygon:
            return any(_polygon_touches_rect(left.polygon, core, work) for core in right.cores)
        if right.polygon:
            return any(_polygon_touches_rect(right.polygon, core, work) for core in left.cores)
        return any(_rectangles_touch(one, other) for one in left.cores for other in right.cores)

    parent = list(range(len(objects)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for first in range(len(objects)):
        for second in range(first + 1, len(objects)):
            work.obstacle_check()
            left, right = objects[first], objects[second]
            if not (left.layer_ids & right.layer_ids):
                continue
            if find(first) == find(second):
                continue
            if touching(left, right):
                parent[max(find(first), find(second))] = min(find(first), find(second))
    if len({find(index) for index in pad_indices}) != 1:
        return None
    return via_count, fill_count


def _component_roots(rectangles: tuple[_Rect, ...], work: _WorkBudget) -> tuple[int, ...]:
    """Return each rectangle's exact component root with deterministic bounded union-find."""

    parent = list(range(len(rectangles)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for first in range(len(rectangles)):
        for second in range(first + 1, len(rectangles)):
            work.obstacle_check()
            if not _rectangles_touch(rectangles[first], rectangles[second]):
                continue
            left, right = find(first), find(second)
            if left != right:
                # The lowest index always wins, so a component's root never depends on
                # discovery order and stays reproducible across runs.
                parent[max(left, right)] = min(left, right)
    return tuple(find(index) for index in range(len(rectangles)))


def _prepare(
    snapshot: BoardIRSnapshot,
    request: RouteRequest,
    work: _WorkBudget,
    verified_fill: tuple[VerifiedFill, ...] = (),
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
    blocking_pads: list[Pad] = []
    for index, pad in enumerate(content.pads):
        if index % 64 == 0:
            work.checkpoint()
        if pad.net_id == request.net_id:
            target_pads.append(pad)
        elif request.layer_id in pad.layer_ids:
            blocking_pads.append(pad)
    pads = tuple(sorted(target_pads, key=lambda pad: pad.id))
    # A net wider than two pads cannot be routed, but it can still be recognised as already
    # connected, so the pad-count refusal is deferred until after connectivity is decided.
    two_pin = len(pads) == 2
    if len(pads) < 2 or any(request.layer_id not in pad.layer_ids for pad in pads):
        raise _fail(
            RouteFailureCode.INVALID_TWO_PIN_NET,
            "the selected net must resolve to exactly two pads on the selected layer",
        )
    if two_pin and pads[0].center == pads[1].center:
        raise _fail(
            RouteFailureCode.UNSUPPORTED_GEOMETRY,
            "coincident route endpoints are outside the first-slice contract",
        )

    # A via or a zone on the routed net is copper this model does not represent, so it can
    # neither be routed around nor counted as connectivity. A two-pin net says so directly; a
    # wider one is refused for its pad count instead, which is the more useful fact about it.
    same_net_via = any(via.net_id == request.net_id for via in content.vias)
    work.checkpoint()
    for index, arc in enumerate(content.arcs):
        if index % 64 == 0:
            work.checkpoint()
        if arc.layer_id == request.layer_id:
            raise _fail(
                RouteFailureCode.UNSUPPORTED_GEOMETRY,
                "selected-layer arcs are outside the supported obstacle model",
            )
    blocking_zones: list[Zone] = []
    same_net_zone = False
    same_net_zone_present = any(
        zone.layer_id == request.layer_id and zone.net_id == request.net_id
        for zone in content.zones
    )
    # A same-net zone normally blocks any claim, because a cached fill may not describe the
    # board around it. Verified fill is the exception: the caller has already proved this
    # board's cache is what KiCad recomputes from it, so the poured copper counts as evidence.
    net_fill = tuple(island for island in verified_fill if island.net_id == request.net_id)
    if (same_net_via or net_fill) and (not same_net_zone_present or net_fill):
        # Routing stays single-layer, but a net already joined through its vias or its poured
        # copper needs no route at all, and refusing to look would report a problem the board
        # does not have.
        multilayer = _multilayer_via_count(snapshot, request, pads, work, net_fill)
        if multilayer is not None:
            multilayer_vias, multilayer_fill = multilayer
            raise _AlreadyConnectedError(
                start_pad_id=pads[0].id,
                end_pad_id=pads[-1].id,
                attachment_segments=sum(
                    1 for segment in content.segments if segment.net_id == request.net_id
                ),
                pad_count=len(pads),
                vias=multilayer_vias,
                fill_polygons=multilayer_fill,
                obstacle_checks=work.obstacle_checks,
            )
    if same_net_via and two_pin:
        raise _fail(
            RouteFailureCode.UNSUPPORTED_GEOMETRY,
            "the selected net already carries a via and is partially routed",
        )
    for index, zone in enumerate(content.zones):
        if index % 64 == 0:
            work.checkpoint()
        if zone.layer_id != request.layer_id:
            continue
        if zone.net_id == request.net_id:
            same_net_zone = True
            if two_pin:
                raise _fail(
                    RouteFailureCode.UNSUPPORTED_GEOMETRY,
                    "the selected net already carries a zone and is partially routed",
                )
            continue
        blocking_zones.append(zone)
    attachment_segments: list[Segment] = []
    for index, segment in enumerate(content.segments):
        if index % 64 == 0:
            work.checkpoint()
        if segment.layer_id != request.layer_id or segment.net_id != request.net_id:
            continue
        attachment_segments.append(segment)

    # Connectivity depends only on exactly modeled pad and same-net segment geometry, so it is
    # decided here rather than after the outline, keepout, and obstacle model. Nothing later in
    # preparation can change the answer, and reporting an unsupported outline for a net that
    # needs no routing at all would be less honest than reporting that it is already connected.
    source_cores: tuple[_Rect, ...] = ()
    target_cores: tuple[_Rect, ...] = ()
    routable_components: tuple[tuple[_Rect, ...], ...] | None = None
    # Two-pin nets skip the analysis when the net carries no copper, which keeps every board
    # the router already accepted on its original path. A wider net always needs it, because
    # its components are what routing merges.
    if attachment_segments or not two_pin:
        if len(attachment_segments) > request.settings.max_obstacles:
            raise _fail(
                RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                "the same-net attachment copper exceeds the configured obstacle budget",
            )
        pad_cores: list[_Rect] = []
        pad_core_offsets: list[int] = []
        for pad in pads:
            pad_core_group = _pad_cores(pad)
            if pad_core_group is None:
                if not two_pin:
                    # Unmodeled pad geometry cannot support a connectivity claim, and a wider
                    # net has the pad-count refusal waiting for it below regardless.
                    break
                raise _fail(
                    RouteFailureCode.UNSUPPORTED_GEOMETRY,
                    "a route endpoint pad is not modeled exactly for same-net attachment",
                )
            # Every rectangle of one pad contains that pad's centre, so they always share a
            # component; recording where each pad's group starts is enough to read its root.
            pad_core_offsets.append(len(pad_cores))
            pad_cores.extend(pad_core_group)
        # An orthogonal track is one exact rectangle; a diagonal one is a chain of squares that
        # is provably inside it and provably self-connected, so both reduce to axis-aligned
        # rectangles the component model already understands.
        core_list: list[_Rect] = []
        for segment in attachment_segments:
            orthogonal_core = _segment_core_extent(segment)
            if orthogonal_core is not None:
                core_list.append(orthogonal_core)
                continue
            diagonal_cores = _diagonal_segment_cores(segment, work)
            if diagonal_cores is None:
                raise _fail(
                    RouteFailureCode.UNSUPPORTED_GEOMETRY,
                    "a selected-layer track on the routed net is too narrow to model exactly",
                )
            core_list.extend(diagonal_cores)
        segment_cores = tuple(core_list)
        if len(pad_core_offsets) == len(pads):
            roots = _component_roots(tuple(pad_cores) + segment_cores, work)
            pad_roots = tuple(roots[offset] for offset in pad_core_offsets)
            # Vias and zones on the routed net are copper this model cannot see, so a net that
            # carries them is never claimed connected however its segments happen to fall.
            component_of_root: dict[int, list[int]] = {}
            for offset, root in enumerate(roots):
                component_of_root.setdefault(root, []).append(offset)
            all_cores = tuple(pad_cores) + segment_cores
            routable_components = tuple(
                tuple(all_cores[index] for index in indices)
                for _, indices in sorted(component_of_root.items())
                # Only components that hold at least one pad have to be merged; stray copper
                # that touches no pad of this net is neither an obstacle nor a destination.
                if any(index in set(pad_core_offsets) for index in indices)
            )
            if len(set(pad_roots)) == 1 and not same_net_via and not same_net_zone:
                raise _AlreadyConnectedError(
                    start_pad_id=pads[0].id,
                    end_pad_id=pads[-1].id,
                    attachment_segments=len(attachment_segments),
                    pad_count=len(pads),
                    obstacle_checks=work.obstacle_checks,
                )
            if two_pin:
                segment_roots = roots[len(pad_cores) :]
                source_cores = tuple(
                    core
                    for core, root in zip(segment_cores, segment_roots, strict=True)
                    if root == pad_roots[0]
                )
                target_cores = tuple(
                    core
                    for core, root in zip(segment_cores, segment_roots, strict=True)
                    if root == pad_roots[1]
                )

    if not two_pin and (routable_components is None or same_net_via or same_net_zone):
        raise _fail(
            RouteFailureCode.INVALID_TWO_PIN_NET,
            "the selected net must resolve to exactly two pads on the selected layer",
        )
    start_pad, end_pad = pads[0], pads[-1]

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

    rect_obstacles: list[_Rect] = []
    polygon_obstacles: list[_PolygonObstacle] = []

    def ensure_obstacle_capacity() -> None:
        # Same-net attachment copper is a modeled selected-layer object too, so it shares the
        # object ceiling with every obstacle rather than escaping it.
        modeled = len(attachment_segments) + len(rect_obstacles) + len(polygon_obstacles)
        if modeled >= request.settings.max_obstacles:
            raise _fail(
                RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                "the selected-layer obstacle count exceeds the configured obstacle budget",
            )

    def add_rect_obstacle(rectangle: _Rect, clearance_nm: int) -> None:
        """Inflate one exact rectangle by the route half-width plus the governing clearance."""

        ensure_obstacle_capacity()
        margin_nm = half_width_nm + clearance_nm
        rect_obstacles.append(_inflate_rectangle(rectangle, margin_nm))

    def governing_clearance_nm(net_id: str | None) -> int:
        """Use the stricter of the routed net's clearance and the obstacle net's clearance."""

        other = _net_clearance_nm(snapshot, net_id, work)
        if other is None:
            return net_class.clearance_nm
        return max(net_class.clearance_nm, other)

    for index, keepout in enumerate(content.keepouts):
        if index % 64 == 0:
            work.checkpoint()
        if request.layer_id not in keepout.layer_ids or not keepout.prohibit_tracks:
            continue
        rectangle = _rectangle(keepout.boundary)
        if rectangle is not None:
            # An axis-aligned rectangle keeps its exact square-cornered inflation. The polygon
            # path offsets by Euclidean distance and so rounds corners, which would be a
            # different — and looser — obstacle for the same board.
            add_rect_obstacle(rectangle, net_class.clearance_nm)
            continue
        # A keepout carries no net, so there is no second class clearance to be stricter than:
        # the routed net's own class clearance is the only rule that applies.
        ensure_obstacle_capacity()
        keepout_points = keepout.boundary.points
        polygon_obstacles.append(
            _PolygonObstacle(
                source_id=keepout.id,
                points=keepout_points,
                bounds=_polygon_bounds(keepout_points, work),
                margin_nm=half_width_nm + net_class.clearance_nm,
            )
        )

    for index, pad in enumerate(blocking_pads):
        if index % 64 == 0:
            work.checkpoint()
        pad_extent = _pad_extent(pad)
        if pad_extent is None:
            raise _fail(
                RouteFailureCode.UNSUPPORTED_GEOMETRY,
                "a selected-layer pad is rotated off axis and is not modeled exactly",
            )
        half_x_nm, half_y_nm = pad_extent
        add_rect_obstacle(
            (
                pad.center.x - half_x_nm,
                pad.center.y - half_y_nm,
                pad.center.x + half_x_nm,
                pad.center.y + half_y_nm,
            ),
            governing_clearance_nm(pad.net_id),
        )

    for index, segment in enumerate(content.segments):
        if index % 64 == 0:
            work.checkpoint()
        if segment.layer_id != request.layer_id:
            continue
        if segment.net_id == request.net_id:
            # Classified above as attachment copper; the routed net never blocks itself.
            continue
        segment_extent = _segment_extent(segment)
        if segment_extent is not None:
            add_rect_obstacle(segment_extent, governing_clearance_nm(segment.net_id))
            continue
        # A diagonal foreign track is a conservative polygon envelope rather than a board-level
        # refusal. The margin is the same rule the orthogonal path uses, and offsetting the
        # envelope by it is a superset of offsetting the true stadium, because the envelope
        # already contains the stadium.
        envelope = _segment_envelope(segment)
        if envelope is None:  # pragma: no cover - orthogonal is handled by the fast path above
            raise _fail(
                RouteFailureCode.UNSUPPORTED_GEOMETRY,
                "a selected-layer segment is not modeled exactly",
            )
        ensure_obstacle_capacity()
        polygon_obstacles.append(
            _PolygonObstacle(
                source_id=segment.id,
                points=envelope,
                bounds=_polygon_bounds(envelope, work),
                margin_nm=half_width_nm + governing_clearance_nm(segment.net_id),
            )
        )

    for index, via in enumerate(content.vias):
        if index % 64 == 0:
            work.checkpoint()
        # Board IR v0.1 admits through vias only, so every via crosses the selected layer.
        half_span_nm = (via.diameter_nm + 1) // 2
        add_rect_obstacle(
            (
                via.center.x - half_span_nm,
                via.center.y - half_span_nm,
                via.center.x + half_span_nm,
                via.center.y + half_span_nm,
            ),
            governing_clearance_nm(via.net_id),
        )

    for index, zone in enumerate(blocking_zones):
        if index % 64 == 0:
            work.checkpoint()
        ensure_obstacle_capacity()
        points = zone.boundary.points
        clearance_nm = max(governing_clearance_nm(zone.net_id), zone.clearance_nm)
        polygon_obstacles.append(
            _PolygonObstacle(
                source_id=zone.id,
                points=points,
                bounds=_polygon_bounds(points, work),
                margin_nm=half_width_nm + clearance_nm,
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

    def attachment_nodes(rectangle: _Rect) -> tuple[_Node, ...]:
        """Enumerate the lattice nodes one attachment rectangle covers, by index range.

        Scanning the lattice and testing every rectangle would cost the node budget times the
        obstacle budget. Solving the covered index range directly costs one charge per
        rectangle plus one per emitted node, so the seed set stays bounded by the same
        obstacle-check budget as every other geometric relation.
        """

        work.obstacle_check()
        low_ix = max(min_ix, _ceil_div(rectangle[0] - start_pad.center.x, step))
        high_ix = min(max_ix, (rectangle[2] - start_pad.center.x) // step)
        low_iy = max(min_iy, _ceil_div(rectangle[1] - start_pad.center.y, step))
        high_iy = min(max_iy, (rectangle[3] - start_pad.center.y) // step)
        nodes: list[_Node] = []
        for node_ix in range(low_ix, high_ix + 1):
            for node_iy in range(low_iy, high_iy + 1):
                work.obstacle_check()
                nodes.append((node_ix, node_iy))
        return tuple(nodes)

    # Pads contribute only their centre node. Their cores decide connectivity, but seeding from
    # every node beneath a pad would change the geometry of every board that routes today.
    source_nodes: set[_Node] = {(0, 0)}
    for rectangle in source_cores:
        source_nodes.update(attachment_nodes(rectangle))
    target_nodes: set[_Node] = {(goal_ix, goal_iy)}
    for rectangle in target_cores:
        target_nodes.update(attachment_nodes(rectangle))

    problem = _Problem(
        snapshot=snapshot,
        request=request,
        start_pad=start_pad,
        end_pad=end_pad,
        width_nm=net_class.track_width_nm,
        clearance_nm=net_class.clearance_nm,
        safe_board=safe_board,
        rect_obstacles=tuple(sorted(rect_obstacles)),
        polygon_obstacles=tuple(sorted(polygon_obstacles, key=_polygon_obstacle_sort_key)),
        min_ix=min_ix,
        max_ix=max_ix,
        min_iy=min_iy,
        max_iy=max_iy,
        goal_ix=goal_ix,
        goal_iy=goal_iy,
        pad_count=len(pads),
        components=routable_components or (),
        source_nodes=frozenset(source_nodes),
        target_nodes=frozenset(target_nodes),
        target_min_ix=min(node[0] for node in target_nodes),
        target_max_ix=max(node[0] for node in target_nodes),
        target_min_iy=min(node[1] for node in target_nodes),
        target_max_iy=max(node[1] for node in target_nodes),
    )
    for obstacle in problem.rect_obstacles:
        work.obstacle_check()
        if _inside_open(start_pad.center, obstacle) or _inside_open(end_pad.center, obstacle):
            raise _fail(
                RouteFailureCode.NO_PATH,
                "a route endpoint is blocked by a selected-layer obstacle",
                obstacle_checks=work.obstacle_checks,
            )
    for polygon in problem.polygon_obstacles:
        if _point_within_polygon_offset(
            start_pad.center,
            polygon,
            polygon.margin_nm,
            work,
        ) or _point_within_polygon_offset(
            end_pad.center,
            polygon,
            polygon.margin_nm,
            work,
        ):
            raise _fail(
                RouteFailureCode.NO_PATH,
                "a route endpoint is blocked by a selected-layer obstacle",
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
    """Return the Manhattan distance to the target bounding box, in nanometres.

    Every target node lies inside that box, so this never exceeds the Manhattan distance to
    the nearest target; each grid edge costs at least one grid step and the bend and proximity
    terms are non-negative, so the estimate stays admissible. One unit step changes it by at
    most one step, so it is also consistent and no state is ever reopened. With a single
    target the box is degenerate and this is exactly the original two-pin heuristic.
    """

    delta_ix = max(problem.target_min_ix - ix, 0, ix - problem.target_max_ix)
    delta_iy = max(problem.target_min_iy - iy, 0, iy - problem.target_max_iy)
    return (delta_ix + delta_iy) * problem.request.settings.grid_step_nm


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
            "paths": [
                [{"x_nm": point.x, "y_nm": point.y} for point in path.vertices]
                for path in candidate.patch.paths
            ],
            "width_nm": candidate.patch.width_nm,
        },
        "ordering_policy": candidate.ordering_policy,
        "pad_count": candidate.pad_count,
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


@dataclass(frozen=True, slots=True)
class _Leg:
    """One routed merge between two components, before it becomes part of a patch."""

    vertices: tuple[PointNM, ...]
    bend_count: int
    proximity_steps: int
    expanded_states: int
    peak_frontier_states: int


def _build_candidate(
    problem: _Problem,
    legs: tuple[_Leg, ...],
    *,
    pad_count: int,
    ordering_policy: str,
    work: _WorkBudget,
) -> RouteCandidate:
    settings = problem.request.settings
    paths = tuple(RoutePath(vertices=leg.vertices) for leg in legs)
    length_nm = sum(path.length_nm for path in paths)
    bend_count = sum(leg.bend_count for leg in legs)
    proximity_steps = sum(leg.proximity_steps for leg in legs)
    expanded_states = sum(leg.expanded_states for leg in legs)
    peak_frontier_states = max(leg.peak_frontier_states for leg in legs)
    if bend_count != sum(path.bend_count for path in paths):
        raise RuntimeError("internal route bend accounting is inconsistent")
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
        pad_count=pad_count,
        ordering_policy=ordering_policy,
        patch=RoutePatch(
            net_id=problem.request.net_id,
            layer_id=problem.request.layer_id,
            width_nm=problem.width_nm,
            paths=paths,
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


def _start_states(problem: _Problem) -> tuple[_State, ...]:
    """Return every seed state in the heap's own ordering, so counters stay reproducible."""

    return tuple(
        (node[0], node[1], _NO_DIRECTION)
        for node in sorted(problem.source_nodes, key=lambda node: (node[1], node[0]))
    )


def _search(problem: _Problem, work: _WorkBudget) -> _Leg:
    settings = problem.request.settings
    start_states = _start_states(problem)
    start_state_set = frozenset(start_states)
    start_score: _Score = (0, 0, 0)
    best: dict[_State, _Score] = dict.fromkeys(start_states, start_score)
    parents: dict[_State, _State] = {}
    frontier: list[tuple[int, int, int, int, int, int, int, int, _State]] = []
    counter = 0
    for state in start_states:
        heapq.heappush(
            frontier,
            (
                _heuristic(problem, state[0], state[1]),
                0,
                0,
                0,
                state[1],
                state[0],
                _NO_DIRECTION,
                counter,
                state,
            ),
        )
        counter += 1
    expanded_states = 0
    peak_frontier_states = len(start_states)

    while frontier:
        work.checkpoint()
        _, g_cost, bends, proximity_steps, iy, ix, direction, _, state = heapq.heappop(frontier)
        if best.get(state) != (g_cost, bends, proximity_steps):
            continue
        if (ix, iy) in problem.target_nodes:
            route_states = [state]
            while route_states[-1] not in start_state_set:
                route_states.append(parents[route_states[-1]])
            route_states.reverse()
            points = tuple(_point(problem, item[0], item[1]) for item in route_states)
            compressed = _compress(points)
            start_node = (route_states[0][0], route_states[0][1])
            if (
                start_node not in problem.source_nodes
                or (ix, iy) not in problem.target_nodes
                or compressed[0] != _point(problem, *start_node)
                or compressed[-1] != _point(problem, ix, iy)
            ):
                raise RuntimeError("internal route reconstruction left its attachment copper")
            for edge_start, edge_end in pairwise(compressed):
                if not _edge_is_legal(edge_start, edge_end, problem, work):
                    raise RuntimeError("internal route post-validation rejected generated geometry")
            if bends != len(compressed) - 2:
                raise RuntimeError("internal route bend accounting is inconsistent")
            return _Leg(
                vertices=compressed,
                bend_count=bends,
                proximity_steps=proximity_steps,
                expanded_states=expanded_states,
                peak_frontier_states=peak_frontier_states,
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


def _nodes_for_cores(
    problem: _Problem, cores: tuple[_Rect, ...], work: _WorkBudget
) -> frozenset[_Node]:
    """Map every lattice node covered by a component's copper, by exact index range."""

    step = problem.request.settings.grid_step_nm
    anchor = problem.start_pad.center
    nodes: set[_Node] = set()
    for rectangle in cores:
        work.obstacle_check()
        low_ix = max(problem.min_ix, _ceil_div(rectangle[0] - anchor.x, step))
        high_ix = min(problem.max_ix, (rectangle[2] - anchor.x) // step)
        low_iy = max(problem.min_iy, _ceil_div(rectangle[1] - anchor.y, step))
        high_iy = min(problem.max_iy, (rectangle[3] - anchor.y) // step)
        for node_ix in range(low_ix, high_ix + 1):
            for node_iy in range(low_iy, high_iy + 1):
                work.obstacle_check()
                nodes.add((node_ix, node_iy))
    return frozenset(nodes)


def _component_bounds(cores: tuple[_Rect, ...]) -> _Rect:
    return (
        min(core[0] for core in cores),
        min(core[1] for core in cores),
        max(core[2] for core in cores),
        max(core[3] for core in cores),
    )


def _rectilinear_gap(first: _Rect, second: _Rect) -> int:
    """Exact integer Manhattan gap between two axis-aligned bounding boxes."""

    gap_x = max(second[0] - first[2], first[0] - second[2], 0)
    gap_y = max(second[1] - first[3], first[1] - second[3], 0)
    return gap_x + gap_y


def _merge_order(
    components: tuple[tuple[_Rect, ...], ...], work: _WorkBudget
) -> tuple[tuple[int, int], ...]:
    """Return the component pairs to merge, as a deterministic minimum spanning tree.

    Edges are weighted by the exact rectilinear gap between component bounding boxes and
    ordered by ``(gap, lower index, higher index)``, so the tree is a pure function of the
    snapshot. This is a spanning tree over components, not a Steiner tree: it bounds total
    added copper without claiming to minimise it.
    """

    bounds = [_component_bounds(cores) for cores in components]
    edges: list[tuple[int, int, int]] = []
    for first in range(len(components)):
        for second in range(first + 1, len(components)):
            work.obstacle_check()
            edges.append((_rectilinear_gap(bounds[first], bounds[second]), first, second))
    edges.sort()
    parent = list(range(len(components)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    order: list[tuple[int, int]] = []
    for _, first, second in edges:
        left, right = find(first), find(second)
        if left == right:
            continue
        parent[max(left, right)] = min(left, right)
        order.append((first, second))
    return tuple(order)


def _emitted_cores(leg: _Leg, half_width_nm: int) -> tuple[_Rect, ...]:
    """Return exact cores for one emitted leg, which is orthogonal by construction."""

    cores: list[_Rect] = []
    for start, end in pairwise(leg.vertices):
        if start.y == end.y:
            cores.append(
                (
                    min(start.x, end.x),
                    start.y - half_width_nm,
                    max(start.x, end.x),
                    start.y + half_width_nm,
                )
            )
        else:
            cores.append(
                (
                    start.x - half_width_nm,
                    min(start.y, end.y),
                    start.x + half_width_nm,
                    max(start.y, end.y),
                )
            )
    return tuple(cores)


def _route_tree(problem: _Problem, work: _WorkBudget) -> RouteCandidate:
    """Merge the net's components one leg at a time until a single component remains."""

    if problem.pad_count == 2:
        return _build_candidate(
            problem,
            (_search(problem, work),),
            pad_count=2,
            ordering_policy=SINGLE_PATH_ORDERING,
            work=work,
        )

    # Component index -> its copper. Merging rewrites both entries to the union, so the
    # bookkeeping stays a plain dictionary keyed by the original indices.
    groups: dict[int, tuple[_Rect, ...]] = dict(enumerate(problem.components))
    parent = list(range(len(problem.components)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    half_width_nm = problem.width_nm // 2
    legs: list[_Leg] = []
    for first, second in _merge_order(problem.components, work):
        left, right = find(first), find(second)
        if left == right:
            continue
        source_cores, target_cores = groups[left], groups[right]
        source_nodes = _nodes_for_cores(problem, source_cores, work)
        target_nodes = _nodes_for_cores(problem, target_cores, work)
        if not source_nodes or not target_nodes:
            raise _fail(
                RouteFailureCode.NO_PATH,
                "a pad cannot be reached from the bounded routing lattice",
                expanded_states=work.expanded_states,
                obstacle_checks=work.obstacle_checks,
            )
        if source_nodes & target_nodes:
            raise RuntimeError("internal component analysis produced overlapping components")
        leg = _search(
            replace(
                problem,
                source_nodes=source_nodes,
                target_nodes=target_nodes,
                target_min_ix=min(node[0] for node in target_nodes),
                target_max_ix=max(node[0] for node in target_nodes),
                target_min_iy=min(node[1] for node in target_nodes),
                target_max_iy=max(node[1] for node in target_nodes),
            ),
            work,
        )
        # A leg that does not begin on the copper it claimed to leave would make the merge a
        # fiction, so the emitted start is checked against the source component itself.
        head = leg.vertices[0]
        if not any(
            core[0] <= head.x <= core[2] and core[1] <= head.y <= core[3] for core in source_cores
        ):
            raise RuntimeError("internal leg does not begin on its source component")
        legs.append(leg)
        merged = source_cores + target_cores + _emitted_cores(leg, half_width_nm)
        winner, loser = min(left, right), max(left, right)
        parent[loser] = winner
        groups[winner] = merged
        del groups[loser]
    if len(groups) != 1:
        raise RuntimeError("internal merge order left the net disconnected")
    return _build_candidate(
        problem,
        tuple(legs),
        pad_count=problem.pad_count,
        ordering_policy=COMPONENT_MST_ORDERING,
        work=work,
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
        verified_fill: tuple[VerifiedFill, ...] = (),
    ) -> RouteResult:
        """Return an unapplied candidate, an already-connected record, or an expected failure.

        ``verified_fill`` is poured copper a caller has already bound to freshness evidence.
        The router never reads a board or runs KiCad, so fill it was not handed is fill that
        does not exist as far as any claim here is concerned.
        """

        validated = _validate_public_inputs(snapshot, request, cancelled)
        if isinstance(validated, RouteResult):
            return validated
        checked_snapshot, checked_request, cancellation_check = validated
        work = _WorkBudget(settings=checked_request.settings, cancelled=cancellation_check)
        try:
            problem = _prepare(checked_snapshot, checked_request, work, verified_fill)
            return RouteResult(candidate=_route_tree(problem, work))
        except _AlreadyConnectedError as connection:
            return RouteResult(
                connected=RouteConnection(
                    base_revision=checked_snapshot.snapshot_digest,
                    start_pad_id=connection.start_pad_id,
                    end_pad_id=connection.end_pad_id,
                    attachment_segments=connection.attachment_segments,
                    component_objects=(
                        connection.attachment_segments
                        + connection.pad_count
                        + connection.vias
                        + connection.fill_polygons
                    ),
                    pad_count=connection.pad_count,
                    vias=connection.vias,
                    fill_polygons=connection.fill_polygons,
                    obstacle_checks=connection.obstacle_checks,
                )
            )
        except _ExpectedFailureError as failure:
            return _result_failure(failure)
