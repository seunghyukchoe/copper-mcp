"""Internal bounded worker for durable optimization lifecycle metadata.

The executor is a trusted host callback, never an MCP argument.  It retains every private input
and raw output; this worker can publish only a validated :class:`OptimizationPackage`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Final

from pydantic import ValidationError

from copper_mcp.optimization import lifecycle
from copper_mcp.optimization.contracts import OptimizationError, OptimizationRequest
from copper_mcp.optimization.lifecycle import (
    TERMINAL,
    FailureCode,
    JobStatus,
    OptimizationJobRecord,
    ResourceUsage,
    advance_job,
    fail_job,
)
from copper_mcp.optimization.package import OptimizationPackage
from copper_mcp.optimization.repository import (
    OptimizationJobConflictError,
    OptimizationJobLease,
    OptimizationJobLeaseError,
    OptimizationJobRepository,
    OptimizationJobUnavailableError,
)

_EXECUTOR_FAILURES: Final = frozenset(
    {
        "stale_revision",
        "budget_exhausted",
        "unsupported_geometry",
        "backend_failure",
        "judge_failed",
        "required_domain_inconclusive",
        "invalid_candidate",
    }
)


class OptimizationExecutionError(OptimizationError):
    """Typed, non-echoing executor failure; diagnostic text is never persisted."""

    def __init__(self, code: FailureCode) -> None:
        if code not in _EXECUTOR_FAILURES:
            raise ValueError("optimization execution failure code is unsupported")
        super().__init__("optimization execution failed")
        self.code = code


class OptimizationJobCancelledError(OptimizationError):
    """Cooperative stop raised after a durable cancellation wins."""


class OptimizationJobLeaseLostError(OptimizationError):
    """The executing callback no longer owns the durable fence."""


class OptimizationExecutionProbe:
    """Lease, cancellation, lifecycle, and cumulative-budget surface for one callback."""

    def __init__(
        self,
        repository: OptimizationJobRepository,
        lease: OptimizationJobLease,
        request: OptimizationRequest,
        owner_binding: str,
        *,
        absolute_deadline_ms: int | None = None,
    ) -> None:
        self._repository = repository
        self._lease = lease
        self._request = request
        self._owner_binding = owner_binding
        self._started_ms = repository.monotonic_ms()
        request_deadline = self._started_ms + request.limits.max_runtime_ms
        if absolute_deadline_ms is None:
            self._deadline_ms = request_deadline
        elif (
            isinstance(absolute_deadline_ms, bool)
            or not isinstance(absolute_deadline_ms, int)
            or absolute_deadline_ms < 0
        ):
            raise ValueError("optimization deadline is malformed")
        else:
            self._deadline_ms = min(request_deadline, absolute_deadline_ms)
        self._last_accounted_ms = self._started_ms
        self._last_remaining_ms = max(0, self._deadline_ms - self._started_ms)
        self._unaccounted_initial_ms = max(
            0, request.limits.max_runtime_ms - self._last_remaining_ms
        )

    @property
    def job_id(self) -> str:
        return self._lease.job_id

    @property
    def record(self) -> OptimizationJobRecord:
        return self._repository.lease_record(self._lease)

    def remaining_time_ms(self) -> int:
        """Return a non-increasing duration based only on a monotonic process clock."""

        remaining = max(0, self._deadline_ms - self._repository.monotonic_ms())
        self._last_remaining_ms = min(self._last_remaining_ms, remaining)
        return self._last_remaining_ms

    def cancelled(self) -> bool:
        if self.remaining_time_ms() == 0:
            return True
        try:
            self._repository.lease_record(self._lease)
        except (
            OptimizationJobConflictError,
            OptimizationJobLeaseError,
            OptimizationJobUnavailableError,
        ):
            return True
        return False

    def checkpoint(self) -> OptimizationJobRecord:
        """Renew a live lease or stop on cancellation, fencing loss, or runtime exhaustion."""

        remaining_time_ms = self.remaining_time_ms()
        if remaining_time_ms == 0:
            raise OptimizationExecutionError("budget_exhausted")
        try:
            self._lease = self._repository.heartbeat(
                replace(self._lease, lease_ms=min(self._lease.lease_ms, remaining_time_ms))
            )
            return self._repository.lease_record(self._lease)
        except (
            OptimizationJobConflictError,
            OptimizationJobLeaseError,
            OptimizationJobUnavailableError,
        ) as error:
            try:
                record = self._repository.get(self.job_id, self._owner_binding)
            except OptimizationJobUnavailableError:
                raise OptimizationJobLeaseLostError(
                    "optimization execution lease was lost"
                ) from error
            if record.status == "cancelled":
                raise OptimizationJobCancelledError("optimization job was cancelled") from error
            raise OptimizationJobLeaseLostError("optimization execution lease was lost") from error

    def _charge_with_elapsed(self, charge: ResourceUsage | None) -> ResourceUsage:
        current = self._repository.monotonic_ms()
        elapsed = max(0, current - self._last_accounted_ms)
        supplied = ResourceUsage.model_validate(charge) if charge is not None else ResourceUsage()
        try:
            combined = supplied.plus(
                ResourceUsage(runtime_ms=elapsed + self._unaccounted_initial_ms)
            )
        except ValidationError as error:
            raise OptimizationExecutionError("budget_exhausted") from error
        self._last_accounted_ms = current
        self._unaccounted_initial_ms = 0
        return combined

    def _commit_same_phase(self, charge: ResourceUsage) -> OptimizationJobRecord:
        current = self.checkpoint()
        reserved = self._charge_with_elapsed(charge)
        try:
            usage = current.usage.plus(reserved)
        except ValidationError:
            replacement = lifecycle._failed(current, "budget_exhausted")
        else:
            replacement = (
                lifecycle._failed(current, "budget_exhausted")
                if usage.exhausted(self._request)
                else lifecycle._replace(
                    current,
                    revision=current.revision + 1,
                    usage=usage,
                )
            )
        result = self._repository.compare_and_swap(
            current,
            replacement,
            owner_binding=self._owner_binding,
            lease=self._lease,
        )
        self._lease = self._lease.with_revision(result.revision)
        return result

    def advance(
        self,
        next_status: JobStatus,
        *,
        charge: ResourceUsage | None = None,
        observed_board_revision: str | None = None,
        observed_snapshot_digest: str | None = None,
    ) -> OptimizationJobRecord:
        """Create a pure lifecycle transition, then commit it with the current fence."""

        if next_status in {"awaiting_approval", "approved", "completed"}:
            raise OptimizationError("optimization transition is worker-owned")
        current = self.checkpoint()
        replacement = advance_job(
            current,
            self._request,
            expected_revision=current.revision,
            owner_binding=self._owner_binding,
            next_status=next_status,
            observed_board_revision=(
                self._request.board_revision
                if observed_board_revision is None
                else observed_board_revision
            ),
            observed_snapshot_digest=(
                self._request.snapshot_digest
                if observed_snapshot_digest is None
                else observed_snapshot_digest
            ),
            charge=self._charge_with_elapsed(charge),
        )
        result = self._repository.compare_and_swap(
            current,
            replacement,
            owner_binding=self._owner_binding,
            lease=self._lease,
        )
        self._lease = self._lease.with_revision(result.revision)
        return result

    def reserve(
        self,
        charge: ResourceUsage,
        *,
        next_status: JobStatus | None = None,
        observed_board_revision: str | None = None,
        observed_snapshot_digest: str | None = None,
    ) -> OptimizationJobRecord:
        """Reserve cumulative work in-place, or in the CAS that enters a new phase."""

        if next_status is None:
            return self._commit_same_phase(charge)
        return self.advance(
            next_status,
            charge=charge,
            observed_board_revision=observed_board_revision,
            observed_snapshot_digest=observed_snapshot_digest,
        )


def _current_after_stop(
    repository: OptimizationJobRepository,
    job_id: str,
    owner_binding: str,
    error: Exception,
) -> OptimizationJobRecord:
    try:
        return repository.get(job_id, owner_binding)
    except OptimizationJobUnavailableError:
        raise OptimizationJobLeaseLostError("optimization execution lease was lost") from error


def _fail(
    probe: OptimizationExecutionProbe,
    code: FailureCode,
) -> OptimizationJobRecord:
    try:
        current = probe._repository.lease_record(probe._lease)
        elapsed_charge = probe._charge_with_elapsed(None)
        if any(getattr(elapsed_charge, name) != 0 for name in type(elapsed_charge).model_fields):
            try:
                usage = current.usage.plus(elapsed_charge)
            except ValidationError:
                charged = lifecycle._failed(current, "budget_exhausted")
            else:
                charged = (
                    lifecycle._failed(current, "budget_exhausted")
                    if usage.exhausted(probe._request)
                    else lifecycle._replace(
                        current,
                        revision=current.revision + 1,
                        usage=usage,
                    )
                )
            current = probe._repository.compare_and_swap(
                current,
                charged,
                owner_binding=probe._owner_binding,
                lease=probe._lease,
            )
            probe._lease = probe._lease.with_revision(current.revision)
            if current.status in TERMINAL:
                return current
        replacement = fail_job(
            current,
            probe._request,
            expected_revision=current.revision,
            owner_binding=probe._owner_binding,
            code=code,
        )
        result = probe._repository.compare_and_swap(
            current,
            replacement,
            owner_binding=probe._owner_binding,
            lease=probe._lease,
        )
        probe._lease = probe._lease.with_revision(result.revision)
        return result
    except (
        OptimizationJobConflictError,
        OptimizationJobLeaseError,
        OptimizationJobUnavailableError,
    ) as error:
        return _current_after_stop(
            probe._repository,
            probe.job_id,
            probe._owner_binding,
            error,
        )


def _finish_package(
    probe: OptimizationExecutionProbe,
    package: OptimizationPackage,
) -> OptimizationJobRecord:
    current = probe.checkpoint()
    charge = probe._charge_with_elapsed(None)
    try:
        package.require_reviewable_for(probe._request)
    except OptimizationError:
        # Let the lifecycle authority classify judge failure, required-domain uncertainty, or
        # another invalid candidate.  The resulting terminal CAS carries no package payload.
        rejected = advance_job(
            current,
            probe._request,
            expected_revision=current.revision,
            owner_binding=probe._owner_binding,
            next_status="awaiting_approval",
            observed_board_revision=probe._request.board_revision,
            observed_snapshot_digest=probe._request.snapshot_digest,
            charge=charge,
            package=package,
        )
        return probe._repository.compare_and_swap(
            current,
            rejected,
            owner_binding=probe._owner_binding,
            lease=probe._lease,
        )
    replacement = advance_job(
        current,
        probe._request,
        expected_revision=current.revision,
        owner_binding=probe._owner_binding,
        next_status="awaiting_approval",
        observed_board_revision=probe._request.board_revision,
        observed_snapshot_digest=probe._request.snapshot_digest,
        charge=charge,
        package=package,
    )
    return probe._repository.compare_and_swap(
        current,
        replacement,
        owner_binding=probe._owner_binding,
        lease=probe._lease,
        package=package,
    )


def execute_optimization_job(
    repository: OptimizationJobRepository,
    job_id: str,
    request: OptimizationRequest,
    owner_binding: str,
    executor: Callable[[OptimizationExecutionProbe], OptimizationPackage],
    *,
    absolute_deadline_ms: int | None = None,
) -> OptimizationJobRecord:
    """Execute one internal callback and publish only its reviewable package metadata."""

    if not isinstance(repository, OptimizationJobRepository):
        raise ValueError("optimization repository is malformed")
    request = OptimizationRequest.model_validate(request)
    if not callable(executor):
        raise ValueError("optimization executor is not callable")
    if absolute_deadline_ms is not None and (
        isinstance(absolute_deadline_ms, bool)
        or not isinstance(absolute_deadline_ms, int)
        or absolute_deadline_ms < 0
    ):
        raise ValueError("optimization deadline is malformed")
    now_monotonic = repository.monotonic_ms()
    remaining_before_claim = (
        request.limits.max_runtime_ms
        if absolute_deadline_ms is None
        else max(0, absolute_deadline_ms - now_monotonic)
    )
    lease = repository.claim(
        job_id,
        request,
        owner_binding,
        lease_ms=max(1, min(request.limits.max_runtime_ms, remaining_before_claim)),
    )
    probe = OptimizationExecutionProbe(
        repository,
        lease,
        request,
        owner_binding,
        absolute_deadline_ms=absolute_deadline_ms,
    )
    if probe.remaining_time_ms() == 0:
        return _fail(probe, "budget_exhausted")
    if probe._unaccounted_initial_ms:
        initial = probe._commit_same_phase(ResourceUsage())
        if initial.status in TERMINAL:
            return initial
    try:
        raw_package = executor(probe)
        package = OptimizationPackage.model_validate(raw_package)
        return _finish_package(probe, package)
    except OptimizationJobCancelledError as error:
        return _current_after_stop(repository, job_id, owner_binding, error)
    except OptimizationJobLeaseLostError as error:
        return _current_after_stop(repository, job_id, owner_binding, error)
    except OptimizationExecutionError as error:
        return _fail(probe, error.code)
    except ValidationError:
        return _fail(probe, "invalid_candidate")
    except OptimizationError:
        # Lifecycle/package refusals are bounded invalid output unless the durable row already
        # records a terminal budget, revision, interruption, or cancellation outcome.
        record = repository.get(job_id, owner_binding)
        if record.status in TERMINAL:
            return record
        return _fail(probe, "invalid_candidate")
    except TimeoutError:
        return _fail(probe, "budget_exhausted")
    except Exception:
        return _fail(probe, "backend_failure")


__all__ = [
    "OptimizationExecutionError",
    "OptimizationExecutionProbe",
    "OptimizationJobCancelledError",
    "OptimizationJobLeaseLostError",
    "execute_optimization_job",
]
