"""Source-preserving serialization for one private branch-aware layered tree."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace

from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.kicad_layered_route_patch import _render_segment, _render_via
from copper_mcp.adapters.kicad_route_patch import (
    _modeled_object_count,
    _require_native_geometry_identities,
    _rewrite_writer_metadata,
    _source_structure,
)
from copper_mcp.board_ir import BoardIRSnapshot, ParseLimits, Segment, Via, ViaKind
from copper_mcp.routing._layered_tree_cancel import (
    LayeredTreeCancellationError,
    poll_cancelled,
)
from copper_mcp.routing.layered_tree_contracts import (
    LayeredTreeCandidate,
    layered_tree_physical_edges,
    layered_tree_physical_vias,
    verify_layered_tree_candidate_id,
)
from copper_mcp.routing.layered_tree_router import LayeredTreeRequest, LayeredTreeRouter
from copper_mcp.routing.layered_tree_verifier import (
    _admit_candidate_shape,
    verify_layered_tree_candidate,
    verify_reparsed_layered_tree_connectivity,
)

_SEGMENT_NAMESPACE = uuid.UUID("d24c8a89-8ec8-4fea-9c51-1c4e67d74545")
_VIA_NAMESPACE = uuid.UUID("6bcc1bb0-419e-4bbd-a4cc-4633d87671bd")
_WRITER_ID = "copper-mcp"


class KiCadLayeredTreePatchError(ValueError):
    """A private layered tree cannot be replayed and rendered safely."""


def _native_uuid(namespace: uuid.UUID, candidate_id: str, index: int) -> str:
    return str(uuid.uuid5(namespace, f"{candidate_id}:{index}"))


def render_kicad_layered_tree_candidate_board(
    source: bytes,
    snapshot: BoardIRSnapshot,
    candidate: LayeredTreeCandidate,
    profile: KiCadConstraintProfile,
    *,
    request: LayeredTreeRequest,
    limits: ParseLimits | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> bytes:
    """Replay, serialize, reparse, and prove complete selected-net connectivity."""

    active_limits = limits or ParseLimits()
    if type(source) is not bytes:
        raise KiCadLayeredTreePatchError("KiCad source must be immutable bytes")
    if type(snapshot) is not BoardIRSnapshot or type(candidate) is not LayeredTreeCandidate:
        raise KiCadLayeredTreePatchError("layered tree inputs are malformed")
    if type(profile) is not KiCadConstraintProfile or type(request) is not LayeredTreeRequest:
        raise KiCadLayeredTreePatchError("layered tree rendering context is malformed")
    if type(active_limits) is not ParseLimits:
        raise KiCadLayeredTreePatchError("parse limits are malformed")
    if _admit_candidate_shape(candidate) is None:
        raise KiCadLayeredTreePatchError("layered tree exceeds its structural budget")
    try:
        candidate.__post_init__()
    except (TypeError, ValueError):
        raise KiCadLayeredTreePatchError("layered tree is malformed") from None
    if cancelled is not None and not callable(cancelled):
        raise KiCadLayeredTreePatchError("cancellation check is malformed")

    def checkpoint() -> None:
        failed = False
        try:
            if poll_cancelled(cancelled):
                raise KiCadLayeredTreePatchError("layered tree serialization was cancelled")
        except LayeredTreeCancellationError:
            failed = True
        if failed:
            raise KiCadLayeredTreePatchError("cancellation check failed")

    checkpoint()
    conversion = parse_kicad_bytes(source, profile, active_limits)
    if conversion.snapshot is None or conversion.diagnostics or conversion.snapshot != snapshot:
        raise KiCadLayeredTreePatchError("KiCad source and Board IR snapshot do not match")
    _require_native_geometry_identities(snapshot)
    try:
        verify_layered_tree_candidate_id(candidate, checkpoint=checkpoint)
    except KiCadLayeredTreePatchError:
        raise
    except (TypeError, ValueError) as error:
        raise KiCadLayeredTreePatchError("layered tree identity verification failed") from error
    checkpoint()
    replay = LayeredTreeRouter().replay(snapshot, candidate, request, cancelled=cancelled)
    checkpoint()
    if replay.candidate != candidate:
        raise KiCadLayeredTreePatchError("layered tree does not match deterministic replay")
    structural = verify_layered_tree_candidate(
        candidate, snapshot, expected_request=request, cancelled=cancelled
    )
    checkpoint()
    if not structural.ok:
        raise KiCadLayeredTreePatchError(
            "layered tree structural verification refused: " + structural.code
        )

    signal_layers = tuple(
        sorted(
            (layer for layer in snapshot.content.copper_layers if layer.kind == "signal"),
            key=lambda layer: (layer.index, layer.id),
        )
    )
    if not 2 <= len(signal_layers) <= 8 or len(signal_layers) != len(
        snapshot.content.copper_layers
    ):
        raise KiCadLayeredTreePatchError(
            "layered tree serializer requires two through eight signal layers"
        )
    layer_by_id = {layer.id: layer for layer in signal_layers}
    edges = layered_tree_physical_edges(candidate.branches)
    vias = layered_tree_physical_vias(candidate.branches)
    if _modeled_object_count(snapshot) + len(edges) + len(vias) > active_limits.max_objects:
        raise KiCadLayeredTreePatchError("rendered tree exceeds the object budget")

    checkpoint()
    root, native_identities = _source_structure(source, active_limits)
    writer_source = _rewrite_writer_metadata(source, root)
    stripped = writer_source.rstrip(b" \t\r\n")
    if not stripped or stripped[-1:] != b")":
        raise KiCadLayeredTreePatchError("KiCad source has no supported root closing delimiter")
    closing_index = len(stripped) - 1
    prefix, suffix = writer_source[:closing_index], writer_source[closing_index:]
    separator = b"" if prefix.endswith(b"\n") else b"\n"
    net_name = next(item.name for item in snapshot.content.nets if item.id == candidate.net_id)

    rendered: list[bytes] = []
    projected_bytes = len(prefix) + len(separator) + len(suffix)
    expected_segments: list[Segment] = []
    for index, (layer_id, start, end) in enumerate(edges):
        checkpoint()
        native_uuid = _native_uuid(_SEGMENT_NAMESPACE, candidate.candidate_id, index)
        if native_uuid.lower() in native_identities:
            raise KiCadLayeredTreePatchError("deterministic segment identity collides with source")
        chunk = _render_segment(
            start=start,
            end=end,
            width_nm=candidate.width_nm,
            layer_name=layer_by_id[layer_id].name,
            net_name=net_name,
            native_uuid=native_uuid,
        )
        projected_bytes += len(chunk)
        if projected_bytes > active_limits.max_input_bytes:
            raise KiCadLayeredTreePatchError("rendered tree exceeds the input-byte budget")
        rendered.append(chunk)
        expected_segments.append(
            Segment(
                id=f"segment:kicad:{native_uuid}",
                net_id=candidate.net_id,
                layer_id=layer_id,
                start=start,
                end=end,
                width_nm=candidate.width_nm,
            )
        )
    expected_vias: list[Via] = []
    for index, via in enumerate(vias):
        checkpoint()
        native_uuid = _native_uuid(_VIA_NAMESPACE, candidate.candidate_id, index)
        if native_uuid.lower() in native_identities:
            raise KiCadLayeredTreePatchError("deterministic via identity collides with source")
        chunk = _render_via(
            center=via.center,
            diameter_nm=via.diameter_nm,
            drill_nm=via.drill_nm,
            layer_names=(signal_layers[0].name, signal_layers[-1].name),
            net_name=net_name,
            native_uuid=native_uuid,
        )
        projected_bytes += len(chunk)
        if projected_bytes > active_limits.max_input_bytes:
            raise KiCadLayeredTreePatchError("rendered tree exceeds the input-byte budget")
        rendered.append(chunk)
        expected_vias.append(
            Via(
                id=f"via:kicad:{native_uuid}",
                net_id=candidate.net_id,
                center=via.center,
                diameter_nm=via.diameter_nm,
                drill_nm=via.drill_nm,
                start_layer_id=signal_layers[0].id,
                end_layer_id=signal_layers[-1].id,
                kind=ViaKind.THROUGH,
            )
        )
    rendered_board = prefix + separator + b"".join(rendered) + suffix
    if len(rendered_board) > active_limits.max_input_bytes:
        raise KiCadLayeredTreePatchError("rendered tree exceeds the input-byte budget")
    reparsed = parse_kicad_bytes(rendered_board, profile, active_limits)
    checkpoint()
    if reparsed.snapshot is None or reparsed.diagnostics:
        raise KiCadLayeredTreePatchError("rendered tree failed Board IR round-trip parsing")
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
        raise KiCadLayeredTreePatchError("rendered tree changed unrelated source semantics")
    connected = verify_reparsed_layered_tree_connectivity(
        reparsed.snapshot,
        candidate.net_id,
        tuple(item.pad_id for item in candidate.terminals),
        cancelled=cancelled,
    )
    if not connected.ok:
        raise KiCadLayeredTreePatchError(
            "rendered tree does not connect every original target pad: " + connected.code
        )
    checkpoint()
    return rendered_board


__all__: list[str] = []
