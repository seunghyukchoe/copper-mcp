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

from collections.abc import Callable
from itertools import pairwise
from math import isqrt

from copper_mcp.board_ir import CourtyardCircle, Pad, PadShape, PointNM, Ring, signed_double_area

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


#: Nanometres KiCad 10.0.5 contracts each cached courtyard outline by.
#:
#: ``FOOTPRINT::BuildCourtyardCaches`` polygonises the courtyard at ``maxError = 0.005 mm``
#: (``pcbnew/footprint.cpp:3701``) and then applies ``Inflate( -maxError, ... )`` to the cached
#: front and back outlines (``:3712`` and ``:3741``).  The source comment at ``:3709`` states the
#: intent: "Touching courtyards, or courtyards -at- the clearance distance are legal."  The
#: contraction compensates for arc/circle polygonisation deviation; for the exactly orthogonal
#: subset modelled here that deviation is zero, so the contraction is pure slack.
COURTYARD_CACHE_INSET_NM = 5_000

#: Both footprints' caches are contracted, so KiCad's zero-clearance courtyard collision needs
#: this much nominal penetration.  Reproduced against real ``kicad-cli`` 10.0.5: 9,999 nm is
#: clear and 10,000 nm collides, on both edge-on and corner-only overlap.
COURTYARD_COLLISION_THRESHOLD_NM = 2 * COURTYARD_CACHE_INSET_NM


def orthogonal_rings_overlap_open(
    first: Ring, second: Ring, *, charge: Callable[[], None] | None = None
) -> bool:
    """Return whether two simple orthogonal rings share a positive-area interior.

    All calculations use doubled integer coordinates.  Each open horizontal strip between
    consecutive vertex y-coordinates has a constant even-odd interior, so a midpoint scan finds
    boundary crossings as exact vertical-edge x coordinates.  Intersecting the paired intervals
    detects proper crossings and strict containment while deliberately treating an edge or corner
    touch as clear.  Callers must supply simple axis-aligned rings: Board IR now validates
    courtyard rings as octilinear, and a diagonal-bearing ring must go through the bracketed
    bounds of :func:`courtyard_region_overlap` rather than through this scan.

    This is raw ring-versus-ring geometry.  It is *not* KiCad's courtyard question: it treats every
    ring as a solid and ignores the cached inset.  Courtyard legality goes through
    :func:`orthogonal_courtyard_region_overlap` instead.
    """

    return _region_overlap_witness((first,), (second,), charge=charge)[0]


def orthogonal_courtyard_region_overlap(
    first: tuple[Ring, ...],
    second: tuple[Ring, ...],
    *,
    inset_nm: int = COURTYARD_CACHE_INSET_NM,
    charge: Callable[[], None] | None = None,
) -> str:
    """Compare two footprints' whole courtyard regions the way KiCad 10.0.5 does.

    Two things distinguish this from a ring-by-ring solid comparison, and both are KiCad's own
    semantics rather than a modelling choice:

    * **A footprint's rings form one region, filled even-odd.** KiCad builds the cache with
      ``buildContourHierarchy`` (``pcbnew/convert_shape_list_to_polygon.cpp:373-400``), which counts
      how many other contours contain a contour's first point; an even count makes it an outline
      (``:411``) and an odd count makes it a hole in the parent with one fewer parents (``:446``).
      A ring nested inside another is therefore a *hole*, not a second solid, so the centre of a
      donut courtyard is legitimately occupiable.
    * **Each region is contracted by** :data:`COURTYARD_CACHE_INSET_NM` **before the collision
      test**, so collision needs :data:`COURTYARD_COLLISION_THRESHOLD_NM` of penetration.

    The answer is three-valued, because contraction is only exactly modelled where a witness
    rectangle can be produced:

    ``proven_clear``
        The raw regions do not share positive area.  Contraction only shrinks a region — the
        outline moves inward and any hole grows — so a raw miss is a proof that the contracted
        caches miss too, whatever the ring shapes are.
    ``violated``
        The shared area contains an axis-aligned rectangle at least
        :data:`COURTYARD_COLLISION_THRESHOLD_NM` on both sides.  Contracting that rectangle by
        ``inset_nm`` per side leaves a non-empty closed set inside both contracted regions, which
        is exactly what KiCad's zero-clearance ``Collide`` reports.
    ``inconclusive``
        The regions share area, but no such witness was found — the penetration is inside the band
        where CopperMCP's geometry and KiCad's contracted cache genuinely disagree, or the shared
        area is a shape this scan does not certify.  Reporting ``violated`` here would assert a
        collision KiCad does not make, and reporting ``proven_clear`` would assert a clearance the
        geometry does not have, so neither claim is made.
    """

    return courtyard_region_overlap(first, second, inset_nm=inset_nm, charge=charge)


