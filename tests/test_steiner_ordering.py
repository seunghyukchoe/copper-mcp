from __future__ import annotations

import pytest

from copper_mcp.routing.steiner_ordering import batched_one_steiner_order


def _components() -> tuple[tuple[tuple[int, int, int, int], ...], ...]:
    # Four isolated terminals chosen so the median-point guide changes the first trunk and
    # reduces the resulting obstacle-free A* tree on the matching KiCad fixture.
    return (
        ((900, 7_900, 1_100, 8_100),),
        ((4_900, 2_900, 5_100, 3_100),),
        ((900, 900, 1_100, 1_100),),
        ((3_900, 5_900, 4_100, 6_100),),
    )


def test_batched_one_steiner_order_is_deterministic_and_spans_components() -> None:
    checks = 0

    def checkpoint() -> None:
        nonlocal checks
        checks += 1

    first = batched_one_steiner_order(_components(), checkpoint=checkpoint)
    second = batched_one_steiner_order(_components(), checkpoint=lambda: None)

    assert first == second
    assert len(first) == 3
    assert checks > 0
    assert {index for edge in first for index in edge} == {0, 1, 2, 3}


def test_batched_one_steiner_order_is_bounded_by_the_supplied_checkpoint() -> None:
    calls = 0

    def checkpoint() -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RuntimeError("caller budget")

    with pytest.raises(RuntimeError, match="caller budget"):
        batched_one_steiner_order(_components(), checkpoint=checkpoint)


@pytest.mark.parametrize("components", [(), (((0, 0, 1, 1),),), (((0, 0, 1, 1),), ())])
def test_batched_one_steiner_order_rejects_malformed_components(components: object) -> None:
    with pytest.raises(ValueError):
        batched_one_steiner_order(components, checkpoint=lambda: None)  # type: ignore[arg-type]
