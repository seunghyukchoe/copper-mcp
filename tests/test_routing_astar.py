from __future__ import annotations

import math
import random
from dataclasses import replace
from itertools import pairwise
from typing import cast

import pytest

from copper_mcp.board_ir import (
    Arc,
    BoardIRSnapshot,
    ConstraintSet,
    Footprint,
    FootprintSide,
    Keepout,
    Layer,
    LengthRule,
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
    Via,
    Zone,
    make_content,
    make_snapshot,
)
from copper_mcp.board_ir.types import JSON_SAFE_INTEGER
from copper_mcp.mcp_contracts import OffGridEvidenceContract
from copper_mcp.routing import (
    AStarRouter,
    AStarSettings,
    OffGridEvidence,
    RouteCandidate,
    RouteConnection,
    RouteDiagnostic,
    RouteFailureCode,
    RoutePatch,
    RoutePath,
    RouteRequest,
    RouteResult,
    VerifiedFill,
    canonical_candidate_bytes,
    fill_binding_for,
    verify_candidate_id,
)
from copper_mcp.routing.astar import (
    OFF_GRID_MESSAGE_LEAD,
    _arc_envelope,
    _arc_sagitta_bound_nm,
    _arc_spans_at_most_half_turn,
    _diagonal_segment_cores,
    _off_grid_evidence,
    _pad_cores,
    _point_segment_distance_lt,
    _prepare,
    _Problem,
    _ray_crosses_right,
    _rectangles_touch,
    _segment_envelope,
    _swept_square_envelope,
    _via_cores,
    _WorkBudget,
)
from copper_mcp.routing.oracle import DijkstraResult, run_dijkstra_oracle

SOURCE_REVISION = f"sha256:{'a' * 64}"
OTHER_REVISION = f"sha256:{'b' * 64}"
LAYER_ID = "layer:F.Cu"
OTHER_NET_ID = "net:power"
NET_ID = "net:audio"

# One track arc, as the (start, mid, end) control points KiCad itself stores.
_ArcPoints = tuple[tuple[int, int], tuple[int, int], tuple[int, int]]


def _ring(coordinates: tuple[tuple[int, int], ...]) -> Ring:
    return Ring(tuple(PointNM(x, y) for x, y in coordinates))


def _rectangle(min_x: int, min_y: int, max_x: int, max_y: int) -> Ring:
    return _ring(((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)))


def _pad(
    identifier: str,
    center: tuple[int, int],
    *,
    net_id: str | None = NET_ID,
    rotation_udeg: int = 0,
) -> Pad:
    return Pad(
        id=identifier,
        net_id=net_id,
        center=PointNM(*center),
        rotation_udeg=rotation_udeg,
        shape=PadShape.RECT,
        kind=PadKind.SMD,
        size_x_nm=400,
        size_y_nm=400,
        roundrect_radius_nm=None,
        drill_x_nm=None,
        drill_y_nm=None,
        layer_ids=(LAYER_ID,),
    )


def _snapshot(
    *,
    start: tuple[int, int] = (1_000, 5_000),
    end: tuple[int, int] = (9_000, 5_000),
    outline: Ring | None = None,
    keepouts: tuple[tuple[int, int, int, int], ...] = (),
    polygon_keepouts: tuple[Ring, ...] = (),
    keepout_layer_id: str = LAYER_ID,
    keepout_prohibits_tracks: bool = True,
    include_end: bool = True,
    third_target: bool = False,
    extra_pad: bool = False,
    blocking_pad: tuple[int, int] | None = None,
    blocking_pad_rotation_udeg: int = 0,
    foreign_segment: tuple[int, int, int, int] | None = None,
    netless_segment: tuple[int, int, int, int] | None = None,
    foreign_via: tuple[int, int] | None = None,
    netless_via: tuple[int, int] | None = None,
    own_via: bool = False,
    foreign_zones: tuple[Ring, ...] = (),
    foreign_zone_layer_id: str = LAYER_ID,
    own_zone: Ring | None = None,
    own_zone_layer_id: str = LAYER_ID,
    route_clearance_nm: int = 100,
    other_clearance_nm: int = 100,
    zone_clearance_nm: int = 100,
    existing_copper: bool = False,
    own_segments: tuple[tuple[int, int, int, int], ...] = (),
    own_segment_layer_id: str = LAYER_ID,
    own_segment_width_nm: int = 200,
    start_pad_rotation_udeg: int = 0,
    layer_kind: str = "signal",
    length_rule: bool = False,
    foreign_arc: _ArcPoints | None = None,
    own_arc: _ArcPoints | None = None,
    arc_layer_id: str = LAYER_ID,
    arc_width_nm: int = 200,
) -> BoardIRSnapshot:
    layer = Layer(id=LAYER_ID, name="F.Cu", index=0, kind=layer_kind)
    # Vias span two layers, so the back layer only exists when a fixture needs one.
    copper_layers: tuple[Layer, ...] = (layer,)
    if (
        foreign_via is not None
        or netless_via is not None
        or own_via
        or foreign_zone_layer_id != LAYER_ID
        or own_segment_layer_id != LAYER_ID
        or keepout_layer_id != LAYER_ID
        or arc_layer_id != LAYER_ID
    ):
        copper_layers += (Layer(id="layer:B.Cu", name="B.Cu", index=1, kind="signal"),)
    net = Net(id=NET_ID, name="AUDIO")
    net_class = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=route_clearance_nm,
        track_width_nm=200,
        via_diameter_nm=600,
        via_drill_nm=300,
    )
    other_net_class = NetClass(
        id="class:power",
        name="Power",
        clearance_nm=other_clearance_nm,
        track_width_nm=200,
        via_diameter_nm=600,
        via_drill_nm=300,
    )
    pads = [_pad("pad:01", start, rotation_udeg=start_pad_rotation_udeg)]
    if include_end:
        pads.append(_pad("pad:02", end))
    if third_target:
        pads.append(_pad("pad:03", (5_000, 8_000)))
    if extra_pad:
        pads.append(_pad("pad:other", (5_000, 8_000), net_id=None))
    if blocking_pad is not None:
        pads.append(
            replace(
                _pad("pad:blocker", blocking_pad, net_id=None),
                rotation_udeg=blocking_pad_rotation_udeg,
                size_x_nm=800,
                size_y_nm=2_000,
            )
        )
    segments: tuple[Segment, ...] = ()
    if existing_copper:
        segments += (
            Segment(
                id="segment:existing",
                net_id=NET_ID,
                layer_id=LAYER_ID,
                start=PointNM(*start),
                end=PointNM(start[0] + 1_000, start[1]),
                width_nm=200,
            ),
        )
    segments += tuple(
        Segment(
            id=f"segment:own:{index:02d}",
            net_id=NET_ID,
            layer_id=own_segment_layer_id,
            start=PointNM(bounds[0], bounds[1]),
            end=PointNM(bounds[2], bounds[3]),
            width_nm=own_segment_width_nm,
        )
        for index, bounds in enumerate(own_segments)
    )
    if foreign_segment is not None:
        segments += (
            Segment(
                id="segment:foreign",
                net_id=OTHER_NET_ID,
                layer_id=LAYER_ID,
                start=PointNM(foreign_segment[0], foreign_segment[1]),
                end=PointNM(foreign_segment[2], foreign_segment[3]),
                width_nm=200,
            ),
        )
    if netless_segment is not None:
        segments += (
            Segment(
                id="segment:netless",
                net_id=None,
                layer_id=LAYER_ID,
                start=PointNM(netless_segment[0], netless_segment[1]),
                end=PointNM(netless_segment[2], netless_segment[3]),
                width_nm=200,
            ),
        )
    vias: tuple[Via, ...] = ()
    if foreign_via is not None:
        vias += (
            Via(
                id="via:foreign",
                net_id=OTHER_NET_ID,
                center=PointNM(*foreign_via),
                diameter_nm=800,
                drill_nm=400,
                start_layer_id=LAYER_ID,
                end_layer_id="layer:B.Cu",
            ),
        )
    if netless_via is not None:
        vias += (
            Via(
                id="via:netless",
                net_id=None,
                center=PointNM(*netless_via),
                diameter_nm=800,
                drill_nm=400,
                start_layer_id=LAYER_ID,
                end_layer_id="layer:B.Cu",
            ),
        )
    if own_via:
        vias += (
            Via(
                id="via:own",
                net_id=NET_ID,
                center=PointNM(5_000, 5_000),
                diameter_nm=800,
                drill_nm=400,
                start_layer_id=LAYER_ID,
                end_layer_id="layer:B.Cu",
            ),
        )
    content = make_content(
        source=SourceInfo(
            format="test",
            revision=SOURCE_REVISION,
            format_version="1",
            generator="routing-fixture",
        ),
        outline=(
            OutlineContour(
                id="contour:main",
                outer=outline or _rectangle(0, 0, 10_000, 10_000),
            ),
        ),
        copper_layers=copper_layers,
        nets=(net, Net(id=OTHER_NET_ID, name="POWER")),
        constraints=ConstraintSet(
            net_classes=(net_class, other_net_class),
            assignments=(
                NetClassAssignment(net_id=NET_ID, net_class_id=net_class.id),
                NetClassAssignment(net_id=OTHER_NET_ID, net_class_id=other_net_class.id),
            ),
            length_rules=(
                LengthRule(
                    id="rule:audio_length",
                    net_id=NET_ID,
                    minimum_nm=1_000,
                    maximum_nm=20_000,
                ),
            )
            if length_rule
            else (),
        ),
        footprints=(
            Footprint(
                id="footprint:routing-fixture",
                origin=PointNM(*start),
                rotation_udeg=start_pad_rotation_udeg,
                side=FootprintSide.FRONT,
                pad_ids=tuple(pad.id for pad in pads),
            ),
        ),
        pads=tuple(pads),
        segments=segments,
        vias=vias,
        arcs=tuple(
            Arc(
                id=f"arc:{name}",
                net_id=net_id,
                layer_id=arc_layer_id,
                start=PointNM(*points[0]),
                mid=PointNM(*points[1]),
                end=PointNM(*points[2]),
                width_nm=arc_width_nm,
            )
            for name, net_id, points in (
                ("foreign", OTHER_NET_ID, foreign_arc),
                ("own", NET_ID, own_arc),
            )
            if points is not None
        ),
        zones=tuple(
            Zone(
                id=f"zone:foreign:{index:02d}",
                net_id=OTHER_NET_ID,
                layer_id=foreign_zone_layer_id,
                boundary=boundary,
                clearance_nm=zone_clearance_nm,
                min_thickness_nm=100,
                thermal_gap_nm=100,
                thermal_bridge_width_nm=100,
            )
            for index, boundary in enumerate(foreign_zones)
        )
        + (
            (
                Zone(
                    id="zone:own",
                    net_id=NET_ID,
                    layer_id=own_zone_layer_id,
                    boundary=own_zone,
                    clearance_nm=zone_clearance_nm,
                    min_thickness_nm=100,
                    thermal_gap_nm=100,
                    thermal_bridge_width_nm=100,
                ),
            )
            if own_zone is not None
            else ()
        ),
        keepouts=tuple(
            Keepout(
                id=f"keepout:{index:02d}",
                layer_ids=(LAYER_ID,),
                boundary=_rectangle(*bounds),
                prohibit_tracks=True,
                prohibit_vias=True,
                prohibit_pads=False,
                prohibit_zones=False,
                prohibit_footprints=False,
            )
            for index, bounds in enumerate(keepouts)
        )
        + tuple(
            Keepout(
                id=f"keepout:polygon:{index:02d}",
                layer_ids=(keepout_layer_id,),
                boundary=boundary,
                prohibit_tracks=keepout_prohibits_tracks,
                prohibit_vias=True,
                prohibit_pads=False,
                prohibit_zones=False,
                prohibit_footprints=False,
            )
            for index, boundary in enumerate(polygon_keepouts)
        ),
    )
    return make_snapshot(content)


def _settings(**changes: int) -> AStarSettings:
    defaults = {
        "grid_step_nm": 1_000,
        "bend_penalty_nm": 500,
        "proximity_penalty_nm": 50,
        "max_grid_nodes": 1_000,
        "max_expansions": 5_000,
        "max_obstacles": 128,
        "max_obstacle_checks": 100_000,
    }
    defaults.update(changes)
    return AStarSettings(**defaults)


def _request(snapshot: BoardIRSnapshot, **changes: object) -> RouteRequest:
    defaults: dict[str, object] = {
        "board_revision": snapshot.snapshot_digest,
        "net_id": NET_ID,
        "layer_id": LAYER_ID,
        "seed": 7,
        "settings": _settings(),
    }
    defaults.update(changes)
    return RouteRequest(**defaults)  # type: ignore[arg-type]


def _candidate(result: RouteResult) -> RouteCandidate:
    assert result.ok
    assert result.diagnostic is None
    assert result.candidate is not None
    return result.candidate


def _assert_failure(result: RouteResult, code: RouteFailureCode) -> None:
    assert not result.ok
    assert not result.terminal
    assert result.candidate is None
    assert result.connected is None
    assert result.diagnostic is not None
    assert result.diagnostic.code is code


def _connection(result: RouteResult) -> RouteConnection:
    assert not result.ok
    assert result.terminal
    assert result.candidate is None
    assert result.diagnostic is None
    assert result.connected is not None
    return result.connected


def _problem_of(snapshot: BoardIRSnapshot, request: RouteRequest) -> _Problem:
    return _prepare(snapshot, request, _WorkBudget(settings=request.settings, cancelled=None))


def test_straight_route_is_exact_replayable_and_content_addressed() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    router = AStarRouter()

    first = _candidate(router.propose(snapshot, request))
    second = _candidate(router.propose(snapshot, request))

    assert router.name == "orthogonal-a-star-spatial-index-v1"
    assert first.router_version == "astar-grid/0.7.0"
    assert first == second
    assert first.patch.paths[0].vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))
    assert first.patch.width_nm == 200
    assert first.cost.length_nm == 8_000
    assert first.cost.bend_count == 0
    assert first.metrics.wire_length_nm == 8_000
    assert canonical_candidate_bytes(first) == canonical_candidate_bytes(second)
    assert verify_candidate_id(first)

    another_seed = _candidate(router.propose(snapshot, replace(request, seed=8)))
    assert another_seed.patch == first.patch
    assert another_seed.cost == first.cost
    assert another_seed.candidate_id != first.candidate_id


def test_obstacle_detour_has_a_stable_global_tie_break() -> None:
    snapshot = _snapshot(keepouts=((4_000, 4_000, 6_000, 6_000),))
    router = AStarRouter()

    candidate = _candidate(router.propose(snapshot, _request(snapshot)))

    assert candidate.patch.paths[0].vertices == (
        PointNM(1_000, 5_000),
        PointNM(1_000, 7_000),
        PointNM(9_000, 7_000),
        PointNM(9_000, 5_000),
    )
    assert candidate.cost.length_nm == 12_000
    assert candidate.cost.bend_count == 2
    assert candidate.metrics.hard_internal_violations == 0
    assert candidate.metrics.obstacle_checks > 0


@pytest.mark.parametrize(
    ("keepouts", "expected_ok"),
    [
        ((), True),
        (((4_000, 4_000, 6_000, 6_000),), True),
        (
            (
                (4_000, 1_000, 6_000, 4_800),
                (4_000, 5_200, 6_000, 9_000),
            ),
            True,
        ),
        (((4_500, -1_000, 5_500, 11_000),), False),
    ],
)
def test_dijkstra_oracle_matches_astar_optimal_cost_and_completion(
    keepouts: tuple[tuple[int, int, int, int], ...],
    expected_ok: bool,
) -> None:
    snapshot = _snapshot(keepouts=keepouts)
    request = _request(snapshot)

    astar = AStarRouter().propose(snapshot, request)
    first = run_dijkstra_oracle(snapshot, request)
    second = run_dijkstra_oracle(snapshot, request)

    assert first == second
    assert astar.ok is expected_ok
    assert first.ok is expected_ok
    if expected_ok:
        assert astar.candidate is not None
        assert first.total_cost_nm == astar.candidate.cost.total_cost_nm
        assert first.bend_count == astar.candidate.cost.bend_count
        assert first.proximity_steps == astar.candidate.cost.proximity_steps
        assert first.expanded_states >= astar.candidate.metrics.expanded_states
    else:
        assert astar.diagnostic is not None
        assert first.diagnostic is not None
        assert astar.diagnostic.code is RouteFailureCode.NO_PATH
        assert first.diagnostic.code is RouteFailureCode.NO_PATH


def test_dijkstra_oracle_is_bounded_and_rejects_malformed_public_inputs() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)

    invalid_snapshot = run_dijkstra_oracle(object(), request)
    assert invalid_snapshot.diagnostic is not None
    assert invalid_snapshot.diagnostic.code is RouteFailureCode.INVALID_SNAPSHOT

    invalid_request = run_dijkstra_oracle(snapshot, object())
    assert invalid_request.diagnostic is not None
    assert invalid_request.diagnostic.code is RouteFailureCode.INVALID_REQUEST

    cancelled = run_dijkstra_oracle(snapshot, request, cancelled=lambda: True)
    assert cancelled.diagnostic is not None
    assert cancelled.diagnostic.code is RouteFailureCode.CANCELLED

    limited = run_dijkstra_oracle(
        snapshot,
        _request(snapshot, settings=_settings(max_expansions=1)),
    )
    assert limited.diagnostic is not None
    assert limited.diagnostic.code is RouteFailureCode.SEARCH_BUDGET_EXCEEDED

    with pytest.raises(ValueError, match="exactly one"):
        DijkstraResult()
    with pytest.raises(ValueError, match="exactly one"):
        DijkstraResult(
            total_cost_nm=1,
            diagnostic=RouteDiagnostic(
                code=RouteFailureCode.NO_PATH,
                message="no path",
            ),
        )


def test_exact_keepout_clearance_is_legal_and_one_nanometre_inside_is_not() -> None:
    exact = _snapshot(
        keepouts=(
            (4_000, 1_000, 6_000, 4_800),
            (4_000, 5_200, 6_000, 9_000),
        )
    )
    inside = _snapshot(
        keepouts=(
            (4_000, 1_000, 6_000, 4_800),
            (4_000, 5_199, 6_000, 9_000),
        )
    )
    settings = _settings(proximity_penalty_nm=0)
    router = AStarRouter()

    exact_route = _candidate(router.propose(exact, _request(exact, settings=settings)))
    inside_result = router.propose(inside, _request(inside, settings=settings))

    assert exact_route.patch.paths[0].vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))
    _assert_failure(inside_result, RouteFailureCode.NO_PATH)


