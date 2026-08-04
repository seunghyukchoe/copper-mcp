"""Revision-safe lifecycle contracts for bounded routing jobs.

This module deliberately contains a *record contract*, not a worker, queue, or filesystem
database.  A transport may persist :class:`RoutingJobRecord` as JSON and use the monotonically
increasing ``revision`` as its compare-and-swap token.  The record never stores board bytes or
model output.  A successful transition stores only the immutable candidate identity; a separate
candidate repository can retain the candidate when that capability is eventually authorized.

The separation is important for the routing trust boundary: workers may run for longer than an
MCP request, but they still route one immutable revision and can publish only an unapplied,
content-addressed candidate.  Applying copper, exporting a board, and authoritative DRC remain
separate capabilities.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, TypeAlias, cast

from copper_mcp.routing.astar import verify_candidate_id
from copper_mcp.routing.contracts import RouteCandidate
from copper_mcp.routing.layered_contracts import LayeredRouteCandidate, verify_layered_candidate_id

_SCHEMA_VERSION: Final = "0.1.0"
_SHA256_PREFIX: Final = "sha256:"
_SHA256_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_STABLE_NAME_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_PAD_ID_RE: Final = re.compile(r"^pad:[A-Za-z0-9_.:-]{1,160}$")
_MAX_SAFE_INT: Final = (1 << 53) - 1
_MAX_ATTEMPTS: Final = 16
_MAX_RUNTIME_MS: Final = 86_400_000
_MAX_DIAGNOSTIC_MESSAGE: Final = 256
_MAX_STORE_RECORDS: Final = 4_096
_MAX_STORE_TTL_MS: Final = 7 * 86_400_000
_MAX_RECORD_BYTES: Final = 128_000


def _integer(name: str, value: object, *, minimum: int = 0, maximum: int = _MAX_SAFE_INT) -> None:
    """Validate an integer without accepting JSON booleans as integers."""

    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported integer range")


def _digest(name: str, value: object) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be content-addressed with sha256")


def _optional_digest(name: str, value: object) -> None:
    if value is not None:
        _digest(name, value)


def _stable_name(name: str, value: object) -> None:
    if not isinstance(value, str) or _STABLE_NAME_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a stable lowercase identifier")


def _pad_id(name: str, value: object) -> None:
    if not isinstance(value, str) or _PAD_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a stable pad ID")


def _canonical_bytes(payload: object) -> bytes:
    """Encode a JSON-compatible payload deterministically for content addressing."""

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


class RoutingJobKind(StrEnum):
    """Stable request families supported by the job lifecycle."""

    SINGLE_LAYER = "single-layer"
    LAYERED = "layered"


class RoutingJobStatus(StrEnum):
    """State machine states persisted in a durable job record."""

    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RoutingJobFailureCode(StrEnum):
    """Non-echoing expected terminal failures for a routing job."""

    INVALID_REQUEST = "invalid_request"
    STALE_REVISION = "stale_revision"
    UNSUPPORTED = "unsupported"
    NO_PATH = "no_path"
    SEARCH_BUDGET_EXCEEDED = "search_budget_exceeded"
    OBSTACLE_BUDGET_EXCEEDED = "obstacle_budget_exceeded"
    WORKER_ERROR = "worker_error"
    CANCELLED = "cancelled"


_FIXED_FAILURE_MESSAGES: Final[dict[RoutingJobFailureCode, str]] = {
    RoutingJobFailureCode.INVALID_REQUEST: "routing executor returned an invalid candidate",
    RoutingJobFailureCode.STALE_REVISION: "routing source revision is stale",
    RoutingJobFailureCode.UNSUPPORTED: "routing geometry is unsupported",
    RoutingJobFailureCode.NO_PATH: "routing search found no path",
    RoutingJobFailureCode.SEARCH_BUDGET_EXCEEDED: "routing search budget was exceeded",
    RoutingJobFailureCode.OBSTACLE_BUDGET_EXCEEDED: "routing obstacle budget was exceeded",
    RoutingJobFailureCode.WORKER_ERROR: "routing worker failed",
}


class RoutingJobError(ValueError):
    """Base error for malformed records or invalid lifecycle transitions."""


class RoutingJobConflictError(RoutingJobError):
    """Raised when a caller tries to transition an old record revision."""


class RoutingJobStateError(RoutingJobError):
    """Raised when a transition is not legal for the current status."""


class RoutingJobNotFoundError(RoutingJobError):
    """Raised for both unknown and expired job IDs (without revealing which one occurred)."""


class RoutingJobLimitError(RoutingJobError):
    """Raised when the bounded job store has no available record slot."""


@dataclass(frozen=True, slots=True)
class RoutingJobLimits:
    """Explicit ceilings carried by a job so a worker cannot silently expand its budget."""

    max_runtime_ms: int = 300_000
    max_attempts: int = 1
    max_expansions: int = 100_000
    max_obstacle_checks: int = 2_000_000

    def __post_init__(self) -> None:
        _integer("maximum runtime", self.max_runtime_ms, minimum=1, maximum=_MAX_RUNTIME_MS)
        _integer("maximum attempts", self.max_attempts, minimum=1, maximum=_MAX_ATTEMPTS)
        _integer("maximum expansions", self.max_expansions, minimum=1, maximum=1_000_000)
        _integer(
            "maximum obstacle checks",
            self.max_obstacle_checks,
            minimum=1,
            maximum=10_000_000,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_attempts": self.max_attempts,
            "max_expansions": self.max_expansions,
            "max_obstacle_checks": self.max_obstacle_checks,
            "max_runtime_ms": self.max_runtime_ms,
        }

    @classmethod
    def from_dict(cls, payload: object) -> RoutingJobLimits:
        if not isinstance(payload, dict) or set(payload) != {
            "max_attempts",
            "max_expansions",
            "max_obstacle_checks",
            "max_runtime_ms",
        }:
            raise ValueError("routing job limits must be a closed object")
        return cls(
            max_attempts=payload["max_attempts"],
            max_expansions=payload["max_expansions"],
            max_obstacle_checks=payload["max_obstacle_checks"],
            max_runtime_ms=payload["max_runtime_ms"],
        )


_DEFAULT_LIMITS: Final = RoutingJobLimits()


def _spec_payload(
    *,
    board_revision: str,
    snapshot_digest: str | None,
    start_pad_id: str,
    end_pad_id: str,
    request_digest: str,
    request_kind: RoutingJobKind,
    backend: str,
    router_version: str,
    policy: str,
    seed: int,
    limits: RoutingJobLimits,
) -> dict[str, object]:
    return {
        "backend": backend,
        "board_revision": board_revision,
        "end_pad_id": end_pad_id,
        "limits": limits.to_dict(),
        "policy": policy,
        "request_digest": request_digest,
        "request_kind": request_kind.value,
        "router_version": router_version,
        "seed": seed,
        "snapshot_digest": snapshot_digest,
        "start_pad_id": start_pad_id,
    }


@dataclass(frozen=True, slots=True)
class RoutingJobSpec:
    """Immutable, content-addressed input identity for one bounded routing job.

    ``board_revision`` is the source/editor compare-and-swap digest.  When the route is derived
    from a Board IR snapshot, ``snapshot_digest`` is also required by the caller and becomes the
    candidate's expected base revision.  ``request_digest`` identifies the validated request
    stored by the caller; raw board bytes, net names, and model prompts are intentionally absent.
    """

    job_id: str
    board_revision: str
    snapshot_digest: str | None
    start_pad_id: str
    end_pad_id: str
    request_digest: str
    request_kind: RoutingJobKind
    backend: str
    router_version: str
    policy: str
    seed: int
    limits: RoutingJobLimits = RoutingJobLimits()

    def __post_init__(self) -> None:
        _digest("job ID", self.job_id)
        _digest("board revision", self.board_revision)
        _optional_digest("snapshot digest", self.snapshot_digest)
        _pad_id("start pad ID", self.start_pad_id)
        _pad_id("end pad ID", self.end_pad_id)
        if self.start_pad_id == self.end_pad_id:
            raise ValueError("routing job endpoints must be distinct")
        _digest("request digest", self.request_digest)
        if not isinstance(self.request_kind, RoutingJobKind):
            raise ValueError("routing job kind is unsupported")
        _stable_name("backend", self.backend)
        _stable_name("router version", self.router_version)
        _stable_name("routing policy", self.policy)
        _integer("job seed", self.seed)
        if not isinstance(self.limits, RoutingJobLimits):
            raise ValueError("routing job limits are malformed")
        identity_payload = _spec_payload(
            board_revision=self.board_revision,
            snapshot_digest=self.snapshot_digest,
            start_pad_id=self.start_pad_id,
            end_pad_id=self.end_pad_id,
            request_digest=self.request_digest,
            request_kind=self.request_kind,
            backend=self.backend,
            router_version=self.router_version,
            policy=self.policy,
            seed=self.seed,
            limits=self.limits,
        )
        expected_digest = hashlib.sha256(_canonical_bytes(identity_payload)).hexdigest()
        expected = f"{_SHA256_PREFIX}{expected_digest}"
        if self.job_id != expected:
            raise ValueError("job ID does not match its immutable specification")

    @classmethod
    def create(
        cls,
        *,
        board_revision: str,
        snapshot_digest: str | None,
        start_pad_id: str,
        end_pad_id: str,
        request_digest: str,
        request_kind: RoutingJobKind,
        backend: str,
        router_version: str,
        policy: str,
        seed: int = 0,
        limits: RoutingJobLimits = _DEFAULT_LIMITS,
    ) -> RoutingJobSpec:
        payload = _spec_payload(
            board_revision=board_revision,
            snapshot_digest=snapshot_digest,
            start_pad_id=start_pad_id,
            end_pad_id=end_pad_id,
            request_digest=request_digest,
            request_kind=request_kind,
            backend=backend,
            router_version=router_version,
            policy=policy,
            seed=seed,
            limits=limits,
        )
        job_id = f"{_SHA256_PREFIX}{hashlib.sha256(_canonical_bytes(payload)).hexdigest()}"
        return cls(
            job_id=job_id,
            board_revision=board_revision,
            snapshot_digest=snapshot_digest,
            start_pad_id=start_pad_id,
            end_pad_id=end_pad_id,
            request_digest=request_digest,
            request_kind=request_kind,
            backend=backend,
            router_version=router_version,
            policy=policy,
            seed=seed,
            limits=limits,
        )

    @property
    def expected_candidate_revision(self) -> str:
        """Return the revision a candidate must carry in its immutable base field."""

        return self.snapshot_digest or self.board_revision

    def to_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "board_revision": self.board_revision,
            "end_pad_id": self.end_pad_id,
            "job_id": self.job_id,
            "limits": self.limits.to_dict(),
            "policy": self.policy,
            "request_digest": self.request_digest,
            "request_kind": self.request_kind.value,
            "router_version": self.router_version,
            "schema_version": _SCHEMA_VERSION,
            "seed": self.seed,
            "snapshot_digest": self.snapshot_digest,
            "start_pad_id": self.start_pad_id,
        }

    @classmethod
    def from_dict(cls, payload: object) -> RoutingJobSpec:
        if not isinstance(payload, dict) or set(payload) != {
            "backend",
            "board_revision",
            "end_pad_id",
            "job_id",
            "limits",
            "policy",
            "request_digest",
            "request_kind",
            "router_version",
            "schema_version",
            "seed",
            "snapshot_digest",
            "start_pad_id",
        }:
            raise ValueError("routing job specification must be a closed object")
        if payload["schema_version"] != _SCHEMA_VERSION:
            raise ValueError("routing job specification schema version is unsupported")
        try:
            kind = RoutingJobKind(payload["request_kind"])
        except (TypeError, ValueError) as error:
            raise ValueError("routing job kind is unsupported") from error
        return cls(
            job_id=payload["job_id"],
            board_revision=payload["board_revision"],
            snapshot_digest=payload["snapshot_digest"],
            start_pad_id=payload["start_pad_id"],
            end_pad_id=payload["end_pad_id"],
            request_digest=payload["request_digest"],
            request_kind=kind,
            backend=payload["backend"],
            router_version=payload["router_version"],
            policy=payload["policy"],
            seed=payload["seed"],
            limits=RoutingJobLimits.from_dict(payload["limits"]),
        )


Candidate: TypeAlias = RouteCandidate | LayeredRouteCandidate


def _candidate_id(candidate: Candidate, spec: RoutingJobSpec) -> tuple[str, str]:
    """Verify one immutable candidate and return its ID and base revision."""

    if isinstance(candidate, RouteCandidate):
        candidate_kind = RoutingJobKind.SINGLE_LAYER
        verify_candidate_id(candidate)
    elif isinstance(candidate, LayeredRouteCandidate):
        candidate_kind = RoutingJobKind.LAYERED
        verify_layered_candidate_id(candidate)
    else:
        raise RoutingJobError("routing job completion requires a supported route candidate")
    if candidate_kind is not spec.request_kind:
        raise RoutingJobError("candidate kind does not match the routing job")
    if candidate.base_revision != spec.expected_candidate_revision:
        raise RoutingJobError("candidate base revision does not match the job's compare-and-swap")
    if candidate.start_pad_id != spec.start_pad_id or candidate.end_pad_id != spec.end_pad_id:
        raise RoutingJobError("candidate endpoints do not match the routing job")
    if candidate.router_version != spec.router_version:
        raise RoutingJobError("candidate router version does not match the routing job")
    if candidate.policy != spec.policy:
        raise RoutingJobError("candidate policy does not match the routing job")
    if candidate.seed != spec.seed:
        raise RoutingJobError("candidate seed does not match the routing job")
    if (
        candidate.metrics.expanded_states > spec.limits.max_expansions
        or candidate.metrics.obstacle_checks > spec.limits.max_obstacle_checks
    ):
        raise RoutingJobError("candidate work metrics exceed the routing job limits")
    return candidate.candidate_id, candidate.base_revision


@dataclass(frozen=True, slots=True)
class RoutingJobRecord:
    """One immutable state transition in a durable routing-job ledger.

    A record contains no candidate geometry.  ``candidate_id`` and ``candidate_base_revision``
    are enough to bind a separately stored candidate without making a job response a hidden
    board export.  Every transition increments ``revision`` and requires the caller's observed
    revision, giving stores a simple compare-and-swap guard against duplicate workers.
    """

    spec: RoutingJobSpec
    status: RoutingJobStatus = RoutingJobStatus.QUEUED
    revision: int = 0
    attempt: int = 0
    created_at_ms: int = 0
    updated_at_ms: int = 0
    candidate_id: str | None = None
    candidate_base_revision: str | None = None
    diagnostic_code: RoutingJobFailureCode | None = None
    diagnostic_message: str | None = None
    cancel_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.spec, RoutingJobSpec):
            raise ValueError("routing job record specification is malformed")
        if not isinstance(self.status, RoutingJobStatus):
            raise ValueError("routing job status is unsupported")
        _integer("job revision", self.revision)
        _integer("job attempt", self.attempt)
        _integer("created timestamp", self.created_at_ms)
        _integer("updated timestamp", self.updated_at_ms)
        if self.updated_at_ms < self.created_at_ms:
            raise ValueError("job update timestamp cannot precede creation")
        if self.attempt > self.spec.limits.max_attempts:
            raise ValueError("job attempts exceed the immutable job limit")
        _optional_digest("candidate ID", self.candidate_id)
        _optional_digest("candidate base revision", self.candidate_base_revision)
        if self.diagnostic_code is not None and not isinstance(
            self.diagnostic_code, RoutingJobFailureCode
        ):
            raise ValueError("job diagnostic code is unsupported")
        if self.diagnostic_message is not None and (
            not isinstance(self.diagnostic_message, str)
            or not 1 <= len(self.diagnostic_message) <= _MAX_DIAGNOSTIC_MESSAGE
            or any(ord(character) < 0x20 for character in self.diagnostic_message)
        ):
            raise ValueError("job diagnostic message is malformed")
        if self.cancel_reason is not None and (
            not isinstance(self.cancel_reason, str)
            or not 1 <= len(self.cancel_reason) <= _MAX_DIAGNOSTIC_MESSAGE
            or any(ord(character) < 0x20 for character in self.cancel_reason)
        ):
            raise ValueError("job cancellation reason is malformed")
        self._validate_state_shape()

    def _validate_state_shape(self) -> None:
        terminal = self.status in {
            RoutingJobStatus.COMPLETED,
            RoutingJobStatus.FAILED,
            RoutingJobStatus.CANCELLED,
        }
        if self.status is RoutingJobStatus.QUEUED:
            if self.revision != 0 or self.attempt != 0:
                raise ValueError("queued job must have revision and attempt zero")
        elif self.status is RoutingJobStatus.CANCELLED:
            if self.revision < 1:
                raise ValueError("cancelled job must have a positive revision")
        else:
            if self.revision < 1 or self.attempt < 1:
                raise ValueError("started job must have a positive revision and attempt")
        if self.status is RoutingJobStatus.COMPLETED:
            if self.candidate_id is None or self.candidate_base_revision is None:
                raise ValueError("completed job must bind a candidate")
            if any(
                value is not None
                for value in (self.diagnostic_code, self.diagnostic_message, self.cancel_reason)
            ):
                raise ValueError("completed job cannot carry failure or cancellation data")
        elif self.status is RoutingJobStatus.FAILED:
            if self.diagnostic_code is None or self.diagnostic_message is None:
                raise ValueError("failed job must carry a bounded diagnostic")
            if any(
                value is not None
                for value in (self.candidate_id, self.candidate_base_revision, self.cancel_reason)
            ):
                raise ValueError("failed job cannot bind a candidate or cancellation reason")
        elif self.status is RoutingJobStatus.CANCELLED:
            if self.diagnostic_code is not RoutingJobFailureCode.CANCELLED:
                raise ValueError("cancelled job must carry the cancelled diagnostic code")
            if self.cancel_reason is None or self.diagnostic_message is None:
                raise ValueError("cancelled job must carry bounded cancellation details")
            if any(
                value is not None for value in (self.candidate_id, self.candidate_base_revision)
            ):
                raise ValueError("cancelled job cannot bind a candidate")
        else:
            if terminal:
                raise AssertionError("terminal status set is inconsistent")
            if any(
                value is not None
                for value in (
                    self.candidate_id,
                    self.candidate_base_revision,
                    self.diagnostic_code,
                    self.diagnostic_message,
                )
            ):
                raise ValueError("non-terminal job cannot carry terminal outcome data")
            if self.status is RoutingJobStatus.CANCEL_REQUESTED and self.cancel_reason is None:
                raise ValueError("cancel-requested job must carry a bounded reason")
            if (
                self.status is not RoutingJobStatus.CANCEL_REQUESTED
                and self.cancel_reason is not None
            ):
                raise ValueError("only a cancel-requested job can carry a cancellation reason")

    def _check_revision(self, expected_revision: int) -> None:
        _integer("expected job revision", expected_revision)
        if expected_revision != self.revision:
            raise RoutingJobConflictError(
                f"job revision conflict: expected {expected_revision}, current {self.revision}"
            )

    def _time(self, now_ms: int) -> None:
        _integer("transition timestamp", now_ms)
        if now_ms < self.updated_at_ms:
            raise RoutingJobStateError("job transition timestamp moved backwards")

    def _transition(
        self, *, expected_revision: int, now_ms: int, **changes: object
    ) -> RoutingJobRecord:
        self._check_revision(expected_revision)
        self._time(now_ms)
        status = cast(RoutingJobStatus, changes.pop("status", self.status))
        attempt = cast(int, changes.pop("attempt", self.attempt))
        candidate_id = cast(str | None, changes.pop("candidate_id", self.candidate_id))
        candidate_base_revision = cast(
            str | None,
            changes.pop("candidate_base_revision", self.candidate_base_revision),
        )
        diagnostic_code = cast(
            RoutingJobFailureCode | None,
            changes.pop("diagnostic_code", self.diagnostic_code),
        )
        diagnostic_message = cast(
            str | None,
            changes.pop("diagnostic_message", self.diagnostic_message),
        )
        cancel_reason = cast(str | None, changes.pop("cancel_reason", self.cancel_reason))
        if changes:
            raise RoutingJobError("unknown routing job transition field")
        return RoutingJobRecord(
            spec=self.spec,
            revision=self.revision + 1,
            created_at_ms=self.created_at_ms,
            updated_at_ms=now_ms,
            status=status,
            attempt=attempt,
            candidate_id=candidate_id,
            candidate_base_revision=candidate_base_revision,
            diagnostic_code=diagnostic_code,
            diagnostic_message=diagnostic_message,
            cancel_reason=cancel_reason,
        )

    @classmethod
    def create(cls, spec: RoutingJobSpec, *, now_ms: int = 0) -> RoutingJobRecord:
        _integer("creation timestamp", now_ms)
        return cls(spec=spec, created_at_ms=now_ms, updated_at_ms=now_ms)

    def start(self, *, expected_revision: int, now_ms: int) -> RoutingJobRecord:
        if self.status is not RoutingJobStatus.QUEUED:
            raise RoutingJobStateError("only a queued job can start")
        return self._transition(
            expected_revision=expected_revision,
            now_ms=now_ms,
            status=RoutingJobStatus.RUNNING,
            attempt=self.attempt + 1,
            cancel_reason=None,
        )

    def request_cancel(
        self, *, expected_revision: int, now_ms: int, reason: str = "caller_requested"
    ) -> RoutingJobRecord:
        if self.status is RoutingJobStatus.QUEUED:
            return self._transition(
                expected_revision=expected_revision,
                now_ms=now_ms,
                status=RoutingJobStatus.CANCELLED,
                diagnostic_code=RoutingJobFailureCode.CANCELLED,
                diagnostic_message="routing job cancelled before execution",
                cancel_reason=reason,
            )
        if self.status is RoutingJobStatus.RUNNING:
            if not isinstance(reason, str) or not 1 <= len(reason) <= _MAX_DIAGNOSTIC_MESSAGE:
                raise RoutingJobError("cancellation reason is malformed")
            return self._transition(
                expected_revision=expected_revision,
                now_ms=now_ms,
                status=RoutingJobStatus.CANCEL_REQUESTED,
                cancel_reason=reason,
            )
        raise RoutingJobStateError("only queued or running jobs can be cancelled")

    def acknowledge_cancel(self, *, expected_revision: int, now_ms: int) -> RoutingJobRecord:
        if self.status is not RoutingJobStatus.CANCEL_REQUESTED:
            raise RoutingJobStateError("only a cancel-requested job can acknowledge cancellation")
        return self._transition(
            expected_revision=expected_revision,
            now_ms=now_ms,
            status=RoutingJobStatus.CANCELLED,
            diagnostic_code=RoutingJobFailureCode.CANCELLED,
            diagnostic_message="routing job cancellation acknowledged",
        )

    def complete(
        self, candidate: Candidate, *, expected_revision: int, now_ms: int
    ) -> RoutingJobRecord:
        if self.status is not RoutingJobStatus.RUNNING:
            raise RoutingJobStateError("only a running job can publish a candidate")
        candidate_id, candidate_revision = _candidate_id(candidate, self.spec)
        return self._transition(
            expected_revision=expected_revision,
            now_ms=now_ms,
            status=RoutingJobStatus.COMPLETED,
            candidate_id=candidate_id,
            candidate_base_revision=candidate_revision,
            diagnostic_code=None,
            diagnostic_message=None,
            cancel_reason=None,
        )

    def fail(
        self,
        code: RoutingJobFailureCode,
        message: str,
        *,
        expected_revision: int,
        now_ms: int,
    ) -> RoutingJobRecord:
        if self.status is not RoutingJobStatus.RUNNING:
            raise RoutingJobStateError("only a running job can fail")
        if not isinstance(code, RoutingJobFailureCode) or code is RoutingJobFailureCode.CANCELLED:
            raise RoutingJobError("failure code is unsupported for a failed job")
        if not isinstance(message, str):
            raise RoutingJobError("failure message is malformed")
        # The caller may have a useful local diagnostic, but it is never safe to persist
        # executor-provided text: it can contain board names, prompts, or credentials.  Store
        # only the fixed public message associated with the typed failure code.
        try:
            safe_message = _FIXED_FAILURE_MESSAGES[code]
        except KeyError as error:
            raise RoutingJobError("failure code has no public diagnostic") from error
        return self._transition(
            expected_revision=expected_revision,
            now_ms=now_ms,
            status=RoutingJobStatus.FAILED,
            candidate_id=None,
            candidate_base_revision=None,
            diagnostic_code=code,
            diagnostic_message=safe_message,
            cancel_reason=None,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a strict, redacted JSON record suitable for durable storage."""

        return {
            "attempt": self.attempt,
            "candidate_base_revision": self.candidate_base_revision,
            "candidate_id": self.candidate_id,
            "cancel_reason": self.cancel_reason,
            "created_at_ms": self.created_at_ms,
            "diagnostic_code": self.diagnostic_code.value if self.diagnostic_code else None,
            "diagnostic_message": self.diagnostic_message,
            "job_id": self.spec.job_id,
            "revision": self.revision,
            "schema_version": _SCHEMA_VERSION,
            "spec": self.spec.to_dict(),
            "status": self.status.value,
            "updated_at_ms": self.updated_at_ms,
        }

    def to_json(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, payload: object) -> RoutingJobRecord:
        if not isinstance(payload, dict) or set(payload) != {
            "attempt",
            "candidate_base_revision",
            "candidate_id",
            "cancel_reason",
            "created_at_ms",
            "diagnostic_code",
            "diagnostic_message",
            "job_id",
            "revision",
            "schema_version",
            "spec",
            "status",
            "updated_at_ms",
        }:
            raise ValueError("routing job record must be a closed object")
        if payload["schema_version"] != _SCHEMA_VERSION:
            raise ValueError("routing job record schema version is unsupported")
        spec = RoutingJobSpec.from_dict(payload["spec"])
        if payload["job_id"] != spec.job_id:
            raise ValueError("routing job record ID does not match its specification")
        try:
            status = RoutingJobStatus(payload["status"])
            code = (
                None
                if payload["diagnostic_code"] is None
                else RoutingJobFailureCode(payload["diagnostic_code"])
            )
        except (TypeError, ValueError) as error:
            raise ValueError("routing job status or diagnostic code is unsupported") from error
        return cls(
            spec=spec,
            status=status,
            revision=payload["revision"],
            attempt=payload["attempt"],
            created_at_ms=payload["created_at_ms"],
            updated_at_ms=payload["updated_at_ms"],
            candidate_id=payload["candidate_id"],
            candidate_base_revision=payload["candidate_base_revision"],
            diagnostic_code=code,
            diagnostic_message=payload["diagnostic_message"],
            cancel_reason=payload["cancel_reason"],
        )

    @classmethod
    def from_json(cls, payload: bytes) -> RoutingJobRecord:
        if not isinstance(payload, bytes) or len(payload) > 128_000:
            raise ValueError("routing job record JSON is oversized")
        try:
            decoded = json.loads(payload.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("routing job record JSON is malformed") from error
        return cls.from_dict(decoded)


def _store_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _store_time(value: int | None) -> int:
    resolved = _store_clock_ms() if value is None else value
    _integer("store timestamp", resolved)
    return resolved


class RoutingJobStore:
    """A small SQLite ledger for immutable routing-job records.

    The store owns lifecycle metadata only.  Its payload is the redacted JSON returned by
    :meth:`RoutingJobRecord.to_json`; neither board bytes nor candidate geometry can enter the
    database through this API.  Every mutation runs in a ``BEGIN IMMEDIATE`` transaction and
    updates by ``job_id`` *and* ``revision``.  A caller holding an old record therefore cannot
    overwrite a worker's newer transition.

    ``now_ms`` is optional on methods for normal use but accepted everywhere so tests and a
    future deterministic scheduler can drive expiry without wall-clock dependence.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_records: int = 256,
        ttl_ms: int = 86_400_000,
    ) -> None:
        if not isinstance(path, str | Path):
            raise ValueError("routing job store path is malformed")
        _integer("maximum stored records", max_records, minimum=1, maximum=_MAX_STORE_RECORDS)
        _integer("job record TTL", ttl_ms, minimum=1, maximum=_MAX_STORE_TTL_MS)
        self.max_records = max_records
        self.ttl_ms = ttl_ms
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS routing_jobs (
                job_id TEXT PRIMARY KEY NOT NULL,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                record_json BLOB NOT NULL
            ) WITHOUT ROWID
            """
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> RoutingJobStore:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _transaction(self) -> sqlite3.Cursor:
        self._connection.execute("BEGIN IMMEDIATE")
        return self._connection.cursor()

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.rollback()

    def _commit(self) -> None:
        if self._connection.in_transaction:
            self._connection.commit()

    def _purge_locked(self, now_ms: int) -> int:
        cursor = self._connection.execute(
            "DELETE FROM routing_jobs WHERE expires_at_ms <= ?", (now_ms,)
        )
        return cursor.rowcount

    def _row_locked(self, job_id: str) -> tuple[object, ...] | None:
        row = self._connection.execute(
            """
            SELECT job_id, status, revision, created_at_ms, updated_at_ms,
                   expires_at_ms, record_json
            FROM routing_jobs WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        return cast(tuple[object, ...] | None, row)

    @staticmethod
    def _record_from_row(row: tuple[object, ...]) -> RoutingJobRecord:
        if len(row) != 7:
            raise RoutingJobError("stored routing job row is malformed")
        job_id, status, revision, created_at_ms, updated_at_ms, expires_at_ms, payload = row
        if not isinstance(job_id, str) or not isinstance(status, str):
            raise RoutingJobError("stored routing job identity is malformed")
        if not isinstance(payload, bytes | str):
            raise RoutingJobError("stored routing job payload is malformed")
        _integer("stored job revision", revision)
        _integer("stored creation timestamp", created_at_ms)
        _integer("stored update timestamp", updated_at_ms)
        _integer("stored expiry timestamp", expires_at_ms)
        created_timestamp = cast(int, created_at_ms)
        expires_timestamp = cast(int, expires_at_ms)
        if expires_timestamp < created_timestamp:
            raise RoutingJobError("stored routing job expiry precedes creation")
        raw = payload if isinstance(payload, bytes) else payload.encode("utf-8", errors="strict")
        if len(raw) > _MAX_RECORD_BYTES:
            raise RoutingJobError("stored routing job payload is oversized")
        record = RoutingJobRecord.from_json(raw)
        if (
            record.spec.job_id != job_id
            or record.status.value != status
            or record.revision != revision
            or record.created_at_ms != created_at_ms
            or record.updated_at_ms != updated_at_ms
        ):
            raise RoutingJobError("stored routing job metadata is inconsistent")
        return record

    def _require_row_locked(self, job_id: str, now_ms: int) -> RoutingJobRecord:
        try:
            _digest("job ID", job_id)
        except ValueError:
            raise RoutingJobNotFoundError("routing job is unavailable") from None
        self._purge_locked(now_ms)
        row = self._row_locked(job_id)
        if row is None:
            raise RoutingJobNotFoundError("routing job is unavailable")
        return self._record_from_row(row)

    def _insert_locked(self, record: RoutingJobRecord, expires_at_ms: int) -> None:
        payload = record.to_json()
        if len(payload) > _MAX_RECORD_BYTES:
            raise RoutingJobError("routing job record is oversized")
        self._connection.execute(
            """
            INSERT INTO routing_jobs(
                job_id, status, revision, created_at_ms, updated_at_ms,
                expires_at_ms, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.spec.job_id,
                record.status.value,
                record.revision,
                record.created_at_ms,
                record.updated_at_ms,
                expires_at_ms,
                sqlite3.Binary(payload),
            ),
        )

    def create(self, spec: RoutingJobSpec, *, now_ms: int | None = None) -> RoutingJobRecord:
        if not isinstance(spec, RoutingJobSpec):
            raise ValueError("routing job specification is malformed")
        timestamp = _store_time(now_ms)
        expires_at_ms = timestamp + self.ttl_ms
        _integer("job expiry timestamp", expires_at_ms)
        with self._lock:
            cursor = self._transaction()
            try:
                self._purge_locked(timestamp)
                row = self._row_locked(spec.job_id)
                if row is not None:
                    existing = self._record_from_row(row)
                    if existing.spec != spec:
                        raise RoutingJobError("job ID collision with a different specification")
                    self._commit()
                    return existing
                count = cursor.execute("SELECT COUNT(*) FROM routing_jobs").fetchone()
                if count is None or not isinstance(count[0], int):
                    raise RoutingJobError("routing job store count is malformed")
                if count[0] >= self.max_records:
                    raise RoutingJobLimitError("routing job store capacity is exhausted")
                record = RoutingJobRecord.create(spec, now_ms=timestamp)
                self._insert_locked(record, expires_at_ms)
                self._commit()
                return record
            except BaseException:
                self._rollback()
                raise

    def get(self, job_id: str, *, now_ms: int | None = None) -> RoutingJobRecord:
        timestamp = _store_time(now_ms)
        with self._lock:
            self._transaction()
            try:
                record = self._require_row_locked(job_id, timestamp)
                self._commit()
                return record
            except BaseException:
                self._rollback()
                raise

    def purge(self, *, now_ms: int | None = None) -> int:
        timestamp = _store_time(now_ms)
        with self._lock:
            self._transaction()
            try:
                purged = self._purge_locked(timestamp)
                self._commit()
                return purged
            except BaseException:
                self._rollback()
                raise

    def _transition(
        self,
        job_id: str,
        *,
        expected_revision: int,
        now_ms: int | None,
        operation: Callable[[RoutingJobRecord], RoutingJobRecord],
    ) -> RoutingJobRecord:
        timestamp = _store_time(now_ms)
        with self._lock:
            cursor = self._transaction()
            try:
                current = self._require_row_locked(job_id, timestamp)
                if current.revision != expected_revision:
                    raise RoutingJobConflictError(
                        "job revision conflict: "
                        f"expected {expected_revision}, current {current.revision}"
                    )
                next_record = operation(current)
                payload = next_record.to_json()
                if len(payload) > _MAX_RECORD_BYTES:
                    raise RoutingJobError("routing job record is oversized")
                updated = cursor.execute(
                    """
                    UPDATE routing_jobs
                    SET status = ?, revision = ?, updated_at_ms = ?, record_json = ?
                    WHERE job_id = ? AND revision = ?
                    """,
                    (
                        next_record.status.value,
                        next_record.revision,
                        next_record.updated_at_ms,
                        sqlite3.Binary(payload),
                        next_record.spec.job_id,
                        expected_revision,
                    ),
                )
                if updated.rowcount != 1:
                    raise RoutingJobConflictError("routing job changed during transition")
                self._commit()
                return next_record
            except BaseException:
                self._rollback()
                raise

    def start(
        self,
        job_id: str,
        *,
        expected_revision: int,
        now_ms: int | None = None,
    ) -> RoutingJobRecord:
        return self._transition(
            job_id,
            expected_revision=expected_revision,
            now_ms=now_ms,
            operation=lambda record: record.start(
                expected_revision=expected_revision,
                now_ms=_store_time(now_ms),
            ),
        )

    def request_cancel(
        self,
        job_id: str,
        *,
        expected_revision: int,
        reason: str = "caller_requested",
        now_ms: int | None = None,
    ) -> RoutingJobRecord:
        return self._transition(
            job_id,
            expected_revision=expected_revision,
            now_ms=now_ms,
            operation=lambda record: record.request_cancel(
                expected_revision=expected_revision,
                now_ms=_store_time(now_ms),
                reason=reason,
            ),
        )

    def acknowledge_cancel(
        self,
        job_id: str,
        *,
        expected_revision: int,
        now_ms: int | None = None,
    ) -> RoutingJobRecord:
        return self._transition(
            job_id,
            expected_revision=expected_revision,
            now_ms=now_ms,
            operation=lambda record: record.acknowledge_cancel(
                expected_revision=expected_revision,
                now_ms=_store_time(now_ms),
            ),
        )

    def complete(
        self,
        job_id: str,
        candidate: Candidate,
        *,
        expected_revision: int,
        now_ms: int | None = None,
    ) -> RoutingJobRecord:
        return self._transition(
            job_id,
            expected_revision=expected_revision,
            now_ms=now_ms,
            operation=lambda record: record.complete(
                candidate,
                expected_revision=expected_revision,
                now_ms=_store_time(now_ms),
            ),
        )

    def fail(
        self,
        job_id: str,
        code: RoutingJobFailureCode,
        message: str,
        *,
        expected_revision: int,
        now_ms: int | None = None,
    ) -> RoutingJobRecord:
        return self._transition(
            job_id,
            expected_revision=expected_revision,
            now_ms=now_ms,
            operation=lambda record: record.fail(
                code,
                message,
                expected_revision=expected_revision,
                now_ms=_store_time(now_ms),
            ),
        )


__all__ = [
    "Candidate",
    "RoutingJobConflictError",
    "RoutingJobError",
    "RoutingJobFailureCode",
    "RoutingJobKind",
    "RoutingJobLimitError",
    "RoutingJobLimits",
    "RoutingJobNotFoundError",
    "RoutingJobRecord",
    "RoutingJobSpec",
    "RoutingJobStateError",
    "RoutingJobStatus",
    "RoutingJobStore",
]
