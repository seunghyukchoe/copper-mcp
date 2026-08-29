"""Coordinator-derived, immutable provenance for one local exact-repair attempt.

This is deliberately internal.  It turns only already accepted negotiated state into the abstract
local-repair input; policies may select the resulting window but cannot create or widen one.
Board-IR path authority remains with ``validate_candidate_path`` before publication.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import cast

from copper_mcp.board_ir import BoardIRSnapshot, PointNM, verify_snapshot
from copper_mcp.routing.astar import verify_candidate_id
from copper_mcp.routing.contracts import (
    CancellationCheck,
    RouteCandidate,
    RouteFailureCode,
    RoutePath,
    RouteRequest,
)
from copper_mcp.routing.policy import PolicyBounds, RepairWindowCandidate
from copper_mcp.routing.repair import LocalRepairRequest, RepairTransactionSettings

_SCHEMA = "copper-mcp.negotiated-local-repair-provenance.v1"
_PROJECTION_SCHEMA = "copper-mcp.negotiated-local-repair-projection.v2"
_TREE_PATH_SCHEMA = "copper-mcp.negotiated-tree-repair-path.v1"
_TREE_SELECTION_SCHEMA = "copper-mcp.negotiated-tree-repair-selection.v1"
_TREE_PROJECTION_SCHEMA = "copper-mcp.negotiated-tree-repair-projection.v1"
_TREE_PROVENANCE_SCHEMA = "copper-mcp.negotiated-tree-repair-provenance.v1"
_SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
_CAPABILITY = object()
_TREE_SELECTION_CAPABILITY = object()
_TREE_PROVENANCE_CAPABILITY = object()
_MAX_TREE_PADS = 32
_MAX_TREE_CONFLICTS = _MAX_TREE_PADS - 1
_MAX_RESPONSIBILITY_CHECKS = 10_000_000
_MAX_PROJECTION_OBSTACLE_CHECKS = 4_096
_MAX_TREE_REFUSAL_WORK = 2 * _MAX_PROJECTION_OBSTACLE_CHECKS


class _BoardIRProjectionError(ValueError):
    """Closed internal projection failure that preserves already consumed work."""

    __slots__ = ("cancelled", "obstacle_checks")

    def __init__(self, obstacle_checks: int, *, cancelled: bool) -> None:
        if (
            type(obstacle_checks) is not int
            or not 0 <= obstacle_checks <= _MAX_PROJECTION_OBSTACLE_CHECKS
            or type(cancelled) is not bool
        ):
            raise ValueError("repair projection refusal accounting is invalid")
        super().__init__("repair provenance Board IR projection was refused")
        self.obstacle_checks = obstacle_checks
        self.cancelled = cancelled


class _TreeRepairProvenanceError(ValueError):
    """Closed coordinator-facing tree refusal with no candidate or geometry content.

    ``obstacle_checks`` is the coordinator's single consumed-work return channel.  A refusal can
    carry both the untouched-branch projection charge and the later Board-IR charge, hence its
    closed range is the sum of the two unchanged 4,096-unit ceilings.
    """

    __slots__ = ("cancelled", "obstacle_checks")

    def __init__(self, obstacle_checks: int, *, cancelled: bool) -> None:
        if (
            type(obstacle_checks) is not int
            or not 0 <= obstacle_checks <= _MAX_TREE_REFUSAL_WORK
            or type(cancelled) is not bool
        ):
            raise ValueError("tree repair refusal accounting is invalid")
        super().__init__("tree repair provenance is invalid")
        self.obstacle_checks = obstacle_checks
        self.cancelled = cancelled


class _TreeUntouchedProjectionError(ValueError):
    """Closed refusal from the cancellation-aware untouched-tree projection pass."""

    __slots__ = ("cancelled", "projection_work")

    def __init__(self, projection_work: int, *, cancelled: bool) -> None:
        if (
            type(projection_work) is not int
            or not 0 <= projection_work <= _MAX_PROJECTION_OBSTACLE_CHECKS
            or type(cancelled) is not bool
        ):
            raise ValueError("untouched tree projection accounting is invalid")
        super().__init__("tree repair untouched projection was refused")
        self.projection_work = projection_work
        self.cancelled = cancelled


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, allow_nan=False, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return f"sha256:{hashlib.sha256(encoded.encode('ascii')).hexdigest()}"


def _cell(point: PointNM, origin: PointNM, step: int) -> tuple[int, int]:
    dx, dy = point.x - origin.x, point.y - origin.y
    if dx % step or dy % step:
        raise ValueError("candidate geometry is not on the coordinator grid")
    return (dx // step, dy // step)


def _axis_projection(first: int, second: int, origin: int, step: int) -> range:
    """Cover one physical interval with every adjacent coordinator-lattice coordinate."""

    lower = (min(first, second) - origin) // step
    upper_delta = max(first, second) - origin
    upper = -((-upper_delta) // step)
    return range(lower, upper + 1)


def _strict_candidate_bounds(
    candidate: RouteCandidate, origin: PointNM, step: int
) -> tuple[int, int, int, int]:
    """Return bounds only when every target vertex belongs to its authoritative lattice."""

    cells = tuple(
        _cell(point, origin, step) for path in candidate.patch.paths for point in path.vertices
    )
    if not cells:
        raise ValueError("candidate geometry is empty")
    return (
        min(cell[0] for cell in cells),
        min(cell[1] for cell in cells),
        max(cell[0] for cell in cells),
        max(cell[1] for cell in cells),
    )


def _candidate_bounds(
    candidate: RouteCandidate, origin: PointNM, step: int
) -> tuple[int, int, int, int]:
    """Return conservative lattice bounds without enumerating a single unit cell.

    Conflicting candidates may use the same step on a different request-local phase.  Floor/ceiling
    coverage keeps their geometry inside the target lattice window; aligned legacy geometry maps
    to the same exact cells as before.
    """

    points = tuple(point for path in candidate.patch.paths for point in path.vertices)
    if not points:
        raise ValueError("candidate geometry is empty")
    return (
        min((point.x - origin.x) // step for point in points),
        min((point.y - origin.y) // step for point in points),
        max(-((-(point.x - origin.x)) // step) for point in points),
        max(-((-(point.y - origin.y)) // step) for point in points),
    )


def _candidate_cells(candidate: RouteCandidate, origin: PointNM, step: int) -> set[tuple[int, int]]:
    """Conservatively cover candidate segments on the target request's lattice.

    A shifted-phase segment is assigned to both adjacent rows or columns.  This can refuse an
    otherwise searchable window, but it prevents foreign-phase copper from disappearing merely
    because its vertices are not congruent with the target origin.
    """

    cells: set[tuple[int, int]] = set()
    for path in candidate.patch.paths:
        for start, end in pairwise(path.vertices):
            if start.x != end.x and start.y != end.y:
                raise ValueError("candidate geometry is not orthogonal")
            if start == end:
                raise ValueError("candidate geometry is not orthogonal")
            xs = _axis_projection(start.x, end.x, origin.x, step)
            ys = _axis_projection(start.y, end.y, origin.y, step)
            cells.update((x, y) for x in xs for y in ys)
    return cells


def _project_board_ir_blocked_cells(
    snapshot: BoardIRSnapshot,
    request: RouteRequest,
    *,
    origin: PointNM,
    bounds: PolicyBounds,
    settings: RepairTransactionSettings,
    cancelled: CancellationCheck | None = None,
) -> tuple[set[tuple[int, int]], int]:
    """Project exact Board-IR edge authority into the already bounded repair lattice.

    The projection deliberately centralizes the reference router adapter in one internal seam.
    It is conservative: a cell is unavailable if no one-step continuation from it is legal.  This
    can refuse a repair window that a less conservative projection would search, but it cannot
    turn Board-IR material into free lattice space.  Keeping the private reference symbols local
    to this adapter prevents the repair transaction from depending on their representation.
    """

    # ``astar`` has no stable public primitive for preparing a Board-IR obstacle predicate yet.
    # Keep that representation dependency here, rather than spreading it over provenance or the
    # coordinator.  The final repaired candidate is independently checked through the public
    # ``validate_candidate_path`` boundary before any result is published.
    from copper_mcp.routing.astar import (
        _edge_is_legal,
        _ExpectedFailureError,
        _prepare,
        _WorkBudget,
    )

    bounded_request = replace(
        request,
        settings=replace(
            request.settings,
            max_obstacle_checks=min(
                request.settings.max_obstacle_checks,
                settings.max_validator_obstacle_checks,
            ),
        ),
    )
    work = _WorkBudget(settings=bounded_request.settings, cancelled=cancelled)
    try:
        problem = _prepare(snapshot, bounded_request, work)
    except _ExpectedFailureError as error:
        raise _BoardIRProjectionError(
            work.obstacle_checks,
            cancelled=error.code is RouteFailureCode.CANCELLED,
        ) from error
    except Exception as error:  # pragma: no cover - defensive private reference boundary
        raise _BoardIRProjectionError(work.obstacle_checks, cancelled=False) from error
    if work.obstacle_checks > settings.max_validator_obstacle_checks:
        raise _BoardIRProjectionError(work.obstacle_checks, cancelled=False)

    blocked: set[tuple[int, int]] = set()
    for x in range(bounds.min_x, bounds.max_x + 1):
        for y in range(bounds.min_y, bounds.max_y + 1):
            point = PointNM(
                origin.x + x * request.settings.grid_step_nm,
                origin.y + y * request.settings.grid_step_nm,
            )
            legal = False
            for dx, dy in ((-1, 0), (0, -1), (0, 1), (1, 0)):
                try:
                    if _edge_is_legal(
                        point,
                        PointNM(
                            point.x + dx * request.settings.grid_step_nm,
                            point.y + dy * request.settings.grid_step_nm,
                        ),
                        problem,
                        work,
                    ):
                        legal = True
                        break
                except _ExpectedFailureError as error:
                    raise _BoardIRProjectionError(
                        work.obstacle_checks,
                        cancelled=error.code is RouteFailureCode.CANCELLED,
                    ) from error
                except Exception as error:  # pragma: no cover - defensive private boundary
                    raise _BoardIRProjectionError(
                        work.obstacle_checks,
                        cancelled=False,
                    ) from error
            if not legal:
                blocked.add((x, y))
    return blocked, work.obstacle_checks


def _net_clearance_nm(snapshot: BoardIRSnapshot, net_id: str) -> int:
    """Resolve a validated net's clearance without falling back to a weaker class."""

    classes = {item.id: item for item in snapshot.content.constraints.net_classes}
    assignments = {
        item.net_id: item.net_class_id for item in snapshot.content.constraints.assignments
    }
    class_id = assignments.get(net_id)
    net_class = None if class_id is None else classes.get(class_id)
    if net_class is None:
        raise ValueError("repair provenance net class is invalid")
    return net_class.clearance_nm


