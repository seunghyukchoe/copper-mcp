"""Pure CAS lifecycle for an optimization coordinator, not a durable executor.

A future repository must transact these records using their revision. Losing ephemeral inputs
on restart terminates a job as interrupted; it never reconstructs geometry from a durable row.
Observation digests and owner bindings are supplied by trusted host/worker reads, not MCP input.
"""

from __future__ import annotations

from typing import Annotated, Literal, Protocol, TypeAlias

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from copper_mcp.optimization.contracts import (
    AnyOptimizationRequest,
    Backend,
    BackendVersion,
    ClosedModel,
    Counter,
    Digest,
    OptimizationError,
    digest_document,
    validate_request,
)
from copper_mcp.optimization.package import (
    AnyOptimizationPackage,
    Comparison,
    ObjectiveMetrics,
    ObjectiveMetricsV2,
    validate_package,
)

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

    def exhausted(self, request: AnyOptimizationRequest) -> bool:
        for name in type(self).model_fields:
            value = getattr(self, name)
            ceiling = getattr(request.limits, "max_" + name)
            if value > ceiling or (name == "runtime_ms" and value >= ceiling):
                return True
        return False


class BackendIdentity(ClosedModel):
    backend: Backend
    version: BackendVersion


class _OptimizationJobFields(ClosedModel):
    """The only retention projection: no request, refs, path, capability or geometry field."""

    schema_version: Literal["optimization/v1", "optimization/v2"]
    job_id: Digest
    owner_binding: Digest
    request_digest: Digest
    limits_digest: Digest
    board_revision: Digest
    snapshot_digest: Digest
    revision: Counter
    status: JobStatus | Literal["evaluating"]
    usage: ResourceUsage
    failure_code: FailureCode | None = None
    package_digest: Digest | None = None
    judge_digest: Digest | None = None
    candidate_ids: Annotated[tuple[Digest, ...], Field(max_length=32)] = ()
    backend_versions: Annotated[tuple[BackendIdentity, ...], Field(max_length=32)] = ()
    metrics: ObjectiveMetrics | ObjectiveMetricsV2 | None = None
    approval_receipt_digest: Digest | None = None

    @model_validator(mode="after")
    def record_invariants(self) -> _OptimizationJobFields:
        expected = digest_document(
            f"copper-mcp/{self.schema_version}/job",
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


class OptimizationJobRecord(_OptimizationJobFields):
    """The only retention projection: no request, refs, path, capability or geometry field."""

    schema_version: Literal["optimization/v1"]
    status: JobStatus
    metrics: ObjectiveMetrics | None = None


class OptimizationJobRecordV2(_OptimizationJobFields):
    identity_namespace = "copper-mcp/optimization/v2/job-record"
    schema_version: Literal["optimization/v2"]
    metrics: ObjectiveMetricsV2 | None = None
    comparison: Comparison | None = None

    @model_validator(mode="after")
    def comparison_accounting(self) -> OptimizationJobRecordV2:
        comparison = self.comparison
        if comparison is None:
            if self.package_digest is not None:
                raise ValueError("selected v2 job is missing its comparison")
            return self
        basis = comparison.allocation
        if basis.limits.digest != self.limits_digest:
            raise ValueError("comparison limits differ from job limits")
        for field in ("expansions", "route_attempts", "repair_rounds"):
            if getattr(self.usage, field) < getattr(basis, "used_" + field) + sum(
                getattr(row.charged, field) for row in comparison.outcomes
            ):
                raise ValueError("comparison work was not charged to the root")
        if self.usage.obstacle_checks < basis.used_obstacle_checks + sum(
            row.charged.routing_checks + row.charged.validation_checks
            for row in comparison.outcomes
        ) or self.usage.candidates < basis.used_candidates + len(comparison.outcomes):
            raise ValueError("comparison work was not charged to the root")
        return self


AnyOptimizationJobRecord = OptimizationJobRecord | OptimizationJobRecordV2
_RECORD_ADAPTER: TypeAdapter[AnyOptimizationJobRecord] = TypeAdapter(AnyOptimizationJobRecord)


class BlockedEvaluationV2(ClosedModel):
    """Immutable terminal metadata, explicitly not a candidate or engineering authority."""

    identity_namespace = "copper-mcp/optimization/v2/blocked-evaluation"
    schema_version: Literal["optimization/v2"] = "optimization/v2"
    kind: Literal["blocked-evaluation"] = "blocked-evaluation"
    status: Literal["blocked"] = "blocked"
    record: OptimizationJobRecordV2
    blocking_codes: Annotated[tuple[FailureCode, ...], Field(min_length=1, max_length=9)]
    evidence_scope: Literal["terminal-metadata-only"] = "terminal-metadata-only"
    approval_authority: Literal["none"] = "none"
    apply_authority: Literal["none"] = "none"
    geometry_disclosure: Literal["not_disclosed"] = "not_disclosed"

    @model_validator(mode="after")
    def blocked_record(self) -> BlockedEvaluationV2:
        if self.record.status not in TERMINAL or self.record.status == "completed":
            raise ValueError("blocked evaluation requires an unsuccessful terminal job")
        if self.blocking_codes != _blocked_codes(self.record):
            raise ValueError("blocked evaluation differs from its terminal record")
        return self


def _blocked_codes(record: OptimizationJobRecordV2) -> tuple[FailureCode, ...]:
    if record.failure_code is None:
        raise ValueError("blocked evaluation is missing its failure")
    codes: set[FailureCode] = {record.failure_code}
    if record.comparison is not None:
        for row in record.comparison.outcomes:
            if row.status != "reviewable":
                codes.add(row.status)
    return tuple(sorted(codes))


def blocked_evaluation(record: AnyOptimizationJobRecord) -> BlockedEvaluationV2 | None:
    if (
        not isinstance(record, OptimizationJobRecordV2)
        or record.status not in TERMINAL
        or record.status == "completed"
    ):
        return None
    return BlockedEvaluationV2(record=record, blocking_codes=_blocked_codes(record))


def validate_record(value: object) -> AnyOptimizationJobRecord:
    return _RECORD_ADAPTER.validate_python(value)


def decode_record(value: str | bytes) -> AnyOptimizationJobRecord:
    return _RECORD_ADAPTER.validate_json(value)


def _replace(record: AnyOptimizationJobRecord, **updates: object) -> AnyOptimizationJobRecord:
    # model_copy(update=...) deliberately bypasses validators; never use it for transitions.
    return validate_record({**record.model_dump(), **updates})


def create_job(request: AnyOptimizationRequest, *, owner_binding: str) -> AnyOptimizationJobRecord:
    request = validate_request(request)
    return validate_record(
        {
            "schema_version": request.schema_version,
            "job_id": digest_document(
                f"copper-mcp/{request.schema_version}/job",
                {
                    "request_digest": request.digest,
                    "owner_binding": owner_binding,
                },
            ),
            "owner_binding": owner_binding,
            "request_digest": request.digest,
            "limits_digest": request.limits.digest,
            "board_revision": request.board_revision,
            "snapshot_digest": request.snapshot_digest,
            "revision": 0,
            "status": "queued",
            "usage": ResourceUsage(),
        }
    )


def _check(
    record: AnyOptimizationJobRecord,
    request: AnyOptimizationRequest,
    expected_revision: int,
    owner_binding: str,
) -> None:
    validate_record(record)
    validate_request(request)
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


def _failed(record: AnyOptimizationJobRecord, code: FailureCode) -> AnyOptimizationJobRecord:
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
    record: AnyOptimizationJobRecord,
    request: AnyOptimizationRequest,
    *,
    expected_revision: int,
    owner_binding: str,
    code: FailureCode,
) -> AnyOptimizationJobRecord:
    _check(record, request, expected_revision, owner_binding)
    return _failed(record, code)


