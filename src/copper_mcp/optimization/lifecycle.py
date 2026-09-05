"""Pure CAS lifecycle for an optimization coordinator, not a durable executor.

A future repository must transact these records using their revision. Losing ephemeral inputs
on restart terminates a job as interrupted; it never reconstructs geometry from a durable row.
Observation digests and owner bindings are supplied by trusted host/worker reads, not MCP input.
"""

from __future__ import annotations

from typing import Annotated, Literal, Protocol, TypeAlias

from pydantic import Field, ValidationError, model_validator

from copper_mcp.optimization.contracts import (
    Backend,
    BackendVersion,
    ClosedModel,
    Counter,
    Digest,
    OptimizationError,
    OptimizationRequest,
    digest_document,
)
from copper_mcp.optimization.package import ObjectiveMetrics, OptimizationPackage

JobStatus: TypeAlias = Literal[
    "queued",
    "inspecting",
    "placing",
    "routing",
    "judging",
    "repairing",
    "awaiting_approval",
    "approved",
    "completed",
    "cancelled",
    "stale_revision",
    "budget_exhausted",
    "unsupported_geometry",
    "backend_failure",
    "failed",
]
FailureCode: TypeAlias = Literal[
    "cancelled",
    "stale_revision",
    "budget_exhausted",
    "unsupported_geometry",
    "backend_failure",
    "judge_failed",
    "required_domain_inconclusive",
    "invalid_candidate",
    "interrupted",
]
TERMINAL = frozenset(
    {
        "completed",
        "cancelled",
        "stale_revision",
        "budget_exhausted",
        "unsupported_geometry",
        "backend_failure",
        "failed",
    }
)
_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"inspecting"}),
    "inspecting": frozenset({"placing"}),
    "placing": frozenset({"routing"}),
    "routing": frozenset({"judging"}),
    "judging": frozenset({"repairing", "awaiting_approval"}),
    "repairing": frozenset({"routing"}),
    "approved": frozenset({"completed"}),
}


class ResourceUsage(ClosedModel):
    """Cumulative reservations. A new phase or repair never resets these counters."""

    runtime_ms: Counter = 0
    candidates: Counter = 0
    placement_evaluations: Counter = 0
    route_attempts: Counter = 0
    repair_rounds: Counter = 0
    expansions: Counter = 0
    obstacle_checks: Counter = 0
    external_output_bytes: Counter = 0

    def plus(self, other: ResourceUsage) -> ResourceUsage:
        return ResourceUsage.model_validate(
            {name: getattr(self, name) + getattr(other, name) for name in type(self).model_fields}
        )

    def exhausted(self, request: OptimizationRequest) -> bool:
        for name in type(self).model_fields:
            value = getattr(self, name)
            ceiling = getattr(request.limits, "max_" + name)
            if value > ceiling or (name == "runtime_ms" and value >= ceiling):
                return True
        return False


class BackendIdentity(ClosedModel):
    backend: Backend
    version: BackendVersion


class OptimizationJobRecord(ClosedModel):
    """The only retention projection: no request, refs, path, capability or geometry field."""

    schema_version: Literal["optimization/v1"]
    job_id: Digest
    owner_binding: Digest
    request_digest: Digest
    limits_digest: Digest
    board_revision: Digest
    snapshot_digest: Digest
    revision: Counter
    status: JobStatus
    usage: ResourceUsage
    failure_code: FailureCode | None = None
    package_digest: Digest | None = None
    judge_digest: Digest | None = None
    candidate_ids: Annotated[tuple[Digest, ...], Field(max_length=32)] = ()
    backend_versions: Annotated[tuple[BackendIdentity, ...], Field(max_length=32)] = ()
    metrics: ObjectiveMetrics | None = None
    approval_receipt_digest: Digest | None = None

    @model_validator(mode="after")
    def record_invariants(self) -> OptimizationJobRecord:
        expected = digest_document(
            "copper-mcp/optimization/v1/job",
            {
                "request_digest": self.request_digest,
                "owner_binding": self.owner_binding,
            },
        )
        if self.job_id != expected:
            raise ValueError("optimization job identity is invalid")
        failed = self.status in TERMINAL and self.status != "completed"
        if failed != (self.failure_code is not None):
            raise ValueError("optimization terminal outcome is inconsistent")
        if failed and self.status != "failed" and self.failure_code != self.status:
            raise ValueError("optimization failure classification is inconsistent")
        selected = self.status in ("awaiting_approval", "approved", "completed")
        fields_present = (
            self.package_digest is not None,
            self.judge_digest is not None,
            bool(self.candidate_ids),
            bool(self.backend_versions),
            self.metrics is not None,
        )
        if any(value != selected for value in fields_present):
            raise ValueError("optimization selection metadata is inconsistent")
        if (self.status in ("approved", "completed")) != (self.approval_receipt_digest is not None):
            raise ValueError("optimization approval metadata is inconsistent")
        return self


def _replace(record: OptimizationJobRecord, **updates: object) -> OptimizationJobRecord:
    # model_copy(update=...) deliberately bypasses validators; never use it for transitions.
    return OptimizationJobRecord.model_validate({**record.model_dump(), **updates})


def create_job(request: OptimizationRequest, *, owner_binding: str) -> OptimizationJobRecord:
    request = OptimizationRequest.model_validate(request)
    return OptimizationJobRecord(
        schema_version="optimization/v1",
        job_id=digest_document(
            "copper-mcp/optimization/v1/job",
            {
                "request_digest": request.digest,
                "owner_binding": owner_binding,
            },
        ),
        owner_binding=owner_binding,
        request_digest=request.digest,
        limits_digest=request.limits.digest,
        board_revision=request.board_revision,
        snapshot_digest=request.snapshot_digest,
        revision=0,
        status="queued",
        usage=ResourceUsage(),
    )