def courtyard_region_overlap(
    first_rings: tuple[Ring, ...],
    second_rings: tuple[Ring, ...],
    *,
    first_circles: tuple[CourtyardCircle, ...] = (),
    second_circles: tuple[CourtyardCircle, ...] = (),
    inset_nm: int = COURTYARD_CACHE_INSET_NM,
    charge: Callable[[], None] | None = None,
) -> str:
    """Three-valued courtyard collision over octilinear rings and exact circles.

    The orthogonal subset keeps ADR-0075's exact treatment: the raw regions are scanned once,
    a shared axis-aligned rectangle at least ``2 * inset_nm`` on both axes proves ``violated``,
    raw disjointness proves ``proven_clear``, and the band between is ``inconclusive``.

    The two shapes KiCad's cache does not share exactly with that scan are bracketed instead
    of guessed, and each bracket's *direction* is what licenses its claim:

    * ``proven_clear`` comes only from an **outer** bound.  Each region is replaced by a
      superset (a chamfer corner restored to its right angle, a circle by its bounding box).
      KiCad's cached courtyard is a subset of the raw shape - a polygon's cache is the shape
      contracted by the inset, and a circle is polygonised *inward* before that contraction -
      so disjoint outer bounds prove the caches are disjoint.
    * ``violated`` comes only from an **inner** bound.  Each region is replaced by a subset
      (a chamfer cut back to its corner square, a circle by an inscribed rectilinear cross).
      A witness rectangle large enough to survive each side's worst-case cache loss proves
      the contracted caches meet, so KiCad's zero-clearance collide provably fires.  A
      polygon loses exactly ``inset_nm``; a circle loses up to ``2 * inset_nm`` because its
      cache is polygonised inward before contraction, so a circle-bearing region doubles the
      witness requirement.
    * Anything the brackets cannot certify is ``inconclusive``.  Widening a keep-out is
      conservative for collision, but this surface publishes per-rule *evidence*: reporting
      ``violated`` out of an outer bound would assert an overlap KiCad may not report, and
      reporting ``proven_clear`` out of an inner bound would assert a clearance nobody proved.

    A pair of lone circles is decided by the exact integer distance predicate instead, which
    leaves only the deliberate threshold band inconclusive.
    """

    if inset_nm < 0:
        raise ValueError("courtyard inset must not be negative")
    threshold = 2 * inset_nm
    if (
        not first_rings
        and not second_rings
        and len(first_circles) == 1
        and len(second_circles) == 1
    ):
        return _circle_pair_overlap(first_circles[0], second_circles[0], threshold=threshold)
    first_outer, first_inner = _region_envelopes(first_rings, first_circles, charge=charge)
    second_outer, second_inner = _region_envelopes(second_rings, second_circles, charge=charge)
    if first_outer is first_rings and second_outer is second_rings:
        # Both regions are purely orthogonal: outer, inner, and raw coincide, so one scan
        # decides all three claims exactly as before this function learned new shapes.
        overlaps, witnessed = _region_overlap_witness(
            first_rings, second_rings, threshold=threshold, charge=charge
        )
        if witnessed:
            return "violated"
        return "inconclusive" if overlaps else "proven_clear"
    if not _region_overlap_witness(first_outer, second_outer, charge=charge)[0]:
        return "proven_clear"
    # A circle's cache loses up to one extra inset of inward polygonisation sagitta on top of
    # the cache contraction, so when either region carries a circle the witness rectangle
    # must survive twice the loss - and strictly, because the exact-threshold contact is a
    # measured fact only for the polygon-on-polygon case.
    witness_threshold = threshold if not first_circles and not second_circles else 2 * threshold + 1
    if (
        first_inner
        and second_inner
        and _region_overlap_witness(
            first_inner, second_inner, threshold=witness_threshold, charge=charge
        )[1]
    ):
        return "violated"
    return "inconclusive"