def _conflict_exclusion_radius_cells(
    snapshot: BoardIRSnapshot,
    request: RouteRequest,
    target: RouteCandidate,
    conflict: RouteCandidate,
) -> int:
    """Return a conservative lattice radius for the physical pairwise clearance rule."""

    required_nm = (
        target.patch.width_nm
        + conflict.patch.width_nm
        + 2
        * max(
            _net_clearance_nm(snapshot, request.net_id),
            _net_clearance_nm(snapshot, conflict.patch.net_id),
        )
    )
    # ``required_nm`` is in doubled coordinate units, exactly like the pairwise physical gate.
    # A lattice separation of ``radius`` is blocked precisely while its doubled distance is
    # *strictly* below that rule; exact equality remains legal in both paths.
    return (required_nm - 1) // (2 * request.settings.grid_step_nm)


def _expanded_conflict_cells(
    candidate: RouteCandidate,
    *,
    origin: PointNM,
    step: int,
    radius: int,
    bounds: PolicyBounds,
) -> set[tuple[int, int]]:
    """Project a conflicting centreline with its width-and-clearance exclusion envelope."""

    blocked: set[tuple[int, int]] = set()
    for cell in _candidate_cells(candidate, origin, step):
        for x in range(
            max(bounds.min_x, cell[0] - radius), min(bounds.max_x, cell[0] + radius) + 1
        ):
            for y in range(
                max(bounds.min_y, cell[1] - radius), min(bounds.max_y, cell[1] + radius) + 1
            ):
                blocked.add((x, y))
    return blocked