def _check(
    record: OptimizationJobRecord,
    request: OptimizationRequest,
    expected_revision: int,
    owner_binding: str,
) -> None:
    OptimizationJobRecord.model_validate(record)
    OptimizationRequest.model_validate(request)
    if (
        type(expected_revision) is not int
        or expected_revision != record.revision
        or owner_binding != record.owner_binding
        or request.digest != record.request_digest
        or request.board_revision != record.board_revision
        or request.snapshot_digest != record.snapshot_digest
        or request.limits.digest != record.limits_digest
    ):
        raise OptimizationError("optimization job is unavailable or conflicted")
    if record.status in TERMINAL:
        raise OptimizationError("optimization job is terminal")


def _failed(record: OptimizationJobRecord, code: FailureCode) -> OptimizationJobRecord:
    state = code if code in TERMINAL else "failed"
    return _replace(
        record,
        revision=record.revision + 1,
        status=state,
        failure_code=code,
        package_digest=None,
        judge_digest=None,
        candidate_ids=(),
        backend_versions=(),
        metrics=None,
        approval_receipt_digest=None,
    )


def fail_job(
    record: OptimizationJobRecord,
    request: OptimizationRequest,
    *,
    expected_revision: int,
    owner_binding: str,
    code: FailureCode,
) -> OptimizationJobRecord:
    _check(record, request, expected_revision, owner_binding)
    return _failed(record, code)


def _matches_observation(record: OptimizationJobRecord, board: str, snapshot: str) -> bool:
    return board == record.board_revision and snapshot == record.snapshot_digest


def advance_job(
    record: OptimizationJobRecord,
    request: OptimizationRequest,
    *,
    expected_revision: int,
    owner_binding: str,
    next_status: JobStatus,
    observed_board_revision: str,
    observed_snapshot_digest: str,
    charge: ResourceUsage | None = None,
    package: OptimizationPackage | None = None,
) -> OptimizationJobRecord:
    """Reserve bounded work before execution; approval is deliberately not an ordinary edge."""

    _check(record, request, expected_revision, owner_binding)
    if not _matches_observation(record, observed_board_revision, observed_snapshot_digest):
        return _failed(record, "stale_revision")
    if next_status not in _TRANSITIONS.get(record.status, frozenset()):
        raise OptimizationError("optimization transition is not permitted")
    reserved = ResourceUsage.model_validate(charge) if charge is not None else ResourceUsage()
    try:
        usage = record.usage.plus(reserved)
        if next_status == "routing":
            usage = usage.plus(ResourceUsage(route_attempts=1))
        if next_status == "repairing":
            usage = usage.plus(ResourceUsage(repair_rounds=1))
    except ValidationError:
        # Addition beyond the JSON-safe integer ceiling is exhaustion, never success.
        return _failed(record, "budget_exhausted")
    if usage.exhausted(request):
        # Reservation failed, so do not pretend that the over-budget work actually ran.
        return _failed(record, "budget_exhausted")
    if next_status in ("awaiting_approval", "completed"):
        if package is None:
            raise OptimizationError("optimization package is required")
        OptimizationPackage.model_validate(package)
        try:
            package.require_reviewable_for(request)
        except OptimizationError:
            if package.judge.aggregate_status == "fail":
                return _failed(record, "judge_failed")
            if package.judge.required_status == "inconclusive":
                return _failed(record, "required_domain_inconclusive")
            return _failed(record, "invalid_candidate")
        if next_status == "completed":
            if (
                record.package_digest != package.digest
                or record.judge_digest != package.judge.digest
            ):
                raise OptimizationError("optimization approved package changed")
        else:
            return _replace(
                record,
                revision=record.revision + 1,
                status=next_status,
                usage=usage,
                package_digest=package.digest,
                judge_digest=package.judge.digest,
                candidate_ids=(package.binding.digest, *package.alternate_candidate_ids),
                backend_versions=tuple(
                    BackendIdentity(backend=p.backend, version=p.version)
                    for p in package.backend_provenance
                ),
                metrics=package.metrics,
            )
    return _replace(record, revision=record.revision + 1, status=next_status, usage=usage)


class ApprovalConsumer(Protocol):
    def consume(
        self,
        record: OptimizationJobRecord,
        package: OptimizationPackage,
        capability: str,
        *,
        owner_binding: str,
    ) -> str: ...


def approve_job(
    record: OptimizationJobRecord,
    request: OptimizationRequest,
    *,
    expected_revision: int,
    owner_binding: str,
    package: OptimizationPackage,
    capability: str,
    authority: ApprovalConsumer,
    observed_board_revision: str,
    observed_snapshot_digest: str,
) -> OptimizationJobRecord:
    """Human consent acknowledges this exact package; it changes no evidence or apply token."""

    _check(record, request, expected_revision, owner_binding)
    if not _matches_observation(record, observed_board_revision, observed_snapshot_digest):
        return _failed(record, "stale_revision")
    if record.status != "awaiting_approval":
        raise OptimizationError("optimization job is not awaiting approval")
    OptimizationPackage.model_validate(package)
    package.require_reviewable_for(request)
    if record.package_digest != package.digest or record.judge_digest != package.judge.digest:
        raise OptimizationError("optimization package binding is invalid")
    receipt = authority.consume(record, package, capability, owner_binding=owner_binding)
    return _replace(
        record, revision=record.revision + 1, status="approved", approval_receipt_digest=receipt
    )
