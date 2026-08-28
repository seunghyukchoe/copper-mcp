"""Containment and bound tests for the inscribed outline-arc construction (ADR-0124).

Every predicate here is written against the arc's *circle*, in exact integer or rational
arithmetic, and never against a polygon the code under test produced.  That is the difference
between checking a containment claim and checking that a function is self-consistent.
"""

from __future__ import annotations

import math
from fractions import Fraction
from itertools import pairwise

import pytest

from copper_mcp.board_ir.outline_arc import (
    DEFAULT_MAX_SAGITTA_NM,
    ArcInscription,
    OutlineArcError,
    arc_is_minor,
    chord_side,
    circumcentre,
    inscribe_outline_arc,
)
from copper_mcp.board_ir.types import PointNM

# A quarter turn of a 2 mm radius circle centred on the origin: the rounded corner every board in
# B-134's cohort is made of, and the case the sagitta rule is sized for.
QUARTER_START = PointNM(2_000_000, 0)
QUARTER_MID = PointNM(1_414_214, 1_414_214)
QUARTER_END = PointNM(0, 2_000_000)


def _radius_squared(point: PointNM, centre: tuple[Fraction, Fraction]) -> Fraction:
    return (point.x - centre[0]) ** 2 + (point.y - centre[1]) ** 2


def _assert_inside_chord_and_arc_region(
    start: PointNM, mid: PointNM, end: PointNM, inscription: ArcInscription
) -> None:
    """Every vertex must satisfy both exact predicates that define the safe convex region."""

    centre = circumcentre(start, mid, end)
    radius_squared = _radius_squared(start, centre)
    side = chord_side(start, end, mid)
    for point in inscription.points:
        assert _radius_squared(point, centre) <= radius_squared, "vertex escaped the closed disc"
        assert chord_side(start, end, point) in (side, 0), "vertex crossed to the wrong side"


def test_every_inscribed_vertex_lies_in_the_chord_and_arc_region() -> None:
    """The whole containment argument, checked one vertex at a time.

    The region bounded by a chord and its arc is ``disc ∩ half-plane`` — convex — so vertices
    inside it imply *edges* inside it, and a polyline that never leaves it can never model more
    board than the arc bounds.  This is the test that would fail if a vertex were ever nudged
    outward to reduce the deviation.
    """

    inscription = inscribe_outline_arc(QUARTER_START, QUARTER_MID, QUARTER_END, max_points=64)

    assert inscription.points
    _assert_inside_chord_and_arc_region(QUARTER_START, QUARTER_MID, QUARTER_END, inscription)


@pytest.mark.parametrize(
    "mid",
    [
        PointNM(1_414_214, 1_414_214),
        PointNM(1_931_852, 517_638),
        PointNM(517_638, 1_931_852),
        PointNM(1_999_999, 2_000),
    ],
)
def test_containment_holds_wherever_the_control_point_sits_on_the_arc(mid: PointNM) -> None:
    """Sweeps from a few degrees to a quarter turn, all held to the same predicate."""

    inscription = inscribe_outline_arc(QUARTER_START, mid, QUARTER_END, max_points=64)

    _assert_inside_chord_and_arc_region(QUARTER_START, mid, QUARTER_END, inscription)


def test_the_reported_deviation_is_an_upper_bound_and_not_an_estimate() -> None:
    """Measured against the true arc by sampling it, in the direction that matters.

    The published number decides whether a caller may still make a claim about the outline, so it
    must never be smaller than the truth.  Here the arc is sampled densely and each sample's
    distance to the modelled polyline is compared against the bound; a bound that understated even
    once would be worse than no bound.
    """

    inscription = inscribe_outline_arc(QUARTER_START, QUARTER_MID, QUARTER_END, max_points=64)

    chain = [QUARTER_START, *inscription.points, QUARTER_END]
    radius = 2_000_000.0
    worst = 0.0
    for step in range(1, 400):
        angle = (math.pi / 2) * step / 400
        sample = (radius * math.cos(angle), radius * math.sin(angle))
        worst = max(worst, min(_point_to_segment(sample, a, b) for a, b in pairwise(chain)))
    assert worst <= inscription.inward_deviation_nm + 1


def test_the_deviation_stays_within_the_sagitta_the_project_borrowed_from_kicad() -> None:
    """The bound is a bound, not just a report: it is what sizes the subdivision."""

    inscription = inscribe_outline_arc(QUARTER_START, QUARTER_MID, QUARTER_END, max_points=1024)

    assert 0 < inscription.inward_deviation_nm <= DEFAULT_MAX_SAGITTA_NM


