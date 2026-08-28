"""Exact-integer inscribed polylines for circular ``Edge.Cuts`` outline arcs.

The board outline is routing **room**, not an obstacle, so it may only ever be
*under*-approximated: a modelled outline larger than the drawn one hands a caller area the
fabricated board does not have.  That rule is what makes an arc hard, and it is why this module
exists separately from the adapter that calls it — the containment argument is a geometric claim
and it is tested as one.

**The convex region that carries the whole proof.**  A KiCad arc is three drawn integer points —
``start``, ``mid``, ``end`` — on one circle of centre ``O`` and radius ``r``.  Let ``H`` be the
closed half-plane bounded by the chord ``start``-``end`` that contains ``mid``.  The region
bounded by the chord and the arc is exactly

    S  =  disc(O, r)  ∩  H

which is an intersection of two convex sets and therefore **convex**, for a minor arc and a major
one alike.  Convexity is the entire load-bearing fact: a polyline whose *vertices* all lie in ``S``
has all of its *edges* in ``S`` too, so containment can be decided per vertex by two exact integer
predicates and never needs a segment-versus-circle test.

**Direction, per arc, decided by the ring not by the arc.**  Whether replacing an arc by points
inside ``S`` shrinks or grows the board depends on which side of the chord the board's interior is:

* **Convex** arc — the arc bulges *away* from the interior.  ``S`` is board material, so a polyline
  through ``S`` gives back a region contained in the true one.  Safe, and this module builds it.
* **Concave** arc — the arc cuts *into* the board, and ``S`` is exactly the material the cut
  removed.  A polyline through ``S`` claims removed material as board: an over-approximation, and
  the forbidden direction.  The safe construction there is the mirror image — a polyline *outside*
  the disc, built from tangent segments — and its safe region, ``complement(disc) ∩ H``, is **not
  convex**, so it needs an exact per-edge distance test rather than two per-vertex ones.  This
  module does not build it; the caller refuses a concave arc by name.  See ADR-0124.

Nothing here rounds "to the nearest point on the arc" and hopes.  Sample positions are chosen with
floating point, because a *count* and a *position* need no proof, and then every candidate vertex
is **verified** against ``S`` in exact integer arithmetic.  A vertex that cannot be verified is
dropped, never nudged outward: dropping a vertex leaves a coarser polyline that is still inside
``S``, so the failure mode of this module is a less useful board, never a larger one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise
from math import isqrt

from copper_mcp.board_ir.types import PointNM

__all__ = [
    "ArcInscription",
    "OutlineArcError",
    "arc_is_minor",
    "chord_side",
    "circumcentre",
    "inscribe_outline_arc",
]

#: How far the modelled boundary may fall inside the drawn arc, in nanometres.
#:
#: This is KiCad's own ``maxError`` for turning board graphics into polygons —
#: ``pcbIUScale.mmToIU( 0.005 )``, 5,000 nm — which ``ConvertOutlineToPolygon`` applies to the
#: board outline and ``FOOTPRINT::BuildCourtyardCaches`` applies to courtyards
#: (docs/research/courtyard-curved-shapes-v1.md pins the second against KiCad 10.0.5).  Borrowing
#: the constant is deliberate: it is the deviation KiCad itself accepts when it polygonises the
#: same shape for its own design-rule checks, so a caller comparing against KiCad is not being
#: handed a coarser board than KiCad works with.  The difference is the **sign**: KiCad's bound is
#: two-sided and this one is strictly inward.
DEFAULT_MAX_SAGITTA_NM = 5_000

#: Bound on how many 1 nm pulls toward the centre a candidate vertex may take before it is dropped.
#: Rounding a point on a circle of radius at least one micrometre to the integer lattice moves it
#: by less than one nanometre in the radial direction, so a single pull is enough in practice and
#: four is a guard, not a tuning knob.
_MAX_PULLS = 4

_NEIGHBOURS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


class OutlineArcError(ValueError):
    """An outline arc that carries no circle at all."""


@dataclass(frozen=True, slots=True)
class ArcInscription:
    """The interior vertices of one inscribed arc, and how far inside the arc they run.

    ``points`` excludes ``start`` and ``end``: those are drawn points the caller already holds and
    shares with the neighbouring outline edges, so repeating them here would invite a ring with a
    duplicated vertex.  ``inward_deviation_nm`` is an **upper bound**, computed by taking the
    radius up and the polyline's closest approach down, so it is never smaller than the truth.
    """

    points: tuple[PointNM, ...]
    inward_deviation_nm: int

    def __post_init__(self) -> None:
        if not isinstance(self.points, tuple) or not all(
            isinstance(item, PointNM) for item in self.points
        ):
            raise ValueError("inscribed arc points must be an immutable tuple of points")
        if isinstance(self.inward_deviation_nm, bool) or not isinstance(
            self.inward_deviation_nm, int
        ):
            raise ValueError("inward deviation must be an integer nanometre count")
        if self.inward_deviation_nm < 0:
            raise ValueError("inward deviation must not be negative")


def circumcentre(start: PointNM, mid: PointNM, end: PointNM) -> tuple[Fraction, Fraction]:
    """Return the exact rational centre of the circle through three integer points.

    Three integer points put the centre at a rational coordinate and the radius at the square root
    of a rational, which is why every predicate below is written against ``r**2`` and never
    against ``r``.  Collinear points describe no circle and raise rather than returning something
    a caller could mistake for one.
    """

    ax, ay = start.x, start.y
    bx, by = mid.x, mid.y
    cx, cy = end.x, end.y
    determinant = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if determinant == 0:
        raise OutlineArcError("three collinear points describe no arc")
    sa = ax * ax + ay * ay
    sb = bx * bx + by * by
    sc = cx * cx + cy * cy
    ux = sa * (by - cy) + sb * (cy - ay) + sc * (ay - by)
    uy = sa * (cx - bx) + sb * (ax - cx) + sc * (bx - ax)
    return Fraction(ux, determinant), Fraction(uy, determinant)


def chord_side(start: PointNM, end: PointNM, point: PointNM) -> int:
    """Return the exact sign of ``point`` against the directed chord ``start``->``end``."""

    value = (end.x - start.x) * (point.y - start.y) - (end.y - start.y) * (point.x - start.x)
    return (value > 0) - (value < 0)


def arc_is_minor(start: PointNM, mid: PointNM, end: PointNM) -> bool:
    """Whether the arc through ``mid`` spans at most a half turn, by one integer dot product.

    The inscribed-angle theorem puts the angle subtended at ``mid`` by the chord at half the arc
    that does **not** contain ``mid``.  So that angle exceeds a right angle — a negative dot
    product — exactly when the complementary arc is major, which is exactly when the arc through
    ``mid`` is minor.  No trigonometry, no rounding, and the boundary case (a semicircle, dot
    product zero) counts as minor.
    """

    ax, ay = start.x - mid.x, start.y - mid.y
    bx, by = end.x - mid.x, end.y - mid.y
    return ax * bx + ay * by <= 0


def _scaled(point: PointNM, denominator: int) -> tuple[int, int]:
    return point.x * denominator, point.y * denominator


def _distance_squared(point: tuple[int, int], centre: tuple[int, int]) -> int:
    dx = point[0] - centre[0]
    dy = point[1] - centre[1]
    return dx * dx + dy * dy


def _isqrt_ceiling(value: int) -> int:
    root = isqrt(value)
    return root if root * root == value else root + 1


def _segment_distance_squared(
    a: tuple[int, int], b: tuple[int, int], centre: tuple[int, int]
) -> Fraction:
    """Exact squared distance from ``centre`` to the closed segment ``a``-``b``."""

    vx, vy = b[0] - a[0], b[1] - a[1]
    wx, wy = centre[0] - a[0], centre[1] - a[1]
    projection = wx * vx + wy * vy
    length_squared = vx * vx + vy * vy
    if length_squared == 0 or projection <= 0:
        return Fraction(wx * wx + wy * wy)
    if projection >= length_squared:
        return Fraction(_distance_squared(centre, b))
    # |w|^2 - (w.v)^2/|v|^2, kept exact rather than evaluated in floating point.
    return Fraction((wx * wx + wy * wy) * length_squared - projection * projection, length_squared)


def _floor_root(value: Fraction) -> Fraction:
    """A rational at most the true square root of ``value``."""

    return Fraction(isqrt(value.numerator * value.denominator), value.denominator)


def _ceiling_root(value: Fraction) -> Fraction:
    """A rational at least the true square root of ``value``."""

    return Fraction(_isqrt_ceiling(value.numerator * value.denominator), value.denominator)


def _sweep(
    start: PointNM, mid: PointNM, end: PointNM, centre: tuple[float, float]
) -> tuple[float, float, float]:
    """Return the start angle, the signed step direction, and the sweep magnitude."""

    def angle(point: PointNM) -> float:
        return math.atan2(point.y - centre[1], point.x - centre[0])

    def normalise(value: float) -> float:
        return value % (2 * math.pi)

    start_angle = angle(start)
    counter_clockwise = normalise(angle(end) - start_angle)
    through_mid = normalise(angle(mid) - start_angle)
    if through_mid <= counter_clockwise:
        return start_angle, 1.0, counter_clockwise
    return start_angle, -1.0, 2 * math.pi - counter_clockwise


def inscribe_outline_arc(
    start: PointNM,
    mid: PointNM,
    end: PointNM,
    *,
    max_sagitta_nm: int = DEFAULT_MAX_SAGITTA_NM,
    max_points: int,
) -> ArcInscription:
    """Return integer vertices strictly inside the chord-and-arc region, and the deviation bound.

    Every returned vertex satisfies both exact predicates that define ``S``: it lies in the closed
    disc, and it lies on ``mid``'s side of the chord.  Because ``S`` is convex, that is the whole
    containment proof — the caller may join the vertices in order and the resulting polyline never
    leaves ``S``.

    ``max_points`` is a hard ceiling and not a target.  A vertex the exact predicates reject is
    dropped, so this function can return fewer points than the sagitta rule asked for; the returned
    ``inward_deviation_nm`` is measured against the vertices actually produced, so a coarser
    polyline reports a larger deviation rather than a false one.
    """

    if max_points < 0:
        raise ValueError("max_points must not be negative")
    if max_sagitta_nm < 1:
        raise ValueError("max sagitta must be a positive nanometre count")
    if start == end or start == mid or mid == end:
        raise OutlineArcError("an outline arc needs three distinct points")
    centre_x, centre_y = circumcentre(start, mid, end)
    denominator = (
        centre_x.denominator
        * centre_y.denominator
        // math.gcd(centre_x.denominator, centre_y.denominator)
    )
    centre = (
        int(centre_x * denominator),
        int(centre_y * denominator),
    )
    scaled_start = _scaled(start, denominator)
    radius_squared = _distance_squared(scaled_start, centre)
    radius = math.sqrt(radius_squared) / denominator
    float_centre = (float(centre_x), float(centre_y))
    start_angle, direction, sweep = _sweep(start, mid, end, float_centre)

    # How many sub-chords keep every sagitta under the bound.  Floating point picks a *count*; the
    # predicates below decide whether any particular vertex is admissible, so an inaccurate count
    # costs vertices, never containment.
    if radius <= max_sagitta_nm:
        steps = 2
    else:
        step_angle = 2 * math.acos(max(-1.0, min(1.0, 1 - max_sagitta_nm / radius)))
        steps = max(2, math.ceil(sweep / step_angle)) if step_angle > 0 else 2
    steps = min(steps, max_points + 1)

    mid_side = chord_side(start, end, mid)
    points: list[PointNM] = []
    seen = {start, end}
    for index in range(1, steps):
        angle = start_angle + direction * sweep * index / steps
        candidate = PointNM(
            round(float_centre[0] + radius * math.cos(angle)),
            round(float_centre[1] + radius * math.sin(angle)),
        )
        verified = _pull_inside(candidate, centre, denominator, radius_squared)
        if verified is None:
            continue
        if chord_side(start, end, verified) not in (mid_side, 0):
            continue
        if verified in seen:
            continue
        seen.add(verified)
        points.append(verified)

    # Every length so far lives in the lattice scaled by ``denominator``, because that is what
    # made the containment predicates exact integers.  The deviation is a *nanometre* quantity, so
    # it is divided back out here -- once, at the end, after the bounds have been taken outward.
    chain = [start, *points, end]
    worst = Fraction(0)
    radius_upper = _ceiling_root(Fraction(radius_squared))
    for first, second in pairwise(chain):
        closest = _segment_distance_squared(
            _scaled(first, denominator), _scaled(second, denominator), centre
        )
        # Radius rounded up and the polyline's closest approach rounded down, so the difference is
        # never smaller than the true deviation.  A bound that could understate would be worse
        # than no bound at all: a caller reads this to decide whether it may still make a claim.
        worst = max(worst, radius_upper - _floor_root(closest))
    scaled = worst / denominator
    return ArcInscription(
        points=tuple(points),
        inward_deviation_nm=max(0, -((-scaled.numerator) // scaled.denominator)),
    )


def _pull_inside(
    candidate: PointNM,
    centre: tuple[int, int],
    denominator: int,
    radius_squared: int,
) -> PointNM | None:
    """Move a rounded candidate onto the closed disc, or give up on it.

    Rounding a point on the circle to the integer lattice can land it a nanometre outside, and a
    vertex outside the disc is the one thing this module may never emit.  Stepping toward the
    centre is the only correction offered; there is deliberately no step that could move a vertex
    outward, so a bug here can lose a vertex and cannot grow a board.
    """

    for _ in range(_MAX_PULLS + 1):
        if _distance_squared(_scaled(candidate, denominator), centre) <= radius_squared:
            return candidate
        best: PointNM | None = None
        best_distance: int | None = None
        for dx, dy in _NEIGHBOURS:
            neighbour = PointNM(candidate.x + dx, candidate.y + dy)
            distance = _distance_squared(_scaled(neighbour, denominator), centre)
            if best_distance is None or distance < best_distance:
                best, best_distance = neighbour, distance
        if best is None:
            return None
        candidate = best
    return None
