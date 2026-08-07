"""Region-scoped obstacle modelling: it must be cheaper, and it must stay conservative.

The router's obstacle model over-approximates on purpose. Dropping an obstacle is therefore the
one direction of error that can produce a candidate routed through real copper, which is not
recoverable the way a refusal is. Every test here exists to hold that line while the model stops
being whole-board.

The board is 200 mm square with roughly 12,000 pieces of foreign copper, which is the shape of
the real boards in issue #128 (up to 31,389 segments against a 256-object budget) rather than
the shape of the small fixtures the rest of the routing suite uses.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

import pytest

from copper_mcp.board_ir import (
    BoardIRSnapshot,
    ConstraintSet,
    Footprint,
    FootprintSide,
    Keepout,
    Layer,
    Net,
    NetClass,
    NetClassAssignment,
    OutlineContour,
    Pad,
    PadKind,
    PadShape,
    PointNM,
    Ring,
    Segment,
    SourceInfo,
    make_content,
    make_snapshot,
)
from copper_mcp.routing import (
    AStarRouter,
    AStarSettings,
    RouteFailureCode,
    RouteRequest,
    canonical_candidate_bytes,
)

LAYER_ID = "layer:F.Cu"
NET_ID = "net:signal"
OTHER_NET_ID = "net:power"
SOURCE_REVISION = "sha256:" + "b1" * 32

MM = 1_000_000
BOARD_NM = 200 * MM
#: Pads sit low-left, 20 mm apart, so the default 10 mm region is a small part of the board.
START = (20 * MM, 20 * MM)
END = (40 * MM, 20 * MM)
TRACK_WIDTH_NM = 250_000
CLEARANCE_NM = 200_000


def _ring(points: tuple[tuple[int, int], ...]) -> Ring:
    return Ring(tuple(PointNM(x, y) for x, y in points))


def _rect_ring(min_x: int, min_y: int, max_x: int, max_y: int) -> Ring:
    return _ring(((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)))


def _pad(identifier: str, center: tuple[int, int]) -> Pad:
    return Pad(
        id=identifier,
        net_id=NET_ID,
        center=PointNM(*center),
        rotation_udeg=0,
        shape=PadShape.RECT,
        kind=PadKind.SMD,
        size_x_nm=500_000,
        size_y_nm=500_000,
        roundrect_radius_nm=None,
        drill_x_nm=None,
        drill_y_nm=None,
        layer_ids=(LAYER_ID,),
    )


def _far_copper(count: int) -> tuple[tuple[int, int, int, int], ...]:
    """Foreign tracks packed into the far half of the board, well outside any region.

    ``count`` of them, each 1 mm long on a 1 mm pitch, filling rows from y = 80 mm upward.
    None comes within 40 mm of the pads, so none can touch a route between them.
    """

    per_row = 100
    return tuple(
        (
            (index % per_row) * MM + 50 * MM,
            (index // per_row) * MM + 80 * MM,
            (index % per_row) * MM + 50 * MM + 800_000,
            (index // per_row) * MM + 80 * MM,
        )
        for index in range(count)
    )


def _snapshot(
    *,
    foreign_segments: tuple[tuple[int, int, int, int], ...] = (),
    keepouts: tuple[tuple[int, int, int, int], ...] = (),
) -> BoardIRSnapshot:
    net_class = NetClass(
        id="class:signal",
        name="Signal",
        clearance_nm=CLEARANCE_NM,
        track_width_nm=TRACK_WIDTH_NM,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    pads = (_pad("pad:01", START), _pad("pad:02", END))
    content = make_content(
        source=SourceInfo(
            format="test",
            revision=SOURCE_REVISION,
            format_version="1",
            generator="region-scope-fixture",
        ),
        outline=(OutlineContour(id="contour:main", outer=_rect_ring(0, 0, BOARD_NM, BOARD_NM)),),
        copper_layers=(Layer(id=LAYER_ID, name="F.Cu", index=0, kind="signal"),),
        nets=(Net(id=NET_ID, name="SIGNAL"), Net(id=OTHER_NET_ID, name="POWER")),
        constraints=ConstraintSet(
            net_classes=(net_class,),
            assignments=(
                NetClassAssignment(net_id=NET_ID, net_class_id=net_class.id),
                NetClassAssignment(net_id=OTHER_NET_ID, net_class_id=net_class.id),
            ),
        ),
        footprints=(
            Footprint(
                id="footprint:region-scope",
                origin=PointNM(*START),
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=tuple(pad.id for pad in pads),
            ),
        ),
        pads=pads,
        segments=tuple(
            Segment(
                id=f"segment:foreign:{index:05d}",
                net_id=OTHER_NET_ID,
                layer_id=LAYER_ID,
                start=PointNM(bounds[0], bounds[1]),
                end=PointNM(bounds[2], bounds[3]),
                width_nm=TRACK_WIDTH_NM,
            )
            for index, bounds in enumerate(foreign_segments)
        ),
        keepouts=tuple(
            Keepout(
                id=f"keepout:{index:05d}",
                layer_ids=(LAYER_ID,),
                boundary=_rect_ring(*bounds),
                prohibit_tracks=True,
                prohibit_vias=True,
                prohibit_pads=False,
                prohibit_zones=False,
                prohibit_footprints=False,
            )
            for index, bounds in enumerate(keepouts)
        ),
    )
    return make_snapshot(content)


def _settings(**changes: int) -> AStarSettings:
    defaults: dict[str, int] = {"grid_step_nm": 1 * MM, "max_grid_nodes": 250_000}
    defaults.update(changes)
    return AStarSettings(**defaults)


def _request(snapshot: BoardIRSnapshot, **changes: object) -> RouteRequest:
    defaults: dict[str, object] = {
        "board_revision": snapshot.snapshot_digest,
        "net_id": NET_ID,
        "layer_id": LAYER_ID,
        "seed": 11,
        "settings": _settings(),
    }
    defaults.update(changes)
    return RouteRequest(**defaults)  # type: ignore[arg-type]


def _whole_board_obstacles(
    snapshot: BoardIRSnapshot,
) -> tuple[tuple[int, int, int, int], ...]:
    """Every foreign selected-layer object, inflated exactly as the router would inflate it.

    Deliberately recomputed here from Board IR rather than read out of the router, so the
    verification below is independent of the code under test.
    """

    margin_nm = (TRACK_WIDTH_NM + 1) // 2 + CLEARANCE_NM
    rectangles: list[tuple[int, int, int, int]] = []
    for segment in snapshot.content.segments:
        if segment.layer_id != LAYER_ID or segment.net_id == NET_ID:
            continue
        half_nm = (segment.width_nm + 1) // 2
        rectangles.append(
            (
                min(segment.start.x, segment.end.x) - half_nm - margin_nm,
                min(segment.start.y, segment.end.y) - half_nm - margin_nm,
                max(segment.start.x, segment.end.x) + half_nm + margin_nm,
                max(segment.start.y, segment.end.y) + half_nm + margin_nm,
            )
        )
    for keepout in snapshot.content.keepouts:
        xs = [point.x for point in keepout.boundary.points]
        ys = [point.y for point in keepout.boundary.points]
        rectangles.append(
            (min(xs) - margin_nm, min(ys) - margin_nm, max(xs) + margin_nm, max(ys) + margin_nm)
        )
    return tuple(rectangles)


def _path_clears_every_obstacle(
    vertices: tuple[PointNM, ...],
    obstacles: tuple[tuple[int, int, int, int], ...],
) -> bool:
    """Exact integer check that no path edge enters any inflated whole-board obstacle."""

    for start, end in pairwise(vertices):
        edge = (
            min(start.x, end.x),
            min(start.y, end.y),
            max(start.x, end.x),
            max(start.y, end.y),
        )
        for obstacle in obstacles:
            overlaps_x = edge[0] < obstacle[2] and obstacle[0] < edge[2]
            overlaps_y = edge[1] < obstacle[3] and obstacle[1] < edge[3]
            if overlaps_x and overlaps_y:
                return False
    return True


def test_a_board_whose_far_copper_exhausts_the_budget_now_routes() -> None:
    """The defect from issue #128, at fixture scale: 12,000 objects, none near the route."""

    snapshot = _snapshot(foreign_segments=_far_copper(12_000))

    whole_board = AStarRouter().propose(
        snapshot, _request(snapshot, settings=_settings(region_margin_nm=BOARD_NM))
    )
    assert not whole_board.ok
    assert whole_board.diagnostic is not None
    assert whole_board.diagnostic.code is RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED
    assert "max_obstacles=4096" in whole_board.diagnostic.message

    scoped = AStarRouter().propose(snapshot, _request(snapshot))
    assert scoped.ok
    assert scoped.candidate is not None

    # Verified against the *whole-board* obstacle set, not the one the router modelled.
    assert _path_clears_every_obstacle(
        scoped.candidate.patch.paths[0].vertices, _whole_board_obstacles(snapshot)
    )