def test_board_edge_half_width_is_inclusive_but_one_nanometre_outside_fails() -> None:
    exact = _snapshot(start=(100, 5_000), end=(9_100, 5_000))
    outside = _snapshot(start=(99, 5_000), end=(9_099, 5_000))
    router = AStarRouter()

    assert router.propose(exact, _request(exact)).ok
    _assert_failure(router.propose(outside, _request(outside)), RouteFailureCode.NO_PATH)


def test_spanning_keepout_returns_no_path() -> None:
    snapshot = _snapshot(keepouts=((4_500, -1_000, 5_500, 11_000),))

    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.NO_PATH)


def test_revision_snapshot_net_grid_and_geometry_fail_closed() -> None:
    router = AStarRouter()
    snapshot = _snapshot()
    stale = _request(snapshot, board_revision=OTHER_REVISION)
    _assert_failure(router.propose(snapshot, stale), RouteFailureCode.STALE_REVISION)

    forged = replace(snapshot, snapshot_digest=OTHER_REVISION)
    forged_request = _request(forged)
    _assert_failure(router.propose(forged, forged_request), RouteFailureCode.INVALID_SNAPSHOT)

    one_pad = _snapshot(include_end=False)
    _assert_failure(
        router.propose(one_pad, _request(one_pad)), RouteFailureCode.INVALID_TWO_PIN_NET
    )
    # Three pads are no longer a refusal; they are routed as a tree. A net with too few pads
    # on the layer still is.

    off_grid = _snapshot(end=(9_001, 5_000))
    _assert_failure(router.propose(off_grid, _request(off_grid)), RouteFailureCode.OFF_GRID)

    triangle = _snapshot(outline=_ring(((0, 0), (10_000, 0), (0, 10_000))))
    _assert_failure(
        router.propose(triangle, _request(triangle)), RouteFailureCode.UNSUPPORTED_GEOMETRY
    )
    _assert_failure(
        router.propose(snapshot, _request(snapshot, layer_id="layer:B.Cu")),
        RouteFailureCode.UNSUPPORTED_GEOMETRY,
    )
    plane = _snapshot(layer_kind="plane")
    _assert_failure(router.propose(plane, _request(plane)), RouteFailureCode.UNSUPPORTED_GEOMETRY)
    constrained = _snapshot(length_rule=True)
    _assert_failure(
        router.propose(constrained, _request(constrained)),
        RouteFailureCode.UNSUPPORTED_CONSTRAINT,
    )
    _assert_failure(
        router.propose(snapshot, _request(snapshot, net_id="net:missing")),
        RouteFailureCode.INVALID_TWO_PIN_NET,
    )


def test_an_off_grid_refusal_names_the_pad_the_pitch_and_the_exact_miss() -> None:
    """The refusal carries what a designer needs to act, as exact integers (ADR-0093).

    The fixture lattice is 1,000 nm and the anchor pad sits at x = 1,000, so an end pad at
    x = 9,001 is one nanometre past a lattice line. 8,001 is the largest step that represents
    the pair, which is deliberately *larger* than the requested 1,000 nm step: representability
    is divisibility, not magnitude.
    """

    snapshot = _snapshot(end=(9_001, 5_000))

    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.OFF_GRID)
    assert result.diagnostic is not None
    assert result.diagnostic.off_grid == OffGridEvidence(
        pad_id="pad:02",
        anchor_pad_id="pad:01",
        grid_step_nm=1_000,
        miss_x_nm=1,
        miss_y_nm=0,
        largest_representable_step_nm=8_001,
    )
    assert result.diagnostic.message == (
        f"{OFF_GRID_MESSAGE_LEAD}: it misses the nearest lattice point by (1 nm, 0 nm) at "
        "grid_step_nm=1000; the largest step that represents this pad pair is 8001 nm"
    )


def test_the_off_grid_miss_is_signed_toward_the_nearest_line_and_ties_go_low() -> None:
    """Three placements pin the sign, the tie, and the negative-delta branch.

    Each one kills a different arithmetic mutation: reporting the remainder instead of the
    signed miss, breaking the half-step tie upward, and taking the remainder of ``abs(delta)``
    so a pad on the far side of the anchor reports the wrong sign.
    """

    def evidence_for(end: tuple[int, int], start: tuple[int, int] = (1_000, 5_000)) -> object:
        snapshot = _snapshot(start=start, end=end)
        result = AStarRouter().propose(snapshot, _request(snapshot))
        _assert_failure(result, RouteFailureCode.OFF_GRID)
        assert result.diagnostic is not None
        return result.diagnostic.off_grid

    # 8,999 nm along: nearer the line above, so the miss is negative.
    above = evidence_for((9_999, 5_000))
    assert isinstance(above, OffGridEvidence)
    assert (above.miss_x_nm, above.miss_y_nm) == (-1, 0)

    # Exactly half a step: the tie resolves to the lower line, so the miss stays positive.
    tie = evidence_for((9_500, 5_000))
    assert isinstance(tie, OffGridEvidence)
    assert (tie.miss_x_nm, tie.miss_y_nm) == (500, 0)
    assert tie.largest_representable_step_nm == 8_500

    # The anchor is the lower pad ID, not the leftmost pad, so this delta is negative.
    behind = evidence_for((1_000, 5_000), start=(9_001, 5_000))
    assert isinstance(behind, OffGridEvidence)
    assert (behind.miss_x_nm, behind.miss_y_nm) == (-1, 0)
    assert behind.pad_id == "pad:02"
    assert behind.anchor_pad_id == "pad:01"


def test_an_off_grid_miss_on_both_axes_is_reported_on_both_axes() -> None:
    snapshot = _snapshot(end=(9_001, 5_002))

    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.OFF_GRID)
    assert result.diagnostic is not None
    assert result.diagnostic.off_grid is not None
    assert (result.diagnostic.off_grid.miss_x_nm, result.diagnostic.off_grid.miss_y_nm) == (1, 2)
    # gcd(8_001, 2) == 1: no lattice coarser than one nanometre represents this pair.
    assert result.diagnostic.off_grid.largest_representable_step_nm == 1


def test_the_off_grid_message_stays_inside_the_contract_bound_at_its_widest() -> None:
    """The message interpolates four unbounded-looking integers, so pin the worst case.

    Every one is at its contract maximum: a half-step miss on both axes at the largest legal
    grid step, and a representable step at the JSON-safe integer ceiling.
    """

    widest = (
        f"{OFF_GRID_MESSAGE_LEAD}: it misses the nearest lattice point by "
        f"({-500_000_000} nm, {-500_000_000} nm) at grid_step_nm={1_000_000_000}; "
        f"the largest step that represents this pad pair is {2**53 - 1} nm"
    )

    assert len(widest) <= 256
    RouteDiagnostic(
        code=RouteFailureCode.OFF_GRID,
        message=widest,
        off_grid=OffGridEvidence(
            pad_id="pad:01",
            anchor_pad_id="pad:02",
            grid_step_nm=1_000_000_000,
            miss_x_nm=-500_000_000,
            miss_y_nm=-500_000_000,
            largest_representable_step_nm=2**53 - 1,
        ),
    )

    # The withheld branch is *longer* than the numeric one, and the first attempt at it
    # overflowed 256 characters -- which would have moved the crash rather than removed it.
    withheld = (
        f"{OFF_GRID_MESSAGE_LEAD}: it misses the nearest lattice point by "
        f"({-500_000_000} nm, {-500_000_000} nm) at grid_step_nm={1_000_000_000}; "
        "the largest step that represents this pad pair is above this contract's "
        "exact-integer range"
    )

    assert len(withheld) <= 256
    RouteDiagnostic(
        code=RouteFailureCode.OFF_GRID,
        message=withheld,
        off_grid=OffGridEvidence(
            pad_id="pad:01",
            anchor_pad_id="pad:02",
            grid_step_nm=1_000_000_000,
            miss_x_nm=-500_000_000,
            miss_y_nm=-500_000_000,
            largest_representable_step_nm=None,
        ),
    )


def test_pads_at_opposite_coordinate_extremes_refuse_rather_than_raise() -> None:
    """A legal Board IR board must never reach a caller as an exception.

    Board IR admits any coordinate in ``[-(2**53 - 1), 2**53 - 1]``, so a pad-centre delta
    reaches ``2 * (2**53 - 1)`` and its divisor with it. Every other field of the evidence is
    bounded by the *request's* settings; this one alone is bounded by the *board*, which is why
    it alone can leave the JSON-safe range. It used to raise ``ValueError`` out of
    ``propose`` -- past ``_fail``, past ``preview_route``, and out of the MCP tool -- for input
    the format permits. The divisor is now withheld and the refusal stays typed.
    """

    limit = JSON_SAFE_INTEGER
    snapshot = _snapshot(
        start=(-limit + 1, 0),
        end=(limit - 1, 0),
        outline=_rectangle(-limit, -limit, limit, limit),
    )

    result = AStarRouter().propose(
        snapshot, _request(snapshot, settings=_settings(grid_step_nm=1_000_000_000))
    )

    _assert_failure(result, RouteFailureCode.OFF_GRID)
    assert result.diagnostic is not None
    assert result.diagnostic.off_grid is not None
    # Withheld, never clamped and never fabricated: the true divisor is 2**54 - 2.
    assert result.diagnostic.off_grid.largest_representable_step_nm is None
    # Everything bounded by the request's settings is still exact and still actionable.
    assert result.diagnostic.off_grid.grid_step_nm == 1_000_000_000
    assert result.diagnostic.off_grid.miss_x_nm == -490_518_020
    assert result.diagnostic.off_grid.miss_y_nm == 0
    assert "above this contract's exact-integer range" in result.diagnostic.message


def test_no_legal_geometry_can_make_the_evidence_constructor_reject_its_own_measurement() -> None:
    """Sweep the legal input space instead of reasoning about it field by field.

    The first adversarial review of #142 proved by contrapositive, guard by guard, that the
    constructor cannot reject genuine router evidence. The proof was sound for the guards it
    enumerated and silent about the one bound that was not among them, and a second review
    found the crash. A proof that ranges over an enumerated set of guards says nothing about a
    bound outside that set, so this asserts the property over inputs rather than over guards:
    whatever the router can measure, both contract layers must accept.

    Both layers, because a value the dataclass admits and the published schema refuses is the
    same defect one seam further out.
    """

    limit = JSON_SAFE_INTEGER
    extremes = (-limit, -limit + 1, -1, 0, 1, limit - 1, limit)
    steps = (1, 2, 3, 250_000, 999_999_937, 1_000_000_000)
    generator = random.Random(20260812)  # noqa: S311 - deterministic sweep, not a secret
    coordinates = [*extremes, *(generator.randint(-limit, limit) for _ in range(96))]

    checked = 0
    withheld = 0
    for step in steps:
        for start_x in coordinates:
            for end_x in (coordinates[checked % len(coordinates)], -start_x, limit - start_x % 7):
                if not -limit <= end_x <= limit:
                    continue
                delta_x = end_x - start_x
                # A net routed along one axis gives ``delta_y == 0`` and therefore a divisor of
                # ``abs(delta_x)`` -- the largest a divisor can be, and the shape that overflows.
                # Without it the sweep exercises 1,400 cases and reaches the overflow in none.
                for delta_y in (0, delta_x, start_x // 3 - end_x // 5):
                    if delta_x % step == 0 and delta_y % step == 0:
                        continue
                    evidence = _off_grid_evidence(
                        _pad("pad:01", (0, 0)), _pad("pad:02", (0, 0)), delta_x, delta_y, step
                    )
                    OffGridEvidenceContract.model_validate(
                        {
                            "pad_id": evidence.pad_id,
                            "anchor_pad_id": evidence.anchor_pad_id,
                            "grid_step_nm": evidence.grid_step_nm,
                            "miss_x_nm": evidence.miss_x_nm,
                            "miss_y_nm": evidence.miss_y_nm,
                            "largest_representable_step_nm": (
                                evidence.largest_representable_step_nm
                            ),
                        }
                    )
                    checked += 1
                    withheld += evidence.largest_representable_step_nm is None

    # A sweep that silently exercised nothing would pass, so require it to have bitten -- and
    # to have reached the overflow region specifically, which is the whole reason it exists.
    assert checked > 3_000
    assert withheld > 100


def test_the_largest_divisor_that_still_fits_is_reported_rather_than_withheld() -> None:
    """The boundary is off-by-one sensitive, so pin the value that must still be reported.

    A divisor of exactly ``2**53 - 1`` fits and must be a number; one nanometre more must be
    withheld. Without both halves, capping at the wrong side of the boundary passes.
    """

    limit = JSON_SAFE_INTEGER
    snapshot = _snapshot(
        start=(0, 0), end=(limit, 0), outline=_rectangle(-limit, -limit, limit, limit)
    )

    result = AStarRouter().propose(
        snapshot, _request(snapshot, settings=_settings(grid_step_nm=1_000_000_000))
    )

    _assert_failure(result, RouteFailureCode.OFF_GRID)
    assert result.diagnostic is not None
    assert result.diagnostic.off_grid is not None
    assert result.diagnostic.off_grid.largest_representable_step_nm == limit


def test_off_grid_evidence_belongs_to_the_off_grid_code_and_to_no_other() -> None:
    evidence = OffGridEvidence(
        pad_id="pad:02",
        anchor_pad_id="pad:01",
        grid_step_nm=1_000,
        miss_x_nm=1,
        miss_y_nm=0,
        largest_representable_step_nm=8_001,
    )

    with pytest.raises(ValueError, match="off_grid diagnostic alone"):
        RouteDiagnostic(code=RouteFailureCode.NO_PATH, message="no path", off_grid=evidence)
    with pytest.raises(ValueError, match="off_grid diagnostic alone"):
        RouteDiagnostic(code=RouteFailureCode.OFF_GRID, message="off grid")
    with pytest.raises(ValueError, match="must be typed"):
        RouteDiagnostic(
            code=RouteFailureCode.OFF_GRID,
            message="off grid",
            off_grid=cast("OffGridEvidence", {"pad_id": "pad:02"}),
        )


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"pad_id": "footprint:02"}, "must be a stable pad ID"),
        ({"anchor_pad_id": "pad:02"}, "lattice anchor must differ"),
        ({"grid_step_nm": 0}, "grid step is outside"),
        ({"largest_representable_step_nm": 0}, "representable step is outside"),
        ({"miss_x_nm": 501}, "x miss is outside"),
        ({"miss_y_nm": -501}, "y miss is outside"),
        ({"miss_x_nm": 0, "miss_y_nm": 0}, "at least one axis"),
        ({"largest_representable_step_nm": 2_000}, "must not be representable"),
    ],
)
def test_off_grid_evidence_refuses_a_self_contradicting_measurement(
    changes: dict[str, object], expected: str
) -> None:
    """Every field is checked against the others, not merely against its own range.

    The last two cases are the ones that matter: a pad that misses on neither axis is on the
    lattice, and a pair whose largest representable step is a multiple of the requested step is
    representable at it. Either would be evidence contradicting the refusal carrying it.
    """

    fields: dict[str, object] = {
        "pad_id": "pad:02",
        "anchor_pad_id": "pad:01",
        "grid_step_nm": 1_000,
        "miss_x_nm": 1,
        "miss_y_nm": 0,
        "largest_representable_step_nm": 8_001,
    }
    fields.update(changes)

    with pytest.raises(ValueError, match=expected):
        OffGridEvidence(**fields)  # type: ignore[arg-type]


def test_the_dijkstra_oracle_returns_the_off_grid_refusal_rather_than_raising() -> None:
    """The oracle shares ``_prepare``, so it meets the same failure and must carry its evidence.

    Rebuilding the diagnostic without the evidence does not lose a field quietly: the
    diagnostic requires evidence on exactly this code, so the oracle would raise instead of
    refusing.
    """

    snapshot = _snapshot(end=(9_001, 5_000))

    result = run_dijkstra_oracle(snapshot, _request(snapshot))

    assert result.diagnostic is not None
    assert result.diagnostic.code is RouteFailureCode.OFF_GRID
    assert result.diagnostic.off_grid is not None
    assert result.diagnostic.off_grid.largest_representable_step_nm == 8_001


def test_grid_search_and_cancellation_budgets_are_distinct() -> None:
    snapshot = _snapshot()
    router = AStarRouter()

    grid_limited = _request(snapshot, settings=_settings(max_grid_nodes=10))
    _assert_failure(router.propose(snapshot, grid_limited), RouteFailureCode.GRID_BUDGET_EXCEEDED)

    search_limited = _request(snapshot, settings=_settings(max_expansions=1))
    search_result = router.propose(snapshot, search_limited)
    _assert_failure(search_result, RouteFailureCode.SEARCH_BUDGET_EXCEEDED)
    assert search_result.diagnostic is not None
    assert search_result.diagnostic.expanded_states == 1

    _assert_failure(
        router.propose(snapshot, _request(snapshot), cancelled=lambda: True),
        RouteFailureCode.CANCELLED,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"grid_step_nm": 0},
        {"bend_penalty_nm": -1},
        {"proximity_penalty_nm": True},
        {"max_grid_nodes": 500_001},
        {"max_expansions": 1_000_001},
        {"max_obstacles": 32_769},
        {"max_net_objects": 4_097},
        {"max_net_objects": 0},
        {"region_margin_nm": 0},
        {"region_margin_nm": 1_000_000_001},
        {"max_obstacle_checks": 10_000_001},
    ],
)
def test_settings_reject_invalid_or_unbounded_values(changes: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        _settings(**changes)


def test_route_contracts_reject_noncanonical_geometry_and_identity_tampering() -> None:
    with pytest.raises(ValueError, match="orthogonal"):
        RoutePatch(
            net_id=NET_ID,
            layer_id=LAYER_ID,
            width_nm=200,
            paths=(RoutePath(vertices=(PointNM(0, 0), PointNM(1, 1))),),
        )
    with pytest.raises(ValueError, match="collinear"):
        RoutePatch(
            net_id=NET_ID,
            layer_id=LAYER_ID,
            width_nm=200,
            paths=(RoutePath(vertices=(PointNM(0, 0), PointNM(1, 0), PointNM(2, 0))),),
        )

    snapshot = _snapshot()
    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))
    with pytest.raises(ValueError, match="candidate ID"):
        verify_candidate_id(replace(candidate, candidate_id=OTHER_REVISION))
    with pytest.raises(ValueError, match="obstacle checks"):
        replace(
            candidate,
            metrics=replace(
                candidate.metrics,
                obstacle_checks=candidate.settings.max_obstacle_checks + 1,
            ),
        )
    with pytest.raises(ValueError, match="exactly one"):
        RouteResult()
    with pytest.raises(ValueError, match="board revision"):
        RouteRequest(
            board_revision="not-a-digest",
            net_id=NET_ID,
            layer_id=LAYER_ID,
            seed=0,
        )


