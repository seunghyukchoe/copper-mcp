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

from copper_mcp.board_ir import BoardIRSnapshot, PointNM, verify_snapshot
from copper_mcp.routing.astar import verify_candidate_id
from copper_mcp.routing.contracts import RouteCandidate, RouteRequest
from copper_mcp.routing.policy import PolicyBounds, RepairWindowCandidate
from copper_mcp.routing.repair import LocalRepairRequest, RepairTransactionSettings

_SCHEMA = "copper-mcp.negotiated-local-repair-provenance.v1"
_PROJECTION_SCHEMA = "copper-mcp.negotiated-local-repair-projection.v2"
_SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
_CAPABILITY = object()


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


def _candidate_bounds(
    candidate: RouteCandidate, origin: PointNM, step: int
) -> tuple[int, int, int, int]:
    """Return a path's lattice bounds without enumerating a single unit cell."""

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


def _candidate_cells(candidate: RouteCandidate, origin: PointNM, step: int) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for path in candidate.patch.paths:
        for start, end in pairwise(path.vertices):
            left, right = _cell(start, origin, step), _cell(end, origin, step)
            dx, dy = right[0] - left[0], right[1] - left[1]
            if (dx and dy) or (not dx and not dy):
                raise ValueError("candidate geometry is not orthogonal")
            distance = abs(dx) + abs(dy)
            unit = (0 if dx == 0 else dx // distance, 0 if dy == 0 else dy // distance)
            cells.update(
                (left[0] + unit[0] * index, left[1] + unit[1] * index)
                for index in range(distance + 1)
            )
    return cells


def _project_board_ir_blocked_cells(
    snapshot: BoardIRSnapshot,
    request: RouteRequest,
    *,
    origin: PointNM,
    bounds: PolicyBounds,
    settings: RepairTransactionSettings,
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
    work = _WorkBudget(settings=bounded_request.settings, cancelled=None)
    try:
        problem = _prepare(snapshot, bounded_request, work)
    except _ExpectedFailureError as error:
        raise ValueError("repair provenance Board IR projection is invalid") from error
    except Exception as error:  # pragma: no cover - defensive private reference boundary
        raise ValueError("repair provenance Board IR projection is invalid") from error
    if work.obstacle_checks > settings.max_validator_obstacle_checks:
        raise ValueError("repair provenance projection exceeds its obstacle budget")

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
                    raise ValueError(
                        "repair provenance projection exceeds its obstacle budget"
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
            _candidate_bounds(target, origin, request.settings.grid_step_nm),
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


__all__ = ["CoordinatorRepairProvenance", "derive_repair_provenance"]
