from __future__ import annotations

from dataclasses import replace

from copper_mcp.routing.layered_astar import (
    LayeredAStarRequest,
    LayeredAStarSettings,
    LayeredFailureCode,
    LayeredObstacle,
    LayeredPoint,
    LayeredStep,
    route_layered,
)

REVISION = "board-revision-a"
DEFAULT_START = LayeredPoint(0, 1, 0)
DEFAULT_GOAL = LayeredPoint(4, 1, 0)
DEFAULT_SETTINGS = LayeredAStarSettings()


def _request(
    *,
    start: LayeredPoint = DEFAULT_START,
    goal: LayeredPoint = DEFAULT_GOAL,
    bounds: tuple[int, int, int, int] = (0, 0, 4, 4),
    obstacles: tuple[LayeredObstacle, ...] = (),
    via_obstacles: tuple[LayeredObstacle, ...] = (),
    settings: LayeredAStarSettings = DEFAULT_SETTINGS,
    expected_revision: str | None = REVISION,
) -> LayeredAStarRequest:
    return LayeredAStarRequest(
        board_revision=REVISION,
        expected_revision=expected_revision,
        bounds=bounds,
        start=start,
        goal=goal,
        obstacles=obstacles,
        via_obstacles=via_obstacles,
        settings=settings,
    )


def test_single_layer_block_requires_two_explicit_vias() -> None:
    request = _request(
        obstacles=(LayeredObstacle(0, 1, 0, 3, 4),),
        settings=LayeredAStarSettings(via_cost=2),
    )

    result = route_layered(request)

    assert result.ok
    assert result.path is not None
    assert result.path[0] == LayeredStep(0, 1, 0, "start")
    assert (result.path[-1].x, result.path[-1].y, result.path[-1].layer) == (4, 1, 0)
    assert result.metrics.via_steps == 2
    assert result.metrics.path_cost == 8  # four moves plus two vias
    assert [step.kind for step in result.path].count("via") == 2


def test_via_cost_prefers_short_other_layer_only_when_cheap() -> None:
    obstacle = LayeredObstacle(0, 1, 1, 3, 3)
    start = LayeredPoint(0, 2, 0)
    goal = LayeredPoint(4, 2, 0)
    cheap = route_layered(
        _request(
            start=start,
            goal=goal,
            obstacles=(obstacle,),
            settings=LayeredAStarSettings(via_cost=1),
        )
    )
    expensive = route_layered(
        _request(
            start=start,
            goal=goal,
            obstacles=(obstacle,),
            settings=LayeredAStarSettings(via_cost=10),
        )
    )

    assert cheap.ok and expensive.ok
    assert cheap.metrics.via_steps == 2
    assert cheap.metrics.path_cost == 6  # direct layer-1 crossing beats the layer-0 detour
    assert expensive.metrics.via_steps == 0
    assert expensive.metrics.path_cost == 8  # top-row detour on layer 0


def test_replay_is_byte_for_byte_deterministic() -> None:
    request = _request(
        obstacles=(LayeredObstacle(0, 1, 1, 3, 3), LayeredObstacle(1, 2, 0, 2, 2)),
        settings=LayeredAStarSettings(via_cost=3),
    )

    first = route_layered(request)
    second = route_layered(request)

    assert first == second


def test_obstacles_are_scoped_to_their_declared_layer() -> None:
    blocked = route_layered(
        _request(
            obstacles=(
                LayeredObstacle(0, 1, 0, 3, 4),
                LayeredObstacle(1, 1, 0, 3, 4),
            ),
            settings=LayeredAStarSettings(via_cost=100),
        )
    )
    unblocked = route_layered(
        _request(
            start=LayeredPoint(0, 1, 1),
            goal=LayeredPoint(4, 1, 1),
            obstacles=(LayeredObstacle(0, 1, 0, 3, 4),),
        )
    )

    assert blocked.diagnostic is not None
    assert blocked.diagnostic.code is LayeredFailureCode.NO_PATH
    assert unblocked.ok
    assert unblocked.metrics.via_steps == 0


