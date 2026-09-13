"""Independent high-precision arithmetic checks, not physical calibration evidence."""

from decimal import ROUND_FLOOR, Decimal, localcontext
from fractions import Fraction
from random import Random

from test_optimization_clearance_score import N1, N2, _measure, _pad, _snapshot, _track


def test_point_track_margins_match_independent_projection_oracle() -> None:
    random = Random(20260908)  # noqa: S311 - reproducible geometry samples, never secrets
    for _ in range(128):
        start = (random.randrange(-100, 101), random.randrange(-100, 101))
        delta = (random.randrange(1, 51), random.randrange(-50, 51))
        end = (start[0] + delta[0], start[1] + delta[1])
        point = (random.randrange(-100, 101), random.randrange(-100, 101))
        width, diameter = random.randrange(1, 22), random.randrange(1, 22)
        clearance = random.randrange(0, 12)
        offset = (point[0] - start[0], point[1] - start[1])
        parameter = max(
            Fraction(0),
            min(
                Fraction(1),
                Fraction(
                    offset[0] * delta[0] + offset[1] * delta[1],
                    delta[0] ** 2 + delta[1] ** 2,
                ),
            ),
        )
        squared = sum((offset[axis] - parameter * delta[axis]) ** 2 for axis in (0, 1))
        with localcontext() as context:
            context.prec = 80
            distance = (Decimal(squared.numerator) / Decimal(squared.denominator)).sqrt()
            gap = max(Decimal(0), distance - Decimal(width + diameter) / 2)
            expected = int(gap.to_integral_value(rounding=ROUND_FLOOR)) - clearance
        snapshot = _snapshot(
            _track("segment:oracle", N1, start, end, width),
            _pad("pad:oracle", N2, *point, size=(diameter, diameter)),
            clearances=(clearance, clearance),
        )
        result = _measure(snapshot)
        assert result.status == "measured"
        assert result.minimum_margin_nm == expected


def test_spacing_margin_is_translation_and_reflection_invariant() -> None:
    margins = []
    for x_offset, y_offset, direction in ((0, 0, 1), (10**12, -(10**12), 1), (0, 0, -1)):
        snapshot = _snapshot(
            _track(
                "segment:oracle",
                N1,
                (x_offset, y_offset),
                (x_offset + 12 * direction, y_offset + 16 * direction),
                3,
            ),
            _pad("pad:oracle", N2, x_offset + 15 * direction, y_offset, size=(7, 7)),
            clearances=(2, 3),
        )
        margins.append(_measure(snapshot).minimum_margin_nm)
    assert margins == [4, 4, 4]