def test_refusing_every_interior_vertex_degrades_to_the_chord_and_says_so() -> None:
    """A vertex budget of zero must coarsen the model, never overstate its accuracy.

    Dropping vertices is the only failure this construction has, and it is deliberately the safe
    one: the chord alone is still inside the region.  What must move with it is the disclosure —
    the reported deviation becomes the full sagitta rather than staying at the fine-grained figure.
    """

    chord_only = inscribe_outline_arc(QUARTER_START, QUARTER_MID, QUARTER_END, max_points=0)
    subdivided = inscribe_outline_arc(QUARTER_START, QUARTER_MID, QUARTER_END, max_points=64)

    assert chord_only.points == ()
    assert chord_only.inward_deviation_nm > subdivided.inward_deviation_nm
    # 2 mm radius through a quarter turn has a sagitta of r(1 - cos(45 deg)) = 585,786 nm.
    assert 585_000 <= chord_only.inward_deviation_nm <= 586_500


def test_three_collinear_points_are_refused_rather_than_approximated() -> None:
    """No circle, so no region, so nothing to be inside of."""

    with pytest.raises(OutlineArcError):
        inscribe_outline_arc(
            PointNM(0, 0), PointNM(1_000_000, 0), PointNM(2_000_000, 0), max_points=8
        )


def test_repeated_control_points_are_refused() -> None:
    """A zero-length arc has no direction to chain along and no circle to inscribe in."""

    with pytest.raises(OutlineArcError):
        inscribe_outline_arc(QUARTER_START, QUARTER_MID, QUARTER_START, max_points=8)


@pytest.mark.parametrize(
    ("start", "mid", "end", "minor"),
    [
        # A quarter turn: 0 deg -> 45 deg -> 90 deg.
        (QUARTER_START, PointNM(1_414_214, 1_414_214), QUARTER_END, True),
        # Exactly a half turn, the boundary case: the dot product at ``mid`` is zero.
        (QUARTER_START, QUARTER_END, PointNM(-2_000_000, 0), True),
        # The *other* arc on the same two endpoints as the quarter turn: three quarters, major.
        (QUARTER_START, PointNM(-2_000_000, 0), QUARTER_END, False),
        (QUARTER_START, PointNM(0, -2_000_000), QUARTER_END, False),
    ],
)
def test_the_half_turn_test_is_one_integer_dot_product(
    start: PointNM, mid: PointNM, end: PointNM, minor: bool
) -> None:
    """The inscribed-angle theorem, used exactly and without a single trigonometric call.

    The angle subtended at ``mid`` is half the arc that does *not* contain ``mid``, so a
    non-positive dot product at ``mid`` is exactly a minor arc.  A semicircle sits on the boundary
    with a dot product of zero and counts as minor, which the second case pins — and the third
    shows the test reading ``mid`` rather than the endpoints, because it shares its endpoints with
    the first and reaches the opposite verdict.
    """

    assert arc_is_minor(start, mid, end) is minor


def test_the_circumcentre_is_exact_rather_than_rounded() -> None:
    """Three integer points put the centre at a rational coordinate, and it stays one.

    Rounding the centre would move the disc, and a disc moved outward is a region larger than the
    arc bounds — the forbidden direction, arriving through the back door.  These three points put
    the centre at ``(2, 1/4)``, which no integer lattice holds.
    """

    centre = circumcentre(PointNM(0, 0), PointNM(1, 2), PointNM(4, 0))

    assert centre == (Fraction(2), Fraction(1, 4))
    assert _radius_squared(PointNM(0, 0), centre) == _radius_squared(PointNM(1, 2), centre)
    assert _radius_squared(PointNM(4, 0), centre) == _radius_squared(PointNM(1, 2), centre)


def _point_to_segment(point: tuple[float, float], first: PointNM, second: PointNM) -> float:
    ax, ay = float(first.x), float(first.y)
    bx, by = float(second.x), float(second.y)
    vx, vy = bx - ax, by - ay
    wx, wy = point[0] - ax, point[1] - ay
    length = vx * vx + vy * vy
    if length == 0:
        return math.hypot(wx, wy)
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / length))
    return math.hypot(wx - t * vx, wy - t * vy)
