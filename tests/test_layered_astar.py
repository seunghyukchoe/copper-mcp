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


def test_three_layer_stack_completes_a_case_the_two_layer_restriction_cannot() -> None:
    """Only layer 2 has a crossing; the fixed two-layer slice must refuse it."""

    common = {
        "board_revision": REVISION,
        "expected_revision": REVISION,
        "bounds": (0, 0, 4, 4),
        "start": LayeredPoint(0, 2, 0),
        "goal": LayeredPoint(4, 2, 0),
        "obstacles": (
            LayeredObstacle(0, 1, 0, 3, 4),
            LayeredObstacle(1, 1, 0, 3, 4),
        ),
        "settings": LayeredAStarSettings(via_cost=2, max_vias=2),
    }

    two_layer = route_layered(LayeredAStarRequest(**common, layers=(0, 1)))
    three_layer = route_layered(LayeredAStarRequest(**common, layers=(0, 1, 2)))

    assert two_layer.diagnostic is not None
    assert two_layer.diagnostic.code is LayeredFailureCode.NO_PATH
    assert three_layer.ok
    assert three_layer.path is not None
    assert any(step.layer == 2 for step in three_layer.path)
    assert three_layer.metrics.via_steps == 2
    assert route_layered(LayeredAStarRequest(**common, layers=(0, 1, 2))) == three_layer


def test_stack_and_via_budgets_fail_closed() -> None:
    over_stack = route_layered(
        LayeredAStarRequest(
            board_revision=REVISION,
            bounds=(0, 0, 0, 0),
            start=LayeredPoint(0, 0, 0),
            goal=LayeredPoint(0, 0, 1),
            layers=tuple(range(9)),
        )
    )
    via_limited = route_layered(
        LayeredAStarRequest(
            board_revision=REVISION,
            bounds=(0, 0, 0, 0),
            start=LayeredPoint(0, 0, 0),
            goal=LayeredPoint(0, 0, 2),
            layers=(0, 1, 2),
            settings=LayeredAStarSettings(max_vias=0),
        )
    )

    assert over_stack.diagnostic is not None
    assert over_stack.diagnostic.code is LayeredFailureCode.INVALID_REQUEST
    assert via_limited.diagnostic is not None
    assert via_limited.diagnostic.code is LayeredFailureCode.NO_PATH


def _review_capped_via_request(
    *, settings: LayeredAStarSettings | None = None
) -> LayeredAStarRequest:
    """Return the three-layer ``max_vias=3`` coordinate-dominance regression request."""

    chosen_settings = settings or LayeredAStarSettings(via_cost=1, max_vias=3)
    return LayeredAStarRequest(
        board_revision=REVISION,
        expected_revision=REVISION,
        bounds=(0, 0, 4, 3),
        start=LayeredPoint(0, 2, 0),
        goal=LayeredPoint(4, 2, 2),
        obstacles=tuple(
            LayeredObstacle(layer, x, y, x, y)
            for x, y, layer in (
                (0, 2, 2),
                (0, 3, 0),
                (1, 0, 0),
                (1, 1, 0),
                (1, 2, 0),
                (1, 3, 1),
                (2, 0, 2),
                (2, 1, 1),
                (2, 1, 2),
                (2, 2, 1),
                (2, 3, 2),
                (3, 0, 1),
                (3, 2, 2),
                (4, 1, 2),
            )
        ),
        layers=(0, 1, 2),
        settings=chosen_settings,
    )


def test_three_layer_via_cap_keeps_remaining_budget_in_the_search_state() -> None:
    """A cheaper arrival at a choke point uses all vias; the legal arrival does not.

    Layer 1 is intentionally available despite the returned path using the direct 0↔2 full-stack
    transitions, so the case exercises the ordered three-layer search rather than a two-layer
    special case.
    """

    result = route_layered(_review_capped_via_request())

    assert result.ok
    assert result.path is not None
    assert [(step.x, step.y, step.layer) for step in result.path] == [
        (0, 2, 0),
        (0, 1, 0),
        (0, 1, 2),
        (1, 1, 2),
        (1, 2, 2),
        (2, 2, 2),
        (2, 2, 0),
        (3, 2, 0),
        (4, 2, 0),
        (4, 2, 2),
    ]
    assert result.metrics.via_steps == 3


def test_capped_search_charges_each_augmented_state_to_the_node_budget() -> None:
    """The non-dominated finite-cap states in the review case consume the node budget."""

    result = route_layered(
        _review_capped_via_request(
            settings=LayeredAStarSettings(via_cost=1, max_vias=3, max_nodes=45)
        )
    )

    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredFailureCode.GRID_BUDGET_EXCEEDED
    assert result.metrics.discovered_nodes == 45


def _alternating_via_request(
    via_count: int,
    *,
    layers: tuple[int, ...],
    settings: LayeredAStarSettings = DEFAULT_SETTINGS,
) -> LayeredAStarRequest:
    """Force one 0↔1 transition per two cells; any extra layer is blocked outright."""

    obstacles = [
        LayeredObstacle(index % 2, index * 2 + 1, 0, index * 2 + 1, 0) for index in range(via_count)
    ]
    if len(layers) > 2:
        obstacles.extend(LayeredObstacle(2, x, 0, x, 0) for x in range(via_count * 2 + 1))
    return LayeredAStarRequest(
        board_revision=REVISION,
        expected_revision=REVISION,
        bounds=(0, 0, via_count * 2, 0),
        start=LayeredPoint(0, 0, 0),
        goal=LayeredPoint(via_count * 2, 0, via_count % 2),
        obstacles=tuple(obstacles),
        layers=layers,
        settings=settings,
    )


def test_omitted_two_layer_via_budget_preserves_legacy_long_route_behavior() -> None:
    legacy = route_layered(_alternating_via_request(65, layers=(0, 1)))
    explicit_boundary = route_layered(
        _alternating_via_request(65, layers=(0, 1), settings=LayeredAStarSettings(max_vias=65))
    )
    one_over = route_layered(
        _alternating_via_request(66, layers=(0, 1), settings=LayeredAStarSettings(max_vias=65))
    )
    generalized = route_layered(_alternating_via_request(65, layers=(0, 1, 2)))

    assert legacy.ok
    assert legacy.metrics.via_steps == 65
    assert explicit_boundary.ok
    assert explicit_boundary.metrics.via_steps == 65
    assert one_over.diagnostic is not None
    assert one_over.diagnostic.code is LayeredFailureCode.NO_PATH
    assert generalized.diagnostic is not None
    assert generalized.diagnostic.code is LayeredFailureCode.NO_PATH


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
    malformed_board_revision = route_layered(
        replace(_request(), board_revision="", expected_revision="different")
    )

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
    assert malformed_board_revision.diagnostic is not None
    assert malformed_board_revision.diagnostic.code is LayeredFailureCode.INVALID_REQUEST
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


def test_malformed_obstacle_is_invalid_before_obstacle_budget_is_reported() -> None:
    result = route_layered(
        _request(
            obstacles=(
                LayeredObstacle(0, 4, 4, 4, 4),
                object(),  # type: ignore[tuple-item]
            ),
            settings=LayeredAStarSettings(max_obstacles=1),
        )
    )

    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredFailureCode.INVALID_REQUEST
