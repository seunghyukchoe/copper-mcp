"""Private source-preserving copper removal for one bounded optimization repair batch."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from pydantic import model_validator

from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.adapters.cst import CstError, Splice, apply_splices, span
from copper_mcp.adapters.kicad_route_patch import (
    KiCadRoutePatchError,
    _require_native_geometry_identities,
    _source_structure,
)
from copper_mcp.adapters.sexpr import SExpr, atoms, children
from copper_mcp.board_ir import BoardIRSnapshot
from copper_mcp.config import Settings
from copper_mcp.optimization.contracts import ClosedModel, Counter, Digest, digest_document
from copper_mcp.parse_budgets import parse_limits_for

if TYPE_CHECKING:
    from copper_mcp.optimization.evaluation_v2 import SlotProbe
    from copper_mcp.optimization.inputs import PreparedOptimization


class CopperRepairBinding(ClosedModel):
    identity_namespace = "copper-mcp/optimization/v2/copper-repair"
    policy: Literal["moved-or-disconnected-target-copper-reset/v1"] = (
        "moved-or-disconnected-target-copper-reset/v1"
    )
    base_snapshot_digest: Digest
    input_board_revision: Digest
    input_snapshot_digest: Digest
    output_board_revision: Digest
    output_snapshot_digest: Digest
    target_scope_digest: Digest
    target_net_count: Counter
    removed_ids_digest: Digest
    removed_segments: Counter
    removed_vias: Counter
    removed_arcs: Counter

    @model_validator(mode="after")
    def nonempty_repair(self) -> CopperRepairBinding:
        if (
            self.target_net_count < 1
            or self.removed_segments + self.removed_vias + self.removed_arcs < 1
        ):
            raise ValueError("copper repair must remove declared target copper")
        if self.input_board_revision == self.output_board_revision:
            raise ValueError("copper repair must change its private derivative")
        return self


@dataclass(frozen=True)
class PrivateCopperRepair:
    source: bytes
    snapshot: BoardIRSnapshot
    binding: CopperRepairBinding


def remove_target_copper(
    prepared: PreparedOptimization,
    source: bytes,
    snapshot: BoardIRSnapshot,
    target_nets: tuple[str, ...],
    base_snapshot_digest: str,
    settings: Settings,
    probe: SlotProbe,
) -> PrivateCopperRepair:
    """Remove only an internally selected batch; publication still requires complete rerouting.

    Locked copper and grouped copper refuse. Every other source expression stays byte-identical,
    and the modeled round trip may differ only by these removals and the resulting source digest.
    """
    from copper_mcp.optimization.lifecycle import ResourceUsage
    from copper_mcp.optimization.worker import OptimizationExecutionError

    if (
        prepared.request.schema_version != "optimization/v2"
        or not target_nets
        or tuple(sorted(set(target_nets))) != target_nets
        or not set(target_nets) <= set(prepared.target_net_refs)
    ):
        raise OptimizationExecutionError("invalid_candidate")
    content = snapshot.content
    groups = (content.segments, content.vias, content.arcs)
    probe.reserve(
        ResourceUsage(repair_rounds=1, obstacle_checks=sum(map(len, groups)) + len(target_nets))
    )
    selected = tuple(item for group in groups for item in group if item.net_id in target_nets)
    if not selected:
        raise OptimizationExecutionError("invalid_candidate")
    if any(item.locked for item in selected):
        raise OptimizationExecutionError("unsupported_geometry")
    removed = frozenset(item.id for item in selected)
    native_ids = frozenset(value.partition(":kicad:")[2].lower() for value in removed)
    if "" in native_ids or len(native_ids) != len(removed):
        raise OptimizationExecutionError("unsupported_geometry")
    limits = parse_limits_for(settings)
    parsed = parse_kicad_bytes(source, prepared.profile, limits)
    if parsed.snapshot != snapshot or parsed.diagnostics:
        raise OptimizationExecutionError("invalid_candidate")
    failed = False
    try:
        _require_native_geometry_identities(snapshot)
        root, _ = _source_structure(source, limits)
        pending = [root]
        while pending:
            probe.checkpoint()
            expression = pending.pop()
            if expression.head == "group":
                for members in children(expression, "members"):
                    if native_ids.intersection(value.lower() for value in atoms(members)):
                        raise OptimizationExecutionError("unsupported_geometry")
            pending.extend(child for child in expression.items[1:] if isinstance(child, SExpr))
        text = source.decode("utf-8")
        edits: list[Splice] = []
        found: set[str] = set()
        by_native = {value.lower(): value for value in removed}
        for node in root.items[1:]:
            probe.checkpoint()
            if not isinstance(node, SExpr) or node.head not in {"segment", "via", "arc"}:
                continue
            identity_nodes = (*children(node, "uuid"), *children(node, "tstamp"))
            if len(identity_nodes) != 1 or len(atoms(identity_nodes[0])) != 1:
                raise OptimizationExecutionError("unsupported_geometry")
            identity = by_native.get(f"{node.head}:kicad:{atoms(identity_nodes[0])[0]}".lower())
            if identity is not None:
                if identity in found:
                    raise OptimizationExecutionError("invalid_candidate")
                found.add(identity)
                start, end = span(node, text)
                edits.append(Splice(start, end, ""))
        if found != removed:
            raise OptimizationExecutionError("unsupported_geometry")
        result = apply_splices(text, edits).encode("utf-8")
    except (CstError, KiCadRoutePatchError, UnicodeError):
        failed = True
    if failed:
        raise OptimizationExecutionError("invalid_candidate")
    probe.checkpoint()
    converted = parse_kicad_bytes(result, prepared.profile, limits)
    if converted.snapshot is None or converted.diagnostics:
        raise OptimizationExecutionError("invalid_candidate")
    expected = replace(
        content,
        source=replace(content.source, revision=converted.snapshot.content.source.revision),
        segments=tuple(item for item in content.segments if item.id not in removed),
        vias=tuple(item for item in content.vias if item.id not in removed),
        arcs=tuple(item for item in content.arcs if item.id not in removed),
    )
    if converted.snapshot.content != expected:
        raise OptimizationExecutionError("invalid_candidate")
    probe.checkpoint()
    return PrivateCopperRepair(
        result,
        converted.snapshot,
        CopperRepairBinding(
            base_snapshot_digest=base_snapshot_digest,
            input_board_revision=content.source.revision,
            input_snapshot_digest=snapshot.snapshot_digest,
            output_board_revision=converted.snapshot.content.source.revision,
            output_snapshot_digest=converted.snapshot.snapshot_digest,
            target_scope_digest=digest_document("optimization-repair-targets/v1", target_nets),
            target_net_count=len(target_nets),
            removed_ids_digest=digest_document("optimization-removed-copper/v1", sorted(removed)),
            removed_segments=sum(item.id in removed for item in content.segments),
            removed_vias=sum(item.id in removed for item in content.vias),
            removed_arcs=sum(item.id in removed for item in content.arcs),
        ),
    )
