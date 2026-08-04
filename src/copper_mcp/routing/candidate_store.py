"""Durable, redacted storage for immutable routing-candidate manifests.

The manifest store is deliberately independent of a router or board format.  It persists the
identity and bounded summary of a candidate, never its patch geometry, source board, raw net
names, prompts, credentials, or DRC evidence.  A candidate can therefore be looked up after a
worker restart without turning the job ledger into a hidden board export.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast

from copper_mcp.routing.jobs import RoutingJobSpec

_SCHEMA_VERSION: Final = "0.1.0"
_SHA256_PREFIX: Final = "sha256:"
_SHA256_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_PAD_ID_RE: Final = re.compile(r"^pad:[A-Za-z0-9_.:-]{1,160}$")
_STABLE_NAME_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_METRIC_KEY_RE: Final = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_MAX_SAFE_INT: Final = (1 << 53) - 1
_MAX_RECORDS: Final = 4_096
_MAX_TTL_MS: Final = 7 * 86_400_000
_MAX_MANIFEST_BYTES: Final = 64_000
_MAX_METRICS: Final = 32
_MAX_COUNT: Final = 4_096
_MAX_METRIC_VALUE: Final = float(_MAX_SAFE_INT)
_FORBIDDEN_METRIC_PARTS: Final = (
    "board",
    "credential",
    "drc",
    "geometry",
    "net",
    "prompt",
    "secret",
    "token",
    "violation",
    "vertex",
)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _integer(name: str, value: object, *, minimum: int = 0, maximum: int = _MAX_SAFE_INT) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported integer range")


def _digest(name: str, value: object, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be content-addressed with sha256")


def _pad_id(name: str, value: object) -> None:
    if not isinstance(value, str) or _PAD_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a stable pad ID")


def _stable_name(name: str, value: object) -> None:
    if not isinstance(value, str) or _STABLE_NAME_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a stable lowercase identifier")


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


class CandidateManifestError(ValueError):
    """Base class for malformed, unavailable, or conflicting manifests."""


class CandidateManifestNotFoundError(CandidateManifestError):
    """The candidate is unknown or has expired.

    Unknown and expired IDs intentionally share this exception and message so a caller cannot
    use this API to distinguish an old candidate from an ID that was never present.
    """


class CandidateManifestLimitError(CandidateManifestError):
    """The bounded manifest store cannot accept another record."""


class CandidateManifestConflictError(CandidateManifestError):
    """A candidate ID was already bound to a different immutable manifest."""


class CandidateManifestIntegrityError(CandidateManifestError):
    """Stored metadata or its content address failed integrity validation."""


class CandidateManifestBindingError(CandidateManifestError):
    """A manifest does not match the supplied immutable routing-job specification."""


class CandidateManifestKind(StrEnum):
    """Stable request families accepted by the manifest boundary."""

    SINGLE_LAYER = "single-layer"
    LAYERED = "layered"


@dataclass(frozen=True, slots=True)
class CandidateManifest:
    """An immutable, content-addressed candidate summary without geometry.

    ``created_at_ms``, ``updated_at_ms``, and ``expires_at_ms`` are store metadata and are not
    included in ``manifest_digest``.  This lets a caller construct a zero-timestamp manifest and
    lets the store apply its own injected clock and TTL while retaining one stable content address.
    """

    candidate_id: str
    base_revision: str
    start_pad_id: str
    end_pad_id: str
    kind: str
    router: str
    policy: str
    path_count: int
    via_count: int
    cost: int
    metrics: Mapping[str, int | float]
    job_id: str | None = None
    created_at_ms: int = 0
    updated_at_ms: int = 0
    expires_at_ms: int = 0
    manifest_digest: str = ""

    def __post_init__(self) -> None:
        _digest("candidate ID", self.candidate_id)
        _digest("base revision", self.base_revision)
        _pad_id("start pad ID", self.start_pad_id)
        _pad_id("end pad ID", self.end_pad_id)
        if self.start_pad_id == self.end_pad_id:
            raise ValueError("candidate manifest endpoints must be distinct")
        _stable_name("candidate kind", self.kind)
        if self.kind not in {kind.value for kind in CandidateManifestKind}:
            raise ValueError("candidate manifest kind is unsupported")
        _stable_name("candidate router", self.router)
        _stable_name("candidate policy", self.policy)
        _integer("candidate path count", self.path_count, maximum=_MAX_COUNT)
        _integer("candidate via count", self.via_count, maximum=_MAX_COUNT)
        _integer("candidate cost", self.cost)
        _metrics(self.metrics)
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        _digest("job ID", self.job_id, optional=True)
        _integer("created timestamp", self.created_at_ms)
        _integer("updated timestamp", self.updated_at_ms)
        _integer("expiry timestamp", self.expires_at_ms)
        if self.updated_at_ms < self.created_at_ms:
            raise ValueError("updated timestamp cannot precede creation")
        if self.expires_at_ms and self.expires_at_ms < self.created_at_ms:
            raise ValueError("expiry timestamp cannot precede creation")
        _digest("manifest digest", self.manifest_digest)
        if self.manifest_digest != self.expected_digest:
            raise CandidateManifestIntegrityError(
                "candidate manifest digest does not match content"
            )

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        base_revision: str,
        start_pad_id: str,
        end_pad_id: str,
        kind: str | CandidateManifestKind,
        router: str,
        policy: str,
        path_count: int,
        via_count: int,
        cost: int,
        metrics: Mapping[str, int | float],
        job_id: str | None = None,
        created_at_ms: int = 0,
        updated_at_ms: int = 0,
        expires_at_ms: int = 0,
    ) -> CandidateManifest:
        resolved_kind = kind.value if isinstance(kind, CandidateManifestKind) else kind
        payload = cls._identity_payload(
            candidate_id=candidate_id,
            base_revision=base_revision,
            start_pad_id=start_pad_id,
            end_pad_id=end_pad_id,
            kind=resolved_kind,
            router=router,
            policy=policy,
            path_count=path_count,
            via_count=via_count,
            cost=cost,
            metrics=metrics,
            job_id=job_id,
        )
        digest = f"{_SHA256_PREFIX}{hashlib.sha256(_canonical_bytes(payload)).hexdigest()}"
        return cls(
            candidate_id=candidate_id,
            base_revision=base_revision,
            start_pad_id=start_pad_id,
            end_pad_id=end_pad_id,
            kind=resolved_kind,
            router=router,
            policy=policy,
            path_count=path_count,
            via_count=via_count,
            cost=cost,
            metrics=dict(metrics),
            job_id=job_id,
            created_at_ms=created_at_ms,
            updated_at_ms=updated_at_ms,
            expires_at_ms=expires_at_ms,
            manifest_digest=digest,
        )

    @property
    def expected_digest(self) -> str:
        digest = hashlib.sha256(_canonical_bytes(self._identity_payload_from_self())).hexdigest()
        return f"{_SHA256_PREFIX}{digest}"

    def _identity_payload_from_self(self) -> dict[str, object]:
        return self._identity_payload(
            candidate_id=self.candidate_id,
            base_revision=self.base_revision,
            start_pad_id=self.start_pad_id,
            end_pad_id=self.end_pad_id,
            kind=self.kind,
            router=self.router,
            policy=self.policy,
            path_count=self.path_count,
            via_count=self.via_count,
            cost=self.cost,
            metrics=self.metrics,
            job_id=self.job_id,
        )

    @staticmethod
    def _identity_payload(
        *,
        candidate_id: str,
        base_revision: str,
        start_pad_id: str,
        end_pad_id: str,
        kind: str,
        router: str,
        policy: str,
        path_count: int,
        via_count: int,
        cost: int,
        metrics: Mapping[str, int | float],
        job_id: str | None,
    ) -> dict[str, object]:
        return {
            "base_revision": base_revision,
            "candidate_id": candidate_id,
            "end_pad_id": end_pad_id,
            "job_id": job_id,
            "kind": kind,
            "metrics": dict(sorted(metrics.items())),
            "path_count": path_count,
            "policy": policy,
            "router": router,
            "start_pad_id": start_pad_id,
            "via_count": via_count,
            "cost": cost,
            "schema_version": _SCHEMA_VERSION,
        }

    def with_timestamps(
        self, *, created_at_ms: int, updated_at_ms: int, expires_at_ms: int
    ) -> CandidateManifest:
        """Return this same immutable manifest with store-owned lifecycle timestamps."""

        return CandidateManifest(
            candidate_id=self.candidate_id,
            base_revision=self.base_revision,
            start_pad_id=self.start_pad_id,
            end_pad_id=self.end_pad_id,
            kind=self.kind,
            router=self.router,
            policy=self.policy,
            path_count=self.path_count,
            via_count=self.via_count,
            cost=self.cost,
            metrics=dict(self.metrics),
            job_id=self.job_id,
            created_at_ms=created_at_ms,
            updated_at_ms=updated_at_ms,
            expires_at_ms=expires_at_ms,
            manifest_digest=self.manifest_digest,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "base_revision": self.base_revision,
            "candidate_id": self.candidate_id,
            "created_at_ms": self.created_at_ms,
            "end_pad_id": self.end_pad_id,
            "expires_at_ms": self.expires_at_ms,
            "job_id": self.job_id,
            "kind": self.kind,
            "manifest_digest": self.manifest_digest,
            "metrics": dict(sorted(self.metrics.items())),
            "path_count": self.path_count,
            "policy": self.policy,
            "router": self.router,
            "schema_version": _SCHEMA_VERSION,
            "start_pad_id": self.start_pad_id,
            "updated_at_ms": self.updated_at_ms,
            "via_count": self.via_count,
            "cost": self.cost,
        }

    def to_json(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, payload: object) -> CandidateManifest:
        fields = {
            "base_revision",
            "candidate_id",
            "created_at_ms",
            "end_pad_id",
            "expires_at_ms",
            "job_id",
            "kind",
            "manifest_digest",
            "metrics",
            "path_count",
            "policy",
            "router",
            "schema_version",
            "start_pad_id",
            "updated_at_ms",
            "via_count",
            "cost",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise CandidateManifestIntegrityError("candidate manifest payload is not closed")
        if payload["schema_version"] != _SCHEMA_VERSION:
            raise CandidateManifestIntegrityError("candidate manifest schema is unsupported")
        return cls(
            candidate_id=payload["candidate_id"],
            base_revision=payload["base_revision"],
            start_pad_id=payload["start_pad_id"],
            end_pad_id=payload["end_pad_id"],
            kind=payload["kind"],
            router=payload["router"],
            policy=payload["policy"],
            path_count=payload["path_count"],
            via_count=payload["via_count"],
            cost=payload["cost"],
            metrics=payload["metrics"],
            job_id=payload["job_id"],
            created_at_ms=payload["created_at_ms"],
            updated_at_ms=payload["updated_at_ms"],
            expires_at_ms=payload["expires_at_ms"],
            manifest_digest=payload["manifest_digest"],
        )

    @classmethod
    def from_json(cls, payload: bytes) -> CandidateManifest:
        if not isinstance(payload, bytes) or len(payload) > _MAX_MANIFEST_BYTES:
            raise CandidateManifestIntegrityError("candidate manifest payload is oversized")
        try:
            decoded = json.loads(payload.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CandidateManifestIntegrityError(
                "candidate manifest payload is malformed"
            ) from error
        return cls.from_dict(decoded)


def _metrics(metrics: object) -> None:
    if not isinstance(metrics, Mapping) or len(metrics) > _MAX_METRICS:
        raise ValueError("candidate metrics are oversized")
    for key, value in metrics.items():
        if not isinstance(key, str) or _METRIC_KEY_RE.fullmatch(key) is None:
            raise ValueError("candidate metric key is malformed")
        if any(part in key for part in _FORBIDDEN_METRIC_PARTS):
            raise ValueError("candidate metrics cannot carry board or diagnostic data")
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("candidate metric value is not numeric")
        if not math.isfinite(float(value)) or abs(float(value)) > _MAX_METRIC_VALUE:
            raise ValueError("candidate metric value is outside the supported range")


def _validate_binding(manifest: CandidateManifest, job_spec: RoutingJobSpec) -> None:
    if not isinstance(job_spec, RoutingJobSpec):
        raise CandidateManifestBindingError("routing job specification is malformed")
    if manifest.base_revision != job_spec.expected_candidate_revision:
        raise CandidateManifestBindingError("candidate base revision does not match routing job")
    if (manifest.start_pad_id, manifest.end_pad_id) != (
        job_spec.start_pad_id,
        job_spec.end_pad_id,
    ):
        raise CandidateManifestBindingError("candidate endpoints do not match routing job")
    if manifest.kind != job_spec.request_kind.value:
        raise CandidateManifestBindingError("candidate kind does not match routing job")
    if manifest.router not in {job_spec.backend, job_spec.router_version}:
        raise CandidateManifestBindingError("candidate router does not match routing job")
    if manifest.policy != job_spec.policy:
        raise CandidateManifestBindingError("candidate policy does not match routing job")
    if manifest.job_id is not None and manifest.job_id != job_spec.job_id:
        raise CandidateManifestBindingError("candidate job ID does not match routing job")


class CandidateManifestStore:
    """A bounded SQLite store for redacted, immutable candidate manifests.

    The database contains one canonical manifest JSON blob and lifecycle columns.  ``put`` never
    updates an existing row: a repeated content-identical put is idempotent, while a different
    digest for the same candidate ID is refused.  The injected clock is in milliseconds and is
    used for every expiry decision, which keeps tests deterministic and restart behaviour explicit.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_records: int = 256,
        ttl_ms: int = 86_400_000,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(path, str | Path):
            raise ValueError("candidate manifest store path is malformed")
        if clock is not None and not callable(clock):
            raise ValueError("candidate manifest clock is malformed")
        _integer("maximum stored manifests", max_records, minimum=1, maximum=_MAX_RECORDS)
        _integer("manifest TTL", ttl_ms, minimum=1, maximum=_MAX_TTL_MS)
        self.max_records = max_records
        self.ttl_ms = ttl_ms
        self._clock = _now_ms if clock is None else clock
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS routing_candidate_manifests (
                candidate_id TEXT PRIMARY KEY NOT NULL,
                manifest_digest TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                manifest_json BLOB NOT NULL
            ) WITHOUT ROWID
            """
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> CandidateManifestStore:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _time(self, now_ms: int | None) -> int:
        resolved = self._clock() if now_ms is None else now_ms
        _integer("store timestamp", resolved)
        return resolved

    def _transaction(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _finish(self, *, commit: bool) -> None:
        if not self._connection.in_transaction:
            return
        if commit:
            self._connection.commit()
        else:
            self._connection.rollback()

    def _purge_locked(self, now_ms: int) -> int:
        cursor = self._connection.execute(
            "DELETE FROM routing_candidate_manifests WHERE expires_at_ms <= ?", (now_ms,)
        )
        return cursor.rowcount

    def _row_locked(self, candidate_id: str) -> tuple[object, ...] | None:
        row = self._connection.execute(
            """
            SELECT candidate_id, manifest_digest, created_at_ms, updated_at_ms,
                   expires_at_ms, manifest_json
            FROM routing_candidate_manifests WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()
        return cast(tuple[object, ...] | None, row)

    @staticmethod
    def _manifest_from_row(row: tuple[object, ...]) -> CandidateManifest:
        if len(row) != 6:
            raise CandidateManifestIntegrityError("stored candidate manifest row is malformed")
        candidate_id, digest, created, updated, expires, raw = row
        if not isinstance(candidate_id, str) or not isinstance(digest, str):
            raise CandidateManifestIntegrityError("stored candidate manifest identity is malformed")
        for name, value in (
            ("stored creation timestamp", created),
            ("stored update timestamp", updated),
            ("stored expiry timestamp", expires),
        ):
            _integer(name, value)
        if not isinstance(raw, bytes | str):
            raise CandidateManifestIntegrityError("stored candidate manifest payload is malformed")
        payload = raw if isinstance(raw, bytes) else raw.encode("utf-8", errors="strict")
        if len(payload) > _MAX_MANIFEST_BYTES:
            raise CandidateManifestIntegrityError("stored candidate manifest payload is oversized")
        manifest = CandidateManifest.from_json(payload)
        if (
            manifest.candidate_id != candidate_id
            or manifest.manifest_digest != digest
            or manifest.created_at_ms != created
            or manifest.updated_at_ms != updated
            or manifest.expires_at_ms != expires
        ):
            raise CandidateManifestIntegrityError(
                "stored candidate manifest metadata is inconsistent"
            )
        return manifest

    def put(
        self,
        manifest: CandidateManifest,
        *,
        job_spec: RoutingJobSpec | None = None,
        now_ms: int | None = None,
    ) -> CandidateManifest:
        if not isinstance(manifest, CandidateManifest):
            raise ValueError("candidate manifest is malformed")
        if job_spec is not None:
            _validate_binding(manifest, job_spec)
        timestamp = self._time(now_ms)
        expires = timestamp + self.ttl_ms
        _integer("manifest expiry timestamp", expires)
        stamped = manifest.with_timestamps(
            created_at_ms=timestamp,
            updated_at_ms=timestamp,
            expires_at_ms=expires,
        )
        payload = stamped.to_json()
        if len(payload) > _MAX_MANIFEST_BYTES:
            raise CandidateManifestLimitError("candidate manifest payload is oversized")
        with self._lock:
            self._transaction()
            try:
                self._purge_locked(timestamp)
                existing_row = self._row_locked(manifest.candidate_id)
                if existing_row is not None:
                    existing = self._manifest_from_row(existing_row)
                    if existing.manifest_digest != manifest.manifest_digest:
                        raise CandidateManifestConflictError(
                            "candidate ID is already bound to another manifest"
                        )
                    self._finish(commit=True)
                    return existing
                count = self._connection.execute(
                    "SELECT COUNT(*) FROM routing_candidate_manifests"
                ).fetchone()
                if count is None or not isinstance(count[0], int):
                    raise CandidateManifestIntegrityError(
                        "candidate manifest store count is malformed"
                    )
                if count[0] >= self.max_records:
                    raise CandidateManifestLimitError(
                        "candidate manifest store capacity is exhausted"
                    )
                self._connection.execute(
                    """
                    INSERT INTO routing_candidate_manifests(
                        candidate_id, manifest_digest, created_at_ms, updated_at_ms,
                        expires_at_ms, manifest_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stamped.candidate_id,
                        stamped.manifest_digest,
                        stamped.created_at_ms,
                        stamped.updated_at_ms,
                        stamped.expires_at_ms,
                        sqlite3.Binary(payload),
                    ),
                )
                self._finish(commit=True)
                return stamped
            except BaseException:
                self._finish(commit=False)
                raise

    def get(self, candidate_id: str, *, now_ms: int | None = None) -> CandidateManifest:
        timestamp = self._time(now_ms)
        if not isinstance(candidate_id, str) or _SHA256_RE.fullmatch(candidate_id) is None:
            raise CandidateManifestNotFoundError("candidate manifest is unavailable")
        with self._lock:
            self._transaction()
            try:
                self._purge_locked(timestamp)
                row = self._row_locked(candidate_id)
                if row is None:
                    raise CandidateManifestNotFoundError("candidate manifest is unavailable")
                manifest = self._manifest_from_row(row)
                self._finish(commit=True)
                return manifest
            except BaseException:
                self._finish(commit=False)
                raise

    def purge(self, *, now_ms: int | None = None) -> int:
        timestamp = self._time(now_ms)
        with self._lock:
            self._transaction()
            try:
                purged = self._purge_locked(timestamp)
                self._finish(commit=True)
                return purged
            except BaseException:
                self._finish(commit=False)
                raise


# Verbose aliases keep the public boundary self-describing while retaining concise names for
# callers that already use the surrounding ``CandidateManifest`` vocabulary.
RoutingCandidateManifest = CandidateManifest
RoutingCandidateManifestStore = CandidateManifestStore


__all__ = [
    "CandidateManifest",
    "CandidateManifestBindingError",
    "CandidateManifestConflictError",
    "CandidateManifestError",
    "CandidateManifestIntegrityError",
    "CandidateManifestKind",
    "CandidateManifestLimitError",
    "CandidateManifestNotFoundError",
    "CandidateManifestStore",
    "RoutingCandidateManifest",
    "RoutingCandidateManifestStore",
]
