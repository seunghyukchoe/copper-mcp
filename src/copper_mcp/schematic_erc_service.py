"""Authoritative schematic ERC and KiCad round-trip verification for Circuit Intent.

This service owns the seam that [ADR-0056](../../docs/adr/0056-kicad-schematic-parity.md) left
open: the pure parity verifier proves that a KiCad-exported netlist matches the source intent, but
something has to actually run KiCad to produce that netlist, and nothing did. It also closes the
deferral recorded in [ADR-0015](../../docs/adr/0015-bounded-circuit-schematic-delivery.md), which
held authoritative ERC back until its schematic subprocess and report contract were reviewed.

The division of labour is deliberate and load-bearing:

* KiCad decides what an electrical rule violation is. CopperMCP never reimplements ERC semantics,
  never reclassifies a severity, and never suppresses a finding.
* CopperMCP decides what is *claimed*. A successful ERC run is evidence that KiCad checked these
  exact bytes and returned this exact verdict — nothing more. In particular a warning-only report
  is reported as ``passed`` (no hard errors) and ``clean: false`` (findings exist), because the
  bounded passive fixture genuinely produces KiCad warnings and relabelling them would be a lie.

Everything crossing the boundary is a digest, a count, or a fixed literal. No net name, component
reference, value, coordinate, UUID, or KiCad description text is returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from copper_mcp.adapters.kicad_schematic import KiCadSchematicArtifact, render_kicad_schematic
from copper_mcp.adapters.kicad_schematic_parity import (
    KiCadSchematicParityEvidence,
    verify_kicad_schematic_parity,
)
from copper_mcp.circuit_intent_service import KICAD_SCHEMATIC_MIME_TYPE
from copper_mcp.circuit_ir import (
    CIRCUIT_INTENT_SCHEMA,
    CIRCUIT_INTENT_SCHEMA_VERSION,
    CircuitIntentSnapshot,
    CircuitParseLimits,
    decode_snapshot_json,
    snapshot_from_content,
)
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    export_circuit_schematic_netlist,
    run_circuit_schematic_erc,
)
from copper_mcp.models import ErcSummary

CIRCUIT_SCHEMATIC_ERC_SCHEMA = "copper.circuit-schematic-erc"
CIRCUIT_SCHEMATIC_ERC_SCHEMA_VERSION = "0.1.0"
ERC_AUTHORITY = "kicad-cli-sch-erc"
ROUND_TRIP_AUTHORITY = "kicad-cli-sch-export-netlist"


@dataclass(frozen=True, slots=True)
class CircuitSchematicErcResult:
    """One redacted authoritative-ERC and round-trip result bound to its exact inputs."""

    artifact: KiCadSchematicArtifact
    erc: ErcSummary
    parity: KiCadSchematicParityEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, KiCadSchematicArtifact):
            raise ValueError("schematic ERC result requires a verified artifact")
        if not isinstance(self.erc, ErcSummary):
            raise ValueError("schematic ERC result requires an authoritative ERC summary")
        if not isinstance(self.parity, KiCadSchematicParityEvidence):
            raise ValueError("schematic ERC result requires round-trip parity evidence")
        # Evidence that is not bound to the artifact it describes is evidence about nothing.
        for name, digest in (
            ("ERC intent", self.erc.intent_digest),
            ("round-trip intent", self.parity.intent_digest),
        ):
            if digest != self.artifact.intent_digest:
                raise ValueError(f"{name} digest is not bound to the rendered artifact")
        for name, digest in (
            ("ERC schematic", self.erc.schematic_digest),
            ("round-trip schematic", self.parity.schematic_digest),
        ):
            if digest != self.artifact.artifact_digest:
                raise ValueError(f"{name} digest is not bound to the rendered artifact")

    def to_dict(self) -> dict[str, Any]:
        """Serialize digests, counts, and fixed literals only.

        The exported netlist's own digest is deliberately absent: KiCad stamps the export with a
        wall-clock date and the private snapshot path, so it is not reproducible and would look
        like a stable identity while behaving like a nonce.
        """

        artifact = self.artifact
        erc = self.erc
        parity = self.parity
        return {
            "schema": CIRCUIT_SCHEMATIC_ERC_SCHEMA,
            "schema_version": CIRCUIT_SCHEMATIC_ERC_SCHEMA_VERSION,
            "status": "checked",
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
            "schematic": {
                "kind": "kicad_schematic",
                "mime_type": KICAD_SCHEMATIC_MIME_TYPE,
                "format_version": artifact.format_version,
                "artifact_digest": artifact.artifact_digest,
                "intent_digest": artifact.intent_digest,
                "size_bytes": len(artifact.content),
            },
            "erc": {
                "authority": ERC_AUTHORITY,
                "kicad_version": erc.kicad_version,
                "erc_schema": erc.erc_schema,
                "coordinate_units": erc.coordinate_units,
                "counts": {
                    "errors": erc.error_count,
                    "warnings": erc.warning_count,
                    "exclusions": erc.exclusion_count,
                    "ignored_checks": erc.ignored_check_count,
                    "sheets": erc.sheet_count,
                },
                "violation_type_counts": dict(erc.violation_type_counts),
                "passed": erc.passed,
                "clean": erc.clean,
            },
            "round_trip": {
                "authority": ROUND_TRIP_AUTHORITY,
                "netlist_format_version": parity.netlist_format_version,
                "counts": {
                    "components": parity.component_count,
                    "nets": parity.net_count,
                    "connections": parity.connection_count,
                },
                "source_replay": parity.source_replay,
                "component_parity": parity.component_parity,
                "connectivity_parity": parity.connectivity_parity,
            },
            "verification": {
                "intent_topology": "passed",
                "artifact_digest": "passed",
                "provenance_binding": "passed",
                "deterministic_replay": "passed",
                # KiCad cannot run ERC on a schematic it failed to load, so an accepted ERC
                # report is itself the parse evidence ADR-0015 listed as not_run.
                "kicad_cli_parse": "passed",
                "erc": "completed",
                "schematic_round_trip": "passed",
                # Explicit non-claims. Source-to-board parity needs a board to compare against
                # and is not modelled by any surface in this release.
                "schematic_board_parity": "not_run",
                "electrical_validation": "not_run",
                "board_ready": False,
            },
        }


def _verify(snapshot: CircuitIntentSnapshot, settings: Settings) -> CircuitSchematicErcResult:
    first = render_kicad_schematic(snapshot)
    replay = render_kicad_schematic(snapshot)
    if first != replay:
        raise RuntimeError("deterministic schematic replay did not match")
    erc = run_circuit_schematic_erc(
        first.content,
        intent_digest=first.intent_digest,
        schematic_digest=first.artifact_digest,
        settings=settings,
    )
    netlist = export_circuit_schematic_netlist(first.content, settings=settings)
    parity = verify_kicad_schematic_parity(snapshot, first.content, netlist)
    return CircuitSchematicErcResult(artifact=first, erc=erc, parity=parity)


def verify_schematic_erc_from_snapshot_json(
    payload: bytes,
    settings: Settings,
    limits: CircuitParseLimits | None = None,
) -> CircuitSchematicErcResult:
    """Verify from one strict, self-verifying Circuit Intent JSON snapshot."""

    return _verify(decode_snapshot_json(payload, limits), settings)


def verify_schematic_erc_from_content(
    content: Any,
    settings: Settings,
    limits: CircuitParseLimits | None = None,
) -> CircuitSchematicErcResult:
    """Verify from structured Circuit Intent content, creating its digest internally."""

    return _verify(snapshot_from_content(content, limits), settings)


__all__ = [
    "CIRCUIT_SCHEMATIC_ERC_SCHEMA",
    "CIRCUIT_SCHEMATIC_ERC_SCHEMA_VERSION",
    "ERC_AUTHORITY",
    "ROUND_TRIP_AUTHORITY",
    "CircuitSchematicErcResult",
    "verify_schematic_erc_from_content",
    "verify_schematic_erc_from_snapshot_json",
]