def _matches_observation(record: AnyOptimizationJobRecord, board: str, snapshot: str) -> bool:
    return board == record.board_revision and snapshot == record.snapshot_digest


def advance_job(
    record: AnyOptimizationJobRecord,
    request: AnyOptimizationRequest,
    *,
    expected_revision: int,
    owner_binding: str,
    next_status: JobStatus | Literal["evaluating"],
    observed_board_revision: str,
    observed_snapshot_digest: str,
    charge: ResourceUsage | None = None,
    package: AnyOptimizationPackage | None = None,
) -> AnyOptimizationJobRecord:
    """Reserve bounded work before execution; approval is deliberately not an ordinary edge."""

    _check(record, request, expected_revision, owner_binding)
    if not _matches_observation(record, observed_board_revision, observed_snapshot_digest):
        return _failed(record, "stale_revision")
    transitions = _TRANSITIONS
    if request.schema_version == "optimization/v2":
        transitions = {
            **transitions,
            "placing": frozenset({"evaluating"}),
            "evaluating": frozenset({"awaiting_approval"}),
        }
    if next_status not in transitions.get(record.status, frozenset()):
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
        validate_package(package)
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
        record: AnyOptimizationJobRecord,
        package: AnyOptimizationPackage,
        capability: str,
        *,
        owner_binding: str,
    ) -> str: ...


def approve_job(
    record: AnyOptimizationJobRecord,
    request: AnyOptimizationRequest,
    *,
    expected_revision: int,
    owner_binding: str,
    package: AnyOptimizationPackage,
    capability: str,
    authority: ApprovalConsumer,
    observed_board_revision: str,
    observed_snapshot_digest: str,
) -> AnyOptimizationJobRecord:
    """Human consent acknowledges this exact package; it changes no evidence or apply token."""

    _check(record, request, expected_revision, owner_binding)
    if not _matches_observation(record, observed_board_revision, observed_snapshot_digest):
        return _failed(record, "stale_revision")
    if record.status != "awaiting_approval":
        raise OptimizationError("optimization job is not awaiting approval")
    validate_package(package)
    package.require_reviewable_for(request)
    if record.package_digest != package.digest or record.judge_digest != package.judge.digest:
        raise OptimizationError("optimization package binding is invalid")
    receipt = authority.consume(record, package, capability, owner_binding=owner_binding)
    return _replace(
        record, revision=record.revision + 1, status="approved", approval_receipt_digest=receipt
    )
