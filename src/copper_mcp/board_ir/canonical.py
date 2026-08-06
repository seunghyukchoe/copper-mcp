"""Restricted canonical JSON encoding and content-addressed Board IR factories."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TypeAlias

from copper_mcp.board_ir.limits import ParseBudget, ParseLimits
from copper_mcp.board_ir.types import (
    BOARD_IR_SCHEMA,
    BOARD_IR_SCHEMA_VERSION,
    Arc,
    BoardIRContent,
    BoardIRSnapshot,
    ConstraintSet,
    Footprint,
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


def _footprint(item: Footprint) -> dict[str, JsonValue]:
    return {
        "courtyards": [_ring(courtyard) for courtyard in item.courtyards],
        "id": item.id,
        "locked": item.locked,
        "origin": _point(item.origin),
        "pad_ids": list(item.pad_ids),
        "rotation_udeg": item.rotation_udeg,
        "side": item.side.value,
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
        "island_removal": item.island_removal.value,
        "layer_id": item.layer_id,
        "locked": item.locked,
        "min_thickness_nm": item.min_thickness_nm,
        "net_id": item.net_id,
        "pad_connection": item.pad_connection.value,
        "priority": item.priority,
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
            "footprints": [
                _footprint(item) for item in sorted(content.footprints, key=lambda item: item.id)
            ],
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
        footprints=tuple(
            sorted(
                (
                    replace(
                        item,
                        pad_ids=tuple(sorted(item.pad_ids)),
                        courtyards=tuple(
                            sorted(
                                (
                                    Ring(_normalize_ring(courtyard, clockwise=False))
                                    for courtyard in item.courtyards
                                ),
                                key=lambda ring: ring.points,
                            )
                        ),
                    )
                    for item in content.footprints
                ),
                key=lambda item: item.id,
            )
        ),
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


def _canonicalized_content(content: BoardIRContent) -> BoardIRContent:
    validate_content(content)
    normalized = normalize_content(content)
    validate_content(normalized)
    expected = constraint_digest(normalized)
    if normalized.constraint_digest != expected:
        raise BoardIRValidationError(
            "digest.constraint_mismatch", "constraint digest does not match content"
        )
    return normalized


def canonical_content_bytes(content: BoardIRContent) -> bytes:
    """Encode the validated snapshot body using restricted canonical JSON."""

    normalized = _canonicalized_content(content)
    return _canonical_json(_content_payload(normalized))


def make_content(
    *,
    source: SourceInfo,
    outline: tuple[OutlineContour, ...],
    copper_layers: tuple[Layer, ...],
    nets: tuple[Net, ...],
    constraints: ConstraintSet,
    footprints: tuple[Footprint, ...] = (),
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
        footprints=footprints,
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

    normalized = _canonicalized_content(content)
    digest = _digest(_canonical_json(_content_payload(normalized)))
    snapshot = BoardIRSnapshot(snapshot_digest=digest, content=normalized)
    _encode_envelope(snapshot, enforce_default_budget=True)
    return snapshot


def verify_snapshot(snapshot: BoardIRSnapshot) -> bool:
    """Raise on a stale or forged digest and otherwise return true."""

    normalized = _canonicalized_content(snapshot.content)
    if normalized != snapshot.content:
        raise BoardIRValidationError(
            "canonical.not_normalized",
            "snapshot content is not in canonical Board IR order",
        )
    expected = _digest(_canonical_json(_content_payload(normalized)))
    if snapshot.snapshot_digest != expected:
        raise BoardIRValidationError(
            "digest.snapshot_mismatch", "snapshot digest does not match canonical content"
        )
    return True


def _enforce_default_budget(value: JsonValue, payload: bytes) -> None:
    """Keep public writer output consumable by the default untrusted decoder."""

    limits = ParseLimits()
    if len(payload) > limits.max_input_bytes:
        raise BoardIRValidationError(
            ParseBudget.INPUT_BYTES.value,
            "canonical snapshot exceeds the default byte budget",
            "snapshot",
        )
    stack: list[tuple[JsonValue, int]] = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            raise BoardIRValidationError(
                ParseBudget.NODES.value,
                "canonical snapshot exceeds the default node budget",
                "snapshot",
            )
        if depth > limits.max_depth:
            raise BoardIRValidationError(
                ParseBudget.DEPTH.value,
                "canonical snapshot exceeds the default depth budget",
                "snapshot",
            )
        if isinstance(item, str) and len(item) > limits.max_atom_chars:
            raise BoardIRValidationError(
                ParseBudget.ATOM_CHARS.value,
                "canonical snapshot exceeds the default string budget",
                "snapshot",
            )
        if isinstance(item, list):
            if len(item) > limits.max_children_per_list:
                raise BoardIRValidationError(
                    ParseBudget.CHILDREN_PER_LIST.value,
                    "canonical snapshot exceeds the default array-child budget",
                    "snapshot",
                )
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, dict):
            if len(item) > limits.max_children_per_list:
                raise BoardIRValidationError(
                    ParseBudget.CHILDREN_PER_LIST.value,
                    "canonical snapshot exceeds the default object-child budget",
                    "snapshot",
                )
            for key in item:
                if len(key) > limits.max_atom_chars:
                    raise BoardIRValidationError(
                        ParseBudget.ATOM_CHARS.value,
                        "canonical snapshot exceeds the default string budget",
                        "snapshot",
                    )
            stack.extend((child, depth + 1) for child in item.values())


def _encode_envelope(snapshot: BoardIRSnapshot, *, enforce_default_budget: bool) -> bytes:
    envelope: dict[str, JsonValue] = {
        "content": _content_payload(snapshot.content),
        "schema": BOARD_IR_SCHEMA,
        "schema_version": BOARD_IR_SCHEMA_VERSION,
        "snapshot_digest": snapshot.snapshot_digest,
    }
    payload = _canonical_json(envelope)
    if enforce_default_budget:
        _enforce_default_budget(envelope, payload)
    return payload


def encode_snapshot(snapshot: BoardIRSnapshot) -> bytes:
    """Encode a verified snapshot envelope as byte-stable canonical JSON."""

    verify_snapshot(snapshot)
    return _encode_envelope(snapshot, enforce_default_budget=True)
