"""Restricted canonical JSON encoding and content-addressed Board IR factories."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TypeAlias

from copper_mcp.board_ir.types import (
    BOARD_IR_SCHEMA,
    BOARD_IR_SCHEMA_VERSION,
    Arc,
    BoardIRContent,
    BoardIRSnapshot,
    ConstraintSet,
    Keepout,
    Layer,
    Net,
    OutlineContour,
    Pad,
    PointNM,
    Ring,
    Segment,
    SourceInfo,
    UnitSystem,
    Via,
    Zone,
    signed_double_area,
)
from copper_mcp.board_ir.validation import BoardIRValidationError, validate_content

JsonValue: TypeAlias = bool | int | str | list["JsonValue"] | dict[str, "JsonValue"] | None
_EMPTY_DIGEST = f"sha256:{'0' * 64}"


def _canonical_json(value: JsonValue) -> bytes:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return rendered.encode("utf-8", errors="strict") + b"\n"


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _point(point: PointNM) -> dict[str, JsonValue]:
    return {"x_nm": point.x, "y_nm": point.y}


def _normalize_ring(ring: Ring, *, clockwise: bool) -> tuple[PointNM, ...]:
    points = ring.points
    is_clockwise = signed_double_area(points) < 0
    if is_clockwise != clockwise:
        points = tuple(reversed(points))
    least = min(range(len(points)), key=lambda index: points[index])
    return points[least:] + points[:least]


def _ring(ring: Ring, *, clockwise: bool = False) -> dict[str, JsonValue]:
    return {"points": [_point(point) for point in _normalize_ring(ring, clockwise=clockwise)]}


def _layer_order(content: BoardIRContent) -> dict[str, int]:
    return {
        layer.id: order
        for order, layer in enumerate(sorted(content.copper_layers, key=lambda x: x.index))
    }


def _ordered_layers(layer_ids: tuple[str, ...], order: dict[str, int]) -> list[JsonValue]:
    ordered: list[str] = sorted(layer_ids, key=lambda layer_id: (order[layer_id], layer_id))
    result: list[JsonValue] = []
    result.extend(ordered)
    return result


def _constraints(constraints: ConstraintSet) -> dict[str, JsonValue]:
    return {
        "assignments": [
            {"net_class_id": item.net_class_id, "net_id": item.net_id}
            for item in sorted(constraints.assignments, key=lambda item: item.net_id)
        ],
        "differential_pairs": [
            {
                "gap_nm": item.gap_nm,
                "id": item.id,
                "max_skew_nm": item.max_skew_nm,
                "negative_net_id": item.negative_net_id,
                "positive_net_id": item.positive_net_id,
                "width_nm": item.width_nm,
            }
            for item in sorted(constraints.differential_pairs, key=lambda item: item.id)
        ],
        "length_rules": [
            {
                "id": item.id,
                "maximum_nm": item.maximum_nm,
                "minimum_nm": item.minimum_nm,
                "net_id": item.net_id,
            }
            for item in sorted(constraints.length_rules, key=lambda item: item.id)
        ],
        "net_classes": [
            {
                "clearance_nm": item.clearance_nm,
                "id": item.id,
                "name": item.name,
                "track_width_nm": item.track_width_nm,
                "via_diameter_nm": item.via_diameter_nm,
                "via_drill_nm": item.via_drill_nm,
            }
            for item in sorted(constraints.net_classes, key=lambda item: item.id)
        ],
    }


def _outline(contour: OutlineContour) -> dict[str, JsonValue]:
    return {
        "holes": [_ring(hole, clockwise=True) for hole in contour.holes],
        "id": contour.id,
        "outer": _ring(contour.outer),
    }


def _pad(item: Pad, order: dict[str, int]) -> dict[str, JsonValue]:
    return {
        "center": _point(item.center),
        "drill_x_nm": item.drill_x_nm,
        "drill_y_nm": item.drill_y_nm,
        "id": item.id,
        "kind": item.kind.value,
        "layer_ids": _ordered_layers(item.layer_ids, order),
        "locked": item.locked,
        "net_id": item.net_id,
        "rotation_udeg": item.rotation_udeg,
        "roundrect_radius_nm": item.roundrect_radius_nm,
        "shape": item.shape.value,
        "size_x_nm": item.size_x_nm,
        "size_y_nm": item.size_y_nm,
    }


def _via(item: Via) -> dict[str, JsonValue]:
    return {
        "center": _point(item.center),
        "diameter_nm": item.diameter_nm,
        "drill_nm": item.drill_nm,
        "end_layer_id": item.end_layer_id,
        "id": item.id,
        "kind": item.kind.value,
        "locked": item.locked,
        "net_id": item.net_id,
        "start_layer_id": item.start_layer_id,
    }


def _segment(item: Segment) -> dict[str, JsonValue]:
    return {
        "end": _point(item.end),
        "id": item.id,
        "layer_id": item.layer_id,
        "locked": item.locked,
        "net_id": item.net_id,
        "start": _point(item.start),
        "width_nm": item.width_nm,
    }


def _arc(item: Arc) -> dict[str, JsonValue]:
    return {
        "end": _point(item.end),
        "id": item.id,
        "layer_id": item.layer_id,
        "locked": item.locked,
        "mid": _point(item.mid),
        "net_id": item.net_id,
        "start": _point(item.start),
        "width_nm": item.width_nm,
    }


def _zone(item: Zone) -> dict[str, JsonValue]:
    return {
        "boundary": _ring(item.boundary),
        "clearance_nm": item.clearance_nm,
        "fill_mode": item.fill_mode,
        "id": item.id,
        "layer_id": item.layer_id,
        "locked": item.locked,
        "min_thickness_nm": item.min_thickness_nm,
        "net_id": item.net_id,
        "thermal_bridge_width_nm": item.thermal_bridge_width_nm,
        "thermal_gap_nm": item.thermal_gap_nm,
    }


def _keepout(item: Keepout, order: dict[str, int]) -> dict[str, JsonValue]:
    return {
        "boundary": _ring(item.boundary),
        "id": item.id,
        "layer_ids": _ordered_layers(item.layer_ids, order),
        "locked": item.locked,
        "prohibit_footprints": item.prohibit_footprints,
        "prohibit_pads": item.prohibit_pads,
        "prohibit_tracks": item.prohibit_tracks,
        "prohibit_vias": item.prohibit_vias,
        "prohibit_zones": item.prohibit_zones,
    }


def _content_payload(content: BoardIRContent) -> dict[str, JsonValue]:
    order = _layer_order(content)
    return {
        "constraint_digest": content.constraint_digest,
        "constraints": _constraints(content.constraints),
        "copper_layers": [
            {"id": layer.id, "index": layer.index, "kind": layer.kind, "name": layer.name}
            for layer in sorted(content.copper_layers, key=lambda item: item.index)
        ],
        "items": {
            "arcs": [_arc(item) for item in sorted(content.arcs, key=lambda item: item.id)],
            "keepouts": [
                _keepout(item, order) for item in sorted(content.keepouts, key=lambda item: item.id)
            ],
            "pads": [_pad(item, order) for item in sorted(content.pads, key=lambda item: item.id)],
            "segments": [
                _segment(item) for item in sorted(content.segments, key=lambda item: item.id)
            ],
            "vias": [_via(item) for item in sorted(content.vias, key=lambda item: item.id)],
            "zones": [_zone(item) for item in sorted(content.zones, key=lambda item: item.id)],
        },
        "nets": [
            {"id": net.id, "name": net.name}
            for net in sorted(content.nets, key=lambda item: item.id)
        ],
        "outline": {
            "contours": [
                _outline(contour) for contour in sorted(content.outline, key=lambda item: item.id)
            ]
        },
        "source": {
            "format": content.source.format,
            "format_version": content.source.format_version,
            "generator": content.source.generator,
            "revision": content.source.revision,
        },
        "units": {"angle": content.units.angle, "distance": content.units.distance},
    }


def _constraint_payload(content: BoardIRContent) -> dict[str, JsonValue]:
    return {
        "constraints": _constraints(content.constraints),
        "nets": [{"id": net.id} for net in sorted(content.nets, key=lambda item: item.id)],
    }


def normalize_content(content: BoardIRContent) -> BoardIRContent:
    """Return an equivalent body with canonical tuple, ring, and layer-set ordering."""

    layers = tuple(sorted(content.copper_layers, key=lambda item: item.index))
    layer_order = {layer.id: layer.index for layer in layers}
    normalized_constraints = replace(
        content.constraints,
        net_classes=tuple(sorted(content.constraints.net_classes, key=lambda item: item.id)),
        assignments=tuple(sorted(content.constraints.assignments, key=lambda item: item.net_id)),
        differential_pairs=tuple(
            sorted(content.constraints.differential_pairs, key=lambda item: item.id)
        ),
        length_rules=tuple(sorted(content.constraints.length_rules, key=lambda item: item.id)),
    )
    contours = tuple(
        sorted(
            (
                replace(
                    contour,
                    outer=Ring(_normalize_ring(contour.outer, clockwise=False)),
                    holes=tuple(
                        sorted(
                            (Ring(_normalize_ring(hole, clockwise=True)) for hole in contour.holes),
                            key=lambda ring: ring.points,
                        )
                    ),
                )
                for contour in content.outline
            ),
            key=lambda item: item.id,
        )
    )

    def ordered_layers(references: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(references, key=lambda layer_id: (layer_order[layer_id], layer_id)))

    return replace(
        content,
        outline=contours,
        copper_layers=layers,
        nets=tuple(sorted(content.nets, key=lambda item: item.id)),
        constraints=normalized_constraints,
        pads=tuple(
            sorted(
                (replace(item, layer_ids=ordered_layers(item.layer_ids)) for item in content.pads),
                key=lambda item: item.id,
            )
        ),
        vias=tuple(
            sorted(
                (
                    replace(
                        item,
                        start_layer_id=min(
                            (item.start_layer_id, item.end_layer_id), key=layer_order.__getitem__
                        ),
                        end_layer_id=max(
                            (item.start_layer_id, item.end_layer_id), key=layer_order.__getitem__
                        ),
                    )
                    for item in content.vias
                ),
                key=lambda item: item.id,
            )
        ),
        segments=tuple(sorted(content.segments, key=lambda item: item.id)),
        arcs=tuple(sorted(content.arcs, key=lambda item: item.id)),
        zones=tuple(
            sorted(
                (
                    replace(item, boundary=Ring(_normalize_ring(item.boundary, clockwise=False)))
                    for item in content.zones
                ),
                key=lambda item: item.id,
            )
        ),
        keepouts=tuple(
            sorted(
                (
                    replace(
                        item,
                        layer_ids=ordered_layers(item.layer_ids),
                        boundary=Ring(_normalize_ring(item.boundary, clockwise=False)),
                    )
                    for item in content.keepouts
                ),
                key=lambda item: item.id,
            )
        ),
    )


def constraint_digest(content: BoardIRContent) -> str:
    """Hash the exact routing-constraint projection of a Board IR body."""

    return _digest(_canonical_json(_constraint_payload(content)))


def canonical_content_bytes(content: BoardIRContent) -> bytes:
    """Encode the validated snapshot body using restricted canonical JSON."""

    validate_content(content)
    expected = constraint_digest(content)
    if content.constraint_digest != expected:
        raise BoardIRValidationError(
            "digest.constraint_mismatch", "constraint digest does not match content"
        )
    return _canonical_json(_content_payload(content))


def make_content(
    *,
    source: SourceInfo,
    outline: tuple[OutlineContour, ...],
    copper_layers: tuple[Layer, ...],
    nets: tuple[Net, ...],
    constraints: ConstraintSet,
    pads: tuple[Pad, ...] = (),
    vias: tuple[Via, ...] = (),
    segments: tuple[Segment, ...] = (),
    arcs: tuple[Arc, ...] = (),
    zones: tuple[Zone, ...] = (),
    keepouts: tuple[Keepout, ...] = (),
) -> BoardIRContent:
    """Build and validate a body with its semantic constraint digest."""

    content = BoardIRContent(
        units=UnitSystem(),
        source=source,
        constraint_digest=_EMPTY_DIGEST,
        outline=outline,
        copper_layers=copper_layers,
        nets=nets,
        constraints=constraints,
        pads=pads,
        vias=vias,
        segments=segments,
        arcs=arcs,
        zones=zones,
        keepouts=keepouts,
    )
    validate_content(content)
    content = normalize_content(content)
    content = replace(content, constraint_digest=constraint_digest(content))
    validate_content(content)
    return content


def make_snapshot(content: BoardIRContent) -> BoardIRSnapshot:
    """Create a self-verifying snapshot envelope without a recursive hash."""

    digest = _digest(canonical_content_bytes(content))
    return BoardIRSnapshot(snapshot_digest=digest, content=content)


def verify_snapshot(snapshot: BoardIRSnapshot) -> bool:
    """Raise on a stale or forged digest and otherwise return true."""

    expected = _digest(canonical_content_bytes(snapshot.content))
    if snapshot.snapshot_digest != expected:
        raise BoardIRValidationError(
            "digest.snapshot_mismatch", "snapshot digest does not match canonical content"
        )
    return True


def encode_snapshot(snapshot: BoardIRSnapshot) -> bytes:
    """Encode a verified snapshot envelope as byte-stable canonical JSON."""

    verify_snapshot(snapshot)
    envelope: dict[str, JsonValue] = {
        "content": _content_payload(snapshot.content),
        "schema": BOARD_IR_SCHEMA,
        "schema_version": BOARD_IR_SCHEMA_VERSION,
        "snapshot_digest": snapshot.snapshot_digest,
    }
    return _canonical_json(envelope)
