"""Deterministic, disposable KiCad serialization for one replayed route candidate."""

from __future__ import annotations

import uuid
from dataclasses import replace
from itertools import pairwise

from copper_mcp import __version__
from copper_mcp.adapters.cst import CstError, Splice, apply_splices, line_indent, span
from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.sexpr import SExpr, SExprError, atoms, children, parse_sexpr
from copper_mcp.board_ir import BoardIRSnapshot, ParseLimits, Segment, nm_to_mm
from copper_mcp.routing import (
    AStarRouter,
    RouteCandidate,
    RouteRequest,
    verify_candidate_id,
)

_SEGMENT_NAMESPACE = uuid.UUID("0d904ca7-1130-4d38-a044-6ba5d92f357b")
_WRITER_ID = "copper-mcp"
_NATIVE_ID_HEADS = frozenset({"tstamp", "uuid"})


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


def _source_structure(source: bytes, limits: ParseLimits) -> tuple[SExpr, frozenset[str]]:
    """Parse once and collect all native identities for constant-time collision checks."""

    try:
        root = parse_sexpr(source, limits)
        native_identities: set[str] = set()
        pending = [root]
        while pending:
            expression = pending.pop()
            if expression.head in _NATIVE_ID_HEADS:
                values = atoms(expression)
                if len(values) != 1:
                    raise SExprError(
                        "syntax.invalid",
                        "native identity must contain exactly one atom",
                        expression.offset,
                    )
                native_identities.add(values[0].lower())
            pending.extend(item for item in expression.items[1:] if isinstance(item, SExpr))
    except SExprError as error:
        raise KiCadRoutePatchError("KiCad source identity scan failed") from error
    return root, frozenset(native_identities)


def _rewrite_writer_metadata(source: bytes, root: SExpr) -> bytes:
    """Identify CopperMCP as the writer of the disposable derivative."""

    text = source.decode("utf-8", errors="strict")
    newline = "\r\n" if "\r\n" in text else "\n"
    generators = children(root, "generator")
    generator_versions = children(root, "generator_version")
    versions = children(root, "version")
    if len(generators) > 1 or len(generator_versions) > 1 or len(versions) != 1:
        raise KiCadRoutePatchError("KiCad writer metadata is ambiguous")

    writer = f'(generator "{_quoted_atom(_WRITER_ID)}")'
    writer_version = f'(generator_version "{_quoted_atom(__version__)}")'
    replacements: list[Splice] = []

    if generators:
        generator_start, generator_end = span(generators[0], text)
        replacements.append(Splice(generator_start, generator_end, writer))
        if not generator_versions:
            indentation = line_indent(text, generator_start)
            replacements.append(
                Splice(generator_end, generator_end, f"{newline}{indentation}{writer_version}")
            )
    else:
        version_start, version_end = span(versions[0], text)
        indentation = line_indent(text, version_start)
        inserted = f"{newline}{indentation}{writer}"
        if not generator_versions:
            inserted += f"{newline}{indentation}{writer_version}"
        replacements.append(Splice(version_end, version_end, inserted))

    if generator_versions:
        version_start, version_end = span(generator_versions[0], text)
        replacements.append(Splice(version_start, version_end, writer_version))

    try:
        return apply_splices(text, replacements).encode("utf-8", errors="strict")
    except CstError as error:
        raise KiCadRoutePatchError("KiCad writer metadata could not be rewritten") from error


def _modeled_object_count(snapshot: BoardIRSnapshot) -> int:
    content = snapshot.content
    return sum(
        len(group)
        for group in (
            content.outline,
            content.copper_layers,
            content.nets,
            content.constraints.net_classes,
            content.constraints.assignments,
            content.constraints.differential_pairs,
            content.constraints.length_rules,
            content.footprints,
            content.pads,
            content.vias,
            content.segments,
            content.arcs,
            content.zones,
            content.keepouts,
        )
    )


def _require_native_geometry_identities(snapshot: BoardIRSnapshot) -> None:
    content = snapshot.content
    geometry_ids = (
        tuple(item.id for item in content.outline)
        + tuple(item.id for item in content.footprints)
        + tuple(item.id for item in content.pads)
        + tuple(item.id for item in content.vias)
        + tuple(item.id for item in content.segments)
        + tuple(item.id for item in content.arcs)
        + tuple(item.id for item in content.zones)
        + tuple(item.id for item in content.keepouts)
    )
    if any(":derived:" in identity for identity in geometry_ids):
        raise KiCadRoutePatchError(
            "modeled KiCad geometry requires native uuid or tstamp identities"
        )


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
    must differ from the input Board IR solely by source revision, CopperMCP writer provenance, and
    the appended track segments.
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
    _require_native_geometry_identities(snapshot)
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

    edge_count = sum(len(path.vertices) - 1 for path in candidate.patch.paths)
    if _modeled_object_count(snapshot) + edge_count > limits.max_objects:
        raise KiCadRoutePatchError("rendered board exceeds the configured object budget")

    root, native_identities = _source_structure(source, limits)
    writer_source = _rewrite_writer_metadata(source, root)

    stripped = writer_source.rstrip(b" \t\r\n")
    if not stripped or stripped[-1:] != b")":
        raise KiCadRoutePatchError("KiCad source has no supported root closing delimiter")
    closing_index = len(stripped) - 1
    prefix = writer_source[:closing_index]
    suffix = writer_source[closing_index:]
    separator = b"" if prefix.endswith(b"\n") else b"\n"

    rendered_segments: list[bytes] = []
    expected_segments: list[Segment] = []
    output_size = len(prefix) + len(separator) + len(suffix)
    if output_size > limits.max_input_bytes:
        raise KiCadRoutePatchError("rendered candidate board exceeds the input-byte budget")
    # One running index across every path, so a tree's segment identities stay unique and
    # reproducible in path-then-edge order.
    edges = [edge for path in candidate.patch.paths for edge in pairwise(path.vertices)]
    for index, (start, end) in enumerate(edges):
        native_uuid = _segment_uuid(candidate.candidate_id, index)
        if native_uuid in native_identities:
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
    expected_source = replace(
        snapshot.content.source,
        revision=patched.snapshot.content.source.revision,
        generator=_WRITER_ID,
    )
    expected_content = replace(
        snapshot.content,
        source=expected_source,
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