def _expanded_untouched_tree_cells(
    candidate: RouteCandidate,
    *,
    selected_path_index: int,
    origin: PointNM,
    step: int,
    bounds: PolicyBounds,
    maximum: int,
    cancelled: CancellationCheck | None = None,
    consumed_work: list[int] | None = None,
) -> set[tuple[int, int]]:
    """Block every untouched branch except the selected branch's authorized endpoints.

    The local solver is not allowed to treat existing same-net copper as free space: doing so can
    duplicate an untouched edge or attach at a second point and close a loop.  A square expansion
    by the trace-width lattice radius is conservative for thick traces; the private complete-tree
    validator remains the final exact topology authority.  ``maximum`` is the remaining shared
    projection-work ceiling after the conflict projection preflight.  Every attempted blocked-cell
    insertion consumes one unit from it, even when the cell is already present in the set.
    """

    if type(maximum) is not int or not 0 <= maximum <= _MAX_PROJECTION_OBSTACLE_CHECKS:
        raise ValueError("tree repair projection maximum is invalid")
    radius = candidate.patch.width_nm // step
    blocked: set[tuple[int, int]] = set()
    projection_work = 0
    for path_index, path in enumerate(candidate.patch.paths):
        if path_index == selected_path_index:
            continue
        for start, end in pairwise(path.vertices):
            if start == end or (start.x != end.x and start.y != end.y):
                raise ValueError("tree repair target geometry is invalid")
            for cell_x in _axis_projection(start.x, end.x, origin.x, step):
                for cell_y in _axis_projection(start.y, end.y, origin.y, step):
                    for x in range(
                        max(bounds.min_x, cell_x - radius),
                        min(bounds.max_x, cell_x + radius) + 1,
                    ):
                        for y in range(
                            max(bounds.min_y, cell_y - radius),
                            min(bounds.max_y, cell_y + radius) + 1,
                        ):
                            if projection_work >= maximum:
                                raise _TreeUntouchedProjectionError(
                                    projection_work,
                                    cancelled=False,
                                )
                            if cancelled is not None:
                                try:
                                    if bool(cancelled()):
                                        raise _TreeUntouchedProjectionError(
                                            projection_work,
                                            cancelled=True,
                                        )
                                except _TreeUntouchedProjectionError:
                                    raise
                                except Exception as error:
                                    raise _TreeUntouchedProjectionError(
                                        projection_work,
                                        cancelled=True,
                                    ) from error
                            blocked.add((x, y))
                            projection_work += 1
                            if consumed_work is not None:
                                consumed_work[0] = projection_work
    return blocked


def _provenance_digest(
    *,
    snapshot_digest: str,
    envelope_digest: str,
    iteration: int,
    target_net_id: str,
    target_candidate_id: str,
    conflicting_candidate_ids: tuple[str, ...],
    grid_origin: PointNM,
    grid_step_nm: int,
    window: RepairWindowCandidate,
    start: tuple[int, int],
    end: tuple[int, int],
    blocked_cells: tuple[tuple[int, int], ...],
    projection_digest: str,
    projection_obstacle_checks: int,
) -> str:
    """Return the complete coordinator-state binding for a repair provenance record."""

    return _digest(
        {
            "blocked_cells": [list(item) for item in blocked_cells],
            "conflicting_candidate_ids": list(conflicting_candidate_ids),
            "end": list(end),
            "envelope_digest": envelope_digest,
            "grid_origin": [grid_origin.x, grid_origin.y],
            "grid_step_nm": grid_step_nm,
            "iteration": iteration,
            "projection_digest": projection_digest,
            "projection_obstacle_checks": projection_obstacle_checks,
            "schema": _SCHEMA,
            "snapshot_digest": snapshot_digest,
            "start": list(start),
            "target_candidate_id": target_candidate_id,
            "target_net_id": target_net_id,
            "window": window.as_json(),
        }
    )


@dataclass(frozen=True, slots=True)
class CoordinatorRepairProvenance:
    """The complete non-policy binding for one selected repair window."""

    snapshot_digest: str
    envelope_digest: str
    iteration: int
    target_net_id: str
    target_candidate_id: str
    conflicting_candidate_ids: tuple[str, ...]
    grid_origin: PointNM
    grid_step_nm: int
    window: RepairWindowCandidate
    start: tuple[int, int]
    end: tuple[int, int]
    blocked_cells: tuple[tuple[int, int], ...]
    projection_digest: str
    projection_obstacle_checks: int
    integrity_digest: str
    _capability: object

    @property
    def digest(self) -> str:
        return self.integrity_digest

    def _recomputed_digest(self) -> str:
        return _provenance_digest(
            snapshot_digest=self.snapshot_digest,
            envelope_digest=self.envelope_digest,
            iteration=self.iteration,
            target_net_id=self.target_net_id,
            target_candidate_id=self.target_candidate_id,
            conflicting_candidate_ids=self.conflicting_candidate_ids,
            grid_origin=self.grid_origin,
            grid_step_nm=self.grid_step_nm,
            window=self.window,
            start=self.start,
            end=self.end,
            blocked_cells=self.blocked_cells,
            projection_digest=self.projection_digest,
            projection_obstacle_checks=self.projection_obstacle_checks,
        )

    def local_request(self, settings: RepairTransactionSettings) -> LocalRepairRequest:
        if (
            self._capability is not _CAPABILITY
            or not _SHA256.fullmatch(self.integrity_digest)
            or self.integrity_digest != self._recomputed_digest()
        ):
            raise ValueError("repair provenance is not coordinator-derived")
        return LocalRepairRequest(
            self.window,
            self.start,
            self.end,
            self.blocked_cells,
            settings.max_local_expansions,
            settings.max_window_cells,
        )


