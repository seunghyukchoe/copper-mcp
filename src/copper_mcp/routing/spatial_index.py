"""Deterministic conservative spatial indexing for routing obstacle queries.

The router still decides legality with its exact integer predicates.  This module only narrows
the set of obstacle objects handed to those predicates.  Every indexed bound is an over-approx
imation, and a deterministic linear fallback keeps small or unusually large objects bounded.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isqrt
from typing import Generic, TypeAlias, TypeVar

_Bounds: TypeAlias = tuple[int, int, int, int]
_Cell: TypeAlias = tuple[int, int]
_Value = TypeVar("_Value")


def _bounds_intersect(first: _Bounds, second: _Bounds) -> bool:
    return (
        first[0] <= second[2]
        and second[0] <= first[2]
        and first[1] <= second[3]
        and second[1] <= first[3]
    )


def _cell_range(low: int, high: int, cell_size: int) -> range:
    return range(low // cell_size, high // cell_size + 1)


@dataclass(frozen=True, slots=True)
class SpatialIndexEntry(Generic[_Value]):
    """One conservatively bounded object in its canonical source order."""

    ordinal: int
    bounds: _Bounds
    value: _Value

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("spatial index ordinals must be non-negative integers")
        if (
            len(self.bounds) != 4
            or self.bounds[0] > self.bounds[2]
            or self.bounds[1] > self.bounds[3]
        ):
            raise ValueError("spatial index bounds must be an ordered rectangle")


class ConservativeSpatialIndex(Generic[_Value]):
    """A query-only uniform grid with a conservative linear fallback.

    The grid is bulk-built from immutable entries.  Cell occupancy is bounded; if a board has
    giant objects that would explode the bucket count, the index intentionally falls back to a
    canonical linear sequence.  A query always returns source-order values, independent of hash
    insertion order or the order in which cells are visited.
    """

    def __init__(
        self,
        entries: tuple[SpatialIndexEntry[_Value], ...],
        *,
        min_index_entries: int = 8,
        max_bucket_entries: int | None = None,
    ) -> None:
        if isinstance(min_index_entries, bool) or min_index_entries < 0:
            raise ValueError("min_index_entries must be a non-negative integer")
        if max_bucket_entries is not None and (
            isinstance(max_bucket_entries, bool) or max_bucket_entries < 1
        ):
            raise ValueError("max_bucket_entries must be a positive integer")
        ordered = tuple(sorted(entries, key=lambda entry: entry.ordinal))
        if tuple(entry.ordinal for entry in ordered) != tuple(range(len(ordered))):
            raise ValueError("spatial index ordinals must be contiguous and canonical")
        self._entries = ordered
        self._buckets: dict[_Cell, tuple[int, ...]] | None = None
        self._cell_size_nm = 0
        if len(ordered) < min_index_entries or not ordered:
            return

        min_x = min(entry.bounds[0] for entry in ordered)
        min_y = min(entry.bounds[1] for entry in ordered)
        max_x = max(entry.bounds[2] for entry in ordered)
        max_y = max(entry.bounds[3] for entry in ordered)
        span = max(max_x - min_x, max_y - min_y, 1)
        target_cells = max(1, min(256, len(ordered) * 4))
        grid_side = max(1, isqrt(target_cells))
        cell_size = max(1, (span + grid_side - 1) // grid_side)
        bucket_limit = max_bucket_entries or max(1_024, len(ordered) * 64)
        buckets: dict[_Cell, list[int]] = {}
        total_entries = 0
        for entry in ordered:
            cells = [
                (cell_x, cell_y)
                for cell_x in _cell_range(entry.bounds[0], entry.bounds[2], cell_size)
                for cell_y in _cell_range(entry.bounds[1], entry.bounds[3], cell_size)
            ]
            total_entries += len(cells)
            if total_entries > bucket_limit:
                return
            for cell in cells:
                buckets.setdefault(cell, []).append(entry.ordinal)
        self._buckets = {
            cell: tuple(sorted(ordinals)) for cell, ordinals in sorted(buckets.items())
        }
        self._cell_size_nm = cell_size

    @property
    def indexed(self) -> bool:
        """Whether this instance uses buckets instead of the bounded linear fallback."""

        return self._buckets is not None

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def bucket_count(self) -> int:
        return len(self._buckets or {})

    @property
    def cell_size_nm(self) -> int:
        return self._cell_size_nm

    def query(self, bounds: _Bounds) -> tuple[_Value, ...]:
        """Return every possibly intersecting object in canonical source order.

        The query bounds are allowed to be empty or reversed; such a query has no candidates.
        Bucket filtering is only an acceleration: the final bounds intersection keeps the result
        conservative and exact with respect to the supplied over-approximate entry bounds.
        """

        if bounds[0] > bounds[2] or bounds[1] > bounds[3] or not self._entries:
            return ()
        if self._buckets is None:
            return tuple(entry.value for entry in self._entries)
        assert self._cell_size_nm > 0
        ordinals: set[int] = set()
        for cell_x in _cell_range(bounds[0], bounds[2], self._cell_size_nm):
            for cell_y in _cell_range(bounds[1], bounds[3], self._cell_size_nm):
                ordinals.update(self._buckets.get((cell_x, cell_y), ()))
        return tuple(
            self._entries[ordinal].value
            for ordinal in sorted(ordinals)
            if _bounds_intersect(self._entries[ordinal].bounds, bounds)
        )