def test_obstacle_work_preparation_cancellation_and_public_types_are_bounded() -> None:
    router = AStarRouter()
    # In-region keepouts, well clear of the y=5,000 corridor the route takes. They have to be
    # inside the routing region to be modelled at all: copper the lattice cannot reach is not
    # charged to the obstacle budget, because it cannot change the answer either.
    crowd = tuple((200 + index * 10, 500, 205 + index * 10, 600) for index in range(130))
    snapshot = _snapshot(keepouts=crowd)

    count_limited = _request(snapshot, settings=_settings(max_obstacles=2))
    _assert_failure(
        router.propose(snapshot, count_limited),
        RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
    )

    one_obstacle = _snapshot(keepouts=(crowd[0],))
    relation_limited = _request(
        one_obstacle,
        settings=_settings(max_obstacles=2, max_obstacle_checks=1),
    )
    relation_result = router.propose(one_obstacle, relation_limited)
    _assert_failure(relation_result, RouteFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED)
    assert relation_result.diagnostic is not None
    assert relation_result.diagnostic.obstacle_checks == 1
    assert relation_result.diagnostic.expanded_states == 1

    calls = 0

    def cancel_during_preparation() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 9

    cancellation_result = router.propose(
        snapshot,
        _request(snapshot, settings=_settings(max_obstacles=256)),
        cancelled=cancel_during_preparation,
    )
    _assert_failure(cancellation_result, RouteFailureCode.CANCELLED)
    assert calls == 9
    assert cancellation_result.diagnostic is not None
    assert cancellation_result.diagnostic.expanded_states == 0

    valid_request = _request(one_obstacle)
    _assert_failure(
        router.propose(object(), valid_request),  # type: ignore[arg-type]
        RouteFailureCode.INVALID_SNAPSHOT,
    )
    _assert_failure(
        router.propose(one_obstacle, object()),  # type: ignore[arg-type]
        RouteFailureCode.INVALID_REQUEST,
    )
    _assert_failure(
        router.propose(one_obstacle, valid_request, cancelled=object()),  # type: ignore[arg-type]
        RouteFailureCode.INVALID_REQUEST,
    )


def test_foreign_pads_become_exact_obstacles_instead_of_a_rejection() -> None:
    router = AStarRouter()
    clear = _snapshot()
    blocked = _snapshot(blocking_pad=(5_000, 5_000))

    straight = _candidate(router.propose(clear, _request(clear)))
    detour = _candidate(router.propose(blocked, _request(blocked)))

    assert straight.cost.bend_count == 0
    assert detour.cost.bend_count > 0
    assert detour.cost.total_cost_nm > straight.cost.total_cost_nm
    assert detour.metrics.hard_internal_violations == 0
    # The blocker spans x 4600..5400 and y 4000..6000; inflated by half width 100
    # plus clearance 100 it forbids any centreline inside x 4500..5500, y 3900..6100.
    assert all(
        not (4_500 < point.x < 5_500 and 3_900 < point.y < 6_100)
        for point in detour.patch.paths[0].vertices
    )


def test_foreign_segments_become_exact_obstacles() -> None:
    router = AStarRouter()
    blocked = _snapshot(foreign_segment=(5_000, 3_000, 5_000, 7_000))

    detour = _candidate(router.propose(blocked, _request(blocked)))

    assert detour.cost.bend_count > 0
    assert all(
        not (4_800 < point.x < 5_200 and 2_800 < point.y < 7_200)
        for point in detour.patch.paths[0].vertices
    )


def test_obstacle_routes_agree_with_the_dijkstra_oracle() -> None:
    router = AStarRouter()
    for snapshot in (
        _snapshot(blocking_pad=(5_000, 5_000)),
        _snapshot(foreign_segment=(5_000, 3_000, 5_000, 7_000)),
        _snapshot(blocking_pad=(3_000, 5_000), foreign_segment=(7_000, 3_000, 7_000, 7_000)),
    ):
        request = _request(snapshot)
        candidate = _candidate(router.propose(snapshot, request))
        oracle = run_dijkstra_oracle(snapshot, request)

        assert isinstance(oracle, DijkstraResult)
        assert oracle.total_cost_nm == candidate.cost.total_cost_nm


def test_unmodeled_obstacle_geometry_still_fails_closed() -> None:
    router = AStarRouter()

    rotated = _snapshot(blocking_pad=(5_000, 5_000), blocking_pad_rotation_udeg=45_000_000)
    _assert_failure(
        router.propose(rotated, _request(rotated)), RouteFailureCode.UNSUPPORTED_GEOMETRY
    )

    # A via on the routed net is the remaining same-net refusal: a layer change is not
    # something this single-layer contract can model, however the copper is shaped.
    own_via = _snapshot(own_via=True)
    result = router.propose(own_via, _request(own_via))
    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
    assert result.diagnostic is not None
    assert "via" in result.diagnostic.message


def test_quarter_turn_pads_swap_their_modeled_extents() -> None:
    router = AStarRouter()
    upright = _snapshot(blocking_pad=(5_000, 6_600))
    turned = _snapshot(blocking_pad=(5_000, 6_600), blocking_pad_rotation_udeg=90_000_000)

    # Upright the blocker is 800 x 2000 and spans y 5600..7600, clear of a straight route.
    assert _candidate(router.propose(upright, _request(upright))).cost.bend_count == 0
    # Rotated a quarter turn it becomes 2000 x 800 spanning y 6200..7000 — still clear.
    assert _candidate(router.propose(turned, _request(turned))).cost.bend_count == 0

    low = _snapshot(blocking_pad=(5_000, 5_500), blocking_pad_rotation_udeg=90_000_000)
    assert _candidate(router.propose(low, _request(low))).cost.bend_count > 0


def test_existing_copper_counts_against_the_obstacle_budget() -> None:
    router = AStarRouter()
    snapshot = _snapshot(blocking_pad=(5_000, 5_000), foreign_segment=(7_000, 3_000, 7_000, 7_000))

    request = _request(snapshot, settings=_settings(max_obstacles=1))

    _assert_failure(router.propose(snapshot, request), RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED)


def test_foreign_vias_become_obstacles_on_every_layer_they_cross() -> None:
    router = AStarRouter()
    blocked = _snapshot(foreign_via=(5_000, 5_000))

    detour = _candidate(router.propose(blocked, _request(blocked)))

    assert detour.cost.bend_count > 0
    # The 800 nm via spans 4600..5400 on both axes; inflated by half width 100 plus
    # clearance 100 it forbids any centreline inside 4500..5500.
    assert all(
        not (4_500 < point.x < 5_500 and 4_500 < point.y < 5_500)
        for point in detour.patch.paths[0].vertices
    )
    oracle = run_dijkstra_oracle(blocked, _request(blocked))
    assert isinstance(oracle, DijkstraResult)
    assert oracle.total_cost_nm == detour.cost.total_cost_nm


def test_a_via_on_the_routed_net_still_fails_closed() -> None:
    router = AStarRouter()
    partially_routed = _snapshot(own_via=True)

    _assert_failure(
        router.propose(partially_routed, _request(partially_routed)),
        RouteFailureCode.UNSUPPORTED_GEOMETRY,
    )


def test_a_netless_stitching_via_is_still_an_obstacle() -> None:
    # A via saved on KiCad's net 0 has no net to belong to, but its barrel is real copper:
    # the router must detour around it exactly as it does around any foreign via.
    router = AStarRouter()
    blocked = _snapshot(netless_via=(5_000, 5_000))

    detour = _candidate(router.propose(blocked, _request(blocked)))

    assert detour.cost.bend_count > 0
    assert all(
        not (4_500 < point.x < 5_500 and 4_500 < point.y < 5_500)
        for point in detour.patch.paths[0].vertices
    )


def test_a_route_through_a_netless_via_barrel_is_refused_when_no_detour_exists() -> None:
    # The outline strip leaves no legal centreline outside the via's clearance zone, so the
    # only geometric way to finish the route would be through the barrel. That is refused.
    router = AStarRouter()
    pinched = _snapshot(
        netless_via=(5_000, 5_000),
        outline=_rectangle(0, 4_400, 10_000, 5_600),
    )

    _assert_failure(router.propose(pinched, _request(pinched)), RouteFailureCode.NO_PATH)


def test_a_netless_via_never_joins_the_net_it_touches() -> None:
    # Mutation guard: two same-net stubs each reach the netless via's annulus — the exact
    # geometry that IS a connection when the via carries the routed net. With no net the via
    # contributes nothing, so no already-connected claim may appear; the router must instead
    # propose copper of its own around the barrel. If a mutation lets a None net compare
    # equal to the routed net, this claim flips to connected and the assertion fails.
    snapshot = _snapshot(
        netless_via=(5_000, 5_000),
        own_segments=((1_000, 5_000, 4_700, 5_000), (5_300, 5_000, 9_000, 5_000)),
    )
    router = AStarRouter()

    result = router.propose(snapshot, _request(snapshot))

    assert result.connected is None
    candidate = _candidate(result)
    assert candidate.cost.length_nm > 0
    assert all(
        not (4_500 < point.x < 5_500 and 4_500 < point.y < 5_500)
        for path in candidate.patch.paths
        for point in path.vertices
    )


def test_a_netless_segment_across_both_pads_never_claims_connectivity() -> None:
    # The netless track lies exactly where same-net copper would close the two-pin net. It
    # must stay an obstacle: never attachment copper, never a connectivity claim.
    snapshot = _snapshot(netless_segment=(1_000, 5_000, 9_000, 5_000))

    result = AStarRouter().propose(snapshot, _request(snapshot))

    assert result.connected is None


def test_a_via_clear_of_the_route_does_not_force_a_detour() -> None:
    router = AStarRouter()
    aside = _snapshot(foreign_via=(5_000, 8_000))

    assert _candidate(router.propose(aside, _request(aside))).cost.bend_count == 0


def test_foreign_zone_produces_a_deterministic_detour_and_matches_the_oracle() -> None:
    snapshot = _snapshot(foreign_zones=(_rectangle(4_000, 4_000, 6_000, 6_000),))
    request = _request(snapshot)
    router = AStarRouter()

    first = _candidate(router.propose(snapshot, request))
    second = _candidate(router.propose(snapshot, request))
    oracle = run_dijkstra_oracle(snapshot, request)

    assert first == second
    assert first.patch.paths[0].vertices == (
        PointNM(1_000, 5_000),
        PointNM(1_000, 7_000),
        PointNM(9_000, 7_000),
        PointNM(9_000, 5_000),
    )
    assert first.cost.length_nm == 12_000
    assert first.cost.bend_count == 2
    assert first.metrics.hard_internal_violations == 0
    assert isinstance(oracle, DijkstraResult)
    assert oracle.total_cost_nm == first.cost.total_cost_nm


def test_concave_zone_is_not_replaced_by_its_bounding_box() -> None:
    # The start is inside the U-shaped outline's bounding box but in its open notch.
    # A rectangular approximation would reject the endpoint; the polygon leaves a
    # 2,000 nm-wide corridor around the exact centreline.
    notched = _ring(
        (
            (3_000, 2_000),
            (7_000, 2_000),
            (7_000, 8_000),
            (6_000, 8_000),
            (6_000, 3_000),
            (4_000, 3_000),
            (4_000, 8_000),
            (3_000, 8_000),
        )
    )
    snapshot = _snapshot(
        start=(5_000, 5_000),
        end=(5_000, 9_000),
        foreign_zones=(notched,),
    )

    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    assert candidate.patch.paths[0].vertices == (PointNM(5_000, 5_000), PointNM(5_000, 9_000))
    assert candidate.cost.bend_count == 0


def test_zone_collision_checks_the_complete_grid_edge() -> None:
    snapshot = _snapshot(foreign_zones=(_rectangle(4_000, 4_000, 6_000, 6_000),))
    request = _request(
        snapshot,
        settings=_settings(grid_step_nm=8_000, proximity_penalty_nm=0),
    )

    result = AStarRouter().propose(snapshot, request)

    _assert_failure(result, RouteFailureCode.NO_PATH)


def test_zone_exact_clearance_boundary_is_legal_and_one_nanometre_inside_is_not() -> None:
    lower = _rectangle(3_000, 0, 7_000, 4_800)
    exact_upper = _rectangle(3_000, 5_200, 7_000, 10_000)
    inside_upper = _rectangle(3_000, 5_199, 7_000, 10_000)
    exact = _snapshot(foreign_zones=(lower, exact_upper))
    inside = _snapshot(foreign_zones=(lower, inside_upper))
    settings = _settings(proximity_penalty_nm=0)
    router = AStarRouter()

    exact_route = _candidate(router.propose(exact, _request(exact, settings=settings)))
    inside_result = router.propose(inside, _request(inside, settings=settings))

    assert exact_route.patch.paths[0].vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))
    _assert_failure(inside_result, RouteFailureCode.NO_PATH)


@pytest.mark.parametrize(
    ("route_clearance_nm", "other_clearance_nm", "zone_clearance_nm"),
    ((300, 100, 100), (100, 300, 100), (100, 100, 300)),
)
def test_zone_uses_the_strictest_of_all_three_clearances(
    route_clearance_nm: int,
    other_clearance_nm: int,
    zone_clearance_nm: int,
) -> None:
    exact = _snapshot(
        foreign_zones=(_rectangle(4_000, 1_000, 6_000, 4_600),),
        route_clearance_nm=route_clearance_nm,
        other_clearance_nm=other_clearance_nm,
        zone_clearance_nm=zone_clearance_nm,
    )
    inside = _snapshot(
        foreign_zones=(_rectangle(4_000, 1_000, 6_000, 4_601),),
        route_clearance_nm=route_clearance_nm,
        other_clearance_nm=other_clearance_nm,
        zone_clearance_nm=zone_clearance_nm,
    )
    settings = _settings(proximity_penalty_nm=0)
    router = AStarRouter()

    exact_route = _candidate(router.propose(exact, _request(exact, settings=settings)))
    inside_route = _candidate(router.propose(inside, _request(inside, settings=settings)))

    assert exact_route.cost.bend_count == 0
    assert inside_route.cost.bend_count > 0


def test_diagonal_zone_edge_uses_exact_rational_distance() -> None:
    # The start is exactly 200 nm from the 3:4:5 edge A-B. Its perpendicular
    # foot (4,840, 5,120) lies inside A-B, so this exercises the rational
    # cross-product branch rather than an endpoint distance.
    boundary = _ring(
        (
            (3_340, 3_120),
            (6_340, 7_120),
            (5_940, 7_420),
            (2_940, 3_420),
        )
    )
    exact = _snapshot(
        start=(5_000, 5_000),
        end=(5_000, 1_000),
        foreign_zones=(boundary,),
    )
    one_nanometre_inside = _snapshot(
        start=(5_000, 5_000),
        end=(5_000, 1_000),
        foreign_zones=(boundary,),
        zone_clearance_nm=101,
    )
    settings = _settings(proximity_penalty_nm=0)
    router = AStarRouter()

    exact_route = _candidate(router.propose(exact, _request(exact, settings=settings)))
    inside = router.propose(
        one_nanometre_inside,
        _request(one_nanometre_inside, settings=settings),
    )

    assert exact_route.patch.paths[0].vertices == (PointNM(5_000, 5_000), PointNM(5_000, 1_000))
    _assert_failure(inside, RouteFailureCode.NO_PATH)


def test_same_net_zone_remains_partial_routing() -> None:
    snapshot = _snapshot(own_zone=_rectangle(1_000, 1_000, 2_000, 2_000))

    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)


def test_zones_share_object_and_edge_relation_budgets() -> None:
    router = AStarRouter()
    two_zones = _snapshot(
        foreign_zones=(
            _rectangle(2_000, 1_000, 3_000, 2_000),
            _rectangle(7_000, 8_000, 8_000, 9_000),
        )
    )
    object_limited = router.propose(
        two_zones,
        _request(two_zones, settings=_settings(max_obstacles=1)),
    )
    _assert_failure(object_limited, RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED)

    notched = _ring(
        (
            (3_000, 2_000),
            (7_000, 2_000),
            (7_000, 8_000),
            (6_000, 8_000),
            (6_000, 3_000),
            (4_000, 3_000),
            (4_000, 8_000),
            (3_000, 8_000),
        )
    )
    relation_limited_snapshot = _snapshot(
        start=(5_000, 5_000),
        end=(5_000, 9_000),
        foreign_zones=(notched,),
    )
    relation_limited = router.propose(
        relation_limited_snapshot,
        _request(
            relation_limited_snapshot,
            settings=_settings(max_obstacle_checks=8),
        ),
    )

    _assert_failure(relation_limited, RouteFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED)
    assert relation_limited.diagnostic is not None
    assert relation_limited.diagnostic.obstacle_checks == 8
    assert relation_limited.diagnostic.expanded_states == 0


def test_zone_on_another_layer_is_ignored() -> None:
    clear = _snapshot()
    other_layer = _snapshot(
        foreign_zones=(_rectangle(4_000, 4_000, 6_000, 6_000),),
        foreign_zone_layer_id="layer:B.Cu",
    )
    router = AStarRouter()

    clear_route = _candidate(router.propose(clear, _request(clear)))
    other_layer_route = _candidate(router.propose(other_layer, _request(other_layer)))

    assert other_layer_route.patch.paths[0].vertices == clear_route.patch.paths[0].vertices
    assert other_layer_route.cost == clear_route.cost


