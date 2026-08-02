"""Deterministic, disposable KiCad serialization for one replayed route candidate."""

from __future__ import annotations

import uuid
from dataclasses import replace
from itertools import pairwise

from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import BoardIRSnapshot, ParseLimits, Segment, nm_to_mm
from copper_mcp.routing import (
    AStarRouter,
    RouteCandidate,
    RouteRequest,
    verify_candidate_id,
)

_SEGMENT_NAMESPACE = uuid.UUID("0d904ca7-1130-4d38-a044-6ba5d92f357b")


class KiCadRoutePatchError(ValueError):
    """Raised when a candidate cannot be safely rendered into a disposable board."""


def _quoted_atom(value: str) -> str:
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        raise KiCadRoutePatchError("KiCad name contains an unsupported control character")
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _segment_uuid(candidate_id: str, index: int) -> str:
    return str(uuid.uuid5(_SEGMENT_NAMESPACE, f"{candidate_id}:{index}"))


def _render_segment(
    *,
    start_x_nm: int,
    start_y_nm: int,
    end_x_nm: int,
    end_y_nm: int,
    width_nm: int,
    layer_name: str,
    net_name: str,
    segment_uuid: str,
) -> bytes:
    return (
        "  (segment\n"
        f"    (start {nm_to_mm(start_x_nm)} {nm_to_mm(start_y_nm)})\n"
        f"    (end {nm_to_mm(end_x_nm)} {nm_to_mm(end_y_nm)})\n"
        f"    (width {nm_to_mm(width_nm)})\n"
        f'    (layer "{_quoted_atom(layer_name)}")\n'
        f'    (net "{_quoted_atom(net_name)}")\n'
        f'    (uuid "{segment_uuid}")\n'
        "  )\n"
    ).encode("utf-8", errors="strict")


def _replay_candidate(snapshot: BoardIRSnapshot, candidate: RouteCandidate) -> None:
    request = RouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=candidate.patch.net_id,
        layer_id=candidate.patch.layer_id,
        seed=candidate.seed,
        settings=candidate.settings,
    )
    replay = AStarRouter().propose(snapshot, request)
    if replay.candidate != candidate:
        raise KiCadRoutePatchError("candidate does not match a deterministic router replay")


def render_kicad_candidate_board(
    source: bytes,
    snapshot: BoardIRSnapshot,
    candidate: RouteCandidate,
    profile: KiCadConstraintProfile,
    *,
    limits: ParseLimits | None = None,
) -> bytes:
    """Render a verified candidate into new bytes without mutating or writing its source board.

    This function is a serialization boundary, not an apply operation and not DRC evidence. It
    accepts only a source/profile pair that reproduces ``snapshot`` exactly and only the byte-exact
    candidate reproduced by the bounded reference router. The returned bytes are parsed again and
    must differ from the input Board IR solely by source identity and the appended track segments.
    """

    limits = limits or ParseLimits()
    if not isinstance(source, bytes):
        raise KiCadRoutePatchError("KiCad source must be immutable bytes")
    if not isinstance(snapshot, BoardIRSnapshot):
        raise KiCadRoutePatchError("board snapshot is malformed")
    if not isinstance(candidate, RouteCandidate):
        raise KiCadRoutePatchError("route candidate is malformed")
    if not isinstance(profile, KiCadConstraintProfile):
        raise KiCadRoutePatchError("KiCad constraint profile is malformed")
    if not isinstance(limits, ParseLimits):
        raise KiCadRoutePatchError("parse limits are malformed")

    conversion = parse_kicad_bytes(source, profile, limits)
    if conversion.snapshot is None or conversion.diagnostics:
        raise KiCadRoutePatchError("KiCad source cannot be represented by the supported Board IR")
    if conversion.snapshot != snapshot:
        raise KiCadRoutePatchError("KiCad source and constraint profile do not match the snapshot")
    if candidate.base_revision != snapshot.snapshot_digest:
        raise KiCadRoutePatchError("candidate is stale for the supplied board snapshot")
    try:
        verify_candidate_id(candidate)
    except ValueError as error:
        raise KiCadRoutePatchError("candidate identity verification failed") from error
    _replay_candidate(snapshot, candidate)

    nets = {item.id: item for item in snapshot.content.nets}
    layers = {item.id: item for item in snapshot.content.copper_layers}
    net = nets.get(candidate.patch.net_id)
    layer = layers.get(candidate.patch.layer_id)
    if net is None or layer is None:
        raise KiCadRoutePatchError("candidate references an unknown net or copper layer")

    edge_count = len(candidate.patch.vertices) - 1
    if edge_count > limits.max_objects:
        raise KiCadRoutePatchError("candidate segment count exceeds the configured object budget")

    stripped = source.rstrip(b" \t\r\n")
    if not stripped or stripped[-1:] != b")":
        raise KiCadRoutePatchError("KiCad source has no supported root closing delimiter")
    closing_index = len(stripped) - 1
    prefix = source[:closing_index]
    suffix = source[closing_index:]
    separator = b"" if prefix.endswith(b"\n") else b"\n"

    rendered_segments: list[bytes] = []
    expected_segments: list[Segment] = []
    output_size = len(prefix) + len(separator) + len(suffix)
    for index, (start, end) in enumerate(pairwise(candidate.patch.vertices)):
        native_uuid = _segment_uuid(candidate.candidate_id, index)
        if native_uuid.encode("ascii") in source:
            raise KiCadRoutePatchError(
                "deterministic route identity collides with the source board"
            )
        rendered = _render_segment(
            start_x_nm=start.x,
            start_y_nm=start.y,
            end_x_nm=end.x,
            end_y_nm=end.y,
            width_nm=candidate.patch.width_nm,
            layer_name=layer.name,
            net_name=net.name,
            segment_uuid=native_uuid,
        )
        output_size += len(rendered)
        if output_size > limits.max_input_bytes:
            raise KiCadRoutePatchError("rendered candidate board exceeds the input-byte budget")
        rendered_segments.append(rendered)
        expected_segments.append(
            Segment(
                id=f"segment:kicad:{native_uuid}",
                net_id=net.id,
                layer_id=layer.id,
                start=start,
                end=end,
                width_nm=candidate.patch.width_nm,
            )
        )

    rendered_board = prefix + separator + b"".join(rendered_segments) + suffix
    patched = parse_kicad_bytes(rendered_board, profile, limits)
    if patched.snapshot is None or patched.diagnostics:
        raise KiCadRoutePatchError("rendered candidate board failed Board IR round-trip parsing")
    expected_content = replace(
        snapshot.content,
        source=patched.snapshot.content.source,
        segments=tuple(
            sorted(
                snapshot.content.segments + tuple(expected_segments),
                key=lambda item: item.id,
            )
        ),
    )
    if patched.snapshot.content != expected_content:
        raise KiCadRoutePatchError(
            "rendered candidate board changed content outside its route patch"
        )
    return rendered_board
