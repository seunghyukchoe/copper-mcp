"""Disposable KiCad serialization for the bounded layered-route proposal.

This adapter is deliberately narrower than an apply service.  It renders a verified, immutable
two-signal-layer candidate into new bytes, reparses those bytes through the Board IR adapter, and
checks that the only semantic additions are the candidate's segments and through-vias.  It never
writes a board, calls KiCad, runs DRC, or grants an apply token.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from itertools import pairwise

from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.kicad_route_patch import (
    _modeled_object_count,
    _quoted_atom,
    _require_native_geometry_identities,
    _rewrite_writer_metadata,
    _source_structure,
)
from copper_mcp.board_ir import (
    BoardIRSnapshot,
    ParseLimits,
    PointNM,
    Segment,
    Via,
    ViaKind,
    nm_to_mm,
)
from copper_mcp.routing.layered_board_adapter import LayeredBoardRouter, LayeredRouteRequest
from copper_mcp.routing.layered_candidate_verifier import verify_layered_candidate
from copper_mcp.routing.layered_contracts import (
    LayeredRouteCandidate,
    LayeredRoutePath,
    verify_layered_candidate_id,
)

_SEGMENT_NAMESPACE = uuid.UUID("f4ed0f4a-f0ad-4d41-8ce8-c6dc91e95a2a")
_VIA_NAMESPACE = uuid.UUID("e17a4c19-4d6b-4a21-b79f-e93ddfcd3f0f")
_WRITER_ID = "copper-mcp"


class KiCadLayeredRoutePatchError(ValueError):
    """Raised when a layered candidate cannot be rendered safely."""


def _native_uuid(namespace: uuid.UUID, candidate_id: str, index: int) -> str:
    return str(uuid.uuid5(namespace, f"{candidate_id}:{index}"))


def _render_segment(
    *,
    start: PointNM,
    end: PointNM,
    width_nm: int,
    layer_name: str,
    net_name: str,
    native_uuid: str,
) -> bytes:
    return (
        "  (segment\n"
        f"    (start {nm_to_mm(start.x)} {nm_to_mm(start.y)})\n"
        f"    (end {nm_to_mm(end.x)} {nm_to_mm(end.y)})\n"
        f"    (width {nm_to_mm(width_nm)})\n"
        f'    (layer "{_quoted_atom(layer_name)}")\n'
        f'    (net "{_quoted_atom(net_name)}")\n'
        f'    (uuid "{native_uuid}")\n'
        "  )\n"
    ).encode("utf-8", errors="strict")


def _render_via(
    *,
    center: PointNM,
    diameter_nm: int,
    drill_nm: int,
    layer_names: tuple[str, str],
    net_name: str,
    native_uuid: str,
) -> bytes:
    return (
        "  (via\n"
        f"    (at {nm_to_mm(center.x)} {nm_to_mm(center.y)})\n"
        f"    (size {nm_to_mm(diameter_nm)})\n"
        f"    (drill {nm_to_mm(drill_nm)})\n"
        f'    (layers "{_quoted_atom(layer_names[0])}" "{_quoted_atom(layer_names[1])}")\n'
        f'    (net "{_quoted_atom(net_name)}")\n'
        f'    (uuid "{native_uuid}")\n'
        "  )\n"
    ).encode("utf-8", errors="strict")


def _path_edges(paths: tuple[LayeredRoutePath, ...]) -> list[tuple[str, PointNM, PointNM]]:
    return [(path.layer_id, start, end) for path in paths for start, end in pairwise(path.vertices)]


def _validate_candidate_geometry(
    snapshot: BoardIRSnapshot,
    candidate: LayeredRouteCandidate,
    layer_ids: tuple[str, str],
) -> None:
    """Validate candidate references and endpoint/via topology before rendering."""

    content = snapshot.content
    layers = {layer.id: layer for layer in content.copper_layers}
    nets = {net.id: net for net in content.nets}
    pads = {pad.id: pad for pad in content.pads}
    if candidate.patch.net_id not in nets:
        raise KiCadLayeredRoutePatchError("candidate references an unknown net")
    if candidate.start_pad_id not in pads or candidate.end_pad_id not in pads:
        raise KiCadLayeredRoutePatchError("candidate references an unknown endpoint pad")
    if (
        pads[candidate.start_pad_id].net_id != candidate.patch.net_id
        or pads[candidate.end_pad_id].net_id != candidate.patch.net_id
    ):
        raise KiCadLayeredRoutePatchError("candidate endpoints are not on the patch net")
    if any(
        path.layer_id not in layer_ids or layers[path.layer_id].kind != "signal"
        for path in candidate.patch.paths
    ):
        raise KiCadLayeredRoutePatchError("candidate path references an unsupported layer")

    edges = _path_edges(candidate.patch.paths)
    if not edges:
        raise KiCadLayeredRoutePatchError("candidate carries no layered segments")
    start_center = pads[candidate.start_pad_id].center
    end_center = pads[candidate.end_pad_id].center
    via_centers = {via.center for via in candidate.patch.vias}
    path_endpoints = {point for _, start, end in edges for point in (start, end)}
    if (
        start_center not in path_endpoints | via_centers
        or end_center not in path_endpoints | via_centers
    ):
        raise KiCadLayeredRoutePatchError(
            "candidate geometry does not attach to both endpoint pads"
        )
    if any(
        via.center not in path_endpoints | {start_center, end_center}
        for via in candidate.patch.vias
    ):
        raise KiCadLayeredRoutePatchError("candidate via is disconnected from its route geometry")
    expected_layers = set(layer_ids)
    for via in candidate.patch.vias:
        if {via.start_layer_id, via.end_layer_id} != expected_layers:
            raise KiCadLayeredRoutePatchError(
                "candidate via does not span the full supported stack"
            )
        if (
            via.diameter_nm != candidate.patch.via_diameter_nm
            or via.drill_nm != candidate.patch.via_drill_nm
        ):
            raise KiCadLayeredRoutePatchError("candidate via dimensions do not match its patch")


def render_kicad_layered_candidate_board(
    source: bytes,
    snapshot: BoardIRSnapshot,
    candidate: LayeredRouteCandidate,
    profile: KiCadConstraintProfile,
    *,
    request: LayeredRouteRequest,
    limits: ParseLimits | None = None,
) -> bytes:
    """Render and round-trip one replayed layered candidate without mutation or DRC."""

    limits = limits or ParseLimits()
    if not isinstance(source, bytes):
        raise KiCadLayeredRoutePatchError("KiCad source must be immutable bytes")
    if not isinstance(snapshot, BoardIRSnapshot):
        raise KiCadLayeredRoutePatchError("board snapshot is malformed")
    if not isinstance(candidate, LayeredRouteCandidate):
        raise KiCadLayeredRoutePatchError("layered route candidate is malformed")
    if not isinstance(profile, KiCadConstraintProfile):
        raise KiCadLayeredRoutePatchError("KiCad constraint profile is malformed")
    if not isinstance(request, LayeredRouteRequest):
        raise KiCadLayeredRoutePatchError("layered route request is malformed")
    if not isinstance(limits, ParseLimits):
        raise KiCadLayeredRoutePatchError("parse limits are malformed")

    conversion = parse_kicad_bytes(source, profile, limits)
    if conversion.snapshot is None or conversion.diagnostics:
        raise KiCadLayeredRoutePatchError("KiCad source cannot be represented by Board IR")
    if conversion.snapshot != snapshot:
        raise KiCadLayeredRoutePatchError("KiCad source and snapshot do not match")
    _require_native_geometry_identities(snapshot)
    if candidate.base_revision != snapshot.snapshot_digest:
        raise KiCadLayeredRoutePatchError("candidate is stale for the supplied snapshot")
    try:
        verify_layered_candidate_id(candidate)
    except ValueError as error:
        raise KiCadLayeredRoutePatchError("candidate identity verification failed") from error
    replay = LayeredBoardRouter().propose(snapshot, request)
    if replay.candidate is None or replay.candidate != candidate:
        raise KiCadLayeredRoutePatchError("candidate does not match a deterministic router replay")

    structural = verify_layered_candidate(
        candidate,
        snapshot,
        expected_board_revision=snapshot.snapshot_digest,
        expected_start_pad_id=request.start_pad_id,
        expected_end_pad_id=request.end_pad_id,
    )
    if not structural.ok:
        raise KiCadLayeredRoutePatchError(
            f"candidate structural verification refused: {structural.diagnostic.code.value}"
        )

    signal_layers = tuple(
        sorted(
            (layer for layer in snapshot.content.copper_layers if layer.kind == "signal"),
            key=lambda layer: (layer.index, layer.id),
        )
    )
    if len(signal_layers) != 2:
        raise KiCadLayeredRoutePatchError("layered serializer requires exactly two signal layers")
    layer_ids = (signal_layers[0].id, signal_layers[1].id)
    _validate_candidate_geometry(snapshot, candidate, layer_ids)
    edges = _path_edges(candidate.patch.paths)
    if (
        _modeled_object_count(snapshot) + len(edges) + len(candidate.patch.vias)
        > limits.max_objects
    ):
        raise KiCadLayeredRoutePatchError("rendered board exceeds the configured object budget")

    root, native_identities = _source_structure(source, limits)
    writer_source = _rewrite_writer_metadata(source, root)
    stripped = writer_source.rstrip(b" \t\r\n")
    if not stripped or stripped[-1:] != b")":
        raise KiCadLayeredRoutePatchError("KiCad source has no supported root closing delimiter")
    closing_index = len(stripped) - 1
    prefix = writer_source[:closing_index]
    suffix = writer_source[closing_index:]
    separator = b"" if prefix.endswith(b"\n") else b"\n"
    net_name = next(net.name for net in snapshot.content.nets if net.id == candidate.patch.net_id)
    layer_by_id = {layer.id: layer for layer in signal_layers}

    rendered: list[bytes] = []
    expected_segments: list[Segment] = []
    for index, (layer_id, start, end) in enumerate(edges):
        native_uuid = _native_uuid(_SEGMENT_NAMESPACE, candidate.candidate_id, index)
        if native_uuid.lower() in native_identities:
            raise KiCadLayeredRoutePatchError("deterministic segment identity collides with source")
        rendered.append(
            _render_segment(
                start=start,
                end=end,
                width_nm=candidate.patch.width_nm,
                layer_name=layer_by_id[layer_id].name,
                net_name=net_name,
                native_uuid=native_uuid,
            )
        )
        expected_segments.append(
            Segment(
                id=f"segment:kicad:{native_uuid}",
                net_id=candidate.patch.net_id,
                layer_id=layer_id,
                start=start,
                end=end,
                width_nm=candidate.patch.width_nm,
            )
        )

    expected_vias: list[Via] = []
    for index, via in enumerate(candidate.patch.vias):
        native_uuid = _native_uuid(_VIA_NAMESPACE, candidate.candidate_id, index)
        if native_uuid.lower() in native_identities:
            raise KiCadLayeredRoutePatchError("deterministic via identity collides with source")
        rendered.append(
            _render_via(
                center=via.center,
                diameter_nm=via.diameter_nm,
                drill_nm=via.drill_nm,
                layer_names=(signal_layers[0].name, signal_layers[1].name),
                net_name=net_name,
                native_uuid=native_uuid,
            )
        )
        expected_vias.append(
            Via(
                id=f"via:kicad:{native_uuid}",
                net_id=candidate.patch.net_id,
                center=via.center,
                diameter_nm=via.diameter_nm,
                drill_nm=via.drill_nm,
                start_layer_id=layer_ids[0],
                end_layer_id=layer_ids[1],
                kind=ViaKind.THROUGH,
            )
        )

    rendered_board = prefix + separator + b"".join(rendered) + suffix
    if len(rendered_board) > limits.max_input_bytes:
        raise KiCadLayeredRoutePatchError("rendered candidate board exceeds the input-byte budget")
    reparsed = parse_kicad_bytes(rendered_board, profile, limits)
    if reparsed.snapshot is None or reparsed.diagnostics:
        raise KiCadLayeredRoutePatchError("rendered candidate failed Board IR round-trip parsing")
    expected_source = replace(
        snapshot.content.source,
        revision=reparsed.snapshot.content.source.revision,
        generator=_WRITER_ID,
    )
    expected_content = replace(
        snapshot.content,
        source=expected_source,
        segments=tuple(
            sorted(snapshot.content.segments + tuple(expected_segments), key=lambda item: item.id)
        ),
        vias=tuple(sorted(snapshot.content.vias + tuple(expected_vias), key=lambda item: item.id)),
    )
    if reparsed.snapshot.content != expected_content:
        raise KiCadLayeredRoutePatchError(
            "rendered board changed content outside the layered patch"
        )
    return rendered_board


__all__ = ["KiCadLayeredRoutePatchError", "render_kicad_layered_candidate_board"]