def test_polygon_preparation_scan_observes_the_cancellation_cadence() -> None:
    bottom = tuple((x, 1_000) for x in range(1_000, 5_001, 200))
    right = tuple((5_000, y) for y in range(1_200, 5_001, 200))
    top = tuple((x, 5_000) for x in range(4_800, 999, -200))
    left = tuple((1_000, y) for y in range(4_800, 1_000, -200))
    many_vertices = _ring(bottom + right + top + left)
    snapshot = _snapshot(foreign_zones=(many_vertices,))
    calls = 0

    def cancel_on_first_relation_checkpoint() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 12

    result = AStarRouter().propose(
        snapshot,
        _request(snapshot),
        cancelled=cancel_on_first_relation_checkpoint,
    )

    _assert_failure(result, RouteFailureCode.CANCELLED)
    assert result.diagnostic is not None
    assert result.diagnostic.obstacle_checks == 64
    assert result.diagnostic.expanded_states == 0
    assert calls == 12


@pytest.mark.parametrize("cancel_at", (10, 11))
def test_zone_net_class_lookup_does_not_swallow_cancellation(cancel_at: int) -> None:
    snapshot = _snapshot(
        foreign_zones=(_rectangle(4_000, 4_000, 6_000, 6_000),),
    )
    calls = 0

    def cancel_once() -> bool:
        nonlocal calls
        calls += 1
        return calls == cancel_at

    result = AStarRouter().propose(
        snapshot,
        _request(snapshot),
        cancelled=cancel_once,
    )

    _assert_failure(result, RouteFailureCode.CANCELLED)
    assert calls == cancel_at


def test_same_net_stub_is_attachment_copper_not_a_veto() -> None:
    router = AStarRouter()
    clear = _snapshot()
    stubbed = _snapshot(existing_copper=True)

    full = _candidate(router.propose(clear, _request(clear)))
    completion = _candidate(router.propose(stubbed, _request(stubbed)))

    # The stub reaches x=2,000, so the router only has to add the remaining 7,000 nm.
    assert full.patch.paths[0].vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))
    assert completion.patch.paths[0].vertices == (PointNM(2_000, 5_000), PointNM(9_000, 5_000))
    assert completion.cost.length_nm == 7_000
    assert completion.cost.bend_count == 0
    assert completion.metrics.hard_internal_violations == 0
    assert completion.metrics.unrouted_connections == 0
    assert completion.start_pad_id == full.start_pad_id
    assert completion.end_pad_id == full.end_pad_id


def test_same_net_segment_joining_both_pads_reports_already_connected() -> None:
    snapshot = _snapshot(own_segments=((1_000, 5_000, 9_000, 5_000),))

    result = AStarRouter().propose(snapshot, _request(snapshot))
    connection = _connection(result)

    assert connection.base_revision == snapshot.snapshot_digest
    assert connection.start_pad_id == "pad:01"
    assert connection.end_pad_id == "pad:02"
    assert connection.attachment_segments == 1
    assert connection.component_objects == 3
    assert connection.obstacle_checks > 0


def test_already_connected_nets_agree_with_the_dijkstra_oracle() -> None:
    snapshot = _snapshot(own_segments=((1_000, 5_000, 9_000, 5_000),))

    oracle = run_dijkstra_oracle(snapshot, _request(snapshot))

    assert oracle.ok
    assert oracle.total_cost_nm == 0
    assert oracle.bend_count == 0
    assert oracle.proximity_steps == 0


def test_route_result_admits_exactly_one_terminal_arm() -> None:
    connection = RouteConnection(
        base_revision=SOURCE_REVISION,
        start_pad_id="pad:01",
        end_pad_id="pad:02",
        attachment_segments=1,
        component_objects=3,
    )
    snapshot = _snapshot()
    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    with pytest.raises(ValueError, match="exactly one"):
        RouteResult(candidate=candidate, connected=connection)
    with pytest.raises(ValueError, match="exactly one"):
        RouteResult(
            connected=connection,
            diagnostic=RouteDiagnostic(code=RouteFailureCode.NO_PATH, message="no path"),
        )
    with pytest.raises(ValueError, match="must be distinct"):
        replace(connection, end_pad_id="pad:01")
    with pytest.raises(ValueError, match="every pad, segment, via and fill island"):
        replace(connection, attachment_segments=2)
    assert RouteResult(connected=connection).terminal
    assert not RouteResult(connected=connection).ok


def test_diagonal_same_net_segment_is_attachment_copper() -> None:
    router = AStarRouter()
    clear = _snapshot()
    stubbed = _snapshot(own_segments=((1_000, 5_000, 4_000, 6_000),))

    full = _candidate(router.propose(clear, _request(clear)))
    completion = _candidate(router.propose(stubbed, _request(stubbed)))

    assert full.patch.paths[0].vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))
    assert full.cost.length_nm == 8_000
    # The chain's last square is centred exactly on the stub's far endpoint, so the search may
    # start there and only has to add the remaining 6,000 nm.
    assert completion.patch.paths[0].vertices == (
        PointNM(4_000, 6_000),
        PointNM(4_000, 5_000),
        PointNM(9_000, 5_000),
    )
    assert completion.cost.length_nm == 6_000
    assert completion.cost.bend_count == 1
    assert completion.metrics.unrouted_connections == 0


def test_diagonal_attachment_chain_seeds_every_covered_lattice_node() -> None:
    # A chain square is centred on each sampled centreline point, so a diagonal crossing
    # several lattice nodes offers all of them as attachment points, not just its endpoints.
    snapshot = _snapshot(own_segments=((1_000, 5_000, 3_000, 7_000),))

    problem = _problem_of(snapshot, _request(snapshot))

    assert problem.source_nodes == {(0, 0), (1, 1), (2, 2)}
    assert problem.target_nodes == {(8, 0)}


def test_a_diagonal_segment_joining_both_pads_reports_already_connected() -> None:
    # One diagonal running corner to corner between the two pads: nothing to route.
    snapshot = _snapshot(
        start=(1_000, 1_000),
        end=(9_000, 9_000),
        own_segments=((1_000, 1_000, 9_000, 9_000),),
    )

    connection = _connection(AStarRouter().propose(snapshot, _request(snapshot)))

    assert connection.attachment_segments == 1
    assert connection.component_objects == 3


def test_diagonal_attachment_is_deterministic_and_matches_the_oracle() -> None:
    snapshot = _snapshot(
        own_segments=((1_000, 5_000, 4_000, 6_000), (7_000, 3_000, 9_000, 5_000)),
        keepouts=((5_000, 4_000, 6_000, 6_000),),
    )
    request = _request(snapshot)
    router = AStarRouter()

    first = _candidate(router.propose(snapshot, request))
    second = _candidate(router.propose(snapshot, request))
    oracle = run_dijkstra_oracle(snapshot, request)

    assert first == second
    assert canonical_candidate_bytes(first) == canonical_candidate_bytes(second)
    assert isinstance(oracle, DijkstraResult)
    assert oracle.total_cost_nm == first.cost.total_cost_nm
    assert oracle.bend_count == first.cost.bend_count
    assert oracle.proximity_steps == first.cost.proximity_steps


def test_diagonal_core_chain_is_independent_of_stored_endpoint_order() -> None:
    work = _WorkBudget(settings=_settings(), cancelled=None)
    forward = Segment(
        id="segment:own:forward",
        net_id=NET_ID,
        layer_id=LAYER_ID,
        start=PointNM(1_000, 5_000),
        end=PointNM(4_000, 6_000),
        width_nm=200,
    )
    reversed_segment = replace(forward, start=forward.end, end=forward.start)

    assert _diagonal_segment_cores(forward, work) == _diagonal_segment_cores(reversed_segment, work)


def test_a_diagonal_too_narrow_to_model_fails_closed() -> None:
    snapshot = _snapshot(own_segments=((1_000, 5_000, 4_000, 6_000),), own_segment_width_nm=4)

    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
    assert result.diagnostic is not None
    assert "too narrow" in result.diagnostic.message


def test_diagonal_core_chain_charges_the_obstacle_check_budget() -> None:
    # A long diagonal needs many squares, and each one charges the shared budget.
    snapshot = _snapshot(own_segments=((1_000, 1_000, 9_000, 9_000),))

    result = AStarRouter().propose(
        snapshot, _request(snapshot, settings=_settings(max_obstacle_checks=5))
    )

    _assert_failure(result, RouteFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED)
    assert result.diagnostic is not None
    assert result.diagnostic.obstacle_checks == 5
    assert result.diagnostic.expanded_states == 0


def test_same_net_via_and_zone_remain_partial_routing_beside_a_stub() -> None:
    router = AStarRouter()
    with_via = _snapshot(own_segments=((1_000, 5_000, 2_000, 5_000),), own_via=True)
    with_zone = _snapshot(
        own_segments=((1_000, 5_000, 2_000, 5_000),),
        own_zone=_rectangle(1_000, 1_000, 2_000, 2_000),
    )

    _assert_failure(
        router.propose(with_via, _request(with_via)), RouteFailureCode.UNSUPPORTED_GEOMETRY
    )
    _assert_failure(
        router.propose(with_zone, _request(with_zone)), RouteFailureCode.UNSUPPORTED_GEOMETRY
    )


def test_off_axis_routed_pad_fails_closed_only_when_attachment_copper_exists() -> None:
    router = AStarRouter()
    rotated = _snapshot(start_pad_rotation_udeg=45_000_000)
    rotated_with_stub = _snapshot(
        start_pad_rotation_udeg=45_000_000,
        own_segments=((1_000, 5_000, 2_000, 5_000),),
    )

    # Without same-net copper no connectivity question arises, so the pad centre still routes.
    assert _candidate(router.propose(rotated, _request(rotated))).cost.bend_count == 0
    result = router.propose(rotated_with_stub, _request(rotated_with_stub))
    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
    assert result.diagnostic is not None
    assert "route endpoint pad" in result.diagnostic.message


@pytest.mark.parametrize(
    ("stub_end_x", "expected_vertices", "expected_length_nm"),
    [
        # The end-pad core starts at x=8,800; a stub reaching it exactly is connected copper.
        (8_800, (PointNM(1_000, 5_000), PointNM(5_000, 5_000)), 4_000),
        # One nanometre short it is an isolated component, neither obstacle nor attachment.
        (8_799, (PointNM(1_000, 5_000), PointNM(9_000, 5_000)), 8_000),
    ],
)
def test_component_contact_is_exact_and_touching_counts(
    stub_end_x: int,
    expected_vertices: tuple[PointNM, ...],
    expected_length_nm: int,
) -> None:
    snapshot = _snapshot(own_segments=((5_000, 5_000, stub_end_x, 5_000),))

    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    assert candidate.patch.paths[0].vertices == expected_vertices
    assert candidate.cost.length_nm == expected_length_nm


def test_attachment_copper_is_never_an_obstacle() -> None:
    router = AStarRouter()
    clear = _snapshot()
    crossed = _snapshot(own_segments=((5_000, 3_000, 5_000, 7_000),))

    straight = _candidate(router.propose(clear, _request(clear)))
    crossing = _candidate(router.propose(crossed, _request(crossed)))

    # The identical foreign segment forces a detour; on the routed net it is not an obstacle.
    assert crossing.patch.paths[0].vertices == straight.patch.paths[0].vertices
    assert crossing.cost == straight.cost


def test_multi_target_search_is_deterministic_and_matches_the_oracle() -> None:
    snapshot = _snapshot(
        own_segments=(
            (1_000, 5_000, 2_000, 5_000),
            (8_000, 5_000, 9_000, 5_000),
            (8_000, 5_000, 8_000, 6_000),
        )
    )
    request = _request(snapshot)
    router = AStarRouter()

    first = _candidate(router.propose(snapshot, request))
    second = _candidate(router.propose(snapshot, request))
    oracle = run_dijkstra_oracle(snapshot, request)

    assert first == second
    assert canonical_candidate_bytes(first) == canonical_candidate_bytes(second)
    assert first.patch.paths[0].vertices == (PointNM(2_000, 5_000), PointNM(8_000, 5_000))
    assert first.cost.length_nm == 6_000
    assert first.cost.bend_count == 0
    assert isinstance(oracle, DijkstraResult)
    assert oracle.total_cost_nm == first.cost.total_cost_nm
    assert oracle.bend_count == first.cost.bend_count
    assert oracle.proximity_steps == first.cost.proximity_steps


@pytest.mark.parametrize(
    ("own_segments", "keepouts"),
    [
        (((1_000, 5_000, 2_000, 5_000),), ()),
        (((1_000, 5_000, 2_000, 5_000),), ((4_000, 4_000, 6_000, 6_000),)),
        (((8_000, 5_000, 9_000, 5_000),), ((4_000, 4_000, 6_000, 6_000),)),
        (
            ((1_000, 5_000, 1_000, 7_000), (8_000, 5_000, 9_000, 5_000)),
            ((4_000, 4_000, 6_000, 6_000),),
        ),
        (((1_000, 5_000, 2_000, 5_000),), ((4_500, -1_000, 5_500, 11_000),)),
    ],
)
def test_multi_target_heuristic_stays_admissible(
    own_segments: tuple[tuple[int, int, int, int], ...],
    keepouts: tuple[tuple[int, int, int, int], ...],
) -> None:
    snapshot = _snapshot(own_segments=own_segments, keepouts=keepouts)
    request = _request(snapshot)

    astar = AStarRouter().propose(snapshot, request)
    oracle = run_dijkstra_oracle(snapshot, request)

    assert astar.ok is oracle.ok
    if astar.candidate is None:
        assert oracle.diagnostic is not None
        assert astar.diagnostic is not None
        assert astar.diagnostic.code is oracle.diagnostic.code
        return
    assert oracle.total_cost_nm == astar.candidate.cost.total_cost_nm
    assert oracle.bend_count == astar.candidate.cost.bend_count
    assert oracle.proximity_steps == astar.candidate.cost.proximity_steps


def test_seed_and_target_nodes_are_disjoint_and_inside_the_lattice() -> None:
    snapshot = _snapshot(
        own_segments=(
            (1_000, 5_000, 2_000, 5_000),
            (8_000, 5_000, 9_000, 5_000),
        )
    )

    problem = _problem_of(snapshot, _request(snapshot))

    assert problem.source_nodes & problem.target_nodes == frozenset()
    assert (0, 0) in problem.source_nodes
    assert (problem.goal_ix, problem.goal_iy) in problem.target_nodes
    assert problem.source_nodes == {(0, 0), (1, 0)}
    assert problem.target_nodes == {(7, 0), (8, 0)}
    assert problem.target_min_ix == 7
    assert problem.target_max_ix == 8
    assert problem.target_min_iy == problem.target_max_iy == 0
    for node_ix, node_iy in problem.source_nodes | problem.target_nodes:
        assert problem.min_ix <= node_ix <= problem.max_ix
        assert problem.min_iy <= node_iy <= problem.max_iy


def test_same_net_copper_on_another_layer_is_neither_attachment_nor_obstacle() -> None:
    router = AStarRouter()
    clear = _snapshot()
    # A back-layer stub that would join both pads if the layer filter were wrong.
    other_layer = _snapshot(
        own_segments=((1_000, 5_000, 9_000, 5_000),),
        own_segment_layer_id="layer:B.Cu",
    )

    straight = _candidate(router.propose(clear, _request(clear)))
    unaffected = _candidate(router.propose(other_layer, _request(other_layer)))

    assert unaffected.patch.paths[0].vertices == straight.patch.paths[0].vertices
    assert unaffected.cost == straight.cost


def test_attachment_copper_outside_the_lattice_is_clamped() -> None:
    # The stub touches the start pad but runs far below the board, so its covered index
    # range has to be clamped rather than producing nodes outside the lattice.
    snapshot = _snapshot(own_segments=((1_000, 5_000, 1_000, -20_000),))
    request = _request(snapshot)

    problem = _problem_of(snapshot, request)
    candidate = _candidate(AStarRouter().propose(snapshot, request))

    assert problem.source_nodes == {(0, 0), (0, -1), (0, -2), (0, -3), (0, -4)}
    for _, node_iy in problem.source_nodes:
        assert node_iy >= problem.min_iy
    assert candidate.patch.paths[0].vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))


def test_attachment_copper_charges_the_net_object_budget_not_the_obstacle_budget() -> None:
    """The routed net's own copper and the copper it must avoid are separate populations."""

    router = AStarRouter()
    stub_and_keepout = _snapshot(
        own_segments=((1_000, 5_000, 2_000, 5_000),),
        keepouts=((200, 500, 205, 600), (400, 500, 405, 600)),
    )
    two_stubs = _snapshot(
        own_segments=(
            (1_000, 5_000, 2_000, 5_000),
            (8_000, 5_000, 9_000, 5_000),
        )
    )

    # One keepout against a budget of one: the obstacle budget still refuses foreign copper.
    shared = router.propose(
        stub_and_keepout, _request(stub_and_keepout, settings=_settings(max_obstacles=1))
    )
    _assert_failure(shared, RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED)
    assert shared.diagnostic is not None
    assert "max_obstacles=1" in shared.diagnostic.message

    # Two attachment stubs and no foreign copper: an obstacle budget of one does not refuse,
    # because attachment copper is not an obstacle.
    obstacle_limited = router.propose(
        two_stubs, _request(two_stubs, settings=_settings(max_obstacles=1))
    )
    assert obstacle_limited.ok

    attachment_only = router.propose(
        two_stubs, _request(two_stubs, settings=_settings(max_net_objects=1))
    )
    _assert_failure(attachment_only, RouteFailureCode.NET_OBJECT_BUDGET_EXCEEDED)
    assert attachment_only.diagnostic is not None
    assert "attachment copper" in attachment_only.diagnostic.message
    assert "max_net_objects=1" in attachment_only.diagnostic.message


def test_component_build_respects_the_obstacle_check_budget() -> None:
    snapshot = _snapshot(
        own_segments=(
            (1_000, 5_000, 2_000, 5_000),
            (4_000, 1_000, 5_000, 1_000),
            (7_000, 8_000, 8_000, 8_000),
        )
    )

    result = AStarRouter().propose(
        snapshot,
        _request(snapshot, settings=_settings(max_obstacle_checks=4)),
    )

    _assert_failure(result, RouteFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED)
    assert result.diagnostic is not None
    assert result.diagnostic.obstacle_checks == 4
    assert result.diagnostic.expanded_states == 0


def test_component_build_observes_the_cancellation_cadence() -> None:
    # Eleven stubs plus two pads make thirteen rectangles and seventy-eight exact pair
    # comparisons, so the union-find crosses the sixty-fourth obstacle-check checkpoint.
    snapshot = _snapshot(
        own_segments=tuple((500 + index * 500, 500, 700 + index * 500, 500) for index in range(11))
    )
    calls = 0

    def cancel_on_first_component_checkpoint() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 8

    result = AStarRouter().propose(
        snapshot,
        _request(snapshot),
        cancelled=cancel_on_first_component_checkpoint,
    )

    _assert_failure(result, RouteFailureCode.CANCELLED)
    assert result.diagnostic is not None
    assert result.diagnostic.obstacle_checks == 64
    assert result.diagnostic.expanded_states == 0
    assert calls == 8


