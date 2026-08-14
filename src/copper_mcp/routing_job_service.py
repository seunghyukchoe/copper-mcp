"""Application service for bounded, file-backed routing jobs.

The service is intentionally narrower than the read-only preview surface: the first durable
queue accepts only the existing two-signal layered request, refuses live KiCad jobs, and runs the
same Board IR → deterministic router path used by ``preview_layered_route``.  It persists the
validated request before returning a job record, binds lookup/cancellation/export to a
caller-provided context digest, and never treats the deterministic job ID as authorization.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Final

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.config import Settings
from copper_mcp.layered_route_preview import (
    LayeredRoutePreviewError,
    LayeredRoutePreviewRequest,
    parse_layered_route_preview_request,
)
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.request_boundary import RequestError, mapping, text
from copper_mcp.routing import (
    LAYERED_ROUTER_VERSION,
    LAYERED_ROUTING_POLICY,
    LayeredBoardRouter,
    LayeredRouteFailureCode,
    LayeredRouteRequest,
    RoutingJobFailureCode,
    RoutingJobKind,
    RoutingJobLimits,
    RoutingJobRecord,
    RoutingJobRepository,
    RoutingJobStatus,
)
from copper_mcp.routing.job_repository import (
    RoutingCandidateExportUnavailableError,
    RoutingJobRequestEnvelope,
    RoutingJobRequestUnavailableError,
)
from copper_mcp.routing.job_worker import (
    CancellationProbe,
    RoutingJobCancelledError,
    RoutingJobExecutionError,
)
from copper_mcp.routing.layered_board_adapter import has_exactly_two_signal_layers
from copper_mcp.security import read_workspace_file

_SHA256_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_SAFE_INT: Final = (1 << 53) - 1
_DEFAULT_MAX_RUNTIME_MS: Final = 300_000
_JOB_SCHEMA_VERSION: Final = "1.0"
#: Preview-only request flags that never reach the persisted, redacted job envelope.
_PREVIEW_ONLY_CAPABILITIES: Final = frozenset({"include_drc", "include_fill_authority"})


class RoutingJobServiceError(ValueError):
    """Expected, non-echoing routing-job service refusal."""


class RoutingJobStaleError(RoutingJobServiceError):
    """The file or Board IR snapshot no longer matches the queued preconditions."""


class RoutingJobUnsupportedError(RoutingJobServiceError):
    """The requested board is outside the first durable-job subset."""


@dataclass(frozen=True, slots=True)
class PreparedLayeredRoutingJob:
    request: LayeredRoutePreviewRequest
    snapshot: Any
    board_revision: str
    relative_board_path: str


def _digest(name: str, value: object) -> str:
    candidate = text(name, value, maximum=71)
    if _SHA256_RE.fullmatch(candidate) is None:
        raise RoutingJobServiceError(f"{name} must be content-addressed with sha256")
    return candidate


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
        + b"\n"
    )


def _outer(
    name: str,
    payload: object,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> dict[str, Any]:
    try:
        fields = mapping(name, payload)
        allowed = frozenset((*required, *optional))
        unknown = set(fields) - allowed
        if unknown:
            raise RequestError(f"{name} has unsupported fields")
        missing = set(required) - set(fields)
        if missing:
            raise RequestError(f"{name} is missing required fields")
        return fields
    except RequestError as error:
        raise RoutingJobServiceError(str(error)) from error


def _authorization(fields: Mapping[str, Any]) -> str:
    return _digest("authorization_digest", fields.get("authorization_digest"))


def _lookup_job(
    repository: RoutingJobRepository,
    job_id: Any,
    authorization_digest: Any,
) -> tuple[RoutingJobRecord, RoutingJobRequestEnvelope]:
    """Use the repository's purge-first handle boundary for public job lookups."""

    try:
        return repository.get(job_id, authorization_digest)
    except RoutingJobRequestUnavailableError:
        raise RoutingJobServiceError("routing job is unavailable") from None


