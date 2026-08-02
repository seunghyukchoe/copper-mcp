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
    segments = (
        (
            Segment(
                id="segment:existing",
                net_id=NET_ID,
                layer_id=LAYER_ID,
                start=PointNM(*start),
                end=PointNM(start[0] + 1_000, start[1]),
                width_nm=200,
            ),
        )
        if existing_copper
        else ()
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
        nets=(net,),
        constraints=ConstraintSet(
            net_classes=(net_class,),
            assignments=(NetClassAssignment(net_id=NET_ID, net_class_id=net_class.id),),
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
    extra_pad = _snapshot(extra_pad=True)
    _assert_failure(
        router.propose(extra_pad, _request(extra_pad)), RouteFailureCode.UNSUPPORTED_GEOMETRY
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