def derive_repair_provenance(
    snapshot: object,
    request: object,
    target: object,
    conflicts: object,
    *,
    envelope_digest: object,
    iteration: object,
    settings: object,
) -> CoordinatorRepairProvenance:
    """Derive one bounded window from trusted negotiated candidates, or raise ``ValueError``."""

    if (
        type(snapshot) is not BoardIRSnapshot
        or type(request) is not RouteRequest
        or type(target) is not RouteCandidate
        or type(conflicts) is not tuple
        or type(envelope_digest) is not str
        or type(iteration) is not int
        or type(settings) is not RepairTransactionSettings
    ):
        raise ValueError("repair provenance input is invalid")
    if not _SHA256.fullmatch(envelope_digest):
        raise ValueError("repair provenance input is invalid")
    if (
        snapshot.snapshot_digest != request.board_revision
        or target.base_revision != snapshot.snapshot_digest
        or target.patch.net_id != request.net_id
        or target.patch.layer_id != request.layer_id
        or target.settings.grid_step_nm != request.settings.grid_step_nm
        or not 1 <= iteration <= 32
    ):
        raise ValueError("repair provenance is stale or mismatched")
    try:
        verify_candidate_id(target)
        verify_snapshot(snapshot)
        if len(target.patch.paths) != 1:
            raise ValueError("repair provenance requires one path")
        path = target.patch.paths[0].vertices
        origin = path[0]
        start, end = (
            _cell(path[0], origin, request.settings.grid_step_nm),
            _cell(path[-1], origin, request.settings.grid_step_nm),
        )
        others = tuple(sorted(conflicts, key=lambda item: item.candidate_id))
        if not others or any(
            type(item) is not RouteCandidate
            or item.patch.net_id == request.net_id
            or item.base_revision != snapshot.snapshot_digest
            or item.patch.layer_id != request.layer_id
            or item.settings.grid_step_nm != request.settings.grid_step_nm
            for item in others
        ):
            raise ValueError("repair provenance conflicts are invalid")
        if target.candidate_id in {item.candidate_id for item in others} or len(
            {item.candidate_id for item in others}
        ) != len(others):
            raise ValueError("repair provenance conflicts are invalid")
        for item in others:
            verify_candidate_id(item)
        all_bounds = (
            _strict_candidate_bounds(target, origin, request.settings.grid_step_nm),
            *(_candidate_bounds(item, origin, request.settings.grid_step_nm) for item in others),
        )
        exclusion_radius = max(
            _conflict_exclusion_radius_cells(snapshot, request, target, item) for item in others
        )
        min_x = min(item[0] for item in all_bounds)
        min_y = min(item[1] for item in all_bounds)
        max_x = max(item[2] for item in all_bounds)
        max_y = max(item[3] for item in all_bounds)
        # The deterministic escape room includes the maximum pairwise physical exclusion radius
        # plus one lattice cell.  A conflict therefore cannot be represented as a naked
        # centreline that the local solver may legally brush past.
        margin = exclusion_radius + 1
        bounds = PolicyBounds(min_x - margin, min_y - margin, max_x + margin, max_y + margin)
        if (bounds.max_x - bounds.min_x + 1) * (
            bounds.max_y - bounds.min_y + 1
        ) > settings.max_projection_cells:
            raise ValueError("repair provenance projection exceeds its cell budget")
        # The area preflight above is intentionally before unit-cell expansion.  Any candidate
        # path lies inside this bounded rectangle, so subsequent expansion is bounded as well.
        blocked: set[tuple[int, int]] = set()
        for item in others:
            blocked.update(
                _expanded_conflict_cells(
                    item,
                    origin=origin,
                    step=request.settings.grid_step_nm,
                    radius=_conflict_exclusion_radius_cells(snapshot, request, target, item),
                    bounds=bounds,
                )
            )
        board_blocked, projection_obstacle_checks = _project_board_ir_blocked_cells(
            snapshot,
            request,
            origin=origin,
            bounds=bounds,
            settings=settings,
        )
        blocked.update(board_blocked)
        if start in blocked or end in blocked:
            raise ValueError("repair provenance endpoint is physically unavailable")
        window = RepairWindowCandidate(request.net_id, bounds, len(others))
        projection_digest = _digest(
            {
                "blocked_cells": [list(item) for item in sorted(blocked)],
                "board_ir_snapshot_digest": snapshot.snapshot_digest,
                "obstacle_checks": projection_obstacle_checks,
                "schema": _PROJECTION_SCHEMA,
            }
        )
        blocked_cells = tuple(sorted(blocked))
        integrity_digest = _provenance_digest(
            snapshot_digest=snapshot.snapshot_digest,
            envelope_digest=envelope_digest,
            iteration=iteration,
            target_net_id=request.net_id,
            target_candidate_id=target.candidate_id,
            conflicting_candidate_ids=tuple(item.candidate_id for item in others),
            grid_origin=origin,
            grid_step_nm=request.settings.grid_step_nm,
            window=window,
            start=start,
            end=end,
            blocked_cells=blocked_cells,
            projection_digest=projection_digest,
            projection_obstacle_checks=projection_obstacle_checks,
        )
        return CoordinatorRepairProvenance(
            snapshot.snapshot_digest,
            envelope_digest,
            iteration,
            request.net_id,
            target.candidate_id,
            tuple(item.candidate_id for item in others),
            origin,
            request.settings.grid_step_nm,
            window,
            start,
            end,
            blocked_cells,
            projection_digest,
            projection_obstacle_checks,
            integrity_digest,
            _CAPABILITY,
        )
    except Exception as error:
        raise ValueError("repair provenance is invalid") from error


def _tree_path_digest(candidate_id: str, path_index: int, path: RoutePath) -> str:
    """Bind one compressed path without granting authority to detached geometry."""

    return _digest(
        {
            "candidate_id": candidate_id,
            "path_index": path_index,
            "schema": _TREE_PATH_SCHEMA,
            "vertices": [[point.x, point.y] for point in path.vertices],
        }
    )


def _tree_selection_digest(
    *,
    snapshot_digest: str,
    envelope_digest: str,
    iteration: int,
    target_net_id: str,
    target_candidate_id: str,
    target_path_count: int,
    target_path_index: int,
    target_path_digest: str,
    target_path_start: PointNM,
    target_path_end: PointNM,
    conflict_net_id: str,
    conflict_candidate_id: str,
    conflict_path_count: int,
    conflict_path_index: int,
    conflict_path_digest: str,
    responsibility_digest: str,
    responsibility_checks: int,
) -> str:
    """Return the immutable coordinator binding for one responsible path pair."""

    return _digest(
        {
            "conflict_candidate_id": conflict_candidate_id,
            "conflict_net_id": conflict_net_id,
            "conflict_path_count": conflict_path_count,
            "conflict_path_digest": conflict_path_digest,
            "conflict_path_index": conflict_path_index,
            "envelope_digest": envelope_digest,
            "iteration": iteration,
            "responsibility_checks": responsibility_checks,
            "responsibility_digest": responsibility_digest,
            "schema": _TREE_SELECTION_SCHEMA,
            "snapshot_digest": snapshot_digest,
            "target_candidate_id": target_candidate_id,
            "target_net_id": target_net_id,
            "target_path_count": target_path_count,
            "target_path_digest": target_path_digest,
            "target_path_end": [target_path_end.x, target_path_end.y],
            "target_path_index": target_path_index,
            "target_path_start": [target_path_start.x, target_path_start.y],
        }
    )


