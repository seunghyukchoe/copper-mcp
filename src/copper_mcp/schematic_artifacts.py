"""Bounded process-local capability store for private schematic resources."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from copper_mcp.adapters.kicad_schematic import (
    MAX_RENDERED_SCHEMATIC_BYTES,
    KiCadSchematicArtifact,
)

MAX_SCHEMATIC_ARTIFACTS = 16
MAX_SCHEMATIC_ARTIFACT_STORE_BYTES = 16 * 1024 * 1024
SCHEMATIC_ARTIFACT_TTL_SECONDS = 15 * 60
SCHEMATIC_ARTIFACT_URI_TEMPLATE = (
    "pcb://artifacts/schematic/{token}/circuit.kicad_sch"
)

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class SchematicArtifactUnavailableError(ValueError):
    """Raised uniformly for invalid, expired, evicted, or unknown capabilities."""


@dataclass(frozen=True, slots=True)
class _StoredArtifact:
    artifact: KiCadSchematicArtifact
    content: bytes
    artifact_digest: str
    created_at: float
    size_bytes: int


class SchematicArtifactStore:
    """Thread-safe, non-enumerable, TTL and LRU bounded in-memory artifact store."""

    def __init__(
        self,
        max_artifacts: int = MAX_SCHEMATIC_ARTIFACTS,
        max_total_bytes: int = MAX_SCHEMATIC_ARTIFACT_STORE_BYTES,
        ttl_seconds: int = SCHEMATIC_ARTIFACT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        limits = (
            (max_artifacts, MAX_SCHEMATIC_ARTIFACTS),
            (max_total_bytes, MAX_SCHEMATIC_ARTIFACT_STORE_BYTES),
            (ttl_seconds, SCHEMATIC_ARTIFACT_TTL_SECONDS),
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 1
            or value > ceiling
            for value, ceiling in limits
        ):
            raise ValueError("schematic artifact store limits must be positive and tighten-only")
        if not callable(clock) or (token_factory is not None and not callable(token_factory)):
            raise ValueError("schematic artifact store providers must be callable")
        self._max_artifacts = max_artifacts
        self._max_total_bytes = max_total_bytes
        self._ttl_seconds = ttl_seconds
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
        raise RuntimeError("schematic artifact capability generation failed")

    def put(self, artifact: KiCadSchematicArtifact) -> str:
        """Store one verified artifact and return a new opaque capability URI."""

        if not isinstance(artifact, KiCadSchematicArtifact):
            raise ValueError("schematic artifact is malformed")
        size = len(artifact.content)
        if size > MAX_RENDERED_SCHEMATIC_BYTES or size > self._max_total_bytes:
            raise ValueError("schematic artifact exceeds the store byte budget")
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
                artifact=artifact,
                content=artifact.content,
                artifact_digest=artifact.artifact_digest,
                created_at=now,
                size_bytes=size,
            )
            self._total_bytes += size
        return SCHEMATIC_ARTIFACT_URI_TEMPLATE.format(token=token)

    def read(self, token: str) -> bytes:
        """Read exact bytes for one live capability while refreshing its LRU position."""

        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise SchematicArtifactUnavailableError("schematic artifact is unavailable")
        with self._lock:
            now = float(self._clock())
            self._purge_expired(now)
            entry = self._entries.get(token)
            if entry is None:
                raise SchematicArtifactUnavailableError("schematic artifact is unavailable")
            content = entry.content
            digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
            if (
                entry.artifact.content != content
                or entry.artifact.artifact_digest != entry.artifact_digest
                or digest != entry.artifact_digest
                or len(content) != entry.size_bytes
            ):
                self._entries.pop(token)
                self._total_bytes -= entry.size_bytes
                raise SchematicArtifactUnavailableError("schematic artifact is unavailable")
            self._entries.move_to_end(token)
            return content


__all__ = [
    "MAX_SCHEMATIC_ARTIFACTS",
    "MAX_SCHEMATIC_ARTIFACT_STORE_BYTES",
    "SCHEMATIC_ARTIFACT_TTL_SECONDS",
    "SCHEMATIC_ARTIFACT_URI_TEMPLATE",
    "SchematicArtifactStore",
    "SchematicArtifactUnavailableError",
]
