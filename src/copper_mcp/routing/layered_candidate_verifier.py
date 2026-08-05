"""Pure structural verification for immutable layered routing candidates.

The layered router intentionally stops at a candidate.  This module is the small, deterministic
gate that can be run before a serializer, DRC process, or any future apply authority.  It checks
the facts represented by the Board IR and by :class:`LayeredRouteCandidate` itself: identity,
revision and endpoint binding, path/via continuity, layer transitions, and bounded arithmetic.

It does **not** claim to be a PCB DRC implementation.  KiCad's clearance, hole, padstack,
unconnected, zone-fill, and manufacturing checks remain outside this pure seam.  The result
always records ``physical_validation="not_modelled"``; callers that need physical assurance can
set ``require_physical_validation=True`` and receive an explicit refusal rather than accidentally
promoting structural checks to a physical claim.  Endpoint-via (including conservative via-in-pad)
geometry is refused unconditionally because the Board IR candidate contract does not carry the
padstack evidence needed to establish that it is legal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations, pairwise
from math import isqrt
from typing import Final

from copper_mcp.board_ir import BoardIRSnapshot, Pad, PointNM, verify_snapshot
from copper_mcp.routing.layered_astar import effective_max_vias
from copper_mcp.routing.layered_contracts import LayeredRouteCandidate, verify_layered_candidate_id

_MAX_SAFE_INT: Final = (1 << 53) - 1
_SHA256_LENGTH: Final = 71


class LayeredCandidateVerificationCode(StrEnum):
    """Stable, redacted outcomes from :func:`verify_layered_candidate`."""

    VERIFIED = "verified"
    INVALID_CANDIDATE = "invalid_candidate"
    INVALID_SNAPSHOT = "invalid_snapshot"
    STALE_REVISION = "stale_revision"
    ENDPOINT_MISMATCH = "endpoint_mismatch"
    LAYER_MISMATCH = "layer_mismatch"
    PATH_DISCONTINUITY = "path_discontinuity"
    VIA_DISCONTINUITY = "via_discontinuity"
    DUPLICATE_GEOMETRY = "duplicate_geometry"
    BUDGET_EXCEEDED = "budget_exceeded"
    UNSUPPORTED_ENDPOINT_VIA = "unsupported_endpoint_via"
    PHYSICAL_VALIDATION_NOT_MODELED = "physical_validation_not_modelled"


class LayeredPhysicalValidation(StrEnum):
    """Whether a result includes authoritative physical-board checks."""

    NOT_MODELED = "not_modelled"


@dataclass(frozen=True, slots=True)
class LayeredCandidateVerificationLimits:
    """Hard limits for verifier work and candidate geometry.

    These ceilings are intentionally independent of router settings.  A candidate can carry a
    valid router budget while still being too large for a caller's verification budget.  Values
    are checked before pairwise geometry work, so an untrusted candidate cannot turn this pure
    verifier into an unbounded intersection scan.
    """

    max_paths: int = 256
    max_vertices: int = 16_384
    max_vias: int = 256
    max_segments: int = 16_384
    max_pair_checks: int = 2_000_000

    def __post_init__(self) -> None:
        for name, value in (
            ("max paths", self.max_paths),
            ("max vertices", self.max_vertices),
            ("max vias", self.max_vias),
            ("max segments", self.max_segments),
            ("max pair checks", self.max_pair_checks),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= _MAX_SAFE_INT
            ):
                raise ValueError(f"{name} must be a positive safe integer")


@dataclass(frozen=True, slots=True)
class LayeredCandidateVerificationDiagnostic:
    """One bounded, non-echoing refusal or success explanation."""

    code: LayeredCandidateVerificationCode
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, LayeredCandidateVerificationCode):
            raise ValueError("layered verification code is unsupported")
        if not isinstance(self.message, str) or not 1 <= len(self.message) <= 256:
            raise ValueError("layered verification message is malformed")


@dataclass(frozen=True, slots=True)
class LayeredCandidateVerificationResult:
    """Structural verification outcome with an explicit physical non-claim."""

    diagnostic: LayeredCandidateVerificationDiagnostic
    candidate_id: str | None = None
    path_count: int = 0
    vertex_count: int = 0
    via_count: int = 0
    pair_checks: int = 0
    physical_validation: LayeredPhysicalValidation = LayeredPhysicalValidation.NOT_MODELED

    def __post_init__(self) -> None:
        if not isinstance(self.diagnostic, LayeredCandidateVerificationDiagnostic):
            raise ValueError("layered verification diagnostic is malformed")
        if self.candidate_id is not None and not _is_digest(self.candidate_id):
            raise ValueError("layered verification candidate ID is malformed")
        for name, value in (
            ("path count", self.path_count),
            ("vertex count", self.vertex_count),
            ("via count", self.via_count),
            ("pair checks", self.pair_checks),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_SAFE_INT
            ):
                raise ValueError(f"{name} is outside the supported integer range")
        if self.physical_validation is not LayeredPhysicalValidation.NOT_MODELED:
            raise ValueError("layered physical validation state is unsupported")

    @property
    def ok(self) -> bool:
        """Return true only for a structurally verified candidate."""

        return self.diagnostic.code is LayeredCandidateVerificationCode.VERIFIED

    @property
    def verified(self) -> bool:
        """Readable alias for callers that use verification terminology."""

        return self.ok


def _is_digest(value: object) -> bool:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or not value.startswith("sha256:")
    ):
        return False
    return all(character in "0123456789abcdef" for character in value[7:])


def _failure(
    code: LayeredCandidateVerificationCode,
    message: str,
    *,
    candidate_id: str | None = None,
    path_count: int = 0,
    vertex_count: int = 0,
    via_count: int = 0,
    pair_checks: int = 0,
) -> LayeredCandidateVerificationResult:
    return LayeredCandidateVerificationResult(
        diagnostic=LayeredCandidateVerificationDiagnostic(code=code, message=message),
        candidate_id=candidate_id,
        path_count=path_count,
        vertex_count=vertex_count,
        via_count=via_count,
        pair_checks=pair_checks,
    )


def _success(
    candidate: LayeredRouteCandidate,
    *,
    vertex_count: int,
    pair_checks: int,
) -> LayeredCandidateVerificationResult:
    return LayeredCandidateVerificationResult(
        diagnostic=LayeredCandidateVerificationDiagnostic(
            code=LayeredCandidateVerificationCode.VERIFIED,
            message="candidate identity and layered geometry are structurally verified",
        ),
        candidate_id=candidate.candidate_id,
        path_count=len(candidate.patch.paths),
        vertex_count=vertex_count,
        via_count=len(candidate.patch.vias),
        pair_checks=pair_checks,
    )


def _point_in_pad_envelope(point: PointNM, pad: Pad) -> bool:
    """Conservatively detect a via-in-pad candidate without claiming exact padstack geometry."""

    # A rotated or non-rectangular pad cannot be checked exactly by this module.  Use a
    # circumradius envelope for every pad, which is conservative for arbitrary rotation and
    # shape while keeping the verifier independent of a geometry kernel.
    half_x = (pad.size_x_nm + 1) // 2
    half_y = (pad.size_y_nm + 1) // 2
    radius = isqrt(half_x * half_x + half_y * half_y) + 1
    return (
        pad.center.x - radius <= point.x <= pad.center.x + radius
        and pad.center.y - radius <= point.y <= pad.center.y + radius
    )


def _segment_key(layer_id: str, start: PointNM, end: PointNM) -> tuple[str, PointNM, PointNM]:
    return layer_id, min(start, end), max(start, end)


def _segments_intersect(left: tuple[PointNM, PointNM], right: tuple[PointNM, PointNM]) -> bool:
    """Return true for any overlap or crossing, including a single shared point."""

    a, b = left
    c, d = right
    left_horizontal = a.y == b.y
    right_horizontal = c.y == d.y
    if left_horizontal and right_horizontal:
        if a.y != c.y:
            return False
        return max(min(a.x, b.x), min(c.x, d.x)) <= min(max(a.x, b.x), max(c.x, d.x))
    if not left_horizontal and not right_horizontal:
        if a.x != c.x:
            return False
        return max(min(a.y, b.y), min(c.y, d.y)) <= min(max(a.y, b.y), max(c.y, d.y))
    horizontal, vertical = (left, right) if left_horizontal else (right, left)
    h_start, h_end = horizontal
    v_start, v_end = vertical
    return min(h_start.x, h_end.x) <= v_start.x <= max(h_start.x, h_end.x) and min(
        v_start.y, v_end.y
    ) <= h_start.y <= max(v_start.y, v_end.y)


def _validate_candidate_budget(
    candidate: LayeredRouteCandidate, limits: LayeredCandidateVerificationLimits
) -> str | None:
    settings = candidate.settings
    for name, value, maximum in (
        ("move cost", settings.move_cost, _MAX_SAFE_INT),
        ("via cost", settings.via_cost, _MAX_SAFE_INT),
        ("expansion budget", settings.max_expansions, 1_000_000),
        ("node budget", settings.max_nodes, 500_000),
        ("obstacle budget", settings.max_obstacles, 4_096),
        ("obstacle-check budget", settings.max_obstacle_checks, 10_000_000),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            return f"{name} exceeds the finite layered budget"
    if settings.max_vias is not None and (
        isinstance(settings.max_vias, bool)
        or not isinstance(settings.max_vias, int)
        or not 0 <= settings.max_vias <= 256
    ):
        return "via budget exceeds the finite layered budget"
    if len(candidate.patch.paths) > limits.max_paths:
        return "candidate path count exceeds the verification budget"
    if len(candidate.patch.vias) > limits.max_vias:
        return "candidate via count exceeds the verification budget"
    vertex_count = sum(len(path.vertices) for path in candidate.patch.paths)
    if vertex_count > limits.max_vertices:
        return "candidate vertex count exceeds the verification budget"
    segment_count = sum(max(0, len(path.vertices) - 1) for path in candidate.patch.paths)
    if segment_count > limits.max_segments:
        return "candidate segment count exceeds the verification budget"
    if candidate.metrics.expanded_states > settings.max_expansions:
        return "candidate expanded-state metric exceeds its budget"
    if candidate.metrics.discovered_states > settings.max_nodes:
        return "candidate discovered-state metric exceeds its budget"
    if candidate.metrics.obstacle_checks > settings.max_obstacle_checks:
        return "candidate obstacle-check metric exceeds its budget"
    for name, value in (
        ("route width", candidate.patch.width_nm),
        ("via diameter", candidate.patch.via_diameter_nm),
        ("via drill", candidate.patch.via_drill_nm),
        ("wire length", candidate.cost.wire_length_nm),
        ("via count", candidate.cost.via_count),
        ("via cost", candidate.cost.via_cost_units),
        ("search cost", candidate.cost.total_search_cost_units),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_SAFE_INT:
            return f"{name} is outside the supported integer range"
    return None


def verify_layered_candidate(
    candidate: object,
    snapshot: object,
    *,
    expected_board_revision: str | None = None,
    expected_start_pad_id: str | None = None,
    expected_end_pad_id: str | None = None,
    limits: object | None = None,
    require_physical_validation: bool = False,
) -> LayeredCandidateVerificationResult:
    """Verify candidate identity and Board IR-visible layered geometry.

    The function never raises for an untrusted candidate or snapshot; malformed values become a
    bounded diagnostic.  ``require_physical_validation`` is an explicit caller opt-in to the
    residual non-claim and therefore returns ``physical_validation_not_modelled`` after all
    structural checks pass.  No board bytes, net names, prompts, or coordinates are copied into a
    diagnostic.
    """

    active_limits = limits if limits is not None else LayeredCandidateVerificationLimits()
    if not isinstance(active_limits, LayeredCandidateVerificationLimits):
        return _failure(
            LayeredCandidateVerificationCode.INVALID_CANDIDATE, "verification limits are malformed"
        )
    if not isinstance(candidate, LayeredRouteCandidate):
        return _failure(
            LayeredCandidateVerificationCode.INVALID_CANDIDATE, "candidate type is invalid"
        )
    candidate_id = candidate.candidate_id if _is_digest(candidate.candidate_id) else None
    if not isinstance(snapshot, BoardIRSnapshot):
        return _failure(
            LayeredCandidateVerificationCode.INVALID_SNAPSHOT,
            "Board IR snapshot type is invalid",
            candidate_id=candidate_id,
        )
    try:
        verify_snapshot(snapshot)
    except (TypeError, ValueError):
        return _failure(
            LayeredCandidateVerificationCode.INVALID_SNAPSHOT,
            "Board IR snapshot verification failed",
            candidate_id=candidate_id,
        )
    # Apply cheap structural ceilings before canonicalizing and hashing the candidate.  The
    # identity check is deliberately later: a hostile over-limit candidate must not spend the
    # full canonical-JSON cost merely to discover that its geometry was already outside the
    # caller's verification budget.
    try:
        budget_error = _validate_candidate_budget(candidate, active_limits)
        path_count = len(candidate.patch.paths)
        vertex_count = sum(len(path.vertices) for path in candidate.patch.paths)
        via_count = len(candidate.patch.vias)
    except (AttributeError, TypeError, ValueError):
        return _failure(
            LayeredCandidateVerificationCode.INVALID_CANDIDATE,
            "candidate budget fields are malformed",
            candidate_id=candidate_id,
        )
    if budget_error is not None:
        return _failure(
            LayeredCandidateVerificationCode.BUDGET_EXCEEDED,
            budget_error,
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    try:
        verify_layered_candidate_id(candidate)
    except (TypeError, ValueError):
        return _failure(
            LayeredCandidateVerificationCode.INVALID_CANDIDATE,
            "candidate identity verification failed",
            candidate_id=candidate_id,
        )
    if expected_board_revision is not None and (
        not _is_digest(expected_board_revision)
        or expected_board_revision != snapshot.snapshot_digest
    ):
        return _failure(
            LayeredCandidateVerificationCode.STALE_REVISION,
            "expected board revision is not the verified snapshot revision",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    if candidate.base_revision != snapshot.snapshot_digest:
        return _failure(
            LayeredCandidateVerificationCode.STALE_REVISION,
            "candidate base revision does not match the Board IR snapshot",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    if expected_start_pad_id is not None and expected_start_pad_id != candidate.start_pad_id:
        return _failure(
            LayeredCandidateVerificationCode.ENDPOINT_MISMATCH,
            "candidate start endpoint does not match the requested endpoint",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    if expected_end_pad_id is not None and expected_end_pad_id != candidate.end_pad_id:
        return _failure(
            LayeredCandidateVerificationCode.ENDPOINT_MISMATCH,
            "candidate end endpoint does not match the requested endpoint",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    pads = {pad.id: pad for pad in snapshot.content.pads}
    start_pad = pads.get(candidate.start_pad_id)
    end_pad = pads.get(candidate.end_pad_id)
    if start_pad is None or end_pad is None or start_pad.net_id is None or end_pad.net_id is None:
        return _failure(
            LayeredCandidateVerificationCode.ENDPOINT_MISMATCH,
            "candidate endpoints are not bound to one electrical net",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    if start_pad.net_id != end_pad.net_id or candidate.patch.net_id != start_pad.net_id:
        return _failure(
            LayeredCandidateVerificationCode.ENDPOINT_MISMATCH,
            "candidate endpoint and patch net bindings are inconsistent",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    net_class_id = next(
        (
            assignment.net_class_id
            for assignment in snapshot.content.constraints.assignments
            if assignment.net_id == candidate.patch.net_id
        ),
        None,
    )
    net_class = next(
        (item for item in snapshot.content.constraints.net_classes if item.id == net_class_id),
        None,
    )
    if (
        net_class is None
        or candidate.patch.width_nm != net_class.track_width_nm
        or candidate.patch.via_diameter_nm != net_class.via_diameter_nm
        or candidate.patch.via_drill_nm != net_class.via_drill_nm
    ):
        return _failure(
            LayeredCandidateVerificationCode.INVALID_CANDIDATE,
            "candidate dimensions do not match the Board IR net-class binding",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    ordered_layers = tuple(sorted(snapshot.content.copper_layers, key=lambda layer: layer.index))
    layer_ids = {layer.id for layer in ordered_layers}
    signal_layer_ids = {layer.id for layer in ordered_layers if layer.kind == "signal"}
    if not layer_ids or not signal_layer_ids:
        return _failure(
            LayeredCandidateVerificationCode.LAYER_MISMATCH,
            "Board IR snapshot exposes no supported signal layers",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    if not 2 <= len(ordered_layers) <= 8 or len(signal_layer_ids) != len(ordered_layers):
        return _failure(
            LayeredCandidateVerificationCode.LAYER_MISMATCH,
            "layered candidate verification requires two through eight ordered signal layers",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    via_limit = effective_max_vias(candidate.settings, len(ordered_layers))
    if via_limit is not None and via_count > via_limit:
        return _failure(
            LayeredCandidateVerificationCode.BUDGET_EXCEEDED,
            "candidate via count exceeds its effective routing budget",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    if len(candidate.patch.paths) != via_count + 1:
        endpoint_via = any(
            via.center == start_pad.center
            or via.center == end_pad.center
            or _point_in_pad_envelope(via.center, start_pad)
            or _point_in_pad_envelope(via.center, end_pad)
            for via in candidate.patch.vias
        )
        return _failure(
            LayeredCandidateVerificationCode.UNSUPPORTED_ENDPOINT_VIA
            if endpoint_via
            else LayeredCandidateVerificationCode.PATH_DISCONTINUITY,
            "endpoint-via geometry is not modelled by the structural candidate contract"
            if endpoint_via
            else "path and via counts do not form one continuous chain",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    if not candidate.patch.paths:
        return _failure(
            LayeredCandidateVerificationCode.PATH_DISCONTINUITY,
            "candidate has no route path",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    if candidate.patch.paths[0].vertices[0] != start_pad.center:
        return _failure(
            LayeredCandidateVerificationCode.ENDPOINT_MISMATCH,
            "first route path does not begin at the start pad",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    if candidate.patch.paths[-1].vertices[-1] != end_pad.center:
        return _failure(
            LayeredCandidateVerificationCode.ENDPOINT_MISMATCH,
            "last route path does not end at the end pad",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    if candidate.patch.paths[0].layer_id not in start_pad.layer_ids:
        return _failure(
            LayeredCandidateVerificationCode.LAYER_MISMATCH,
            "start path layer is not exposed by the start pad",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    if candidate.patch.paths[-1].layer_id not in end_pad.layer_ids:
        return _failure(
            LayeredCandidateVerificationCode.LAYER_MISMATCH,
            "end path layer is not exposed by the end pad",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
        )
    all_segments: list[tuple[str, int, int, PointNM, PointNM]] = []
    seen_segments: set[tuple[str, PointNM, PointNM]] = set()
    for path_index, path in enumerate(candidate.patch.paths):
        if path.layer_id not in signal_layer_ids:
            return _failure(
                LayeredCandidateVerificationCode.LAYER_MISMATCH,
                "route path uses a non-signal or unknown layer",
                candidate_id=candidate.candidate_id,
                path_count=path_count,
                vertex_count=vertex_count,
                via_count=via_count,
            )
        for edge_index, (start, end) in enumerate(pairwise(path.vertices)):
            if start == end or (start.x != end.x and start.y != end.y):
                return _failure(
                    LayeredCandidateVerificationCode.PATH_DISCONTINUITY,
                    "route path contains a non-orthogonal or zero-length edge",
                    candidate_id=candidate.candidate_id,
                    path_count=path_count,
                    vertex_count=vertex_count,
                    via_count=via_count,
                )
            key = _segment_key(path.layer_id, start, end)
            if key in seen_segments:
                return _failure(
                    LayeredCandidateVerificationCode.DUPLICATE_GEOMETRY,
                    "route path contains duplicate segment geometry",
                    candidate_id=candidate.candidate_id,
                    path_count=path_count,
                    vertex_count=vertex_count,
                    via_count=via_count,
                )
            seen_segments.add(key)
            all_segments.append((path.layer_id, path_index, edge_index, start, end))
    pair_checks = 0
    for (left_layer, left_path_index, left_index, left_start, left_end), (
        right_layer,
        right_path_index,
        right_index,
        right_start,
        right_end,
    ) in combinations(all_segments, 2):
        if left_layer != right_layer:
            continue
        pair_checks += 1
        if pair_checks > active_limits.max_pair_checks:
            return _failure(
                LayeredCandidateVerificationCode.BUDGET_EXCEEDED,
                "route intersection checks exceed the verification budget",
                candidate_id=candidate.candidate_id,
                path_count=path_count,
                vertex_count=vertex_count,
                via_count=via_count,
                pair_checks=pair_checks,
            )
        if not _segments_intersect((left_start, left_end), (right_start, right_end)):
            continue
        if left_path_index == right_path_index and abs(left_index - right_index) == 1:
            continue
        return _failure(
            LayeredCandidateVerificationCode.DUPLICATE_GEOMETRY,
            "route path geometry overlaps or crosses itself",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
            pair_checks=pair_checks,
        )
    for via_index, via in enumerate(candidate.patch.vias):
        if via.center == start_pad.center or via.center == end_pad.center:
            return _failure(
                LayeredCandidateVerificationCode.UNSUPPORTED_ENDPOINT_VIA,
                "via-on-pad geometry is not modelled by the structural candidate contract",
                candidate_id=candidate.candidate_id,
                path_count=path_count,
                vertex_count=vertex_count,
                via_count=via_count,
                pair_checks=pair_checks,
            )
        if _point_in_pad_envelope(via.center, start_pad) or _point_in_pad_envelope(
            via.center, end_pad
        ):
            return _failure(
                LayeredCandidateVerificationCode.UNSUPPORTED_ENDPOINT_VIA,
                "conservative via-in-pad geometry is not modelled by the structural "
                "candidate contract",
                candidate_id=candidate.candidate_id,
                path_count=path_count,
                vertex_count=vertex_count,
                via_count=via_count,
                pair_checks=pair_checks,
            )
        previous_path = candidate.patch.paths[via_index]
        next_path = candidate.patch.paths[via_index + 1]
        if (
            via.start_layer_id == via.end_layer_id
            or via.center != previous_path.vertices[-1]
            or via.center != next_path.vertices[0]
            # Board IR v0.2 has only full-stack vias.  A route may transition between any two
            # signal layers, but the via record must state the canonical outer stack span.
            or via.start_layer_id != ordered_layers[0].id
            or via.end_layer_id != ordered_layers[-1].id
            or previous_path.layer_id == next_path.layer_id
        ):
            return _failure(
                LayeredCandidateVerificationCode.VIA_DISCONTINUITY,
                "via does not join adjacent path endpoints and layers",
                candidate_id=candidate.candidate_id,
                path_count=path_count,
                vertex_count=vertex_count,
                via_count=via_count,
                pair_checks=pair_checks,
            )
    if require_physical_validation:
        return _failure(
            LayeredCandidateVerificationCode.PHYSICAL_VALIDATION_NOT_MODELED,
            "authoritative physical validation is not implemented in this pure verifier",
            candidate_id=candidate.candidate_id,
            path_count=path_count,
            vertex_count=vertex_count,
            via_count=via_count,
            pair_checks=pair_checks,
        )
    return _success(candidate, vertex_count=vertex_count, pair_checks=pair_checks)


__all__ = [
    "LayeredCandidateVerificationCode",
    "LayeredCandidateVerificationDiagnostic",
    "LayeredCandidateVerificationLimits",
    "LayeredCandidateVerificationResult",
    "LayeredPhysicalValidation",
    "verify_layered_candidate",
]
