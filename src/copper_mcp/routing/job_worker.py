"""Single-worker execution and lease policy for durable routing jobs.

The :mod:`copper_mcp.routing.jobs` module intentionally owns the durable state machine but
does not own a queue or an executor.  This module is the small, protocol-independent bridge
between those two concerns.  A worker claims one queued record with the store's CAS transition,
passes a cancellation probe to a caller-owned executor, and publishes only the resulting
candidate identity through ``RoutingJobStore.complete``.  Neither board bytes nor candidate
geometry are retained by this module.

Leases are deliberately represented by the worker's bounded in-memory claim and by the
``updated_at_ms`` timestamp in the redacted job record.  A new worker can therefore detect an
orphaned ``running`` record after a crash and close it as a bounded ``worker_error``.  Closing an
orphan is safer than silently retrying against a potentially changed board; a caller can submit a
new immutable job specification after inspecting the failure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, TypeAlias

from copper_mcp.routing.jobs import (
    Candidate,
    RoutingJobConflictError,
    RoutingJobError,
    RoutingJobFailureCode,
    RoutingJobNotFoundError,
    RoutingJobRecord,
    RoutingJobStateError,
    RoutingJobStatus,
    RoutingJobStore,
)

_MAX_SAFE_INT: Final = (1 << 53) - 1
_MAX_LEASE_MS: Final = 86_400_000
_MAX_POLL_MS: Final = 60_000
_MAX_FAILURE_MESSAGE: Final = 256


def _bounded_integer(
    name: str,
    value: object,
    *,
    minimum: int = 0,
    maximum: int = _MAX_SAFE_INT,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported integer range")
    return value


def _failure_message(code: RoutingJobFailureCode) -> str:
    """Return a stable, non-echoing diagnostic for a worker terminal state."""

    messages = {
        RoutingJobFailureCode.INVALID_REQUEST: "routing request was rejected",
        RoutingJobFailureCode.STALE_REVISION: "routing source revision is stale",
        RoutingJobFailureCode.UNSUPPORTED: "routing geometry is unsupported",
        RoutingJobFailureCode.NO_PATH: "routing search found no path",
        RoutingJobFailureCode.SEARCH_BUDGET_EXCEEDED: "routing search budget was exceeded",
        RoutingJobFailureCode.OBSTACLE_BUDGET_EXCEEDED: "routing obstacle budget was exceeded",
        RoutingJobFailureCode.WORKER_ERROR: "routing worker failed",
        RoutingJobFailureCode.CANCELLED: "routing job was cancelled",
    }
    return messages[code]


RoutingJobExecutor: TypeAlias = Callable[["CancellationProbe"], Candidate]


class RoutingJobExecutionError(RoutingJobError):
    """Expected, bounded failure raised by a route executor.

    ``CANCELLED`` is intentionally not accepted here: cancellation is a state transition owned
    by the worker and must acknowledge the caller's ``cancel_requested`` record.
    """

    def __init__(self, code: RoutingJobFailureCode, message: str) -> None:
        if not isinstance(code, RoutingJobFailureCode) or code is RoutingJobFailureCode.CANCELLED:
            raise ValueError("execution failure code is unsupported")
        if (
            not isinstance(message, str)
            or not 1 <= len(message) <= _MAX_FAILURE_MESSAGE
            or any(ord(character) < 0x20 for character in message)
        ):
            raise ValueError("execution failure message is malformed")
        super().__init__(message)
        self.code = code
        self.message = message


class RoutingJobCancelledError(RoutingJobError):
    """Optional cooperative signal an executor may raise after observing its probe."""


class RoutingJobAlreadyClaimedError(RoutingJobError):
    """Raised when this worker or another live worker owns a non-expired lease."""


class RoutingJobLeaseExpiredError(RoutingJobError):
    """Raised when a caller tries to use an expired in-memory lease."""


@dataclass(frozen=True, slots=True)
class RoutingJobLease:
    """The opaque claim token held by one worker invocation."""

    job_id: str
    revision: int
    attempt: int
    claimed_at_ms: int
    expires_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not self.job_id.startswith("sha256:"):
            raise ValueError("lease job ID is malformed")
        _bounded_integer("lease revision", self.revision, minimum=1)
        _bounded_integer("lease attempt", self.attempt, minimum=1)
        _bounded_integer("lease claim timestamp", self.claimed_at_ms)
        _bounded_integer("lease expiry timestamp", self.expires_at_ms)
        if self.expires_at_ms <= self.claimed_at_ms:
            raise ValueError("lease expiry must follow its claim timestamp")


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    """Ceilings for one worker invocation and its cancellation polling helper."""

    lease_ms: int = 30_000
    poll_ms: int = 25

    def __post_init__(self) -> None:
        _bounded_integer("worker lease", self.lease_ms, minimum=1, maximum=_MAX_LEASE_MS)
        _bounded_integer("worker poll interval", self.poll_ms, minimum=1, maximum=_MAX_POLL_MS)


Clock = Callable[[], int]
Sleeper = Callable[[float], None]
_DEFAULT_WORKER_LIMITS: Final = WorkerLimits()


class CancellationProbe:
    """Read-only, cooperative cancellation and lease probe supplied to executors."""

    def __init__(
        self,
        worker: RoutingJobWorker,
        lease: RoutingJobLease,
    ) -> None:
        self._worker = worker
        self._lease = lease

    @property
    def job_id(self) -> str:
        return self._lease.job_id

    def is_cancelled(self) -> bool:
        """Return true for caller cancellation, expiry, or disappearance of the job."""

        if self._worker._clock() >= self._lease.expires_at_ms:
            return True
        try:
            record = self._worker._store.get(self._lease.job_id, now_ms=self._worker._clock())
        except RoutingJobNotFoundError:
            return True
        return record.status in {
            RoutingJobStatus.CANCEL_REQUESTED,
            RoutingJobStatus.CANCELLED,
        }

    def wait(self, timeout_ms: int) -> bool:
        """Poll cancellation for at most ``timeout_ms`` using injected time primitives."""

        timeout = _bounded_integer("cancellation wait", timeout_ms, maximum=_MAX_SAFE_INT)
        deadline = self._worker._clock() + timeout
        _bounded_integer("cancellation deadline", deadline)
        while not self.is_cancelled():
            now = self._worker._clock()
            if now >= deadline:
                break
            remaining = min(self._worker._limits.poll_ms, deadline - now)
            self._worker._sleep(remaining / 1000.0)
        return self.is_cancelled()


class RoutingJobWorker:
    """Execute at most one routing job at a time with CAS-backed claims."""

    def __init__(
        self,
        store: RoutingJobStore,
        *,
        limits: WorkerLimits = _DEFAULT_WORKER_LIMITS,
        clock: Clock | None = None,
        sleep: Sleeper | None = None,
    ) -> None:
        if not isinstance(store, RoutingJobStore):
            raise ValueError("routing job store is malformed")
        if not isinstance(limits, WorkerLimits):
            raise ValueError("worker limits are malformed")
        self._store = store
        self._limits = limits
        self._clock = clock or _wall_clock_ms
        self._sleep = sleep or _sleep_seconds
        self._active: RoutingJobLease | None = None

    @property
    def active_lease(self) -> RoutingJobLease | None:
        """Return the current claim token without exposing board or candidate content."""

        return self._active

    def claim(self, job_id: str) -> RoutingJobLease:
        """Atomically claim a queued job, or close an orphaned expired running job."""

        now = self._now()
        if self._active is not None:
            if self._active.job_id == job_id and now < self._active.expires_at_ms:
                raise RoutingJobAlreadyClaimedError("worker already owns this routing job")
            raise RoutingJobAlreadyClaimedError("worker already owns another routing job")
        record = self._store.get(job_id, now_ms=now)
        if record.status is RoutingJobStatus.RUNNING:
            if now - record.updated_at_ms >= self._limits.lease_ms:
                self._store.fail(
                    job_id,
                    RoutingJobFailureCode.WORKER_ERROR,
                    "routing worker lease expired before completion",
                    expected_revision=record.revision,
                    now_ms=now,
                )
                raise RoutingJobLeaseExpiredError("routing job lease expired and was closed")
            raise RoutingJobAlreadyClaimedError("routing job is already running")
        if record.status is not RoutingJobStatus.QUEUED:
            raise RoutingJobStateError("only a queued job can be claimed")
        running = self._store.start(job_id, expected_revision=record.revision, now_ms=now)
        lease = RoutingJobLease(
            job_id=running.spec.job_id,
            revision=running.revision,
            attempt=running.attempt,
            claimed_at_ms=now,
            expires_at_ms=now + self._limits.lease_ms,
        )
        self._active = lease
        return lease

    def recover_expired(self, job_id: str) -> RoutingJobRecord:
        """Close a stale running record after a worker crash or lease timeout."""

        now = self._now()
        record = self._store.get(job_id, now_ms=now)
        if record.status is RoutingJobStatus.CANCEL_REQUESTED:
            if now - record.updated_at_ms < self._limits.lease_ms:
                raise RoutingJobLeaseExpiredError("routing job lease has not expired")
            return self._store.acknowledge_cancel(
                job_id,
                expected_revision=record.revision,
                now_ms=now,
            )
        if record.status is not RoutingJobStatus.RUNNING:
            return record
        if now - record.updated_at_ms < self._limits.lease_ms:
            raise RoutingJobLeaseExpiredError("routing job lease has not expired")
        return self._store.fail(
            job_id,
            RoutingJobFailureCode.WORKER_ERROR,
            "routing worker lease expired before completion",
            expected_revision=record.revision,
            now_ms=now,
        )

    def execute(self, job_id: str, executor: RoutingJobExecutor) -> RoutingJobRecord:
        """Claim, execute, and publish one candidate through the existing CAS store."""

        if not callable(executor):
            raise ValueError("routing job executor is not callable")
        lease = self.claim(job_id)
        probe = CancellationProbe(self, lease)
        try:
            candidate = executor(probe)
        except RoutingJobCancelledError:
            return self._cancel_or_expire(lease)
        except RoutingJobExecutionError as error:
            return self._fail_or_cancel(lease, error.code, _failure_message(error.code))
        except TimeoutError:
            return self._fail_or_cancel(
                lease,
                RoutingJobFailureCode.SEARCH_BUDGET_EXCEEDED,
                "routing executor exceeded its bounded time budget",
            )
        except Exception:
            return self._fail_or_cancel(
                lease,
                RoutingJobFailureCode.WORKER_ERROR,
                _failure_message(RoutingJobFailureCode.WORKER_ERROR),
            )
        if probe.is_cancelled():
            return self._cancel_or_expire(lease)
        try:
            completed = self._store.complete(
                job_id,
                candidate,
                expected_revision=lease.revision,
                now_ms=self._now(),
            )
        except (RoutingJobConflictError, RoutingJobStateError):
            return self._resolve_publish_race(lease)
        except (RoutingJobError, TypeError, ValueError):
            # Candidate identity/base/endpoints are untrusted at this boundary.  Do not leave a
            # job in ``running`` when publication rejects malformed or stale executor output, and
            # do not persist the executor's raw diagnostic text.
            return self._fail_or_cancel(
                lease,
                RoutingJobFailureCode.INVALID_REQUEST,
                "routing executor returned an invalid candidate",
            )
        finally:
            self._active = None
        return completed

    def _resolve_publish_race(self, lease: RoutingJobLease) -> RoutingJobRecord:
        try:
            record = self._store.get(lease.job_id, now_ms=self._now())
            if record.status is RoutingJobStatus.CANCEL_REQUESTED:
                return self._store.acknowledge_cancel(
                    lease.job_id,
                    expected_revision=record.revision,
                    now_ms=self._now(),
                )
            raise RoutingJobConflictError("routing candidate publication lost its CAS race")
        finally:
            # A race loser may also discover that the job expired between publication and lookup.
            # Clear the in-memory lease even when the durable row is already gone, otherwise this
            # worker remains permanently unavailable for later jobs.
            self._active = None

    def _cancel_or_expire(self, lease: RoutingJobLease) -> RoutingJobRecord:
        try:
            record = self._store.get(lease.job_id, now_ms=self._now())
            if self._now() >= lease.expires_at_ms:
                if record.status is RoutingJobStatus.RUNNING:
                    result = self._store.fail(
                        lease.job_id,
                        RoutingJobFailureCode.WORKER_ERROR,
                        "routing worker lease expired before completion",
                        expected_revision=record.revision,
                        now_ms=self._now(),
                    )
                elif record.status is RoutingJobStatus.CANCEL_REQUESTED:
                    # Expiry must not strand a cancellation request in a non-terminal state.
                    # A worker that dies after the caller's cancel CAS still leaves the store
                    # responsible for acknowledging that terminal outcome.
                    result = self._store.acknowledge_cancel(
                        lease.job_id,
                        expected_revision=record.revision,
                        now_ms=self._now(),
                    )
                else:
                    result = record
            elif record.status is RoutingJobStatus.CANCEL_REQUESTED:
                result = self._store.acknowledge_cancel(
                    lease.job_id,
                    expected_revision=record.revision,
                    now_ms=self._now(),
                )
            else:
                result = self._store.fail(
                    lease.job_id,
                    RoutingJobFailureCode.WORKER_ERROR,
                    "executor stopped without requesting cancellation",
                    expected_revision=record.revision,
                    now_ms=self._now(),
                )
        finally:
            self._active = None
        return result

    def _fail_or_cancel(
        self,
        lease: RoutingJobLease,
        code: RoutingJobFailureCode,
        message: str,
    ) -> RoutingJobRecord:
        if CancellationProbe(self, lease).is_cancelled():
            return self._cancel_or_expire(lease)
        try:
            result = self._store.fail(
                lease.job_id,
                code,
                message,
                expected_revision=lease.revision,
                now_ms=self._now(),
            )
        finally:
            self._active = None
        return result

    def _now(self) -> int:
        return _bounded_integer("worker timestamp", self._clock())


def _wall_clock_ms() -> int:
    import time

    return time.time_ns() // 1_000_000


def _sleep_seconds(seconds: float) -> None:
    import time

    time.sleep(seconds)


__all__ = [
    "CancellationProbe",
    "Clock",
    "RoutingJobAlreadyClaimedError",
    "RoutingJobCancelledError",
    "RoutingJobExecutionError",
    "RoutingJobExecutor",
    "RoutingJobLease",
    "RoutingJobLeaseExpiredError",
    "RoutingJobWorker",
    "Sleeper",
    "WorkerLimits",
]