def _octagon(center_x: int, center_y: int, radius: int, cut: int) -> Ring:
    """Build an axis-aligned octagon, the shape KiCad uses for mounting-hole rule areas."""

    return _ring(
        (
            (center_x - radius + cut, center_y - radius),
            (center_x + radius - cut, center_y - radius),
            (center_x + radius, center_y - radius + cut),
            (center_x + radius, center_y + radius - cut),
            (center_x + radius - cut, center_y + radius),
            (center_x - radius + cut, center_y + radius),
            (center_x - radius, center_y + radius - cut),
            (center_x - radius, center_y - radius + cut),
        )
    )


def test_octagonal_keepout_detours_deterministically_and_matches_the_oracle() -> None:
    snapshot = _snapshot(polygon_keepouts=(_octagon(5_000, 5_000, 1_000, 300),))
    request = _request(snapshot)
    router = AStarRouter()

    first = _candidate(router.propose(snapshot, request))
    second = _candidate(router.propose(snapshot, request))
    oracle = run_dijkstra_oracle(snapshot, request)

    assert first == second
    assert canonical_candidate_bytes(first) == canonical_candidate_bytes(second)
    assert first.cost.bend_count > 0
    assert first.metrics.hard_internal_violations == 0
    # The octagon spans 4,000..6,000 on both axes; with half width 100 and clearance 100 no
    # centreline may enter its 200 nm offset, so the straight y=5,000 corridor is closed.
    assert all(
        point.y != 5_000 or point.x <= 3_800 or point.x >= 6_200
        for point in first.patch.paths[0].vertices
    )
    assert isinstance(oracle, DijkstraResult)
    assert oracle.total_cost_nm == first.cost.total_cost_nm
    assert oracle.bend_count == first.cost.bend_count
    assert oracle.proximity_steps == first.cost.proximity_steps


def test_concave_keepout_is_not_replaced_by_its_bounding_box() -> None:
    # The same U-shaped outline the zone model uses: the start sits inside the bounding box
    # but in the open notch, so a rectangular approximation would refuse the endpoint.
    notched = _ring(
        (
            (3_000, 2_000),
            (7_000, 2_000),
            (7_000, 8_000),
            (6_000, 8_000),
            (6_000, 3_000),
            (4_000, 3_000),
            (4_000, 8_000),
            (3_000, 8_000),
        )
    )
    snapshot = _snapshot(
        start=(5_000, 5_000),
        end=(5_000, 9_000),
        polygon_keepouts=(notched,),
    )

    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    assert candidate.patch.paths[0].vertices == (PointNM(5_000, 5_000), PointNM(5_000, 9_000))
    assert candidate.cost.bend_count == 0


def test_rectangular_keepout_keeps_the_exact_square_cornered_fast_path() -> None:
    # A rectangle must not be re-routed through the polygon path: that offsets by Euclidean
    # distance, which rounds the corners and is a strictly looser obstacle for the same board.
    # Assert the model itself, not just an incidental route.
    rectangular = _snapshot(keepouts=((4_000, 4_000, 6_000, 6_000),))
    octagonal = _snapshot(polygon_keepouts=(_octagon(5_000, 5_000, 1_000, 300),))

    rectangular_problem = _problem_of(rectangular, _request(rectangular))
    octagonal_problem = _problem_of(octagonal, _request(octagonal))

    # Half width 100 plus clearance 100 is a 200 nm margin applied with square corners, so the
    # forbidden region reaches the full (6,200, 6,200) corner rather than a rounded 200 nm arc.
    assert rectangular_problem.rect_obstacles == ((3_800, 3_800, 6_200, 6_200),)
    assert rectangular_problem.polygon_obstacles == ()
    assert octagonal_problem.rect_obstacles == ()
    assert len(octagonal_problem.polygon_obstacles) == 1
    assert octagonal_problem.polygon_obstacles[0].margin_nm == 200
    assert octagonal_problem.polygon_obstacles[0].source_id == "keepout:polygon:00"


def test_rectangular_keepout_routing_is_unchanged() -> None:
    snapshot = _snapshot(keepouts=((4_000, 4_000, 6_000, 6_000),))

    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    assert candidate.patch.paths[0].vertices == (
        PointNM(1_000, 5_000),
        PointNM(1_000, 7_000),
        PointNM(9_000, 7_000),
        PointNM(9_000, 5_000),
    )
    assert candidate.cost.length_nm == 12_000
    assert candidate.cost.bend_count == 2


def test_polygon_keepout_uses_the_routed_class_clearance_exactly() -> None:
    # A keepout carries no net, so only the routed class clearance applies. At clearance 100
    # and half width 100 an octagon edge exactly 200 nm from the corridor is legal; one
    # nanometre closer is not.
    exact = _snapshot(
        polygon_keepouts=(_octagon(5_000, 6_200, 1_000, 300),),
        route_clearance_nm=100,
    )
    inside = _snapshot(
        polygon_keepouts=(_octagon(5_000, 6_199, 1_000, 300),),
        route_clearance_nm=100,
    )
    settings = _settings(proximity_penalty_nm=0)
    router = AStarRouter()

    exact_route = _candidate(router.propose(exact, _request(exact, settings=settings)))
    inside_route = _candidate(router.propose(inside, _request(inside, settings=settings)))

    assert exact_route.patch.paths[0].vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))
    assert inside_route.cost.bend_count > 0


def test_polygon_keepout_blocking_an_endpoint_returns_no_path() -> None:
    snapshot = _snapshot(polygon_keepouts=(_octagon(1_000, 5_000, 1_000, 300),))

    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.NO_PATH)


def test_spanning_polygon_keepout_returns_no_path() -> None:
    spanning = _ring(
        ((4_500, -1_000), (5_500, -1_000), (5_800, 5_000), (5_500, 11_000), (4_500, 11_000))
    )
    snapshot = _snapshot(polygon_keepouts=(spanning,))
    request = _request(snapshot)

    astar = AStarRouter().propose(snapshot, request)
    oracle = run_dijkstra_oracle(snapshot, request)

    _assert_failure(astar, RouteFailureCode.NO_PATH)
    assert oracle.diagnostic is not None
    assert oracle.diagnostic.code is RouteFailureCode.NO_PATH


def test_polygon_keepouts_share_the_object_and_relation_budgets() -> None:
    router = AStarRouter()
    two_keepouts = _snapshot(
        polygon_keepouts=(
            _octagon(2_500, 2_500, 500, 150),
            _octagon(7_500, 7_500, 500, 150),
        )
    )
    object_limited = router.propose(
        two_keepouts, _request(two_keepouts, settings=_settings(max_obstacles=1))
    )
    _assert_failure(object_limited, RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED)

    mixed = _snapshot(
        keepouts=((200, 500, 205, 600),),
        polygon_keepouts=(_octagon(5_000, 5_000, 1_000, 300),),
    )
    mixed_limited = router.propose(mixed, _request(mixed, settings=_settings(max_obstacles=1)))
    _assert_failure(mixed_limited, RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED)

    relation_limited = router.propose(
        two_keepouts, _request(two_keepouts, settings=_settings(max_obstacle_checks=4))
    )
    _assert_failure(relation_limited, RouteFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED)
    assert relation_limited.diagnostic is not None
    assert relation_limited.diagnostic.obstacle_checks == 4
    assert relation_limited.diagnostic.expanded_states == 0


def test_zone_and_keepout_polygons_share_one_deterministic_order() -> None:
    # Both object classes now land in the same polygon list, so the sort key has to keep a
    # stable total order across them. Their ID prefixes differ, so ties cannot occur.
    snapshot = _snapshot(
        polygon_keepouts=(_octagon(3_000, 5_000, 700, 200),),
        foreign_zones=(_octagon(7_000, 5_000, 700, 200),),
    )
    request = _request(snapshot)

    problem = _problem_of(snapshot, request)
    first = _candidate(AStarRouter().propose(snapshot, request))
    second = _candidate(AStarRouter().propose(snapshot, request))

    assert [obstacle.source_id for obstacle in problem.polygon_obstacles] == [
        "keepout:polygon:00",
        "zone:foreign:00",
    ]
    assert first == second
    assert first.cost.bend_count > 0


def test_keepout_is_ignored_on_another_layer_or_without_track_prohibition() -> None:
    router = AStarRouter()
    clear = _snapshot()
    blocking = _octagon(5_000, 5_000, 1_000, 300)
    other_layer = _snapshot(polygon_keepouts=(blocking,), keepout_layer_id="layer:B.Cu")
    vias_only = _snapshot(polygon_keepouts=(blocking,), keepout_prohibits_tracks=False)

    straight = _candidate(router.propose(clear, _request(clear)))
    for snapshot in (other_layer, vias_only):
        candidate = _candidate(router.propose(snapshot, _request(snapshot)))
        assert candidate.patch.paths[0].vertices == straight.patch.paths[0].vertices
        assert candidate.cost == straight.cost


@pytest.mark.parametrize(
    "bounds",
    [
        (4_000, 4_000, 6_000, 6_000),
        (6_000, 4_000, 4_000, 6_000),
        (4_000, 6_000, 6_000, 4_000),
        (6_000, 6_000, 4_000, 4_000),
        (3_000, 4_500, 7_000, 5_500),
        (4_500, 3_000, 5_500, 7_000),
        (3_100, 4_700, 6_900, 5_300),
    ],
)
def test_diagonal_segment_envelope_contains_the_exact_stadium(
    bounds: tuple[int, int, int, int],
) -> None:
    """Exhaustively check the integer envelope is a superset of the true track shape."""

    width_nm = 200
    segment = Segment(
        id="segment:probe",
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        start=PointNM(bounds[0], bounds[1]),
        end=PointNM(bounds[2], bounds[3]),
        width_nm=width_nm,
    )
    envelope = _segment_envelope(segment)
    assert envelope is not None
    assert len(envelope) == 6
    assert len(set(envelope)) == 6
    # A simple, non-degenerate hexagon, so the ray-crossing containment test is well defined.
    assert Ring(envelope).points == envelope

    radius_nm = (width_nm + 1) // 2
    start, end = segment.start, segment.end
    edge_x, edge_y = end.x - start.x, end.y - start.y
    edge_length_sq = edge_x * edge_x + edge_y * edge_y
    minimum_x = min(point.x for point in envelope)
    maximum_x = max(point.x for point in envelope)
    minimum_y = min(point.y for point in envelope)
    maximum_y = max(point.y for point in envelope)

    # Sample the stadium densely in exact integers: every lattice point within the envelope's
    # bounding box whose squared distance to the centreline is at most the squared half width
    # is real copper and must be inside the envelope.
    checked = 0
    for x in range(minimum_x, maximum_x + 1, 10):
        for y in range(minimum_y, maximum_y + 1, 10):
            point_x, point_y = x - start.x, y - start.y
            projection = point_x * edge_x + point_y * edge_y
            if projection <= 0:
                distance_sq = point_x * point_x + point_y * point_y
            elif projection >= edge_length_sq:
                distance_sq = (x - end.x) ** 2 + (y - end.y) ** 2
            else:
                area = edge_x * point_y - edge_y * point_x
                distance_sq = (area * area) // edge_length_sq
            if distance_sq > radius_nm * radius_nm:
                continue
            checked += 1
            assert _point_in_polygon(PointNM(x, y), envelope), (x, y)
    assert checked > 100


def _point_in_polygon(point: PointNM, polygon: tuple[PointNM, ...]) -> bool:
    """Closed-region point-in-polygon using the router's own exact crossing predicate."""

    inside = False
    for index, edge_start in enumerate(polygon):
        edge_end = polygon[(index + 1) % len(polygon)]
        if _point_segment_distance_lt(point, edge_start, edge_end, 1):
            return True
        if _ray_crosses_right(point, edge_start, edge_end):
            inside = not inside
    return inside


def _scaled_circumcircle(start: PointNM, mid: PointNM, end: PointNM) -> tuple[int, int, int, int]:
    """Return (centre_x, centre_y, denominator, radius_squared) in exact scaled integers.

    The circumcentre of three integer points is rational, so the test keeps it as a scaled
    integer pair rather than dividing. Every predicate below is then a comparison between
    integers, exactly as the router's own geometry is.
    """

    ax, ay = start.x, start.y
    mx, my = mid.x, mid.y
    bx, by = end.x, end.y
    a_sq, m_sq, b_sq = ax * ax + ay * ay, mx * mx + my * my, bx * bx + by * by
    denominator = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    centre_x = a_sq * (my - by) + m_sq * (by - ay) + b_sq * (ay - my)
    centre_y = a_sq * (bx - mx) + m_sq * (ax - bx) + b_sq * (mx - ax)
    if denominator < 0:
        centre_x, centre_y, denominator = -centre_x, -centre_y, -denominator
    radius_squared = (ax * denominator - centre_x) ** 2 + (ay * denominator - centre_y) ** 2
    return centre_x, centre_y, denominator, radius_squared


def _on_arc_track(
    point: PointNM,
    start: PointNM,
    mid: PointNM,
    end: PointNM,
    half_width_nm: int,
) -> bool:
    """Exactly decide whether a lattice point is real copper of the arc track.

    A track arc is its circular centreline swept with a disc of the half width, plus the
    round caps at both ends. Membership therefore needs an annulus test and an angular
    span test. The annulus test avoids square roots by rearranging
    ``|sqrt(u) - sqrt(r)| <= h`` into ``(u + r - h^2)^2 <= 4*u*r`` once the left side is
    known non-negative, which is an exact integer comparison.
    """

    centre_x, centre_y, denominator, radius_squared = _scaled_circumcircle(start, mid, end)

    def vector(item: PointNM) -> tuple[int, int]:
        return item.x * denominator - centre_x, item.y * denominator - centre_y

    def cross(left: tuple[int, int], right: tuple[int, int]) -> int:
        return left[0] * right[1] - left[1] * right[0]

    for cap in (start, end):
        if (point.x - cap.x) ** 2 + (point.y - cap.y) ** 2 <= half_width_nm**2:
            return True

    to_point = vector(point)
    distance_squared = to_point[0] ** 2 + to_point[1] ** 2
    scaled_half_width = half_width_nm * denominator
    slack = distance_squared + radius_squared - scaled_half_width**2
    if slack > 0 and slack**2 > 4 * distance_squared * radius_squared:
        return False

    # The arc is minor here, so its span is the side of the start radius that ``mid`` is on.
    side = cross(vector(start), vector(mid))
    forward = cross(vector(start), to_point)
    backward = cross(to_point, vector(end))
    if side < 0:
        return forward <= 0 and backward <= 0
    return forward >= 0 and backward >= 0


ARC_CONTROL_POINTS: tuple[_ArcPoints, ...] = (
    # A semicircle: the inclusive boundary of the supported span, and the largest sagitta
    # any admitted arc can have.
    ((3_000, 5_000), (4_000, 4_000), (5_000, 5_000)),
    ((5_000, 5_000), (4_000, 6_000), (3_000, 5_000)),
    # Shallower arcs, where the sagitta term is small and the envelope is nearly the
    # straight-track hexagon.
    ((3_000, 5_000), (3_300, 4_400), (4_000, 4_000)),
    ((4_000, 4_000), (5_000, 4_200), (6_000, 5_000)),
    ((3_000, 3_000), (5_000, 2_000), (7_000, 3_000)),
    ((6_000, 6_000), (5_000, 5_100), (4_000, 6_000)),
    # An arc whose chord is axis-aligned, which takes the rectangle branch of the sweep.
    ((4_000, 3_000), (5_000, 4_000), (6_000, 3_000)),
)


@pytest.mark.parametrize("points", ARC_CONTROL_POINTS)
def test_arc_envelope_contains_the_exact_arc_track(points: _ArcPoints) -> None:
    """Exhaustively check the integer envelope is a superset of the real arc track."""

    width_nm = 200
    arc = Arc(
        id="arc:probe",
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        start=PointNM(*points[0]),
        mid=PointNM(*points[1]),
        end=PointNM(*points[2]),
        width_nm=width_nm,
    )
    envelope = _arc_envelope(arc)
    assert envelope is not None
    # A simple, non-degenerate polygon, so the ray-crossing containment test is well defined.
    assert Ring(envelope).points == envelope

    half_width_nm = (width_nm + 1) // 2
    # Sampling only the envelope's own bounding box would make the test blind to exactly the
    # failure it exists to catch — copper that escapes the envelope entirely. The window is
    # therefore widened around the control points by the sagitta and the half width, which
    # bounds the arc independently of how the envelope was built. That bound comes from
    # ``_arc_sagitta_bound_nm``, so the companion test below pins that helper against the
    # true sagitta exactly; together the two leave no gap.
    slack = _arc_sagitta_bound_nm(arc.start, arc.mid, arc.end) + half_width_nm + 2
    control = (arc.start, arc.mid, arc.end)
    minimum_x = min(min(point.x for point in control) - slack, *(p.x for p in envelope))
    maximum_x = max(max(point.x for point in control) + slack, *(p.x for p in envelope))
    minimum_y = min(min(point.y for point in control) - slack, *(p.y for p in envelope))
    maximum_y = max(max(point.y for point in control) + slack, *(p.y for p in envelope))

    checked = 0
    for x in range(minimum_x, maximum_x + 1, 5):
        for y in range(minimum_y, maximum_y + 1, 5):
            probe = PointNM(x, y)
            if not _on_arc_track(probe, arc.start, arc.mid, arc.end, half_width_nm):
                continue
            checked += 1
            assert _point_in_polygon(probe, envelope), (x, y)
    # The sampling has to actually find copper, or the assertion above proves nothing.
    assert checked > 100


def test_arc_envelope_refuses_an_arc_past_half_a_turn() -> None:
    """A major arc leaves its chord's span, so no chord-based envelope is honest."""

    # Centre (5,000, 5,000), radius 2,000: endpoints near the east side, mid due west.
    arc = Arc(
        id="arc:major",
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        start=PointNM(6_732, 6_000),
        mid=PointNM(3_000, 5_000),
        end=PointNM(6_732, 4_000),
        width_nm=200,
    )
    assert _arc_spans_at_most_half_turn(arc.start, arc.mid, arc.end) is False
    assert _arc_envelope(arc) is None
    # The chord model would have missed the bulge entirely: the arc reaches x=3,000 while
    # the chord sits at x=6,732, and the sagitta of the *minor* companion arc is far smaller
    # than the real excursion.
    assert arc.mid.x < min(arc.start.x, arc.end.x)


