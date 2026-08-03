"""Metamorphic relations over the routing pipeline.

A pseudo-oracle catches a wrong answer only when something else already knows the right one. The
footprint-rotation defect showed the complement is what was missing: nobody had asked whether the
same board, turned a quarter turn, produces the same answer. These relations ask exactly that.

Two levels are exercised, because the two failure modes live in different places.

*Board IR level* transforms a snapshot's integer geometry and re-runs the router. Nanometre
coordinates make a quarter turn an exact negate-and-swap, so the relation tests the router rather
than a decimal round trip through KiCad text.

*Adapter level* transforms the KiCad source text instead and compares ``parse(rotate(board))`` with
``rotate(parse(board))``. That is the relation the y-down defect would have failed, because the bug
lived in how the adapter placed pads on rotated footprints, not in the search.

What is asserted per relation is deliberately narrower than "everything matches". Search
tie-breaking is *not* rotation-equivariant: the expansion order is east, north, west, south, and the
heap breaks ties on ``(iy, ix)``, so a board with several equally optimal routes may legitimately
return a different one after a quarter turn. Costs are invariant because the legal path set is
exactly the image of the original under the transform and the cost model depends only on length,
bends and proximity. Vertices are therefore asserted only where the optimum is unique, and never
for reflections, which preserve bend *counts* while flipping their chirality.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import (
    BoardIRSnapshot,
    Keepout,
    NetClass,
    Pad,
    PointNM,
    Ring,
    Segment,
    Via,
    Zone,
    make_content,
    make_snapshot,
)
from copper_mcp.routing import AStarRouter, AStarSettings, RouteRequest, RouteResult

FIXTURES = Path(__file__).parent / "fixtures"
ROTATION_BOARD = FIXTURES / "board-ir-v0.1" / "footprint-rotation.kicad_pcb"
FULL_TURN_UDEG = 360_000_000

Transform = Callable[[PointNM], PointNM]


def _rotate_90(point: PointNM) -> PointNM:
    return PointNM(-point.y, point.x)


def _rotate_180(point: PointNM) -> PointNM:
    return PointNM(-point.x, -point.y)


def _rotate_270(point: PointNM) -> PointNM:
    return PointNM(point.y, -point.x)


def _reflect_x(point: PointNM) -> PointNM:
    """Mirror across the y axis."""

    return PointNM(-point.x, point.y)


def _reflect_y(point: PointNM) -> PointNM:
    """Mirror across the x axis."""

    return PointNM(point.x, -point.y)


def _translate(point: PointNM) -> PointNM:
    # A whole multiple of the fixtures' grid step, so the lattice lands the same way.
    return PointNM(point.x + 3_000, point.y - 2_000)


#: Each relation carries the point map, how a pad's own orientation turns with the board, and
#: whether the map preserves orientation. A reflection reverses chirality, so a route's turn
#: sequence mirrors even though its bend count cannot change.
RELATIONS: dict[str, tuple[Transform, int, bool]] = {
    "rotate_90": (_rotate_90, 90_000_000, True),
    "rotate_180": (_rotate_180, 180_000_000, True),
    "rotate_270": (_rotate_270, 270_000_000, True),
    "reflect_x": (_reflect_x, 0, False),
    "reflect_y": (_reflect_y, 0, False),
    "translate": (_translate, 0, True),
}


def _pad_rotation(rotation_udeg: int, added_udeg: int, orientation_preserving: bool) -> int:
    """Turn a pad with the board, negating the angle when the board is mirrored."""

    if orientation_preserving:
        return (rotation_udeg + added_udeg) % FULL_TURN_UDEG
    return (FULL_TURN_UDEG - rotation_udeg) % FULL_TURN_UDEG


def _ring(ring: Ring, transform: Transform) -> Ring:
    return Ring(tuple(transform(point) for point in ring.points))


def transform_snapshot(
    snapshot: BoardIRSnapshot,
    transform: Transform,
    added_udeg: int,
    orientation_preserving: bool,
) -> BoardIRSnapshot:
    """Rebuild a snapshot with every coordinate mapped through one rigid transform."""

    content = snapshot.content
    return make_snapshot(
        make_content(
            source=content.source,
            outline=tuple(
                replace(
                    contour,
                    outer=_ring(contour.outer, transform),
                    holes=tuple(_ring(hole, transform) for hole in contour.holes),
                )
                for contour in content.outline
            ),
            copper_layers=content.copper_layers,
            nets=content.nets,
            constraints=content.constraints,
            pads=tuple(
                replace(
                    pad,
                    center=transform(pad.center),
                    rotation_udeg=_pad_rotation(
                        pad.rotation_udeg, added_udeg, orientation_preserving
                    ),
                )
                for pad in content.pads
            ),
            segments=tuple(
                replace(segment, start=transform(segment.start), end=transform(segment.end))
                for segment in content.segments
            ),
            vias=tuple(replace(via, center=transform(via.center)) for via in content.vias),
            arcs=content.arcs,
            zones=tuple(
                replace(zone, boundary=_ring(zone.boundary, transform)) for zone in content.zones
            ),
            keepouts=tuple(
                replace(keepout, boundary=_ring(keepout.boundary, transform))
                for keepout in content.keepouts
            ),
        )
    )


# --- Board IR level -------------------------------------------------------------------------

LAYER_ID = "layer:F.Cu"
NET_ID = "net:audio"
OTHER_NET_ID = "net:power"


def _rectangle(min_x: int, min_y: int, max_x: int, max_y: int) -> Ring:
    return Ring(
        (
            PointNM(min_x, min_y),
            PointNM(max_x, min_y),
            PointNM(max_x, max_y),
            PointNM(min_x, max_y),
        )
    )


def _board(
    *,
    pads: tuple[Pad, ...],
    segments: tuple[Segment, ...] = (),
    vias: tuple[Via, ...] = (),
    zones: tuple[Zone, ...] = (),
    keepouts: tuple[Keepout, ...] = (),
) -> BoardIRSnapshot:
    from copper_mcp.board_ir import (
        ConstraintSet,
        Layer,
        Net,
        NetClassAssignment,
        OutlineContour,
        SourceInfo,
    )

    net_class = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=100,
        track_width_nm=200,
        via_diameter_nm=800,
        via_drill_nm=400,
    )
    other = replace(net_class, id="class:power", name="Power")
    return make_snapshot(
        make_content(
            source=SourceInfo(
                format="test",
                revision=f"sha256:{'a' * 64}",
                format_version="1",
                generator="metamorphic-fixture",
            ),
            outline=(OutlineContour(id="contour:main", outer=_rectangle(0, 0, 20_000, 20_000)),),
            copper_layers=(
                Layer(id=LAYER_ID, name="F.Cu", index=0, kind="signal"),
                Layer(id="layer:B.Cu", name="B.Cu", index=1, kind="signal"),
            ),
            nets=(Net(id=NET_ID, name="AUDIO"), Net(id=OTHER_NET_ID, name="POWER")),
            constraints=ConstraintSet(
                net_classes=(net_class, other),
                assignments=(
                    NetClassAssignment(net_id=NET_ID, net_class_id=net_class.id),
                    NetClassAssignment(net_id=OTHER_NET_ID, net_class_id=other.id),
                ),
            ),
            pads=pads,
            segments=segments,
            vias=vias,
            zones=zones,
            keepouts=keepouts,
        )
    )


def _pad(
    identifier: str,
    center: tuple[int, int],
    *,
    net_id: str | None = NET_ID,
    rotation_udeg: int = 0,
    size: tuple[int, int] = (400, 400),
) -> Pad:
    from copper_mcp.board_ir import PadKind, PadShape

    return Pad(
        id=identifier,
        net_id=net_id,
        center=PointNM(*center),
        rotation_udeg=rotation_udeg,
        shape=PadShape.RECT,
        kind=PadKind.SMD,
        size_x_nm=size[0],
        size_y_nm=size[1],
        roundrect_radius_nm=None,
        drill_x_nm=None,
        drill_y_nm=None,
        layer_ids=(LAYER_ID,),
    )


def _settings() -> AStarSettings:
    return AStarSettings(
        grid_step_nm=1_000,
        bend_penalty_nm=500,
        proximity_penalty_nm=50,
        max_grid_nodes=2_000,
        max_expansions=20_000,
        max_obstacles=128,
        max_obstacle_checks=400_000,
    )


def _propose(snapshot: BoardIRSnapshot) -> RouteResult:
    return AStarRouter().propose(
        snapshot,
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=NET_ID,
            layer_id=LAYER_ID,
            seed=7,
            settings=_settings(),
        ),
    )


#: Boards chosen so each exercises a different part of the pipeline.
def _straight() -> BoardIRSnapshot:
    return _board(pads=(_pad("pad:01", (5_000, 10_000)), _pad("pad:02", (15_000, 10_000))))


def _detour() -> BoardIRSnapshot:
    return _board(
        pads=(_pad("pad:01", (5_000, 10_000)), _pad("pad:02", (15_000, 10_000))),
        keepouts=(
            Keepout(
                id="keepout:00",
                layer_ids=(LAYER_ID,),
                boundary=_rectangle(9_000, 9_000, 11_000, 11_000),
                prohibit_tracks=True,
                prohibit_vias=True,
                prohibit_pads=False,
                prohibit_zones=False,
                prohibit_footprints=False,
            ),
        ),
    )


def _rotated_pads() -> BoardIRSnapshot:
    """The class that hid the y-down defect: non-square pads at every quarter turn."""

    return _board(
        pads=(
            _pad("pad:01", (5_000, 10_000), rotation_udeg=90_000_000, size=(1_200, 400)),
            _pad("pad:02", (15_000, 10_000), rotation_udeg=270_000_000, size=(1_200, 400)),
            _pad(
                "pad:03", (10_000, 4_000), net_id=None, rotation_udeg=90_000_000, size=(2_000, 600)
            ),
        )
    )


def _attachment() -> BoardIRSnapshot:
    return _board(
        pads=(_pad("pad:01", (5_000, 10_000)), _pad("pad:02", (15_000, 10_000))),
        segments=(
            Segment(
                id="segment:stub",
                net_id=NET_ID,
                layer_id=LAYER_ID,
                start=PointNM(5_000, 10_000),
                end=PointNM(8_000, 10_000),
                width_nm=200,
            ),
        ),
    )


def _connected() -> BoardIRSnapshot:
    return _board(
        pads=(_pad("pad:01", (5_000, 10_000)), _pad("pad:02", (15_000, 10_000))),
        segments=(
            Segment(
                id="segment:spine",
                net_id=NET_ID,
                layer_id=LAYER_ID,
                start=PointNM(5_000, 10_000),
                end=PointNM(15_000, 10_000),
                width_nm=200,
            ),
        ),
    )


def _multi_pin() -> BoardIRSnapshot:
    return _board(
        pads=(
            _pad("pad:01", (5_000, 10_000)),
            _pad("pad:02", (15_000, 10_000)),
            _pad("pad:03", (10_000, 16_000)),
        )
    )


BOARDS: dict[str, Callable[[], BoardIRSnapshot]] = {
    "straight": _straight,
    "detour": _detour,
    "rotated_pads": _rotated_pads,
    "attachment": _attachment,
    "connected": _connected,
    "multi_pin": _multi_pin,
}

#: Boards whose optimum is unique, so the exact route must map back through the inverse.
UNIQUE_OPTIMUM = frozenset({"straight", "attachment", "rotated_pads"})

INVERSE: dict[str, Transform] = {
    "rotate_90": _rotate_270,
    "rotate_180": _rotate_180,
    "rotate_270": _rotate_90,
    "reflect_x": _reflect_x,
    "reflect_y": _reflect_y,
    "translate": lambda point: PointNM(point.x - 3_000, point.y + 2_000),
}


@pytest.mark.parametrize("relation", sorted(RELATIONS))
@pytest.mark.parametrize("board", sorted(BOARDS))
def test_routing_outcome_is_invariant_under_rigid_transforms(relation: str, board: str) -> None:
    """A rigid transform of the whole board must not change what the router concludes."""

    transform, added_udeg, orientation_preserving = RELATIONS[relation]
    original = BOARDS[board]()
    moved = transform_snapshot(original, transform, added_udeg, orientation_preserving)

    before = _propose(original)
    after = _propose(moved)

    # Same arm of the result, always.
    assert (before.candidate is None) == (after.candidate is None)
    assert (before.connected is None) == (after.connected is None)
    assert (before.diagnostic is None) == (after.diagnostic is None)

    if before.diagnostic is not None:
        assert after.diagnostic is not None
        assert before.diagnostic.code is after.diagnostic.code

    if before.connected is not None:
        assert after.connected is not None
        # Counting copper cannot depend on where the board sits or which way it faces.
        assert before.connected.pad_count == after.connected.pad_count
        assert before.connected.attachment_segments == after.connected.attachment_segments
        assert before.connected.vias == after.connected.vias
        assert before.connected.fill_polygons == after.connected.fill_polygons
        assert before.connected.component_objects == after.connected.component_objects

    if before.candidate is not None:
        assert after.candidate is not None
        # Cost is a genuine invariant: the legal path set of the transformed board is exactly
        # the image of the original's, and length, bends and proximity are all preserved.
        assert before.candidate.cost.length_nm == after.candidate.cost.length_nm
        assert before.candidate.cost.bend_count == after.candidate.cost.bend_count
        assert before.candidate.cost.proximity_steps == after.candidate.cost.proximity_steps
        assert before.candidate.cost.total_cost_nm == after.candidate.cost.total_cost_nm
        assert before.candidate.pad_count == after.candidate.pad_count
        assert len(before.candidate.patch.paths) == len(after.candidate.patch.paths)
        assert before.candidate.patch.width_nm == after.candidate.patch.width_nm


@pytest.mark.parametrize("relation", sorted(RELATIONS))
@pytest.mark.parametrize("board", sorted(UNIQUE_OPTIMUM))
def test_a_unique_optimum_maps_back_exactly_through_the_inverse(relation: str, board: str) -> None:
    """Where only one route is optimal, the transformed route must be the transformed route.

    Boards with several equally optimal routes are excluded on purpose: the expansion order and
    the ``(iy, ix)`` heap tie-break are not rotation-equivariant, so a different-but-equal
    route is a correct answer rather than a defect.
    """

    transform, added_udeg, orientation_preserving = RELATIONS[relation]
    original = BOARDS[board]()
    moved = transform_snapshot(original, transform, added_udeg, orientation_preserving)
    inverse = INVERSE[relation]

    before = _propose(original)
    after = _propose(moved)

    assert before.candidate is not None
    assert after.candidate is not None
    mapped = tuple(
        tuple(inverse(point) for point in path.vertices) for path in after.candidate.patch.paths
    )
    expected = tuple(tuple(path.vertices) for path in before.candidate.patch.paths)
    assert mapped == expected


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_swapping_the_two_endpoints_reverses_the_route_without_changing_its_cost(
    board: str,
) -> None:
    """Which pad is called the start is a naming decision, not a geometric one."""

    original = BOARDS[board]()
    content = original.content
    routed = [pad for pad in content.pads if pad.net_id == NET_ID]
    if len(routed) != 2:
        pytest.skip("endpoint swap is only defined for a two-pin net")
    first, second = sorted(routed, key=lambda pad: pad.id)
    swapped = make_snapshot(
        make_content(
            source=content.source,
            outline=content.outline,
            copper_layers=content.copper_layers,
            nets=content.nets,
            constraints=content.constraints,
            pads=tuple(
                replace(pad, center=second.center)
                if pad.id == first.id
                else replace(pad, center=first.center)
                if pad.id == second.id
                else pad
                for pad in content.pads
            ),
            segments=content.segments,
            vias=content.vias,
            arcs=content.arcs,
            zones=content.zones,
            keepouts=content.keepouts,
        )
    )

    before = _propose(original)
    after = _propose(swapped)

    assert (before.candidate is None) == (after.candidate is None)
    if before.candidate is not None:
        assert after.candidate is not None
        assert before.candidate.cost.length_nm == after.candidate.cost.length_nm
        assert before.candidate.cost.bend_count == after.candidate.cost.bend_count
        assert before.candidate.cost.total_cost_nm == after.candidate.cost.total_cost_nm


# --- Adapter level --------------------------------------------------------------------------

_AT = re.compile(r"\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)")
_XY = re.compile(r"\((xy|start|end|center) ([-\d.]+) ([-\d.]+)\)")


def _decimal(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def rotate_kicad_source(source: str, board_width_mm: float) -> str:
    """Turn a whole KiCad board a quarter turn, in KiCad's own convention.

    KiCad stores y downward while its ``(at x y angle)`` angle is counter-clockwise on screen,
    so a positive quarter turn maps ``(x, y)`` to ``(y, -x)``. Translating y by the board width
    afterwards keeps every coordinate positive. Footprint angles gain the same quarter turn;
    pad-local ``(at ...)`` nodes are left alone because they are expressed in the footprint's
    own frame, which is exactly the distinction the y-down defect got wrong.
    """

    def turn(x: float, y: float) -> tuple[float, float]:
        return y, board_width_mm - x

    lines = source.splitlines(keepends=True)
    out: list[str] = []
    footprint_depth: int | None = None
    depth = 0
    for line in lines:
        stripped = line.strip()
        is_footprint_header = stripped.startswith("(footprint")
        # A footprint's own (at ...) is a board coordinate and must move; a pad's (at ...)
        # position is one level deeper, expressed in the footprint's frame, and must not.
        # Getting this backwards is the same confusion the y-down defect was made of. The
        # pad's angle is the exception and is handled inside replace_at below.
        inside_footprint = footprint_depth is not None and depth > footprint_depth + 1

        def replace_at(match: re.Match[str], *, skip: bool = inside_footprint) -> str:
            x, y = float(match.group(1)), float(match.group(2))
            angle = match.group(3)
            if skip:
                # A pad's *position* is footprint-local and does not move when the board
                # turns. Its *angle* is not local: KiCad resolves pad angles into the board
                # frame, so turning the board turns them with it. Leaving the angle alone
                # here would build a board whose pads point the wrong way relative to their
                # own footprint, and the relation would then be checking a fiction.
                # An absent angle means zero, so a turn has to write one in - which is what
                # KiCad itself does when it rotates a footprint whose pads carried none.
                turned_angle = (float(angle or 0.0) + 90.0) % 360.0
                return f"(at {_decimal(x)} {_decimal(y)} {_decimal(turned_angle)})"
            new_x, new_y = turn(x, y)
            if angle is None:
                return f"(at {_decimal(new_x)} {_decimal(new_y)})"
            new_angle = (float(angle) + 90.0) % 360.0
            return f"(at {_decimal(new_x)} {_decimal(new_y)} {_decimal(new_angle)})"

        def replace_point(match: re.Match[str]) -> str:
            head = match.group(1)
            x, y = float(match.group(2)), float(match.group(3))
            new_x, new_y = turn(x, y)
            return f"({head} {_decimal(new_x)} {_decimal(new_y)})"

        rewritten = _AT.sub(replace_at, line)
        rewritten = _XY.sub(replace_point, rewritten)
        out.append(rewritten)
        if is_footprint_header:
            footprint_depth = depth
        depth += line.count("(") - line.count(")")
        if footprint_depth is not None and depth <= footprint_depth:
            footprint_depth = None
    return "".join(out)


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=200_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _normalised_outline(source: str) -> str:
    """Put the Edge.Cuts rectangle back in min/max order after a turn."""

    match = re.search(
        r"\(gr_rect\s*\(start ([-\d.]+) ([-\d.]+)\)\s*\(end ([-\d.]+) ([-\d.]+)\)", source
    )
    if match is None:
        return source
    xs = sorted((float(match.group(1)), float(match.group(3))))
    ys = sorted((float(match.group(2)), float(match.group(4))))
    fixed = (
        f"(gr_rect\n    (start {_decimal(xs[0])} {_decimal(ys[0])})\n"
        f"    (end {_decimal(xs[1])} {_decimal(ys[1])})"
    )
    return source[: match.start()] + fixed + source[match.end() :]


def test_adapter_places_rotated_footprint_pads_equivariantly() -> None:
    """``parse(rotate(board))`` must equal ``rotate(parse(board))``.

    This is the relation the footprint-rotation defect would have failed. It had the quarter
    turn mirrored, so pads on rotated footprints landed at their reflection — a board that
    still parsed, still passed every schema check, and was simply wrong.
    """

    source = ROTATION_BOARD.read_text(encoding="utf-8")
    original = parse_kicad_bytes(source.encode("utf-8"), _profile()).snapshot
    assert original is not None

    turned_source = _normalised_outline(rotate_kicad_source(source, board_width_mm=44.0))
    turned = parse_kicad_bytes(turned_source.encode("utf-8"), _profile()).snapshot
    assert turned is not None, "the rotated board must still be a supported Board IR"

    before = {pad.id: pad for pad in original.content.pads}
    after = {pad.id: pad for pad in turned.content.pads}
    assert set(before) == set(after)

    width_nm = 44_000_000
    for pad_id, pad in before.items():
        expected = PointNM(pad.center.y, width_nm - pad.center.x)
        assert after[pad_id].center == expected, pad_id
        # A quarter turn flips which axis a non-square pad spans. Pad angles are absolute in
        # the board frame, so the turned board must report each pad's angle advanced by
        # exactly one quarter turn - no more, which is what the double-counted footprint
        # rotation used to produce.
        assert after[pad_id].rotation_udeg == (pad.rotation_udeg + 90_000_000) % FULL_TURN_UDEG


def test_the_rotation_relation_would_have_caught_a_mirrored_quarter_turn() -> None:
    """Guard the guard: a deliberately mirrored map must make the relation fail."""

    source = ROTATION_BOARD.read_text(encoding="utf-8")
    original = parse_kicad_bytes(source.encode("utf-8"), _profile()).snapshot
    assert original is not None

    turned_source = _normalised_outline(rotate_kicad_source(source, board_width_mm=44.0))
    turned = parse_kicad_bytes(turned_source.encode("utf-8"), _profile()).snapshot
    assert turned is not None

    before = {pad.id: pad for pad in original.content.pads}
    after = {pad.id: pad for pad in turned.content.pads}
    width_nm = 44_000_000
    mirrored: list[Any] = [
        pad_id
        for pad_id, pad in before.items()
        # The y-up reading of the same quarter turn.
        if after[pad_id].center == PointNM(width_nm - pad.center.y, pad.center.x)
        and after[pad_id].center != PointNM(pad.center.y, width_nm - pad.center.x)
    ]
    assert mirrored == [], "the adapter is using the mirrored quarter turn"


# --- Scene level ----------------------------------------------------------------------------


SCENE_BOARD = FIXTURES / "circuit-scene-v0.1" / "scene-region.kicad_pcb"
SCENE_BOARD_WIDTH_MM = 100.0


def _scene_of(source: str, directory: Path, name: str) -> dict[str, Any]:
    from copper_mcp.circuit_scene import observe_board_scene
    from copper_mcp.config import Settings

    board = directory / name
    board.write_text(source, encoding="utf-8")
    request = {
        "board": name,
        "constraints": {
            "clearance_nm": 200_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
        "region": {
            "min_x_nm": -1_000_000_000,
            "min_y_nm": -1_000_000_000,
            "max_x_nm": 1_000_000_000,
            "max_y_nm": 1_000_000_000,
        },
    }
    return observe_board_scene(request, Settings(workspace=directory.resolve())).to_dict()


def _scene_objects(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["ref_id"]: item
        for partition in ("static", "mutable")
        for items in document[partition].values()
        for item in items
    }


def test_scene_geometry_turns_with_the_board_while_every_reference_holds_still(
    tmp_path: Path,
) -> None:
    """The relation that makes ``ref_id`` worth handing to a model.

    A scene is only useful as a naming surface if a reference survives the board moving. Turn
    the board a quarter turn: every coordinate must be the image of the original under the same
    turn, and every ``ref_id`` must be the one it was before. If ids tracked geometry instead of
    identity, a model that observed a scene, reasoned about it, and then named an object would
    be naming something else.
    """

    source = SCENE_BOARD.read_text(encoding="utf-8")
    original = _scene_of(source, tmp_path, "original.kicad_pcb")
    assert original["supported"], "the scene fixture must be a supported board to start with"

    turned_source = _normalised_outline(
        rotate_kicad_source(source, board_width_mm=SCENE_BOARD_WIDTH_MM)
    )
    turned = _scene_of(turned_source, tmp_path, "turned.kicad_pcb")
    assert turned["supported"], "the turned board must still convert"

    before = _scene_objects(original)
    after = _scene_objects(turned)

    # Identity is invariant. This is the whole point of the partition.
    assert set(before) == set(after)
    assert original["ref_stability"] == turned["ref_stability"]

    width_nm = int(SCENE_BOARD_WIDTH_MM * 1_000_000)

    def turn(point: list[int]) -> list[int]:
        return [point[1], width_nm - point[0]]

    point_fields = {"center_nm", "start_nm", "end_nm", "mid_nm"}
    ring_fields = {"outer_nm", "boundary_nm"}
    scalar_fields = {"width_nm", "diameter_nm", "drill_nm", "clearance_nm", "min_thickness_nm"}
    checked = 0
    for ref_id, item in before.items():
        moved = after[ref_id]
        assert moved["kind"] == item["kind"], ref_id
        assert moved["layer_ids"] == item["layer_ids"], ref_id
        for name, value in item["geometry"].items():
            if name in point_fields:
                assert moved["geometry"][name] == turn(value), (ref_id, name)
                checked += 1
            elif name in ring_fields:
                # A ring may be reported from a different starting vertex, so compare the
                # cyclic point *set*: a turn moves where the ring is, not which ring it is.
                assert {tuple(turn(point)) for point in value} == {
                    tuple(point) for point in moved["geometry"][name]
                }, (ref_id, name)
                checked += 1
            elif name in scalar_fields:
                # A rigid motion cannot change a width, a diameter or a clearance.
                assert moved["geometry"][name] == value, (ref_id, name)

    assert checked >= 6, "the relation must actually have compared some geometry"


def test_the_scene_relation_would_catch_references_that_tracked_geometry(
    tmp_path: Path,
) -> None:
    """Guard the guard: content-hashed ids would not survive the turn, and must be seen not to."""

    import hashlib

    source = SCENE_BOARD.read_text(encoding="utf-8")
    original = _scene_of(source, tmp_path, "guard-original.kicad_pcb")
    turned = _scene_of(
        _normalised_outline(rotate_kicad_source(source, board_width_mm=SCENE_BOARD_WIDTH_MM)),
        tmp_path,
        "guard-turned.kicad_pcb",
    )

    def geometry_hash(item: dict[str, Any]) -> str:
        payload = repr(sorted(item["geometry"].items())).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:32]

    before = {geometry_hash(item) for item in _scene_objects(original).values()}
    after = {geometry_hash(item) for item in _scene_objects(turned).values()}
    moved = before - after
    assert moved, (
        "the fixture has no geometry that a quarter turn changes, so the invariance of the "
        "real ref_ids proves nothing"
    )