def test_the_scoped_model_is_a_superset_of_everything_that_could_touch_the_route() -> None:
    """Differential: scoping must never change a candidate the whole-board model produced.

    Both runs see the same board; only the region differs. If the scoped run dropped copper the
    search could reach, the two candidates would disagree — so byte equality is the property,
    not merely "both routed".
    """

    # Small enough that the whole-board model fits the budget, so both runs reach the search.
    snapshot = _snapshot(
        foreign_segments=_far_copper(2_000),
        keepouts=((25 * MM, 21 * MM, 30 * MM, 60 * MM),),
    )

    scoped = AStarRouter().propose(snapshot, _request(snapshot))
    whole_board = AStarRouter().propose(
        snapshot, _request(snapshot, settings=_settings(region_margin_nm=BOARD_NM))
    )

    assert scoped.candidate is not None
    assert whole_board.candidate is not None
    assert scoped.candidate.patch == whole_board.candidate.patch
    assert scoped.candidate.cost == whole_board.candidate.cost


@pytest.mark.parametrize("margin_nm", [2 * MM, 5 * MM, 10 * MM, 25 * MM, BOARD_NM])
def test_every_region_width_yields_a_path_legal_against_the_whole_board(margin_nm: int) -> None:
    """Whatever the region, either a typed refusal or copper that clears the whole board.

    A narrow region can legitimately fail to find the detour a wide one finds. What it may
    never do is emit a path that a wider region would have called illegal, so the width is
    swept rather than fixed and the verification is always against the unscoped obstacle set.
    """

    snapshot = _snapshot(
        foreign_segments=_far_copper(2_000),
        keepouts=((25 * MM, 15 * MM, 30 * MM, 25 * MM),),
    )

    result = AStarRouter().propose(
        snapshot, _request(snapshot, settings=_settings(region_margin_nm=margin_nm))
    )

    if result.candidate is None:
        assert result.diagnostic is not None
        assert result.diagnostic.code in {
            RouteFailureCode.NO_PATH,
            RouteFailureCode.NO_PATH_IN_REGION,
        }
        return
    assert _path_clears_every_obstacle(
        result.candidate.patch.paths[0].vertices, _whole_board_obstacles(snapshot)
    )