def _prepare_layered_job(payload: object, settings: Settings) -> PreparedLayeredRoutingJob:
    if not isinstance(settings, Settings):
        raise RoutingJobServiceError("routing job settings are malformed")
    try:
        request = parse_layered_route_preview_request(payload)
    except LayeredRoutePreviewError as error:
        raise RoutingJobServiceError("routing job request is invalid") from error
    if request.board == "live" or request.expect_session_revision is not None:
        raise RoutingJobServiceError("durable routing jobs require a file-backed board")
    if request.include_drc:
        raise RoutingJobServiceError(
            "durable routing jobs cannot request authoritative DRC evidence"
        )
    if request.include_fill_authority:
        # A job runs in a later process against bytes it re-reads, so the fill proof would have
        # to be re-established there rather than carried; and a candidate carrying a fill binding
        # cannot be replayed by anything that holds no fill evidence (ADR-0103, ADR-0106).
        raise RoutingJobServiceError("durable routing jobs cannot request zone fill authority")
    try:
        board = read_workspace_file(
            settings.workspace,
            request.board,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
        )
    except Exception as error:
        raise RoutingJobServiceError("routing job board is unavailable") from error
    workspace_root = settings.workspace.resolve(strict=True)
    relative_path = board.path.relative_to(workspace_root).as_posix()
    source = board.content
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"
    if request.expect_board_revision != board_revision:
        raise RoutingJobStaleError("routing job board revision is stale")
    profile = KiCadConstraintProfile(
        net_classes=(request.constraints,),
        default_net_class_id=request.constraints.id,
    )
    limits = parse_limits_for(settings)
    conversion = parse_kicad_bytes(source, profile, limits)
    if conversion.snapshot is None or conversion.diagnostics:
        raise RoutingJobUnsupportedError("routing job board is outside the supported Board IR")
    snapshot = conversion.snapshot
    if request.expect_snapshot_digest != snapshot.snapshot_digest:
        raise RoutingJobStaleError("routing job Board IR revision is stale")
    if not has_exactly_two_signal_layers(snapshot):
        raise RoutingJobUnsupportedError("routing job requires exactly two signal layers")
    pads = {pad.id: pad for pad in snapshot.content.pads}
    start_pad = pads.get(request.start_pad_id)
    end_pad = pads.get(request.end_pad_id)
    if (
        start_pad is None
        or end_pad is None
        or start_pad.net_id is None
        or start_pad.net_id != end_pad.net_id
    ):
        raise RoutingJobServiceError("routing job endpoints are not pads on one common net")
    return PreparedLayeredRoutingJob(
        request=replace(request, board=relative_path),
        snapshot=snapshot,
        board_revision=board_revision,
        relative_board_path=relative_path,
    )


def _spec(prepared: PreparedLayeredRoutingJob, settings: Settings) -> tuple[Any, dict[str, object]]:
    # ``include_drc`` and ``include_fill_authority`` are preview-only capabilities.  The parser
    # and job schema accept only their explicit false defaults, and omitting those defaults from
    # the persisted envelope prevents the redacted job repository from ever acquiring a silently
    # ignored authority flag.  Omission is also what keeps the persisted request digest a stable
    # content address across the addition of the second flag: an envelope that named it would
    # re-address every queued job in an existing ledger to record a capability nothing can grant.
    request_document = {
        key: value
        for key, value in prepared.request.to_dict().items()
        if value is not None and key not in _PREVIEW_ONLY_CAPABILITIES
    }
    request_digest = f"sha256:{hashlib.sha256(_canonical_bytes(request_document)).hexdigest()}"
    request = prepared.request
    limits = RoutingJobLimits(
        max_runtime_ms=min(
            _DEFAULT_MAX_RUNTIME_MS,
            max(1, settings.max_route_preview_seconds * 1_000),
        ),
        max_attempts=1,
        max_expansions=request.settings.max_expansions,
        max_obstacle_checks=request.settings.max_obstacle_checks,
    )
    snapshot = prepared.snapshot
    pads = {pad.id: pad for pad in snapshot.content.pads}
    start = pads[request.start_pad_id]
    assert start.net_id is not None
    from copper_mcp.routing.jobs import RoutingJobSpec

    return (
        RoutingJobSpec.create(
            board_revision=prepared.board_revision,
            snapshot_digest=request.expect_snapshot_digest,
            start_pad_id=request.start_pad_id,
            end_pad_id=request.end_pad_id,
            request_digest=request_digest,
            request_kind=RoutingJobKind.LAYERED,
            backend=LAYERED_ROUTER_VERSION,
            router_version=LAYERED_ROUTER_VERSION,
            policy=LAYERED_ROUTING_POLICY,
            seed=request.seed,
            limits=limits,
        ),
        request_document,
    )


