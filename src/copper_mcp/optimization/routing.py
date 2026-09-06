"""Candidate-only composition over existing single-layer and ordered-layer routers."""

from __future__ import annotations

from dataclasses import dataclass, replace

from copper_mcp.adapters import (
    parse_kicad_bytes,
    render_kicad_candidate_board,
    render_kicad_layered_candidate_board,
)
from copper_mcp.board_ir import BoardIRSnapshot
from copper_mcp.config import Settings
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.optimization.inputs import PreparedOptimization
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.worker import OptimizationExecutionError, OptimizationExecutionProbe
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.routing import (
    AStarRouter,
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteRequest,
)
from copper_mcp.routing.contracts import AStarSettings, RouteRequest


@dataclass(frozen=True)
class PrivateRouteComposition:
    source: bytes
    snapshot: BoardIRSnapshot
    base_snapshot_digest: str
    candidate_ids: tuple[str, ...]
    connected_targets: tuple[str, ...]
    wire_length_nm: int
    vias: int
    route_probes: int

    @property
    def digest(self) -> str:
        return digest_document(
            "optimization-route-composition/v1",
            {
                "base": self.base_snapshot_digest,
                "result": self.snapshot.snapshot_digest,
                "candidates": self.candidate_ids,
                "targets": self.connected_targets,
            },
        )


def _reserve_search(
    prepared: PreparedOptimization, probe: OptimizationExecutionProbe
) -> AStarSettings:
    """Reserve both proposal and serializer replay before either executes."""

    usage = probe.checkpoint().usage
    limits = prepared.request.limits
    expansions = min(
        prepared.routing_settings.max_expansions, (limits.max_expansions - usage.expansions) // 2
    )
    checks = min(
        prepared.routing_settings.max_obstacle_checks,
        (limits.max_obstacle_checks - usage.obstacle_checks) // 2,
    )
    if expansions < 1 or checks < 1:
        raise OptimizationExecutionError("budget_exhausted")
    probe.reserve(ResourceUsage(expansions=2 * expansions, obstacle_checks=2 * checks))
    return replace(prepared.routing_settings, max_expansions=expansions, max_obstacle_checks=checks)


def route_targets(
    prepared: PreparedOptimization,
    source: bytes,
    snapshot: BoardIRSnapshot,
    settings: Settings,
    probe: OptimizationExecutionProbe,
) -> PrivateRouteComposition:
    """Route every explicit target in a private derivative or withhold the entire composition.

    Each successful edit is replayed, serialized, and reparsed before the next net sees it.
    Already-connected nets count as connected without manufacturing an empty copper candidate.
    Multi-pin nets require a common supported layer; cross-layer multi-pin search remains a
    refusal until its tree composer can preserve all pad connectivity.
    """

    base = snapshot.snapshot_digest
    candidate_ids: list[str] = []
    connected: list[str] = []
    length = vias = 0
    probes = 0
    limits = parse_limits_for(settings)
    for index, net in enumerate(prepared.target_net_refs):
        probe.checkpoint()
        pads = tuple(pad for pad in snapshot.content.pads if pad.net_id == net)
        if not 2 <= len(pads) <= 32:
            raise OptimizationExecutionError("unsupported_geometry")
        layers = tuple(sorted(snapshot.content.copper_layers, key=lambda layer: layer.index))
        common = tuple(
            layer.id
            for layer in layers
            if layer.kind == "signal" and all(layer.id in pad.layer_ids for pad in pads)
        )
        routed_source: bytes | None = None
        for layer in common:
            routing_settings = _reserve_search(prepared, probe)
            probes += 1
            result = AStarRouter().propose(
                snapshot,
                RouteRequest(
                    snapshot.snapshot_digest,
                    net,
                    layer,
                    prepared.request.seed + index,
                    routing_settings,
                ),
                cancelled=probe.cancelled,
            )
            probe.checkpoint()
            if result.connected is not None:
                routed_source = source
                break
            if result.candidate is None:
                continue
            routed_source = render_kicad_candidate_board(
                source, snapshot, result.candidate, prepared.profile, limits=limits
            )
            candidate_ids.append(result.candidate.candidate_id)
            length += result.candidate.metrics.wire_length_nm
            vias += result.candidate.metrics.vias
            break
        if routed_source is None and len(pads) == 2:
            routing_settings = _reserve_search(prepared, probe)
            accesses = [
                tuple(
                    layer.id
                    for layer in layers
                    if layer.kind == "signal" and layer.id in pad.layer_ids
                )
                for pad in pads
            ]
            if not all(accesses):
                raise OptimizationExecutionError("unsupported_geometry")
            request = LayeredRouteRequest(
                board_revision=snapshot.snapshot_digest,
                net_id=net,
                start_pad_id=pads[0].id,
                end_pad_id=pads[1].id,
                start_layer_id=accesses[0][0],
                end_layer_id=accesses[1][0],
                grid_step_nm=routing_settings.grid_step_nm,
                seed=prepared.request.seed + index,
                settings=LayeredAStarSettings(
                    max_expansions=routing_settings.max_expansions,
                    max_obstacle_checks=routing_settings.max_obstacle_checks,
                    max_nodes=routing_settings.max_grid_nodes,
                    max_obstacles=min(256, routing_settings.max_obstacles),
                ),
            )
            layered = LayeredBoardRouter().propose(snapshot, request, cancelled=probe.cancelled)
            probes += 1
            probe.checkpoint()
            if layered.candidate is not None:
                routed_source = render_kicad_layered_candidate_board(
                    source,
                    snapshot,
                    layered.candidate,
                    prepared.profile,
                    request=request,
                    limits=limits,
                )
                candidate_ids.append(layered.candidate.candidate_id)
                length += layered.candidate.metrics.wire_length_nm
                vias += layered.candidate.metrics.vias
        if routed_source is None:
            raise OptimizationExecutionError("backend_failure")
        converted = parse_kicad_bytes(routed_source, prepared.profile, limits)
        if converted.snapshot is None or converted.diagnostics:
            raise OptimizationExecutionError("invalid_candidate")
        source, snapshot = routed_source, converted.snapshot
        connected.append(net)
    probe.checkpoint()
    return PrivateRouteComposition(
        source, snapshot, base, tuple(candidate_ids), tuple(connected), length, vias, probes
    )
