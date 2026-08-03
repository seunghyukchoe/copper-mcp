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
    existing_copper: bool = False,
    layer_kind: str = "signal",
    length_rule: bool = False,
) -> BoardIRSnapshot:
    layer = Layer(id=LAYER_ID, name="F.Cu", index=0, kind=layer_kind)
    net = Net(id=NET_ID, name="AUDIO")
    net_class = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=100,
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
        copper_layers=(layer,),
        nets=(net, Net(id=OTHER_NET_ID, name="POWER")),
        constraints=ConstraintSet(
            net_classes=(net_class,),
            assignments=(
                NetClassAssignment(net_id=NET_ID, net_class_id=net_class.id),
                NetClassAssignment(net_id=OTHER_NET_ID, net_class_id=net_class.id),
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
