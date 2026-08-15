"""Direction-typed geometry helpers for Board IR pad anchors and copper envelopes."""

from __future__ import annotations

from math import isqrt

from copper_mcp.board_ir.types import Pad, PadCopperEnvelope

PadBounds = tuple[int, int, int, int]
_QUARTER_UDEG = 90_000_000


def pad_local_obstacle_envelope(pad: Pad) -> PadCopperEnvelope:
    """Return a pad-local AABB that contains every bit of the pad's copper."""

    if pad.copper_envelope is not None:
        return pad.copper_envelope
    half_x = (pad.size_x_nm + 1) // 2
    half_y = (pad.size_y_nm + 1) // 2
    return PadCopperEnvelope(-half_x, -half_y, half_x, half_y)


def pad_obstacle_bounds(pad: Pad) -> PadBounds:
    """Return a board-axis AABB that over-approximates all pad copper.

    Quarter turns transform the local envelope exactly, including an off-centre custom-pad
    envelope. Other angles use a circle around the pad origin through the farthest envelope
    corner. That can only enlarge the obstacle.
    """

    envelope = pad_local_obstacle_envelope(pad)
    if pad.rotation_udeg % _QUARTER_UDEG == 0:
        points: tuple[tuple[int, int], ...] = (
            (envelope.min_x_nm, envelope.min_y_nm),
            (envelope.min_x_nm, envelope.max_y_nm),
            (envelope.max_x_nm, envelope.min_y_nm),
            (envelope.max_x_nm, envelope.max_y_nm),
        )
        turns = pad.rotation_udeg // _QUARTER_UDEG % 4
        # KiCad stores board coordinates with y increasing downward. A positive saved pad angle
        # is therefore a clockwise transform in the raw coordinate frame: (x, y) -> (y, -x).
        for _ in range(turns):
            points = tuple((y, -x) for x, y in points)
        xs = tuple(point[0] for point in points)
        ys = tuple(point[1] for point in points)
        return (
            pad.center.x + min(xs),
            pad.center.y + min(ys),
            pad.center.x + max(xs),
            pad.center.y + max(ys),
        )

    radius_squared = max(
        x * x + y * y
        for x in (envelope.min_x_nm, envelope.max_x_nm)
        for y in (envelope.min_y_nm, envelope.max_y_nm)
    )
    radius = isqrt(radius_squared)
    if radius * radius < radius_squared:
        radius += 1
    return (
        pad.center.x - radius,
        pad.center.y - radius,
        pad.center.x + radius,
        pad.center.y + radius,
    )