def test_copper_one_lattice_step_beyond_the_region_is_still_modelled() -> None:
    """The reach test adds one lattice step to the obstacle margin, and that term is load-bearing.

    Proximity scoring queries a one-step envelope around every node, including nodes on the
    region boundary itself. Copper whose inflated body sits just past that boundary therefore
    still changes cost, and dropping it would make the router quietly cheaper than the truth.

    The fixture forces the path onto the boundary with a wall, then puts a keepout in exactly
    the band that only the ``+ step`` term keeps: its inflated body starts above the region and
    within one step of it. Removing ``+ step_nm`` from ``reaches_region`` makes this fail.
    """

    margin_nm = 2 * MM
    step_nm = 1 * MM
    obstacle_margin_nm = (TRACK_WIDTH_NM + 1) // 2 + CLEARANCE_NM
    region_max_y = 20 * MM + margin_nm
    # A wall that leaves only the region's top row open, so the path must run along y = 22 mm.
    wall = (29 * MM, 15 * MM, 31 * MM, region_max_y - 500_000)
    # Inflated bottom edge lands above the region but no more than one step above it.
    band_min_y = region_max_y + obstacle_margin_nm + step_nm // 2
    band = (19 * MM, band_min_y, 41 * MM, band_min_y + 2 * MM)

    settings = _settings(region_margin_nm=margin_nm, grid_step_nm=step_nm)
    walled = _snapshot(keepouts=(wall,))
    walled_and_banded = _snapshot(keepouts=(wall, band))

    baseline = AStarRouter().propose(walled, _request(walled, settings=settings))
    banded = AStarRouter().propose(
        walled_and_banded, _request(walled_and_banded, settings=settings)
    )

    assert baseline.candidate is not None
    assert banded.candidate is not None
    assert banded.candidate.cost.proximity_steps > baseline.candidate.cost.proximity_steps