@dataclass(frozen=True, slots=True)
class CoordinatorTreeRepairSelection:
    """Capability-bound proof that one path pair owns a multi-pin repair attempt."""

    snapshot_digest: str
    envelope_digest: str
    iteration: int
    target_net_id: str
    target_candidate_id: str
    target_path_count: int
    target_path_index: int
    target_path_digest: str
    target_path_start: PointNM
    target_path_end: PointNM
    conflict_net_id: str
    conflict_candidate_id: str
    conflict_path_count: int
    conflict_path_index: int
    conflict_path_digest: str
    responsibility_digest: str
    responsibility_checks: int
    integrity_digest: str
    _capability: object

    @property
    def digest(self) -> str:
        return self.integrity_digest

    def _recomputed_digest(self) -> str:
        return _tree_selection_digest(
            snapshot_digest=self.snapshot_digest,
            envelope_digest=self.envelope_digest,
            iteration=self.iteration,
            target_net_id=self.target_net_id,
            target_candidate_id=self.target_candidate_id,
            target_path_count=self.target_path_count,
            target_path_index=self.target_path_index,
            target_path_digest=self.target_path_digest,
            target_path_start=self.target_path_start,
            target_path_end=self.target_path_end,
            conflict_net_id=self.conflict_net_id,
            conflict_candidate_id=self.conflict_candidate_id,
            conflict_path_count=self.conflict_path_count,
            conflict_path_index=self.conflict_path_index,
            conflict_path_digest=self.conflict_path_digest,
            responsibility_digest=self.responsibility_digest,
            responsibility_checks=self.responsibility_checks,
        )

    def _is_coordinator_derived(self) -> bool:
        try:
            return (
                self._capability is _TREE_SELECTION_CAPABILITY
                and _SHA256.fullmatch(self.integrity_digest) is not None
                and self.integrity_digest == self._recomputed_digest()
            )
        except Exception:
            return False


def _tree_pad_ids(snapshot: BoardIRSnapshot, net_id: str, layer_id: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            pad.id
            for pad in snapshot.content.pads
            if pad.net_id == net_id and layer_id in pad.layer_ids
        )
    )


def _tree_track_width_nm(snapshot: BoardIRSnapshot, net_id: str) -> int | None:
    classes = {item.id: item.track_width_nm for item in snapshot.content.constraints.net_classes}
    assignments = tuple(
        item.net_class_id
        for item in snapshot.content.constraints.assignments
        if item.net_id == net_id
    )
    if len(assignments) != 1:
        return None
    return classes.get(assignments[0])


def _require_tree_candidate_binding(
    snapshot: BoardIRSnapshot,
    candidate: object,
    *,
    layer_id: str,
    grid_step_nm: int,
) -> RouteCandidate:
    """Prove one candidate belongs to the bounded selected-layer coordinator state."""

    if type(candidate) is not RouteCandidate:
        raise ValueError("tree repair candidate is invalid")
    pad_ids = _tree_pad_ids(snapshot, candidate.patch.net_id, layer_id)
    expected_width = _tree_track_width_nm(snapshot, candidate.patch.net_id)
    vertex_count = sum(len(path.vertices) for path in candidate.patch.paths)
    if (
        not 2 <= len(pad_ids) <= _MAX_TREE_PADS
        or expected_width is None
        or candidate.base_revision != snapshot.snapshot_digest
        or candidate.patch.layer_id != layer_id
        or candidate.patch.width_nm != expected_width
        or candidate.settings.grid_step_nm != grid_step_nm
        or candidate.fill_binding is not None
        or candidate.metrics.vias != 0
        or candidate.start_pad_id != pad_ids[0]
        or candidate.end_pad_id != pad_ids[-1]
        or candidate.pad_count != len(pad_ids)
        or not 1 <= len(candidate.patch.paths) <= candidate.pad_count - 1
        or vertex_count > candidate.settings.max_grid_nodes
    ):
        raise ValueError("tree repair candidate is stale or mismatched")
    verify_candidate_id(candidate)
    return candidate


def derive_tree_repair_selection(
    snapshot: object,
    request: object,
    target: object,
    conflict: object,
    *,
    envelope_digest: object,
    iteration: object,
    target_path_index: object,
    conflict_path_index: object,
    responsibility_digest: object,
    responsibility_checks: object,
) -> CoordinatorTreeRepairSelection:
    """Bind one independently proven responsible path pair, without redoing geometry work."""

    if (
        type(snapshot) is not BoardIRSnapshot
        or type(request) is not RouteRequest
        or type(target) is not RouteCandidate
        or type(conflict) is not RouteCandidate
        or type(envelope_digest) is not str
        or type(iteration) is not int
        or type(target_path_index) is not int
        or type(conflict_path_index) is not int
        or type(responsibility_digest) is not str
        or type(responsibility_checks) is not int
    ):
        raise ValueError("tree repair selection input is invalid")
    if (
        not _SHA256.fullmatch(envelope_digest)
        or not _SHA256.fullmatch(responsibility_digest)
        or not 1 <= iteration <= 32
        or not 1 <= responsibility_checks <= _MAX_RESPONSIBILITY_CHECKS
    ):
        raise ValueError("tree repair selection input is invalid")
    try:
        verify_snapshot(snapshot)
        checked_target = _require_tree_candidate_binding(
            snapshot,
            target,
            layer_id=request.layer_id,
            grid_step_nm=request.settings.grid_step_nm,
        )
        checked_conflict = _require_tree_candidate_binding(
            snapshot,
            conflict,
            layer_id=request.layer_id,
            grid_step_nm=request.settings.grid_step_nm,
        )
        if (
            snapshot.snapshot_digest != request.board_revision
            or checked_target.patch.net_id != request.net_id
            or checked_target.settings != request.settings
            or checked_target.seed != request.seed
            or not 3 <= checked_target.pad_count <= _MAX_TREE_PADS
            or checked_conflict.patch.net_id == request.net_id
            or checked_conflict.candidate_id == checked_target.candidate_id
            or not 0 <= target_path_index < len(checked_target.patch.paths)
            or not 0 <= conflict_path_index < len(checked_conflict.patch.paths)
        ):
            raise ValueError("tree repair selection is stale or mismatched")
        target_path = checked_target.patch.paths[target_path_index]
        conflict_path = checked_conflict.patch.paths[conflict_path_index]
        # This is lattice membership, not a new obstacle or clearance predicate.  Complete target
        # membership is repeated when provenance derives the repair window.
        tuple(
            _cell(point, target_path.vertices[0], request.settings.grid_step_nm)
            for point in target_path.vertices
        )
        target_path_digest = _tree_path_digest(
            checked_target.candidate_id, target_path_index, target_path
        )
        conflict_path_digest = _tree_path_digest(
            checked_conflict.candidate_id, conflict_path_index, conflict_path
        )
        integrity_digest = _tree_selection_digest(
            snapshot_digest=snapshot.snapshot_digest,
            envelope_digest=envelope_digest,
            iteration=iteration,
            target_net_id=request.net_id,
            target_candidate_id=checked_target.candidate_id,
            target_path_count=len(checked_target.patch.paths),
            target_path_index=target_path_index,
            target_path_digest=target_path_digest,
            target_path_start=target_path.vertices[0],
            target_path_end=target_path.vertices[-1],
            conflict_net_id=checked_conflict.patch.net_id,
            conflict_candidate_id=checked_conflict.candidate_id,
            conflict_path_count=len(checked_conflict.patch.paths),
            conflict_path_index=conflict_path_index,
            conflict_path_digest=conflict_path_digest,
            responsibility_digest=responsibility_digest,
            responsibility_checks=responsibility_checks,
        )
        return CoordinatorTreeRepairSelection(
            snapshot.snapshot_digest,
            envelope_digest,
            iteration,
            request.net_id,
            checked_target.candidate_id,
            len(checked_target.patch.paths),
            target_path_index,
            target_path_digest,
            target_path.vertices[0],
            target_path.vertices[-1],
            checked_conflict.patch.net_id,
            checked_conflict.candidate_id,
            len(checked_conflict.patch.paths),
            conflict_path_index,
            conflict_path_digest,
            responsibility_digest,
            responsibility_checks,
            integrity_digest,
            _TREE_SELECTION_CAPABILITY,
        )
    except Exception as error:
        raise ValueError("tree repair selection is invalid") from error


