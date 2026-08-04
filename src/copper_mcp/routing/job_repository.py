"""Durable request, result, and candidate-export repository for routing jobs.

``RoutingJobStore`` deliberately persists only lifecycle metadata.  This module adds the
smallest adjacent persistence surface needed to execute a queued file-backed route after a
process restart:

* a validated, normalized request envelope bound to a caller-context digest;
* immutable candidate geometry export, retained separately from the redacted job record; and
* a composition layer that verifies candidate identity before publishing both the geometry and
  the redacted candidate manifest.

The repository never accepts board bytes, raw KiCad net names, prompts, credentials, DRC
findings, or apply authority.  Candidate geometry is an explicitly authorized export and is
bounded by size, count, and TTL.  The deterministic ``RoutingJobSpec.job_id`` remains an
idempotency key, never a bearer handle.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, cast

from copper_mcp.routing.astar import canonical_candidate_bytes, verify_candidate_id
from copper_mcp.routing.candidate_store import (
    CandidateManifest,
    CandidateManifestKind,
    CandidateManifestStore,
)
from copper_mcp.routing.contracts import RouteCandidate
from copper_mcp.routing.job_worker import RoutingJobExecutor, RoutingJobWorker, WorkerLimits
from copper_mcp.routing.jobs import (
    Candidate,
    RoutingJobConflictError,
    RoutingJobError,
    RoutingJobNotFoundError,
    RoutingJobRecord,
    RoutingJobSpec,
    RoutingJobStore,
)
from copper_mcp.routing.layered_contracts import (
    LayeredRouteCandidate,
    canonical_layered_candidate_bytes,
    verify_layered_candidate_id,
)

_SHA256_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_PAD_ID_RE: Final = re.compile(r"^pad:[A-Za-z0-9_.:-]{1,160}$")
_MAX_SAFE_INT: Final = (1 << 53) - 1
_MAX_REQUEST_BYTES: Final = 96_000
_MAX_EXPORT_BYTES: Final = 512_000
_MAX_RECORDS: Final = 4_096
_MAX_TTL_MS: Final = 7 * 86_400_000
_MAX_REQUEST_FIELDS: Final = 64
_MAX_EXPORT_DEPTH: Final = 32
_FORBIDDEN_KEYS: Final = frozenset(
    {
        "board_bytes",
        "credential",
        "credentials",
        "drc",
        "drc_evidence",
        "password",
        "prompt",
        "secret",
        "token",
    }
)
_FORBIDDEN_KEY_FRAGMENTS: Final = (
    "credential",
    "password",
    "prompt",
    "secret",
    "token",
    "private_key",
    "api_key",
    "drc",
)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _integer(name: str, value: object, *, minimum: int = 0, maximum: int = _MAX_SAFE_INT) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported integer range")
    return value


def _digest(name: str, value: object) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be content-addressed with sha256")
    return value


def _canonical_bytes(payload: object) -> bytes:
    if isinstance(payload, Mapping):
        payload = {str(key): _jsonable(value) for key, value in payload.items()}
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


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(child) for child in value]
    if isinstance(value, list):
        return [_jsonable(child) for child in value]
    return value


def _freeze_json(value: object) -> object:
    """Deep-freeze normalized JSON so a persisted envelope cannot be mutated in memory."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(child) for key, child in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: object) -> object:
    """Return ordinary JSON containers when a safe envelope is serialized for a caller."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _scan_request(value: object, *, depth: int = 0) -> None:
    if depth > _MAX_EXPORT_DEPTH:
        raise ValueError("routing request nesting exceeds the supported limit")
    if isinstance(value, Mapping):
        if len(value) > _MAX_REQUEST_FIELDS:
            raise ValueError("routing request has too many fields")
        for key, child in value.items():
            normalized_key = key.lower() if isinstance(key, str) else ""
            if (
                not isinstance(key, str)
                or normalized_key in _FORBIDDEN_KEYS
                or any(fragment in normalized_key for fragment in _FORBIDDEN_KEY_FRAGMENTS)
            ):
                raise ValueError("routing request contains a forbidden field")
            _scan_request(child, depth=depth + 1)
    elif isinstance(value, list | tuple):
        if len(value) > _MAX_REQUEST_FIELDS * 256:
            raise ValueError("routing request contains too many values")
        for child in value:
            _scan_request(child, depth=depth + 1)
    elif isinstance(value, str | int | float | bool) or value is None:
        return
    else:
        raise ValueError("routing request contains an unsupported value")


def _authorization_digest(value: object) -> str:
    return _digest("authorization context digest", value)


class RoutingJobRequestUnavailableError(RoutingJobError):
    """Unknown, expired, or unauthorized request envelopes share one error."""


class RoutingJobRequestConflictError(RoutingJobError):
    """A deterministic job ID was presented with different request/auth metadata."""


class RoutingCandidateExportUnavailableError(RoutingJobError):
    """Unknown, expired, or unauthorized candidate exports share one error."""


class RoutingCandidateExportConflictError(RoutingJobError):
    """An immutable candidate ID was presented with different export bytes."""


@dataclass(frozen=True, slots=True)
class RoutingJobRequestEnvelope:
    """One immutable, normalized request retained without board content."""

    job_id: str
    request_digest: str
    authorization_digest: str
    request: Mapping[str, object]
    created_at_ms: int = 0
    updated_at_ms: int = 0
    expires_at_ms: int = 0

    def __post_init__(self) -> None:
        _digest("job ID", self.job_id)
        _digest("request digest", self.request_digest)
        _authorization_digest(self.authorization_digest)
        if not isinstance(self.request, Mapping):
            raise ValueError("routing request must be an object")
        _scan_request(self.request)
        raw = _canonical_bytes(self.request)
        if len(raw) > _MAX_REQUEST_BYTES:
            raise ValueError("routing request exceeds the persisted size limit")
        expected = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if self.request_digest != expected:
            raise ValueError("routing request digest does not match its normalized content")
        _integer("request creation timestamp", self.created_at_ms)
        _integer("request update timestamp", self.updated_at_ms)
        _integer("request expiry timestamp", self.expires_at_ms)
        if self.updated_at_ms < self.created_at_ms or (
            self.expires_at_ms and self.expires_at_ms < self.created_at_ms
        ):
            raise ValueError("routing request timestamps are inconsistent")
        object.__setattr__(self, "request", _freeze_json(self.request))

    @classmethod
    def create(
        cls,
        *,
        spec: RoutingJobSpec,
        request: Mapping[str, object],
        authorization_digest: str,
        now_ms: int = 0,
        expires_at_ms: int = 0,
    ) -> RoutingJobRequestEnvelope:
        if not isinstance(spec, RoutingJobSpec):
            raise ValueError("routing job specification is malformed")
        raw = _canonical_bytes(request)
        digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if digest != spec.request_digest:
            raise ValueError("routing request does not match the job specification")
        return cls(
            job_id=spec.job_id,
            request_digest=spec.request_digest,
            authorization_digest=authorization_digest,
            request=request,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            expires_at_ms=expires_at_ms,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "authorization_digest": self.authorization_digest,
            "created_at_ms": self.created_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "job_id": self.job_id,
            "request": _thaw_json(self.request),
            "request_digest": self.request_digest,
            "updated_at_ms": self.updated_at_ms,
        }


class RoutingJobRequestStore:
    """Bounded SQLite persistence for normalized file-backed routing requests."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_records: int = 256,
        ttl_ms: int = 86_400_000,
    ) -> None:
        if not isinstance(path, str | Path):
            raise ValueError("routing request store path is malformed")
        _integer("maximum request records", max_records, minimum=1, maximum=_MAX_RECORDS)
        _integer("request TTL", ttl_ms, minimum=1, maximum=_MAX_TTL_MS)
        self.max_records = max_records
        self.ttl_ms = ttl_ms
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS routing_job_requests (
                job_id TEXT PRIMARY KEY NOT NULL,
                request_digest TEXT NOT NULL,
                authorization_digest TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                request_json BLOB NOT NULL
            ) WITHOUT ROWID
            """
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> RoutingJobRequestStore:
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
        return self._connection.execute(
            "DELETE FROM routing_job_requests WHERE expires_at_ms <= ?", (now_ms,)
        ).rowcount

    @staticmethod
    def _decode(row: tuple[object, ...]) -> RoutingJobRequestEnvelope:
        if len(row) != 7:
            raise RoutingJobError("stored routing request row is malformed")
        job_id, digest, auth, created, updated, expires, payload = row
        if not isinstance(job_id, str) or not isinstance(digest, str) or not isinstance(auth, str):
            raise RoutingJobError("stored routing request identity is malformed")
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in (created, updated, expires)
        ):
            raise RoutingJobError("stored routing request timestamps are malformed")
        if not isinstance(payload, bytes | str):
            raise RoutingJobError("stored routing request payload is malformed")
        raw = payload if isinstance(payload, bytes) else payload.encode("utf-8", errors="strict")
        if len(raw) > _MAX_REQUEST_BYTES:
            raise RoutingJobError("stored routing request is oversized")
        try:
            decoded = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RoutingJobError("stored routing request is malformed") from error
        if not isinstance(decoded, dict):
            raise RoutingJobError("stored routing request is not an object")
        return RoutingJobRequestEnvelope(
            job_id=job_id,
            request_digest=digest,
            authorization_digest=auth,
            request=decoded,
            created_at_ms=cast(int, created),
            updated_at_ms=cast(int, updated),
            expires_at_ms=cast(int, expires),
        )

    def put(self, envelope: RoutingJobRequestEnvelope) -> RoutingJobRequestEnvelope:
        if not isinstance(envelope, RoutingJobRequestEnvelope):
            raise ValueError("routing request envelope is malformed")
        with self._lock:
            cursor = self._transaction()
            try:
                self._purge_locked(envelope.created_at_ms)
                row = self._connection.execute(
                    "SELECT job_id, request_digest, authorization_digest, created_at_ms, "
                    "updated_at_ms, expires_at_ms, request_json FROM routing_job_requests "
                    "WHERE job_id = ?",
                    (envelope.job_id,),
                ).fetchone()
                if row is not None:
                    existing = self._decode(cast(tuple[object, ...], row))
                    if (
                        existing.job_id != envelope.job_id
                        or existing.request_digest != envelope.request_digest
                        or existing.authorization_digest != envelope.authorization_digest
                        or _canonical_bytes(existing.request) != _canonical_bytes(envelope.request)
                    ):
                        raise RoutingJobRequestConflictError(
                            "routing job request is already bound to different metadata"
                        )
                    self._commit()
                    return existing
                count = cursor.execute("SELECT COUNT(*) FROM routing_job_requests").fetchone()
                if count is None or not isinstance(count[0], int):
                    raise RoutingJobError("routing request store count is malformed")
                if count[0] >= self.max_records:
                    raise RoutingJobError("routing request store capacity is exhausted")
                raw = _canonical_bytes(envelope.request)
                cursor.execute(
                    "INSERT INTO routing_job_requests(job_id, request_digest, "
                    "authorization_digest, created_at_ms, updated_at_ms, expires_at_ms, "
                    "request_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        envelope.job_id,
                        envelope.request_digest,
                        envelope.authorization_digest,
                        envelope.created_at_ms,
                        envelope.updated_at_ms,
                        envelope.expires_at_ms,
                        sqlite3.Binary(raw),
                    ),
                )
                self._commit()
                return envelope
            except BaseException:
                self._rollback()
                raise

    def get(
        self,
        job_id: str,
        authorization_digest: str,
        *,
        now_ms: int | None = None,
    ) -> RoutingJobRequestEnvelope:
        timestamp = _now_ms() if now_ms is None else now_ms
        _integer("request lookup timestamp", timestamp)
        try:
            _digest("job ID", job_id)
            _authorization_digest(authorization_digest)
        except ValueError:
            raise RoutingJobRequestUnavailableError("routing request is unavailable") from None
        with self._lock:
            self._transaction()
            try:
                self._purge_locked(timestamp)
                row = self._connection.execute(
                    "SELECT job_id, request_digest, authorization_digest, created_at_ms, "
                    "updated_at_ms, expires_at_ms, request_json FROM routing_job_requests "
                    "WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise RoutingJobRequestUnavailableError("routing request is unavailable")
                envelope = self._decode(cast(tuple[object, ...], row))
                if envelope.authorization_digest != authorization_digest:
                    raise RoutingJobRequestUnavailableError("routing request is unavailable")
                self._commit()
                return envelope
            except BaseException:
                self._rollback()
                raise

    def remove(self, job_id: str) -> None:
        """Remove one envelope during repository-level creation rollback."""

        with self._lock:
            self._connection.execute("DELETE FROM routing_job_requests WHERE job_id = ?", (job_id,))


def _candidate_document(candidate: Candidate) -> tuple[str, bytes, dict[str, object]]:
    if isinstance(candidate, RouteCandidate):
        verify_candidate_id(candidate)
        kind = CandidateManifestKind.SINGLE_LAYER.value
        canonical = canonical_candidate_bytes(candidate)
    elif isinstance(candidate, LayeredRouteCandidate):
        verify_layered_candidate_id(candidate)
        kind = CandidateManifestKind.LAYERED.value
        canonical = canonical_layered_candidate_bytes(candidate)
    else:
        raise RoutingJobError("candidate export requires a supported route candidate")
    try:
        document = json.loads(canonical.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RoutingJobError("candidate canonicalization is malformed") from error
    if not isinstance(document, dict):
        raise RoutingJobError("candidate canonicalization is not an object")
    document = dict(document)
    document["candidate_id"] = candidate.candidate_id
    rendered = _canonical_bytes(document)
    if len(rendered) > _MAX_EXPORT_BYTES:
        raise RoutingJobError("candidate export exceeds the persisted size limit")
    return kind, rendered, document


def _candidate_document_digest(document: Mapping[str, object]) -> str:
    """Recompute a stored export's content address without trusting its ID field."""

    identity = dict(document)
    candidate_id = identity.pop("candidate_id", None)
    if not isinstance(candidate_id, str):
        raise RoutingJobError("stored candidate export identity is malformed")
    rendered = (
        json.dumps(
            identity,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
        + b"\n"
    )
    return f"sha256:{hashlib.sha256(rendered).hexdigest()}"


class RoutingCandidateExportStore:
    """Bounded, authorization-bound persistence for explicit candidate geometry export."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_records: int = 256,
        ttl_ms: int = 86_400_000,
    ) -> None:
        if not isinstance(path, str | Path):
            raise ValueError("candidate export store path is malformed")
        _integer("maximum candidate exports", max_records, minimum=1, maximum=_MAX_RECORDS)
        _integer("candidate export TTL", ttl_ms, minimum=1, maximum=_MAX_TTL_MS)
        self.max_records = max_records
        self.ttl_ms = ttl_ms
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS routing_candidate_exports (
                candidate_id TEXT PRIMARY KEY NOT NULL,
                job_id TEXT NOT NULL,
                base_revision TEXT NOT NULL,
                kind TEXT NOT NULL,
                authorization_digest TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                candidate_json BLOB NOT NULL
            ) WITHOUT ROWID
            """
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> RoutingCandidateExportStore:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _purge_locked(self, now_ms: int) -> int:
        return self._connection.execute(
            "DELETE FROM routing_candidate_exports WHERE expires_at_ms <= ?", (now_ms,)
        ).rowcount

    def put(
        self,
        candidate: Candidate,
        *,
        spec: RoutingJobSpec,
        authorization_digest: str,
        now_ms: int | None = None,
    ) -> dict[str, object]:
        if not isinstance(spec, RoutingJobSpec):
            raise ValueError("routing job specification is malformed")
        if not isinstance(candidate, RouteCandidate | LayeredRouteCandidate):
            raise RoutingJobError("candidate export requires a supported route candidate")
        _authorization_digest(authorization_digest)
        if candidate.base_revision != spec.expected_candidate_revision:
            raise RoutingJobError("candidate base revision does not match the job")
        if candidate.start_pad_id != spec.start_pad_id or candidate.end_pad_id != spec.end_pad_id:
            raise RoutingJobError("candidate endpoints do not match the job")
        kind, raw, document = _candidate_document(candidate)
        timestamp = _now_ms() if now_ms is None else now_ms
        _integer("candidate export timestamp", timestamp)
        expiry = timestamp + self.ttl_ms
        _integer("candidate export expiry", expiry)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._purge_locked(timestamp)
                row = self._connection.execute(
                    "SELECT candidate_id, job_id, base_revision, kind, authorization_digest, "
                    "created_at_ms, expires_at_ms, candidate_json FROM routing_candidate_exports "
                    "WHERE candidate_id = ?",
                    (candidate.candidate_id,),
                ).fetchone()
                if row is not None:
                    existing_raw = row[7]
                    existing_bytes = (
                        existing_raw
                        if isinstance(existing_raw, bytes)
                        else str(existing_raw).encode("utf-8")
                    )
                    if (
                        row[1] != spec.job_id
                        or row[4] != authorization_digest
                        or existing_bytes != raw
                    ):
                        raise RoutingCandidateExportConflictError(
                            "candidate export is already bound to different metadata"
                        )
                    self._connection.commit()
                    return document
                count = self._connection.execute(
                    "SELECT COUNT(*) FROM routing_candidate_exports"
                ).fetchone()
                if count is None or not isinstance(count[0], int):
                    raise RoutingJobError("candidate export store count is malformed")
                if count[0] >= self.max_records:
                    raise RoutingJobError("candidate export store capacity is exhausted")
                self._connection.execute(
                    "INSERT INTO routing_candidate_exports(candidate_id, job_id, base_revision, "
                    "kind, authorization_digest, created_at_ms, expires_at_ms, candidate_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        candidate.candidate_id,
                        spec.job_id,
                        candidate.base_revision,
                        kind,
                        authorization_digest,
                        timestamp,
                        expiry,
                        sqlite3.Binary(raw),
                    ),
                )
                self._connection.commit()
                return document
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def get(
        self,
        job_id: str,
        candidate_id: str,
        authorization_digest: str,
        *,
        now_ms: int | None = None,
    ) -> dict[str, object]:
        timestamp = _now_ms() if now_ms is None else now_ms
        _integer("candidate export lookup timestamp", timestamp)
        try:
            _digest("job ID", job_id)
            _digest("candidate ID", candidate_id)
            _authorization_digest(authorization_digest)
        except ValueError:
            raise RoutingCandidateExportUnavailableError(
                "routing candidate export is unavailable"
            ) from None
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._purge_locked(timestamp)
                row = self._connection.execute(
                    "SELECT job_id, authorization_digest, kind, candidate_json FROM "
                    "routing_candidate_exports WHERE candidate_id = ?",
                    (candidate_id,),
                ).fetchone()
                if row is None or row[0] != job_id or row[1] != authorization_digest:
                    raise RoutingCandidateExportUnavailableError(
                        "routing candidate export is unavailable"
                    )
                if row[2] not in {
                    CandidateManifestKind.SINGLE_LAYER.value,
                    CandidateManifestKind.LAYERED.value,
                }:
                    raise RoutingJobError("stored candidate export kind is unsupported")
                raw = row[3] if isinstance(row[3], bytes) else str(row[3]).encode("utf-8")
                if len(raw) > _MAX_EXPORT_BYTES:
                    raise RoutingJobError("stored candidate export is oversized")
                try:
                    document = json.loads(raw.decode("utf-8", errors="strict"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise RoutingJobError("stored candidate export is malformed") from error
                if (
                    not isinstance(document, dict)
                    or document.get("candidate_id") != candidate_id
                    or _candidate_document_digest(document) != candidate_id
                ):
                    raise RoutingJobError("stored candidate export identity is inconsistent")
                self._connection.commit()
                return cast(dict[str, object], document)
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise


def _manifest_for_candidate(
    candidate: Candidate,
    *,
    spec: RoutingJobSpec,
    now_ms: int,
) -> CandidateManifest:
    if isinstance(candidate, RouteCandidate):
        kind = CandidateManifestKind.SINGLE_LAYER
        path_count = len(candidate.patch.paths)
        via_count = candidate.metrics.vias
        cost = candidate.cost.total_cost_nm
        metrics: Mapping[str, int | float] = {
            "bend_count": candidate.cost.bend_count,
            "expanded_states": candidate.metrics.expanded_states,
            "obstacle_checks": candidate.metrics.obstacle_checks,
            "wire_length_nm": candidate.metrics.wire_length_nm,
        }
        router = candidate.router_version
        policy = candidate.policy
    else:
        kind = CandidateManifestKind.LAYERED
        path_count = len(candidate.patch.paths)
        via_count = len(candidate.patch.vias)
        cost = candidate.cost.total_search_cost_units
        metrics = {
            "bend_count": candidate.metrics.bend_count,
            "expanded_states": candidate.metrics.expanded_states,
            "obstacle_checks": candidate.metrics.obstacle_checks,
            "wire_length_nm": candidate.metrics.wire_length_nm,
        }
        router = candidate.router_version
        policy = candidate.policy
    return CandidateManifest.create(
        candidate_id=candidate.candidate_id,
        base_revision=candidate.base_revision,
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        kind=kind,
        router=router,
        policy=policy,
        path_count=path_count,
        via_count=via_count,
        cost=cost,
        metrics=metrics,
        job_id=spec.job_id,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        expires_at_ms=now_ms,
    )


class RoutingJobRepository:
    """Compose lifecycle, request, manifest, and explicit geometry stores."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_records: int = 256,
        ttl_ms: int = 86_400_000,
    ) -> None:
        self.jobs = RoutingJobStore(path, max_records=max_records, ttl_ms=ttl_ms)
        self.requests = RoutingJobRequestStore(path, max_records=max_records, ttl_ms=ttl_ms)
        self.manifests = CandidateManifestStore(path, max_records=max_records, ttl_ms=ttl_ms)
        self.exports = RoutingCandidateExportStore(path, max_records=max_records, ttl_ms=ttl_ms)
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self.exports.close()
            self.manifests.close()
            self.requests.close()
            self.jobs.close()

    def __enter__(self) -> RoutingJobRepository:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def create(
        self,
        spec: RoutingJobSpec,
        request: Mapping[str, object],
        authorization_digest: str,
        *,
        now_ms: int | None = None,
    ) -> RoutingJobRecord:
        timestamp = _now_ms() if now_ms is None else now_ms
        expiry = timestamp + self.requests.ttl_ms
        envelope = RoutingJobRequestEnvelope.create(
            spec=spec,
            request=request,
            authorization_digest=authorization_digest,
            now_ms=timestamp,
            expires_at_ms=expiry,
        )
        with self._lock:
            self.requests.put(envelope)
            try:
                return self.jobs.create(spec, now_ms=timestamp)
            except BaseException:
                # A request row without its lifecycle row cannot be looked up, so roll it back
                # when the job store rejects a create due to a race or capacity.
                self.requests.remove(spec.job_id)
                raise

    def get(
        self,
        job_id: str,
        authorization_digest: str,
        *,
        now_ms: int | None = None,
    ) -> tuple[RoutingJobRecord, RoutingJobRequestEnvelope]:
        envelope = self.requests.get(job_id, authorization_digest, now_ms=now_ms)
        try:
            record = self.jobs.get(job_id, now_ms=now_ms)
        except RoutingJobNotFoundError as error:
            raise RoutingJobRequestUnavailableError("routing request is unavailable") from error
        if record.spec.request_digest != envelope.request_digest:
            raise RoutingJobError("routing request and lifecycle record are inconsistent")
        return record, envelope

    def publish_candidate(
        self,
        job_id: str,
        candidate: Candidate,
        *,
        expected_revision: int,
        authorization_digest: str,
        now_ms: int | None = None,
    ) -> RoutingJobRecord:
        record, envelope = self.get(job_id, authorization_digest, now_ms=now_ms)
        if record.revision != expected_revision:
            raise RoutingJobConflictError("routing job revision conflict")
        timestamp = _now_ms() if now_ms is None else now_ms
        self.exports.put(
            candidate,
            spec=record.spec,
            authorization_digest=envelope.authorization_digest,
            now_ms=timestamp,
        )
        manifest = _manifest_for_candidate(candidate, spec=record.spec, now_ms=timestamp)
        self.manifests.put(manifest, now_ms=timestamp)
        return self.jobs.complete(
            job_id,
            candidate,
            expected_revision=expected_revision,
            now_ms=timestamp,
        )

    def execute(
        self,
        job_id: str,
        authorization_digest: str,
        executor: RoutingJobExecutor,
    ) -> RoutingJobRecord:
        """Execute one job while publishing geometry only after executor output is verified."""

        record, _ = self.get(job_id, authorization_digest)
        worker = RoutingJobWorker(
            self.jobs,
            limits=WorkerLimits(
                lease_ms=max(1, min(30_000, record.spec.limits.max_runtime_ms)),
            ),
        )

        def wrapped(probe: object) -> Candidate:
            candidate = executor(cast(Any, probe))
            record = self.jobs.get(job_id)
            # The export/manifest writes are immutable and bounded. The worker still performs the
            # final lifecycle CAS, so a stale worker cannot publish a newer revision.
            envelope = self.requests.get(job_id, authorization_digest)
            self.exports.put(
                candidate,
                spec=record.spec,
                authorization_digest=envelope.authorization_digest,
            )
            self.manifests.put(
                _manifest_for_candidate(
                    candidate,
                    spec=record.spec,
                    now_ms=record.updated_at_ms,
                )
            )
            return candidate

        return worker.execute(job_id, wrapped)


__all__ = [
    "RoutingCandidateExportConflictError",
    "RoutingCandidateExportStore",
    "RoutingCandidateExportUnavailableError",
    "RoutingJobRepository",
    "RoutingJobRequestConflictError",
    "RoutingJobRequestEnvelope",
    "RoutingJobRequestStore",
    "RoutingJobRequestUnavailableError",
]