def _job_document(
    record: RoutingJobRecord,
    *,
    request: Mapping[str, object] | None = None,
) -> dict[str, object]:
    spec = record.spec
    return {
        "schema_version": _JOB_SCHEMA_VERSION,
        "job_id": spec.job_id,
        "status": record.status.value,
        "revision": record.revision,
        "attempt": record.attempt,
        "created_at_ms": record.created_at_ms,
        "updated_at_ms": record.updated_at_ms,
        "request_digest": spec.request_digest,
        "request_kind": spec.request_kind.value,
        "board_revision": spec.board_revision,
        "snapshot_digest": spec.snapshot_digest,
        "start_pad_id": spec.start_pad_id,
        "end_pad_id": spec.end_pad_id,
        "candidate_id": record.candidate_id,
        "candidate_base_revision": record.candidate_base_revision,
        "diagnostic_code": (
            None if record.diagnostic_code is None else record.diagnostic_code.value
        ),
        "diagnostic_message": record.diagnostic_message,
        "cancel_reason": record.cancel_reason,
        "request": None if request is None else dict(request),
    }


def start_routing_job(
    payload: object,
    settings: Settings,
    repository: RoutingJobRepository,
) -> dict[str, object]:
    fields = _outer("routing job start", payload, ("request", "authorization_digest"))
    authorization = _authorization(fields)
    prepared = _prepare_layered_job(fields["request"], settings)
    spec, request_document = _spec(prepared, settings)
    record = repository.create(spec, request_document, authorization)
    return _job_document(record)


def get_routing_job(
    payload: object,
    repository: RoutingJobRepository,
) -> dict[str, object]:
    fields = _outer("routing job lookup", payload, ("job_id", "authorization_digest"))
    record, envelope = _lookup_job(
        repository,
        fields["job_id"],
        fields["authorization_digest"],
    )
    return _job_document(record, request=envelope.request)


def cancel_routing_job(
    payload: object,
    repository: RoutingJobRepository,
) -> dict[str, object]:
    fields = _outer(
        "routing job cancellation",
        payload,
        ("job_id", "authorization_digest"),
        optional=("reason",),
    )
    job_id = fields["job_id"]
    authorization = fields["authorization_digest"]
    # Resolve through the repository before validating optional cancellation text so malformed
    # input cannot bypass the request/lifecycle stores' purge-first retention boundary.
    record, _ = _lookup_job(repository, job_id, authorization)
    reason = fields.get("reason", "caller_requested")
    if (
        not isinstance(reason, str)
        or not 1 <= len(reason) <= 256
        or any(ord(character) < 0x20 for character in reason)
    ):
        raise RoutingJobServiceError("cancellation reason is malformed")
    cancelled = repository.jobs.request_cancel(
        job_id,
        expected_revision=record.revision,
        reason=reason,
    )
    return _job_document(cancelled)