def _circle_pair_overlap(first: CourtyardCircle, second: CourtyardCircle, *, threshold: int) -> str:
    """Exact three-valued verdict for two lone circular courtyards.

    ``proven_clear`` when the raw discs share no area: KiCad polygonises a courtyard circle
    *inward* (vertices on the circle, chords inside) and then contracts the cache, so each
    cache is a subset of its disc and disjoint discs prove disjoint caches.  The same inward
    polygonisation means a circle's cache can lose up to ``maxError`` of chord sagitta *plus*
    the ``maxError`` cache contraction - twice the loss an exact polygon suffers - so
    ``violated`` is only provable once the discs contracted by ``threshold`` each (both
    losses, both sides) still share positive area.  Measured against ``kicad-cli`` 10.0.5,
    the real boundary for one circle pair fell between 12,000 and 15,000 nm of penetration
    and depends on where the polygon vertices land, so the band up to ``2 * threshold`` is
    deliberately conceded rather than fitted, exactly like ADR-0075's tiny-shape band.
    """

    delta_x = first.center.x - second.center.x
    delta_y = first.center.y - second.center.y
    distance_squared = delta_x * delta_x + delta_y * delta_y
    radius_sum = first.radius_nm + second.radius_nm
    if distance_squared >= radius_sum * radius_sum:
        return "proven_clear"
    contracted = radius_sum - 2 * threshold
    if contracted > 0 and distance_squared < contracted * contracted:
        return "violated"
    return "inconclusive"


def _ring_has_diagonal(ring: Ring) -> bool:
    points = ring.points
    return any(
        start.x != points[(index + 1) % len(points)].x
        and start.y != points[(index + 1) % len(points)].y
        for index, start in enumerate(points)
    )


def _rect_ring(rect: Rect) -> Ring:
    return Ring(
        (
            PointNM(rect[0], rect[1]),
            PointNM(rect[2], rect[1]),
            PointNM(rect[2], rect[3]),
            PointNM(rect[0], rect[3]),
        )
    )


def _circle_outer_ring(circle: CourtyardCircle) -> Ring:
    """The circumscribed axis-aligned square: an exact integer superset of the disc."""

    return _rect_ring(
        (
            circle.center.x - circle.radius_nm,
            circle.center.y - circle.radius_nm,
            circle.center.x + circle.radius_nm,
            circle.center.y + circle.radius_nm,
        )
    )


