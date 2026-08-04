"""Exact integer geometry for placement legality.

Every predicate here is decided with integer arithmetic in nanometres. No floating point
appears at any point, so there is no rounding rule to reason about and no tolerance that could
silently swallow a real violation.

Two directions of error are used deliberately and must not be confused:

* ``pad_bounds`` **over**-approximates a pad. Two pads whose bounds are disjoint are provably
  disjoint, so a negative answer from bounds is a proof of legality.
* ``pad_core`` **under**-approximates a pad. Two pads whose cores overlap provably overlap, so
  a positive answer from cores is a proof of illegality.

Neither can prove the other's conclusion, which is exactly why pad overlap is reported with
three values rather than two.
"""

from __future__ import annotations

from math import isqrt

from copper_mcp.board_ir import Pad, PadShape, PointNM, Ring

QUARTER_UDEG = 90_000_000
FULL_UDEG = 360_000_000

#: An axis-aligned box as ``(min_x, min_y, max_x, max_y)`` in nanometres.
Rect = tuple[int, int, int, int]

#: Shapes whose inscribed rectangle this module knows how to compute.
_CORE_MODELLED_SHAPES = frozenset(
    {PadShape.RECT, PadShape.ROUNDRECT, PadShape.CIRCLE, PadShape.OVAL}
)


def ceil_sqrt(value: int) -> int:
    """Smallest integer whose square is at least ``value``."""

    if value <= 0:
        return 0
    root = isqrt(value)
    return root if root * root == value else root + 1


def rects_overlap(first: Rect, second: Rect) -> bool:
    """Open overlap: rectangles that merely touch along an edge do not overlap."""

    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )


def rects_intersect_closed(first: Rect, second: Rect) -> bool:
    """Closed intersection: a shared edge or point counts as contact."""

    return not (
        first[2] < second[0] or second[2] < first[0] or first[3] < second[1] or second[3] < first[1]
    )


def inset_rect(rect: Rect, amount: int) -> Rect | None:
    """Inset every edge equally, returning ``None`` when no positive area remains."""

    if amount < 0:
        raise ValueError("rectangle inset must not be negative")
    inset = (rect[0] + amount, rect[1] + amount, rect[2] - amount, rect[3] - amount)
    return inset if inset[0] < inset[2] and inset[1] < inset[3] else None


def rect_gap(first: Rect, second: Rect) -> int:
    """Rectilinear separation between two boxes, or ``0`` when they touch or overlap."""

    horizontal = max(first[0] - second[2], second[0] - first[2], 0)
    vertical = max(first[1] - second[3], second[1] - first[3], 0)
    return max(horizontal, vertical)


def translate(rect: Rect, delta_x: int, delta_y: int) -> Rect:
    return (rect[0] + delta_x, rect[1] + delta_y, rect[2] + delta_x, rect[3] + delta_y)


def union(first: Rect, second: Rect) -> Rect:
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def ring_bounds(ring: Ring) -> Rect:
    xs = [point.x for point in ring.points]
    ys = [point.y for point in ring.points]
    return (min(xs), min(ys), max(xs), max(ys))


def pad_half_extents(pad: Pad) -> tuple[int, int]:
    """Half extents of a box containing the pad at its own angle.

    A pad angle in Board IR is absolute and need not be a quarter turn, so quadrant parity
    alone is not enough. Quarter turns keep exact extents; any other angle falls back to the
    rectangle's circumscribed circle, which contains it at every rotation without trigonometry.
    """

    if pad.rotation_udeg % QUARTER_UDEG == 0:
        half_x, half_y = (pad.size_x_nm + 1) // 2, (pad.size_y_nm + 1) // 2
        if pad.rotation_udeg // QUARTER_UDEG % 2 == 1:
            half_x, half_y = half_y, half_x
        return half_x, half_y
    half = (ceil_sqrt(pad.size_x_nm**2 + pad.size_y_nm**2) + 1) // 2
    return half, half


def pad_bounds(pad: Pad, origin: PointNM | None = None) -> Rect:
    """Over-approximating box for one pad, optionally re-centred on ``origin``."""

    centre = origin if origin is not None else pad.center
    half_x, half_y = pad_half_extents(pad)
    return (centre.x - half_x, centre.y - half_y, centre.x + half_x, centre.y + half_y)


