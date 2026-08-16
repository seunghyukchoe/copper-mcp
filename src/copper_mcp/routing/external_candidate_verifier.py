"""Bounded disposer for externally generated single-layer route candidates.

The external document is untrusted geometry, not a :class:`RouteCandidate`.  This module accepts
only a closed, versioned document, rebuilds candidate identity and all routing metadata from
coordinator-owned state, and delegates Board-IR legality to ``validate_candidate_path``.  It has
no mutation, apply, MCP, CLI, persistence, or benchmark authority.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import pairwise
from typing import Final

from copper_mcp.board_ir import BoardIRContent, BoardIRSnapshot, PointNM, verify_snapshot
from copper_mcp.routing.astar import canonical_candidate_bytes
from copper_mcp.routing.candidate_path_validator import (
    EXTERNAL_PATCH_TREE_ORDERING,
    CandidatePathValidationFailure,
    validate_candidate_patch_with_exact_off_grid_obstacle_fallback,
    validate_candidate_path_with_exact_off_grid_obstacle_fallback,
)
from copper_mcp.routing.contracts import (
    SINGLE_PATH_ORDERING,
    AStarSettings,
    RouteCandidate,
    RouteCost,
    RouteMetrics,
    RoutePatch,
    RoutePath,
    RouteRequest,
)

CancellationCheck = Callable[[], bool]

EXTERNAL_ROUTE_CANDIDATE_SCHEMA: Final = "copper-mcp/external-route-candidate/v1"
EXTERNAL_ROUTE_PATCH_SCHEMA: Final = "copper-mcp/external-route-patch/v2"
_JSON_SAFE_INTEGER: Final = (1 << 53) - 1
_MAX_SEGMENTS: Final = 4_096
_EMPTY_DIGEST: Final = f"sha256:{'0' * 64}"
_DOCUMENT_KEYS: Final = frozenset(
    {"schema", "problem_revision", "start_pad_id", "end_pad_id", "segments", "vias"}
)
_PATCH_DOCUMENT_KEYS: Final = frozenset(
    {"schema", "problem_revision", "start_pad_id", "end_pad_id", "paths", "vias"}
)
_PATH_KEYS: Final = frozenset({"segments"})
_SEGMENT_KEYS: Final = frozenset({"layer_id", "width_nm", "start", "end"})
_POINT_KEYS: Final = frozenset({"x_nm", "y_nm"})
_VIA_KEYS: Final = frozenset({"start_layer_id", "end_layer_id", "at"})


class ExternalCandidateFailure(StrEnum):
    """Closed, non-echoing refusal taxonomy for the external disposer."""

    INVALID_REQUEST = "invalid_request"
    INVALID_CANDIDATE = "invalid_candidate"
    STALE_REVISION = "stale_revision"
    DISCONTINUOUS_PATH = "discontinuous_path"
    ENDPOINT_MISMATCH = "endpoint_mismatch"
    UNDECLARED_LAYER = "undeclared_layer"
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"
    INFEASIBLE = "infeasible"
    OBSTACLE_VIOLATION = "obstacle_violation"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"


_DIAGNOSTICS: Final[dict[ExternalCandidateFailure, str]] = {
    ExternalCandidateFailure.INVALID_REQUEST: "external candidate verification input is invalid",
    ExternalCandidateFailure.INVALID_CANDIDATE: "external candidate is invalid",
    ExternalCandidateFailure.STALE_REVISION: "external candidate is stale",
    ExternalCandidateFailure.DISCONTINUOUS_PATH: "external candidate path is discontinuous",
    ExternalCandidateFailure.ENDPOINT_MISMATCH: "external candidate endpoints do not match",
    ExternalCandidateFailure.UNDECLARED_LAYER: "external candidate names an undeclared layer",
    ExternalCandidateFailure.UNSUPPORTED_GEOMETRY: ("external candidate uses unsupported geometry"),
    ExternalCandidateFailure.INFEASIBLE: "the immutable board cannot accept this candidate",
    ExternalCandidateFailure.OBSTACLE_VIOLATION: (
        "external candidate violates the Board IR obstacle authority"
    ),
    ExternalCandidateFailure.BUDGET_EXCEEDED: (
        "external candidate verification exhausted its bounded work budget"
    ),
    ExternalCandidateFailure.CANCELLED: "external candidate verification was cancelled",
    ExternalCandidateFailure.DEADLINE_EXCEEDED: (
        "external candidate verification exceeded its cooperative deadline"
    ),
}


@dataclass(frozen=True, slots=True)
class ExternalCandidateVerificationResult:
    """Redacted disposer result; accepted geometry remains internal and immutable."""

    status: str
    candidate_id: str | None
    failure: ExternalCandidateFailure | None
    diagnostic: str | None
    segment_count: int
    edge_checks: int
    obstacle_checks: int
    physical_validation: str = "not_run"

    def __post_init__(self) -> None:
        if self.status not in {"accepted", "refused"} or self.physical_validation != "not_run":
            raise ValueError("external candidate verification result is malformed")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000_000
            for value in (self.segment_count, self.edge_checks, self.obstacle_checks)
        ):
            raise ValueError("external candidate verification counts are malformed")
        if self.status == "accepted":
            if self.candidate_id is None or self.failure is not None or self.diagnostic is not None:
                raise ValueError("accepted external candidate result is malformed")
        elif (
            self.candidate_id is not None
            or not isinstance(self.failure, ExternalCandidateFailure)
            or self.diagnostic != _DIAGNOSTICS[self.failure]
        ):
            raise ValueError("refused external candidate result is malformed")

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "physical_validation": self.physical_validation,
            "segment_count": self.segment_count,
            "edge_checks": self.edge_checks,
            "obstacle_checks": self.obstacle_checks,
        }
        if self.accepted:
            payload["candidate_id"] = self.candidate_id
        else:
            payload["code"] = None if self.failure is None else self.failure.value
            payload["diagnostic"] = self.diagnostic
        return payload


def _refused(
    failure: ExternalCandidateFailure,
    *,
    segment_count: int = 0,
    edge_checks: int = 0,
    obstacle_checks: int = 0,
) -> ExternalCandidateVerificationResult:
    return ExternalCandidateVerificationResult(
        status="refused",
        candidate_id=None,
        failure=failure,
        diagnostic=_DIAGNOSTICS[failure],
        segment_count=segment_count,
        edge_checks=edge_checks,
        obstacle_checks=obstacle_checks,
    )


def _stopped(
    cancelled: CancellationCheck | None, deadline_check: CancellationCheck | None
) -> ExternalCandidateFailure | None:
    for callback, failure in (
        (cancelled, ExternalCandidateFailure.CANCELLED),
        (deadline_check, ExternalCandidateFailure.DEADLINE_EXCEEDED),
    ):
        if callback is None:
            continue
        try:
            if bool(callback()):
                return failure
        except Exception:
            return failure
    return None


def _integer(value: object, *, minimum: int = -_JSON_SAFE_INTEGER) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= _JSON_SAFE_INTEGER
    ):
        return None
    return value


def _closed_dict(value: object, keys: frozenset[str]) -> bool:
    """Check a closed object without iterating caller-controlled keys."""

    return type(value) is dict and len(value) == len(keys) and all(key in value for key in keys)


def _digest(value: object) -> bool:
    if type(value) is not str or len(value) != 71 or not value.startswith("sha256:"):
        return False
    try:
        int(value[7:], 16)
    except ValueError:
        return False
    return value[7:] == value[7:].lower()


def _typed_id(value: object, prefix: str) -> bool:
    return (
        type(value) is str
        and value.startswith(prefix)
        and 1 <= len(value.removeprefix(prefix)) <= 160
        and all(
            character.isascii() and (character.isalnum() or character in "_.:-")
            for character in value
        )
    )


def _canonical_request(value: object) -> RouteRequest | None:
    if type(value) is not RouteRequest or type(value.settings) is not AStarSettings:
        return None
    settings = value.settings
    try:
        rebuilt_settings = AStarSettings(
            grid_step_nm=settings.grid_step_nm,
            bend_penalty_nm=settings.bend_penalty_nm,
            proximity_penalty_nm=settings.proximity_penalty_nm,
            max_grid_nodes=settings.max_grid_nodes,
            max_expansions=settings.max_expansions,
            max_obstacles=settings.max_obstacles,
            max_net_objects=settings.max_net_objects,
            region_margin_nm=settings.region_margin_nm,
            max_obstacle_checks=settings.max_obstacle_checks,
        )
        return RouteRequest(
            board_revision=value.board_revision,
            net_id=value.net_id,
            layer_id=value.layer_id,
            seed=value.seed,
            settings=rebuilt_settings,
        )
    except Exception:
        return None


def _point(value: object) -> PointNM | None:
    if not _closed_dict(value, _POINT_KEYS):
        return None
    assert isinstance(value, dict)
    x = _integer(value.get("x_nm"))
    y = _integer(value.get("y_nm"))
    if x is None or y is None:
        return None
    try:
        return PointNM(x, y)
    except ValueError:
        return None


def _compress(vertices: list[PointNM]) -> tuple[PointNM, ...]:
    compressed: list[PointNM] = []
    for point in vertices:
        if len(compressed) >= 2:
            first, middle = compressed[-2:]
            monotonic_vertical = first.x == middle.x == point.x and (
                first.y <= middle.y <= point.y or point.y <= middle.y <= first.y
            )
            monotonic_horizontal = first.y == middle.y == point.y and (
                first.x <= middle.x <= point.x or point.x <= middle.x <= first.x
            )
            if monotonic_vertical or monotonic_horizontal:
                compressed[-1] = point
                continue
        compressed.append(point)
    return tuple(compressed)


def _candidate(
    request: RouteRequest,
    *,
    start_pad_id: str,
    end_pad_id: str,
    width_nm: int,
    vertices: tuple[PointNM, ...],
) -> RouteCandidate:
    path = RoutePath(vertices=vertices)
    patch = RoutePatch(
        net_id=request.net_id,
        layer_id=request.layer_id,
        width_nm=width_nm,
        paths=(path,),
    )
    bend_cost = patch.bend_count * request.settings.bend_penalty_nm
    cost = RouteCost(
        length_nm=patch.length_nm,
        bend_count=patch.bend_count,
        bend_cost_nm=bend_cost,
        proximity_steps=0,
        proximity_cost_nm=0,
        via_cost_nm=0,
        total_cost_nm=patch.length_nm + bend_cost,
    )
    unsigned = RouteCandidate(
        candidate_id=_EMPTY_DIGEST,
        base_revision=request.board_revision,
        start_pad_id=start_pad_id,
        end_pad_id=end_pad_id,
        patch=patch,
        cost=cost,
        metrics=RouteMetrics(
            hard_internal_violations=0,
            unrouted_connections=0,
            vias=0,
            wire_length_nm=patch.length_nm,
            expanded_states=0,
            peak_frontier_states=1,
            obstacle_checks=0,
        ),
        settings=request.settings,
        router_version="external-candidate-verifier-v1",
        policy="external-candidate-disposer-v1",
        seed=request.seed,
        pad_count=2,
        ordering_policy=SINGLE_PATH_ORDERING,
    )
    digest = hashlib.sha256(canonical_candidate_bytes(unsigned)).hexdigest()
    return replace(unsigned, candidate_id=f"sha256:{digest}")


def _patch_candidate(
    request: RouteRequest,
    *,
    start_pad_id: str,
    end_pad_id: str,
    width_nm: int,
    paths: tuple[RoutePath, ...],
    pad_count: int,
) -> RouteCandidate:
    patch = RoutePatch(
        net_id=request.net_id,
        layer_id=request.layer_id,
        width_nm=width_nm,
        paths=paths,
    )
    bend_cost = patch.bend_count * request.settings.bend_penalty_nm
    unsigned = RouteCandidate(
        candidate_id=_EMPTY_DIGEST,
        base_revision=request.board_revision,
        start_pad_id=start_pad_id,
        end_pad_id=end_pad_id,
        patch=patch,
        cost=RouteCost(
            length_nm=patch.length_nm,
            bend_count=patch.bend_count,
            bend_cost_nm=bend_cost,
            proximity_steps=0,
            proximity_cost_nm=0,
            via_cost_nm=0,
            total_cost_nm=patch.length_nm + bend_cost,
        ),
        metrics=RouteMetrics(
            hard_internal_violations=0,
            unrouted_connections=0,
            vias=0,
            wire_length_nm=patch.length_nm,
            expanded_states=0,
            peak_frontier_states=1,
            obstacle_checks=0,
        ),
        settings=request.settings,
        router_version="external-candidate-verifier-v2",
        policy="external-candidate-disposer-v2",
        seed=request.seed,
        pad_count=pad_count,
        ordering_policy=EXTERNAL_PATCH_TREE_ORDERING,
    )
    digest = hashlib.sha256(canonical_candidate_bytes(unsigned)).hexdigest()
    return replace(unsigned, candidate_id=f"sha256:{digest}")


_VALIDATOR_FAILURES: Final = {
    CandidatePathValidationFailure.INVALID_REQUEST: ExternalCandidateFailure.INVALID_REQUEST,
    CandidatePathValidationFailure.INVALID_CANDIDATE: ExternalCandidateFailure.INVALID_CANDIDATE,
    CandidatePathValidationFailure.STALE_REVISION: ExternalCandidateFailure.STALE_REVISION,
    CandidatePathValidationFailure.UNSUPPORTED_GEOMETRY: (
        ExternalCandidateFailure.UNSUPPORTED_GEOMETRY
    ),
    CandidatePathValidationFailure.INFEASIBLE: ExternalCandidateFailure.INFEASIBLE,
    CandidatePathValidationFailure.OBSTACLE_VIOLATION: (
        ExternalCandidateFailure.OBSTACLE_VIOLATION
    ),
    CandidatePathValidationFailure.BUDGET_EXHAUSTED: ExternalCandidateFailure.BUDGET_EXCEEDED,
    CandidatePathValidationFailure.CANCELLED: ExternalCandidateFailure.CANCELLED,
    CandidatePathValidationFailure.DEADLINE_EXCEEDED: ExternalCandidateFailure.DEADLINE_EXCEEDED,
}


def _verify_external_patch(
    snapshot: BoardIRSnapshot,
    request: RouteRequest,
    document: dict[object, object],
    *,
    start_pad_id: str,
    end_pad_id: str,
    pad_count: int,
    layers: set[str],
    max_obstacle_checks: int,
    max_path_edges: int,
    cancelled: CancellationCheck | None,
    deadline_check: CancellationCheck | None,
) -> ExternalCandidateVerificationResult:
    raw_paths = document.get("paths")
    if type(raw_paths) is not list or not 2 <= len(raw_paths) <= _MAX_SEGMENTS:
        return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
    if len(raw_paths) > pad_count - 1:
        return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)

    paths: list[RoutePath] = []
    width_nm: int | None = None
    segment_count = 0
    for raw_path in raw_paths:
        stopped = _stopped(cancelled, deadline_check)
        if stopped is not None:
            return _refused(stopped, segment_count=segment_count)
        if not _closed_dict(raw_path, _PATH_KEYS):
            return _refused(
                ExternalCandidateFailure.INVALID_CANDIDATE,
                segment_count=segment_count,
            )
        assert isinstance(raw_path, dict)
        raw_segments = raw_path.get("segments")
        if type(raw_segments) is not list or not raw_segments:
            return _refused(
                ExternalCandidateFailure.INVALID_CANDIDATE,
                segment_count=segment_count,
            )
        if len(raw_segments) > _MAX_SEGMENTS or segment_count + len(raw_segments) > max_path_edges:
            return _refused(
                ExternalCandidateFailure.BUDGET_EXCEEDED,
                segment_count=segment_count,
            )
        parsed: list[tuple[PointNM, PointNM]] = []
        for segment in raw_segments:
            stopped = _stopped(cancelled, deadline_check)
            if stopped is not None:
                return _refused(stopped, segment_count=segment_count + len(parsed))
            if not _closed_dict(segment, _SEGMENT_KEYS):
                return _refused(
                    ExternalCandidateFailure.INVALID_CANDIDATE,
                    segment_count=segment_count + len(parsed),
                )
            assert isinstance(segment, dict)
            layer_id = segment.get("layer_id")
            if not _typed_id(layer_id, "layer:"):
                return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
            if layer_id not in layers:
                return _refused(ExternalCandidateFailure.UNDECLARED_LAYER)
            if layer_id != request.layer_id:
                return _refused(ExternalCandidateFailure.UNSUPPORTED_GEOMETRY)
            width = _integer(segment.get("width_nm"), minimum=1)
            start = _point(segment.get("start"))
            end = _point(segment.get("end"))
            if width is None or start is None or end is None or start == end:
                return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
            if start.x != end.x and start.y != end.y:
                return _refused(ExternalCandidateFailure.UNSUPPORTED_GEOMETRY)
            if width_nm is None:
                width_nm = width
            elif width != width_nm:
                return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
            parsed.append((start, end))
        segment_count += len(parsed)
        if any(first[1] != second[0] for first, second in pairwise(parsed)):
            return _refused(
                ExternalCandidateFailure.DISCONTINUOUS_PATH,
                segment_count=segment_count,
            )
        try:
            paths.append(RoutePath(vertices=_compress([parsed[0][0], *(end for _, end in parsed)])))
        except (TypeError, ValueError):
            return _refused(
                ExternalCandidateFailure.INVALID_CANDIDATE,
                segment_count=segment_count,
            )

    try:
        candidate = _patch_candidate(
            request,
            start_pad_id=start_pad_id,
            end_pad_id=end_pad_id,
            width_nm=width_nm if width_nm is not None else 0,
            paths=tuple(paths),
            pad_count=pad_count,
        )
    except (TypeError, ValueError):
        return _refused(
            ExternalCandidateFailure.INVALID_CANDIDATE,
            segment_count=segment_count,
        )
    validation = validate_candidate_patch_with_exact_off_grid_obstacle_fallback(
        snapshot,
        request,
        candidate,
        max_obstacle_checks=max_obstacle_checks,
        max_path_edges=max_path_edges,
        cancelled=cancelled,
        deadline_check=deadline_check,
    )
    if validation.failure is not None:
        return _refused(
            _VALIDATOR_FAILURES[validation.failure],
            segment_count=segment_count,
            edge_checks=validation.edge_checks,
            obstacle_checks=validation.obstacle_checks,
        )
    return ExternalCandidateVerificationResult(
        status="accepted",
        candidate_id=candidate.candidate_id,
        failure=None,
        diagnostic=None,
        segment_count=segment_count,
        edge_checks=validation.edge_checks,
        obstacle_checks=validation.obstacle_checks,
    )


def verify_external_route_candidate(
    snapshot: object,
    request: object,
    document: object,
    *,
    start_pad_id: object,
    end_pad_id: object,
    max_obstacle_checks: object,
    max_path_edges: object,
    cancelled: object = None,
    deadline_check: object = None,
) -> ExternalCandidateVerificationResult:
    """Dispose one foreign route document against coordinator-owned Board IR state."""

    if (
        type(snapshot) is not BoardIRSnapshot
        or type(request) is not RouteRequest
        or not isinstance(start_pad_id, str)
        or not isinstance(end_pad_id, str)
        or not _typed_id(start_pad_id, "pad:")
        or not _typed_id(end_pad_id, "pad:")
        or start_pad_id == end_pad_id
        or (cancelled is not None and not callable(cancelled))
        or (deadline_check is not None and not callable(deadline_check))
        or isinstance(max_obstacle_checks, bool)
        or not isinstance(max_obstacle_checks, int)
        or not 1 <= max_obstacle_checks <= 10_000_000
        or isinstance(max_path_edges, bool)
        or not isinstance(max_path_edges, int)
        or not 1 <= max_path_edges <= _MAX_SEGMENTS
    ):
        return _refused(ExternalCandidateFailure.INVALID_REQUEST)
    stopped = _stopped(cancelled, deadline_check)
    if stopped is not None:
        return _refused(stopped)
    checked_request = _canonical_request(request)
    if checked_request is None or type(snapshot.content) is not BoardIRContent:
        return _refused(ExternalCandidateFailure.INVALID_REQUEST)
    try:
        verify_snapshot(snapshot)
    except Exception:
        return _refused(ExternalCandidateFailure.INVALID_REQUEST)
    if type(document) is not dict or len(document) != len(_DOCUMENT_KEYS):
        return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
    assert isinstance(document, dict)
    schema = document.get("schema")
    if schema == EXTERNAL_ROUTE_CANDIDATE_SCHEMA:
        document_keys = _DOCUMENT_KEYS
    elif schema == EXTERNAL_ROUTE_PATCH_SCHEMA:
        document_keys = _PATCH_DOCUMENT_KEYS
    else:
        return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
    if not all(key in document for key in document_keys):
        return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
    if not _digest(document.get("problem_revision")):
        return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
    if not _typed_id(document.get("start_pad_id"), "pad:") or not _typed_id(
        document.get("end_pad_id"), "pad:"
    ):
        return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
    if document.get("problem_revision") != checked_request.board_revision:
        return _refused(ExternalCandidateFailure.STALE_REVISION)
    if document.get("start_pad_id") != start_pad_id or document.get("end_pad_id") != end_pad_id:
        return _refused(ExternalCandidateFailure.ENDPOINT_MISMATCH)

    layers = {layer.id for layer in snapshot.content.copper_layers}
    pads = {pad.id: pad for pad in snapshot.content.pads}
    net_pads = tuple(
        sorted(
            (
                pad
                for pad in snapshot.content.pads
                if pad.net_id == checked_request.net_id
                and checked_request.layer_id in pad.layer_ids
            ),
            key=lambda pad: pad.id,
        )
    )
    start_pad = pads.get(start_pad_id)
    end_pad = pads.get(end_pad_id)
    if (
        start_pad is None
        or end_pad is None
        or start_pad.net_id != checked_request.net_id
        or end_pad.net_id != checked_request.net_id
    ):
        return _refused(ExternalCandidateFailure.INVALID_REQUEST)
    if checked_request.layer_id not in layers:
        return _refused(ExternalCandidateFailure.INVALID_REQUEST)

    raw_vias = document.get("vias")
    if type(raw_vias) is not list or len(raw_vias) > _MAX_SEGMENTS:
        return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
    for via in raw_vias:
        if not _closed_dict(via, _VIA_KEYS):
            return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
        assert isinstance(via, dict)
        if _point(via.get("at")) is None:
            return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
        via_start_layer = via.get("start_layer_id")
        via_end_layer = via.get("end_layer_id")
        if not _typed_id(via_start_layer, "layer:") or not _typed_id(via_end_layer, "layer:"):
            return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
        if via_start_layer not in layers or via_end_layer not in layers:
            return _refused(ExternalCandidateFailure.UNDECLARED_LAYER)
    if raw_vias:
        return _refused(ExternalCandidateFailure.UNSUPPORTED_GEOMETRY)

    if schema == EXTERNAL_ROUTE_PATCH_SCHEMA:
        if len(net_pads) <= 2 or start_pad_id != net_pads[0].id or end_pad_id != net_pads[-1].id:
            return _refused(ExternalCandidateFailure.INVALID_REQUEST)
        return _verify_external_patch(
            snapshot,
            checked_request,
            document,
            start_pad_id=start_pad_id,
            end_pad_id=end_pad_id,
            pad_count=len(net_pads),
            layers=layers,
            max_obstacle_checks=max_obstacle_checks,
            max_path_edges=max_path_edges,
            cancelled=cancelled,
            deadline_check=deadline_check,
        )

    raw_segments = document.get("segments")
    if type(raw_segments) is not list or not raw_segments:
        return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
    if len(raw_segments) > _MAX_SEGMENTS or len(raw_segments) > max_path_edges:
        return _refused(ExternalCandidateFailure.BUDGET_EXCEEDED)
    parsed: list[tuple[PointNM, PointNM]] = []
    width_nm: int | None = None
    for segment in raw_segments:
        stopped = _stopped(cancelled, deadline_check)
        if stopped is not None:
            return _refused(stopped, segment_count=len(parsed))
        if not _closed_dict(segment, _SEGMENT_KEYS):
            return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
        assert isinstance(segment, dict)
        layer_id = segment.get("layer_id")
        if not _typed_id(layer_id, "layer:"):
            return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
        if layer_id not in layers:
            return _refused(ExternalCandidateFailure.UNDECLARED_LAYER)
        if layer_id != checked_request.layer_id:
            return _refused(ExternalCandidateFailure.UNSUPPORTED_GEOMETRY)
        width = _integer(segment.get("width_nm"), minimum=1)
        start = _point(segment.get("start"))
        end = _point(segment.get("end"))
        if width is None or start is None or end is None or start == end:
            return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
        if (start.x != end.x) and (start.y != end.y):
            return _refused(ExternalCandidateFailure.UNSUPPORTED_GEOMETRY)
        if width_nm is None:
            width_nm = width
        elif width != width_nm:
            return _refused(ExternalCandidateFailure.INVALID_CANDIDATE)
        parsed.append((start, end))
    if any(first[1] != second[0] for first, second in pairwise(parsed)):
        return _refused(ExternalCandidateFailure.DISCONTINUOUS_PATH, segment_count=len(parsed))
    vertices = _compress([parsed[0][0], *(end for _, end in parsed)])
    if vertices[0] != start_pad.center or vertices[-1] != end_pad.center:
        return _refused(ExternalCandidateFailure.ENDPOINT_MISMATCH, segment_count=len(parsed))
    try:
        candidate = _candidate(
            checked_request,
            start_pad_id=start_pad_id,
            end_pad_id=end_pad_id,
            width_nm=width_nm if width_nm is not None else 0,
            vertices=vertices,
        )
    except (TypeError, ValueError):
        return _refused(ExternalCandidateFailure.INVALID_CANDIDATE, segment_count=len(parsed))

    validation = validate_candidate_path_with_exact_off_grid_obstacle_fallback(
        snapshot,
        checked_request,
        candidate,
        max_obstacle_checks=max_obstacle_checks,
        max_path_edges=max_path_edges,
        cancelled=cancelled,
        deadline_check=deadline_check,
    )
    if validation.failure is not None:
        return _refused(
            _VALIDATOR_FAILURES[validation.failure],
            segment_count=len(parsed),
            edge_checks=validation.edge_checks,
            obstacle_checks=validation.obstacle_checks,
        )
    return ExternalCandidateVerificationResult(
        status="accepted",
        candidate_id=candidate.candidate_id,
        failure=None,
        diagnostic=None,
        segment_count=len(parsed),
        edge_checks=validation.edge_checks,
        obstacle_checks=validation.obstacle_checks,
    )


__all__ = [
    "EXTERNAL_ROUTE_CANDIDATE_SCHEMA",
    "EXTERNAL_ROUTE_PATCH_SCHEMA",
    "ExternalCandidateFailure",
    "ExternalCandidateVerificationResult",
    "verify_external_route_candidate",
]