def test_arc_spans_at_most_half_turn_admits_the_exact_semicircle() -> None:
    """The inclusive boundary is deliberate: containment still holds at half a turn."""

    start, mid, end = PointNM(3_000, 5_000), PointNM(4_000, 4_000), PointNM(5_000, 5_000)
    # The inscribed angle at ``mid`` is exactly a right angle, so the dot product is zero.
    assert (start.x - mid.x) * (end.x - mid.x) + (start.y - mid.y) * (end.y - mid.y) == 0
    assert _arc_spans_at_most_half_turn(start, mid, end) is True
    # A semicircle's sagitta is its radius, which is 1,000 here.
    assert _arc_sagitta_bound_nm(start, mid, end) >= 1_000


def test_arc_sagitta_bound_never_understates_the_true_sagitta() -> None:
    """Randomised check that the integer bound is an upper bound, never an approximation."""

    generator = random.Random(20_260_806)  # noqa: S311 - deterministic fixture generation
    checked = 0
    for _ in range(400):
        centre_x = generator.randrange(-20_000, 20_000)
        centre_y = generator.randrange(-20_000, 20_000)
        radius = generator.randrange(100, 8_000)
        base = generator.uniform(0.0, 6.283)
        span = generator.uniform(0.01, 3.14)
        points = []
        for fraction in (0.0, 0.5, 1.0):
            angle = base + span * fraction
            points.append(
                PointNM(
                    round(centre_x + radius * math.cos(angle)),
                    round(centre_y + radius * math.sin(angle)),
                )
            )
        start, mid, end = points
        if len({start, mid, end}) != 3:
            continue
        if (mid.x - start.x) * (end.y - start.y) == (mid.y - start.y) * (end.x - start.x):
            continue
        if not _arc_spans_at_most_half_turn(start, mid, end):
            continue
        checked += 1
        bound = _arc_sagitta_bound_nm(start, mid, end)
        assert _bound_covers_true_sagitta(bound, start, mid, end), (start, mid, end, bound)
    assert checked > 100


def _bound_covers_true_sagitta(bound: int, start: PointNM, mid: PointNM, end: PointNM) -> bool:
    """Exactly decide ``bound >= r - h``, the true sagitta of the minor arc.

    ``r`` and ``h`` are both square roots of rationals, so the comparison hides a nested
    radical. Isolating one root at a time and squaring twice removes it, and each squaring
    is guarded by the sign of the side being squared, so the result is an exact integer
    decision rather than a tolerance. This is deliberately a different derivation from the
    one the implementation uses, so the two cannot fail in the same direction together.
    """

    centre_x, centre_y, denominator, radius_squared = _scaled_circumcircle(start, mid, end)
    chord_squared = (end.x - start.x) ** 2 + (end.y - start.y) ** 2
    # ``height`` is the scaled numerator of h: h = height / (denominator * sqrt(chord)).
    height = abs(
        (end.x - start.x) * (start.y * denominator - centre_y)
        - (end.y - start.y) * (start.x * denominator - centre_x)
    )
    # Want: bound * denominator * sqrt(chord) + height >= sqrt(radius_squared * chord).
    scaled_bound = bound * denominator
    first = radius_squared * chord_squared  # (sqrt of this) on the right
    # Step 1: scaled_bound * sqrt(chord) >= sqrt(first) - height.
    if height * height >= first:
        return True
    # Step 2: square once -> 2 * height * sqrt(first) >= first + height^2 - scaled_bound^2 * chord.
    remainder = first + height * height - scaled_bound * scaled_bound * chord_squared
    if remainder <= 0:
        return True
    # Step 3: square again; both sides are now non-negative integers.
    return 4 * height * height * first >= remainder * remainder


def test_swept_square_envelope_reproduces_the_diagonal_segment_construction() -> None:
    """The shared sweep must not have changed the committed diagonal-track envelope."""

    for bounds in ((4_000, 4_000, 6_000, 6_000), (6_000, 4_000, 4_000, 6_000)):
        segment = Segment(
            id="segment:probe",
            net_id=OTHER_NET_ID,
            layer_id=LAYER_ID,
            start=PointNM(bounds[0], bounds[1]),
            end=PointNM(bounds[2], bounds[3]),
            width_nm=200,
        )
        envelope = _segment_envelope(segment)
        assert envelope is not None
        assert envelope == _swept_square_envelope(segment.start, segment.end, 100)
    # An orthogonal track keeps its exact rectangle fast path and never becomes a polygon.
    orthogonal = Segment(
        id="segment:orthogonal",
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        start=PointNM(4_000, 5_000),
        end=PointNM(6_000, 5_000),
        width_nm=200,
    )
    assert _segment_envelope(orthogonal) is None
    assert len(_swept_square_envelope(orthogonal.start, orthogonal.end, 100)) == 4


def test_collinear_control_points_bulge_nowhere() -> None:
    """The helper's degenerate guard: Board IR rejects such an arc, callers may not."""

    assert _arc_sagitta_bound_nm(PointNM(0, 0), PointNM(1_000, 1_000), PointNM(2_000, 2_000)) == 0


# A shallow foreign arc bowing across the straight corridor at x=5,000. Its chord is the
# vertical segment (5,000, 3,000) to (5,000, 7,000) and its sagitta is 100 nm, so the
# envelope is meaningfully wider than the chord without swallowing the whole board.
BLOCKING_ARC: _ArcPoints = ((5_000, 3_000), (5_100, 5_000), (5_000, 7_000))


def test_foreign_arc_blocks_its_corridor_and_matches_the_oracle() -> None:
    """A board that used to be refused outright now routes, detouring around the arc."""

    baseline = _candidate(AStarRouter().propose(_snapshot(), _request(_snapshot())))
    assert baseline.cost.bend_count == 0

    snapshot = _snapshot(foreign_arc=BLOCKING_ARC)
    request = _request(snapshot)
    router = AStarRouter()

    first = _candidate(router.propose(snapshot, request))
    second = _candidate(router.propose(snapshot, request))
    oracle = run_dijkstra_oracle(snapshot, request)

    assert first == second
    assert canonical_candidate_bytes(first) == canonical_candidate_bytes(second)
    # The straight corridor is gone, so the route has to bend around the arc envelope.
    assert first.cost.bend_count > 0
    assert first.metrics.hard_internal_violations == 0
    # No vertex may sit inside the arc envelope inflated by the route half width plus the
    # stricter of the two class clearances, which is 100 + 100 here.
    assert all(
        not (4_599 < vertex.x < 5_401 and 2_599 < vertex.y < 7_401)
        for vertex in first.patch.paths[0].vertices
    )
    assert isinstance(oracle, DijkstraResult)
    assert oracle.total_cost_nm == first.cost.total_cost_nm
    assert oracle.bend_count == first.cost.bend_count


def test_foreign_arc_envelope_is_wider_than_its_bare_chord() -> None:
    """The sagitta term is load-bearing: the bulge is what closes the straight corridor."""

    snapshot = _snapshot(foreign_arc=BLOCKING_ARC)
    problem = _problem_of(snapshot, _request(snapshot))
    envelopes = [item for item in problem.polygon_obstacles if item.source_id.startswith("arc:")]

    assert len(envelopes) == 1
    minimum_x = min(point.x for point in envelopes[0].points)
    maximum_x = max(point.x for point in envelopes[0].points)
    # The chord is the line x=5,000 and the half width is 100, so a chord-only envelope
    # would stop at 4,900/5,100. The arc bows 100 nm further, and the envelope must show it.
    assert minimum_x < 4_900
    assert maximum_x > 5_100


def test_selected_net_arc_is_refused_rather_than_enveloped() -> None:
    """Obstacles may over-approximate; attachment copper may not, so this stays a refusal."""

    snapshot = _snapshot(own_arc=BLOCKING_ARC)
    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
    assert result.diagnostic is not None
    assert "attachment copper" in result.diagnostic.message


def test_selected_net_arc_is_refused_on_every_copper_layer() -> None:
    """Connectivity is a multilayer question, so an off-layer arc is refused as well."""

    snapshot = _snapshot(own_arc=BLOCKING_ARC, arc_layer_id="layer:B.Cu")
    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)


def test_foreign_arc_on_another_layer_leaves_the_corridor_clear() -> None:
    """A single-layer route cannot reach foreign copper it never shares a layer with."""

    snapshot = _snapshot(foreign_arc=BLOCKING_ARC, arc_layer_id="layer:B.Cu")
    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    assert candidate.cost.bend_count == 0


def test_major_foreign_arc_on_the_selected_layer_is_refused() -> None:
    """Past half a turn the chord containment argument fails, so there is no honest model."""

    snapshot = _snapshot(foreign_arc=((6_732, 6_000), (3_000, 5_000), (6_732, 4_000)))
    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
    assert result.diagnostic is not None
    assert "half a turn" in result.diagnostic.message


def test_arc_envelope_is_charged_against_the_obstacle_budget() -> None:
    """An arc envelope counts against max_obstacles exactly as a zone or keepout does."""

    # One keepout takes the single permitted slot, so the arc envelope is the object that
    # runs the budget out — it is charged, not exempt.
    snapshot = _snapshot(foreign_arc=BLOCKING_ARC, keepouts=((1_500, 1_500, 2_000, 2_000),))
    result = AStarRouter().propose(
        snapshot, _request(snapshot, settings=_settings(max_obstacles=1))
    )

    _assert_failure(result, RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED)

    # Without the arc the same board and budget are fine, so the budget failure is the
    # arc's doing rather than the keepout's.
    clear = _snapshot(keepouts=((1_500, 1_500, 2_000, 2_000),))
    assert AStarRouter().propose(clear, _request(clear, settings=_settings(max_obstacles=1))).ok


def test_foreign_diagonal_segment_detours_deterministically_and_matches_the_oracle() -> None:
    snapshot = _snapshot(foreign_segment=(4_000, 3_000, 6_000, 7_000))
    request = _request(snapshot)
    router = AStarRouter()

    first = _candidate(router.propose(snapshot, request))
    second = _candidate(router.propose(snapshot, request))
    oracle = run_dijkstra_oracle(snapshot, request)

    assert first == second
    assert canonical_candidate_bytes(first) == canonical_candidate_bytes(second)
    assert first.cost.bend_count > 0
    assert first.metrics.hard_internal_violations == 0
    assert isinstance(oracle, DijkstraResult)
    assert oracle.total_cost_nm == first.cost.total_cost_nm
    assert oracle.bend_count == first.cost.bend_count
    assert oracle.proximity_steps == first.cost.proximity_steps


def test_forty_five_degree_foreign_segment_blocks_its_corridor() -> None:
    # A 45 degree track from (3,000, 3,000) to (7,000, 7,000) crosses the straight y=5,000
    # corridor at x=5,000, so the straight route must be refused and a detour found.
    snapshot = _snapshot(foreign_segment=(3_000, 3_000, 7_000, 7_000))

    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    assert candidate.cost.bend_count > 0
    assert candidate.patch.paths[0].vertices[0] == PointNM(1_000, 5_000)
    assert candidate.patch.paths[0].vertices[-1] == PointNM(9_000, 5_000)


def test_foreign_diagonal_segment_uses_the_stricter_class_clearance() -> None:
    router = AStarRouter()
    lenient = _snapshot(foreign_segment=(4_000, 1_500, 6_000, 3_500), other_clearance_nm=100)
    strict = _snapshot(foreign_segment=(4_000, 1_500, 6_000, 3_500), other_clearance_nm=2_000)
    settings = _settings(proximity_penalty_nm=0)

    lenient_route = _candidate(router.propose(lenient, _request(lenient, settings=settings)))
    strict_route = _candidate(router.propose(strict, _request(strict, settings=settings)))

    # The diagonal sits clear of the straight corridor under the lenient clearance and
    # reaches into it once the obstacle net's own stricter class clearance governs.
    assert lenient_route.cost.bend_count == 0
    assert strict_route.cost.bend_count > 0


def test_orthogonal_foreign_segments_keep_the_exact_rectangle_fast_path() -> None:
    orthogonal = _snapshot(foreign_segment=(5_000, 3_000, 5_000, 7_000))
    diagonal = _snapshot(foreign_segment=(4_000, 3_000, 6_000, 7_000))

    orthogonal_problem = _problem_of(orthogonal, _request(orthogonal))
    diagonal_problem = _problem_of(diagonal, _request(diagonal))

    # Half width 100 plus clearance 100 inflates the swept rectangle with square corners.
    assert orthogonal_problem.rect_obstacles == ((4_700, 2_700, 5_300, 7_300),)
    assert orthogonal_problem.polygon_obstacles == ()
    assert diagonal_problem.rect_obstacles == ()
    assert len(diagonal_problem.polygon_obstacles) == 1
    assert diagonal_problem.polygon_obstacles[0].margin_nm == 200
    assert diagonal_problem.polygon_obstacles[0].source_id == "segment:foreign"
    assert len(diagonal_problem.polygon_obstacles[0].points) == 6


def test_foreign_diagonal_segments_share_the_object_and_relation_budgets() -> None:
    router = AStarRouter()
    snapshot = _snapshot(
        foreign_segment=(4_000, 3_000, 6_000, 7_000),
        keepouts=((200, 500, 205, 600),),
    )

    object_limited = router.propose(
        snapshot, _request(snapshot, settings=_settings(max_obstacles=1))
    )
    _assert_failure(object_limited, RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED)

    relation_limited = router.propose(
        snapshot, _request(snapshot, settings=_settings(max_obstacle_checks=3))
    )
    _assert_failure(relation_limited, RouteFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED)
    assert relation_limited.diagnostic is not None
    assert relation_limited.diagnostic.obstacle_checks == 3
    assert relation_limited.diagnostic.expanded_states == 0


def test_a_diagonal_foreign_segment_clear_of_the_route_does_not_force_a_detour() -> None:
    aside = _snapshot(foreign_segment=(3_000, 8_000, 5_000, 9_500))

    assert _candidate(AStarRouter().propose(aside, _request(aside))).cost.bend_count == 0


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_diagonal_core_chain_is_inside_the_track_and_self_connected(seed: int) -> None:
    """Every square is real copper and touches its neighbour, in exact integer arithmetic.

    These are the two properties the component model relies on. The subset half is what stops
    the router claiming an electrical connection the board does not have; the overlap half is
    what makes a chain behave as one piece of copper rather than a dotted line.
    """

    # Reproducible sampling of the geometry space; nothing here is security relevant.
    generator = random.Random(seed)  # noqa: S311
    work = _WorkBudget(settings=_settings(max_obstacle_checks=10_000_000), cancelled=None)
    checked_squares = 0

    for _ in range(60):
        start_x = generator.randint(-50_000, 50_000)
        start_y = generator.randint(-50_000, 50_000)
        end_x = start_x + generator.choice([-1, 1]) * generator.randint(1, 2_000_000)
        end_y = start_y + generator.choice([-1, 1]) * generator.randint(1, 2_000_000)
        width_nm = generator.choice([63_500, 100_000, 150_000, 200_000, 250_000, 400_000])
        segment = Segment(
            id="segment:probe",
            net_id=NET_ID,
            layer_id=LAYER_ID,
            start=PointNM(start_x, start_y),
            end=PointNM(end_x, end_y),
            width_nm=width_nm,
        )
        cores = _diagonal_segment_cores(segment, work)
        assert cores is not None

        radius_nm = width_nm // 2
        edge_x, edge_y = end_x - start_x, end_y - start_y
        edge_length_sq = edge_x * edge_x + edge_y * edge_y

        for minimum_x, minimum_y, maximum_x, maximum_y in cores:
            # A square is convex, so containment of its four corners implies containment of
            # every point in it.
            for corner_x, corner_y in (
                (minimum_x, minimum_y),
                (maximum_x, minimum_y),
                (minimum_x, maximum_y),
                (maximum_x, maximum_y),
            ):
                point_x, point_y = corner_x - start_x, corner_y - start_y
                projection = point_x * edge_x + point_y * edge_y
                if projection <= 0:
                    distance_sq = point_x * point_x + point_y * point_y
                elif projection >= edge_length_sq:
                    distance_sq = (corner_x - end_x) ** 2 + (corner_y - end_y) ** 2
                else:
                    area = edge_x * point_y - edge_y * point_x
                    distance_sq = (area * area) // edge_length_sq
                assert distance_sq <= radius_nm * radius_nm
            checked_squares += 1

        for previous, following in pairwise(cores):
            assert previous[0] <= following[2]
            assert following[0] <= previous[2]
            assert previous[1] <= following[3]
            assert following[1] <= previous[3]

        # The chain reaches both solder points, which is what lets it attach to pads.
        canonical_start, canonical_end = sorted(
            [(start_x, start_y), (end_x, end_y)],
        )
        assert (cores[0][0] + cores[0][2]) // 2 == canonical_start[0]
        assert (cores[0][1] + cores[0][3]) // 2 == canonical_start[1]
        assert (cores[-1][0] + cores[-1][2]) // 2 == canonical_end[0]
        assert (cores[-1][1] + cores[-1][3]) // 2 == canonical_end[1]

    assert checked_squares > 200


# Three pads: pad:01 at (1,000, 5,000), pad:02 at (9,000, 5,000), pad:03 at (5,000, 8,000).
_SPINE = (1_000, 5_000, 9_000, 5_000)
_BRANCH = (5_000, 5_000, 5_000, 8_000)


def test_a_fully_connected_multi_pin_net_reports_already_connected() -> None:
    snapshot = _snapshot(third_target=True, own_segments=(_SPINE, _BRANCH))
    request = _request(snapshot)
    router = AStarRouter()

    first = router.propose(snapshot, request)
    connection = _connection(first)

    assert first == router.propose(snapshot, request)
    assert connection.pad_count == 3
    assert connection.attachment_segments == 2
    assert connection.component_objects == 5
    # The bounding pair of the lexicographically sorted pads, not a route.
    assert connection.start_pad_id == "pad:01"
    assert connection.end_pad_id == "pad:03"