def test_via_only_obstacles_block_transitions_without_blocking_tracks() -> None:
    via_keepout = LayeredObstacle(0, 0, 1, 0, 1)
    result = route_layered(
        _request(
            start=LayeredPoint(0, 1, 0),
            goal=LayeredPoint(4, 1, 1),
            via_obstacles=(via_keepout,),
        )
    )

    assert result.ok
    assert result.path is not None
    assert result.metrics.via_steps == 1
    assert all(not (step.kind == "via" and step.x == 0 and step.y == 1) for step in result.path)


def test_stale_invalid_budget_and_cancelled_requests_fail_closed() -> None:
    stale = route_layered(_request(expected_revision="different"))
    invalid = route_layered(
        replace(_request(), bounds=(3, 0, 0, 4)),
    )
    budget = route_layered(
        _request(settings=LayeredAStarSettings(max_nodes=1)),
    )
    cancelled = route_layered(_request(), cancelled=lambda: True)
    malformed_revision = route_layered(
        replace(_request(), expected_revision=123),  # type: ignore[arg-type]
    )
    oversized_revision = route_layered(replace(_request(), expected_revision="x" * 257))

    assert stale.diagnostic is not None
    assert stale.diagnostic.code is LayeredFailureCode.STALE_REVISION
    assert invalid.diagnostic is not None
    assert invalid.diagnostic.code is LayeredFailureCode.INVALID_REQUEST
    assert budget.diagnostic is not None
    assert budget.diagnostic.code is LayeredFailureCode.GRID_BUDGET_EXCEEDED
    assert cancelled.diagnostic is not None
    assert cancelled.diagnostic.code is LayeredFailureCode.CANCELLED
    assert malformed_revision.diagnostic is not None
    assert malformed_revision.diagnostic.code is LayeredFailureCode.INVALID_REQUEST
    assert oversized_revision.diagnostic is not None
    assert oversized_revision.diagnostic.code is LayeredFailureCode.INVALID_REQUEST
    assert not stale.ok and not invalid.ok and not budget.ok and not cancelled.ok


def test_expansion_and_obstacle_budgets_are_distinct() -> None:
    expansion = route_layered(_request(settings=LayeredAStarSettings(max_expansions=1)))
    obstacle = route_layered(
        _request(
            obstacles=(LayeredObstacle(0, 4, 4, 4, 4),),
            settings=LayeredAStarSettings(max_obstacle_checks=1),
        )
    )

    assert expansion.diagnostic is not None
    assert expansion.diagnostic.code is LayeredFailureCode.SEARCH_BUDGET_EXCEEDED
    assert obstacle.diagnostic is not None
    assert obstacle.diagnostic.code is LayeredFailureCode.OBSTACLE_BUDGET_EXCEEDED


def test_terminals_obey_obstacles_including_zero_length_routes() -> None:
    obstacle = LayeredObstacle(0, 0, 1, 0, 1)
    blocked_start = route_layered(_request(obstacles=(obstacle,)))
    blocked_goal = route_layered(
        _request(
            start=LayeredPoint(0, 0, 0),
            goal=LayeredPoint(0, 1, 0),
            obstacles=(obstacle,),
        )
    )
    blocked_same_cell = route_layered(
        _request(
            start=LayeredPoint(0, 1, 0),
            goal=LayeredPoint(0, 1, 0),
            obstacles=(obstacle,),
        )
    )

    assert blocked_start.diagnostic is not None
    assert blocked_start.diagnostic.code is LayeredFailureCode.NO_PATH
    assert blocked_goal.diagnostic is not None
    assert blocked_goal.diagnostic.code is LayeredFailureCode.NO_PATH
    assert blocked_same_cell.diagnostic is not None
    assert blocked_same_cell.diagnostic.code is LayeredFailureCode.NO_PATH


def test_resource_limits_reject_unbounded_obstacle_scans() -> None:
    oversized = route_layered(_request(settings=LayeredAStarSettings(max_obstacles=4_097)))

    assert oversized.diagnostic is not None
    assert oversized.diagnostic.code is LayeredFailureCode.INVALID_REQUEST


def test_obstacle_count_exhaustion_has_a_typed_budget_diagnostic() -> None:
    result = route_layered(
        _request(
            obstacles=(
                LayeredObstacle(0, 4, 4, 4, 4),
                LayeredObstacle(1, 4, 4, 4, 4),
            ),
            settings=LayeredAStarSettings(max_obstacles=1),
        )
    )

    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredFailureCode.OBSTACLE_BUDGET_EXCEEDED
