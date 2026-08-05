"""Bounded, clean-room topology ordering for multi-pin rectilinear routing.

The router remains the authority for geometry.  This module only chooses a deterministic order
in which the existing component-to-component A* legs are requested.  Its score is a conservative
*guide*, not a Steiner certificate: for each possible merge it estimates the best improvement a
median-point one-Steiner star could offer over two edges of the current component envelopes.  The
actual legs are still routed against the full obstacle model, and a candidate records this policy
explicitly for replay.

This is intentionally independent of FLUTE lookup tables.  FLUTE is optimal for low-degree
point nets, while this board router has obstacle-bearing component geometry; importing a table
would also introduce a dependency/licensing decision.  The implementation is the bounded
ordering seam described by the routing ADR and can later be replaced by a reviewed FLUTE or
learned policy without changing the candidate contract.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import combinations
from typing import TypeAlias

Rect: TypeAlias = tuple[int, int, int, int]
Checkpoint: TypeAlias = Callable[[], None]


def _bounds(cores: Sequence[Rect]) -> Rect:
    if not cores:
        raise ValueError("a routing component must contain geometry")
    return (
        min(core[0] for core in cores),
        min(core[1] for core in cores),
        max(core[2] for core in cores),
        max(core[3] for core in cores),
    )


def _gap(first: Rect, second: Rect) -> int:
    """Return the exact Manhattan gap between two closed rectangles."""

    return max(second[0] - first[2], first[0] - second[2], 0) + max(
        second[1] - first[3], first[1] - second[3], 0
    )


def _point_gap(point: tuple[int, int], rectangle: Rect) -> int:
    """Return Manhattan distance from a point to a closed rectangle."""

    return max(rectangle[0] - point[0], point[0] - rectangle[2], 0) + max(
        rectangle[1] - point[1], point[1] - rectangle[3], 0
    )


def _median(values: tuple[int, int, int]) -> int:
    """Return the middle of three integers without floating-point arithmetic."""

    return sorted(values)[1]


def _one_steiner_savings(rectangles: tuple[Rect, Rect, Rect]) -> int:
    """Estimate the non-negative saving of a median-point three-way merge.

    The candidate point is the coordinate-wise median of rectangle centres.  Connecting that
    point to each closed envelope gives a lower-cost guide; no geometry is emitted from this
    estimate.  The comparison is against the two cheapest pairwise gaps, which is the local
    three-terminal MST cost.
    """

    centres = tuple(
        ((rectangle[0] + rectangle[2]) // 2, (rectangle[1] + rectangle[3]) // 2)
        for rectangle in rectangles
    )
    star_point = (
        _median((centres[0][0], centres[1][0], centres[2][0])),
        _median((centres[0][1], centres[1][1], centres[2][1])),
    )
    star_cost = sum(_point_gap(star_point, rectangle) for rectangle in rectangles)
    pair_costs = sorted(_gap(left, right) for left, right in combinations(rectangles, 2))
    return max(0, pair_costs[0] + pair_costs[1] - star_cost)


def batched_one_steiner_order(
    components: tuple[tuple[Rect, ...], ...],
    *,
    checkpoint: Checkpoint,
) -> tuple[tuple[int, int], ...]:
    """Return a deterministic sequence of component indices to merge.

    Active groups are represented by the union of their exact core rectangles.  At each round,
    every pair receives a score consisting of its direct gap minus the largest estimated local
    one-Steiner saving with a third group.  The lowest score wins, with direct gap and original
    indices as stable tie-breakers.  The number of geometric comparisons is bounded by
    ``O(n^3)`` and every comparison is charged through ``checkpoint`` before it can consume
    caller work.
    """

    if len(components) < 2 or any(not group for group in components):
        raise ValueError("at least two non-empty routing components are required")

    groups: dict[int, tuple[Rect, ...]] = {
        index: tuple(group) for index, group in enumerate(components)
    }
    active = list(groups)
    order: list[tuple[int, int]] = []
    while len(active) > 1:
        choices: list[tuple[int, int, int, int]] = []
        for left, right in combinations(active, 2):
            checkpoint()
            left_bounds = _bounds(groups[left])
            right_bounds = _bounds(groups[right])
            direct_gap = _gap(left_bounds, right_bounds)
            best_saving = 0
            for third in active:
                if third in {left, right}:
                    continue
                checkpoint()
                saving = _one_steiner_savings((left_bounds, right_bounds, _bounds(groups[third])))
                best_saving = max(best_saving, saving)
            # The first field is the guide score; the next fields make ties independent of set
            # iteration and preserve the same output across Python versions.
            choices.append((max(0, direct_gap - best_saving), direct_gap, left, right))
        _, _, left, right = min(choices)
        winner, loser = min(left, right), max(left, right)
        order.append((left, right))
        groups[winner] = groups[winner] + groups[loser]
        del groups[loser]
        active.remove(loser)
    return tuple(order)


__all__ = ["Rect", "batched_one_steiner_order"]