def _tree_projection_cell_upper_bound(
    candidate: RouteCandidate,
    *,
    origin: PointNM,
    step: int,
    maximum: int,
) -> int:
    """Preflight conservative segment projection before any unit-cell enumeration."""

    count = 0
    for path in candidate.patch.paths:
        for start, end in pairwise(path.vertices):
            if start == end or (start.x != end.x and start.y != end.y):
                raise ValueError("tree repair conflict geometry is invalid")
            count += len(_axis_projection(start.x, end.x, origin.x, step)) * len(
                _axis_projection(start.y, end.y, origin.y, step)
            )
            if count > maximum:
                raise ValueError("tree repair projection exceeds its cell budget")
    return count


def _preflight_tree_conflict_projection(
    candidates: tuple[RouteCandidate, ...],
    *,
    origin: PointNM,
    step: int,
    maximum: int,
) -> int:
    """Bound the complete conflict population before projecting any one candidate."""

    remaining = maximum
    for candidate in candidates:
        remaining -= _tree_projection_cell_upper_bound(
            candidate,
            origin=origin,
            step=step,
            maximum=remaining,
        )
    return maximum - remaining


def _tree_untouched_projection_work_upper_bound(
    candidate: RouteCandidate,
    *,
    selected_path_index: int,
    origin: PointNM,
    step: int,
    bounds: PolicyBounds,
    maximum: int,
) -> int:
    """Preflight every untouched branch's repeated expansion without materializing cells.

    The runtime pass charges one unit for each innermost ``set.add`` attempt.  A projected
    centreline cell can expand to at most the full square ``(2 * radius + 1) ** 2``; multiplying
    that square by the compressed segment's projected-cell count is therefore a safe upper bound
    even when the square is clipped by ``bounds`` or overlaps an earlier insertion.  The arithmetic
    is O(compressed segments), so an adversarial path cannot make the preflight enumerate its
    window before the bound is known.
    """

    if type(maximum) is not int or not 0 <= maximum <= _MAX_PROJECTION_OBSTACLE_CHECKS:
        raise ValueError("tree repair projection maximum is invalid")
    _ = bounds
    radius = candidate.patch.width_nm // step
    expansion_area = (2 * radius + 1) ** 2
    work = 0
    for path_index, path in enumerate(candidate.patch.paths):
        if path_index == selected_path_index:
            continue
        for start, end in pairwise(path.vertices):
            if start == end or (start.x != end.x and start.y != end.y):
                raise ValueError("tree repair target geometry is invalid")
            projected_cells = len(_axis_projection(start.x, end.x, origin.x, step)) * len(
                _axis_projection(start.y, end.y, origin.y, step)
            )
            work += projected_cells * expansion_area
            if work > maximum:
                raise ValueError("tree repair projection exceeds its cell budget")
    return work


def _preflight_tree_untouched_projection(
    candidate: RouteCandidate,
    *,
    selected_path_index: int,
    origin: PointNM,
    step: int,
    bounds: PolicyBounds,
    maximum: int,
) -> int:
    """Return the cumulative untouched-tree expansion work before enumeration begins."""

    # The bound deliberately remains safe when individual expansion squares are clipped.
    return _tree_untouched_projection_work_upper_bound(
        candidate,
        selected_path_index=selected_path_index,
        origin=origin,
        step=step,
        bounds=bounds,
        maximum=maximum,
    )


def _tree_provenance_digest(
    *,
    selection_digest: str,
    snapshot_digest: str,
    envelope_digest: str,
    iteration: int,
    target_net_id: str,
    target_candidate_id: str,
    conflicting_candidate_ids: tuple[str, ...],
    grid_origin: PointNM,
    grid_step_nm: int,
    window: RepairWindowCandidate,
    start: tuple[int, int],
    end: tuple[int, int],
    blocked_cells: tuple[tuple[int, int], ...],
    projection_digest: str,
    projection_obstacle_checks: int,
) -> str:
    return _digest(
        {
            "blocked_cells": [list(item) for item in blocked_cells],
            "conflicting_candidate_ids": list(conflicting_candidate_ids),
            "end": list(end),
            "envelope_digest": envelope_digest,
            "grid_origin": [grid_origin.x, grid_origin.y],
            "grid_step_nm": grid_step_nm,
            "iteration": iteration,
            "projection_digest": projection_digest,
            "projection_obstacle_checks": projection_obstacle_checks,
            "schema": _TREE_PROVENANCE_SCHEMA,
            "selection_digest": selection_digest,
            "snapshot_digest": snapshot_digest,
            "start": list(start),
            "target_candidate_id": target_candidate_id,
            "target_net_id": target_net_id,
            "window": window.as_json(),
        }
    )


