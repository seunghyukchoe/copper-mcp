"""Authoritative source-to-board connectivity parity for Circuit Intent.

This closes the leg [ADR-0071](../../docs/adr/0071-authoritative-schematic-erc.md) left open. That
slice proved a generated schematic round-trips through KiCad and passes authoritative ERC, then
reported ``schematic_board_parity`` as ``not_run`` because there was no board to compare against
and, it recorded, because a board-side verdict "requires a project, not standalone mode".

The project requirement turned out not to exist for the CLI: ``JobExportDrc`` derives the schematic
by swapping the board filename's extension, and the project load beneath it is guarded by an
existence check. What does exist are four ways to get a *silent* false pass, documented in
[ADR-0076](../../docs/adr/0076-authoritative-source-to-board-parity.md) and its
[research note](../../docs/research/source-to-board-parity-v1.md). Three are KiCad's — an unfetched
netlist degrades to an empty array with exit 0, exit codes OR three providers together, and parity
findings are warning-severity so a narrowed severity set empties the array. The fourth is ours:
CopperMCP's delivered schematic marks every symbol ``on_board no``, which makes KiCad's board-side
netlist empty and renders a correct board indistinguishable from a wrong one.

So this service checks the board against a **board-eligible projection** of the same intent, and
reports both digests rather than hiding the distinction. The claim is scoped to match: the board
implements *the Circuit Intent's connectivity*. It is never a claim that the delivered schematic
file matches the board -- that file is board-excluded and could not support one.

Everything crossing the boundary is a digest, a count, or a fixed literal. Parity descriptions
embed net names verbatim and affected items carry UUIDs and coordinates; none of it is returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from copper_mcp.adapters.kicad_schematic import KiCadSchematicArtifact, render_kicad_schematic
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
from copper_mcp.kicad_cli import SourceToBoardParityEvidence, run_source_to_board_parity

SOURCE_TO_BOARD_PARITY_SCHEMA = "copper.source-to-board-parity"
SOURCE_TO_BOARD_PARITY_SCHEMA_VERSION = "0.1.0"
PARITY_AUTHORITY = "kicad-cli-pcb-drc-schematic-parity"


@dataclass(frozen=True, slots=True)
class SourceToBoardParityResult:
    """One redacted parity verdict bound to its intent, both derivatives, and the board."""

    artifact: KiCadSchematicArtifact
    projection: KiCadSchematicArtifact
    parity: SourceToBoardParityEvidence

    def __post_init__(self) -> None:
        for name, value in (("artifact", self.artifact), ("projection", self.projection)):
            if not isinstance(value, KiCadSchematicArtifact):
                raise ValueError(f"source-to-board parity result requires a verified {name}")
        if not isinstance(self.parity, SourceToBoardParityEvidence):
            raise ValueError("source-to-board parity result requires authoritative evidence")
        # Two derivatives of one intent. If they disagree about which intent, neither is evidence.
        if self.artifact.intent_digest != self.projection.intent_digest:
            raise ValueError("parity projection is not derived from the delivered intent")
        if self.artifact.artifact_digest == self.projection.artifact_digest:
            raise ValueError("parity projection must differ from the delivered schematic")
        for name, expected, actual in (
            ("intent", self.artifact.intent_digest, self.parity.intent_digest),
            ("schematic", self.artifact.artifact_digest, self.parity.schematic_digest),
            (
                "projection",
                self.projection.artifact_digest,
                self.parity.parity_schematic_digest,
            ),
        ):
            if expected != actual:
                raise ValueError(f"parity {name} digest is not bound to the rendered artifacts")
        if self.parity.component_count != self.artifact.component_count:
            raise ValueError("parity component count is not bound to the rendered artifact")
        for name, count in (
            ("component", self.projection.component_count),
            ("net", self.projection.net_count),
            ("port", self.projection.port_count),
        ):
            expected_count = getattr(self.artifact, f"{name}_count")
            if count != expected_count:
                raise ValueError(f"parity projection {name} count diverges from the delivered one")

    def to_dict(self) -> dict[str, Any]:
        """Serialize digests, counts, and fixed literals only."""

        artifact = self.artifact
        parity = self.parity
        return {
            "schema": SOURCE_TO_BOARD_PARITY_SCHEMA,
            "schema_version": SOURCE_TO_BOARD_PARITY_SCHEMA_VERSION,
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
            "parity_projection": {
                # The bytes KiCad actually compared the board against. Not a delivered artifact:
                # it exists only so the board has a board-eligible counterpart to be checked
                # against, and it differs from the delivered schematic solely in board eligibility.
                "kind": "kicad_schematic_board_projection",
                "artifact_digest": self.projection.artifact_digest,
                "intent_digest": self.projection.intent_digest,
                "size_bytes": len(self.projection.content),
                "differs_from_schematic_by": "board_eligibility",
            },
            "board": {
                "board_revision": parity.board_revision,
            },
            "parity": {
                "authority": PARITY_AUTHORITY,
                "kicad_version": parity.kicad_version,
                "drc_schema": parity.drc_schema,
                "coordinate_units": parity.coordinate_units,
                "counts": {
                    "components": parity.component_count,
                    "connectivity_findings": parity.connectivity_finding_count,
                    "projection_findings": parity.projection_finding_count,
                },
                "parity_type_counts": dict(parity.parity_type_counts),
                "oracle_live": parity.oracle_live,
                "passed": parity.passed,
            },
            "verification": {
                "intent_topology": "passed",
                "artifact_digest": "passed",
                "provenance_binding": "passed",
                "deterministic_replay": "passed",
                # KiCad cannot run parity against a schematic it failed to load, and the liveness
                # invariant proves it loaded this one.
                "kicad_cli_parse": "passed",
                "parity_oracle_live": parity.oracle_live,
                "schematic_board_parity": "passed" if parity.passed else "failed",
                # Explicit non-claims. Connectivity parity is not electrical validation, and a
                # board that matches its source may still be unmanufacturable.
                "erc": "not_run",
                "footprint_correctness": "not_run",
                "electrical_validation": "not_run",
                "board_ready": False,
            },
        }


def _verify(
    snapshot: CircuitIntentSnapshot,
    board_path: str,
    settings: Settings,
) -> SourceToBoardParityResult:
    artifact = render_kicad_schematic(snapshot)
    replay = render_kicad_schematic(snapshot)
    if artifact != replay:
        raise RuntimeError("deterministic schematic replay did not match")
    projection = render_kicad_schematic(snapshot, board_eligible=True)
    projection_replay = render_kicad_schematic(snapshot, board_eligible=True)
    if projection != projection_replay:
        raise RuntimeError("deterministic parity projection replay did not match")
    parity = run_source_to_board_parity(
        board_path,
        projection.content,
        component_count=artifact.component_count,
        intent_digest=artifact.intent_digest,
        schematic_digest=artifact.artifact_digest,
        parity_schematic_digest=projection.artifact_digest,
        settings=settings,
    )
    return SourceToBoardParityResult(artifact=artifact, projection=projection, parity=parity)


def verify_source_to_board_parity_from_snapshot_json(
    payload: bytes,
    board_path: str,
    settings: Settings,
    limits: CircuitParseLimits | None = None,
) -> SourceToBoardParityResult:
    """Verify from one strict, self-verifying Circuit Intent JSON snapshot."""

    return _verify(decode_snapshot_json(payload, limits), board_path, settings)


def verify_source_to_board_parity_from_content(
    content: Any,
    board_path: str,
    settings: Settings,
    limits: CircuitParseLimits | None = None,
) -> SourceToBoardParityResult:
    """Verify from structured Circuit Intent content, creating its digest internally."""

    return _verify(snapshot_from_content(content, limits), board_path, settings)


__all__ = [
    "PARITY_AUTHORITY",
    "SOURCE_TO_BOARD_PARITY_SCHEMA",
    "SOURCE_TO_BOARD_PARITY_SCHEMA_VERSION",
    "SourceToBoardParityResult",
    "verify_source_to_board_parity_from_content",
    "verify_source_to_board_parity_from_snapshot_json",
]
