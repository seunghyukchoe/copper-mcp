"""Private, disposable serialization of a verified route-bundle plan.

This is intentionally not an apply or export surface. It exists solely to support bounded
authoritative KiCad checks on committed public fixtures. The caller owns the private output bytes.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from itertools import pairwise

from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.kicad_route_patch import (
    _WRITER_ID as _ROUTE_WRITER_ID,
)
from copper_mcp.adapters.kicad_route_patch import (
    KiCadRoutePatchError,
    _modeled_object_count,
    _render_segment,
    _require_native_geometry_identities,
    _rewrite_writer_metadata,
    _source_structure,
)
from copper_mcp.board_ir import BoardIRSnapshot, ParseLimits, Segment
from copper_mcp.route_bundle import RouteBundlePlan
from copper_mcp.routing.physical_clearance import verify_negotiated_physical_clearance


def render_kicad_route_bundle_board(
    source: bytes,
    snapshot: BoardIRSnapshot,
    plan: RouteBundlePlan,
    profile: KiCadConstraintProfile,
    *,
    limits: ParseLimits | None = None,
) -> bytes:
    """Render every already-composed route once into disposable bytes, without writing source."""

    limits = limits or ParseLimits()
    if not isinstance(source, bytes) or not isinstance(snapshot, BoardIRSnapshot):
        raise KiCadRoutePatchError("route-bundle serialization inputs are malformed")
    if not isinstance(plan, RouteBundlePlan) or not isinstance(profile, KiCadConstraintProfile):
        raise KiCadRoutePatchError("route-bundle plan or profile is malformed")
    conversion = parse_kicad_bytes(source, profile, limits)
    if conversion.snapshot is None or conversion.diagnostics or conversion.snapshot != snapshot:
        raise KiCadRoutePatchError("KiCad source and constraint profile do not match the snapshot")
    if plan.base_revision != snapshot.snapshot_digest:
        raise KiCadRoutePatchError("route bundle is stale for the supplied board snapshot")
    _require_native_geometry_identities(snapshot)
    physical = verify_negotiated_physical_clearance(
        snapshot,
        plan.candidates,
        layer_id=plan.layer_id,
        max_pair_checks=10_000_000,
    )
    if not physical.accepted:
        raise KiCadRoutePatchError("route bundle fails physical-clearance replay")

    nets = {item.id: item for item in snapshot.content.nets}
    layers = {item.id: item for item in snapshot.content.copper_layers}
    edge_count = sum(
        len(path.vertices) - 1 for candidate in plan.candidates for path in candidate.patch.paths
    )
    if _modeled_object_count(snapshot) + edge_count > limits.max_objects:
        raise KiCadRoutePatchError("rendered route bundle exceeds the configured object budget")
    root, native_identities = _source_structure(source, limits)
    writer_source = _rewrite_writer_metadata(source, root)
    stripped = writer_source.rstrip(b" \t\r\n")
    if not stripped or stripped[-1:] != b")":
        raise KiCadRoutePatchError("KiCad source has no supported root closing delimiter")
    prefix = writer_source[: len(stripped) - 1]
    suffix = writer_source[len(stripped) - 1 :]
    separator = b"" if prefix.endswith(b"\n") else b"\n"
    output_size = len(prefix) + len(separator) + len(suffix)
    rendered_segments: list[bytes] = []
    expected_segments: list[Segment] = []
    for index, candidate in enumerate(plan.candidates):
        net = nets.get(candidate.patch.net_id)
        layer = layers.get(candidate.patch.layer_id)
        if net is None or layer is None:
            raise KiCadRoutePatchError("route bundle references an unknown net or layer")
        for path_index, path in enumerate(candidate.patch.paths):
            for edge_index, (start, end) in enumerate(pairwise(path.vertices)):
                segment_uuid = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"copper-mcp-route-bundle:{plan.bundle_id}:{index}:{path_index}:{edge_index}",
                    )
                )
                if segment_uuid in native_identities:
                    raise KiCadRoutePatchError(
                        "route bundle identity collides with the source board"
                    )
                rendered = _render_segment(
                    start_x_nm=start.x,
                    start_y_nm=start.y,
                    end_x_nm=end.x,
                    end_y_nm=end.y,
                    width_nm=candidate.patch.width_nm,
                    layer_name=layer.name,
                    net_name=net.name,
                    segment_uuid=segment_uuid,
                )
                output_size += len(rendered)
                if output_size > limits.max_input_bytes:
                    raise KiCadRoutePatchError(
                        "rendered route bundle exceeds the input-byte budget"
                    )
                rendered_segments.append(rendered)
                expected_segments.append(
                    Segment(
                        id=f"segment:kicad:{segment_uuid}",
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
        raise KiCadRoutePatchError("rendered route bundle failed Board IR round-trip parsing")
    expected_source = replace(
        snapshot.content.source,
        revision=patched.snapshot.content.source.revision,
        generator=_ROUTE_WRITER_ID,
    )
    expected_content = replace(
        snapshot.content,
        source=expected_source,
        segments=tuple(
            sorted(snapshot.content.segments + tuple(expected_segments), key=lambda item: item.id)
        ),
    )
    if patched.snapshot.content != expected_content:
        raise KiCadRoutePatchError(
            "rendered route bundle changed content outside its route patches"
        )
    return rendered_board


__all__ = ["render_kicad_route_bundle_board"]
