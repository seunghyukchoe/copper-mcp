"""Candidate-only composition over existing single-layer and ordered-layer routers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from copper_mcp.adapters import (
    parse_kicad_bytes,
    render_kicad_candidate_board,
    render_kicad_layered_candidate_board,
)
from copper_mcp.adapters.kicad_layered_tree_patch import (
    render_kicad_layered_tree_candidate_board,
)
from copper_mcp.board_ir import BoardIRSnapshot
from copper_mcp.board_ir.pad_geometry import pad_obstacle_bounds
from copper_mcp.config import Settings
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.optimization.inputs import PreparedOptimization
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.repair import CopperRepairBinding, remove_target_copper
from copper_mcp.optimization.worker import OptimizationExecutionError, OptimizationExecutionProbe
from copper_mcp.optimization.zone_fill import (
    CandidateFillBinding,
    FilledCandidate,
    refill_candidate,
)
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.routing import (
    AStarRouter,
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteRequest,
)
from copper_mcp.routing.astar import (
    VerifiedFill,
    _arc_envelope,
    _ExpectedFailureError,
    _multilayer_via_count,
    _point_bounds,
    _polygon_bounds,
    _rectangles_touch,
    _WorkBudget,
)
from copper_mcp.routing.contracts import AStarSettings, RouteRequest
from copper_mcp.routing.layered_tree_contracts import LayeredTreeTerminal
from copper_mcp.routing.layered_tree_router import LayeredTreeRequest, LayeredTreeRouter
from copper_mcp.routing.layered_tree_verifier import _UnionFind


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
    fill: CandidateFillBinding | None = None
    fill_rounds: int = 0
    fill_history_digest: str | None = None
    repair: CopperRepairBinding | None = None

    @property
    def digest(self) -> str:
        return digest_document(
            "optimization-route-composition/v1",
            {
                "base": self.base_snapshot_digest,
                "result": self.snapshot.snapshot_digest,
                "candidates": self.candidate_ids,
                "targets": self.connected_targets,
                **({"fill_history": self.fill_history_digest} if self.fill is not None else {}),
                **({"repair": self.repair.digest} if self.repair is not None else {}),
            },
        )


class _ConnectivityWork(_WorkBudget):
    """Reuse the core's geometric proof while precharging small bounded predicate batches."""

    def __init__(self, settings: AStarSettings, probe: OptimizationExecutionProbe) -> None:
        super().__init__(settings, probe.cancelled)
        self.probe = probe
        self.reserved = 0

    def checkpoint(self) -> None:
        self.probe.checkpoint()
        super().checkpoint()

    def obstacle_check(self) -> None:
        if self.obstacle_checks == self.reserved:
            from copper_mcp.optimization.evaluation_v2 import SlotProbe

            if not isinstance(self.probe, SlotProbe):
                raise OptimizationExecutionError("invalid_candidate")
            available = (
                self.probe.budget.validation_checks - self.probe.validation_checks
                if self.probe.validation
                else self.probe.budget.routing_checks - self.probe.routing_checks
            )
            count = min(
                256,
                self.settings.max_obstacle_checks - self.reserved,
                available,
            )
            if count <= 0:
                raise OptimizationExecutionError("budget_exhausted")
            self.probe.reserve(ResourceUsage(obstacle_checks=count))
            self.reserved += count
        super().obstacle_check()


def _already_connected(
    prepared: PreparedOptimization,
    snapshot: BoardIRSnapshot,
    net: str,
    verified_fill: tuple[VerifiedFill, ...],
    probe: OptimizationExecutionProbe,
) -> bool:
    pads = tuple(
        sorted((pad for pad in snapshot.content.pads if pad.net_id == net), key=lambda p: p.id)
    )
    if len(pads) < 2:
        raise OptimizationExecutionError("invalid_candidate")
    request = RouteRequest(
        snapshot.snapshot_digest,
        net,
        snapshot.content.copper_layers[0].id,
        prepared.request.seed,
        prepared.routing_settings,
    )
    work = _ConnectivityWork(prepared.routing_settings, probe)
    try:
        result = _multilayer_via_count(snapshot, request, pads, work, verified_fill)
    except _ExpectedFailureError:
        probe.checkpoint()
        raise OptimizationExecutionError("unsupported_geometry") from None
    probe.checkpoint()
    return result is not None


