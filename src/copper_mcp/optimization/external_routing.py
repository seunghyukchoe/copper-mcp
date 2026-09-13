"""Production v2 external-router launch, normalization, and source-preserving disposal."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_UP,
    Context,
    Decimal,
    DecimalException,
    Inexact,
    InvalidOperation,
    Overflow,
    Underflow,
    localcontext,
)
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Final, Literal

from copper_mcp import kicad_cli
from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.adapters.kicad_layered_route_patch import _render_segment, _render_via
from copper_mcp.adapters.kicad_route_patch import (
    _modeled_object_count,
    _require_native_geometry_identities,
    _rewrite_writer_metadata,
    _source_structure,
)
from copper_mcp.adapters.sexpr import SExpr, atoms, parse_sexpr
from copper_mcp.board_ir import (
    BoardIRSnapshot,
    Layer,
    PointNM,
    Segment,
    Via,
    ViaKind,
    nm_to_mm,
)
from copper_mcp.board_ir.pad_geometry import pad_obstacle_bounds
from copper_mcp.config import Settings
from copper_mcp.optimization.container_runner import (
    ContainerRouterLimits,
    ContainerRouterRunner,
    ContainerRunRecord,
    ContainerRunRequest,
    ContainerRunStatus,
    EngineKind,
    OperatorContainerRuntime,
    OperatorRouterImages,
)
from copper_mcp.optimization.contracts import Backend, digest_document
from copper_mcp.optimization.inputs import PreparedOptimization
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.native_import import _discard_generated_preferences
from copper_mcp.optimization.package import (
    BackendProvenanceV2,
    CoordinateQuantizationV2,
    ExternalRunV2,
    SpecctraDisposalV2,
)
from copper_mcp.optimization.provenance import bounded_file_digest
from copper_mcp.optimization.repair import CopperRepairBinding, remove_target_copper
from copper_mcp.optimization.routing import (
    PrivateRouteComposition,
    _already_connected,
    _copper_and_moved_nets,
    _repair_connectivity,
)
from copper_mcp.optimization.worker import OptimizationExecutionError
from copper_mcp.optimization.zone_fill import FilledCandidate, refill_candidate
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.security import read_workspace_file

if TYPE_CHECKING:
    from copper_mcp.optimization.evaluation_v2 import SlotProbe

_SRJ_VERSION: Final = "0.0.872"
_FREEROUTING_VERSION: Final = "2.2.4"
_MAX_ROUTER_BYTES: Final = 16 * 1024 * 1024
_SEGMENT_NAMESPACE = uuid.UUID("d435073e-29fd-5d4f-bf88-7f7159830bb6")
_VIA_NAMESPACE = uuid.UUID("fda3eab7-1374-5ba7-8317-d19662736128")


@dataclass(frozen=True, slots=True)
class _RouteSegment:
    net_id: str
    layer_id: str
    start: PointNM
    end: PointNM
    width_nm: int


@dataclass(frozen=True, slots=True)
class _RouteVia:
    net_id: str
    center: PointNM
    diameter_nm: int
    drill_nm: int


@dataclass(frozen=True, slots=True)
class ExternalRouteExecution:
    composition: PrivateRouteComposition
    provenance: BackendProvenanceV2


class _NumberToken(str):
    """Exact JSON number spelling, distinct from a source-controlled string."""


def _optional_digest(path: Path | None) -> str | None:
    try:
        return None if path is None or not path.is_file() else bounded_file_digest(path)
    except (OSError, ValueError):
        return None


def external_router_settings_digest(settings: Settings, backends: tuple[Backend, ...]) -> str:
    """Bind configured executables and images without retaining operator-private paths."""

    socket = settings.optimization_docker_socket
    socket_state = None
    if socket is not None:
        try:
            info = socket.stat()
            socket_state = (info.st_dev, info.st_ino)
        except FileNotFoundError:
            pass
        except OSError:
            raise OptimizationExecutionError("backend_failure") from None
    return digest_document(
        "optimization-external-router-settings/v1",
        {
            "backends": backends,
            "docker": _optional_digest(settings.optimization_docker_executable),
            "socket": None if socket is None else {"path": str(socket), "state": socket_state},
            "config_root": None
            if settings.optimization_docker_config_root is None
            else str(settings.optimization_docker_config_root),
            "images": {
                backend: settings.optimization_freerouting_image
                if backend == "freerouting-dsn-ses-v1"
                else settings.optimization_simpleroutejson_image
                for backend in backends
                if backend != "internal-layered-v1"
            },
            "specctra_python": _optional_digest(settings.optimization_specctra_python)
            if "freerouting-dsn-ses-v1" in backends
            else None,
            "profiles": {
                "freerouting": _FREEROUTING_VERSION,
                "simpleroutejson": _SRJ_VERSION,
                "containment": "local-docker-fixed-entrypoint/v1",
                "normalization": "source-preserving-target-copper/v1",
            },
        },
    )


def _runtime(
    settings: Settings, backend: Backend
) -> tuple[OperatorContainerRuntime, OperatorRouterImages]:
    if backend not in {"freerouting-dsn-ses-v1", "simpleroutejson-v1"}:
        raise OptimizationExecutionError("backend_failure")
    freerouting = backend == "freerouting-dsn-ses-v1"
    values = (
        settings.optimization_docker_executable,
        settings.optimization_docker_socket,
        settings.optimization_docker_config_root,
    )
    if any(value is None for value in values):
        raise OptimizationExecutionError("backend_failure")
    executable, socket, root = values
    assert isinstance(executable, Path) and isinstance(socket, Path) and isinstance(root, Path)
    if freerouting and settings.optimization_specctra_python is None:
        raise OptimizationExecutionError("backend_failure")
    try:
        return (
            OperatorContainerRuntime(executable, socket, root),
            OperatorRouterImages(
                settings.optimization_freerouting_image if freerouting else None,
                None if freerouting else settings.optimization_simpleroutejson_image,
            ),
        )
    except (OSError, ValueError):
        raise OptimizationExecutionError("backend_failure") from None


def _output_cap(probe: SlotProbe, maximum: int) -> int:
    remaining = probe.budget.external_output_bytes - probe.usage.external_output_bytes
    if remaining < 1:
        raise OptimizationExecutionError("budget_exhausted")
    return min(maximum, remaining)


def _router_runner(
    settings: Settings, probe: SlotProbe, engine: EngineKind
) -> ContainerRouterRunner:
    runtime, images = _runtime(
        settings,
        "freerouting-dsn-ses-v1" if engine is EngineKind.FREEROUTING else "simpleroutejson-v1",
    )
    remaining = probe.remaining_time_ms()
    if remaining < 1:
        raise OptimizationExecutionError("budget_exhausted")
    max_output = _output_cap(probe, _MAX_ROUTER_BYTES)
    return ContainerRouterRunner(
        runtime,
        images,
        ContainerRouterLimits(
            max_runtime_ms=min(remaining, 3_600_000),
            max_input_bytes=_MAX_ROUTER_BYTES,
            max_output_bytes=max_output,
            memory_bytes=4 * 1024 * 1024 * 1024,
            cpu_count=1,
            pids_limit=256,
            work_tmpfs_bytes=128 * 1024 * 1024,
        ),
    )


def _run_router(
    prepared: PreparedOptimization,
    settings: Settings,
    probe: SlotProbe,
    engine: EngineKind,
    payload: bytes,
) -> tuple[bytes, ContainerRunRecord]:
    if len(payload) > _MAX_ROUTER_BYTES:
        raise OptimizationExecutionError("unsupported_geometry")
    probe.reserve(ResourceUsage(route_attempts=1))
    runner = _router_runner(settings, probe, engine)
    result = runner.run(
        ContainerRunRequest(engine, payload),
        cancelled=probe.root.cancelled,
        deadline=time.monotonic() + probe.remaining_time_ms() / 1000,
    )
    freerouting = engine is EngineKind.FREEROUTING
    _record_external_run(
        prepared,
        settings,
        probe,
        "freerouting-dsn-ses-v1" if freerouting else "simpleroutejson-v1",
        _FREEROUTING_VERSION if freerouting else _SRJ_VERSION,
        result.record,
    )
    if result.record.output_bytes:
        probe.charge_external_output(result.record.output_bytes)
    if result.record.status is ContainerRunStatus.CANCELLED:
        probe.checkpoint()
        raise OptimizationExecutionError("backend_failure")
    if result.record.status in {
        ContainerRunStatus.DEADLINE_EXCEEDED,
        ContainerRunStatus.OUTPUT_LIMIT_EXCEEDED,
    }:
        raise OptimizationExecutionError("budget_exhausted")
    if result.record.status is not ContainerRunStatus.SUCCESS or result.output is None:
        raise OptimizationExecutionError("backend_failure")
    if result.record.input_digest is None or result.record.output_digest is None:
        raise OptimizationExecutionError("invalid_candidate")
    return result.output, result.record


def _mm(value_nm: int) -> int | float:
    value = nm_to_mm(value_nm)
    return int(value) if "." not in value else float(value)


def _signal_layers(snapshot: BoardIRSnapshot) -> tuple[Layer, ...]:
    layers = tuple(sorted(snapshot.content.copper_layers, key=lambda item: (item.index, item.id)))
    if not 2 <= len(layers) <= 8 or any(layer.kind != "signal" for layer in layers):
        raise OptimizationExecutionError("unsupported_geometry")
    return layers


def _layer_maps(snapshot: BoardIRSnapshot) -> tuple[dict[str, str], dict[str, str]]:
    layers = _signal_layers(snapshot)
    names = ["top", *(f"inner{index}" for index in range(1, len(layers) - 1)), "bottom"]
    forward = {layer.id: name for layer, name in zip(layers, names, strict=True)}
    return forward, {name: layer_id for layer_id, name in forward.items()}


def _require_external_scope(prepared: PreparedOptimization, snapshot: BoardIRSnapshot) -> None:
    _signal_layers(snapshot)
    if snapshot.content.arcs:
        # KiCad's native DSN exporter flattens arcs to endpoints; no backend may inherit that loss.
        raise OptimizationExecutionError("unsupported_geometry")
    copper: tuple[Segment | Via, ...] = (*snapshot.content.segments, *snapshot.content.vias)
    if any(item.net_id is None for item in copper):
        raise OptimizationExecutionError("unsupported_geometry")


def _prepare_external_base(
    prepared: PreparedOptimization,
    source: bytes,
    snapshot: BoardIRSnapshot,
    settings: Settings,
    probe: SlotProbe,
) -> tuple[
    bytes,
    BoardIRSnapshot,
    tuple[str, ...],
    CopperRepairBinding | None,
    int,
    str | None,
]:
    """Reuse v2 fill/connectivity/repair controls before any external bytes are admitted."""

    from copper_mcp.optimization.evaluation_v2 import SlotProbe

    if not isinstance(probe, SlotProbe):
        raise OptimizationExecutionError("invalid_candidate")
    base_snapshot_digest = snapshot.snapshot_digest
    filled: FilledCandidate | None = None
    fill_rounds = 0
    history = None
    if snapshot.content.zones:
        filled = refill_candidate(prepared, source, snapshot, settings, probe)
        source, snapshot = filled.source, filled.snapshot
        fill_rounds = 1
        history = digest_document(
            "optimization-fill-history/v1", {"previous": None, "fill": filled.binding.digest}
        )
    target_copper, moved_nets = _copper_and_moved_nets(prepared, snapshot, probe)
    repair_targets: list[str] = []
    connected: set[str] = set()
    verified_fill = () if filled is None else filled.verified_fill
    for net in prepared.target_net_refs:
        if net not in target_copper:
            continue
        if net in moved_nets:
            repair_targets.append(net)
            continue
        state = _repair_connectivity(prepared, snapshot, net, verified_fill, probe, filled)
        if state == "connected":
            connected.add(net)
        elif state == "disconnected":
            repair_targets.append(net)
        else:
            raise OptimizationExecutionError("unsupported_geometry")
    repair = None
    if repair_targets:
        reset = remove_target_copper(
            prepared,
            source,
            snapshot,
            tuple(sorted(repair_targets)),
            base_snapshot_digest,
            settings,
            probe,
        )
        source, snapshot, repair = reset.source, reset.snapshot, reset.binding
    routes = tuple(net for net in prepared.target_net_refs if net not in connected)
    if not routes:
        raise OptimizationExecutionError("backend_failure")
    return source, snapshot, routes, repair, fill_rounds, history


def _rect(
    bounds: tuple[int, int, int, int],
    layers: list[str],
    connected: list[str],
    layer_indices: dict[str, int],
) -> dict[str, object]:
    left, top, right, bottom = bounds
    ordered = sorted(layers, key=layer_indices.__getitem__)
    z_layers = [layer_indices[layer] for layer in ordered]
    return {
        "type": "rect",
        "layers": ordered,
        "zLayers": z_layers,
        "__zLayers": z_layers,
        "center": {"x": _mm((left + right) // 2), "y": _mm((top + bottom) // 2)},
        "width": _mm(right - left),
        "height": _mm(bottom - top),
        "connectedTo": connected,
    }


def _simple_route_json_input(
    prepared: PreparedOptimization, snapshot: BoardIRSnapshot, route_targets: tuple[str, ...]
) -> bytes:
    _require_external_scope(prepared, snapshot)
    layer_names, _ = _layer_maps(snapshot)
    # The pinned pipeline canonicalizes these aliases even in its originalSrj output.
    # Supply that exact representation; never excuse arbitrary output changes to obstacles.
    # https://github.com/tscircuit/tscircuit-autorouter/blob/b13394891034d560749f46071df5fd98062a9f36/lib/utils/create-srj-with-board-valid-obstacle-layers.ts
    layer_indices = {name: index for index, name in enumerate(layer_names.values())}
    net_class = next(
        item
        for item in snapshot.content.constraints.net_classes
        if item.id == prepared.profile.default_net_class_id
    )
    obstacles: list[dict[str, object]] = []
    for pad in snapshot.content.pads:
        obstacles.append(
            _rect(
                pad_obstacle_bounds(pad),
                [layer_names[layer] for layer in pad.layer_ids],
                [pad.net_id] if pad.net_id is not None else [],
                layer_indices,
            )
        )
    for segment in snapshot.content.segments:
        radius = (segment.width_nm + 1) // 2
        obstacles.append(
            _rect(
                (
                    min(segment.start.x, segment.end.x) - radius,
                    min(segment.start.y, segment.end.y) - radius,
                    max(segment.start.x, segment.end.x) + radius,
                    max(segment.start.y, segment.end.y) + radius,
                ),
                [layer_names[segment.layer_id]],
                [segment.net_id] if segment.net_id is not None else [],
                layer_indices,
            )
        )
    for via in snapshot.content.vias:
        radius = (via.diameter_nm + 1) // 2
        obstacles.append(
            _rect(
                (
                    via.center.x - radius,
                    via.center.y - radius,
                    via.center.x + radius,
                    via.center.y + radius,
                ),
                list(layer_names.values()),
                [via.net_id] if via.net_id is not None else [],
                layer_indices,
            )
        )
    for zone in snapshot.content.zones:
        xs = [point.x for point in zone.boundary.points]
        ys = [point.y for point in zone.boundary.points]
        obstacle = _rect(
            (min(xs), min(ys), max(xs), max(ys)),
            [layer_names[zone.layer_id]],
            [zone.net_id],
            layer_indices,
        )
        obstacle["isCopperPour"] = True
        obstacles.append(obstacle)
    for keepout in snapshot.content.keepouts:
        if not keepout.prohibit_tracks and not keepout.prohibit_vias:
            continue
        xs = [point.x for point in keepout.boundary.points]
        ys = [point.y for point in keepout.boundary.points]
        obstacles.append(
            _rect(
                (min(xs), min(ys), max(xs), max(ys)),
                [layer_names[layer] for layer in keepout.layer_ids],
                [],
                layer_indices,
            )
        )
    connections = []
    for net_id in route_targets:
        points = []
        for pad in sorted(
            (item for item in snapshot.content.pads if item.net_id == net_id),
            key=lambda item: item.id,
        ):
            layers = [layer_names[layer] for layer in pad.layer_ids]
            point: dict[str, object] = {
                "x": _mm(pad.center.x),
                "y": _mm(pad.center.y),
                "pointId": pad.id,
            }
            if len(layers) == 1:
                point["layer"] = layers[0]
            else:
                point["layers"] = layers
            points.append(point)
        connections.append({"name": net_id, "pointsToConnect": points})
    contour = snapshot.content.outline
    if len(contour) != 1 or contour[0].holes:
        raise OptimizationExecutionError("unsupported_geometry")
    outline = contour[0].outer.points
    xs = [point.x for point in outline]
    ys = [point.y for point in outline]
    document = {
        "layerCount": len(layer_names),
        "minTraceWidth": _mm(net_class.track_width_nm),
        "nominalTraceWidth": _mm(net_class.track_width_nm),
        "min_via_hole_diameter": _mm(net_class.via_drill_nm),
        "min_via_pad_diameter": _mm(net_class.via_diameter_nm),
        "defaultObstacleMargin": _mm(net_class.clearance_nm),
        "minTraceToPadEdgeClearance": _mm(net_class.clearance_nm),
        "allowViaInPad": False,
        "obstacles": obstacles,
        "connections": connections,
        "bounds": {
            "minX": _mm(min(xs)),
            "maxX": _mm(max(xs)),
            "minY": _mm(min(ys)),
            "maxY": _mm(max(ys)),
        },
        "outline": [{"x": _mm(point.x), "y": _mm(point.y)} for point in outline],
    }
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _strict_json(payload: bytes) -> dict[str, Any]:
    if not payload or len(payload) > _MAX_ROUTER_BYTES:
        raise OptimizationExecutionError("invalid_candidate")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            parse_float=_NumberToken,
            parse_int=_NumberToken,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constant")),
            object_pairs_hook=pairs,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise OptimizationExecutionError("invalid_candidate") from None
    if type(value) is not dict:
        raise OptimizationExecutionError("invalid_candidate")
    return value


def _bounded_text(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 256:
        raise OptimizationExecutionError("invalid_candidate")
    return value


def _number_nm(value: object) -> Decimal:
    if type(value) is not _NumberToken or len(value) > 40:
        raise OptimizationExecutionError("invalid_candidate")
    scaled = None
    # Forty input characters fit exactly within this private precision, including scaling.
    # Refuse after leaving the context: typed errors must not unwind through its generator.
    with localcontext(
        Context(
            prec=64,
            Emax=MAX_EMAX,
            Emin=MIN_EMIN,
            traps=[InvalidOperation, Inexact, Overflow, Underflow],
        )
    ):
        try:
            scaled = Decimal(value).scaleb(6)
        except (DecimalException, ValueError):
            pass
    if scaled is None or not scaled.is_finite() or scaled.copy_abs() > 1_000_000_000:
        raise OptimizationExecutionError("invalid_candidate") from None
    return scaled


def _nm(value: object) -> int:
    """Dimensions retain exact integer-nanometre interpretation."""
    scaled = _number_nm(value)
    if scaled != scaled.to_integral_value():
        raise OptimizationExecutionError("unsupported_geometry")
    return int(scaled)


def _coordinate_nm(value: object) -> tuple[int, bool]:
    """Project external coordinates before writing exact, six-decimal-place KiCad values.

    The native file format truncates excess precision; this explicit policy instead chooses
    the nearest representable coordinate and then validates the resulting candidate afresh.
    https://dev-docs.kicad.org/en/file-formats/sexpr-intro/#_board_coordinates
    """
    scaled = _number_nm(value)
    rounded = scaled.to_integral_value(rounding=ROUND_HALF_UP)
    return int(rounded), rounded != scaled


def _normalize_srj(
    prepared: PreparedOptimization,
    snapshot: BoardIRSnapshot,
    source_document: bytes,
    output_document: bytes,
    route_targets: tuple[str, ...],
) -> tuple[tuple[_RouteSegment, ...], tuple[_RouteVia, ...], CoordinateQuantizationV2]:
    source = _strict_json(source_document)
    output = _strict_json(output_document)
    if set(output) != set(source) | {"traces"} or any(
        output.get(key) != value for key, value in source.items()
    ):
        raise OptimizationExecutionError("invalid_candidate")
    traces = output.get("traces")
    if type(traces) is not list or not 1 <= len(traces) <= 4096:
        raise OptimizationExecutionError("invalid_candidate")
    _, layer_ids = _layer_maps(snapshot)
    targets = set(route_targets)
    net_class = next(
        item
        for item in snapshot.content.constraints.net_classes
        if item.id == prepared.profile.default_net_class_id
    )
    segments: list[_RouteSegment] = []
    vias: list[_RouteVia] = []
    trace_ids: set[str] = set()
    total_items = 0
    rounded_coordinates = 0
    for trace in traces:
        if type(trace) is not dict or not set(trace).issubset(
            {"type", "pcb_trace_id", "connection_name", "connectsTo", "route"}
        ):
            raise OptimizationExecutionError("invalid_candidate")
        if set(trace) < {"type", "pcb_trace_id", "connection_name", "route"}:
            raise OptimizationExecutionError("invalid_candidate")
        if trace["type"] != "pcb_trace":
            raise OptimizationExecutionError("unsupported_geometry")
        trace_id = _bounded_text(trace["pcb_trace_id"])
        net_id = _bounded_text(trace["connection_name"])
        if trace_id in trace_ids or net_id not in targets:
            raise OptimizationExecutionError("invalid_candidate")
        trace_ids.add(trace_id)
        if "connectsTo" in trace and (
            type(trace["connectsTo"]) is not list
            or len(trace["connectsTo"]) > 4096
            or any(type(item) is not str for item in trace["connectsTo"])
        ):
            raise OptimizationExecutionError("invalid_candidate")
        route = trace["route"]
        if type(route) is not list or not 1 <= len(route) <= 4096:
            raise OptimizationExecutionError("invalid_candidate")
        if len(route) == 1 and (type(route[0]) is not dict or route[0].get("route_type") != "via"):
            raise OptimizationExecutionError("invalid_candidate")
        total_items += len(route)
        if total_items > 16_384:
            raise OptimizationExecutionError("budget_exhausted")
        previous: PointNM | None = None
        active_layer: str | None = None
        for item in route:
            if type(item) is not dict:
                raise OptimizationExecutionError("invalid_candidate")
            route_type = item.get("route_type")
            x, rounded_x = _coordinate_nm(item.get("x"))
            y, rounded_y = _coordinate_nm(item.get("y"))
            rounded_coordinates += int(rounded_x) + int(rounded_y)
            point = PointNM(x, y)
            if route_type == "wire":
                if set(item) - {
                    "route_type",
                    "x",
                    "y",
                    "width",
                    "layer",
                    "start_pcb_port_id",
                    "end_pcb_port_id",
                }:
                    raise OptimizationExecutionError("invalid_candidate")
                layer = layer_ids.get(_bounded_text(item.get("layer")))
                width = _nm(item.get("width"))
                if layer is None or width != net_class.track_width_nm:
                    raise OptimizationExecutionError("unsupported_geometry")
                if previous is not None:
                    if active_layer != layer:
                        raise OptimizationExecutionError("invalid_candidate")
                    if point != previous:
                        segments.append(_RouteSegment(net_id, layer, previous, point, width))
                previous, active_layer = point, layer
            elif route_type == "via":
                if set(item) - {
                    "route_type",
                    "x",
                    "y",
                    "from_layer",
                    "to_layer",
                    "via_diameter",
                    "via_hole_diameter",
                }:
                    raise OptimizationExecutionError("invalid_candidate")
                start = layer_ids.get(_bounded_text(item.get("from_layer")))
                end = layer_ids.get(_bounded_text(item.get("to_layer")))
                if start is None or end is None or start == end:
                    raise OptimizationExecutionError("unsupported_geometry")
                if previous is not None and (point != previous or active_layer != start):
                    raise OptimizationExecutionError("invalid_candidate")
                diameter = (
                    net_class.via_diameter_nm
                    if item.get("via_diameter") is None
                    else _nm(item.get("via_diameter"))
                )
                drill = (
                    net_class.via_drill_nm
                    if item.get("via_hole_diameter") is None
                    else _nm(item.get("via_hole_diameter"))
                )
                if diameter != net_class.via_diameter_nm or drill != net_class.via_drill_nm:
                    raise OptimizationExecutionError("unsupported_geometry")
                vias.append(_RouteVia(net_id, point, diameter, drill))
                previous, active_layer = point, end
            else:
                raise OptimizationExecutionError("unsupported_geometry")
    if not segments and not vias:
        raise OptimizationExecutionError("invalid_candidate")
    normalized_segments, normalized_vias = _canonical_copper(segments, vias)
    return (
        normalized_segments,
        normalized_vias,
        CoordinateQuantizationV2(
            coordinate_count=2 * total_items, rounded_coordinate_count=rounded_coordinates
        ),
    )


def _canonical_copper(
    segments: list[_RouteSegment], vias: list[_RouteVia]
) -> tuple[tuple[_RouteSegment, ...], tuple[_RouteVia, ...]]:
    normalized_segments = []
    for item in segments:
        start, end = sorted((item.start, item.end))
        normalized_segments.append(replace(item, start=start, end=end))
    segment_tuple = tuple(
        sorted(
            normalized_segments,
            key=lambda item: (
                item.net_id,
                item.layer_id,
                item.start,
                item.end,
                item.width_nm,
            ),
        )
    )
    via_tuple = tuple(
        sorted(
            vias,
            key=lambda item: (
                item.net_id,
                item.center,
                item.diameter_nm,
                item.drill_nm,
            ),
        )
    )
    if len(set(segment_tuple)) != len(segment_tuple) or len(set(via_tuple)) != len(via_tuple):
        raise OptimizationExecutionError("invalid_candidate")
    return segment_tuple, via_tuple


def _copper_digest(segments: tuple[_RouteSegment, ...], vias: tuple[_RouteVia, ...]) -> str:
    return digest_document(
        "optimization-external-copper/v1",
        {
            "segments": [
                [
                    item.net_id,
                    item.layer_id,
                    item.start.x,
                    item.start.y,
                    item.end.x,
                    item.end.y,
                    item.width_nm,
                ]
                for item in segments
            ],
            "vias": [
                [
                    item.net_id,
                    item.center.x,
                    item.center.y,
                    item.diameter_nm,
                    item.drill_nm,
                ]
                for item in vias
            ],
        },
    )


def _render_disposed_copper(
    prepared: PreparedOptimization,
    source: bytes,
    snapshot: BoardIRSnapshot,
    segments: tuple[_RouteSegment, ...],
    vias: tuple[_RouteVia, ...],
    settings: Settings,
) -> tuple[bytes, BoardIRSnapshot, str]:
    limits = parse_limits_for(settings)
    parsed = parse_kicad_bytes(source, prepared.profile, limits)
    if parsed.snapshot is None or parsed.diagnostics or parsed.snapshot != snapshot:
        raise OptimizationExecutionError("invalid_candidate")
    _require_native_geometry_identities(snapshot)
    layers = {item.id: item for item in _signal_layers(snapshot)}
    nets = {item.id: item for item in snapshot.content.nets}
    copper: tuple[_RouteSegment | _RouteVia, ...] = (*segments, *vias)
    if any(item.net_id not in prepared.target_net_refs for item in copper):
        raise OptimizationExecutionError("invalid_candidate")
    if _modeled_object_count(snapshot) + len(segments) + len(vias) > limits.max_objects:
        raise OptimizationExecutionError("budget_exhausted")
    digest = _copper_digest(segments, vias)
    root, identities = _source_structure(source, limits)
    writer_source = _rewrite_writer_metadata(source, root)
    stripped = writer_source.rstrip(b" \t\r\n")
    if not stripped or stripped[-1:] != b")":
        raise OptimizationExecutionError("invalid_candidate")
    closing = len(stripped) - 1
    prefix, suffix = writer_source[:closing], writer_source[closing:]
    separator = b"" if prefix.endswith(b"\n") else b"\n"
    rendered: list[bytes] = []
    expected_segments: list[Segment] = []
    for index, item in enumerate(segments):
        native = str(uuid.uuid5(_SEGMENT_NAMESPACE, f"{digest}:{index}"))
        if native in identities or item.layer_id not in layers or item.net_id not in nets:
            raise OptimizationExecutionError("invalid_candidate")
        rendered.append(
            _render_segment(
                start=item.start,
                end=item.end,
                width_nm=item.width_nm,
                layer_name=layers[item.layer_id].name,
                net_name=nets[item.net_id].name,
                native_uuid=native,
            )
        )
        expected_segments.append(
            Segment(
                f"segment:kicad:{native}",
                item.net_id,
                item.layer_id,
                item.start,
                item.end,
                item.width_nm,
            )
        )
    signal_layers = tuple(layers.values())
    expected_vias: list[Via] = []
    for index, via in enumerate(vias):
        native = str(uuid.uuid5(_VIA_NAMESPACE, f"{digest}:{index}"))
        if native in identities or via.net_id not in nets:
            raise OptimizationExecutionError("invalid_candidate")
        rendered.append(
            _render_via(
                center=via.center,
                diameter_nm=via.diameter_nm,
                drill_nm=via.drill_nm,
                layer_names=(signal_layers[0].name, signal_layers[-1].name),
                net_name=nets[via.net_id].name,
                native_uuid=native,
            )
        )
        expected_vias.append(
            Via(
                f"via:kicad:{native}",
                via.net_id,
                via.center,
                via.diameter_nm,
                via.drill_nm,
                signal_layers[0].id,
                signal_layers[-1].id,
                ViaKind.THROUGH,
            )
        )
    output = prefix + separator + b"".join(rendered) + suffix
    if len(output) > limits.max_input_bytes:
        raise OptimizationExecutionError("budget_exhausted")
    reparsed = parse_kicad_bytes(output, prepared.profile, limits)
    if reparsed.snapshot is None or reparsed.diagnostics:
        raise OptimizationExecutionError("invalid_candidate")
    expected_source = replace(
        snapshot.content.source,
        revision=reparsed.snapshot.content.source.revision,
        generator="copper-mcp",
    )
    expected = replace(
        snapshot.content,
        source=expected_source,
        segments=tuple(
            sorted(snapshot.content.segments + tuple(expected_segments), key=lambda x: x.id)
        ),
        vias=tuple(sorted(snapshot.content.vias + tuple(expected_vias), key=lambda x: x.id)),
    )
    if reparsed.snapshot.content != expected:
        raise OptimizationExecutionError("invalid_candidate")
    return output, reparsed.snapshot, digest


def _specctra_process(
    settings: Settings,
    operation: Literal["export", "import"],
    root: Path,
    environment: dict[str, str],
    probe: SlotProbe,
) -> str:
    python = settings.optimization_specctra_python
    worker = Path(__file__).with_name("specctra_worker.py").resolve()
    if (
        python is None
        or not python.is_absolute()
        or not python.is_file()
        or not os.access(python, os.X_OK)
        or not worker.is_file()
    ):
        raise OptimizationExecutionError("backend_failure")
    remaining = probe.remaining_time_ms()
    if remaining < 1:
        raise OptimizationExecutionError("budget_exhausted")
    controller = kicad_cli._validated_executable(Path(sys.executable))
    bounded = kicad_cli._BOUNDED_EXEC.resolve(strict=True)
    if controller is None or os.name != "posix" or not bounded.is_file():
        raise OptimizationExecutionError("backend_failure")
    try:
        result = subprocess.run(  # noqa: S603 - fixed bounded wrapper and operator interpreter
            (
                str(controller),
                "-I",
                str(bounded),
                str(_output_cap(probe, min(settings.max_board_bytes, _MAX_ROUTER_BYTES))),
                str(python),
                "-I",
                str(worker),
                operation,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=max(0.001, min(settings.kicad_timeout_seconds, remaining / 1000)),
            cwd=root,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        raise OptimizationExecutionError("budget_exhausted") from None
    except OSError:
        raise OptimizationExecutionError("backend_failure") from None
    probe.checkpoint()
    if result.returncode != 0:
        raise OptimizationExecutionError("backend_failure")
    return digest_document(
        "optimization-specctra-command/v1",
        {
            "python": _optional_digest(python),
            "worker": bounded_file_digest(worker),
            "operation": operation,
        },
    )


def _record_external_run(
    prepared: PreparedOptimization,
    settings: Settings,
    probe: SlotProbe,
    backend: Backend,
    version: str,
    record: ContainerRunRecord,
) -> None:
    from copper_mcp.optimization.evaluation_v2 import SlotProbe

    if not isinstance(probe, SlotProbe):
        raise OptimizationExecutionError("invalid_candidate")
    docker = settings.optimization_docker_executable
    if docker is None:
        raise OptimizationExecutionError("backend_failure")
    probe.record_external_run(
        ExternalRunV2.model_validate(
            {
                "backend": backend,
                "version": version,
                "status": record.status.value,
                "image_digest": record.image_digest,
                "image_identity_kind": record.image_identity_kind,
                "command_digest": record.command_digest,
                "input_digest": record.input_digest,
                "output_digest": record.output_digest,
                "input_bytes": record.input_bytes,
                "output_bytes": record.output_bytes,
                "exit_code": record.exit_code,
                "executable_digest": bounded_file_digest(docker),
                "settings_digest": prepared.request.routing_profile_digest,
            }
        )
    )


def _validate_dsn(payload: bytes) -> None:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError:
        raise OptimizationExecutionError("unsupported_geometry") from None
    stripped = text.lstrip()
    if not stripped.startswith("(pcb ") or not stripped.rstrip().endswith(")"):
        raise OptimizationExecutionError("unsupported_geometry")
    # Exporting under a fixed relative name prevents private paths and random temp names from
    # entering DSN identity. A path separator in the root design name fails closed.
    root_name = stripped[5 : stripped.find("\n", 5)].strip().strip('"')
    if root_name != "board.dsn" or "/" in root_name or "\\" in root_name:
        raise OptimizationExecutionError("unsupported_geometry")


def _only_children(node: SExpr, allowed: frozenset[str]) -> tuple[SExpr, ...]:
    nested = tuple(item for item in node.items[1:] if isinstance(item, SExpr))
    if any(item.head not in allowed for item in nested):
        raise OptimizationExecutionError("unsupported_geometry")
    return nested


def _validate_ses(
    payload: bytes,
    snapshot: BoardIRSnapshot,
    settings: Settings,
    route_targets: tuple[str, ...],
) -> None:
    """Admit only the Specctra session subset the native importer can preserve exactly."""

    try:
        root = parse_sexpr(payload, parse_limits_for(settings))
    except (UnicodeError, ValueError):
        raise OptimizationExecutionError("invalid_candidate") from None
    if root.head != "session":
        raise OptimizationExecutionError("invalid_candidate")
    direct = _only_children(root, frozenset({"base_design", "placement", "routes", "was_is"}))
    # The pinned headless writer emits an empty renaming section. Pin swapping is not admitted.
    renamings = [item for item in direct if item.head == "was_is"]
    if len(renamings) > 1 or any(item.items != ("was_is",) for item in renamings):
        raise OptimizationExecutionError("unsupported_geometry")
    bases = [item for item in direct if item.head == "base_design"]
    routes = [item for item in direct if item.head == "routes"]
    if len(bases) != 1 or len(routes) != 1 or atoms(bases[0]) != ("input",):
        raise OptimizationExecutionError("invalid_candidate")
    route_sections = _only_children(
        routes[0], frozenset({"resolution", "parser", "library_out", "network_out"})
    )
    libraries = [item for item in route_sections if item.head == "library_out"]
    networks = [item for item in route_sections if item.head == "network_out"]
    if len(libraries) != 1 or len(networks) != 1:
        raise OptimizationExecutionError("invalid_candidate")
    padstacks = _only_children(libraries[0], frozenset({"padstack"}))
    padstack_definitions: dict[str, tuple[object, ...]] = {}
    for padstack in padstacks:
        values = tuple(item for item in padstack.items[1:] if isinstance(item, str))
        if len(values) != 1:
            raise OptimizationExecutionError("invalid_candidate")
        definition: list[object] = []
        fields = _only_children(padstack, frozenset({"shape", "attach", "rotate", "absolute"}))
        for field in fields:
            if field.head == "shape":
                shapes = _only_children(field, frozenset({"circle"}))
                if len(shapes) != 1 or len(atoms(shapes[0])) < 2:
                    raise OptimizationExecutionError("unsupported_geometry")
                definition.append(("circle", atoms(shapes[0])))
            else:
                if any(isinstance(item, SExpr) for item in field.items[1:]):
                    raise OptimizationExecutionError("unsupported_geometry")
                definition.append((field.head, atoms(field)))
        signature = tuple(definition)
        if values[0] in padstack_definitions and padstack_definitions[values[0]] != signature:
            raise OptimizationExecutionError("invalid_candidate")
        padstack_definitions[values[0]] = signature
    source_names = {item.name for item in snapshot.content.nets}
    target_names = {item.name for item in snapshot.content.nets if item.id in route_targets}
    observed: set[str] = set()
    layer_names = {item.name for item in snapshot.content.copper_layers}
    nets = _only_children(networks[0], frozenset({"net"}))
    for net in nets:
        values = tuple(item for item in net.items[1:] if isinstance(item, str))
        if len(values) != 1 or values[0] not in source_names or values[0] in observed:
            raise OptimizationExecutionError("invalid_candidate")
        observed.add(values[0])
        for route in _only_children(net, frozenset({"wire", "via"})):
            if route.head == "wire":
                shapes = _only_children(route, frozenset({"path"}))
                if len(shapes) != 1:
                    raise OptimizationExecutionError("unsupported_geometry")
                path = atoms(shapes[0])
                if len(path) < 6 or path[0] not in layer_names:
                    raise OptimizationExecutionError("unsupported_geometry")
            else:
                if any(isinstance(item, SExpr) for item in route.items[1:]):
                    raise OptimizationExecutionError("unsupported_geometry")
                values = atoms(route)
                if len(values) < 3 or values[0] not in padstack_definitions:
                    raise OptimizationExecutionError("unsupported_geometry")
    if not target_names.issubset(observed):
        raise OptimizationExecutionError("invalid_candidate")


def _extract_imported_copper(
    prepared: PreparedOptimization,
    source_snapshot: BoardIRSnapshot,
    imported: bytes,
    settings: Settings,
    route_targets: tuple[str, ...],
    probe: SlotProbe,
) -> tuple[tuple[_RouteSegment, ...], tuple[_RouteVia, ...], SpecctraDisposalV2]:
    converted = parse_kicad_bytes(imported, prepared.profile, parse_limits_for(settings))
    if converted.snapshot is None or converted.diagnostics:
        raise OptimizationExecutionError("invalid_candidate")
    result = converted.snapshot
    targets = set(route_targets)
    if not targets or not targets <= set(prepared.target_net_refs):
        raise OptimizationExecutionError("invalid_candidate")
    if result.content.arcs or source_snapshot.content.arcs:
        raise OptimizationExecutionError("unsupported_geometry")
    source_base = replace(
        source_snapshot.content,
        source=result.content.source,
        segments=(),
        vias=(),
    )
    imported_base = replace(
        result.content,
        segments=(),
        vias=(),
    )
    if imported_base != source_base:
        raise OptimizationExecutionError("invalid_candidate")
    original_items: tuple[Segment | Via, ...] = (
        *source_snapshot.content.segments,
        *source_snapshot.content.vias,
    )
    imported_items: tuple[Segment | Via, ...] = (*result.content.segments, *result.content.vias)
    probe.reserve(ResourceUsage(obstacle_checks=2 * (len(original_items) + len(imported_items))))
    originals = {item.id: item for item in original_items}
    imported_by_id = {item.id: item for item in imported_items}
    if (
        len(originals) != len(original_items)
        or len(imported_by_id) != len(imported_items)
        or any(imported_by_id.get(identity) != item for identity, item in originals.items())
    ):
        raise OptimizationExecutionError("invalid_candidate")
    classes = {item.id: item for item in result.content.constraints.net_classes}
    net_classes = {
        item.net_id: classes[item.net_class_id] for item in result.content.constraints.assignments
    }
    layers = _signal_layers(source_snapshot)
    layer_ids = {layer.id for layer in layers}
    accepted_segments: list[_RouteSegment] = []
    accepted_vias: list[_RouteVia] = []
    discarded_segments: list[_RouteSegment] = []
    discarded_vias: list[_RouteVia] = []
    all_segments: list[_RouteSegment] = []
    all_vias: list[_RouteVia] = []
    for item in imported_items:
        if item.net_id is None or item.net_id not in net_classes:
            raise OptimizationExecutionError("unsupported_geometry")
        existing = item.id in originals
        net_class = net_classes[item.net_id]
        if isinstance(item, Segment):
            segment = _RouteSegment(item.net_id, item.layer_id, item.start, item.end, item.width_nm)
            all_segments.append(segment)
            if existing:
                continue
            if (
                item.locked
                or item.layer_id not in layer_ids
                or item.width_nm != net_class.track_width_nm
            ):
                raise OptimizationExecutionError("unsupported_geometry")
            (accepted_segments if item.net_id in targets else discarded_segments).append(segment)
        else:
            via = _RouteVia(item.net_id, item.center, item.diameter_nm, item.drill_nm)
            all_vias.append(via)
            if existing:
                continue
            if (
                item.locked
                or item.start_layer_id != layers[0].id
                or item.end_layer_id != layers[-1].id
                or item.diameter_nm != net_class.via_diameter_nm
                or item.drill_nm != net_class.via_drill_nm
            ):
                raise OptimizationExecutionError("unsupported_geometry")
            (accepted_vias if item.net_id in targets else discarded_vias).append(via)
    # Duplicate geometry remains ambiguous even when one copy would be discarded.
    _canonical_copper(all_segments, all_vias)
    segments, vias = _canonical_copper(accepted_segments, accepted_vias)
    discarded = _canonical_copper(discarded_segments, discarded_vias)
    if not segments and not vias:
        raise OptimizationExecutionError("invalid_candidate")
    return (
        segments,
        vias,
        SpecctraDisposalV2(
            original_target_scope_digest=digest_document(
                "optimization-targets/v1", prepared.target_net_refs
            ),
            routing_target_scope_digest=digest_document("optimization-targets/v1", route_targets),
            original_target_count=len(prepared.target_net_refs),
            routing_target_count=len(route_targets),
            retained_copper_count=len(original_items),
            accepted_copper_count=len(segments) + len(vias),
            discarded_copper_count=len(discarded[0]) + len(discarded[1]),
            accepted_copper_digest=_copper_digest(segments, vias),
            discarded_copper_digest=_copper_digest(*discarded),
        ),
    )


def _freerouting_copper(
    prepared: PreparedOptimization,
    source: bytes,
    snapshot: BoardIRSnapshot,
    settings: Settings,
    probe: SlotProbe,
    route_targets: tuple[str, ...],
) -> tuple[
    tuple[_RouteSegment, ...],
    tuple[_RouteVia, ...],
    ContainerRunRecord,
    str,
    str,
    SpecctraDisposalV2,
]:
    _require_external_scope(prepared, snapshot)
    if settings.optimization_specctra_python is None:
        raise OptimizationExecutionError("backend_failure")
    from copper_mcp.optimization.evaluation import composition_context

    deadline = time.monotonic() + probe.remaining_time_ms() / 1000
    context = composition_context(prepared, source, settings, deadline=deadline)
    original_board = PurePosixPath(prepared.board_path)
    mapped_board = original_board.with_name("board.kicad_pcb")
    mapped: dict[str, bytes] = {}
    for name, payload in context.items():
        path = PurePosixPath(name)
        if path.parent == original_board.parent and path.stem == original_board.stem:
            path = path.with_name("board" + path.suffix)
        key = path.as_posix()
        if key in mapped:
            raise OptimizationExecutionError("unsupported_geometry")
        mapped[key] = source if name == prepared.board_path else payload
    with tempfile.TemporaryDirectory(prefix="copper-specctra-") as directory:
        root = Path(directory)
        tree = root / "snapshot"
        kicad_cli._write_drc_snapshot(mapped, tree)
        kicad_cli._make_snapshot_read_only(tree)
        board = tree / mapped_board.as_posix()
        work = board.parent
        work.chmod(0o700)
        environment = kicad_cli._private_kicad_environment(root / "state")
        export_digest = _specctra_process(settings, "export", work, environment, probe)
        if (
            read_workspace_file(
                tree,
                mapped_board.as_posix(),
                allowed_suffixes={".kicad_pcb"},
                max_bytes=settings.max_board_bytes,
            ).content
            != source
        ):
            raise OptimizationExecutionError("invalid_candidate")
        dsn_relative = mapped_board.with_name("board.dsn").as_posix()
        dsn = read_workspace_file(
            tree,
            dsn_relative,
            allowed_suffixes={".dsn"},
            max_bytes=_output_cap(probe, _MAX_ROUTER_BYTES),
        ).content
        probe.reserve(ResourceUsage(external_output_bytes=len(dsn)))
        _validate_dsn(dsn)
        ses, record = _run_router(prepared, settings, probe, EngineKind.FREEROUTING, dsn)
        _validate_ses(ses, snapshot, settings, route_targets)
        ses_path = work / "board.ses"
        ses_path.write_bytes(ses)
        import_digest = _specctra_process(settings, "import", work, environment, probe)
        if (
            read_workspace_file(
                tree,
                mapped_board.as_posix(),
                allowed_suffixes={".kicad_pcb"},
                max_bytes=settings.max_board_bytes,
            ).content
            != source
        ):
            raise OptimizationExecutionError("invalid_candidate")
        routed_relative = mapped_board.with_name("routed.kicad_pcb").as_posix()
        imported = read_workspace_file(
            tree,
            routed_relative,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=_output_cap(probe, settings.max_board_bytes),
        ).content
        probe.reserve(ResourceUsage(external_output_bytes=len(imported)))
        for output in (work / "board.dsn", ses_path, work / "routed.kicad_pcb"):
            output.chmod(0o400)
        _discard_generated_preferences(
            tree,
            mapped_board.as_posix(),
            mapped,
            max_bytes=settings.max_board_bytes,
            deadline=deadline,
        )
        kicad_cli._make_snapshot_read_only(tree)
        kicad_cli._validate_snapshot_tree(
            tree,
            frozenset(mapped)
            | {dsn_relative, mapped_board.with_name("board.ses").as_posix(), routed_relative},
            settings,
        )
        kicad_cli._validate_private_kicad_state(
            root / "state", kicad_cli._candidate_drc_deadline_settings(settings, deadline)
        )
    segments, vias, disposal = _extract_imported_copper(
        prepared, snapshot, imported, settings, route_targets, probe
    )
    converter = digest_document(
        "optimization-specctra-converter/v1",
        {"export": export_digest, "import": import_digest},
    )
    return segments, vias, record, converter, _FREEROUTING_VERSION, disposal


def _fresh_fill_and_connectivity(
    prepared: PreparedOptimization,
    source: bytes,
    snapshot: BoardIRSnapshot,
    base_snapshot_digest: str,
    normalized_digest: str,
    settings: Settings,
    probe: SlotProbe,
    repair: CopperRepairBinding | None,
    prior_fill_rounds: int,
    prior_fill_history: str | None,
    added_segments: tuple[_RouteSegment, ...],
    added_vias: tuple[_RouteVia, ...],
) -> PrivateRouteComposition:
    filled: FilledCandidate | None = None
    history = prior_fill_history
    fill_rounds = prior_fill_rounds
    if snapshot.content.zones:
        filled = refill_candidate(prepared, source, snapshot, settings, probe)
        source, snapshot = filled.source, filled.snapshot
        fill_rounds += 1
        history = digest_document(
            "optimization-fill-history/v1",
            {"previous": history, "fill": filled.binding.digest},
        )
    previous_phase = probe.validation
    probe.validation = True
    try:
        for net in prepared.target_net_refs:
            if not _already_connected(
                prepared,
                snapshot,
                net,
                () if filled is None else filled.verified_fill,
                probe,
            ):
                raise OptimizationExecutionError("invalid_candidate")
    finally:
        probe.validation = previous_phase
    wire_length = sum(
        math.isqrt((item.end.x - item.start.x) ** 2 + (item.end.y - item.start.y) ** 2)
        for item in added_segments
    )
    return PrivateRouteComposition(
        source=source,
        snapshot=snapshot,
        base_snapshot_digest=base_snapshot_digest,
        candidate_ids=(normalized_digest,),
        connected_targets=prepared.target_net_refs,
        wire_length_nm=wire_length,
        vias=len(added_vias),
        route_probes=1,
        fill=None if filled is None else filled.binding,
        fill_rounds=fill_rounds,
        fill_history_digest=history,
        repair=repair,
    )


def route_external_targets(
    prepared: PreparedOptimization,
    source: bytes,
    snapshot: BoardIRSnapshot,
    settings: Settings,
    probe: SlotProbe,
    *,
    backend: Backend | None = None,
) -> ExternalRouteExecution:
    """Execute the one operator-configured external backend and return checked private bytes."""

    if prepared.request.schema_version != "optimization/v2" or (
        backend is None and len(prepared.request.allowed_backends) != 1
    ):
        raise OptimizationExecutionError("backend_failure")
    backend = prepared.request.allowed_backends[0] if backend is None else backend
    if backend == "internal-layered-v1" or backend not in prepared.request.allowed_backends:
        raise OptimizationExecutionError("backend_failure")
    expected_settings = external_router_settings_digest(settings, prepared.request.allowed_backends)
    if expected_settings != prepared.external_router_settings_digest:
        raise OptimizationExecutionError("invalid_candidate")
    (
        route_source,
        route_snapshot,
        route_targets,
        repair,
        prior_fill_rounds,
        prior_fill_history,
    ) = _prepare_external_base(prepared, source, snapshot, settings, probe)
    quantization = None
    disposal = None
    if backend == "simpleroutejson-v1":
        input_document = _simple_route_json_input(prepared, route_snapshot, route_targets)
        output, record = _run_router(
            prepared, settings, probe, EngineKind.SIMPLE_ROUTE_JSON, input_document
        )
        segments, vias, quantization = _normalize_srj(
            prepared, route_snapshot, input_document, output, route_targets
        )
        converter_digest = bounded_file_digest(Path(__file__).resolve())
        version = _SRJ_VERSION
    elif backend == "freerouting-dsn-ses-v1":
        segments, vias, record, converter_digest, version, disposal = _freerouting_copper(
            prepared, route_source, route_snapshot, settings, probe, route_targets
        )
    else:
        raise OptimizationExecutionError("backend_failure")
    routed_source, routed_snapshot, normalized = _render_disposed_copper(
        prepared, route_source, route_snapshot, segments, vias, settings
    )
    from copper_mcp.optimization.evaluation_v2 import SlotProbe

    if not isinstance(probe, SlotProbe):
        raise OptimizationExecutionError("invalid_candidate")
    probe.bind_external_normalization(converter_digest, normalized, quantization, disposal)
    composition = _fresh_fill_and_connectivity(
        prepared,
        routed_source,
        routed_snapshot,
        snapshot.snapshot_digest,
        normalized,
        settings,
        probe,
        repair,
        prior_fill_rounds,
        prior_fill_history,
        segments,
        vias,
    )
    docker = settings.optimization_docker_executable
    if docker is None:
        raise OptimizationExecutionError("backend_failure")
    if record.command_digest is None:
        raise OptimizationExecutionError("invalid_candidate")
    if (
        external_router_settings_digest(settings, prepared.request.allowed_backends)
        != expected_settings
    ):
        raise OptimizationExecutionError("invalid_candidate")
    provenance = BackendProvenanceV2(
        backend=backend,
        version=version,
        executable_digest=bounded_file_digest(docker),
        command_digest=record.command_digest,
        settings_digest=prepared.request.routing_profile_digest,
        input_digest=composition.base_snapshot_digest,
        source_board_revision=prepared.request.board_revision,
        placed_snapshot_digest=composition.base_snapshot_digest,
        route_bundle_id=composition.digest,
        normalized_output_digest=composition.snapshot.content.source.revision,
        image_digest=record.image_digest,
        router_input_digest=record.input_digest,
        router_output_digest=record.output_digest,
        converter_digest=converter_digest,
        coordinate_quantization=quantization,
        specctra_disposal=disposal,
    )
    return ExternalRouteExecution(composition, provenance)


__all__ = [
    "ExternalRouteExecution",
    "external_router_settings_digest",
    "route_external_targets",
]
