"""Deterministic, bounded, integer-only two-pin A* reference router."""

from __future__ import annotations

import hashlib
import heapq
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from math import gcd, isqrt
from typing import TypeAlias, cast

from copper_mcp.board_ir import (
    UDEG_PER_DEGREE,
    Arc,
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
    BATCHED_ONE_STEINER_ORDERING,
    COMPONENT_MST_ORDERING,
    MAX_REPRESENTABLE_STEP_NM,
    SINGLE_PATH_ORDERING,
    AStarSettings,
    CancellationCheck,
    CongestionPenalty,
    OffGridEvidence,
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
from copper_mcp.routing.spatial_index import ConservativeSpatialIndex, SpatialIndexEntry
from copper_mcp.routing.steiner_ordering import batched_one_steiner_order

ROUTER_VERSION = "astar-grid/0.7.0"
ROUTING_POLICY = "orthogonal-a-star-spatial-index-v1"
_PRE_BATCHED_ROUTER_VERSION = "astar-grid/0.4.0"
_PRE_SPATIAL_INDEX_ROUTER_VERSION = "astar-grid/0.5.0"
#: The last version whose obstacle model covered the whole board and whose object ceiling was a
#: single `max_obstacles` shared by same-net copper. Candidates recorded under it replay with
#: the same search behaviour, but their recorded settings predate `region_margin_nm`, so on a
#: board larger than the region they do not reproduce byte-for-byte. See ADR-0087.
_PRE_REGION_SCOPED_ROUTER_VERSION = "astar-grid/0.6.0"
_PRE_SPATIAL_INDEX_POLICY = "orthogonal-a-star-v1"
_EMPTY_DIGEST = f"sha256:{'0' * 64}"
_SPATIAL_INDEX_MIN_ENTRIES = 8
_MAX_CONGESTION_PENALTY = 1_000_000_000

_Rect: TypeAlias = tuple[int, int, int, int]
_Node: TypeAlias = tuple[int, int]
_State: TypeAlias = tuple[int, int, int]
# (policy cost, physical geometry cost, bends, proximity steps).  With no policy hook the first
# two terms are equal, preserving the historical A* ordering exactly.  Congestion only changes
# search ordering; candidate RouteCost remains the physical geometry decomposition.
_Score: TypeAlias = tuple[int, int, int, int]
_DIRECTIONS: tuple[tuple[int, int], ...] = (
    (1, 0),  # east
    (0, -1),  # north
    (-1, 0),  # west
    (0, 1),  # south
)
_NO_DIRECTION = len(_DIRECTIONS)


@dataclass(frozen=True, slots=True)
class _RouterIdentity:
    """One supported deterministic router behavior recorded in candidate identity."""

    router_version: str
    policy: str
    use_spatial_index: bool


_CURRENT_ROUTER_IDENTITY = _RouterIdentity(ROUTER_VERSION, ROUTING_POLICY, True)
_REPLAY_IDENTITIES = {
    (ROUTER_VERSION, ROUTING_POLICY): _CURRENT_ROUTER_IDENTITY,
    (_PRE_REGION_SCOPED_ROUTER_VERSION, ROUTING_POLICY): _RouterIdentity(
        _PRE_REGION_SCOPED_ROUTER_VERSION,
        ROUTING_POLICY,
        True,
    ),
    (_PRE_SPATIAL_INDEX_ROUTER_VERSION, _PRE_SPATIAL_INDEX_POLICY): _RouterIdentity(
        _PRE_SPATIAL_INDEX_ROUTER_VERSION,
        _PRE_SPATIAL_INDEX_POLICY,
        False,
    ),
    (_PRE_BATCHED_ROUTER_VERSION, _PRE_SPATIAL_INDEX_POLICY): _RouterIdentity(
        _PRE_BATCHED_ROUTER_VERSION,
        _PRE_SPATIAL_INDEX_POLICY,
        False,
    ),
}


def _ordering_policy_for(identity: _RouterIdentity, pad_count: int) -> str:
    """Return the only ordering policy this recorded router behavior can reproduce."""

    if pad_count == 2:
        return SINGLE_PATH_ORDERING
    if identity.router_version == _PRE_BATCHED_ROUTER_VERSION:
        return COMPONENT_MST_ORDERING
    if pad_count <= 9:
        return BATCHED_ONE_STEINER_ORDERING
    return COMPONENT_MST_ORDERING


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
    rect_index: ConservativeSpatialIndex[_Rect]
    polygon_index: ConservativeSpatialIndex[_PolygonObstacle]
    use_spatial_index: bool
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
    #: True when the routing region is a proper subset of the safe board, so the obstacle
    #: model covers the region rather than the whole board. An exhausted search is then a
    #: statement about the region, not about the board, and refuses under its own code.
    region_scoped: bool = False
    #: The binding of the verified fill this problem's obstacle model was built from, which
    #: every candidate built from it records.  ``None`` means the model is the conservative
    #: zone envelope.
    fill_binding: str | None = None
    congestion_penalty: CongestionPenalty | None = None


@dataclass(frozen=True, slots=True)
class _ExpectedFailureError(Exception):
    code: RouteFailureCode
    message: str
    expanded_states: int = 0
    obstacle_checks: int = 0
    off_grid: OffGridEvidence | None = None


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
        cancelled = False
        if self.cancelled is not None:
            try:
                cancelled = bool(self.cancelled())
            except Exception:  # pragma: no cover - defensive untrusted callback boundary
                cancelled = True
        if cancelled:
            raise _fail(
                RouteFailureCode.CANCELLED,
                "the routing search was cancelled",
                expanded_states=self.expanded_states,
                obstacle_checks=self.obstacle_checks,
            )

    def obstacle_check(self) -> None:
        if self.obstacle_checks >= self.settings.max_obstacle_checks:
            raise _fail(
                RouteFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED,
                "the routing search reached its obstacle-check budget "
                f"(max_obstacle_checks={self.settings.max_obstacle_checks})",
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
    off_grid: OffGridEvidence | None = None,
) -> _ExpectedFailureError:
    return _ExpectedFailureError(code, message, expanded_states, obstacle_checks, off_grid)


def _result_failure(failure: _ExpectedFailureError) -> RouteResult:
    return RouteResult(
        diagnostic=RouteDiagnostic(
            code=failure.code,
            message=failure.message,
            expanded_states=failure.expanded_states,
            obstacle_checks=failure.obstacle_checks,
            off_grid=failure.off_grid,
        )
    )


def _nearest_lattice_miss(delta_nm: int, step_nm: int) -> int:
    """Signed nanometres from the nearest lattice line to a pad centre offset by ``delta_nm``.

    Exact integer arithmetic throughout: ``remainder`` is the distance up from the lattice line
    below, ``step_nm - remainder`` the distance down from the one above, and the comparison is
    doubled rather than halved so no division rounds. Ties go to the lower line, which makes the
    result a function of the inputs alone rather than of how the tie was broken.
    """

    remainder = delta_nm % step_nm
    return remainder if 2 * remainder <= step_nm else remainder - step_nm


#: Fixed lead sentence of every ``off_grid`` message, carrying no board-derived value.
#:
#: The geometry that follows it is per-request evidence a caller is entitled to (ADR-0093), but
#: it is still derived from the caller's board, so anything that records refusal messages into a
#: durable artifact truncates here rather than storing the numbers. `scripts/
#: benchmark_real_board_capability.py` imports this constant for exactly that reason.
OFF_GRID_MESSAGE_LEAD = "a pad centre does not lie on the requested routing lattice"


def _off_grid_evidence(
    start_pad: Pad,
    end_pad: Pad,
    delta_x: int,
    delta_y: int,
    step_nm: int,
) -> OffGridEvidence:
    """Measure one off-lattice pad centre against the lattice anchored at the other pad.

    The greatest common divisor of the two deltas is the largest step at which the centre is
    representable at all. It is reported because it is the number that separates the two very
    different situations a caller can be in: a coarse-but-usable divisor says a finer lattice
    would include this pad, and a divisor of a few nanometres says no lattice a router can hold
    would, so the pad has to move. It does not promise that routing succeeds at that step, and
    on measured real boards it usually does not (B-100).

    That divisor is bounded by the larger pad-centre delta, not by the lattice, so on a board
    placing the two pads near opposite legal Board IR coordinate extremes it exceeds the
    JSON-safe integer the contract publishes -- ``2 * (2**53 - 1)`` is the worst case. It is
    withheld as ``None`` there rather than clamped, because a clamped divisor would be a false
    claim about the board and this one is a refusal a caller must still receive as a typed
    refusal. Every other field is bounded by the request's own settings and stays exact.
    """

    divisor = gcd(abs(delta_x), abs(delta_y))
    return OffGridEvidence(
        pad_id=end_pad.id,
        anchor_pad_id=start_pad.id,
        grid_step_nm=step_nm,
        miss_x_nm=_nearest_lattice_miss(delta_x, step_nm),
        miss_y_nm=_nearest_lattice_miss(delta_y, step_nm),
        largest_representable_step_nm=divisor if divisor <= MAX_REPRESENTABLE_STEP_NM else None,
    )


def _ceil_div(numerator: int, denominator: int) -> int:
    return -((-numerator) // denominator)


def _routing_region(
    pads: tuple[Pad, ...],
    attachment_segments: Sequence[Segment],
    safe_board: _Rect,
    margin_nm: int,
) -> _Rect:
    """Return the corridor the lattice may occupy, clipped to the safe board.

    The corridor is the axis-aligned envelope of everything the route must join — the routed
    net's pad centres and its selected-layer attachment copper — widened by ``margin_nm`` on
    every side. Widening rather than hugging is the point: the margin is the detour room the
    search is allowed, and it is a setting because how much room a board needs is a property of
    the board, not of this module.

    Clipping to ``safe_board`` means a board smaller than the margin yields the whole board and
    the region stops existing as a distinct concept, which is why every small fixture routes
    byte-identically to before this became region-scoped.
    """

    min_x = min(pad.center.x for pad in pads)
    min_y = min(pad.center.y for pad in pads)
    max_x = max(pad.center.x for pad in pads)
    max_y = max(pad.center.y for pad in pads)
    for segment in attachment_segments:
        min_x = min(min_x, segment.start.x, segment.end.x)
        min_y = min(min_y, segment.start.y, segment.end.y)
        max_x = max(max_x, segment.start.x, segment.end.x)
        max_y = max(max_y, segment.start.y, segment.end.y)
    return (
        max(safe_board[0], min_x - margin_nm),
        max(safe_board[1], min_y - margin_nm),
        min(safe_board[2], max_x + margin_nm),
        min(safe_board[3], max_y + margin_nm),
    )


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


def _point_bounds(first: PointNM, second: PointNM, padding_nm: int = 0) -> _Rect:
    """Return a closed AABB for two points, conservatively padded in every direction."""

    if isinstance(padding_nm, bool) or padding_nm < 0:
        raise ValueError("point-bound padding must be a non-negative integer")
    return (
        min(first.x, second.x) - padding_nm,
        min(first.y, second.y) - padding_nm,
        max(first.x, second.x) + padding_nm,
        max(first.y, second.y) + padding_nm,
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


def _rect_contains(outer: _Rect, inner: _Rect) -> bool:
    """Exact integer closed containment of one axis-aligned rectangle in another."""

    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


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
    if problem.use_spatial_index:
        work.checkpoint()
        edge_bounds = _point_bounds(start, end)
        rect_obstacles = problem.rect_index.query(edge_bounds)
        polygon_obstacles = problem.polygon_index.query(edge_bounds)
    else:
        rect_obstacles = problem.rect_obstacles
        polygon_obstacles = problem.polygon_obstacles
    for obstacle in rect_obstacles:
        work.obstacle_check()
        if _edge_enters_open_rectangle(start, end, obstacle):
            return False
    for polygon in polygon_obstacles:
        if _edge_within_polygon_offset(start, end, polygon, work):
            return False
    return True


def _proximity_step(point: PointNM, problem: _Problem, work: _WorkBudget) -> int:
    step = problem.request.settings.grid_step_nm
    min_x, min_y, max_x, max_y = problem.safe_board
    if min(point.x - min_x, max_x - point.x, point.y - min_y, max_y - point.y) < step:
        return 1
    if problem.use_spatial_index:
        work.checkpoint()
        proximity_bounds = (point.x - step, point.y - step, point.x + step, point.y + step)
        rect_obstacles = problem.rect_index.query(proximity_bounds)
        polygon_obstacles = problem.polygon_index.query(proximity_bounds)
    else:
        rect_obstacles = problem.rect_obstacles
        polygon_obstacles = problem.polygon_obstacles
    for obstacle in rect_obstacles:
        work.obstacle_check()
        obstacle_min_x, obstacle_min_y, obstacle_max_x, obstacle_max_y = obstacle
        dx = max(obstacle_min_x - point.x, 0, point.x - obstacle_max_x)
        dy = max(obstacle_min_y - point.y, 0, point.y - obstacle_max_y)
        if max(dx, dy) < step:
            return 1
    for polygon in polygon_obstacles:
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


def _swept_square_envelope(start: PointNM, end: PointNM, radius_nm: int) -> tuple[PointNM, ...]:
    """Return the Minkowski sum of the segment start-end with an axis-aligned square.

    The square has half side ``radius_nm``, so the result contains every point within
    ``radius_nm`` of the centreline: the disc of that radius is inscribed in the square.
    Every vertex is an exact integer with no rounding step at all, because a square's
    corners are integer offsets from integer endpoints. The alternative constructions all
    need an irrational unit vector along the segment and therefore a rounding rule that has
    to be argued correct separately.
    """

    if start.x == end.x or start.y == end.y:
        # An axis-aligned sweep degenerates from a hexagon to the rectangle of the two
        # squares, and a rectangle keeps its exact square-cornered extent.
        min_x, max_x = min(start.x, end.x), max(start.x, end.x)
        min_y, max_y = min(start.y, end.y), max(start.y, end.y)
        return (
            PointNM(min_x - radius_nm, min_y - radius_nm),
            PointNM(max_x + radius_nm, min_y - radius_nm),
            PointNM(max_x + radius_nm, max_y + radius_nm),
            PointNM(min_x - radius_nm, max_y + radius_nm),
        )
    # Sweeping is symmetric, so orienting left-to-right leaves only two sign cases.
    if start.x > end.x:
        start, end = end, start
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


def _segment_envelope(segment: Segment) -> tuple[PointNM, ...] | None:
    """Return a conservative integer envelope of a diagonal track, or None when orthogonal.

    A track is a stadium: every point within half its width of the centreline. Sweeping an
    axis-aligned square of that half width along the centreline instead of a disc gives the
    convex hull of the two squares at the endpoints, which contains the stadium because the
    disc is inscribed in the square. The envelope is provably a superset rather than an
    approximation that happens to be close. The cost is over-approximating the perpendicular
    extent by at most (sqrt(2) - 1) half widths, which can only refuse a route, never permit
    a violation.
    """

    if segment.start.x == segment.end.x or segment.start.y == segment.end.y:
        return None
    return _swept_square_envelope(segment.start, segment.end, (segment.width_nm + 1) // 2)


def _arc_spans_at_most_half_turn(start: PointNM, mid: PointNM, end: PointNM) -> bool:
    """Return whether the arc through ``mid`` spans at most half a turn.

    By the inscribed-angle theorem the angle subtended at ``mid`` by the chord is half the
    arc that does *not* contain ``mid``, so that angle is at least a right angle exactly
    when the arc through ``mid`` is the minor one. The test is therefore one integer dot
    product with no division, no square root, and no tolerance. A semicircle gives exactly
    zero and is admitted, which is the inclusive side: the containment argument below holds
    with equality at half a turn.
    """

    return (start.x - mid.x) * (end.x - mid.x) + (start.y - mid.y) * (end.y - mid.y) <= 0


def _arc_sagitta_bound_nm(start: PointNM, mid: PointNM, end: PointNM) -> int:
    """Return an exact integer upper bound on a minor arc's distance from its own chord.

    Every point of a minor arc projects onto its chord segment and lies within the sagitta
    of it, so bounding the sagitta bounds the arc against the chord. The circumcentre of
    three integer points is rational, which makes the sagitta ``r - h`` a difference of two
    square roots of rationals; evaluating that difference would need a rounding rule whose
    correctness has to be argued. Instead the bound is the smallest integer ``k`` proved to
    satisfy the sufficient integer condition

        k * denominator * chord_floor + height >= radius_chord_ceiling >= sqrt(P * L2),

    where ``chord_floor <= sqrt(L2)`` and ``radius_chord_ceiling > sqrt(P * L2)``. Each
    substitution can only make the required ``k`` larger, so the result is an upper bound
    by construction rather than by numerical accident. In practice it lands within a
    nanometre or two of the true sagitta.
    """

    mid_x, mid_y = mid.x - start.x, mid.y - start.y
    end_x, end_y = end.x - start.x, end.y - start.y
    cross = mid_x * end_y - mid_y * end_x
    if cross == 0:
        # Collinear control points describe their own chord and bulge nowhere. Board IR
        # rejects such an arc, so this only guards direct callers of the helper.
        return 0
    mid_square = mid_x * mid_x + mid_y * mid_y
    chord_square = end_x * end_x + end_y * end_y
    # Twice the circumcentre offset from ``start``, scaled by ``2 * cross``.
    centre_x = mid_square * end_y - chord_square * mid_y
    centre_y = chord_square * mid_x - mid_square * end_x
    denominator = 2 * abs(cross)
    radius_square = centre_x * centre_x + centre_y * centre_y
    height = abs(end_x * centre_y - end_y * centre_x)
    chord_floor = isqrt(chord_square)
    shortfall = isqrt(radius_square * chord_square) + 1 - height
    if shortfall <= 0:
        return 0
    return -(-shortfall // (denominator * chord_floor))


def _arc_envelope(arc: Arc) -> tuple[PointNM, ...] | None:
    """Return a conservative integer envelope of a track arc, or None when unmodeled.

    A minor arc lies within its own sagitta of the chord segment, so the arc track — the
    arc swept with a disc of its half width — lies within the chord swept with a disc of
    the half width plus that sagitta. Sweeping a square of the same radius instead is a
    superset of that, so the envelope contains the real arc track for the same reason the
    diagonal-segment envelope contains a straight one.

    A major arc is refused rather than enveloped: past half a turn the arc leaves the
    chord's own span, so the chord-based containment argument stops holding and there is no
    honest envelope to build from these three points.
    """

    if not _arc_spans_at_most_half_turn(arc.start, arc.mid, arc.end):
        return None
    radius_nm = (arc.width_nm + 1) // 2 + _arc_sagitta_bound_nm(arc.start, arc.mid, arc.end)
    return _swept_square_envelope(arc.start, arc.end, radius_nm)


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

    The disc treatment is gated on the pad actually *being* a disc, not on its core having
    collapsed. A roundrect whose radius is half its shorter side is a stadium, and its core
    collapses in exactly the same way - but it is nowhere near as tall as a disc of its longer
    half extent, so reading the collapse as "round pad" gave a 2.0 x 1.0 mm stadium a core
    reaching 1.0 mm from the centre in y where the copper stops at 0.5 mm. That claims copper
    that is not there, which is the one direction an attachment core may never err in. KiCad
    writes exactly that pad for a ratio of 0.5, so this is reachable from a real board, and it
    became easier to reach once a fractional radius started rounding up onto the boundary.
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
    if pad.shape not in {PadShape.CIRCLE, PadShape.OVAL} or pad.size_x_nm != pad.size_y_nm:
        # Not a disc, so there is no inscribed square to claim. The degenerate bar is still a
        # true subset of the copper, and staying with it under-approximates rather than invents.
        return (core,)
    # The bar runs along whichever axis is non-zero, so the perpendicular bar simply swaps the
    # half extents. For a disc that perpendicular bar is the pad's other diameter.
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

    ``source_revision`` is the board the evidence was established against, and preparation
    refuses fill whose revision does not match the snapshot in hand. For a foreign net, the fresh
    islands become exact selected-layer obstacles and replace that zone's conservative outline;
    for the routed net, they remain connectivity evidence. This is not a defence against a caller
    determined to lie — an in-process caller can always construct whatever it likes — but it does
    turn the realistic mistake, handing the router a pour proved on some other board or an earlier
    revision of this one, from a silent wrong answer into a refusal.
    """

    net_id: str
    layer_id: str
    points: tuple[PointNM, ...]
    source_revision: str


#: The most islands the single-layer core will accept as evidence, and it is **this path's own
#: number**: every island becomes a candidate obstacle, and `AStarSettings.max_obstacles` admits
#: at most 32,768 of those, so evidence above the ceiling could not be modelled even if it were
#: read.  Deliberately *not* the ordered-layer adapter's 4,096 -- see `invalid_verified_fill`.
_MAX_VERIFIED_FILL_ISLANDS = 32_768

#: The most vertices one island may carry.  Equal to the domain ceiling of
#: `Settings.max_fill_vertices`, which bounds a whole *document's* pour, so it is a true upper
#: bound on any single island a configured reader can produce and refuses nothing any shipped
#: configuration admits.  It is not inert: the seam this gate exists for is an in-process caller
#: that synthesises islands rather than reading them (issue #166), and such a caller reaches it.
_MAX_VERIFIED_FILL_ISLAND_VERTICES = 1_000_000

#: Longest identity body accepted after the `net:` / `layer:` prefix.  The ordered-layer adapter
#: applies the same bound with its own copy of this predicate; `tests/test_routing_astar.py` pins
#: the two implementations to the same answers so the two seams cannot drift into two
#: vocabularies, which is what issue #166 asked to avoid.
_MAX_IDENTITY_BODY = 160


def _typed_identity(value: object, prefix: str) -> bool:
    """Return whether ``value`` is a typed Board IR identity with ``prefix``."""

    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and 1 <= len(value.removeprefix(prefix)) <= _MAX_IDENTITY_BODY
        and all(
            character.isascii() and (character.isalnum() or character in "_.:-")
            for character in value
        )
    )


def _digest_shape(value: object) -> bool:
    """Return whether ``value`` has the shape of a `sha256:` revision digest."""

    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        return False
    try:
        int(value[7:], 16)
    except ValueError:
        return False
    return True


def invalid_verified_fill(fill: object) -> str | None:
    """Reject malformed fill evidence at the public boundary, before any preparation work.

    The ordered-layer adapter has refused all of this at *its* boundary since ADR-0070; the
    single-layer seam did not, and issue #166 asked for the asymmetry to be closed or recorded.
    It is closed here, and what the measurement found is why: of the classes the adapter names,
    a **list instead of a tuple** and a **list of points** were accepted and routed, while a
    non-`VerifiedFill` entry and a non-`PointNM` vertex raised an uncaught `AttributeError` out
    of a seam whose every other refusal is typed.  The rest already refused, but two of them
    only after the work had been done and under a code naming the obstacle model rather than the
    input (`obstacle_budget_exceeded`, `obstacle_check_budget_exceeded`).  ADR-0108 records the
    per-class measurement.

    Two clauses of the adapter's list are deliberately **not** mirrored, and neither omission is
    an oversight:

    * **The three-vertex floor stays in `_prepare`**, which already refuses it under its own
      message and its own test.  Restating it here would leave that check unreachable, and dead
      code cannot be shown to work.
    * **The vertex range clause is not carried at all**, because on this path it is provably
      unreachable: `PointNM.__post_init__` enforces `JSON_SAFE_INTEGER`, which *is*
      `(1 << 53) - 1`, so a value that passes the type check above cannot fail a range check
      below it.  (The same is true of the adapter's own copy.)

    The two ceilings are this path's own numbers.  The adapter's per-island ceiling of 4,096 is
    reached by 14 of 18 boards in the real-board corpus (`B-108`), is filed as an over-refusal in
    issue #167, and is parked pending a paired calibration that plan item P7.1 says no quality
    argument may substitute for.  Importing it here would newly refuse, on a path that accepts
    them today, boards whose only fault is a large pour -- closing a hygiene gap by copying a
    known defect.
    """

    if not isinstance(fill, tuple):
        return "verified fill must be a tuple"
    if len(fill) > _MAX_VERIFIED_FILL_ISLANDS:
        return "verified fill island count exceeds the obstacle ceiling"
    for island in fill:
        if not isinstance(island, VerifiedFill):
            return "verified fill entry must be a VerifiedFill value"
        if not _typed_identity(island.net_id, "net:") or not _typed_identity(
            island.layer_id, "layer:"
        ):
            return "verified fill island identity is malformed"
        if not _digest_shape(island.source_revision):
            return "verified fill source revision is malformed"
        points: object = island.points
        if not isinstance(points, tuple) or len(points) > _MAX_VERIFIED_FILL_ISLAND_VERTICES:
            return "verified fill island is not a bounded polygon"
        for point in points:
            if not isinstance(point, PointNM):
                return "verified fill island vertex is malformed"
    return None


def canonical_fill_bytes(verified_fill: tuple[VerifiedFill, ...]) -> bytes:
    """Return stable identity bytes for one exact sequence of verified fill islands."""

    rendered = json.dumps(
        [
            {
                "layer_id": island.layer_id,
                "net_id": island.net_id,
                "points": [{"x_nm": point.x, "y_nm": point.y} for point in island.points],
                "source_revision": island.source_revision,
            }
            for island in verified_fill
        ],
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return rendered.encode("utf-8", errors="strict") + b"\n"


def fill_binding_for(verified_fill: tuple[VerifiedFill, ...]) -> str | None:
    """Return the binding a candidate records for the fill it was routed under.

    ``None`` for no fill is not an encoding convenience: a candidate routed under the
    conservative envelope and a candidate routed under an empty pour are the same candidate,
    because an empty pour and no pour give the router the same obstacle model.  Islands are
    bound in the order the caller supplied them, and every field of every island is covered,
    so any difference at all in the evidence - including its order - changes the binding and
    makes a replay refuse.  That is the over-approximating side of the choice, which is the
    side this repository takes for obstacles.
    """

    if not verified_fill:
        return None
    return f"sha256:{hashlib.sha256(canonical_fill_bytes(verified_fill)).hexdigest()}"


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

    def admit(copper: _CopperObject) -> None:
        """Charge one same-net object against the net-object budget.

        This population is the routed net's own copper, not the copper it has to avoid, and
        its cost is quadratic: the component merge below compares every admitted pair. It
        therefore has its own budget and its own refusal code rather than sharing the
        obstacle model's, which is what made a finished board's connectivity look like an
        obstacle problem (issue #128).
        """

        if len(objects) >= request.settings.max_net_objects:
            raise _fail(
                RouteFailureCode.NET_OBJECT_BUDGET_EXCEEDED,
                "the same-net connectivity model exceeds the configured net-object budget "
                f"(max_net_objects={request.settings.max_net_objects})",
            )
        objects.append(copper)

    for pad in pads:
        cores = _pad_cores(pad)
        if cores is None:
            return None
        pad_indices.append(len(objects))
        admit(_CopperObject(layer_ids=frozenset(pad.layer_ids), cores=cores))
    for index, segment in enumerate(content.segments):
        if index % 64 == 0:
            work.checkpoint()
        if segment.net_id != request.net_id:
            continue
        orthogonal = _segment_core_extent(segment)
        cores = (orthogonal,) if orthogonal is not None else _diagonal_segment_cores(segment, work)
        if cores is None:
            return None
        admit(_CopperObject(layer_ids=frozenset({segment.layer_id}), cores=tuple(cores)))
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
        admit(_CopperObject(layer_ids=layer_ids, cores=cores))
    fill_count = 0
    for island in verified_fill:
        work.checkpoint()
        if island.net_id != request.net_id:
            continue
        fill_count += 1
        admit(_CopperObject(layer_ids=frozenset({island.layer_id}), polygon=island.points))

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
    congestion_penalty: CongestionPenalty | None = None,
    use_spatial_index: bool = True,
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
        if arc.net_id != request.net_id:
            # Foreign arcs are conservative polygon envelopes, built with the obstacles below.
            continue
        # An obstacle may be over-approximated, but attachment copper must be
        # under-approximated or the router would claim a connection the board does not have.
        # An arc has no exact integer inner core yet, so a selected-net arc cannot be
        # attachment copper and stays a refusal. Like the same-net zone gate below, this
        # covers every copper layer, because connectivity is a multilayer question.
        raise _fail(
            RouteFailureCode.UNSUPPORTED_GEOMETRY,
            "the selected net carries a track arc, which is not modeled as attachment copper",
        )
    blocking_zones: list[Zone] = []
    same_net_zone = False
    # Zone outline bounds are only measured when there is fill to check them against, so a board
    # routed without evidence spends exactly the obstacle-check budget it always did.
    zone_bounds_by_net_layer: dict[tuple[str, str], list[_Rect]] = {}
    if verified_fill:
        for zone in content.zones:
            zone_bounds_by_net_layer.setdefault((zone.net_id, zone.layer_id), []).append(
                _polygon_bounds(zone.boundary.points, work)
            )
    verified_fill_zone_keys = frozenset(
        (zone.net_id, zone.layer_id) for zone in content.zones if zone.net_id is not None
    )
    # A same-net zone anywhere in the stack is unmodeled copper, and connectivity is a
    # multilayer question, so a pour on the back layer can carry a connection just as a front
    # one can. The gate therefore covers every copper layer, and is lifted only for a layer
    # whose pour arrives as verified fill.
    verified_fill_layers = frozenset(
        island.layer_id for island in verified_fill if island.net_id == request.net_id
    )
    ungoverned_zone_layers = frozenset(
        zone.layer_id
        for zone in content.zones
        if zone.net_id == request.net_id and zone.layer_id not in verified_fill_layers
    )
    same_net_zone_present = bool(ungoverned_zone_layers)
    # A same-net zone normally blocks any claim, because a cached fill may not describe the
    # board around it. Verified fill is the exception: the caller has already proved this
    # board's cache is what KiCad recomputes from it, so the poured copper counts as evidence.
    for island in verified_fill:
        if island.source_revision != content.source.revision:
            raise _fail(
                RouteFailureCode.STALE_FILL,
                "verified zone fill was established against a different board revision",
            )
        if (island.net_id, island.layer_id) not in verified_fill_zone_keys:
            raise _fail(
                RouteFailureCode.UNSUPPORTED_GEOMETRY,
                "verified zone fill is not backed by a matching Board IR zone",
            )
        if len(island.points) < 3:
            # The parser's own floor, restated here because the router is also reachable from a
            # typed in-process seam that never went through it.
            raise _fail(
                RouteFailureCode.UNSUPPORTED_GEOMETRY,
                "verified zone fill island is not a closed ring",
            )
        # KiCad clips poured copper to the zone outline, so honest evidence already satisfies
        # this. Checking it is what turns "retiring the envelope only ever shrinks the obstacle"
        # from an assumption about the filler into a verified precondition, and it is the gate
        # ADR-0070 already applies in the ordered-layer adapter.
        island_bounds = _polygon_bounds(island.points, work)
        if not any(
            _rect_contains(outline, island_bounds)
            for outline in zone_bounds_by_net_layer[(island.net_id, island.layer_id)]
        ):
            raise _fail(
                RouteFailureCode.UNSUPPORTED_GEOMETRY,
                "verified zone fill escapes its backing Board IR zone outline",
            )
    net_fill = tuple(island for island in verified_fill if island.net_id == request.net_id)
    if (same_net_via or net_fill) and not same_net_zone_present:
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
        # A fresh KiCad fill is tighter than treating the whole zone outline as copper. Replace
        # the conservative envelope only when this net/layer has at least one verified island;
        # the islands below then become the complete obstacle set for that zone family.
        if (zone.net_id, zone.layer_id) in verified_fill_zone_keys and any(
            island.net_id == zone.net_id and island.layer_id == zone.layer_id
            for island in verified_fill
        ):
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
        if len(attachment_segments) > request.settings.max_net_objects:
            raise _fail(
                RouteFailureCode.NET_OBJECT_BUDGET_EXCEEDED,
                "the same-net attachment copper exceeds the configured net-object budget "
                f"(max_net_objects={request.settings.max_net_objects})",
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

    step = request.settings.grid_step_nm
    # The routing region is the corridor the lattice is allowed to occupy: the routed net's own
    # copper, widened by the configured margin, clipped to the safe board. Every lattice index
    # below is derived from it, so the search cannot leave it, and an obstacle that cannot reach
    # it cannot change any answer this request computes. Modelling the whole board instead made
    # the obstacle budget track board size rather than work (issue #128).
    region = _routing_region(
        pads, attachment_segments, safe_board, request.settings.region_margin_nm
    )
    region_scoped = region != safe_board
    region_min_x, region_min_y, region_max_x, region_max_y = region

    def reaches_region(bounds: _Rect, margin_nm: int) -> bool:
        """Return True when an obstacle inflated by ``margin_nm`` can reach the region.

        The obstacle model over-approximates, so this test decides only what may be *dropped*,
        and it drops nothing a query could return. Every lattice node lies inside ``region``,
        every lattice edge joins two such nodes, and the widest envelope any predicate queries
        with is one lattice step (proximity scoring) around a point on such an edge. An
        obstacle whose ``margin_nm``-inflated bounds stay more than one step clear of the
        region is therefore outside the reach of every predicate, exactly, and not merely
        probably. This is the same envelope the spatial index is built with below.
        """

        reach_nm = margin_nm + step
        return not (
            bounds[2] + reach_nm < region_min_x
            or bounds[0] - reach_nm > region_max_x
            or bounds[3] + reach_nm < region_min_y
            or bounds[1] - reach_nm > region_max_y
        )

    rect_obstacles: list[_Rect] = []
    polygon_obstacles: list[_PolygonObstacle] = []

    def ensure_obstacle_capacity() -> None:
        # Only foreign selected-layer copper is charged here. The routed net's own copper is a
        # different population with a different cost, and it is charged to `max_net_objects`.
        modeled = len(rect_obstacles) + len(polygon_obstacles)
        if modeled >= request.settings.max_obstacles:
            raise _fail(
                RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                "the region-scoped selected-layer obstacle model exceeds the configured "
                f"obstacle budget (max_obstacles={request.settings.max_obstacles})",
            )

    def add_rect_obstacle(rectangle: _Rect, clearance_nm: int) -> None:
        """Inflate one exact rectangle by the route half-width plus the governing clearance."""

        margin_nm = half_width_nm + clearance_nm
        if not reaches_region(rectangle, margin_nm):
            return
        ensure_obstacle_capacity()
        rect_obstacles.append(_inflate_rectangle(rectangle, margin_nm))

    def add_polygon_obstacle(source_id: str, points: tuple[PointNM, ...], margin_nm: int) -> None:
        """Record one conservative polygon envelope, unless it cannot reach the region."""

        bounds = _polygon_bounds(points, work)
        if not reaches_region(bounds, margin_nm):
            return
        ensure_obstacle_capacity()
        polygon_obstacles.append(
            _PolygonObstacle(
                source_id=source_id,
                points=points,
                bounds=bounds,
                margin_nm=margin_nm,
            )
        )

    # Resolving a clearance per obstacle rescans the assignment and class tuples every time,
    # which is quadratic in board size and metered only for cancellation. The mapping is fixed
    # for the whole request, so it is built once here and charged once.
    clearance_by_net: dict[str, int] = {}
    class_clearance = {item.id: item.clearance_nm for item in content.constraints.net_classes}
    for index, assignment in enumerate(content.constraints.assignments):
        if index % 64 == 0:
            work.checkpoint()
        resolved = class_clearance.get(assignment.net_class_id)
        if resolved is not None:
            clearance_by_net[assignment.net_id] = resolved
    # Copper on no net still has to be cleared by something. The widest class on the board is
    # the only choice that cannot under-inflate, and over-inflation only ever refuses a route.
    widest_clearance_nm = max([net_class.clearance_nm, *class_clearance.values()])

    def governing_clearance_nm(net_id: str | None) -> int:
        """Use the stricter of the routed net's clearance and the obstacle net's clearance."""

        if net_id is None:
            return widest_clearance_nm
        other = clearance_by_net.get(net_id)
        if other is None:
            return net_class.clearance_nm
        return max(net_class.clearance_nm, other)

    # A verified fill island is keyed by its net and layer rather than by a Board IR zone ID.
    # There can be more than one zone for that pair, so use the strictest declared zone
    # clearance.  This conservative association keeps a high-clearance zone from being
    # under-modelled while remaining safe for older callers that only provide fill geometry.
    zone_clearance_by_net_layer: dict[tuple[str, str], int] = {}
    for zone in content.zones:
        key = (zone.net_id, zone.layer_id)
        zone_clearance_by_net_layer[key] = max(
            zone_clearance_by_net_layer.get(key, 0), zone.clearance_nm
        )

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
        add_polygon_obstacle(
            keepout.id,
            keepout.boundary.points,
            half_width_nm + net_class.clearance_nm,
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
        add_polygon_obstacle(
            segment.id, envelope, half_width_nm + governing_clearance_nm(segment.net_id)
        )

    for index, arc in enumerate(content.arcs):
        if index % 64 == 0:
            work.checkpoint()
        if arc.layer_id != request.layer_id:
            continue
        # Selected-net arcs were refused above, so every arc reaching here is foreign copper
        # and may be over-approximated. The margin is the same rule the straight-track path
        # uses, and offsetting an envelope that already contains the arc track is a superset
        # of offsetting the arc track itself, so the inflation composes without a new proof.
        envelope = _arc_envelope(arc)
        if envelope is None:
            raise _fail(
                RouteFailureCode.UNSUPPORTED_GEOMETRY,
                "a selected-layer arc spans more than half a turn and is not modeled exactly",
            )
        add_polygon_obstacle(arc.id, envelope, half_width_nm + governing_clearance_nm(arc.net_id))

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
        clearance_nm = max(governing_clearance_nm(zone.net_id), zone.clearance_nm)
        add_polygon_obstacle(zone.id, zone.boundary.points, half_width_nm + clearance_nm)

    for index, island in enumerate(verified_fill):
        if island.layer_id != request.layer_id or island.net_id == request.net_id:
            continue
        # The fill polygon is exact, but the route still has to respect both net-class
        # clearance and the governing zone's own clearance.  Candidate half-width is retained
        # here so the resulting obstacle is a conservative track-center exclusion envelope.
        clearance_nm = max(
            governing_clearance_nm(island.net_id),
            zone_clearance_by_net_layer.get((island.net_id, island.layer_id), 0),
        )
        add_polygon_obstacle(
            f"fill:{island.net_id}:{island.layer_id}:{index}",
            island.points,
            half_width_nm + clearance_nm,
        )

    delta_x = end_pad.center.x - start_pad.center.x
    delta_y = end_pad.center.y - start_pad.center.y
    # A two-pin route joins pad centres, so both must sit on the lattice anchored at the first.
    # Multi-pin legs reach a pad through the lattice nodes its core covers instead, so requiring
    # the centres to divide would refuse boards the search can route perfectly well — and would
    # be unsatisfiable in practice, since the divisor has to serve every pad at once.
    if two_pin and (delta_x % step != 0 or delta_y % step != 0):
        evidence = _off_grid_evidence(start_pad, end_pad, delta_x, delta_y, step)
        divisor = (
            f"{evidence.largest_representable_step_nm} nm"
            if evidence.largest_representable_step_nm is not None
            else "above this contract's exact-integer range"
        )
        raise _fail(
            RouteFailureCode.OFF_GRID,
            f"{OFF_GRID_MESSAGE_LEAD}: it misses the nearest lattice point by "
            f"({evidence.miss_x_nm} nm, {evidence.miss_y_nm} nm) at grid_step_nm={step}; "
            f"the largest step that represents this pad pair is {divisor}",
            off_grid=evidence,
        )
    # The lattice spans the routing region, never the whole safe board. This is what makes the
    # region-scoped obstacle model sound rather than merely cheaper: a node the search can
    # reach is a node inside the region, so the copper omitted above is copper no candidate
    # can be routed through.
    min_ix = _ceil_div(region[0] - start_pad.center.x, step)
    max_ix = (region[2] - start_pad.center.x) // step
    min_iy = _ceil_div(region[1] - start_pad.center.y, step)
    max_iy = (region[3] - start_pad.center.y) // step
    goal_ix = delta_x // step
    goal_iy = delta_y // step
    if not (min_ix <= 0 <= max_ix and min_iy <= 0 <= max_iy):
        raise _fail(RouteFailureCode.NO_PATH, "the start pad cannot contain the routed width")
    if two_pin and not (min_ix <= goal_ix <= max_ix and min_iy <= goal_iy <= max_iy):
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

    rect_obstacle_tuple = tuple(sorted(rect_obstacles))
    polygon_obstacle_tuple = tuple(sorted(polygon_obstacles, key=_polygon_obstacle_sort_key))
    # Index bounds include one lattice step beyond the exact query envelope.  This is a safe
    # superset for both edge legality and proximity scoring; exact predicates still decide.
    rect_index = ConservativeSpatialIndex(
        tuple(
            SpatialIndexEntry(
                ordinal=index,
                bounds=_inflate_rectangle(rectangle, step),
                value=rectangle,
            )
            for index, rectangle in enumerate(rect_obstacle_tuple)
        ),
        min_index_entries=_SPATIAL_INDEX_MIN_ENTRIES,
    )
    polygon_index = ConservativeSpatialIndex(
        tuple(
            SpatialIndexEntry(
                ordinal=index,
                bounds=_inflate_rectangle(polygon.bounds, polygon.margin_nm + step),
                value=polygon,
            )
            for index, polygon in enumerate(polygon_obstacle_tuple)
        ),
        min_index_entries=_SPATIAL_INDEX_MIN_ENTRIES,
    )
    problem = _Problem(
        snapshot=snapshot,
        request=request,
        start_pad=start_pad,
        end_pad=end_pad,
        width_nm=net_class.track_width_nm,
        clearance_nm=net_class.clearance_nm,
        safe_board=safe_board,
        rect_obstacles=rect_obstacle_tuple,
        polygon_obstacles=polygon_obstacle_tuple,
        rect_index=rect_index,
        polygon_index=polygon_index,
        use_spatial_index=use_spatial_index,
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
        region_scoped=region_scoped,
        congestion_penalty=congestion_penalty,
        fill_binding=fill_binding_for(verified_fill),
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
    # `fill_binding` is present only when there is one.  A candidate routed under the
    # conservative envelope is the same proposal it always was - same geometry, same cost, same
    # recorded work - so its published content address must not move, and every persisted
    # candidate from every earlier router version must keep verifying against its stored ID.
    # Emitting `"fill_binding":null` would move all of them at once to record an absence.
    fill_binding = (
        {"fill_binding": candidate.fill_binding} if candidate.fill_binding is not None else {}
    )
    return {
        **fill_binding,
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
    identity: _RouterIdentity,
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
        router_version=identity.router_version,
        policy=identity.policy,
        seed=problem.request.seed,
        fill_binding=problem.fill_binding,
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
    start_score: _Score = (0, 0, 0, 0)
    best: dict[_State, _Score] = dict.fromkeys(start_states, start_score)
    parents: dict[_State, _State] = {}
    frontier: list[tuple[int, int, int, int, int, int, int, int, int, _State]] = []
    counter = 0
    for state in start_states:
        heapq.heappush(
            frontier,
            (
                _heuristic(problem, state[0], state[1]),
                0,
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
    # Every leg draws on one ceiling, because the caller authorised one candidate's worth of
    # work rather than one leg's worth per component.
    already_spent = work.expanded_states

    while frontier:
        work.checkpoint()
        (
            _,
            policy_cost,
            g_cost,
            bends,
            proximity_steps,
            iy,
            ix,
            direction,
            _,
            state,
        ) = heapq.heappop(frontier)
        if best.get(state) != (policy_cost, g_cost, bends, proximity_steps):
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
        if already_spent + expanded_states >= settings.max_expansions:
            raise _fail(
                RouteFailureCode.SEARCH_BUDGET_EXCEEDED,
                "the A* search reached its configured expansion budget",
                expanded_states=already_spent + expanded_states,
                obstacle_checks=work.obstacle_checks,
            )
        expanded_states += 1
        work.expanded_states = already_spent + expanded_states

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
            congestion_increment = 0
            if problem.congestion_penalty is not None:
                try:
                    raw_penalty = problem.congestion_penalty(current, destination)
                except Exception as error:  # pragma: no cover - defensive boundary
                    raise _fail(
                        RouteFailureCode.UNSUPPORTED_CONSTRAINT,
                        "the congestion policy failed closed",
                        expanded_states=work.expanded_states,
                        obstacle_checks=work.obstacle_checks,
                    ) from error
                if (
                    isinstance(raw_penalty, bool)
                    or not isinstance(raw_penalty, int)
                    or raw_penalty < 0
                    or raw_penalty > _MAX_CONGESTION_PENALTY
                ):
                    raise _fail(
                        RouteFailureCode.UNSUPPORTED_CONSTRAINT,
                        "the congestion policy returned an invalid penalty",
                        expanded_states=work.expanded_states,
                        obstacle_checks=work.obstacle_checks,
                    )
                congestion_increment = raw_penalty
            next_state: _State = (next_ix, next_iy, next_direction)
            next_policy_cost = (
                policy_cost
                + settings.grid_step_nm
                + bend_increment * settings.bend_penalty_nm
                + proximity_increment * settings.proximity_penalty_nm
                + congestion_increment
            )
            next_score: _Score = (
                next_policy_cost,
                next_cost,
                next_bends,
                next_proximity,
            )
            if next_score >= best.get(next_state, (1 << 63, 1 << 63, 1 << 63, 1 << 63)):
                continue
            best[next_state] = next_score
            parents[next_state] = state
            counter += 1
            heapq.heappush(
                frontier,
                (
                    next_policy_cost + _heuristic(problem, next_ix, next_iy),
                    next_policy_cost,
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

    # An exhausted search says nothing about copper the request never modelled. When the
    # lattice was clipped to a routing region, the honest claim is about that region, and the
    # caller's recourse — a wider `region_margin_nm` — is different from the recourse for a
    # genuinely blocked board. Two codes, because a message a caller never sees is not one.
    if problem.region_scoped:
        raise _fail(
            RouteFailureCode.NO_PATH_IN_REGION,
            "no legal orthogonal path exists inside the bounded routing region "
            f"(region_margin_nm={problem.request.settings.region_margin_nm})",
            expanded_states=expanded_states,
            obstacle_checks=work.obstacle_checks,
        )
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


def _steiner_merge_order(
    components: tuple[tuple[_Rect, ...], ...], work: _WorkBudget
) -> tuple[tuple[int, int], ...]:
    """Return the bounded one-Steiner-guided merge order for low-degree nets."""

    return batched_one_steiner_order(components, checkpoint=work.obstacle_check)


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


def _route_tree(problem: _Problem, work: _WorkBudget, identity: _RouterIdentity) -> RouteCandidate:
    """Merge the net's components one leg at a time until a single component remains."""

    if problem.pad_count == 2:
        return _build_candidate(
            problem,
            (_search(problem, work),),
            pad_count=2,
            ordering_policy=SINGLE_PATH_ORDERING,
            identity=identity,
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
    ordering_policy = _ordering_policy_for(identity, problem.pad_count)
    if ordering_policy == BATCHED_ONE_STEINER_ORDERING:
        merge_order = _steiner_merge_order(problem.components, work)
    elif ordering_policy == COMPONENT_MST_ORDERING:
        # The cubic topology guide is intentionally limited to low-degree nets.  Large nets
        # retain the previous bounded MST order until a separately budgeted decomposition policy
        # exists; this keeps a hostile pad count from consuming the whole request on ordering.
        merge_order = _merge_order(problem.components, work)
    else:  # pragma: no cover - every identity is closed above this boundary
        raise RuntimeError("internal router identity has no multi-pin ordering policy")
    for first, second in merge_order:
        left, right = find(first), find(second)
        if left == right:
            continue
        source_cores, target_cores = groups[left], groups[right]
        source_nodes = _nodes_for_cores(problem, source_cores, work)
        target_nodes = _nodes_for_cores(problem, target_cores, work)
        if not source_nodes or not target_nodes:
            raise _fail(
                RouteFailureCode.NO_PATH_IN_REGION
                if problem.region_scoped
                else RouteFailureCode.NO_PATH,
                "a pad cannot be reached from the bounded routing lattice",
                expanded_states=work.expanded_states,
                obstacle_checks=work.obstacle_checks,
            )
        if source_nodes & target_nodes:
            # An earlier leg can grow into copper that a later merge was still scheduled to
            # reach, so the two components already touch by the time their turn arrives. That
            # is a merge that has happened, not an inconsistency: absorb it and move on.
            merged_early = source_cores + target_cores
            winner_early, loser_early = min(left, right), max(left, right)
            parent[loser_early] = winner_early
            groups[winner_early] = merged_early
            del groups[loser_early]
            continue
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
        ordering_policy=ordering_policy,
        identity=identity,
        work=work,
    )


def _validate_public_inputs(
    snapshot: object,
    request: object,
    cancelled: object,
    congestion_penalty: object,
) -> (
    tuple[
        BoardIRSnapshot,
        RouteRequest,
        CancellationCheck | None,
        CongestionPenalty | None,
    ]
    | RouteResult
):
    if not isinstance(snapshot, BoardIRSnapshot):
        return _result_failure(
            _fail(
                RouteFailureCode.INVALID_SNAPSHOT,
                "the Board IR snapshot type is invalid",
            )
        )
    if (
        not isinstance(request, RouteRequest)
        or (cancelled is not None and not callable(cancelled))
        or (congestion_penalty is not None and not callable(congestion_penalty))
    ):
        return _result_failure(
            _fail(
                RouteFailureCode.INVALID_REQUEST,
                "the routing request type is invalid",
            )
        )
    return (
        snapshot,
        request,
        cast("CancellationCheck | None", cancelled),
        cast("CongestionPenalty | None", congestion_penalty),
    )


class AStarRouter:
    """Pure CPU reference backend for one exact two-pin route candidate."""

    def __init__(self, identity: _RouterIdentity | None = None) -> None:
        self._identity = identity or _CURRENT_ROUTER_IDENTITY

    @classmethod
    def for_replay(
        cls,
        *,
        router_version: str,
        policy: str,
        ordering_policy: str,
        pad_count: int,
    ) -> AStarRouter:
        """Select one recorded router behavior, refusing unknown historical combinations."""

        identity = _REPLAY_IDENTITIES.get((router_version, policy))
        if identity is None or ordering_policy != _ordering_policy_for(identity, pad_count):
            raise ValueError("candidate router version and ordering policy are unsupported")
        return cls(identity)

    def replay(
        self,
        snapshot: BoardIRSnapshot,
        candidate: RouteCandidate,
        *,
        verified_fill: tuple[VerifiedFill, ...] = (),
    ) -> RouteResult:
        """Replay one immutable candidate under its own closed router identity and obstacle model.

        A replay that substitutes a different obstacle model is not a replay, so the fill this
        is handed must be the fill that produced the candidate: ``fill_binding_for`` over
        ``verified_fill`` has to equal the candidate's own recorded binding, and any difference
        refuses instead of searching.  One equality enforces both directions.  Refusing the
        *understated* direction - a candidate routed with fill, replayed without it - is what
        issue #163 was: the conservative envelope over-approximates the exact pour, so the
        replay was stricter than the route and the disagreement surfaced as the candidate's
        fault.  Refusing the *overstated* direction - a candidate routed under the envelope,
        replayed with fill that opens corridors the envelope closed - matters more: that replay
        would be looser than the route, and would confirm geometry the router never proved.
        """

        malformed = invalid_verified_fill(verified_fill)
        if malformed is not None:
            # Before `fill_binding_for`, which reads every field of every island and would
            # otherwise raise on the malformed evidence rather than refusing it.
            return _result_failure(_fail(RouteFailureCode.UNSUPPORTED_GEOMETRY, malformed))
        binding = fill_binding_for(verified_fill)
        if binding != candidate.fill_binding:
            return _result_failure(
                _fail(
                    RouteFailureCode.FILL_EVIDENCE_MISMATCH,
                    "the verified fill supplied for replay is not the fill this candidate "
                    "was routed under",
                )
            )
        router = self.for_replay(
            router_version=candidate.router_version,
            policy=candidate.policy,
            ordering_policy=candidate.ordering_policy,
            pad_count=candidate.pad_count,
        )
        request = RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=candidate.patch.net_id,
            layer_id=candidate.patch.layer_id,
            seed=candidate.seed,
            settings=candidate.settings,
        )
        return router.propose(snapshot, request, verified_fill=verified_fill)

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
        congestion_penalty: CongestionPenalty | None = None,
    ) -> RouteResult:
        """Return an unapplied candidate, an already-connected record, or an expected failure.

        ``verified_fill`` is poured copper a caller has already bound to freshness evidence.
        The router never reads a board or runs KiCad, so fill it was not handed is fill that
        does not exist as far as any claim here is concerned.
        """

        validated = _validate_public_inputs(snapshot, request, cancelled, congestion_penalty)
        if isinstance(validated, RouteResult):
            return validated
        malformed = invalid_verified_fill(verified_fill)
        if malformed is not None:
            return _result_failure(_fail(RouteFailureCode.UNSUPPORTED_GEOMETRY, malformed))
        checked_snapshot, checked_request, cancellation_check, checked_penalty = validated
        work = _WorkBudget(settings=checked_request.settings, cancelled=cancellation_check)
        try:
            problem = _prepare(
                checked_snapshot,
                checked_request,
                work,
                verified_fill,
                checked_penalty,
                self._identity.use_spatial_index,
            )
            return RouteResult(candidate=_route_tree(problem, work, self._identity))
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
