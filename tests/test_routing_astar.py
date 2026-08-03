from __future__ import annotations

from dataclasses import replace

import pytest

from copper_mcp.board_ir import (
    BoardIRSnapshot,
    ConstraintSet,
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
from copper_mcp.routing import (
    AStarRouter,
    AStarSettings,
    RouteCandidate,
    RouteDiagnostic,
    RouteFailureCode,
    RoutePatch,
    RouteRequest,
    RouteResult,
    canonical_candidate_bytes,
    verify_candidate_id,
)
from copper_mcp.routing.oracle import DijkstraResult, run_dijkstra_oracle

SOURCE_REVISION = f"sha256:{'a' * 64}"
OTHER_REVISION = f"sha256:{'b' * 64}"
LAYER_ID = "layer:F.Cu"
OTHER_NET_ID = "net:power"
NET_ID = "net:audio"


def _ring(coordinates: tuple[tuple[int, int], ...]) -> Ring:
    return Ring(tuple(PointNM(x, y) for x, y in coordinates))


def _rectangle(min_x: int, min_y: int, max_x: int, max_y: int) -> Ring:
    return _ring(((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)))


def _pad(identifier: str, center: tuple[int, int], *, net_id: str | None = NET_ID) -> Pad:
    return Pad(
        id=identifier,
        net_id=net_id,
        center=PointNM(*center),
        rotation_udeg=0,
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
    include_end: bool = True,
    third_target: bool = False,
    extra_pad: bool = False,
    blocking_pad: tuple[int, int] | None = None,
    blocking_pad_rotation_udeg: int = 0,
    foreign_segment: tuple[int, int, int, int] | None = None,
    foreign_via: tuple[int, int] | None = None,
    own_via: bool = False,
    foreign_zones: tuple[Ring, ...] = (),
    foreign_zone_layer_id: str = LAYER_ID,
    own_zone: Ring | None = None,
    route_clearance_nm: int = 100,
    other_clearance_nm: int = 100,
    zone_clearance_nm: int = 100,
    existing_copper: bool = False,
    layer_kind: str = "signal",
    length_rule: bool = False,
) -> BoardIRSnapshot:
    layer = Layer(id=LAYER_ID, name="F.Cu", index=0, kind=layer_kind)
    # Vias span two layers, so the back layer only exists when a fixture needs one.
    copper_layers: tuple[Layer, ...] = (layer,)
    if foreign_via is not None or own_via or foreign_zone_layer_id != LAYER_ID:
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
    pads = [_pad("pad:01", start)]
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
        pads=tuple(pads),
        segments=segments,
        vias=vias,
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
                    layer_id=LAYER_ID,
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
    assert result.candidate is None
    assert result.diagnostic is not None
    assert result.diagnostic.code is code


def test_straight_route_is_exact_replayable_and_content_addressed() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    router = AStarRouter()

    first = _candidate(router.propose(snapshot, request))
    second = _candidate(router.propose(snapshot, request))

    assert router.name == "orthogonal-a-star-v1"
    assert first.router_version == "astar-grid/0.2.0"
    assert first == second
    assert first.patch.vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))
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

    assert candidate.patch.vertices == (
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

    assert exact_route.patch.vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))
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
    three_pads = _snapshot(third_target=True)
    _assert_failure(
        router.propose(three_pads, _request(three_pads)), RouteFailureCode.INVALID_TWO_PIN_NET
    )

    off_grid = _snapshot(end=(9_001, 5_000))
    _assert_failure(router.propose(off_grid, _request(off_grid)), RouteFailureCode.OFF_GRID)

    triangle = _snapshot(outline=_ring(((0, 0), (10_000, 0), (0, 10_000))))
    _assert_failure(
        router.propose(triangle, _request(triangle)), RouteFailureCode.UNSUPPORTED_GEOMETRY
    )
    existing = _snapshot(existing_copper=True)
    _assert_failure(
        router.propose(existing, _request(existing)), RouteFailureCode.UNSUPPORTED_GEOMETRY
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
        {"max_obstacles": 4_097},
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
            vertices=(PointNM(0, 0), PointNM(1, 1)),
        )
    with pytest.raises(ValueError, match="collinear"):
        RoutePatch(
            net_id=NET_ID,
            layer_id=LAYER_ID,
            width_nm=200,
            vertices=(PointNM(0, 0), PointNM(1, 0), PointNM(2, 0)),
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
    offboard = tuple(
        (-10_000 - index * 10, -10_000, -9_995 - index * 10, -9_995) for index in range(130)
    )
    snapshot = _snapshot(keepouts=offboard)

    count_limited = _request(snapshot, settings=_settings(max_obstacles=2))
    _assert_failure(
        router.propose(snapshot, count_limited),
        RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
    )

    one_obstacle = _snapshot(keepouts=(offboard[0],))
    relation_limited = _request(
        one_obstacle,
        settings=_settings(max_obstacles=2, max_obstacle_checks=1),
    )
    relation_result = router.propose(one_obstacle, relation_limited)
    _assert_failure(relation_result, RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED)
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
        not (4_500 < point.x < 5_500 and 3_900 < point.y < 6_100) for point in detour.patch.vertices
    )


def test_foreign_segments_become_exact_obstacles() -> None:
    router = AStarRouter()
    blocked = _snapshot(foreign_segment=(5_000, 3_000, 5_000, 7_000))

    detour = _candidate(router.propose(blocked, _request(blocked)))

    assert detour.cost.bend_count > 0
    assert all(
        not (4_800 < point.x < 5_200 and 2_800 < point.y < 7_200) for point in detour.patch.vertices
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

    diagonal = _snapshot(foreign_segment=(4_000, 4_000, 6_000, 6_000))
    _assert_failure(
        router.propose(diagonal, _request(diagonal)), RouteFailureCode.UNSUPPORTED_GEOMETRY
    )

    partially_routed = _snapshot(existing_copper=True)
    _assert_failure(
        router.propose(partially_routed, _request(partially_routed)),
        RouteFailureCode.UNSUPPORTED_GEOMETRY,
    )


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
        not (4_500 < point.x < 5_500 and 4_500 < point.y < 5_500) for point in detour.patch.vertices
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
    assert first.patch.vertices == (
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

    assert candidate.patch.vertices == (PointNM(5_000, 5_000), PointNM(5_000, 9_000))
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

    assert exact_route.patch.vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))
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

    assert exact_route.patch.vertices == (PointNM(5_000, 5_000), PointNM(5_000, 1_000))
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

    _assert_failure(relation_limited, RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED)
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

    assert other_layer_route.patch.vertices == clear_route.patch.vertices
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