def pad_core(pad: Pad, origin: PointNM | None = None) -> Rect | None:
    """Under-approximating box strictly inside one pad, or ``None`` when unmodelled.

    Only quarter-turn pads are modelled. An oblique pad's inscribed rectangle would itself be
    oblique, and approximating it with an axis-aligned box risks claiming copper that is not
    there - which would turn an inconclusive answer into a false accusation of illegality.
    """

    if pad.rotation_udeg % QUARTER_UDEG != 0:
        return None
    # Screened against a named set first, so a shape added to PadShape later fails closed here
    # instead of silently reaching one of the formulas below. This also leaves the tail
    # unconditional, which is what stops it being either unreachable or a missing return
    # depending on how exhaustively the checker narrows the enum.
    if pad.shape not in _CORE_MODELLED_SHAPES:
        return None
    size_x, size_y = pad.size_x_nm, pad.size_y_nm
    if pad.rotation_udeg // QUARTER_UDEG % 2 == 1:
        size_x, size_y = size_y, size_x
    half_x, half_y = size_x // 2, size_y // 2
    if pad.shape is PadShape.ROUNDRECT:
        radius = pad.roundrect_radius_nm
        if radius is None:
            return None
        # Board IR guarantees 2 * radius <= min(size), so this band stays non-negative and
        # spans the full width across the pad's middle.
        half_y -= radius
    elif pad.shape in {PadShape.CIRCLE, PadShape.OVAL}:
        if half_x == half_y:
            # A circle's central rectangle degenerates to a line, which would leave circular
            # pads with no core at all and so unable to prove any overlap. Its inscribed
            # axis-aligned square is the useful bound: half-side ``isqrt(r^2 // 2)`` satisfies
            # ``2 s^2 <= r^2`` and so lies inside the disc by exact integer arithmetic.
            half_x = half_y = isqrt(half_x * half_x // 2)
        elif half_x > half_y:
            # A stadium contains the central rectangle left after removing its end caps.
            half_x -= half_y
        else:
            half_y -= half_x
    if half_x <= 0 or half_y <= 0:
        return None
    centre = origin if origin is not None else pad.center
    return (centre.x - half_x, centre.y - half_y, centre.x + half_x, centre.y + half_y)


def _cross(origin: PointNM, first: PointNM, second: PointNM) -> int:
    return (first.x - origin.x) * (second.y - origin.y) - (first.y - origin.y) * (
        second.x - origin.x
    )


def _ray_crosses_right(point: PointNM, start: PointNM, end: PointNM) -> bool:
    """One crossing of the ray travelling in +x from ``point``. Exact integers."""

    if (start.y > point.y) == (end.y > point.y):
        return False
    return (_cross(start, end, point) > 0) == (end.y > start.y)


def point_in_ring(point: PointNM, ring: Ring) -> bool:
    """Even-odd containment. A point exactly on an edge is not treated as inside."""

    inside = False
    points = ring.points
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        if _ray_crosses_right(point, start, end):
            inside = not inside
    return inside


def _segments_intersect(
    first_start: PointNM, first_end: PointNM, second_start: PointNM, second_end: PointNM
) -> bool:
    d1 = _cross(second_start, second_end, first_start)
    d2 = _cross(second_start, second_end, first_end)
    d3 = _cross(first_start, first_end, second_start)
    d4 = _cross(first_start, first_end, second_end)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True

    def on_segment(start: PointNM, end: PointNM, probe: PointNM) -> bool:
        return (
            _cross(start, end, probe) == 0
            and min(start.x, end.x) <= probe.x <= max(start.x, end.x)
            and min(start.y, end.y) <= probe.y <= max(start.y, end.y)
        )

    return (
        on_segment(second_start, second_end, first_start)
        or on_segment(second_start, second_end, first_end)
        or on_segment(first_start, first_end, second_start)
        or on_segment(first_start, first_end, second_end)
    )


def _rect_corners(rect: Rect) -> tuple[PointNM, PointNM, PointNM, PointNM]:
    return (
        PointNM(rect[0], rect[1]),
        PointNM(rect[2], rect[1]),
        PointNM(rect[2], rect[3]),
        PointNM(rect[0], rect[3]),
    )


def rect_touches_ring(rect: Rect, ring: Ring) -> bool:
    """Whether a box meets a polygon at all: overlapping area or a crossing boundary."""

    if not rects_overlap(rect, ring_bounds(ring)):
        return False
    corners = _rect_corners(rect)
    if any(point_in_ring(corner, ring) for corner in corners):
        return True
    points = ring.points
    if any(rect[0] <= point.x <= rect[2] and rect[1] <= point.y <= rect[3] for point in points):
        return True
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        for corner_index in range(4):
            if _segments_intersect(
                corners[corner_index], corners[(corner_index + 1) % 4], start, end
            ):
                return True
    return False


def rect_inside_ring(rect: Rect, ring: Ring) -> bool:
    """Whether a box lies wholly inside a polygon.

    Both conditions are needed: every corner inside rules out a box sitting outside, and no
    edge crossing rules out a box that spans a concave notch while keeping its corners in.
    """

    corners = _rect_corners(rect)
    if not all(point_in_ring(corner, ring) for corner in corners):
        return False
    points = ring.points
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        for corner_index in range(4):
            if _segments_intersect(
                corners[corner_index], corners[(corner_index + 1) % 4], start, end
            ):
                return False
    return True


def rotate_offset(offset: PointNM, orientation_udeg: int) -> PointNM:
    """Turn a footprint-local offset into the board frame.

    KiCad stores y downward while its ``(at x y angle)`` angle is counter-clockwise on screen,
    so a positive quarter turn maps ``(x, y)`` to ``(y, -x)``. This is the same table the Board
    IR adapter uses, and the mirrored reading of it is the defect that once swapped the pads of
    every rotated two-pad footprint.
    """

    if orientation_udeg % QUARTER_UDEG != 0:
        raise ValueError("placement supports orthogonal orientations only")
    turn = orientation_udeg // QUARTER_UDEG % 4
    return (
        offset,
        PointNM(offset.y, -offset.x),
        PointNM(-offset.x, -offset.y),
        PointNM(-offset.y, offset.x),
    )[turn]


__all__ = [
    "FULL_UDEG",
    "QUARTER_UDEG",
    "Rect",
    "ceil_sqrt",
    "pad_bounds",
    "pad_core",
    "pad_half_extents",
    "point_in_ring",
    "rect_gap",
    "rect_inside_ring",
    "rect_touches_ring",
    "rects_overlap",
    "ring_bounds",
    "rotate_offset",
    "translate",
    "union",
]