def _repair_connectivity(
    prepared: PreparedOptimization,
    snapshot: BoardIRSnapshot,
    net: str,
    verified_fill: tuple[VerifiedFill, ...],
    probe: OptimizationExecutionProbe,
    filled: FilledCandidate | None,
) -> Literal["connected", "disconnected", "inconclusive"]:
    """Positive copper cores prove connection; disjoint enclosing boxes prove disconnection.

    Overlapping envelopes are only a possibility, not connectivity evidence. Missing geometry
    or fill authority stays inconclusive and cannot authorize resetting an unchanged net.
    """
    if _already_connected(prepared, snapshot, net, verified_fill, probe):
        return "connected"
    work = _ConnectivityWork(prepared.routing_settings, probe)
    objects: list[tuple[frozenset[str], tuple[int, int, int, int]]] = []
    all_layers = frozenset(layer.id for layer in snapshot.content.copper_layers)

    def add(layers: frozenset[str], bounds: tuple[int, int, int, int]) -> None:
        work.obstacle_check()
        if len(objects) >= prepared.routing_settings.max_net_objects:
            raise OptimizationExecutionError("budget_exhausted")
        objects.append((layers, bounds))

    try:
        content = snapshot.content
        for pad in content.pads:
            work.obstacle_check()
            if pad.net_id == net:
                add(frozenset(pad.layer_ids), pad_obstacle_bounds(pad))
        pad_count = len(objects)
        for segment in content.segments:
            work.obstacle_check()
            if segment.net_id == net:
                add(
                    frozenset({segment.layer_id}),
                    _point_bounds(segment.start, segment.end, (segment.width_nm + 1) // 2),
                )
        for via in content.vias:
            work.obstacle_check()
            if via.net_id == net:
                add(all_layers, _point_bounds(via.center, via.center, (via.diameter_nm + 1) // 2))
        for arc in content.arcs:
            work.obstacle_check()
            if arc.net_id == net:
                envelope = _arc_envelope(arc)
                if envelope is None:
                    return "inconclusive"
                add(frozenset({arc.layer_id}), _polygon_bounds(envelope, work))
        zoned = False
        for zone in content.zones:
            work.obstacle_check()
            zoned |= zone.net_id == net
        if zoned:
            if (
                filled is None
                or filled.snapshot.snapshot_digest != snapshot.snapshot_digest
                or filled.binding.output_snapshot_digest != snapshot.snapshot_digest
                or filled.verified_fill != verified_fill
            ):
                return "inconclusive"
            for island in verified_fill:
                work.obstacle_check()
                if island.net_id == net:
                    add(frozenset({island.layer_id}), _polygon_bounds(island.points, work))
        union = _UnionFind(len(objects))
        for index, (layers, bounds) in enumerate(objects):
            for other in range(index):
                work.obstacle_check()
                if layers & objects[other][0] and _rectangles_touch(bounds, objects[other][1]):
                    union.union(index, other)
        probe.checkpoint()
        return (
            "disconnected" if len({union.find(i) for i in range(pad_count)}) > 1 else "inconclusive"
        )
    except _ExpectedFailureError:
        probe.checkpoint()
        raise OptimizationExecutionError("budget_exhausted") from None


def _reserve_search(
    prepared: PreparedOptimization, probe: OptimizationExecutionProbe, *, attempts: int = 1
) -> AStarSettings:
    """Reserve both proposal and serializer replay before either executes."""

    if prepared.request.schema_version == "optimization/v2":
        from copper_mcp.optimization.evaluation_v2 import SlotProbe

        if not isinstance(probe, SlotProbe):
            raise OptimizationExecutionError("invalid_candidate")
        return probe.reserve_search(prepared.routing_settings, attempts)
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
    Multi-pin nets first retain the established common-layer path, then use the private layered
    tree composer only when every original target can be bound and replayed as one component.
    """

    base = snapshot.snapshot_digest
    candidate_ids: list[str] = []
    connected: list[str] = []
    length = vias = 0
    probes = 0
    limits = parse_limits_for(settings)
    filled: FilledCandidate | None = None
    fill_rounds = 0
    history: str | None = None
    repair: CopperRepairBinding | None = None

    def refresh_fill() -> tuple[VerifiedFill, ...]:
        nonlocal source, snapshot, filled, fill_rounds, history
        if prepared.request.schema_version != "optimization/v2" or not snapshot.content.zones:
            return ()
        if filled is None or filled.source != source:
            from copper_mcp.optimization.evaluation_v2 import SlotProbe

            if not isinstance(probe, SlotProbe):
                raise OptimizationExecutionError("invalid_candidate")
            filled = refill_candidate(prepared, source, snapshot, settings, probe)
            source, snapshot = filled.source, filled.snapshot
            fill_rounds += 1
            history = digest_document(
                "optimization-fill-history/v1", {"previous": history, "fill": filled.binding.digest}
            )
        return filled.verified_fill

    if prepared.request.schema_version == "optimization/v2":
        from copper_mcp.optimization.evaluation_v2 import SlotProbe

        if not isinstance(probe, SlotProbe):
            raise OptimizationExecutionError("invalid_candidate")
        if probe.budget.repair_rounds > 0:
            initial_fill = refresh_fill()
            copper = (snapshot.content.segments, snapshot.content.vias, snapshot.content.arcs)
            probe.reserve(
                ResourceUsage(
                    obstacle_checks=(
                        sum(map(len, copper))
                        + len(prepared.snapshot.content.footprints)
                        + len(snapshot.content.footprints)
                        + len(snapshot.content.pads)
                    )
                )
            )
            copper_nets = {item.net_id for group in copper for item in group}
            original_footprints = {item.id: item for item in prepared.snapshot.content.footprints}
            if set(original_footprints) != {item.id for item in snapshot.content.footprints}:
                raise OptimizationExecutionError("invalid_candidate")
            moved = tuple(
                item
                for item in snapshot.content.footprints
                if (item.origin, item.rotation_udeg, item.side)
                != (
                    original_footprints[item.id].origin,
                    original_footprints[item.id].rotation_udeg,
                    original_footprints[item.id].side,
                )
            )
            if any(
                item.id not in prepared.request.placement_scope.movable_footprint_refs
                or original_footprints[item.id].locked
                or item.side != original_footprints[item.id].side
                for item in moved
            ):
                raise OptimizationExecutionError("unsupported_geometry")
            moved_pads = {pad_id for item in moved for pad_id in item.pad_ids}
            moved_nets = {pad.net_id for pad in snapshot.content.pads if pad.id in moved_pads}
            repair_targets = []
            for net in prepared.target_net_refs:
                if net not in copper_nets:
                    continue
                if net in moved_nets:
                    repair_targets.append(net)
                    continue
                connection = _repair_connectivity(
                    prepared, snapshot, net, initial_fill, probe, filled
                )
                if connection == "inconclusive":
                    raise OptimizationExecutionError("unsupported_geometry")
                if connection == "disconnected":
                    repair_targets.append(net)
            if repair_targets:
                reset = remove_target_copper(
                    prepared, source, snapshot, tuple(repair_targets), base, settings, probe
                )
                source, snapshot, repair = reset.source, reset.snapshot, reset.binding

    for index, net in enumerate(prepared.target_net_refs):
        probe.checkpoint()
        verified_fill = refresh_fill()
        if prepared.request.schema_version == "optimization/v2" and _already_connected(
            prepared, snapshot, net, verified_fill, probe
        ):
            connected.append(net)
            continue
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
            routing_settings = _reserve_search(prepared, probe, attempts=len(pads) - 1)
            if prepared.request.schema_version == "optimization/v1":
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
                verified_fill=verified_fill,
            )
            probe.checkpoint()
            if result.connected is not None:
                routed_source = source
                break
            if result.candidate is None:
                continue
            routed_source = render_kicad_candidate_board(
                source,
                snapshot,
                result.candidate,
                prepared.profile,
                limits=limits,
                verified_fill=verified_fill,
            )
            if prepared.request.schema_version == "optimization/v2":
                probes += 2 * len(result.candidate.patch.paths)
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
                verified_fill=verified_fill,
            )
            layered = LayeredBoardRouter().propose(snapshot, request, cancelled=probe.cancelled)
            if prepared.request.schema_version == "optimization/v1":
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
                if prepared.request.schema_version == "optimization/v2":
                    probes += 2
                candidate_ids.append(layered.candidate.candidate_id)
                length += layered.candidate.metrics.wire_length_nm
                vias += layered.candidate.metrics.vias
        if routed_source is None and len(pads) > 2:
            routing_settings = _reserve_search(prepared, probe, attempts=len(pads) - 1)
            ordered_pads = tuple(sorted(pads, key=lambda pad: pad.id))
            terminals: list[LayeredTreeTerminal] = []
            for pad in ordered_pads:
                access = next(
                    (
                        layer.id
                        for layer in layers
                        if layer.kind == "signal" and layer.id in pad.layer_ids
                    ),
                    None,
                )
                if access is None:
                    raise OptimizationExecutionError("unsupported_geometry")
                terminals.append(LayeredTreeTerminal(pad.id, access))
            tree_request = LayeredTreeRequest(
                board_revision=snapshot.snapshot_digest,
                net_id=net,
                terminals=tuple(terminals),
                grid_step_nm=routing_settings.grid_step_nm,
                seed=prepared.request.seed + index,
                settings=LayeredAStarSettings(
                    max_expansions=routing_settings.max_expansions,
                    max_obstacle_checks=routing_settings.max_obstacle_checks,
                    max_nodes=routing_settings.max_grid_nodes,
                    max_obstacles=routing_settings.max_obstacles,
                ),
                verified_fill=verified_fill,
            )
            tree = LayeredTreeRouter().propose(snapshot, tree_request, cancelled=probe.cancelled)
            probe.checkpoint()
            if tree.candidate is not None:
                if prepared.request.schema_version == "optimization/v1":
                    probes += tree.candidate.metrics.branch_count
                routed_source = render_kicad_layered_tree_candidate_board(
                    source,
                    snapshot,
                    tree.candidate,
                    prepared.profile,
                    request=tree_request,
                    limits=limits,
                    cancelled=probe.cancelled,
                )
                if prepared.request.schema_version == "optimization/v2":
                    probes += 2 * sum(
                        not branch.attachment_only for branch in tree.candidate.branches
                    )
                candidate_ids.append(tree.candidate.candidate_id)
                length += tree.candidate.metrics.wire_length_nm
                vias += tree.candidate.metrics.vias
        if routed_source is None:
            raise OptimizationExecutionError("backend_failure")
        converted = parse_kicad_bytes(routed_source, prepared.profile, limits)
        if converted.snapshot is None or converted.diagnostics:
            raise OptimizationExecutionError("invalid_candidate")
        source, snapshot = routed_source, converted.snapshot
        connected.append(net)
    probe.checkpoint()
    final_fill = refresh_fill()
    if prepared.request.schema_version == "optimization/v2":
        from copper_mcp.optimization.evaluation_v2 import SlotProbe

        if not isinstance(probe, SlotProbe):
            raise OptimizationExecutionError("invalid_candidate")
        previous_phase = probe.validation
        probe.validation = True
        try:
            for net in prepared.target_net_refs:
                if not _already_connected(prepared, snapshot, net, final_fill, probe):
                    raise OptimizationExecutionError("invalid_candidate")
        finally:
            probe.validation = previous_phase
    return PrivateRouteComposition(
        source,
        snapshot,
        base,
        tuple(candidate_ids),
        tuple(connected),
        length,
        vias,
        probes,
        None if filled is None else filled.binding,
        fill_rounds,
        history,
        repair,
    )