def test_the_search_can_never_reach_copper_the_region_dropped() -> None:
    """The confinement is the proof, so this test attacks the confinement directly.

    The only way out of the region is a corridor whose copper the region legitimately dropped.
    If the lattice were ever allowed past the region boundary while the obstacle model stayed
    scoped — the plausible half-finished version of this change — the router would route
    straight through that copper and call it a candidate. It must refuse instead.
    """

    margin_nm = 2 * MM
    # A wall taller than the region, so nothing inside the region gets past it.
    wall = (29 * MM, 0, 31 * MM, 24 * MM)
    # Full-width copper above the wall: outside the region's reach, so it is not modelled, and
    # squarely across the only lane an unconfined search would take around the wall.
    unmodelled = (0, 25 * MM, BOARD_NM, 26 * MM)
    snapshot = _snapshot(keepouts=(wall, unmodelled))

    result = AStarRouter().propose(
        snapshot,
        _request(snapshot, settings=_settings(region_margin_nm=margin_nm, grid_step_nm=1 * MM)),
    )

    assert result.candidate is None, "the search escaped the region it modelled obstacles for"
    assert result.diagnostic is not None
    assert result.diagnostic.code is RouteFailureCode.NO_PATH_IN_REGION


def test_an_exhausted_scoped_search_refuses_under_its_own_code() -> None:
    """A region that cannot be escaped must not report a claim about the whole board."""

    walled = _snapshot(keepouts=((25 * MM, 0, 30 * MM, BOARD_NM),))

    scoped = AStarRouter().propose(
        walled, _request(walled, settings=_settings(region_margin_nm=5 * MM))
    )
    assert not scoped.ok
    assert scoped.diagnostic is not None
    assert scoped.diagnostic.code is RouteFailureCode.NO_PATH_IN_REGION
    assert "region_margin_nm=5000000" in scoped.diagnostic.message

    whole_board = AStarRouter().propose(
        walled, _request(walled, settings=_settings(region_margin_nm=BOARD_NM))
    )
    assert not whole_board.ok
    assert whole_board.diagnostic is not None
    assert whole_board.diagnostic.code is RouteFailureCode.NO_PATH


def test_adversarial_density_inside_the_region_still_refuses_within_budget() -> None:
    """Packing the corridor itself is the attack the obstacle budget exists to refuse."""

    dense = tuple(
        (
            10 * MM + (index % 200) * 100_000,
            10 * MM + (index // 200) * 100_000,
            10 * MM + (index % 200) * 100_000 + 50_000,
            10 * MM + (index // 200) * 100_000,
        )
        for index in range(40_000)
    )
    snapshot = _snapshot(foreign_segments=dense)

    result = AStarRouter().propose(snapshot, _request(snapshot))

    assert not result.ok
    assert result.diagnostic is not None
    assert result.diagnostic.code is RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED
    assert "max_obstacles=4096" in result.diagnostic.message


def test_the_same_request_twice_produces_identical_candidate_bytes() -> None:
    snapshot = _snapshot(
        foreign_segments=_far_copper(6_000),
        keepouts=((25 * MM, 15 * MM, 30 * MM, 25 * MM),),
    )
    request = _request(snapshot)

    first = AStarRouter().propose(snapshot, request)
    second = AStarRouter().propose(snapshot, request)

    assert first.candidate is not None
    assert second.candidate is not None
    assert canonical_candidate_bytes(first.candidate) == canonical_candidate_bytes(second.candidate)
    assert first.candidate.candidate_id == second.candidate.candidate_id


def test_the_region_margin_is_part_of_the_settings_contract() -> None:
    base = _settings()
    assert base.region_margin_nm == 10 * MM
    assert base.max_net_objects == 1_024
    assert base.max_obstacles == 4_096

    with pytest.raises(ValueError):
        replace(base, region_margin_nm=0)
    with pytest.raises(ValueError):
        replace(base, max_net_objects=0)
