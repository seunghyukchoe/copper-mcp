"""Internal Board-IR acceptance gate for a coordinator-derived two-pin route path.

This module deliberately validates a candidate but never constructs, publishes, stores, applies,
or serializes one.  It reuses the reference router's exact, integer-only obstacle preparation and
unit-edge predicate so a future deterministic coordinator can prove that a locally derived lattice
path has not skipped Board IR clearance authority.  It is not an MCP or policy boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import pairwise
from typing import Final

from copper_mcp.board_ir import BoardIRSnapshot, PointNM
from copper_mcp.routing.astar import (
    _edge_is_legal,
    _ExpectedFailureError,
    _prepare,
    _WorkBudget,
    verify_candidate_id,
)
from copper_mcp.routing.contracts import (
    SINGLE_PATH_ORDERING,
    AStarSettings,
    RouteCandidate,
    RouteCost,
    RouteFailureCode,
    RouteMetrics,
    RoutePatch,
    RoutePath,
    RouteRequest,
)

CancellationCheck = Callable[[], bool]
_MAX_EDGE_CHECKS: Final = 4_096
_MAX_OBSTACLE_CHECKS: Final = 10_000_000
_CANCELLATION_CADENCE: Final = 64
_MAX_RAW_TEXT_LENGTH: Final = 512
_MAX_RAW_INTEGER: Final = (1 << 53) - 1


class CandidatePathValidationFailure(StrEnum):
    """Fixed, redacted terminal reasons for the internal acceptance gate."""

    INVALID_REQUEST = "invalid_request"
    INVALID_CANDIDATE = "invalid_candidate"
    STALE_REVISION = "stale_revision"
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"
    INFEASIBLE = "infeasible"
    OBSTACLE_VIOLATION = "obstacle_violation"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"


@dataclass(frozen=True, slots=True)
class CandidatePathValidationResult:
    """Redacted, bounded evidence from one candidate-path acceptance attempt."""

    edge_checks: int
    obstacle_checks: int
    failure: CandidatePathValidationFailure | None = None

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("candidate path edge checks", self.edge_checks, _MAX_EDGE_CHECKS),
            ("candidate path obstacle checks", self.obstacle_checks, _MAX_OBSTACLE_CHECKS),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise ValueError(f"{name} are outside the supported range")
        if self.failure is not None and not isinstance(
            self.failure, CandidatePathValidationFailure
        ):
            raise ValueError("candidate path validation failure is malformed")

    @property
    def accepted(self) -> bool:
        """Return true only after the complete Board-IR path check has succeeded."""

        return self.failure is None

    @property
    def diagnostic(self) -> str | None:
        """Return a stable non-echoing diagnostic; never expose board or path content."""

        messages = {
            CandidatePathValidationFailure.INVALID_REQUEST: (
                "candidate path validation input is invalid"
            ),
            CandidatePathValidationFailure.INVALID_CANDIDATE: (
                "candidate path does not bind the request"
            ),
            CandidatePathValidationFailure.STALE_REVISION: (
                "candidate path is stale against the board revision"
            ),
            CandidatePathValidationFailure.UNSUPPORTED_GEOMETRY: (
                "candidate path uses geometry outside the validator subset"
            ),
            CandidatePathValidationFailure.INFEASIBLE: (
                "the immutable board cannot accept this candidate path"
            ),
            CandidatePathValidationFailure.OBSTACLE_VIOLATION: (
                "candidate path violates the Board IR obstacle authority"
            ),
            CandidatePathValidationFailure.BUDGET_EXHAUSTED: (
                "candidate path validation exhausted its bounded work budget"
            ),
            CandidatePathValidationFailure.CANCELLED: "candidate path validation was cancelled",
            CandidatePathValidationFailure.DEADLINE_EXCEEDED: (
                "candidate path validation exceeded its cooperative deadline"
            ),
        }
        return None if self.failure is None else messages[self.failure]


@dataclass(slots=True)
class _StopChecks:
    """Coordinator-owned cooperative stop boundary with deterministic terminal precedence."""

    cancelled: CancellationCheck | None
    deadline_check: CancellationCheck | None
    observed: CandidatePathValidationFailure | None = None

    def check(self) -> CandidatePathValidationFailure | None:
        if self.observed is not None:
            return self.observed
        if _callback_true(self.cancelled):
            self.observed = CandidatePathValidationFailure.CANCELLED
        elif _callback_true(self.deadline_check):
            self.observed = CandidatePathValidationFailure.DEADLINE_EXCEEDED
        return self.observed

    def __call__(self) -> bool:
        return self.check() is not None


def _callback_true(callback: CancellationCheck | None) -> bool:
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception:  # pragma: no cover - external cooperative hooks fail closed
        return True


def _canonical_settings(value: object) -> AStarSettings | None:
    if type(value) is not AStarSettings:
        return None
    try:
        return AStarSettings(
            grid_step_nm=value.grid_step_nm,
            bend_penalty_nm=value.bend_penalty_nm,
            proximity_penalty_nm=value.proximity_penalty_nm,
            max_grid_nodes=value.max_grid_nodes,
            max_expansions=value.max_expansions,
            max_obstacles=value.max_obstacles,
            max_obstacle_checks=value.max_obstacle_checks,
        )
    except Exception:
        return None


def _canonical_request(value: object, stop: _StopChecks) -> RouteRequest | None:
    if stop.check() is not None:
        return None
    if type(value) is not RouteRequest:
        return None
    settings = _canonical_settings(value.settings)
    if settings is None:
        return None
    try:
        return RouteRequest(
            board_revision=value.board_revision,
            net_id=value.net_id,
            layer_id=value.layer_id,
            seed=value.seed,
            settings=settings,
        )
    except Exception:
        return None


def _raw_text(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= _MAX_RAW_TEXT_LENGTH


def _raw_integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_RAW_INTEGER


def _raw_point(value: object) -> bool:
    return (
        type(value) is PointNM
        and type(value.x) is int
        and -_MAX_RAW_INTEGER <= value.x <= _MAX_RAW_INTEGER
        and type(value.y) is int
        and -_MAX_RAW_INTEGER <= value.y <= _MAX_RAW_INTEGER
    )


def _preflight_candidate(
    value: object,
    *,
    max_path_edges: int,
    stop: _StopChecks,
) -> CandidatePathValidationFailure | None:
    """Reject oversized/type-confused candidates before reconstruction or hashing.

    The cap matches the local exact-repair window ceiling. It intentionally constrains this
    integration prerequisite more tightly than the general A* router so adversarial geometry
    cannot turn identity verification into an unmetered large-input operation.
    """

    if type(value) is not RouteCandidate:
        return CandidatePathValidationFailure.INVALID_CANDIDATE
    try:
        if stop.check() is not None:
            return stop.observed
        if (
            not _raw_text(value.candidate_id)
            or not _raw_text(value.base_revision)
            or not _raw_text(value.start_pad_id)
            or not _raw_text(value.end_pad_id)
            or not _raw_text(value.router_version)
            or not _raw_text(value.policy)
            or not _raw_text(value.ordering_policy)
            or not _raw_integer(value.seed)
            or not _raw_integer(value.pad_count)
            or type(value.patch) is not RoutePatch
            or type(value.cost) is not RouteCost
            or type(value.metrics) is not RouteMetrics
            or type(value.settings) is not AStarSettings
        ):
            return CandidatePathValidationFailure.INVALID_CANDIDATE
        patch = value.patch
        if (
            not _raw_text(patch.net_id)
            or not _raw_text(patch.layer_id)
            or not _raw_integer(patch.width_nm)
            or type(patch.paths) is not tuple
            or len(patch.paths) != 1
            or type(patch.paths[0]) is not RoutePath
            or type(patch.paths[0].vertices) is not tuple
        ):
            return CandidatePathValidationFailure.INVALID_CANDIDATE
        vertices = patch.paths[0].vertices
        if not 2 <= len(vertices) <= max_path_edges + 1:
            return CandidatePathValidationFailure.BUDGET_EXHAUSTED
        for index, point in enumerate(vertices):
            if index % _CANCELLATION_CADENCE == 0 and stop.check() is not None:
                return stop.observed
            if not _raw_point(point):
                return CandidatePathValidationFailure.INVALID_CANDIDATE
        scalars = (
            value.cost.length_nm,
            value.cost.bend_count,
            value.cost.bend_cost_nm,
            value.cost.proximity_steps,
            value.cost.proximity_cost_nm,
            value.cost.via_cost_nm,
            value.cost.total_cost_nm,
            value.metrics.hard_internal_violations,
            value.metrics.unrouted_connections,
            value.metrics.vias,
            value.metrics.wire_length_nm,
            value.metrics.expanded_states,
            value.metrics.peak_frontier_states,
            value.metrics.obstacle_checks,
            value.settings.grid_step_nm,
            value.settings.bend_penalty_nm,
            value.settings.proximity_penalty_nm,
            value.settings.max_grid_nodes,
            value.settings.max_expansions,
            value.settings.max_obstacles,
            value.settings.max_obstacle_checks,
        )
        if not all(_raw_integer(item) for item in scalars):
            return CandidatePathValidationFailure.INVALID_CANDIDATE
    except Exception:
        return CandidatePathValidationFailure.INVALID_CANDIDATE
    return stop.check()


def _canonical_candidate(value: object, stop: _StopChecks) -> RouteCandidate | None:
    """Reconstruct a candidate before trusting any frozen dataclass field.

    This is a type/shape boundary, not an origin claim: an equal, valid current value is treated as
    a freshly submitted candidate and must still pass revision, endpoint, width, and geometry
    validation below.
    """

    if stop.check() is not None or type(value) is not RouteCandidate:
        return None
    try:
        if type(value.patch) is not RoutePatch or type(value.cost) is not RouteCost:
            return None
        if type(value.metrics) is not RouteMetrics or type(value.settings) is not AStarSettings:
            return None
        if (
            type(value.patch.paths) is not tuple
            or len(value.patch.paths) != 1
            or type(value.patch.paths[0]) is not RoutePath
            or type(value.patch.paths[0].vertices) is not tuple
        ):
            return None
        vertices: list[PointNM] = []
        for index, point in enumerate(value.patch.paths[0].vertices):
            if index % _CANCELLATION_CADENCE == 0 and stop.check() is not None:
                return None
            if type(point) is not PointNM:
                return None
            vertices.append(PointNM(point.x, point.y))
        if stop.check() is not None:
            return None
        patch = RoutePatch(
            net_id=value.patch.net_id,
            layer_id=value.patch.layer_id,
            width_nm=value.patch.width_nm,
            paths=(RoutePath(vertices=tuple(vertices)),),
        )
        cost = RouteCost(
            length_nm=value.cost.length_nm,
            bend_count=value.cost.bend_count,
            bend_cost_nm=value.cost.bend_cost_nm,
            proximity_steps=value.cost.proximity_steps,
            proximity_cost_nm=value.cost.proximity_cost_nm,
            via_cost_nm=value.cost.via_cost_nm,
            total_cost_nm=value.cost.total_cost_nm,
        )
        metrics = RouteMetrics(
            hard_internal_violations=value.metrics.hard_internal_violations,
            unrouted_connections=value.metrics.unrouted_connections,
            vias=value.metrics.vias,
            wire_length_nm=value.metrics.wire_length_nm,
            expanded_states=value.metrics.expanded_states,
            peak_frontier_states=value.metrics.peak_frontier_states,
            obstacle_checks=value.metrics.obstacle_checks,
        )
        settings = _canonical_settings(value.settings)
        if settings is None:
            return None
        candidate = RouteCandidate(
            candidate_id=value.candidate_id,
            base_revision=value.base_revision,
            start_pad_id=value.start_pad_id,
            end_pad_id=value.end_pad_id,
            patch=patch,
            cost=cost,
            metrics=metrics,
            settings=settings,
            router_version=value.router_version,
            policy=value.policy,
            seed=value.seed,
            pad_count=value.pad_count,
            ordering_policy=value.ordering_policy,
        )
        if stop.check() is not None:
            return None
        verify_candidate_id(candidate)
        if stop.check() is not None:
            return None
    except Exception:
        return None
    return candidate


def _preparation_independent_binding_failure(
    request: RouteRequest,
    candidate: RouteCandidate,
) -> CandidatePathValidationFailure | None:
    """Reject request-bound candidate claims before Board-IR preparation.

    ``_prepare`` remains the authority for canonical snapshot verification and every
    problem-derived fact. These comparisons need neither authority: a candidate that disagrees
    with its declared request cannot become valid because a snapshot happens to be routable.
    Keeping them ahead of preparation prevents a stale or mismatched untrusted candidate from
    consuming the Board-IR obstacle budget.
    """

    if (
        candidate.base_revision != request.board_revision
        or candidate.patch.net_id != request.net_id
        or candidate.patch.layer_id != request.layer_id
        or candidate.settings != request.settings
        or candidate.seed != request.seed
        or candidate.pad_count != 2
        or candidate.ordering_policy != SINGLE_PATH_ORDERING
        or len(candidate.patch.paths) != 1
        or candidate.metrics.vias != 0
    ):
        return CandidatePathValidationFailure.INVALID_CANDIDATE
    return None


def _failure_from_router(
    error: _ExpectedFailureError, stop: _StopChecks
) -> CandidatePathValidationFailure:
    if error.code is RouteFailureCode.CANCELLED:
        return stop.observed or CandidatePathValidationFailure.CANCELLED
    if error.code is RouteFailureCode.STALE_REVISION:
        return CandidatePathValidationFailure.STALE_REVISION
    if error.code in {
        RouteFailureCode.GRID_BUDGET_EXCEEDED,
        RouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
        RouteFailureCode.SEARCH_BUDGET_EXCEEDED,
    }:
        return CandidatePathValidationFailure.BUDGET_EXHAUSTED
    if error.code is RouteFailureCode.NO_PATH:
        return CandidatePathValidationFailure.INFEASIBLE
    if error.code is RouteFailureCode.UNSUPPORTED_GEOMETRY:
        return CandidatePathValidationFailure.UNSUPPORTED_GEOMETRY
    return CandidatePathValidationFailure.INVALID_REQUEST


def _result(
    work: _WorkBudget | None,
    edge_checks: int,
    failure: CandidatePathValidationFailure | None,
) -> CandidatePathValidationResult:
    return CandidatePathValidationResult(
        edge_checks=edge_checks,
        obstacle_checks=0 if work is None else work.obstacle_checks,
        failure=failure,
    )


def validate_candidate_path(
    snapshot: object,
    request: object,
    candidate: object,
    *,
    max_obstacle_checks: object,
    max_path_edges: object,
    cancelled: object = None,
    deadline_check: object = None,
) -> CandidatePathValidationResult:
    """Validate one immutable two-pin candidate path against the reference Board-IR model.

    The caller supplies no geometry other than the candidate being checked, and no model, policy,
    board mutation, or apply capability is accepted. ``max_obstacle_checks`` is a stricter
    coordinator-owned ceiling over reference preparation and every unit-edge check;
    ``max_path_edges`` separately bounds decomposition of compressed path segments. Deadline and
    cancellation hooks are cooperative boundaries: a true value or callback exception fails
    closed and publishes no geometry.
    """

    if (
        type(snapshot) is not BoardIRSnapshot
        or (cancelled is not None and not callable(cancelled))
        or (deadline_check is not None and not callable(deadline_check))
        or isinstance(max_obstacle_checks, bool)
        or not isinstance(max_obstacle_checks, int)
        or not 1 <= max_obstacle_checks <= _MAX_OBSTACLE_CHECKS
        or isinstance(max_path_edges, bool)
        or not isinstance(max_path_edges, int)
        or not 1 <= max_path_edges <= _MAX_EDGE_CHECKS
    ):
        return _result(None, 0, CandidatePathValidationFailure.INVALID_REQUEST)

    stop = _StopChecks(cancelled=cancelled, deadline_check=deadline_check)
    stopped = stop.check()
    if stopped is not None:
        return _result(None, 0, stopped)

    checked_request = _canonical_request(request, stop)
    if checked_request is None:
        return _result(
            None,
            0,
            stop.observed or CandidatePathValidationFailure.INVALID_REQUEST,
        )
    if (
        max_obstacle_checks > checked_request.settings.max_obstacle_checks
        or max_path_edges > checked_request.settings.max_grid_nodes
    ):
        return _result(None, 0, CandidatePathValidationFailure.INVALID_REQUEST)
    if checked_request.board_revision != snapshot.snapshot_digest:
        return _result(None, 0, CandidatePathValidationFailure.STALE_REVISION)

    raw_candidate_failure = _preflight_candidate(
        candidate,
        max_path_edges=max_path_edges,
        stop=stop,
    )
    if raw_candidate_failure is not None:
        return _result(None, 0, raw_candidate_failure)
    checked_candidate = _canonical_candidate(candidate, stop)
    if checked_candidate is None:
        return _result(
            None,
            0,
            stop.observed or CandidatePathValidationFailure.INVALID_CANDIDATE,
        )
    binding_failure = _preparation_independent_binding_failure(
        checked_request,
        checked_candidate,
    )
    if binding_failure is not None:
        return _result(None, 0, binding_failure)

    bounded_request = replace(
        checked_request,
        settings=replace(checked_request.settings, max_obstacle_checks=max_obstacle_checks),
    )
    work = _WorkBudget(settings=bounded_request.settings, cancelled=stop)
    try:
        problem = _prepare(snapshot, bounded_request, work)
    except _ExpectedFailureError as error:
        return _result(work, 0, _failure_from_router(error, stop))
    except Exception:  # pragma: no cover - the private reference boundary must not escape
        return _result(work, 0, CandidatePathValidationFailure.INVALID_REQUEST)

    if (
        checked_candidate.patch.width_nm != problem.width_nm
        or checked_candidate.start_pad_id != problem.start_pad.id
        or checked_candidate.end_pad_id != problem.end_pad.id
    ):
        return _result(work, 0, CandidatePathValidationFailure.INVALID_CANDIDATE)

    path = checked_candidate.patch.paths[0]
    step = checked_request.settings.grid_step_nm
    origin = problem.start_pad.center
    nodes: list[tuple[int, int]] = []
    for index, point in enumerate(path.vertices):
        if index % _CANCELLATION_CADENCE == 0:
            stopped = stop.check()
            if stopped is not None:
                return _result(work, 0, stopped)
        delta_x = point.x - origin.x
        delta_y = point.y - origin.y
        if delta_x % step != 0 or delta_y % step != 0:
            return _result(work, 0, CandidatePathValidationFailure.UNSUPPORTED_GEOMETRY)
        nodes.append((delta_x // step, delta_y // step))
    if nodes[0] not in problem.source_nodes or nodes[-1] not in problem.target_nodes:
        return _result(work, 0, CandidatePathValidationFailure.INVALID_CANDIDATE)

    total_edges = 0
    for start, end in pairwise(path.vertices):
        stopped = stop.check()
        if stopped is not None:
            return _result(work, 0, stopped)
        total_edges += (abs(end.x - start.x) + abs(end.y - start.y)) // step
        if total_edges > max_path_edges:
            return _result(work, 0, CandidatePathValidationFailure.BUDGET_EXHAUSTED)
    edge_checks = 0
    seen = {path.vertices[0]}
    try:
        for start, end in pairwise(path.vertices):
            delta_x = (end.x - start.x) // step
            delta_y = (end.y - start.y) // step
            unit_x = 0 if delta_x == 0 else (1 if delta_x > 0 else -1)
            unit_y = 0 if delta_y == 0 else (1 if delta_y > 0 else -1)
            current = start
            for _ in range(abs(delta_x) + abs(delta_y)):
                stopped = stop.check()
                if stopped is not None:
                    return _result(work, edge_checks, stopped)
                next_point = PointNM(current.x + unit_x * step, current.y + unit_y * step)
                if next_point in seen:
                    return _result(
                        work, edge_checks, CandidatePathValidationFailure.INVALID_CANDIDATE
                    )
                seen.add(next_point)
                if not _edge_is_legal(current, next_point, problem, work):
                    return _result(
                        work, edge_checks + 1, CandidatePathValidationFailure.OBSTACLE_VIOLATION
                    )
                edge_checks += 1
                current = next_point
    except _ExpectedFailureError as error:
        return _result(work, edge_checks, _failure_from_router(error, stop))
    except Exception:  # pragma: no cover - a hostile candidate must fail closed
        return _result(work, edge_checks, CandidatePathValidationFailure.INVALID_CANDIDATE)
    stopped = stop.check()
    if stopped is not None:
        return _result(work, edge_checks, stopped)
    return _result(work, edge_checks, None)


__all__ = [
    "CandidatePathValidationFailure",
    "CandidatePathValidationResult",
    "validate_candidate_path",
]
