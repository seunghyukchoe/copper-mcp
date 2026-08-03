"""Bounded, non-enumerable process-local capability store.

Extracted from the schematic store so that a second artifact kind reuses the reviewed
TTL/LRU/locking discipline rather than a second copy of it. The security properties are
stated once and inherited: opaque capability tokens with at least 256 bits of entropy, no
listing API, absolute expiry that a read does not renew, deterministic least-recently-used
eviction, and a digest recheck on every read.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class ArtifactUnavailableError(ValueError):
    """Raised uniformly for invalid, expired, evicted, or unknown capabilities.

    One error for every cause on purpose: distinguishing "expired" from "never existed"
    would let a caller probe which tokens once existed.
    """


@dataclass(frozen=True, slots=True)
class _StoredArtifact:
    content: bytes
    digest: str
    created_at: float
    size_bytes: int
    #: The object the bytes came from, retained so a subclass can cross-check it on read.
    #: Keeping it is what detects an artifact mutated *after* insertion, which checking the
    #: stored bytes alone cannot see.
    origin: object | None = None


class BoundedArtifactStore:
    """Thread-safe, non-enumerable, TTL and LRU bounded in-memory store."""

    def __init__(
        self,
        *,
        uri_template: str,
        max_artifacts: int,
        max_total_bytes: int,
        ttl_seconds: int,
        max_artifact_bytes: int,
        ceilings: tuple[int, int, int],
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        limits = (
            (max_artifacts, ceilings[0]),
            (max_total_bytes, ceilings[1]),
            (ttl_seconds, ceilings[2]),
        )
        # Tighten-only: a caller may make the store smaller or shorter-lived, never larger or
        # longer-lived than the reviewed defaults.
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > ceiling
            for value, ceiling in limits
        ):
            raise ValueError("artifact store limits must be positive and tighten-only")
        if not callable(clock) or (token_factory is not None and not callable(token_factory)):
            raise ValueError("artifact store providers must be callable")
        self._uri_template = uri_template
        self._max_artifacts = max_artifacts
        self._max_total_bytes = max_total_bytes
        self._ttl_seconds = ttl_seconds
        self._max_artifact_bytes = max_artifact_bytes
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._entries: OrderedDict[str, _StoredArtifact] = OrderedDict()
        self._total_bytes = 0
        self._lock = threading.Lock()

    def _purge_expired(self, now: float) -> None:
        expired = [
            token
            for token, entry in self._entries.items()
            if now - entry.created_at >= self._ttl_seconds
        ]
        for token in expired:
            entry = self._entries.pop(token)
            self._total_bytes -= entry.size_bytes

    def _new_token(self) -> str:
        for _ in range(8):
            token = self._token_factory()
            if isinstance(token, str) and _TOKEN.fullmatch(token) and token not in self._entries:
                return token
        raise RuntimeError("artifact capability generation failed")

    def _verify_entry(self, origin: object | None, content: bytes, digest: str) -> bool:
        """Hook for a subclass to cross-check its own retained object on every read."""

        return True

    def _store(self, content: bytes, digest: str, origin: object | None = None) -> str:
        """Store verified bytes and return a new opaque capability URI."""

        if not isinstance(content, bytes) or not content:
            raise ValueError("artifact content is malformed")
        size = len(content)
        if size > self._max_artifact_bytes or size > self._max_total_bytes:
            raise ValueError("artifact exceeds the store byte budget")
        with self._lock:
            now = float(self._clock())
            self._purge_expired(now)
            while self._entries and (
                len(self._entries) >= self._max_artifacts
                or self._total_bytes + size > self._max_total_bytes
            ):
                _, evicted = self._entries.popitem(last=False)
                self._total_bytes -= evicted.size_bytes
            token = self._new_token()
            self._entries[token] = _StoredArtifact(
                content=content,
                digest=digest,
                created_at=now,
                size_bytes=size,
                origin=origin,
            )
            self._total_bytes += size
        return self._uri_template.format(token=token)

    def read(self, token: str) -> bytes:
        """Read exact bytes for one live capability while refreshing its LRU position."""

        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ArtifactUnavailableError("artifact is unavailable")
        with self._lock:
            now = float(self._clock())
            self._purge_expired(now)
            entry = self._entries.get(token)
            if entry is None:
                raise ArtifactUnavailableError("artifact is unavailable")
            content = entry.content
            digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
            if (
                digest != entry.digest
                or len(content) != entry.size_bytes
                or not self._verify_entry(entry.origin, content, entry.digest)
            ):
                self._entries.pop(token)
                self._total_bytes -= entry.size_bytes
                raise ArtifactUnavailableError("artifact is unavailable")
            self._entries.move_to_end(token)
            return content


__all__ = [
    "ArtifactUnavailableError",
    "BoundedArtifactStore",
]
