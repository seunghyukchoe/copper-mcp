"""Deterministic conservative spatial indexing for routing obstacle queries.

The router still decides legality with its exact integer predicates.  This module only narrows
the set of obstacle objects handed to those predicates.  Every indexed bound is an over-approx
imation, and a deterministic linear fallback keeps small or unusually large objects bounded.

Two structures live here and they are deliberately different shapes:

* :class:`ConservativeSpatialIndex` is **immutable and query-only**.  ADR-0051 built it for one
  A* search, where the obstacle set is fixed for the search's lifetime and mutating buckets
  mid-expansion would make candidate ordering depend on when a bucket moved.
* :class:`IncrementalSpatialIndex` is **mutable between passes**.  ADR-0078 built it for the
  negotiated coordinator, where a rip-up/reroute pass changes only the nets it ripped up and
  rebuilding the whole structure discards the retention the rip-up rule just established.

Both are uniform grids with a conservative superset contract, because two indexes with different
failure directions in one router would be a correctness hazard rather than a feature.
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


def _cell_span(bounds: _Bounds, cell_size: int) -> int:
    """Return how many grid cells one rectangle occupies, without enumerating them."""

    columns = bounds[2] // cell_size - bounds[0] // cell_size + 1
    rows = bounds[3] // cell_size - bounds[1] // cell_size + 1
    return columns * rows


def _checked_bounds(bounds: _Bounds) -> _Bounds:
    if len(bounds) != 4 or any(
        isinstance(value, bool) or not isinstance(value, int) for value in bounds
    ):
        raise ValueError("spatial index bounds must be four exact integers")
    if bounds[0] > bounds[2] or bounds[1] > bounds[3]:
        raise ValueError("spatial index bounds must be an ordered rectangle")
    return bounds


def inflate_bounds(bounds: _Bounds, margin_nm: int) -> _Bounds:
    """Return the exact integer rectangle grown by a non-negative margin on every side."""

    if isinstance(margin_nm, bool) or not isinstance(margin_nm, int) or margin_nm < 0:
        raise ValueError("a spatial margin must be a non-negative integer")
    checked = _checked_bounds(bounds)
    return (
        checked[0] - margin_nm,
        checked[1] - margin_nm,
        checked[2] + margin_nm,
        checked[3] + margin_nm,
    )


def bounds_intersect(first: _Bounds, second: _Bounds) -> bool:
    """Return whether two closed integer rectangles share at least one point.

    This is the exact predicate a caller uses to decide membership after an index narrowed the
    candidates.  Touching rectangles intersect: a rectangle that only shares an edge with an
    obstacle envelope is still in contact with it, and treating contact as separation is the one
    direction this repository's geometry is never allowed to round.
    """

    return _bounds_intersect(_checked_bounds(first), _checked_bounds(second))


class SpatialIndexCapacityError(ValueError):
    """A bounded incremental index refused work that would exceed its declared capacity."""


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

        values, _candidates_examined = self.query_with_stats(bounds)
        return values

    def query_with_stats(self, bounds: _Bounds) -> tuple[tuple[_Value, ...], int]:
        """Return query values and the number of candidate ordinals examined.

        The second value counts entries handed to the exact bounds-intersection predicate. It is
        intended for differential benchmarks and diagnostics; routing correctness continues to
        use :meth:`query`, and the exact predicate remains authoritative.
        """

        if bounds[0] > bounds[2] or bounds[1] > bounds[3] or not self._entries:
            return (), 0
        if self._buckets is None:
            # The linear fallback deliberately returns every immutable entry; the router's
            # exact obstacle predicate performs the authoritative filtering and metrics retain
            # the full candidate count for an apples-to-apples work comparison.
            return tuple(entry.value for entry in self._entries), len(self._entries)
        assert self._cell_size_nm > 0
        ordinals: set[int] = set()
        for cell_x in _cell_range(bounds[0], bounds[2], self._cell_size_nm):
            for cell_y in _cell_range(bounds[1], bounds[3], self._cell_size_nm):
                ordinals.update(self._buckets.get((cell_x, cell_y), ()))
        candidates = tuple(sorted(ordinals))
        return (
            tuple(
                self._entries[ordinal].value
                for ordinal in candidates
                if _bounds_intersect(self._entries[ordinal].bounds, bounds)
            ),
            len(candidates),
        )


DEFAULT_MAX_INCREMENTAL_ENTRIES = 4_096
DEFAULT_MAX_CELLS_PER_ENTRY = 1_024


class IncrementalSpatialIndex:
    """A bounded uniform grid over integer rectangles that supports insert, remove, and query.

    Three design choices make this structure safe to mutate, and each is load-bearing:

    1. **The cell size is declared at construction and never re-derived.**  The set of cells an
       entry occupies is therefore a pure function of ``(bounds, cell_size_nm)``, so an insert or
       a remove can never perturb any other entry's placement.  "Mutate in place" and "rebuild
       from the survivors" are the same computation reached by two routes, which is why
       incremental-equals-rebuilt is a property of the design rather than a hope checked by a
       test.  ``docs/research/incremental-spatial-index-v1.md`` §2 argues this against the
       R-tree alternative, whose node boundaries depend on insertion order.
    2. **Every query returns a superset**, never a subset.  An entry whose bounds intersect the
       query rectangle is always returned; an implementation is free to return more.  An index
       that missed an obstacle would make an illegal route look legal, so the conservatism runs
       in the same direction as every other approximation in this router.
    3. **Work is bounded in both directions.**  An entry that would occupy more cells than the
       declared ceiling is held in an oversize set that *every* query returns, and a query
       rectangle that would sweep more cells than the ceiling degrades to a full scan.  Both
       fallbacks add candidates; neither can remove one.

    Keys are stable string identities (a net id, an obstacle id) rather than a generic type
    parameter: the index sorts its results to stay deterministic, and requiring keys to be both
    hashable and totally ordered is easier to state as "keys are strings" than to encode in a
    bound.  The payload a caller cares about lives in the caller's own mapping.

    ADR-0051's constraint is preserved: this index is mutated *between* negotiation passes, never
    during an A* expansion.  The immutable :class:`ConservativeSpatialIndex` remains the structure
    a single search sees.
    """

    __slots__ = ("_bounds", "_buckets", "_cell_size_nm", "_max_cells", "_max_entries", "_oversize")

    def __init__(
        self,
        *,
        cell_size_nm: int,
        max_entries: int = DEFAULT_MAX_INCREMENTAL_ENTRIES,
        max_cells_per_entry: int = DEFAULT_MAX_CELLS_PER_ENTRY,
    ) -> None:
        if isinstance(cell_size_nm, bool) or not isinstance(cell_size_nm, int) or cell_size_nm < 1:
            raise ValueError("an incremental index cell size must be a positive integer")
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        if (
            isinstance(max_cells_per_entry, bool)
            or not isinstance(max_cells_per_entry, int)
            or max_cells_per_entry < 1
        ):
            raise ValueError("max_cells_per_entry must be a positive integer")
        self._cell_size_nm = cell_size_nm
        self._max_entries = max_entries
        self._max_cells = max_cells_per_entry
        self._bounds: dict[str, _Bounds] = {}
        self._buckets: dict[_Cell, set[str]] = {}
        self._oversize: set[str] = set()

    @property
    def cell_size_nm(self) -> int:
        return self._cell_size_nm

    @property
    def entry_count(self) -> int:
        return len(self._bounds)

    @property
    def oversize_count(self) -> int:
        """How many entries are held in the always-returned oversize set."""

        return len(self._oversize)

    @property
    def bucket_count(self) -> int:
        return len(self._buckets)

    def __contains__(self, key: object) -> bool:
        return key in self._bounds

    def bounds_of(self, key: str) -> _Bounds:
        """Return the exact stored rectangle for one key."""

        return self._bounds[key]

    def keys(self) -> tuple[str, ...]:
        """Return every indexed key in canonical sorted order."""

        return tuple(sorted(self._bounds))

    def insert(self, key: str, bounds: _Bounds) -> None:
        """Index one conservatively bounded object under a key that is not already present.

        Re-inserting a live key is refused rather than silently replaced.  A caller that means to
        move an object says so with :meth:`remove` first, which keeps "the index forgot to drop a
        stale rectangle" from being expressible.
        """

        if not isinstance(key, str) or not key:
            raise ValueError("an incremental index key must be a non-empty string")
        if key in self._bounds:
            raise ValueError("an incremental index key is already present")
        if len(self._bounds) >= self._max_entries:
            raise SpatialIndexCapacityError("the incremental spatial index is at capacity")
        checked = _checked_bounds(bounds)
        self._bounds[key] = checked
        if _cell_span(checked, self._cell_size_nm) > self._max_cells:
            # Bounded in the safe direction: an object too large to bucket cheaply is returned by
            # every query instead of being bucketed partially.  That is more candidates, never
            # fewer, so the superset contract holds and the cost stays proportional to how many
            # such objects exist rather than to how many cells they cover.
            self._oversize.add(key)
            return
        for cell_x in _cell_range(checked[0], checked[2], self._cell_size_nm):
            for cell_y in _cell_range(checked[1], checked[3], self._cell_size_nm):
                self._buckets.setdefault((cell_x, cell_y), set()).add(key)

    def remove(self, key: str) -> None:
        """Drop one key and every cell reference to it.  An absent key is refused."""

        if key not in self._bounds:
            raise ValueError("an incremental index cannot remove an absent key")
        checked = self._bounds.pop(key)
        if key in self._oversize:
            self._oversize.discard(key)
            return
        for cell_x in _cell_range(checked[0], checked[2], self._cell_size_nm):
            for cell_y in _cell_range(checked[1], checked[3], self._cell_size_nm):
                cell = (cell_x, cell_y)
                occupants = self._buckets.get(cell)
                if occupants is None:
                    continue
                occupants.discard(key)
                if not occupants:
                    # An emptied bucket is deleted rather than kept.  Two indexes holding the same
                    # entries must be indistinguishable, including in `bucket_count`, or
                    # incremental-equals-rebuilt would be true of queries but false of the object.
                    del self._buckets[cell]

    def clear(self) -> None:
        """Drop every entry, keeping the declared cell size and capacity."""

        self._bounds.clear()
        self._buckets.clear()
        self._oversize.clear()

    def query(self, bounds: _Bounds) -> tuple[str, ...]:
        """Return, in canonical sorted order, a superset of the keys intersecting ``bounds``.

        An empty or reversed query rectangle selects nothing.  Every returned key is filtered by
        the exact integer intersection predicate against its own stored rectangle, so the result
        is exact with respect to those conservative rectangles — but callers must depend only on
        the superset guarantee, because the fallbacks above are free to widen it.
        """

        keys, _examined = self.query_with_stats(bounds)
        return keys

    def query_with_stats(self, bounds: _Bounds) -> tuple[tuple[str, ...], int]:
        """Return query keys and how many candidates the exact predicate examined.

        The second value is diagnostic and benchmark evidence only.  No routing decision reads
        it, and it is never published in a candidate identity.
        """

        checked = _checked_bounds(bounds)
        if checked[0] > checked[2] or checked[1] > checked[3] or not self._bounds:
            return (), 0
        span = _cell_span(checked, self._cell_size_nm)
        candidates: set[str]
        if span > self._max_cells:
            # A query wider than the cell ceiling would cost more to bucket-sweep than to scan.
            # Scanning is a superset of any bucket answer, so this is a work bound, not a change
            # of result.
            candidates = set(self._bounds)
        else:
            candidates = set(self._oversize)
            for cell_x in _cell_range(checked[0], checked[2], self._cell_size_nm):
                for cell_y in _cell_range(checked[1], checked[3], self._cell_size_nm):
                    occupants = self._buckets.get((cell_x, cell_y))
                    if occupants is not None:
                        candidates |= occupants
        return (
            tuple(
                sorted(key for key in candidates if _bounds_intersect(self._bounds[key], checked))
            ),
            len(candidates),
        )


__all__ = [
    "DEFAULT_MAX_CELLS_PER_ENTRY",
    "DEFAULT_MAX_INCREMENTAL_ENTRIES",
    "ConservativeSpatialIndex",
    "IncrementalSpatialIndex",
    "SpatialIndexCapacityError",
    "SpatialIndexEntry",
    "bounds_intersect",
    "inflate_bounds",
]