def export_routing_candidate(
    payload: object,
    repository: RoutingJobRepository,
) -> dict[str, object]:
    fields = _outer(
        "routing candidate export",
        payload,
        ("job_id", "candidate_id", "authorization_digest"),
    )
    job_id = fields["job_id"]
    candidate_id = fields["candidate_id"]
    authorization = fields["authorization_digest"]
    try:
        # Export storage owns geometry retention, so it validates every public candidate handle
        # before the lifecycle lookup.  Nothing is returned until that lookup confirms the
        # completed job and caller context, preserving the no-geometry-on-miss boundary.
        candidate = repository.exports.get(job_id, candidate_id, authorization)
    except RoutingCandidateExportUnavailableError:
        candidate = None
    try:
        record, _ = _lookup_job(repository, job_id, authorization)
    except RoutingJobServiceError:
        raise RoutingJobServiceError("routing candidate export is unavailable") from None
    if (
        candidate is None
        or record.status is not RoutingJobStatus.COMPLETED
        or record.candidate_id != candidate_id
    ):
        raise RoutingJobServiceError("routing candidate export is unavailable")
    return candidate


def _failure_code(code: LayeredRouteFailureCode) -> RoutingJobFailureCode:
    mapping_codes = {
        LayeredRouteFailureCode.NO_PATH: RoutingJobFailureCode.NO_PATH,
        LayeredRouteFailureCode.SEARCH_BUDGET_EXCEEDED: (
            RoutingJobFailureCode.SEARCH_BUDGET_EXCEEDED
        ),
        LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED: (
            RoutingJobFailureCode.OBSTACLE_BUDGET_EXCEEDED
        ),
        LayeredRouteFailureCode.GRID_BUDGET_EXCEEDED: RoutingJobFailureCode.SEARCH_BUDGET_EXCEEDED,
        LayeredRouteFailureCode.CANCELLED: RoutingJobFailureCode.CANCELLED,
    }
    return mapping_codes.get(code, RoutingJobFailureCode.UNSUPPORTED)


def execute_routing_job(
    job_id: str,
    authorization_digest: str,
    settings: Settings,
    repository: RoutingJobRepository,
) -> RoutingJobRecord:
    """Run one persisted layered request through the same deterministic router as preview."""

    record, envelope = repository.get(job_id, authorization_digest)
    if record.status is not RoutingJobStatus.QUEUED:
        return record

    def executor(probe: CancellationProbe) -> Any:
        prepared = _prepare_layered_job(envelope.request, settings)
        request = prepared.request
        snapshot = prepared.snapshot
        pads = {pad.id: pad for pad in snapshot.content.pads}
        start_pad = pads[request.start_pad_id]
        assert start_pad.net_id is not None
        route_request = LayeredRouteRequest(
            board_revision=snapshot.snapshot_digest,
            expected_revision=snapshot.snapshot_digest,
            net_id=start_pad.net_id,
            start_pad_id=request.start_pad_id,
            end_pad_id=request.end_pad_id,
            seed=request.seed,
            start_layer_id=request.start_layer_id,
            end_layer_id=request.end_layer_id,
            grid_step_nm=request.grid_step_nm,
            settings=request.settings,
        )
        result = LayeredBoardRouter().propose(
            snapshot,
            route_request,
            cancelled=probe.is_cancelled,
        )
        if result.candidate is not None:
            return result.candidate
        assert result.diagnostic is not None
        if result.diagnostic.code is LayeredRouteFailureCode.CANCELLED:
            raise RoutingJobCancelledError("routing job cancelled")
        raise RoutingJobExecutionError(
            _failure_code(result.diagnostic.code),
            "layered routing did not produce a candidate",
        )

    return repository.execute(job_id, authorization_digest, executor)


__all__ = [
    "RoutingJobServiceError",
    "RoutingJobStaleError",
    "RoutingJobUnsupportedError",
    "cancel_routing_job",
    "execute_routing_job",
    "export_routing_candidate",
    "get_routing_job",
    "start_routing_job",
]
