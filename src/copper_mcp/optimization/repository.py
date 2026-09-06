"""Bounded SQLite persistence for redacted optimization job metadata.

Requests, board paths, geometry, raw backend output, and capabilities are deliberately absent.
The caller retains the immutable request and supplies it again for every lifecycle transition.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, cast

from pydantic import ValidationError

from copper_mcp.optimization import lifecycle
from copper_mcp.optimization.contracts import OptimizationError, OptimizationRequest
from copper_mcp.optimization.lifecycle import (
    TERMINAL,
    ApprovalConsumer,
    FailureCode,
    OptimizationJobRecord,
    advance_job,
    approve_job,
    create_job,
    fail_job,
)
from copper_mcp.optimization.package import OptimizationPackage

_MAX_SAFE_INT: Final = (1 << 53) - 1
_MAX_RECORDS: Final = 4_096
_MAX_TTL_MS: Final = 7 * 86_400_000
_MAX_LEASE_MS: Final = 86_400_000
_MAX_RECORD_BYTES: Final = 128_000
_MAX_PACKAGE_BYTES: Final = 512_000
_MAX_PAYLOAD_BYTES: Final = 1_048_576
_DIGEST_RE: Final = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _integer(
    name: str,
    value: object,
    *,
    minimum: int = 0,
    maximum: int = _MAX_SAFE_INT,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported integer range")
    return value


def _clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )


class OptimizationRepositoryError(OptimizationError):
    """Base class for fixed, non-echoing durable repository refusals."""


class OptimizationJobUnavailableError(OptimizationRepositoryError):
    """Unknown, expired, malformed, and wrong-owner lookups share one result."""


class OptimizationJobConflictError(OptimizationRepositoryError):
    """A revision, state, or fencing precondition changed before a CAS."""


class OptimizationJobLimitError(OptimizationRepositoryError):
    """A configured retention bound would be exceeded."""


class OptimizationJobLeaseError(OptimizationRepositoryError):
    """A lease is absent, expired, fenced out, or otherwise unusable."""


class OptimizationJobAlreadyClaimedError(OptimizationJobLeaseError):
    """A non-expired lease already owns the job."""


@dataclass(frozen=True, slots=True)
class OptimizationJobLease:
    """Opaque process-safe fencing coordinates; no board or request data is present."""

    job_id: str
    owner_binding: str
    fence: int
    revision: int
    claimed_at_ms: int
    expires_at_ms: int
    lease_ms: int

    def __post_init__(self) -> None:
        try:
            _integer("lease fence", self.fence, minimum=1)
            _integer("lease revision", self.revision, minimum=1)
            _integer("lease claim timestamp", self.claimed_at_ms)
            _integer("lease expiry timestamp", self.expires_at_ms)
            _integer("lease duration", self.lease_ms, minimum=1, maximum=_MAX_LEASE_MS)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("optimization lease is malformed") from error
        if (
            not isinstance(self.job_id, str)
            or not isinstance(self.owner_binding, str)
            or len(self.job_id) != 71
            or len(self.owner_binding) != 71
            or not self.job_id.startswith("sha256:")
            or not self.owner_binding.startswith("sha256:")
            or self.expires_at_ms <= self.claimed_at_ms
        ):
            raise ValueError("optimization lease is malformed")

    def with_revision(self, revision: int) -> OptimizationJobLease:
        return replace(self, revision=revision)


@dataclass(frozen=True, slots=True)
class _StoredJob:
    record: OptimizationJobRecord
    created_at_ms: int
    updated_at_ms: int
    expires_at_ms: int
    lease_fence: int
    lease_expires_at_ms: int | None


class OptimizationJobRepository:
    """Persist typed lifecycle/package metadata with owner checks and fenced CAS writes."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_records: int = 256,
        ttl_ms: int = 86_400_000,
        max_record_bytes: int = _MAX_RECORD_BYTES,
        max_package_bytes: int = _MAX_PACKAGE_BYTES,
        clock: Callable[[], int] | None = None,
        monotonic_clock: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(path, str | Path):
            raise ValueError("optimization repository path is malformed")
        _integer("maximum stored records", max_records, minimum=1, maximum=_MAX_RECORDS)
        _integer("optimization record TTL", ttl_ms, minimum=1, maximum=_MAX_TTL_MS)
        _integer("maximum record bytes", max_record_bytes, minimum=1, maximum=_MAX_PAYLOAD_BYTES)
        _integer("maximum package bytes", max_package_bytes, minimum=1, maximum=_MAX_PAYLOAD_BYTES)
        self.max_records = max_records
        self.path = Path(path).resolve() if str(path) != ":memory:" else None
        self.ttl_ms = ttl_ms
        self.max_record_bytes = max_record_bytes
        self.max_package_bytes = max_package_bytes
        self._clock = clock or _clock_ms
        self._monotonic_clock = monotonic_clock or _monotonic_ms
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS optimization_jobs (
                job_id TEXT PRIMARY KEY NOT NULL,
                owner_binding TEXT NOT NULL,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                lease_fence INTEGER NOT NULL DEFAULT 0,
                lease_expires_at_ms INTEGER,
                record_json BLOB NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS optimization_packages (
                job_id TEXT PRIMARY KEY NOT NULL
                    REFERENCES optimization_jobs(job_id) ON DELETE CASCADE,
                owner_binding TEXT NOT NULL,
                package_digest TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                package_json BLOB NOT NULL
            ) WITHOUT ROWID;
            """
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> OptimizationJobRepository:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def now_ms(self) -> int:
        return _integer("repository timestamp", self._clock())

    def monotonic_ms(self) -> int:
        value = self._monotonic_clock()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise OptimizationRepositoryError("optimization clock is unavailable")
        return value

    def _time(self, now_ms: int | None) -> int:
        return self.now_ms() if now_ms is None else _integer("repository timestamp", now_ms)

    def _transaction(self) -> sqlite3.Cursor:
        self._connection.execute("BEGIN IMMEDIATE")
        return self._connection.cursor()

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.rollback()

    def _commit(self) -> None:
        if self._connection.in_transaction:
            self._connection.commit()

    def _record_bytes(self, record: OptimizationJobRecord) -> bytes:
        validated = OptimizationJobRecord.model_validate(record)
        payload = _canonical_bytes(validated.model_dump(mode="json"))
        if len(payload) > self.max_record_bytes:
            raise OptimizationJobLimitError("optimization record exceeds its byte limit")
        return payload

    def _package_bytes(self, package: OptimizationPackage) -> bytes:
        validated = OptimizationPackage.model_validate(package)
        payload = _canonical_bytes(validated.model_dump(mode="json"))
        if len(payload) > self.max_package_bytes:
            raise OptimizationJobLimitError("optimization package exceeds its byte limit")
        return payload

    def _row_locked(self, job_id: str) -> tuple[object, ...] | None:
        return cast(
            tuple[object, ...] | None,
            self._connection.execute(
                """
                SELECT owner_binding, status, revision, created_at_ms, updated_at_ms,
                       expires_at_ms, lease_fence, lease_expires_at_ms, record_json
                FROM optimization_jobs WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone(),
        )

    def _decode_row(self, row: tuple[object, ...]) -> _StoredJob:
        if len(row) != 9:
            raise OptimizationRepositoryError("stored optimization job is malformed")
        owner, status, revision, created, updated, expires, fence, lease_expires, payload = row
        if not isinstance(owner, str) or not isinstance(status, str):
            raise OptimizationRepositoryError("stored optimization job is malformed")
        try:
            revision_i = _integer("stored revision", revision)
            created_i = _integer("stored creation timestamp", created)
            updated_i = _integer("stored update timestamp", updated)
            expires_i = _integer("stored expiry timestamp", expires)
            fence_i = _integer("stored lease fence", fence)
            lease_i = (
                None
                if lease_expires is None
                else _integer("stored lease expiry timestamp", lease_expires)
            )
        except ValueError as error:
            raise OptimizationRepositoryError("stored optimization job is malformed") from error
        if not isinstance(payload, bytes | str):
            raise OptimizationRepositoryError("stored optimization job is malformed")
        raw = payload if isinstance(payload, bytes) else payload.encode("utf-8", errors="strict")
        if len(raw) > self.max_record_bytes:
            raise OptimizationRepositoryError("stored optimization job is oversized")
        try:
            record = OptimizationJobRecord.model_validate_json(raw)
        except (ValidationError, ValueError) as error:
            raise OptimizationRepositoryError("stored optimization job is malformed") from error
        if (
            record.owner_binding != owner
            or record.status != status
            or record.revision != revision_i
            or updated_i < created_i
            or expires_i < created_i
        ):
            raise OptimizationRepositoryError("stored optimization job is inconsistent")
        return _StoredJob(record, created_i, updated_i, expires_i, fence_i, lease_i)

    def _require_locked(self, job_id: str, owner_binding: str) -> _StoredJob:
        row = self._row_locked(job_id)
        if row is None:
            raise OptimizationJobUnavailableError("optimization job is unavailable")
        stored = self._decode_row(row)
        if stored.record.owner_binding != owner_binding:
            raise OptimizationJobUnavailableError("optimization job is unavailable")
        return stored

    def _recover_expired_leases_locked(self, now_ms: int) -> int:
        rows = self._connection.execute(
            """
            SELECT job_id, owner_binding, status, revision, created_at_ms, updated_at_ms,
                   expires_at_ms, lease_fence, lease_expires_at_ms, record_json
            FROM optimization_jobs
            WHERE lease_expires_at_ms IS NOT NULL AND lease_expires_at_ms <= ?
            """,
            (now_ms,),
        ).fetchall()
        recovered = 0
        for row in rows:
            job_id = row[0]
            if not isinstance(job_id, str):
                raise OptimizationRepositoryError("stored optimization job is malformed")
            stored = self._decode_row(cast(tuple[object, ...], row[1:]))
            if stored.record.status in TERMINAL:
                self._connection.execute(
                    """
                    UPDATE optimization_jobs
                    SET lease_expires_at_ms = NULL
                    WHERE job_id = ? AND revision = ? AND lease_fence = ?
                    """,
                    (job_id, stored.record.revision, stored.lease_fence),
                )
                continue
            replacement = lifecycle._failed(stored.record, "interrupted")
            payload = self._record_bytes(replacement)
            expiry = now_ms + self.ttl_ms
            _integer("optimization expiry timestamp", expiry)
            changed = self._connection.execute(
                """
                UPDATE optimization_jobs
                SET status = ?, revision = ?, updated_at_ms = ?, expires_at_ms = ?,
                    lease_expires_at_ms = NULL, record_json = ?
                WHERE job_id = ? AND revision = ? AND lease_fence = ?
                      AND lease_expires_at_ms <= ?
                """,
                (
                    replacement.status,
                    replacement.revision,
                    now_ms,
                    expiry,
                    sqlite3.Binary(payload),
                    job_id,
                    stored.record.revision,
                    stored.lease_fence,
                    now_ms,
                ),
            )
            if changed.rowcount == 1:
                self._connection.execute(
                    "DELETE FROM optimization_packages WHERE job_id = ?", (job_id,)
                )
                recovered += 1
        return recovered

    def _maintenance_locked(self, now_ms: int) -> tuple[int, int]:
        recovered = self._recover_expired_leases_locked(now_ms)
        purged = self._connection.execute(
            """
            DELETE FROM optimization_jobs
            WHERE expires_at_ms <= ?
              AND (lease_expires_at_ms IS NULL OR lease_expires_at_ms <= ?)
            """,
            (now_ms, now_ms),
        ).rowcount
        return recovered, purged

    def _transaction_after_maintenance(self, now_ms: int) -> sqlite3.Cursor:
        """Commit expiry/recovery before an unrelated requested operation may fail."""

        self._transaction()
        try:
            self._maintenance_locked(now_ms)
            self._commit()
        except BaseException:
            self._rollback()
            raise
        return self._transaction()

    def create(
        self,
        request: OptimizationRequest,
        owner_binding: str,
        *,
        now_ms: int | None = None,
    ) -> OptimizationJobRecord:
        request = OptimizationRequest.model_validate(request)
        record = create_job(request, owner_binding=owner_binding)
        timestamp = self._time(now_ms)
        expiry = timestamp + self.ttl_ms
        _integer("optimization expiry timestamp", expiry)
        payload = self._record_bytes(record)
        with self._lock:
            cursor = self._transaction_after_maintenance(timestamp)
            try:
                row = self._row_locked(record.job_id)
                if row is not None:
                    existing = self._decode_row(row).record
                    if (
                        existing.schema_version != record.schema_version
                        or existing.job_id != record.job_id
                        or existing.owner_binding != record.owner_binding
                        or existing.request_digest != record.request_digest
                        or existing.limits_digest != record.limits_digest
                        or existing.board_revision != record.board_revision
                        or existing.snapshot_digest != record.snapshot_digest
                    ):
                        raise OptimizationJobConflictError("optimization job identity collided")
                    self._commit()
                    return existing
                count = cursor.execute("SELECT COUNT(*) FROM optimization_jobs").fetchone()
                if count is None or not isinstance(count[0], int):
                    raise OptimizationRepositoryError("optimization repository count is malformed")
                if count[0] >= self.max_records:
                    raise OptimizationJobLimitError("optimization repository capacity is exhausted")
                cursor.execute(
                    """
                    INSERT INTO optimization_jobs(
                        job_id, owner_binding, status, revision, created_at_ms, updated_at_ms,
                        expires_at_ms, lease_fence, lease_expires_at_ms, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)
                    """,
                    (
                        record.job_id,
                        record.owner_binding,
                        record.status,
                        record.revision,
                        timestamp,
                        timestamp,
                        expiry,
                        sqlite3.Binary(payload),
                    ),
                )
                self._commit()
                return record
            except BaseException:
                self._rollback()
                raise

    def get(
        self,
        job_id: str,
        owner_binding: str,
        *,
        now_ms: int | None = None,
    ) -> OptimizationJobRecord:
        timestamp = self._time(now_ms)
        with self._lock:
            self._transaction_after_maintenance(timestamp)
            try:
                record = self._require_locked(job_id, owner_binding).record
                self._commit()
                return record
            except OptimizationJobUnavailableError:
                self._commit()
                raise
            except BaseException:
                self._rollback()
                raise

    def count(self, *, now_ms: int | None = None) -> int:
        timestamp = self._time(now_ms)
        with self._lock:
            self._transaction_after_maintenance(timestamp)
            try:
                row = self._connection.execute("SELECT COUNT(*) FROM optimization_jobs").fetchone()
                if row is None or not isinstance(row[0], int):
                    raise OptimizationRepositoryError("optimization repository count is malformed")
                self._commit()
                return row[0]
            except BaseException:
                self._rollback()
                raise

    def purge(self, *, now_ms: int | None = None) -> int:
        timestamp = self._time(now_ms)
        with self._lock:
            self._transaction()
            try:
                _recovered, purged = self._maintenance_locked(timestamp)
                self._commit()
                return purged
            except BaseException:
                self._rollback()
                raise

    def claim(
        self,
        job_id: str,
        request: OptimizationRequest,
        owner_binding: str,
        *,
        lease_ms: int = 30_000,
        now_ms: int | None = None,
    ) -> OptimizationJobLease:
        request = OptimizationRequest.model_validate(request)
        requested_lease = _integer(
            "optimization lease duration", lease_ms, minimum=1, maximum=_MAX_LEASE_MS
        )
        duration = min(requested_lease, request.limits.max_runtime_ms)
        timestamp = self._time(now_ms)
        lease_expiry = timestamp + duration
        expiry = timestamp + self.ttl_ms
        _integer("optimization lease expiry timestamp", lease_expiry)
        _integer("optimization expiry timestamp", expiry)
        with self._lock:
            self._transaction_after_maintenance(timestamp)
            try:
                stored = self._require_locked(job_id, owner_binding)
                if (
                    stored.lease_expires_at_ms is not None
                    and stored.lease_expires_at_ms > timestamp
                ):
                    raise OptimizationJobAlreadyClaimedError("optimization job is already claimed")
                if stored.record.status != "queued":
                    raise OptimizationJobConflictError("optimization job cannot be claimed")
                replacement = advance_job(
                    stored.record,
                    request,
                    expected_revision=stored.record.revision,
                    owner_binding=owner_binding,
                    next_status="inspecting",
                    observed_board_revision=request.board_revision,
                    observed_snapshot_digest=request.snapshot_digest,
                )
                fence = stored.lease_fence + 1
                _integer("optimization lease fence", fence, minimum=1)
                changed = self._connection.execute(
                    """
                    UPDATE optimization_jobs
                    SET status = ?, revision = ?, updated_at_ms = ?, expires_at_ms = ?,
                        lease_fence = ?, lease_expires_at_ms = ?, record_json = ?
                    WHERE job_id = ? AND revision = ? AND lease_fence = ?
                          AND lease_expires_at_ms IS NULL
                    """,
                    (
                        replacement.status,
                        replacement.revision,
                        timestamp,
                        expiry,
                        fence,
                        lease_expiry,
                        sqlite3.Binary(self._record_bytes(replacement)),
                        job_id,
                        stored.record.revision,
                        stored.lease_fence,
                    ),
                )
                if changed.rowcount != 1:
                    raise OptimizationJobConflictError("optimization claim lost its CAS race")
                self._commit()
                return OptimizationJobLease(
                    job_id,
                    owner_binding,
                    fence,
                    replacement.revision,
                    timestamp,
                    lease_expiry,
                    duration,
                )
            except BaseException:
                self._rollback()
                raise

    def _assert_lease_locked(self, lease: OptimizationJobLease, now_ms: int) -> _StoredJob:
        stored = self._require_locked(lease.job_id, lease.owner_binding)
        if (
            stored.lease_fence != lease.fence
            or stored.record.revision != lease.revision
            or stored.lease_expires_at_ms is None
            or stored.lease_expires_at_ms <= now_ms
        ):
            raise OptimizationJobLeaseError("optimization lease is unavailable")
        return stored

    def lease_record(
        self, lease: OptimizationJobLease, *, now_ms: int | None = None
    ) -> OptimizationJobRecord:
        timestamp = self._time(now_ms)
        with self._lock:
            self._transaction_after_maintenance(timestamp)
            try:
                record = self._assert_lease_locked(lease, timestamp).record
                self._commit()
                return record
            except BaseException:
                self._rollback()
                raise

    def heartbeat(
        self, lease: OptimizationJobLease, *, now_ms: int | None = None
    ) -> OptimizationJobLease:
        timestamp = self._time(now_ms)
        lease_expiry = timestamp + lease.lease_ms
        expiry = timestamp + self.ttl_ms
        _integer("optimization lease expiry timestamp", lease_expiry)
        _integer("optimization expiry timestamp", expiry)
        with self._lock:
            self._transaction_after_maintenance(timestamp)
            try:
                stored = self._assert_lease_locked(lease, timestamp)
                changed = self._connection.execute(
                    """
                    UPDATE optimization_jobs
                    SET updated_at_ms = ?, expires_at_ms = ?, lease_expires_at_ms = ?
                    WHERE job_id = ? AND revision = ? AND lease_fence = ?
                          AND lease_expires_at_ms > ?
                    """,
                    (
                        timestamp,
                        expiry,
                        lease_expiry,
                        lease.job_id,
                        stored.record.revision,
                        lease.fence,
                        timestamp,
                    ),
                )
                if changed.rowcount != 1:
                    raise OptimizationJobLeaseError("optimization lease is unavailable")
                self._commit()
                return replace(lease, expires_at_ms=lease_expiry)
            except BaseException:
                self._rollback()
                raise

    def compare_and_swap(
        self,
        current: OptimizationJobRecord,
        replacement: OptimizationJobRecord,
        *,
        owner_binding: str,
        lease: OptimizationJobLease | None = None,
        package: OptimizationPackage | None = None,
        now_ms: int | None = None,
    ) -> OptimizationJobRecord:
        current = OptimizationJobRecord.model_validate(current)
        replacement = OptimizationJobRecord.model_validate(replacement)
        if (
            current.job_id != replacement.job_id
            or current.owner_binding != owner_binding
            or replacement.owner_binding != owner_binding
            or replacement.revision != current.revision + 1
        ):
            raise OptimizationJobConflictError("optimization transition is conflicted")
        payload = self._record_bytes(replacement)
        package_payload = None if package is None else self._package_bytes(package)
        if package is not None and replacement.package_digest != package.digest:
            raise OptimizationJobConflictError("optimization package is conflicted")
        if replacement.status == "awaiting_approval" and package is None:
            raise OptimizationJobConflictError("optimization package is required")
        if replacement.status == "approved":
            raise OptimizationJobConflictError("optimization approval requires consent")
        if replacement.status == "completed" and (
            current.status != "approved"
            or replacement.package_digest != current.package_digest
            or replacement.judge_digest != current.judge_digest
            or replacement.candidate_ids != current.candidate_ids
            or replacement.backend_versions != current.backend_versions
            or replacement.metrics != current.metrics
            or replacement.approval_receipt_digest != current.approval_receipt_digest
        ):
            raise OptimizationJobConflictError("optimization completion is conflicted")
        timestamp = self._time(now_ms)
        expiry = timestamp + self.ttl_ms
        _integer("optimization expiry timestamp", expiry)
        with self._lock:
            self._transaction_after_maintenance(timestamp)
            try:
                stored = self._require_locked(current.job_id, owner_binding)
                if stored.record != current:
                    raise OptimizationJobConflictError("optimization revision is stale")
                if lease is not None:
                    self._assert_lease_locked(lease, timestamp)
                elif stored.lease_expires_at_ms is not None:
                    raise OptimizationJobConflictError("optimization job has an active lease")
                clear_lease = replacement.status in TERMINAL or replacement.status in {
                    "awaiting_approval",
                    "approved",
                    "completed",
                }
                parameters: list[object] = [
                    replacement.status,
                    replacement.revision,
                    timestamp,
                    expiry,
                    None if clear_lease else stored.lease_expires_at_ms,
                    sqlite3.Binary(payload),
                    current.job_id,
                    current.revision,
                ]
                if lease is not None:
                    parameters.extend((lease.fence, timestamp))
                    changed = self._connection.execute(
                        """
                        UPDATE optimization_jobs
                        SET status = ?, revision = ?, updated_at_ms = ?, expires_at_ms = ?,
                            lease_expires_at_ms = ?, record_json = ?
                        WHERE job_id = ? AND revision = ?
                          AND lease_fence = ? AND lease_expires_at_ms > ?
                        """,
                        tuple(parameters),
                    )
                else:
                    changed = self._connection.execute(
                        """
                        UPDATE optimization_jobs
                        SET status = ?, revision = ?, updated_at_ms = ?, expires_at_ms = ?,
                            lease_expires_at_ms = ?, record_json = ?
                        WHERE job_id = ? AND revision = ?
                        """,
                        tuple(parameters),
                    )
                if changed.rowcount != 1:
                    raise OptimizationJobConflictError("optimization CAS lost its race")
                if replacement.status in TERMINAL and replacement.status != "completed":
                    self._connection.execute(
                        "DELETE FROM optimization_packages WHERE job_id = ?", (current.job_id,)
                    )
                elif package is not None and package_payload is not None:
                    existing = self._connection.execute(
                        """
                        SELECT package_digest, package_json
                        FROM optimization_packages WHERE job_id = ?
                        """,
                        (current.job_id,),
                    ).fetchone()
                    if existing is not None and existing != (package.digest, package_payload):
                        raise OptimizationJobConflictError("optimization package is immutable")
                    self._connection.execute(
                        """
                        INSERT INTO optimization_packages(
                            job_id, owner_binding, package_digest, created_at_ms, package_json
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(job_id) DO NOTHING
                        """,
                        (
                            current.job_id,
                            owner_binding,
                            package.digest,
                            timestamp,
                            sqlite3.Binary(package_payload),
                        ),
                    )
                self._commit()
                return replacement
            except BaseException:
                self._rollback()
                raise

    cas = compare_and_swap

    def cancel(
        self,
        job_id: str,
        request: OptimizationRequest,
        owner_binding: str,
        *,
        expected_revision: int,
        now_ms: int | None = None,
        failure_code: FailureCode = "cancelled",
    ) -> OptimizationJobRecord:
        if failure_code not in {"cancelled", "budget_exhausted", "interrupted"}:
            raise OptimizationError("optimization stop cause is invalid")
        request = OptimizationRequest.model_validate(request)
        _integer("expected optimization revision", expected_revision)
        timestamp = self._time(now_ms)
        with self._lock:
            self._transaction_after_maintenance(timestamp)
            try:
                stored = self._require_locked(job_id, owner_binding)
                if stored.record.revision != expected_revision:
                    raise OptimizationJobConflictError("optimization revision is stale")
                replacement = fail_job(
                    stored.record,
                    request,
                    expected_revision=expected_revision,
                    owner_binding=owner_binding,
                    code=failure_code,
                )
                changed = self._connection.execute(
                    """
                    UPDATE optimization_jobs
                    SET status = ?, revision = ?, updated_at_ms = ?, expires_at_ms = ?,
                        lease_expires_at_ms = NULL, record_json = ?
                    WHERE job_id = ? AND revision = ?
                    """,
                    (
                        replacement.status,
                        replacement.revision,
                        timestamp,
                        timestamp + self.ttl_ms,
                        sqlite3.Binary(self._record_bytes(replacement)),
                        job_id,
                        expected_revision,
                    ),
                )
                if changed.rowcount != 1:
                    raise OptimizationJobConflictError("optimization cancel lost its CAS race")
                self._connection.execute(
                    "DELETE FROM optimization_packages WHERE job_id = ?", (job_id,)
                )
                self._commit()
                return replacement
            except BaseException:
                self._rollback()
                raise

    def recover_interrupted(
        self,
        job_id: str,
        owner_binding: str,
        *,
        expected_revision: int | None = None,
        now_ms: int | None = None,
    ) -> OptimizationJobRecord:
        """Close a non-terminal job when its caller-owned private inputs no longer exist."""

        if expected_revision is not None:
            _integer("expected optimization revision", expected_revision)
        timestamp = self._time(now_ms)
        with self._lock:
            self._transaction_after_maintenance(timestamp)
            try:
                stored = self._require_locked(job_id, owner_binding)
                if stored.record.status in TERMINAL:
                    self._commit()
                    return stored.record
                if expected_revision is not None and stored.record.revision != expected_revision:
                    raise OptimizationJobConflictError("optimization revision is stale")
                if (
                    stored.lease_expires_at_ms is not None
                    and stored.lease_expires_at_ms > timestamp
                ):
                    raise OptimizationJobLeaseError("optimization lease has not expired")
                replacement = lifecycle._failed(stored.record, "interrupted")
                changed = self._connection.execute(
                    """
                    UPDATE optimization_jobs
                    SET status = ?, revision = ?, updated_at_ms = ?, expires_at_ms = ?,
                        lease_expires_at_ms = NULL, record_json = ?
                    WHERE job_id = ? AND revision = ?
                    """,
                    (
                        replacement.status,
                        replacement.revision,
                        timestamp,
                        timestamp + self.ttl_ms,
                        sqlite3.Binary(self._record_bytes(replacement)),
                        job_id,
                        stored.record.revision,
                    ),
                )
                if changed.rowcount != 1:
                    raise OptimizationJobConflictError("optimization recovery lost its CAS race")
                self._connection.execute(
                    "DELETE FROM optimization_packages WHERE job_id = ?", (job_id,)
                )
                self._commit()
                return replacement
            except BaseException:
                self._rollback()
                raise

    def get_package(
        self,
        job_id: str,
        owner_binding: str,
        *,
        now_ms: int | None = None,
    ) -> OptimizationPackage:
        timestamp = self._time(now_ms)
        with self._lock:
            self._transaction_after_maintenance(timestamp)
            try:
                stored = self._require_locked(job_id, owner_binding)
                row = self._connection.execute(
                    """
                    SELECT owner_binding, package_digest, package_json
                    FROM optimization_packages WHERE job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
                if row is None or row[0] != owner_binding or stored.record.package_digest != row[1]:
                    raise OptimizationJobUnavailableError("optimization package is unavailable")
                payload = row[2]
                if not isinstance(payload, bytes | str):
                    raise OptimizationRepositoryError("stored optimization package is malformed")
                raw = (
                    payload
                    if isinstance(payload, bytes)
                    else payload.encode("utf-8", errors="strict")
                )
                if len(raw) > self.max_package_bytes:
                    raise OptimizationRepositoryError("stored optimization package is oversized")
                try:
                    package = OptimizationPackage.model_validate_json(raw)
                except (ValidationError, ValueError) as error:
                    raise OptimizationRepositoryError(
                        "stored optimization package is malformed"
                    ) from error
                if package.digest != row[1]:
                    raise OptimizationRepositoryError("stored optimization package is inconsistent")
                self._commit()
                return package
            except OptimizationJobUnavailableError:
                self._commit()
                raise
            except BaseException:
                self._rollback()
                raise

    def approve(
        self,
        job_id: str,
        request: OptimizationRequest,
        owner_binding: str,
        *,
        expected_revision: int,
        package: OptimizationPackage,
        capability: str,
        authority: ApprovalConsumer,
        observed_board_revision: str,
        observed_snapshot_digest: str,
        now_ms: int | None = None,
        complete: bool = False,
    ) -> OptimizationJobRecord:
        """Consume exact human consent and CAS-persist approval under one write lock."""

        if type(complete) is not bool:
            raise OptimizationError("optimization completion mode is invalid")
        request = OptimizationRequest.model_validate(request)
        package = OptimizationPackage.model_validate(package)
        _integer("expected optimization revision", expected_revision)
        if (
            type(observed_board_revision) is not str
            or type(observed_snapshot_digest) is not str
            or _DIGEST_RE.fullmatch(observed_board_revision) is None
            or _DIGEST_RE.fullmatch(observed_snapshot_digest) is None
        ):
            raise OptimizationError("optimization observations are malformed")
        timestamp = self._time(now_ms)
        expiry = timestamp + self.ttl_ms
        _integer("optimization expiry timestamp", expiry)
        with self._lock:
            self._transaction_after_maintenance(timestamp)
            try:
                stored = self._require_locked(job_id, owner_binding)
                if stored.record.revision != expected_revision:
                    raise OptimizationJobConflictError("optimization revision is stale")
                if stored.lease_expires_at_ms is not None:
                    raise OptimizationJobConflictError("optimization job has an active lease")
                if (
                    observed_board_revision != stored.record.board_revision
                    or observed_snapshot_digest != stored.record.snapshot_digest
                ):
                    raise OptimizationError("optimization observations are stale")
                row = self._connection.execute(
                    """
                    SELECT owner_binding, package_digest, package_json
                    FROM optimization_packages WHERE job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
                if (
                    row is None
                    or row[0] != owner_binding
                    or row[1] != package.digest
                    or stored.record.package_digest != package.digest
                ):
                    raise OptimizationJobUnavailableError("optimization package is unavailable")
                persisted = row[2]
                if not isinstance(persisted, bytes | str):
                    raise OptimizationRepositoryError("stored optimization package is malformed")
                persisted_bytes = (
                    persisted
                    if isinstance(persisted, bytes)
                    else persisted.encode("utf-8", errors="strict")
                )
                if persisted_bytes != self._package_bytes(package):
                    raise OptimizationRepositoryError("stored optimization package is inconsistent")
                replacement = approve_job(
                    stored.record,
                    request,
                    expected_revision=expected_revision,
                    owner_binding=owner_binding,
                    package=package,
                    capability=capability,
                    authority=authority,
                    observed_board_revision=observed_board_revision,
                    observed_snapshot_digest=observed_snapshot_digest,
                )
                if complete and replacement.status == "approved":
                    replacement = advance_job(
                        replacement,
                        request,
                        expected_revision=replacement.revision,
                        owner_binding=owner_binding,
                        next_status="completed",
                        package=package,
                        observed_board_revision=request.board_revision,
                        observed_snapshot_digest=request.snapshot_digest,
                    )
                changed = self._connection.execute(
                    """
                    UPDATE optimization_jobs
                    SET status = ?, revision = ?, updated_at_ms = ?, expires_at_ms = ?,
                        record_json = ?
                    WHERE job_id = ? AND revision = ? AND lease_expires_at_ms IS NULL
                    """,
                    (
                        replacement.status,
                        replacement.revision,
                        timestamp,
                        expiry,
                        sqlite3.Binary(self._record_bytes(replacement)),
                        job_id,
                        expected_revision,
                    ),
                )
                if changed.rowcount != 1:
                    # The capability has already been consumed.  Failing closed is deliberate:
                    # retry requires a fresh human confirmation for the still-current revision.
                    raise OptimizationJobConflictError("optimization approval lost its CAS race")
                if replacement.status in TERMINAL and replacement.status != "completed":
                    self._connection.execute(
                        "DELETE FROM optimization_packages WHERE job_id = ?", (job_id,)
                    )
                self._commit()
                return replacement
            except BaseException:
                self._rollback()
                raise


__all__ = [
    "OptimizationJobAlreadyClaimedError",
    "OptimizationJobConflictError",
    "OptimizationJobLease",
    "OptimizationJobLeaseError",
    "OptimizationJobLimitError",
    "OptimizationJobRepository",
    "OptimizationJobUnavailableError",
    "OptimizationRepositoryError",
]