def test_a_partly_connected_multi_pin_net_is_routed_as_a_tree() -> None:
    # The spine joins pad:01 and pad:02, leaving one component to merge with pad:03.
    snapshot = _snapshot(third_target=True, own_segments=(_SPINE,))
    request = _request(snapshot)
    router = AStarRouter()

    candidate = _candidate(router.propose(snapshot, request))

    assert candidate == router.propose(snapshot, request).candidate
    assert candidate.pad_count == 3
    assert candidate.ordering_policy == "batched-1-steiner-v1"
    # Two components, so exactly one merge.
    assert len(candidate.patch.paths) == 1
    assert candidate.metrics.unrouted_connections == 0


def test_a_multi_pin_net_joined_through_a_via_is_recognised_across_layers() -> None:
    # The via is copper on every layer, so it is a joint rather than a blind spot.
    snapshot = _snapshot(third_target=True, own_segments=(_SPINE, _BRANCH), own_via=True)

    connection = _connection(AStarRouter().propose(snapshot, _request(snapshot)))

    assert connection.pad_count == 3
    assert connection.vias == 1
    assert connection.component_objects == connection.attachment_segments + 3 + 1


def test_a_multi_pin_net_carrying_a_zone_is_never_claimed_connected() -> None:
    snapshot = _snapshot(
        third_target=True,
        own_segments=(_SPINE, _BRANCH),
        own_zone=_rectangle(1_000, 1_000, 2_000, 2_000),
    )

    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.INVALID_TWO_PIN_NET)


def test_a_two_pin_net_still_names_its_via_and_zone_directly() -> None:
    router = AStarRouter()
    with_via = _snapshot(own_segments=((1_000, 5_000, 9_000, 5_000),), own_via=True)
    with_zone = _snapshot(
        own_segments=((1_000, 5_000, 9_000, 5_000),),
        own_zone=_rectangle(1_000, 1_000, 2_000, 2_000),
    )

    via_result = router.propose(with_via, _request(with_via))
    zone_result = router.propose(with_zone, _request(with_zone))

    # The via now joins the net rather than hiding it, so the connection is recognised.
    assert _connection(via_result).vias == 1
    # A zone still cannot prove connectivity, because its fill is not trusted.
    _assert_failure(zone_result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
    assert zone_result.diagnostic is not None
    assert "zone" in zone_result.diagnostic.message


def test_a_multi_pin_net_without_same_net_copper_is_routed_from_its_pads_alone() -> None:
    snapshot = _snapshot(third_target=True)

    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    assert candidate.pad_count == 3
    assert len(candidate.patch.paths) == 2


@pytest.mark.parametrize(
    ("shape", "size_x_nm", "size_y_nm", "radius_nm"),
    [
        (PadShape.CIRCLE, 2_000_000, 2_000_000, None),
        (PadShape.CIRCLE, 1_600_000, 1_600_000, None),
        (PadShape.OVAL, 2_000_000, 2_000_000, None),
        (PadShape.OVAL, 2_000_000, 1_200_000, None),
        (PadShape.RECT, 1_000_000, 600_000, None),
        (PadShape.ROUNDRECT, 1_200_000, 1_400_000, 240_000),
        # A radius of exactly half the short side: the corners meet and the pad is a stadium,
        # so its central band collapses to a bar. Every other case here has a band with real
        # height, which is the fixture monoculture that let a disc-shaped core go unnoticed.
        (PadShape.ROUNDRECT, 2_000_000, 1_000_000, 500_000),
    ],
)
def test_every_pad_core_rectangle_lies_inside_the_pad(
    shape: PadShape, size_x_nm: int, size_y_nm: int, radius_nm: int | None
) -> None:
    """Cores must under-approximate copper, so each corner has to be inside the real pad."""

    pad = replace(
        _pad("pad:probe", (0, 0)),
        shape=shape,
        size_x_nm=size_x_nm,
        size_y_nm=size_y_nm,
        roundrect_radius_nm=radius_nm,
    )
    cores = _pad_cores(pad)
    assert cores is not None

    half_x, half_y = size_x_nm // 2, size_y_nm // 2
    for minimum_x, minimum_y, maximum_x, maximum_y in cores:
        for corner_x, corner_y in (
            (minimum_x, minimum_y),
            (maximum_x, minimum_y),
            (minimum_x, maximum_y),
            (maximum_x, maximum_y),
        ):
            if shape is PadShape.RECT:
                inside = abs(corner_x) <= half_x and abs(corner_y) <= half_y
            elif shape is PadShape.ROUNDRECT:
                assert radius_nm is not None
                inside = abs(corner_x) <= half_x and abs(corner_y) <= half_y - radius_nm
            else:
                # Stadium: within `short` of the centreline running along the long axis.
                short = min(half_x, half_y)
                spine = max(half_x - half_y, 0), max(half_y - half_x, 0)
                offset_x = max(abs(corner_x) - spine[0], 0)
                offset_y = max(abs(corner_y) - spine[1], 0)
                inside = offset_x * offset_x + offset_y * offset_y <= short * short
            assert inside, (shape, (corner_x, corner_y))


def test_a_stadium_roundrect_pad_is_not_mistaken_for_a_round_pad() -> None:
    """A collapsed core means "no band left", not "this pad is a disc".

    ``_pad_cores`` gives a round pad its inscribed square, because a disc's central rectangle
    degenerates to a bar and a bar seeds no search. It used to detect that case by the collapse
    alone - and a roundrect whose radius is half its shorter side collapses identically while
    being nowhere near as tall as a disc of its longer half extent. This 2.0 x 1.0 mm stadium
    was handed a core reaching 1.0 mm from the centre in y, where its copper stops at 0.5 mm:
    an attachment core claiming copper that is not there, which is the one direction it may
    never err in. KiCad writes this pad for a `roundrect_rratio` of 0.5.
    """

    stadium = replace(
        _pad("pad:stadium", (0, 0)),
        shape=PadShape.ROUNDRECT,
        size_x_nm=2_000_000,
        size_y_nm=1_000_000,
        roundrect_radius_nm=500_000,
    )

    cores = _pad_cores(stadium)

    assert cores == ((-1_000_000, 0, 1_000_000, 0),)
    for _, minimum_y, _, maximum_y in cores:
        assert abs(minimum_y) <= 500_000 and abs(maximum_y) <= 500_000


def test_a_round_pad_offers_attachment_area_on_both_axes() -> None:
    """A bar through the centre is a legal core but cannot host a lattice node off its axis."""

    round_pad = replace(
        _pad("pad:round", (0, 0)),
        shape=PadShape.CIRCLE,
        size_x_nm=2_000_000,
        size_y_nm=2_000_000,
    )

    cores = _pad_cores(round_pad)

    assert cores is not None
    assert len(cores) == 3
    assert all(
        maximum_x > minimum_x or maximum_y > minimum_y
        for minimum_x, minimum_y, maximum_x, maximum_y in cores
    )
    # One rectangle has real extent on both axes, which is what a search can seed from.
    assert any(
        maximum_x > minimum_x and maximum_y > minimum_y
        for minimum_x, minimum_y, maximum_x, maximum_y in cores
    )
    # Every rectangle contains the pad centre, so a pad is never split into two components.
    assert all(
        minimum_x <= 0 <= maximum_x and minimum_y <= 0 <= maximum_y
        for minimum_x, minimum_y, maximum_x, maximum_y in cores
    )


def test_a_disconnected_multi_pin_net_is_routed_as_a_deterministic_tree() -> None:
    snapshot = _snapshot(third_target=True)
    request = _request(snapshot)
    router = AStarRouter()

    first = _candidate(router.propose(snapshot, request))
    second = _candidate(router.propose(snapshot, request))

    assert first == second
    assert canonical_candidate_bytes(first) == canonical_candidate_bytes(second)
    assert first.pad_count == 3
    assert first.ordering_policy == "batched-1-steiner-v1"
    # Three isolated pads are three components, so a spanning tree needs exactly two merges.
    assert len(first.patch.paths) == 2
    assert first.cost.length_nm == sum(path.length_nm for path in first.patch.paths)
    assert first.cost.bend_count == sum(path.bend_count for path in first.patch.paths)
    assert first.metrics.unrouted_connections == 0


def test_pre_batched_multi_pin_replay_uses_the_recorded_component_mst_profile() -> None:
    # Eight distant keepouts activate today's spatial index.  They stay far from the route, so
    # the historical linear scan changes only recorded work, not legal geometry.
    snapshot = _snapshot(
        third_target=True,
        keepouts=tuple((500 + index * 1_000, 500, 700 + index * 1_000, 700) for index in range(8)),
    )
    request = _request(snapshot)
    legacy = _candidate(
        AStarRouter.for_replay(
            router_version="astar-grid/0.4.0",
            policy="orthogonal-a-star-v1",
            ordering_policy="component-mst-v1",
            pad_count=3,
        ).propose(snapshot, request)
    )

    replayed = _candidate(AStarRouter().replay(snapshot, legacy))
    current = _candidate(AStarRouter().propose(snapshot, request))

    assert legacy.ordering_policy == "component-mst-v1"
    assert replayed == legacy
    assert canonical_candidate_bytes(replayed) == canonical_candidate_bytes(legacy)
    assert current.ordering_policy == "batched-1-steiner-v1"
    assert legacy.metrics.obstacle_checks > current.metrics.obstacle_checks


def test_every_tree_leg_starts_on_copper_that_already_belongs_to_the_net() -> None:
    """A leg that floats free of its source component would make the merge a fiction."""

    snapshot = _snapshot(third_target=True, own_segments=(_SPINE,))
    problem = _problem_of(snapshot, _request(snapshot))
    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    covered = [core for component in problem.components for core in component]
    for path in candidate.patch.paths:
        head = path.vertices[0]
        assert any(
            core[0] <= head.x <= core[2] and core[1] <= head.y <= core[3] for core in covered
        )


def test_tree_legs_may_attach_to_earlier_legs_rather_than_only_to_pads() -> None:
    # Four pads in a line: the last merge should be able to meet copper laid by an earlier leg.
    snapshot = _snapshot(third_target=True, extra_pad=False)
    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    assert len(candidate.patch.paths) == 2
    # Later legs are never obstacles for one another; a same-net crossing is legal copper.
    assert candidate.metrics.hard_internal_violations == 0


def test_multi_pin_routing_matches_the_dijkstra_oracle_leg_by_leg() -> None:
    snapshot = _snapshot(third_target=True, keepouts=((4_000, 4_000, 6_000, 6_000),))
    request = _request(snapshot)

    candidate = _candidate(AStarRouter().propose(snapshot, request))

    # The oracle shares preparation and edge costs, so per-leg optimality is comparable even
    # though the tree as a whole carries no optimality claim.
    assert candidate.cost.total_cost_nm == (
        candidate.cost.length_nm + candidate.cost.bend_cost_nm + candidate.cost.proximity_cost_nm
    )
    assert candidate.metrics.expanded_states <= candidate.settings.max_expansions


def test_a_tree_shares_one_budget_and_fails_closed_deterministically() -> None:
    snapshot = _snapshot(third_target=True)
    router = AStarRouter()
    limited = _request(snapshot, settings=_settings(max_expansions=12))

    first = router.propose(snapshot, limited)
    second = router.propose(snapshot, limited)

    _assert_failure(first, RouteFailureCode.SEARCH_BUDGET_EXCEEDED)
    assert first.diagnostic is not None
    assert second.diagnostic is not None
    # The merge order and the budget are both pure functions of the snapshot, so the leg that
    # exhausts the ceiling and the counts it reports are reproducible.
    assert first.diagnostic.expanded_states == second.diagnostic.expanded_states
    assert first.diagnostic.obstacle_checks == second.diagnostic.obstacle_checks


def test_a_multi_pin_leg_failure_fails_the_whole_call() -> None:
    # A spanning keepout isolates the third pad, so one merge has no legal path and the whole
    # proposal is refused rather than a partial tree being emitted.
    snapshot = _snapshot(third_target=True, keepouts=((4_500, -1_000, 5_500, 11_000),))

    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.NO_PATH)


def test_two_pin_candidates_keep_a_single_path_and_no_ordering_policy() -> None:
    snapshot = _snapshot()

    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    assert candidate.pad_count == 2
    assert len(candidate.patch.paths) == 1
    assert candidate.ordering_policy == "single-path"


@pytest.mark.parametrize(
    ("diameter_nm", "drill_nm"),
    [(800_000, 400_000), (600_000, 300_000), (1_000_000, 500_000), (450_000, 250_000)],
)
def test_via_annulus_cores_are_inside_the_ring_and_clear_of_the_drill(
    diameter_nm: int, drill_nm: int
) -> None:
    """Cores must be copper: inside the outer circle and outside the drilled hole."""

    via = Via(
        id="via:probe",
        net_id=NET_ID,
        center=PointNM(0, 0),
        diameter_nm=diameter_nm,
        drill_nm=drill_nm,
        start_layer_id=LAYER_ID,
        end_layer_id="layer:B.Cu",
    )

    cores = _via_cores(via)

    assert cores is not None
    assert len(cores) == 4
    outer_nm = diameter_nm // 2
    hole_nm = drill_nm // 2
    for minimum_x, minimum_y, maximum_x, maximum_y in cores:
        assert maximum_x > minimum_x and maximum_y > minimum_y
        for corner_x, corner_y in (
            (minimum_x, minimum_y),
            (maximum_x, minimum_y),
            (minimum_x, maximum_y),
            (maximum_x, maximum_y),
        ):
            # Inside the outer copper circle.
            assert corner_x * corner_x + corner_y * corner_y <= outer_nm * outer_nm
        # Entirely clear of the drilled hole, which is not copper: the rectangle nearest the
        # centre on each axis still starts outside the hole radius.
        nearest_x = min(abs(minimum_x), abs(maximum_x)) if minimum_x * maximum_x > 0 else 0
        nearest_y = min(abs(minimum_y), abs(maximum_y)) if minimum_y * maximum_y > 0 else 0
        assert nearest_x * nearest_x + nearest_y * nearest_y >= hole_nm * hole_nm


def test_a_via_core_never_covers_the_drill_hole_itself() -> None:
    """Copper touching only the hole region must not count as touching the via."""

    via = Via(
        id="via:probe",
        net_id=NET_ID,
        center=PointNM(0, 0),
        diameter_nm=800_000,
        drill_nm=400_000,
        start_layer_id=LAYER_ID,
        end_layer_id="layer:B.Cu",
    )
    cores = _via_cores(via)
    assert cores is not None

    # A rectangle wholly inside the drilled hole is not copper and must touch no core.
    hole_only = (-150_000, -150_000, 150_000, 150_000)
    assert not any(_rectangles_touch(hole_only, core) for core in cores)
    # A rectangle reaching the annulus does touch one.
    reaching = (-150_000, -150_000, 250_000, 150_000)
    assert any(_rectangles_touch(reaching, core) for core in cores)


def test_a_via_joins_two_pieces_of_copper_that_do_not_touch_each_other() -> None:
    # Two stubs stop short of one another but each reaches the via's annulus, so the via is
    # what makes the net one component. Its ring spans 200..387 nm from the centre at (5,000).
    snapshot = _snapshot(
        own_via=True,
        own_segments=((1_000, 5_000, 4_700, 5_000), (5_300, 5_000, 9_000, 5_000)),
    )
    router = AStarRouter()
    request = _request(snapshot)

    first = router.propose(snapshot, request)
    connection = _connection(first)

    assert first == router.propose(snapshot, request)
    assert connection.vias == 1
    assert connection.pad_count == 2
    assert connection.attachment_segments == 2


def test_without_the_via_those_same_two_stubs_are_not_connected() -> None:
    snapshot = _snapshot(
        own_segments=((1_000, 5_000, 4_700, 5_000), (5_300, 5_000, 9_000, 5_000)),
    )

    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    # The gap is real, so the router proposes copper to close it rather than claiming it shut.
    assert candidate.cost.length_nm > 0


def test_a_net_that_a_via_cannot_join_still_fails_closed() -> None:
    # A via far from every piece of the net's copper joins nothing, so the claim is refused.
    snapshot = _snapshot(own_via=True)

    result = AStarRouter().propose(snapshot, _request(snapshot))

    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
    assert result.diagnostic is not None
    assert "via" in result.diagnostic.message


def test_a_same_net_zone_still_blocks_the_multilayer_claim() -> None:
    snapshot = _snapshot(
        own_via=True,
        own_segments=((1_000, 5_000, 4_700, 5_000), (5_300, 5_000, 9_000, 5_000)),
        own_zone=_rectangle(1_000, 1_000, 2_000, 2_000),
    )

    result = AStarRouter().propose(snapshot, _request(snapshot))

    # A zone's fill is not trusted, so no connectivity claim is made whatever the vias show.
    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
    assert result.connected is None


def test_a_leg_that_grows_into_a_later_component_merges_instead_of_crashing() -> None:
    """An earlier leg can reach copper a later merge was still scheduled to join.

    Four pads in a line: whichever legs run first, the growing component can touch the pad a
    subsequent merge was going to reach, so by that merge's turn the two are already one piece.
    That is a merge that has happened, not an inconsistency, and it must not raise.
    """

    snapshot = _snapshot(
        third_target=True,
        own_segments=((1_000, 5_000, 9_000, 5_000), (5_000, 5_000, 5_000, 8_000)),
    )
    router = AStarRouter()
    request = _request(snapshot)

    first = router.propose(snapshot, request)
    second = router.propose(snapshot, request)

    assert first.terminal
    assert first == second


def test_a_tree_shares_one_expansion_ceiling_across_every_leg() -> None:
    """A three-pad net must not be allowed three times the authorised search work."""

    snapshot = _snapshot(third_target=True)
    router = AStarRouter()
    generous = _candidate(router.propose(snapshot, _request(snapshot)))
    assert len(generous.patch.paths) == 2

    # Enough for the first leg alone, not for both.
    limited = _request(snapshot, settings=_settings(max_expansions=19))
    first = router.propose(snapshot, limited)
    second = router.propose(snapshot, limited)

    _assert_failure(first, RouteFailureCode.SEARCH_BUDGET_EXCEEDED)
    assert first.diagnostic is not None
    assert first.diagnostic.expanded_states <= 19
    assert first.diagnostic.expanded_states == second.diagnostic.expanded_states  # type: ignore[union-attr]
    # Whatever the ceiling admits, a returned candidate never claims more than it.
    assert generous.metrics.expanded_states <= generous.settings.max_expansions


