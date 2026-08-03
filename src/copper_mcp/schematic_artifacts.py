"""Bounded process-local capability store for private schematic resources."""

from __future__ import annotations

import time
from collections.abc import Callable

from copper_mcp.adapters.kicad_schematic import (
    MAX_RENDERED_SCHEMATIC_BYTES,
    KiCadSchematicArtifact,
)
from copper_mcp.artifact_store import ArtifactUnavailableError, BoundedArtifactStore

MAX_SCHEMATIC_ARTIFACTS = 16
MAX_SCHEMATIC_ARTIFACT_STORE_BYTES = 16 * 1024 * 1024
SCHEMATIC_ARTIFACT_TTL_SECONDS = 15 * 60
SCHEMATIC_ARTIFACT_URI_TEMPLATE = "pcb://artifacts/schematic/{token}/circuit.kicad_sch"


class SchematicArtifactUnavailableError(ArtifactUnavailableError):
    """Raised uniformly for invalid, expired, evicted, or unknown capabilities."""


class SchematicArtifactStore(BoundedArtifactStore):
    """Thread-safe, non-enumerable, TTL and LRU bounded in-memory artifact store."""

    def __init__(
        self,
        max_artifacts: int = MAX_SCHEMATIC_ARTIFACTS,
        max_total_bytes: int = MAX_SCHEMATIC_ARTIFACT_STORE_BYTES,
        ttl_seconds: int = SCHEMATIC_ARTIFACT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        try:
            super().__init__(
                uri_template=SCHEMATIC_ARTIFACT_URI_TEMPLATE,
                max_artifacts=max_artifacts,
                max_total_bytes=max_total_bytes,
                ttl_seconds=ttl_seconds,
                max_artifact_bytes=MAX_RENDERED_SCHEMATIC_BYTES,
                ceilings=(
                    MAX_SCHEMATIC_ARTIFACTS,
                    MAX_SCHEMATIC_ARTIFACT_STORE_BYTES,
                    SCHEMATIC_ARTIFACT_TTL_SECONDS,
                ),
                clock=clock,
                token_factory=token_factory,
            )
        except ValueError as error:
            raise ValueError(
                "schematic artifact store limits must be positive and tighten-only"
            ) from error

    def put(self, artifact: KiCadSchematicArtifact) -> str:
        """Store one verified artifact and return a new opaque capability URI."""

        if not isinstance(artifact, KiCadSchematicArtifact):
            raise ValueError("schematic artifact is malformed")
        size = len(artifact.content)
        if size > MAX_RENDERED_SCHEMATIC_BYTES:
            raise ValueError("schematic artifact exceeds the store byte budget")
        try:
            return self._store(artifact.content, artifact.artifact_digest, origin=artifact)
        except ValueError as error:
            raise ValueError("schematic artifact exceeds the store byte budget") from error

    def _verify_entry(self, origin: object | None, content: bytes, digest: str) -> bool:
        """Re-check the retained artifact, not just the stored bytes.

        The stored bytes and the artifact object are separate references. Comparing them on
        every read is what catches an artifact mutated after it was handed to the store.
        """

        return (
            isinstance(origin, KiCadSchematicArtifact)
            and origin.content == content
            and origin.artifact_digest == digest
        )

    def read(self, token: str) -> bytes:
        try:
            return super().read(token)
        except ArtifactUnavailableError as error:
            raise SchematicArtifactUnavailableError("schematic artifact is unavailable") from error


__all__ = [
    "MAX_SCHEMATIC_ARTIFACTS",
    "MAX_SCHEMATIC_ARTIFACT_STORE_BYTES",
    "SCHEMATIC_ARTIFACT_TTL_SECONDS",
    "SCHEMATIC_ARTIFACT_URI_TEMPLATE",
    "SchematicArtifactStore",
    "SchematicArtifactUnavailableError",
]
