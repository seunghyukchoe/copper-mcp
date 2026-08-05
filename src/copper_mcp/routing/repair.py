"""Bounded exact local lattice repair for coordinator-supplied windows.

This is deliberately *not* a board mutator, a policy evaluator, or a RouteCandidate serializer.
It turns a conventionally coordinator-supplied :class:`RepairWindowCandidate` plus a bounded,
already-derived occupancy view into an immutable local route proposal. This local type does not
authenticate that provenance or claim ownership; a future coordinator must establish it at its own
validated boundary before binding the geometry to Board IR and sending it through the ordinary
candidate and physical-clearance gates.

The search is Dijkstra over ``(cell, incoming direction)`` states with the lexicographic positive
cost ``(unit steps, bends, cell sequence)``.  The direction state makes bend cost Markovian;
positive unit-step edges preserve Dijkstra's optimality precondition.  The full cell sequence is
included only as a capped deterministic tie-breaker, not as a user/model-provided route.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from heapq import heappop, heappush
from itertools import pairwise
from typing import Final, TypeAlias, cast

from copper_mcp.routing.policy import PolicyBounds, RepairWindowCandidate

GridCell: TypeAlias = tuple[int, int]
CancellationCheck: TypeAlias = Callable[[], bool]

_MAX_WINDOW_CELLS: Final = 4_096
_MAX_EXPANSIONS: Final = 4_096
_DIRECTIONS: Final[tuple[GridCell, ...]] = ((-1, 0), (0, -1), (0, 1), (1, 0))
_EMPTY_DIGEST: Final = f"sha256:{'0' * 64}"
_SHA256_PREFIX_LENGTH: Final = len("sha256:") + 64


class LocalRepairStatus(StrEnum):
    """Terminal state for a bounded local-repair proposal."""

    COMPLETED = "completed"
    NO_PATH = "no_path"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    INVALID_REQUEST = "invalid_request"


def _cell(name: str, value: object) -> GridCell:
    if (
        type(value) is not tuple
        or len(value) != 2
        or any(type(coordinate) is not int for coordinate in value)
    ):
        raise ValueError(f"{name} must be an integer grid cell")
    return cast(GridCell, value)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _route_digest(input_digest: str, route: tuple[GridCell, ...]) -> str:
    """Return the route content binding for a completed local repair proposal."""

    return _digest(
        {
            "input_digest": input_digest,
            "route": [list(cell) for cell in route],
            "schema": "copper-mcp.local-exact-repair-route.v1",
        }
    )


def _within(bounds: PolicyBounds, cell: GridCell) -> bool:
    return bounds.min_x <= cell[0] <= bounds.max_x and bounds.min_y <= cell[1] <= bounds.max_y


@dataclass(frozen=True, slots=True)
class LocalRepairRequest:
    """Trusted coordinator input for one bounded local repair attempt.

    ``blocked_cells`` must be a canonical occupancy projection from the deterministic core.  It is
    intentionally not populated by an advisory policy, and neither it nor the result has Board
    IR, pad IDs, widths, layers, or an apply capability.
    """

    repair_window: RepairWindowCandidate
    start: GridCell
    end: GridCell
    blocked_cells: tuple[GridCell, ...] = ()
    max_expansions: int = _MAX_EXPANSIONS
    max_window_cells: int = _MAX_WINDOW_CELLS
    _construction_digest: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.repair_window) is not RepairWindowCandidate:
            raise ValueError("local repair requires a coordinator-supplied repair window")
        start = _cell("local repair start", self.start)
        end = _cell("local repair end", self.end)
        if start == end:
            raise ValueError("local repair endpoints must differ")
        if not (
            _within(self.repair_window.bounds, start) and _within(self.repair_window.bounds, end)
        ):
            raise ValueError("local repair endpoints must remain inside the repair window")
        if type(self.max_expansions) is not int or not 1 <= self.max_expansions <= _MAX_EXPANSIONS:
            raise ValueError("local repair expansion budget is unsupported")
        if (
            type(self.max_window_cells) is not int
            or not 1 <= self.max_window_cells <= _MAX_WINDOW_CELLS
        ):
            raise ValueError("local repair window budget is unsupported")
        width = self.repair_window.bounds.max_x - self.repair_window.bounds.min_x + 1
        height = self.repair_window.bounds.max_y - self.repair_window.bounds.min_y + 1
        if width * height > self.max_window_cells:
            raise ValueError("local repair window exceeds the cell budget")
        if (
            type(self.blocked_cells) is not tuple
            or len(self.blocked_cells) > self.max_window_cells
            or any(
                not _within(self.repair_window.bounds, _cell("blocked local repair cell", cell))
                for cell in self.blocked_cells
            )
            or tuple(sorted(self.blocked_cells)) != self.blocked_cells
            or len(set(self.blocked_cells)) != len(self.blocked_cells)
        ):
            raise ValueError("local repair blocked cells must be canonical and in-window")
        if start in self.blocked_cells or end in self.blocked_cells:
            raise ValueError("local repair endpoints cannot be blocked")
        object.__setattr__(self, "_construction_digest", self.input_digest)

    @property
    def input_digest(self) -> str:
        """Return the content address of this exact local, candidate-only request."""

        return _digest(
            {
                "blocked_cells": [list(cell) for cell in self.blocked_cells],
                "end": list(self.end),
                "max_expansions": self.max_expansions,
                "max_window_cells": self.max_window_cells,
                "repair_window": self.repair_window.as_json(),
                "schema": "copper-mcp.local-exact-repair-input.v1",
                "start": list(self.start),
            }
        )


@dataclass(frozen=True, slots=True)
class LocalRepairResult:
    """Immutable local route proposal with bounded, auditable work accounting."""

    status: LocalRepairStatus
    input_digest: str
    route: tuple[GridCell, ...] = ()
    route_digest: str = _EMPTY_DIGEST
    expanded_states: int = 0
    bend_count: int = 0
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not LocalRepairStatus:
            raise ValueError("local repair status is malformed")
        if (
            type(self.input_digest) is not str
            or len(self.input_digest) != _SHA256_PREFIX_LENGTH
            or not self.input_digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in self.input_digest[7:])
        ):
            raise ValueError("local repair input digest is malformed")
        if type(self.route) is not tuple or any(
            _cell("local repair route cell", cell) != cell for cell in self.route
        ):
            raise ValueError("local repair route is malformed")
        if (
            type(self.route_digest) is not str
            or len(self.route_digest) != _SHA256_PREFIX_LENGTH
            or not self.route_digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in self.route_digest[7:])
        ):
            raise ValueError("local repair route digest is malformed")
        if type(self.expanded_states) is not int or self.expanded_states < 0:
            raise ValueError("local repair work accounting is malformed")
        if type(self.bend_count) is not int or self.bend_count < 0:
            raise ValueError("local repair bend accounting is malformed")
        if self.status is LocalRepairStatus.COMPLETED:
            if (
                len(self.route) < 2
                or self.route_digest == _EMPTY_DIGEST
                or self.diagnostic is not None
            ):
                raise ValueError("completed local repair must contain a route and no diagnostic")
        elif self.route or self.route_digest != _EMPTY_DIGEST or self.bend_count:
            raise ValueError("non-completed local repair cannot publish a route")


def _canonical_request(request: object) -> LocalRepairRequest | None:
    """Return a reconstructed immutable request, rejecting post-construction mutation.

    The public entry point must never let a previously frozen object directly influence a search:
    hostile in-process code can bypass frozen slots with ``object.__setattr__``.  The construction
    digest seals ordinary object lifetime; reconstruction gives the search a fresh exact-tuple,
    primitive-only copy even when the caller retains the original object.
    """

    if type(request) is not LocalRepairRequest:
        return None
    try:
        supplied_window = request.repair_window
        if type(supplied_window) is not RepairWindowCandidate:
            return None
        supplied_bounds = supplied_window.bounds
        if type(supplied_bounds) is not PolicyBounds:
            return None
        if (
            type(request.start) is not tuple
            or type(request.end) is not tuple
            or type(request.blocked_cells) is not tuple
            or type(request.max_expansions) is not int
            or type(request.max_window_cells) is not int
            or type(request._construction_digest) is not str
        ):
            return None
        bounds = PolicyBounds(
            supplied_bounds.min_x,
            supplied_bounds.min_y,
            supplied_bounds.max_x,
            supplied_bounds.max_y,
        )
        window = RepairWindowCandidate(
            supplied_window.net_id,
            bounds,
            supplied_window.conflict_score,
        )
        canonical = LocalRepairRequest(
            repair_window=window,
            start=request.start,
            end=request.end,
            blocked_cells=request.blocked_cells,
            max_expansions=request.max_expansions,
            max_window_cells=request.max_window_cells,
        )
    except Exception:
        return None
    return canonical if canonical.input_digest == request._construction_digest else None


def _bend_count(route: tuple[GridCell, ...]) -> int:
    """Return the number of direction changes in a proven unit-step route."""

    directions = tuple((end[0] - start[0], end[1] - start[1]) for start, end in pairwise(route))
    return sum(left != right for left, right in pairwise(directions))


def verify_local_repair_result(request: object, result: object) -> bool:
    """Prove a local repair result is bound to one revalidated immutable request.

    The verifier is deliberately stricter than the result dataclass constructor: frozen objects can
    still be forged or mutated through hostile in-process code. A completed result must bind the
    original request digest and route digest, use exact endpoints, stay inside the selected window,
    avoid blocked cells, take only orthogonal unit steps without repeated cells, and report its
    exact bend count. Non-completed results cannot carry geometry and use fixed diagnostics.
    """

    canonical = _canonical_request(request)
    if canonical is None or type(result) is not LocalRepairResult:
        return False
    try:
        if (
            type(result.status) is not LocalRepairStatus
            or type(result.input_digest) is not str
            or type(result.route_digest) is not str
            or type(result.route) is not tuple
            or type(result.expanded_states) is not int
            or type(result.bend_count) is not int
            or (result.diagnostic is not None and type(result.diagnostic) is not str)
            or result.input_digest != canonical.input_digest
            or not 0 <= result.expanded_states <= canonical.max_expansions
        ):
            return False
        if result.status is LocalRepairStatus.COMPLETED:
            if (
                result.diagnostic is not None
                or result.route[0] != canonical.start
                or result.route[-1] != canonical.end
                or len(result.route) > canonical.max_window_cells
                or len(set(result.route)) != len(result.route)
                or result.route_digest != _route_digest(result.input_digest, result.route)
                or result.bend_count != _bend_count(result.route)
            ):
                return False
            blocked = frozenset(canonical.blocked_cells)
            for cell in result.route:
                if _cell("verified local repair route cell", cell) != cell:
                    return False
                if not _within(canonical.repair_window.bounds, cell) or cell in blocked:
                    return False
            return all(
                abs(end[0] - start[0]) + abs(end[1] - start[1]) == 1
                for start, end in pairwise(result.route)
            )
        if result.route or result.route_digest != _EMPTY_DIGEST or result.bend_count:
            return False
        if result.status is LocalRepairStatus.NO_PATH:
            return result.diagnostic == "local repair found no route inside the supplied window"
        if result.status is LocalRepairStatus.BUDGET_EXHAUSTED:
            return (
                result.expanded_states == canonical.max_expansions
                and result.diagnostic == "local repair expansion budget was exhausted"
            )
        if result.status is LocalRepairStatus.CANCELLED:
            return result.diagnostic in {
                "local repair was cancelled before search",
                "local repair was cancelled during search",
            }
        if result.status is LocalRepairStatus.INVALID_REQUEST:
            return (
                result.expanded_states == 0
                and result.diagnostic == "the local repair cancellation hook is invalid"
            )
    except Exception:  # pragma: no cover - hostile in-process result must fail closed
        return False


def _cancelled(cancelled: CancellationCheck | None) -> bool:
    if cancelled is None:
        return False
    try:
        return bool(cancelled())
    except Exception:  # pragma: no cover - external cooperative callback fails closed
        return True


def _result(
    request: LocalRepairRequest,
    status: LocalRepairStatus,
    *,
    expanded_states: int = 0,
    diagnostic: str | None = None,
    route: tuple[GridCell, ...] = (),
    bend_count: int = 0,
) -> LocalRepairResult:
    result = LocalRepairResult(
        status=status,
        input_digest=request.input_digest,
        route=route,
        route_digest=(
            _route_digest(request.input_digest, route)
            if status is LocalRepairStatus.COMPLETED
            else _EMPTY_DIGEST
        ),
        expanded_states=expanded_states,
        bend_count=bend_count,
        diagnostic=diagnostic,
    )
    if not verify_local_repair_result(request, result):  # pragma: no cover - implementation guard
        raise RuntimeError("local repair result failed its own request-bound verifier")
    return result


def exact_local_repair(
    request: object,
    *,
    cancelled: object = None,
) -> LocalRepairResult:
    """Propose the exact shortest, then fewest-bend, route within one repair window.

    Work is capped before every state expansion.  Cancellation is cooperative and atomic: a
    cancelled result contains no route.  Invalid request/cancellation boundaries return fixed
    diagnostics rather than exception content.  This function has no board, model, or apply I/O.
    """

    canonical_request = _canonical_request(request)
    if canonical_request is None:
        return LocalRepairResult(
            status=LocalRepairStatus.INVALID_REQUEST,
            input_digest=_EMPTY_DIGEST,
            diagnostic="the local repair request is invalid",
        )
    if cancelled is not None and not callable(cancelled):
        return _result(
            canonical_request,
            LocalRepairStatus.INVALID_REQUEST,
            diagnostic="the local repair cancellation hook is invalid",
        )
    cancellation_check = cancelled
    if _cancelled(cancellation_check):
        return _result(
            canonical_request,
            LocalRepairStatus.CANCELLED,
            diagnostic="local repair was cancelled before search",
        )

    blocked = frozenset(canonical_request.blocked_cells)
    # State key includes direction so the incremental bend objective is exact.  Path itself is a
    # bounded canonical tie-breaker for equal `(steps, bends)` states and final routes.
    State: TypeAlias = tuple[GridCell, int | None]
    Cost: TypeAlias = tuple[int, int, tuple[GridCell, ...]]
    initial_state: State = (canonical_request.start, None)
    initial_cost: Cost = (0, 0, (canonical_request.start,))
    best: dict[State, Cost] = {initial_state: initial_cost}
    queue: list[tuple[int, int, tuple[GridCell, ...], int, GridCell]] = [
        (0, 0, (canonical_request.start,), -1, canonical_request.start)
    ]
    expanded_states = 0

    while queue:
        steps, bends, path, direction_key, cell = heappop(queue)
        direction = None if direction_key < 0 else direction_key
        state: State = (cell, direction)
        cost: Cost = (steps, bends, path)
        if best.get(state) != cost:
            continue
        if _cancelled(cancellation_check):
            return _result(
                canonical_request,
                LocalRepairStatus.CANCELLED,
                expanded_states=expanded_states,
                diagnostic="local repair was cancelled during search",
            )
        if cell == canonical_request.end:
            return _result(
                canonical_request,
                LocalRepairStatus.COMPLETED,
                expanded_states=expanded_states,
                route=path,
                bend_count=bends,
            )
        if expanded_states >= canonical_request.max_expansions:
            return _result(
                canonical_request,
                LocalRepairStatus.BUDGET_EXHAUSTED,
                expanded_states=expanded_states,
                diagnostic="local repair expansion budget was exhausted",
            )
        expanded_states += 1
        for next_direction, (dx, dy) in enumerate(_DIRECTIONS):
            next_cell = (cell[0] + dx, cell[1] + dy)
            if (
                not _within(canonical_request.repair_window.bounds, next_cell)
                or next_cell in blocked
            ):
                continue
            next_cost: Cost = (
                steps + 1,
                bends + int(direction is not None and direction != next_direction),
                (*path, next_cell),
            )
            next_state: State = (next_cell, next_direction)
            if next_cost >= best.get(next_state, (1 << 60, 1 << 60, ())):
                continue
            best[next_state] = next_cost
            heappush(
                queue,
                (*next_cost, next_direction, next_cell),
            )

    return _result(
        canonical_request,
        LocalRepairStatus.NO_PATH,
        expanded_states=expanded_states,
        diagnostic="local repair found no route inside the supplied window",
    )


__all__ = [
    "GridCell",
    "LocalRepairRequest",
    "LocalRepairResult",
    "LocalRepairStatus",
    "exact_local_repair",
    "verify_local_repair_result",
]