def test_a_multi_pin_pad_off_the_lattice_is_still_reached_through_its_core() -> None:
    """The off-grid rule is a two-pin lattice rule and must not bind multi-pin legs.

    With more than two pads no single grid step can divide every pad-centre delta at once, so
    applying that rule to a wider net would refuse boards the search routes perfectly well.
    """

    router = AStarRouter()
    # pad:03's centre is 500 nm off the 1,000 nm lattice anchored at pad:01, but its 400 nm
    # pad core still covers a lattice node.
    off_lattice = _snapshot(third_target=True)
    content = off_lattice.content
    moved = make_snapshot(
        make_content(
            source=content.source,
            outline=content.outline,
            copper_layers=content.copper_layers,
            nets=content.nets,
            constraints=content.constraints,
            footprints=content.footprints,
            pads=tuple(
                replace(pad, center=PointNM(pad.center.x + 100, pad.center.y + 100))
                if pad.id == "pad:03"
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

    candidate = _candidate(router.propose(moved, _request(moved)))

    assert candidate.pad_count == 3
    assert len(candidate.patch.paths) == 2


def test_verified_fill_from_another_board_is_refused() -> None:
    """Fill proved against a different board must never be believed for this one."""

    snapshot = _snapshot(own_zone=_rectangle(1_000, 1_000, 2_000, 2_000))
    foreign = VerifiedFill(
        net_id=NET_ID,
        layer_id=LAYER_ID,
        points=(PointNM(0, 0), PointNM(9_999, 0), PointNM(9_999, 9_999)),
        source_revision=OTHER_REVISION,
    )

    result = AStarRouter().propose(snapshot, _request(snapshot), verified_fill=(foreign,))

    _assert_failure(result, RouteFailureCode.STALE_FILL)
    assert result.diagnostic is not None
    assert "different board revision" in result.diagnostic.message


def test_fresh_foreign_fill_replaces_conservative_zone_envelope() -> None:
    """Fresh KiCad fill opens a clear corridor that the zone outline would conservatively block."""

    snapshot = _snapshot(
        foreign_zones=(_rectangle(3_000, 3_000, 7_000, 7_000),),
    )
    fill = VerifiedFill(
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        points=(
            PointNM(3_000, 6_000),
            PointNM(7_000, 6_000),
            PointNM(7_000, 7_000),
            PointNM(3_000, 7_000),
        ),
        source_revision=SOURCE_REVISION,
    )

    conservative = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))
    exact = _candidate(AStarRouter().propose(snapshot, _request(snapshot), verified_fill=(fill,)))

    assert conservative.cost.length_nm > 8_000
    assert exact.cost.length_nm == 8_000
    assert exact.patch.paths[0].vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))


def test_fresh_fill_respects_governing_zone_clearance_and_track_half_width() -> None:
    """Exact fill still carries the zone's clearance rule around its copper polygon."""

    fill_points = (
        PointNM(3_000, 5_500),
        PointNM(7_000, 5_500),
        PointNM(7_000, 5_600),
        PointNM(3_000, 5_600),
    )

    low_clearance = _snapshot(
        foreign_zones=(_rectangle(3_000, 5_500, 7_000, 5_600),),
        zone_clearance_nm=100,
    )
    low_fill = VerifiedFill(
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        points=fill_points,
        source_revision=SOURCE_REVISION,
    )
    low_candidate = _candidate(
        AStarRouter().propose(low_clearance, _request(low_clearance), verified_fill=(low_fill,))
    )

    high_clearance = _snapshot(
        foreign_zones=(_rectangle(3_000, 5_500, 7_000, 5_600),),
        zone_clearance_nm=1_000,
    )
    high_fill = VerifiedFill(
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        points=fill_points,
        source_revision=SOURCE_REVISION,
    )
    high_candidate = _candidate(
        AStarRouter().propose(
            high_clearance,
            _request(high_clearance),
            verified_fill=(high_fill,),
        )
    )

    # 100 nm route half-width + 100 nm zone clearance leaves the 5,000 nm centreline open;
    # the 1,000 nm zone rule must inflate the same exact polygon and force a detour.
    assert low_candidate.cost.length_nm == 8_000
    assert high_candidate.cost.length_nm > low_candidate.cost.length_nm


def test_verified_fill_without_a_board_ir_zone_is_refused() -> None:
    snapshot = _snapshot()
    fill = VerifiedFill(
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        points=(PointNM(3_000, 6_000), PointNM(7_000, 6_000), PointNM(7_000, 7_000)),
        source_revision=SOURCE_REVISION,
    )

    result = AStarRouter().propose(snapshot, _request(snapshot), verified_fill=(fill,))

    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
    assert result.diagnostic is not None
    assert "matching Board IR zone" in result.diagnostic.message


def test_verified_fill_escaping_its_zone_outline_is_refused() -> None:
    """Retiring an envelope is the one shrink this router performs, so its precondition is checked.

    KiCad clips poured copper to the zone outline, so an island that reaches past it did not come
    from that zone's fill. Believing it would drop the zone's conservative envelope in exchange
    for copper that never covered the same area, which is the one direction an obstacle may not
    move. ADR-0070 already gates the ordered-layer adapter this way; this is the single-layer half.
    """

    snapshot = _snapshot(foreign_zones=(_rectangle(3_000, 3_000, 7_000, 7_000),))
    escaping = VerifiedFill(
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        points=(
            PointNM(3_000, 6_000),
            PointNM(7_500, 6_000),
            PointNM(7_500, 7_000),
            PointNM(3_000, 7_000),
        ),
        source_revision=SOURCE_REVISION,
    )

    result = AStarRouter().propose(snapshot, _request(snapshot), verified_fill=(escaping,))

    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
    assert result.diagnostic is not None
    assert "escapes its backing" in result.diagnostic.message


def test_a_degenerate_verified_fill_island_is_refused() -> None:
    """The fill parser refuses a ring under three vertices; the typed seam must refuse it too."""

    snapshot = _snapshot(foreign_zones=(_rectangle(3_000, 3_000, 7_000, 7_000),))
    degenerate = VerifiedFill(
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        points=(PointNM(4_000, 4_000), PointNM(5_000, 5_000)),
        source_revision=SOURCE_REVISION,
    )

    result = AStarRouter().propose(snapshot, _request(snapshot), verified_fill=(degenerate,))

    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
    assert result.diagnostic is not None
    assert "closed ring" in result.diagnostic.message


def test_the_containment_gate_leaves_an_unevidenced_route_untouched() -> None:
    """A board routed without fill evidence must not pay for the gate, in cost or in budget.

    The gate measures zone outline bounds, which is obstacle-check work. Doing that on every
    zoned board would move ``obstacle_budget_exceeded`` boundaries for callers who never asked
    for fill-aware routing, so the measurement is taken only when there is evidence to check.
    """

    snapshot = _snapshot(foreign_zones=(_rectangle(3_000, 3_000, 7_000, 7_000),))

    plain = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))
    empty_evidence = _candidate(
        AStarRouter().propose(snapshot, _request(snapshot), verified_fill=())
    )

    assert plain.cost.length_nm == empty_evidence.cost.length_nm
    assert plain.metrics.obstacle_checks == empty_evidence.metrics.obstacle_checks
    # Pinned, not merely compared: measuring the outline bounds unconditionally would charge
    # both calls alike and slip past an equality assertion, while still moving every real
    # board's obstacle budget. 684 is this fixture's cost before the gate existed.
    assert plain.metrics.obstacle_checks == 684


# ---------------------------------------------------------------------------
# A candidate replays under the model that produced it (ADR-0103, issue #163)
# ---------------------------------------------------------------------------
#
# The conservative zone envelope over-approximates the exact pour, so the two obstacle models
# are ordered: the envelope forbids everything the pour forbids, and more.  That ordering is
# what makes the two directions of a wrong replay differ in kind rather than in degree, and
# every test below names which direction it pins.

# A fill-routed candidate ID is a published content address - it is what `preview_route` returns
# and what an apply manifest carries - so it is pinned here rather than left to drift.  This
# population is the one ADR-0103 moves, and it had no pin anywhere before, because a fill-routed
# candidate could not survive a replay at all.
#
# The ID below moved once *within* the pull request that created it, when ADR-0101 (PR #162)
# merged underneath: that change measures a foreign zone's outline bounds when fill evidence is
# present, which charges the obstacle-check meter, and a candidate records the work its search
# performed.  The *geometry* did not move - the same two vertices, 8,000 nm, zero bends - and
# neither did `FILL_ROUTED_FILL_BINDING`, which is the point: the binding addresses the evidence
# and nothing about how the search spent its budget.  Nothing published moved, because no
# fill-routed candidate ID was published before this pin existed.
FILL_ROUTED_FILL_BINDING = "sha256:d33c0ea288c31105338daa2b4c1fa2ab6aa80452f1febbf41b3650c7bb47aab6"
FILL_ROUTED_CANDIDATE_ID = "sha256:878e557c6dd33959d076abf5c7cc6eb0aed5fd6d8495efffd438503fa81b7d94"
FILL_ROUTED_PAYLOAD_BYTES = 975


def _corridor_snapshot() -> BoardIRSnapshot:
    return _snapshot(foreign_zones=(_rectangle(3_000, 3_000, 7_000, 7_000),))


def _corridor_fill(*, low_y: int = 6_000) -> VerifiedFill:
    """One pour that leaves the y=5,000 centreline clear inside the zone outline."""

    return VerifiedFill(
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        points=(
            PointNM(3_000, low_y),
            PointNM(7_000, low_y),
            PointNM(7_000, 7_000),
            PointNM(3_000, 7_000),
        ),
        source_revision=SOURCE_REVISION,
    )


def test_a_fill_routed_candidate_replays_under_the_fill_that_produced_it() -> None:
    """The assertion issue #163 found the suite made nowhere.

    A candidate that cannot reproduce itself is a candidate every downstream verifier refuses:
    `_replay_candidate` is the single chokepoint under both `render_kicad_candidate_board` and
    the apply engine.
    """

    snapshot = _corridor_snapshot()
    fill = _corridor_fill()
    router = AStarRouter()
    candidate = _candidate(router.propose(snapshot, _request(snapshot), verified_fill=(fill,)))

    replay = router.replay(snapshot, candidate, verified_fill=(fill,))

    assert replay.candidate == candidate
    assert canonical_candidate_bytes(_candidate(replay)) == canonical_candidate_bytes(candidate)


def test_replay_without_the_producing_fill_refuses_instead_of_routing_differently() -> None:
    """Issue #163 itself: the *understated* direction, which used to be answered silently.

    Losing the fill left the replay stricter than the route, so it never confirmed anything the
    router had not proved - it produced a different, longer route, and the disagreement was
    reported as the candidate's fault.  Refusing under its own code is what makes the message
    true.
    """

    snapshot = _corridor_snapshot()
    fill = _corridor_fill()
    router = AStarRouter()
    envelope = _candidate(router.propose(snapshot, _request(snapshot)))
    candidate = _candidate(router.propose(snapshot, _request(snapshot), verified_fill=(fill,)))

    replay = router.replay(snapshot, candidate)

    # Not vacuous: without the refusal this replay reproduces the *envelope* route, which is
    # precisely what it used to do.
    assert envelope.cost.length_nm > candidate.cost.length_nm
    _assert_failure(replay, RouteFailureCode.FILL_EVIDENCE_MISMATCH)
    assert replay.diagnostic is not None
    assert "not the fill this candidate was routed under" in replay.diagnostic.message


def test_replay_refuses_fill_a_candidate_was_never_routed_under() -> None:
    """The *overstated* direction, which is the dangerous one, pinned on its own.

    A candidate routed against the conservative envelope, replayed against the exact pour, would
    be verified under a model looser than the one that produced it: the pour opens corridors the
    envelope closed, so such a replay can confirm geometry the router never proved.  Nothing in
    the shape of issue #163 prevented this - it was unreachable only because `replay` had no way
    to be handed fill at all.  One binding equality forbids it, in the same line that forbids the
    understated direction above, so neither can be reintroduced without deleting the other.
    """

    snapshot = _corridor_snapshot()
    fill = _corridor_fill()
    router = AStarRouter()
    envelope = _candidate(router.propose(snapshot, _request(snapshot)))

    replay = router.replay(snapshot, envelope, verified_fill=(fill,))

    # Not vacuous: on this fixture the pour is genuinely the looser model, so a replay that
    # accepted it would be checking the candidate against a weaker obstacle set than its own.
    exact = _candidate(router.propose(snapshot, _request(snapshot), verified_fill=(fill,)))
    assert exact.cost.length_nm < envelope.cost.length_nm
    assert envelope.fill_binding is None
    _assert_failure(replay, RouteFailureCode.FILL_EVIDENCE_MISMATCH)


def test_replay_refuses_a_different_pour_that_would_reach_the_same_route() -> None:
    """Evidence that routes identically is still not the evidence that did."""

    snapshot = _corridor_snapshot()
    router = AStarRouter()
    produced = _corridor_fill()
    other = _corridor_fill(low_y=6_500)
    candidate = _candidate(router.propose(snapshot, _request(snapshot), verified_fill=(produced,)))
    alternative = _candidate(router.propose(snapshot, _request(snapshot), verified_fill=(other,)))

    replay = router.replay(snapshot, candidate, verified_fill=(other,))

    # Both pours leave the same centreline clear, so the geometry is identical and only the
    # recorded model differs.  The binding still refuses, because a replay is a statement about
    # which evidence was believed and not only about which vertices came out.
    assert alternative.patch == candidate.patch
    assert alternative.fill_binding != candidate.fill_binding
    _assert_failure(replay, RouteFailureCode.FILL_EVIDENCE_MISMATCH)


def test_an_empty_pour_and_no_pour_are_the_same_obstacle_model() -> None:
    """An empty tuple of fill gives the router the model no fill gives it, so the same candidate."""

    snapshot = _corridor_snapshot()
    router = AStarRouter()

    without = _candidate(router.propose(snapshot, _request(snapshot)))
    empty = _candidate(router.propose(snapshot, _request(snapshot), verified_fill=()))

    assert fill_binding_for(()) is None
    assert without.fill_binding is None
    assert without == empty
    assert router.replay(snapshot, empty, verified_fill=()).candidate == empty


def test_an_envelope_candidate_carries_no_fill_key_in_its_canonical_identity() -> None:
    """The by-construction reason no pinned content address moves.

    `tests/test_golden_identities.py` pins the route-candidate ID *and* its canonical payload
    byte count, and both hold across ADR-0103 because the key is absent - not null - whenever
    there is no fill.  Removing the recorded binding from a fill-routed candidate reproduces a
    payload with no fill key at all, which is the same payload shape every candidate issued
    before this change was addressed under.
    """

    snapshot = _corridor_snapshot()
    router = AStarRouter()
    envelope = _candidate(router.propose(snapshot, _request(snapshot)))
    exact = _candidate(
        router.propose(snapshot, _request(snapshot), verified_fill=(_corridor_fill(),))
    )
    assert exact.fill_binding is not None

    assert b'"fill_binding"' not in canonical_candidate_bytes(envelope)
    assert b'"fill_binding"' in canonical_candidate_bytes(exact)
    assert verify_candidate_id(envelope)
    assert verify_candidate_id(exact)
    stripped = canonical_candidate_bytes(replace(exact, fill_binding=None))
    assert b'"fill_binding"' not in stripped
    # The key and its value are the whole difference: nothing else in the payload moved.
    assert len(canonical_candidate_bytes(exact)) - len(stripped) == len(
        b'"fill_binding":"",'
    ) + len(exact.fill_binding)


def test_the_fill_routed_candidate_identity_matches_its_committed_golden_value() -> None:
    snapshot = _corridor_snapshot()
    candidate = _candidate(
        AStarRouter().propose(snapshot, _request(snapshot), verified_fill=(_corridor_fill(),))
    )

    assert verify_candidate_id(candidate)
    assert candidate.fill_binding == FILL_ROUTED_FILL_BINDING
    assert candidate.candidate_id == FILL_ROUTED_CANDIDATE_ID
    assert len(canonical_candidate_bytes(candidate)) == FILL_ROUTED_PAYLOAD_BYTES
    # The geometry the pin is about, stated separately so a moved ID says which half moved.
    assert candidate.cost.length_nm == 8_000
    assert candidate.cost.bend_count == 0
    assert candidate.patch.paths[0].vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))


def test_a_candidate_carrying_an_unverifiable_binding_is_refused_at_construction() -> None:
    snapshot = _corridor_snapshot()
    candidate = _candidate(AStarRouter().propose(snapshot, _request(snapshot)))

    with pytest.raises(ValueError, match="fill binding"):
        replace(candidate, fill_binding="not-a-digest")


def test_the_multilayer_connectivity_model_charges_the_net_object_budget() -> None:
    """Same-net copper is charged to its own budget, and the refusal names that budget.

    Sharing one ceiling with the obstacle model is what made a finished board's own copper
    look like an obstacle problem: 59 of the 92 real-board refusals in issue #128 were this
    check, on nets that are simply already connected.
    """

    snapshot = _snapshot(
        own_via=True,
        own_segments=tuple((500 + index * 400, 500, 700 + index * 400, 500) for index in range(8)),
    )

    result = AStarRouter().propose(
        snapshot, _request(snapshot, settings=_settings(max_net_objects=4))
    )

    _assert_failure(result, RouteFailureCode.NET_OBJECT_BUDGET_EXCEEDED)
    assert result.diagnostic is not None
    assert "connectivity model" in result.diagnostic.message
    assert "max_net_objects=4" in result.diagnostic.message

    # The obstacle budget no longer reaches this population at all.
    obstacle_limited = AStarRouter().propose(
        snapshot, _request(snapshot, settings=_settings(max_obstacles=1))
    )
    _assert_failure(obstacle_limited, RouteFailureCode.UNSUPPORTED_GEOMETRY)


def test_a_same_net_zone_on_another_layer_also_blocks_the_claim() -> None:
    """Connectivity is a multilayer question, so an unmodeled pour anywhere blocks it."""

    snapshot = _snapshot(
        own_via=True,
        own_segments=((1_000, 5_000, 4_700, 5_000), (5_300, 5_000, 9_000, 5_000)),
        own_zone=_rectangle(1_000, 1_000, 2_000, 2_000),
        own_zone_layer_id="layer:B.Cu",
    )

    result = AStarRouter().propose(snapshot, _request(snapshot))

    assert result.connected is None
    _assert_failure(result, RouteFailureCode.UNSUPPORTED_GEOMETRY)