def _circle_inner_ring(circle: CourtyardCircle) -> Ring | None:
    """An inscribed rectilinear cross: an exact integer subset of the disc, or ``None``.

    The cross is the union of a wide bar ``2a`` by ``2e`` and a tall bar ``2e`` by ``2a``
    with ``a = isqrt(8 r^2 / 9)`` and ``e = isqrt(r^2 - a^2)``, so every vertex satisfies
    ``x^2 + y^2 <= r^2`` by construction.  It keeps almost the full diameter available on
    each axis for the ``violated`` witness search, which an inscribed square would not.
    """

    radius = circle.radius_nm
    long_half = isqrt(8 * radius * radius // 9)
    short_half = isqrt(radius * radius - long_half * long_half)
    if short_half <= 0:
        square_half = isqrt(radius * radius // 2)
        if square_half <= 0:
            return None
        return _rect_ring(
            (
                circle.center.x - square_half,
                circle.center.y - square_half,
                circle.center.x + square_half,
                circle.center.y + square_half,
            )
        )
    x, y = circle.center.x, circle.center.y
    a, e = long_half, short_half
    if e >= a:
        return _rect_ring((x - e, y - e, x + e, y + e))
    return Ring(
        (
            PointNM(x - a, y - e),
            PointNM(x - e, y - e),
            PointNM(x - e, y - a),
            PointNM(x + e, y - a),
            PointNM(x + e, y - e),
            PointNM(x + a, y - e),
            PointNM(x + a, y + e),
            PointNM(x + e, y + e),
            PointNM(x + e, y + a),
            PointNM(x - e, y + a),
            PointNM(x - e, y + e),
            PointNM(x - a, y + e),
        )
    )


def _diagonal_corners(ring: Ring) -> tuple[tuple[int, PointNM, PointNM], ...] | None:
    """Locate every diagonal edge with its exterior corner, or ``None`` when unsafe.

    For a diagonal edge ``P -> Q`` the two axis-aligned completions are ``(P.x, Q.y)`` and
    ``(Q.x, P.y)``; one lies on the ring's interior side and one on the exterior side, decided
    exactly by the ring orientation.  The replacement is only *sound* - the region changes by
    exactly the corner triangles - when each closed triangle ``P, corner, Q`` touches no other
    part of the ring and the triangles keep clear of one another.  Any arrangement this cannot
    certify returns ``None``, and the caller degrades to a bound that claims strictly less.
    """

    area = signed_double_area(ring.points)
    if area == 0:
        return None
    points = ring.points
    size = len(points)
    found: list[tuple[int, PointNM, PointNM]] = []
    for index, start in enumerate(points):
        end = points[(index + 1) % size]
        delta_x = end.x - start.x
        delta_y = end.y - start.y
        if delta_x == 0 or delta_y == 0:
            continue
        # ``(P.x, Q.y)`` sits on the left of the directed edge exactly when dx * dy > 0, and
        # the interior is on the left exactly when the signed area is positive.
        first_is_left = delta_x * delta_y > 0
        interior_left = area > 0
        if first_is_left == interior_left:
            interior = PointNM(start.x, end.y)
            exterior = PointNM(end.x, start.y)
        else:
            interior = PointNM(end.x, start.y)
            exterior = PointNM(start.x, end.y)
        for corner in (interior, exterior):
            if not _triangle_is_clear(ring, index, corner):
                return None
        found.append((index, interior, exterior))
    for position, (first_index, first_interior, first_exterior) in enumerate(found):
        for second_index, second_interior, second_exterior in found[position + 1 :]:
            first_start, first_end = points[first_index], points[(first_index + 1) % size]
            second_start, second_end = points[second_index], points[(second_index + 1) % size]
            if {first_start, first_end} & {second_start, second_end}:
                # Adjacent chamfers share a ring vertex; their new edges meet there by
                # construction, which the pairwise crossing test below cannot distinguish
                # from a genuine overlap.  Degrading is conservative, never wrong.
                return None
            for first_corner in (first_interior, first_exterior):
                for second_corner in (second_interior, second_exterior):
                    for a, b in ((first_start, first_corner), (first_corner, first_end)):
                        for c, d in (
                            (second_start, second_corner),
                            (second_corner, second_end),
                        ):
                            if _segments_intersect(a, b, c, d):
                                return None
    return tuple(found)


def _collinear_beyond(shared: PointNM, along: PointNM, probe: PointNM) -> bool:
    """Whether ``probe`` lies on the ray from ``shared`` through ``along``."""

    return (
        _cross(shared, along, probe) == 0
        and (along.x - shared.x) * (probe.x - shared.x)
        + (along.y - shared.y) * (probe.y - shared.y)
        > 0
    )


def _triangle_is_clear(ring: Ring, edge_index: int, corner: PointNM) -> bool:
    """Whether the closed triangle over one diagonal edge avoids the rest of the ring.

    Soundness of the corner replacement rests on this: when the closed triangle
    ``start, corner, end`` touches no other part of the ring, inserting or removing the
    corner changes the even-odd region by exactly that triangle.  Every failure mode is
    checked exactly in integers: another vertex inside the closed triangle, a non-adjacent
    edge meeting any triangle side, an adjacent edge crossing the opposite side, or an
    adjacent edge doubling back along a triangle side through its shared vertex.
    """

    points = ring.points
    size = len(points)
    start = points[edge_index]
    end = points[(edge_index + 1) % size]
    if corner in points:
        return False
    for vertex in points:
        if vertex in (start, end):
            continue
        if _point_in_closed_triangle(vertex, start, corner, end):
            return False
    for index in range(size):
        if index == edge_index:
            continue
        edge_start = points[index]
        edge_end = points[(index + 1) % size]
        if edge_end == start:
            # The edge arriving at ``start``: it must stay off the opposite side and must
            # not run back along either triangle side through ``start``.
            if _segments_intersect(edge_start, edge_end, corner, end):
                return False
            if _collinear_beyond(start, corner, edge_start) or _collinear_beyond(
                start, end, edge_start
            ):
                return False
            continue
        if edge_start == end:
            # The edge leaving ``end``: the mirror image of the case above.
            if _segments_intersect(edge_start, edge_end, start, corner):
                return False
            if _collinear_beyond(end, corner, edge_end) or _collinear_beyond(end, start, edge_end):
                return False
            continue
        for side_start, side_end in ((start, corner), (corner, end), (start, end)):
            if _segments_intersect(edge_start, edge_end, side_start, side_end):
                return False
    return True


def _point_in_closed_triangle(point: PointNM, a: PointNM, b: PointNM, c: PointNM) -> bool:
    first = _cross(a, b, point)
    second = _cross(b, c, point)
    third = _cross(c, a, point)
    return (first >= 0 and second >= 0 and third >= 0) or (
        first <= 0 and second <= 0 and third <= 0
    )


def _orthogonalized_ring(
    ring: Ring, corners: tuple[tuple[int, PointNM, PointNM], ...], *, grow: bool
) -> Ring | None:
    """Replace certified diagonal edges with their corner completions, or ``None``."""

    replacement = {index: exterior if grow else interior for index, interior, exterior in corners}
    points: list[PointNM] = []
    for index, start in enumerate(ring.points):
        points.append(start)
        corner = replacement.get(index)
        if corner is not None:
            points.append(corner)
    try:
        return Ring(tuple(points))
    except ValueError:
        return None


def _region_envelopes(
    rings: tuple[Ring, ...],
    circles: tuple[CourtyardCircle, ...],
    *,
    charge: Callable[[], None] | None,
) -> tuple[tuple[Ring, ...], tuple[Ring, ...]]:
    """Outer and inner orthogonal even-odd bounds for one footprint's courtyard region.

    Returns ``(outer, inner)`` with ``inner-region <= raw region <= outer-region`` as point
    sets.  For a purely orthogonal ring set the input tuple itself is returned for both, which
    callers use to detect that the bound is exact.  ``inner`` may be empty, which claims
    nothing.  Board IR validation guarantees circles are box-disjoint from every other shape
    of the same footprint, so pooling their rings keeps even-odd equal to union.
    """

    diagonal = [_ring_has_diagonal(ring) for ring in rings]
    if not any(diagonal) and not circles:
        return rings, rings
    outer: list[Ring] = []
    inner: list[Ring] = []
    if not any(diagonal):
        outer.extend(rings)
        inner.extend(rings)
    elif len(rings) == 1:
        if charge is not None:
            for _ in range(len(rings[0].points)):
                charge()
        corners = _diagonal_corners(rings[0])
        grown = _orthogonalized_ring(rings[0], corners, grow=True) if corners is not None else None
        shrunk = (
            _orthogonalized_ring(rings[0], corners, grow=False) if corners is not None else None
        )
        outer.append(grown if grown is not None else _rect_ring(ring_bounds(rings[0])))
        if shrunk is not None:
            inner.append(shrunk)
    elif rings:
        # Multiple rings with a diagonal present: the nesting interplay of grown and shrunk
        # contours is not certified, so claim only what a bounding box entitles us to.
        outer.append(_rect_ring(_region_bounds(rings)))
    for circle in circles:
        if charge is not None:
            charge()
        outer.append(_circle_outer_ring(circle))
        circle_inner = _circle_inner_ring(circle)
        if circle_inner is not None:
            inner.append(circle_inner)
    return tuple(outer), tuple(inner)


def _region_overlap_witness(
    first: tuple[Ring, ...],
    second: tuple[Ring, ...],
    *,
    threshold: int | None = None,
    charge: Callable[[], None] | None = None,
) -> tuple[bool, bool]:
    """Scan two even-odd orthogonal regions for shared area and a ``threshold``-square witness.

    Returns ``(shares_positive_area, found_witness)``.  Within an open horizontal strip between
    consecutive vertex y-coordinates the region is constant in y, so each shared x-interval spans
    the full strip: interval width by strip height is a rectangle genuinely inside both regions.
    The witness search is therefore sound but not complete — a shared rectangle straddling several
    strips is not assembled, which can only under-report ``violated`` as ``inconclusive``.
    """

    if not first or not second:
        return False, False
    if not rects_overlap(_region_bounds(first), _region_bounds(second)):
        return False, False
    ys = sorted(
        {point.y for ring in first for point in ring.points}
        | {point.y for ring in second for point in ring.points}
    )
    overlaps = False
    for lower, upper in pairwise(ys):
        sample_y_twice = lower + upper
        if charge is not None:
            charge()
        first_intervals = _orthogonal_scanline_intervals(first, sample_y_twice, charge=charge)
        second_intervals = _orthogonal_scanline_intervals(second, sample_y_twice, charge=charge)
        height = upper - lower
        for first_left, first_right in first_intervals:
            for second_left, second_right in second_intervals:
                if charge is not None:
                    charge()
                left = max(first_left, second_left)
                right = min(first_right, second_right)
                if left >= right:
                    continue
                overlaps = True
                if threshold is None:
                    return True, False
                # Crossings are doubled coordinates, so the difference is always even.
                if (right - left) // 2 >= threshold and height >= threshold:
                    return True, True
    return overlaps, False


def _region_bounds(rings: tuple[Ring, ...]) -> Rect:
    boxes = [ring_bounds(ring) for ring in rings]
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _orthogonal_scanline_intervals(
    rings: tuple[Ring, ...], sample_y_twice: int, *, charge: Callable[[], None] | None
) -> tuple[tuple[int, int], ...]:
    """Return doubled-x open interior intervals of an even-odd region at a strip sample.

    Crossings from every ring in the region are pooled before pairing, which is what makes a ring
    nested inside another read as a hole rather than as a second solid.
    """

    crossings: list[int] = []
    for ring in rings:
        for index, start in enumerate(ring.points):
            if charge is not None:
                charge()
            end = ring.points[(index + 1) % len(ring.points)]
            if start.x == end.x:
                lower_y, upper_y = sorted((start.y * 2, end.y * 2))
                if lower_y < sample_y_twice < upper_y:
                    crossings.append(start.x * 2)
    crossings.sort()
    # Every ``Ring`` is simple and the sample avoids every vertex, so each ring contributes an even
    # number of crossings and an odd total is impossible.  A defensive empty answer nevertheless
    # prevents a malformed externally-built ring from producing a positive verdict if this helper
    # is ever called without Board-IR validation.
    if len(crossings) % 2:
        return ()
    return tuple(zip(crossings[::2], crossings[1::2], strict=True))


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
    "COURTYARD_CACHE_INSET_NM",
    "COURTYARD_COLLISION_THRESHOLD_NM",
    "FULL_UDEG",
    "QUARTER_UDEG",
    "Rect",
    "ceil_sqrt",
    "courtyard_region_overlap",
    "orthogonal_courtyard_region_overlap",
    "orthogonal_rings_overlap_open",
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
