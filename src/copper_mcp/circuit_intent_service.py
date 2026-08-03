"""Protocol-independent Circuit Intent validation and schematic build service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from copper_mcp.adapters.kicad_schematic import KiCadSchematicArtifact, render_kicad_schematic
from copper_mcp.circuit_ir import (
    CIRCUIT_INTENT_SCHEMA,
    CIRCUIT_INTENT_SCHEMA_VERSION,
    CircuitIntentSnapshot,
    CircuitParseLimits,
    decode_snapshot_json,
    snapshot_from_content,
)

CIRCUIT_SCHEMATIC_BUILD_SCHEMA = "copper.circuit-schematic-build"
CIRCUIT_SCHEMATIC_BUILD_SCHEMA_VERSION = "0.1.0"
KICAD_SCHEMATIC_MIME_TYPE = "application/x-kicad-schematic"


@dataclass(frozen=True, slots=True)
class CircuitSchematicBuild:
    """One redacted build result plus its private immutable artifact bytes."""

    artifact: KiCadSchematicArtifact

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, KiCadSchematicArtifact):
            raise ValueError("circuit schematic build requires a verified artifact")

    def to_dict(self) -> dict[str, Any]:
        """Serialize metadata without circuit names, values, references, or bytes."""

        artifact = self.artifact
        return {
            "schema": CIRCUIT_SCHEMATIC_BUILD_SCHEMA,
            "schema_version": CIRCUIT_SCHEMATIC_BUILD_SCHEMA_VERSION,
            "status": "rendered",
            "intent": {
                "schema": CIRCUIT_INTENT_SCHEMA,
                "schema_version": CIRCUIT_INTENT_SCHEMA_VERSION,
                "intent_digest": artifact.intent_digest,
                "counts": {
                    "components": artifact.component_count,
                    "nets": artifact.net_count,
                    "ports": artifact.port_count,
                },
            },
            "artifact": {
                "kind": "kicad_schematic",
                "mime_type": KICAD_SCHEMATIC_MIME_TYPE,
                "format_version": artifact.format_version,
                "artifact_digest": artifact.artifact_digest,
                "intent_digest": artifact.intent_digest,
                "size_bytes": len(artifact.content),
            },
            "verification": {
                "intent_topology": "passed",
                "artifact_digest": "passed",
                "provenance_binding": "passed",
                "deterministic_replay": "passed",
                "kicad_cli_parse": "not_run",
                "erc": "not_run",
                "schematic_board_parity": "not_run",
                "electrical_validation": "not_run",
                "board_ready": False,
            },
        }


def _build(snapshot: CircuitIntentSnapshot) -> CircuitSchematicBuild:
    first = render_kicad_schematic(snapshot)
    replay = render_kicad_schematic(snapshot)
    if first != replay:
        raise RuntimeError("deterministic schematic replay did not match")
    return CircuitSchematicBuild(artifact=first)


def build_schematic_from_snapshot_json(
    payload: bytes,
    limits: CircuitParseLimits | None = None,
) -> CircuitSchematicBuild:
    """Build from one strict, self-verifying Circuit Intent JSON snapshot."""

    return _build(decode_snapshot_json(payload, limits))


def build_schematic_from_content(
    content: Any,
    limits: CircuitParseLimits | None = None,
) -> CircuitSchematicBuild:
    """Build from structured Circuit Intent content and create its digest internally."""

    return _build(snapshot_from_content(content, limits))


__all__ = [
    "CIRCUIT_SCHEMATIC_BUILD_SCHEMA",
    "CIRCUIT_SCHEMATIC_BUILD_SCHEMA_VERSION",
    "KICAD_SCHEMATIC_MIME_TYPE",
    "CircuitSchematicBuild",
    "build_schematic_from_content",
    "build_schematic_from_snapshot_json",
]