@dataclass(frozen=True, slots=True)
class CoordinatorTreeRepairProvenance:
    """Complete immutable projection for replacing one path of a multi-pin candidate."""

    selection: CoordinatorTreeRepairSelection
    snapshot_digest: str
    envelope_digest: str
    iteration: int
    target_net_id: str
    target_candidate_id: str
    conflicting_candidate_ids: tuple[str, ...]
    grid_origin: PointNM
    grid_step_nm: int
    window: RepairWindowCandidate
    start: tuple[int, int]
    end: tuple[int, int]
    blocked_cells: tuple[tuple[int, int], ...]
    projection_digest: str
    projection_obstacle_checks: int
    integrity_digest: str
    _capability: object

    @property
    def digest(self) -> str:
        return self.integrity_digest

    @property
    def target_path_count(self) -> int:
        return self.selection.target_path_count

    @property
    def target_path_index(self) -> int:
        return self.selection.target_path_index

    @property
    def target_path_digest(self) -> str:
        return self.selection.target_path_digest

    @property
    def target_path_start(self) -> PointNM:
        return self.selection.target_path_start

    @property
    def target_path_end(self) -> PointNM:
        return self.selection.target_path_end

    @property
    def responsibility_digest(self) -> str:
        return self.selection.responsibility_digest

    @property
    def responsibility_checks(self) -> int:
        return self.selection.responsibility_checks

    def _recomputed_digest(self) -> str:
        return _tree_provenance_digest(
            selection_digest=self.selection.digest,
            snapshot_digest=self.snapshot_digest,
            envelope_digest=self.envelope_digest,
            iteration=self.iteration,
            target_net_id=self.target_net_id,
            target_candidate_id=self.target_candidate_id,
            conflicting_candidate_ids=self.conflicting_candidate_ids,
            grid_origin=self.grid_origin,
            grid_step_nm=self.grid_step_nm,
            window=self.window,
            start=self.start,
            end=self.end,
            blocked_cells=self.blocked_cells,
            projection_digest=self.projection_digest,
            projection_obstacle_checks=self.projection_obstacle_checks,
        )

    def _is_coordinator_derived(self) -> bool:
        try:
            return (
                self._capability is _TREE_PROVENANCE_CAPABILITY
                and self.selection._is_coordinator_derived()
                and _SHA256.fullmatch(self.integrity_digest) is not None
                and self.integrity_digest == self._recomputed_digest()
            )
        except Exception:
            return False

    def local_request(self, settings: RepairTransactionSettings) -> LocalRepairRequest:
        if type(settings) is not RepairTransactionSettings or not self._is_coordinator_derived():
            raise ValueError("tree repair provenance is not coordinator-derived")
        return LocalRepairRequest(
            self.window,
            self.start,
            self.end,
            self.blocked_cells,
            settings.max_local_expansions,
            settings.max_window_cells,
        )


def derive_tree_repair_provenance(
    snapshot: object,
    request: object,
    target: object,
    conflicts: object,
    selection: object,
    *,
    envelope_digest: object,
    iteration: object,
    settings: object,
    cancelled: object = None,
    consumed_work: list[int] | None = None,
) -> CoordinatorTreeRepairProvenance:
    """Derive a bounded local request from one capability-bound multi-pin path selection."""

    projection_obstacle_checks = 0
    untouched_projection_work = [0]
    if (
        type(snapshot) is not BoardIRSnapshot
        or type(request) is not RouteRequest
        or type(target) is not RouteCandidate
        or type(conflicts) is not tuple
        or type(selection) is not CoordinatorTreeRepairSelection
        or type(envelope_digest) is not str
        or type(iteration) is not int
        or type(settings) is not RepairTransactionSettings
        or (cancelled is not None and not callable(cancelled))
        or (
            consumed_work is not None
            and (
                type(consumed_work) is not list
                or len(consumed_work) != 1
                or type(consumed_work[0]) is not int
                or consumed_work[0] != 0
            )
        )
    ):
        raise _TreeRepairProvenanceError(0, cancelled=False)
    if not _SHA256.fullmatch(envelope_digest) or not 1 <= iteration <= 32:
        raise _TreeRepairProvenanceError(0, cancelled=False)
    cancellation_check = cast(CancellationCheck | None, cancelled)
    try:
        verify_snapshot(snapshot)
        checked_target = _require_tree_candidate_binding(
            snapshot,
            target,
            layer_id=request.layer_id,
            grid_step_nm=request.settings.grid_step_nm,
        )
        if (
            not selection._is_coordinator_derived()
            or snapshot.snapshot_digest != request.board_revision
            or checked_target.patch.net_id != request.net_id
            or checked_target.settings != request.settings
            or checked_target.seed != request.seed
            or not 3 <= checked_target.pad_count <= _MAX_TREE_PADS
            or selection.snapshot_digest != snapshot.snapshot_digest
            or selection.envelope_digest != envelope_digest
            or selection.iteration != iteration
            or selection.target_net_id != request.net_id
            or selection.target_candidate_id != checked_target.candidate_id
            or selection.target_path_count != len(checked_target.patch.paths)
            or not 0 <= selection.target_path_index < len(checked_target.patch.paths)
        ):
            raise ValueError("tree repair provenance is stale or mismatched")
        selected_path = checked_target.patch.paths[selection.target_path_index]
        if (
            selection.target_path_digest
            != _tree_path_digest(
                checked_target.candidate_id, selection.target_path_index, selected_path
            )
            or selection.target_path_start != selected_path.vertices[0]
            or selection.target_path_end != selected_path.vertices[-1]
        ):
            raise ValueError("tree repair target path selection is invalid")

        if not 1 <= len(conflicts) <= _MAX_TREE_CONFLICTS or any(
            type(item) is not RouteCandidate for item in conflicts
        ):
            raise ValueError("tree repair conflicts are invalid")
        others = tuple(sorted(conflicts, key=lambda item: item.candidate_id))
        if (
            checked_target.candidate_id in {item.candidate_id for item in others}
            or len({item.candidate_id for item in others}) != len(others)
            or len({item.patch.net_id for item in others}) != len(others)
        ):
            raise ValueError("tree repair conflicts are invalid")
        for item in others:
            _require_tree_candidate_binding(
                snapshot,
                item,
                layer_id=request.layer_id,
                grid_step_nm=request.settings.grid_step_nm,
            )
            if item.patch.net_id == request.net_id:
                raise ValueError("tree repair conflicts are invalid")
        responsible = tuple(
            item for item in others if item.candidate_id == selection.conflict_candidate_id
        )
        if len(responsible) != 1:
            raise ValueError("tree repair responsible conflict is missing")
        responsible_candidate = responsible[0]
        if (
            selection.conflict_net_id != responsible_candidate.patch.net_id
            or selection.conflict_path_count != len(responsible_candidate.patch.paths)
            or not 0 <= selection.conflict_path_index < len(responsible_candidate.patch.paths)
        ):
            raise ValueError("tree repair responsible conflict is invalid")
        responsible_path = responsible_candidate.patch.paths[selection.conflict_path_index]
        if selection.conflict_path_digest != _tree_path_digest(
            responsible_candidate.candidate_id,
            selection.conflict_path_index,
            responsible_path,
        ):
            raise ValueError("tree repair responsible path is invalid")

        origin = selection.target_path_start
        start = _cell(selected_path.vertices[0], origin, request.settings.grid_step_nm)
        end = _cell(selected_path.vertices[-1], origin, request.settings.grid_step_nm)
        all_bounds = (
            _strict_candidate_bounds(checked_target, origin, request.settings.grid_step_nm),
            *(_candidate_bounds(item, origin, request.settings.grid_step_nm) for item in others),
        )
        exclusion_radius = max(
            _conflict_exclusion_radius_cells(snapshot, request, checked_target, item)
            for item in others
        )
        min_x = min(item[0] for item in all_bounds)
        min_y = min(item[1] for item in all_bounds)
        max_x = max(item[2] for item in all_bounds)
        max_y = max(item[3] for item in all_bounds)
        margin = exclusion_radius + 1
        bounds = PolicyBounds(min_x - margin, min_y - margin, max_x + margin, max_y + margin)
        projection_area = (bounds.max_x - bounds.min_x + 1) * (bounds.max_y - bounds.min_y + 1)
        if projection_area > settings.max_projection_cells:
            raise ValueError("tree repair projection exceeds its cell budget")
        conflict_projection_work = _preflight_tree_conflict_projection(
            others,
            origin=origin,
            step=request.settings.grid_step_nm,
            maximum=settings.max_projection_cells,
        )
        untouched_projection_upper_bound = _preflight_tree_untouched_projection(
            checked_target,
            selected_path_index=selection.target_path_index,
            origin=origin,
            step=request.settings.grid_step_nm,
            bounds=bounds,
            maximum=settings.max_projection_cells - conflict_projection_work,
        )
        untouched_projection_budget = settings.max_projection_cells - conflict_projection_work

        try:
            blocked = _expanded_untouched_tree_cells(
                checked_target,
                selected_path_index=selection.target_path_index,
                origin=origin,
                step=request.settings.grid_step_nm,
                bounds=bounds,
                maximum=untouched_projection_budget,
                cancelled=cancellation_check,
                consumed_work=untouched_projection_work,
            )
        except _TreeUntouchedProjectionError as error:
            raise _TreeRepairProvenanceError(
                error.projection_work,
                cancelled=error.cancelled,
            ) from error
        if untouched_projection_work[0] > untouched_projection_upper_bound:
            raise ValueError("tree repair projection accounting is invalid")
        if consumed_work is not None:
            consumed_work[0] = untouched_projection_work[0]
        blocked.discard(start)
        blocked.discard(end)
        for item in others:
            blocked.update(
                _expanded_conflict_cells(
                    item,
                    origin=origin,
                    step=request.settings.grid_step_nm,
                    radius=_conflict_exclusion_radius_cells(
                        snapshot, request, checked_target, item
                    ),
                    bounds=bounds,
                )
            )
        try:
            board_blocked, projection_obstacle_checks = _project_board_ir_blocked_cells(
                snapshot,
                request,
                origin=origin,
                bounds=bounds,
                settings=settings,
                cancelled=cancellation_check,
            )
        except _BoardIRProjectionError as error:
            raise _TreeRepairProvenanceError(
                untouched_projection_work[0] + error.obstacle_checks,
                cancelled=error.cancelled,
            ) from error
        blocked.update(board_blocked)
        if start in blocked or end in blocked:
            raise ValueError("tree repair provenance endpoint is physically unavailable")
        blocked_cells = tuple(sorted(blocked))
        window = RepairWindowCandidate(request.net_id, bounds, len(others))
        projection_digest = _digest(
            {
                "blocked_cells": [list(item) for item in blocked_cells],
                "board_ir_snapshot_digest": snapshot.snapshot_digest,
                "obstacle_checks": projection_obstacle_checks,
                "schema": _TREE_PROJECTION_SCHEMA,
                "selection_digest": selection.digest,
            }
        )
        conflicting_candidate_ids = tuple(item.candidate_id for item in others)
        integrity_digest = _tree_provenance_digest(
            selection_digest=selection.digest,
            snapshot_digest=snapshot.snapshot_digest,
            envelope_digest=envelope_digest,
            iteration=iteration,
            target_net_id=request.net_id,
            target_candidate_id=checked_target.candidate_id,
            conflicting_candidate_ids=conflicting_candidate_ids,
            grid_origin=origin,
            grid_step_nm=request.settings.grid_step_nm,
            window=window,
            start=start,
            end=end,
            blocked_cells=blocked_cells,
            projection_digest=projection_digest,
            projection_obstacle_checks=projection_obstacle_checks,
        )
        return CoordinatorTreeRepairProvenance(
            selection=selection,
            snapshot_digest=snapshot.snapshot_digest,
            envelope_digest=envelope_digest,
            iteration=iteration,
            target_net_id=request.net_id,
            target_candidate_id=checked_target.candidate_id,
            conflicting_candidate_ids=conflicting_candidate_ids,
            grid_origin=origin,
            grid_step_nm=request.settings.grid_step_nm,
            window=window,
            start=start,
            end=end,
            blocked_cells=blocked_cells,
            projection_digest=projection_digest,
            projection_obstacle_checks=projection_obstacle_checks,
            integrity_digest=integrity_digest,
            _capability=_TREE_PROVENANCE_CAPABILITY,
        )
    except _TreeRepairProvenanceError:
        raise
    except Exception as error:
        raise _TreeRepairProvenanceError(
            untouched_projection_work[0] + projection_obstacle_checks,
            cancelled=False,
        ) from error


__all__ = ["CoordinatorRepairProvenance", "derive_repair_provenance"]
