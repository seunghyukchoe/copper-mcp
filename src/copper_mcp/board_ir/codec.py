"""Strict JSON decoder for the versioned Board IR v0.2 envelope."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Never, TypeVar

from copper_mcp.board_ir.canonical import normalize_content, verify_snapshot
from copper_mcp.board_ir.limits import BUDGET_EXCEEDED_PREFIX, ParseBudget, ParseLimits
from copper_mcp.board_ir.types import (
    BOARD_IR_SCHEMA,
    BOARD_IR_SCHEMA_VERSION,
    JSON_SAFE_INTEGER,
    Arc,
    BoardIRContent,
    BoardIRSnapshot,
    ConstraintSet,
    CourtyardCircle,
    DifferentialPairRule,
    Footprint,
    FootprintSide,
    Keepout,
    Layer,
    LengthRule,
    Net,
    NetClass,
    NetClassAssignment,
    OutlineContour,
    Pad,
    PadKind,
    PadShape,
    PointNM,
    Ring,
    Segment,
    SourceInfo,
    UnitSystem,
    Via,
    ViaKind,
    Zone,
    ZoneIslandRemoval,
    ZonePadConnection,
)
from copper_mcp.board_ir.validation import BoardIRValidationError, validate_content

_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


@dataclass(slots=True)
class _JSONFrame:
    kind: Literal["array", "object"]
    state: str
    children: int = 0
    keys: set[str] = field(default_factory=set)


def _json_tokens(text: str, limits: ParseLimits) -> Iterator[tuple[str, str | None]]:
    """Yield lexical JSON tokens while bounding decoded string atoms."""

    index = 0
    length = len(text)
    simple_escapes = frozenset('"\\/bfnrt')
    while index < length:
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if character in "{}[]:,":
            index += 1
            yield character, None
            continue
        if character == '"':
            start = index
            index += 1
            decoded_chars = 0
            while index < length and text[index] != '"':
                character = text[index]
                if ord(character) < 0x20:
                    raise ValueError("JSON string contains a control character")
                if character == "\\":
                    index += 1
                    if index >= length:
                        raise ValueError("JSON string escape is unterminated")
                    escape = text[index]
                    if escape in simple_escapes:
                        index += 1
                    elif escape == "u":
                        digits = text[index + 1 : index + 5]
                        if len(digits) != 4 or any(
                            digit not in "0123456789abcdefABCDEF" for digit in digits
                        ):
                            raise ValueError("JSON unicode escape is malformed")
                        index += 5
                    else:
                        raise ValueError("JSON string escape is unsupported")
                else:
                    index += 1
                decoded_chars += 1
                if decoded_chars > limits.max_atom_chars:
                    raise BoardIRValidationError(
                        ParseBudget.ATOM_CHARS.value, "JSON string budget exceeded", "json"
                    )
            if index >= length:
                raise ValueError("JSON string is unterminated")
            index += 1
            decoded = json.loads(text[start:index])
            if not isinstance(decoded, str):
                raise ValueError("JSON string token is malformed")
            yield "string", decoded
            continue
        if character == "-" or character.isdigit():
            match = _JSON_NUMBER.match(text, index)
            if match is None:
                raise ValueError("JSON number is malformed")
            end = match.end()
            if end < length and not (text[end].isspace() or text[end] in ",]}:"):
                raise ValueError("JSON number is malformed")
            index = end
            yield "scalar", None
            continue
        matched_literal = False
        for literal in ("true", "false", "null"):
            if not text.startswith(literal, index):
                continue
            end = index + len(literal)
            if end < length and not (text[end].isspace() or text[end] in ",]}:"):
                raise ValueError("JSON literal is malformed")
            index = end
            yield "scalar", None
            matched_literal = True
            break
        if matched_literal:
            continue
        raise ValueError("JSON token is malformed")


def _preflight_json(text: str, limits: ParseLimits) -> None:
    """Enforce structural budgets and duplicate keys before allocating a JSON DOM."""

    stack: list[_JSONFrame] = []
    root_seen = False
    nodes = 0

    def start_value(kind: str) -> None:
        nonlocal nodes
        if kind not in {"string", "scalar", "{", "["}:
            raise ValueError("JSON value is malformed")
        depth = len(stack) + 1
        if depth > limits.max_depth:
            raise BoardIRValidationError(
                ParseBudget.DEPTH.value, "JSON depth budget exceeded", "json"
            )
        nodes += 1
        if nodes > limits.max_nodes:
            raise BoardIRValidationError(
                ParseBudget.NODES.value, "JSON node budget exceeded", "json"
            )
        if kind == "{":
            stack.append(_JSONFrame("object", "key_or_end"))
        elif kind == "[":
            stack.append(_JSONFrame("array", "value_or_end"))

    for kind, value in _json_tokens(text, limits):
        if not stack:
            if root_seen:
                raise ValueError("JSON contains more than one root value")
            root_seen = True
            start_value(kind)
            continue

        frame = stack[-1]
        if frame.kind == "object":
            if frame.state in {"key_or_end", "key"}:
                if kind == "}" and frame.state == "key_or_end":
                    stack.pop()
                    continue
                if kind != "string" or value is None:
                    raise ValueError("JSON object property is malformed")
                frame.children += 1
                if frame.children > limits.max_children_per_list:
                    raise BoardIRValidationError(
                        ParseBudget.CHILDREN_PER_LIST.value,
                        "JSON object child budget exceeded",
                        "json",
                    )
                if value in frame.keys:
                    raise ValueError("JSON object contains a duplicate property")
                frame.keys.add(value)
                frame.state = "colon"
                continue
            if frame.state == "colon":
                if kind != ":":
                    raise ValueError("JSON object property is malformed")
                frame.state = "value"
                continue
            if frame.state == "value":
                frame.state = "comma_or_end"
                start_value(kind)
                continue
            if kind == ",":
                frame.state = "key"
                continue
            if kind == "}":
                stack.pop()
                continue
            raise ValueError("JSON object separator is malformed")

        if frame.state in {"value_or_end", "value"}:
            if kind == "]" and frame.state == "value_or_end":
                stack.pop()
                continue
            frame.children += 1
            if frame.children > limits.max_children_per_list:
                raise BoardIRValidationError(
                    ParseBudget.CHILDREN_PER_LIST.value,
                    "JSON array child budget exceeded",
                    "json",
                )
            frame.state = "comma_or_end"
            start_value(kind)
            continue
        if kind == ",":
            frame.state = "value"
            continue
        if kind == "]":
            stack.pop()
            continue
        raise ValueError("JSON array separator is malformed")

    if not root_seen or stack:
        raise ValueError("JSON structure is incomplete")


def _reject_float(_: str) -> Never:
    raise ValueError("floating-point numbers are not canonical Board IR")


def _parse_integer(token: str) -> int:
    if len(token.lstrip("-")) > 16:
        raise ValueError("integer token exceeds the interoperable range")
    value = int(token)
    if not -JSON_SAFE_INTEGER <= value <= JSON_SAFE_INTEGER:
        raise ValueError("integer token exceeds the interoperable range")
    return value


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object contains a duplicate property")
        result[key] = value
    return result


def _object(
    value: object,
    *,
    required: set[str],
    optional: set[str] | None = None,
    path: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{path} must be an object")
    typed = {str(key): item for key, item in value.items()}
    missing = required - typed.keys()
    allowed = required | (optional or set())
    extra = typed.keys() - allowed
    if missing:
        raise ValueError(f"{path} is missing {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"{path} contains an unknown property")
    return typed


def _array(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    return value


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    return value


def _optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    return value


def _optional_integer(value: object, path: str) -> int | None:
    if value is None:
        return None
    return _integer(value, path)


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _point(value: object, path: str) -> PointNM:
    item = _object(value, required={"x_nm", "y_nm"}, path=path)
    return PointNM(
        x=_integer(item["x_nm"], f"{path}.x_nm"), y=_integer(item["y_nm"], f"{path}.y_nm")
    )


def _ring(value: object, path: str) -> Ring:
    item = _object(value, required={"points"}, path=path)
    return Ring(
        tuple(
            _point(point, f"{path}.points[{index}]")
            for index, point in enumerate(_array(item["points"], f"{path}.points"))
        )
    )


T = TypeVar("T")


def _enum_value(enum_type: Callable[[str], T], value: object, path: str) -> T:
    try:
        return enum_type(_string(value, path))
    except ValueError as error:
        raise ValueError(f"{path} is unsupported") from error


def _constraints(value: object, path: str) -> ConstraintSet:
    item = _object(
        value,
        required={"net_classes", "assignments", "differential_pairs", "length_rules"},
        path=path,
    )
    net_classes = tuple(
        NetClass(
            id=_string(entry["id"], f"{entry_path}.id"),
            name=_string(entry["name"], f"{entry_path}.name"),
            clearance_nm=_integer(entry["clearance_nm"], f"{entry_path}.clearance_nm"),
            track_width_nm=_integer(entry["track_width_nm"], f"{entry_path}.track_width_nm"),
            via_diameter_nm=_integer(entry["via_diameter_nm"], f"{entry_path}.via_diameter_nm"),
            via_drill_nm=_integer(entry["via_drill_nm"], f"{entry_path}.via_drill_nm"),
        )
        for index, raw in enumerate(_array(item["net_classes"], f"{path}.net_classes"))
        for entry_path in (f"{path}.net_classes[{index}]",)
        for entry in (
            _object(
                raw,
                required={
                    "id",
                    "name",
                    "clearance_nm",
                    "track_width_nm",
                    "via_diameter_nm",
                    "via_drill_nm",
                },
                path=entry_path,
            ),
        )
    )
    assignments = tuple(
        NetClassAssignment(
            net_id=_string(entry["net_id"], f"{entry_path}.net_id"),
            net_class_id=_string(entry["net_class_id"], f"{entry_path}.net_class_id"),
        )
        for index, raw in enumerate(_array(item["assignments"], f"{path}.assignments"))
        for entry_path in (f"{path}.assignments[{index}]",)
        for entry in (_object(raw, required={"net_id", "net_class_id"}, path=entry_path),)
    )
    differential_pairs = tuple(
        DifferentialPairRule(
            id=_string(entry["id"], f"{entry_path}.id"),
            positive_net_id=_string(entry["positive_net_id"], f"{entry_path}.positive_net_id"),
            negative_net_id=_string(entry["negative_net_id"], f"{entry_path}.negative_net_id"),
            width_nm=_integer(entry["width_nm"], f"{entry_path}.width_nm"),
            gap_nm=_integer(entry["gap_nm"], f"{entry_path}.gap_nm"),
            max_skew_nm=_integer(entry["max_skew_nm"], f"{entry_path}.max_skew_nm"),
        )
        for index, raw in enumerate(
            _array(item["differential_pairs"], f"{path}.differential_pairs")
        )
        for entry_path in (f"{path}.differential_pairs[{index}]",)
        for entry in (
            _object(
                raw,
                required={
                    "id",
                    "positive_net_id",
                    "negative_net_id",
                    "width_nm",
                    "gap_nm",
                    "max_skew_nm",
                },
                path=entry_path,
            ),
        )
    )
    length_rules = tuple(
        LengthRule(
            id=_string(entry["id"], f"{entry_path}.id"),
            net_id=_string(entry["net_id"], f"{entry_path}.net_id"),
            minimum_nm=_integer(entry["minimum_nm"], f"{entry_path}.minimum_nm"),
            maximum_nm=_integer(entry["maximum_nm"], f"{entry_path}.maximum_nm"),
        )
        for index, raw in enumerate(_array(item["length_rules"], f"{path}.length_rules"))
        for entry_path in (f"{path}.length_rules[{index}]",)
        for entry in (
            _object(
                raw,
                required={"id", "net_id", "minimum_nm", "maximum_nm"},
                path=entry_path,
            ),
        )
    )
    return ConstraintSet(
        net_classes=net_classes,
        assignments=assignments,
        differential_pairs=differential_pairs,
        length_rules=length_rules,
    )


def _decode_content(value: object) -> BoardIRContent:
    item = _object(
        value,
        required={
            "units",
            "source",
            "constraint_digest",
            "outline",
            "copper_layers",
            "nets",
            "constraints",
            "items",
        },
        path="content",
    )
    units_raw = _object(item["units"], required={"distance", "angle"}, path="content.units")
    units = UnitSystem(
        distance=_string(units_raw["distance"], "content.units.distance"),
        angle=_string(units_raw["angle"], "content.units.angle"),
    )
    source_raw = _object(
        item["source"],
        required={"format", "revision", "format_version", "generator"},
        path="content.source",
    )
    source = SourceInfo(
        format=_string(source_raw["format"], "content.source.format"),
        revision=_string(source_raw["revision"], "content.source.revision"),
        format_version=_string(source_raw["format_version"], "content.source.format_version"),
        generator=_optional_string(source_raw["generator"], "content.source.generator"),
    )
    layers = tuple(
        Layer(
            id=_string(entry["id"], f"{entry_path}.id"),
            name=_string(entry["name"], f"{entry_path}.name"),
            index=_integer(entry["index"], f"{entry_path}.index"),
            kind=_string(entry["kind"], f"{entry_path}.kind"),
        )
        for index, raw in enumerate(_array(item["copper_layers"], "content.copper_layers"))
        for entry_path in (f"content.copper_layers[{index}]",)
        for entry in (_object(raw, required={"id", "name", "index", "kind"}, path=entry_path),)
    )
    nets = tuple(
        Net(
            id=_string(entry["id"], f"{entry_path}.id"),
            name=_string(entry["name"], f"{entry_path}.name"),
        )
        for index, raw in enumerate(_array(item["nets"], "content.nets"))
        for entry_path in (f"content.nets[{index}]",)
        for entry in (_object(raw, required={"id", "name"}, path=entry_path),)
    )
    outline_raw = _object(item["outline"], required={"contours"}, path="content.outline")
    outline = tuple(
        OutlineContour(
            id=_string(entry["id"], f"{entry_path}.id"),
            outer=_ring(entry["outer"], f"{entry_path}.outer"),
            holes=tuple(
                _ring(hole, f"{entry_path}.holes[{hole_index}]")
                for hole_index, hole in enumerate(_array(entry["holes"], f"{entry_path}.holes"))
            ),
        )
        for index, raw in enumerate(_array(outline_raw["contours"], "content.outline.contours"))
        for entry_path in (f"content.outline.contours[{index}]",)
        for entry in (_object(raw, required={"id", "outer", "holes"}, path=entry_path),)
    )
    items = _object(
        item["items"],
        required={"footprints", "pads", "vias", "segments", "arcs", "zones", "keepouts"},
        path="content.items",
    )

    def decode_courtyard_circle(raw: object, circle_path: str) -> CourtyardCircle:
        entry = _object(raw, required={"center", "radius_nm"}, path=circle_path)
        return CourtyardCircle(
            center=_point(entry["center"], f"{circle_path}.center"),
            radius_nm=_integer(entry["radius_nm"], f"{circle_path}.radius_nm"),
        )

    def decode_footprint(raw: object, entry_path: str) -> Footprint:
        entry = _object(
            raw,
            required={
                "id",
                "origin",
                "rotation_udeg",
                "side",
                "pad_ids",
                "courtyards",
                "locked",
            },
            # The canonical encoder omits each key entirely when the footprint has nothing to
            # put in it, so pre-existing snapshots decode unchanged and their digests hold.
            optional={
                "courtyard_circles",
                "far_side_courtyards",
                "far_side_courtyard_circles",
            },
            path=entry_path,
        )
        courtyard_values = _array(entry["courtyards"], f"{entry_path}.courtyards")
        circle_values = _array(
            entry.get("courtyard_circles", []), f"{entry_path}.courtyard_circles"
        )
        far_courtyard_values = _array(
            entry.get("far_side_courtyards", []), f"{entry_path}.far_side_courtyards"
        )
        far_circle_values = _array(
            entry.get("far_side_courtyard_circles", []),
            f"{entry_path}.far_side_courtyard_circles",
        )
        # One ceiling for the footprint, not one per courtyard layer: the adapter counts every
        # accepted shape against the same 64, and the two paths disagreeing about one rule was
        # the defect `schema.limit` was introduced to close.
        if (
            len(courtyard_values)
            + len(circle_values)
            + len(far_courtyard_values)
            + len(far_circle_values)
            > 64
        ):
            raise BoardIRValidationError(
                "schema.limit",
                "footprint courtyard limit exceeded",
                entry_path,
            )
        return Footprint(
            id=_string(entry["id"], f"{entry_path}.id"),
            origin=_point(entry["origin"], f"{entry_path}.origin"),
            rotation_udeg=_integer(entry["rotation_udeg"], f"{entry_path}.rotation_udeg"),
            side=_enum_value(FootprintSide, entry["side"], f"{entry_path}.side"),
            pad_ids=tuple(
                _string(pad_id, f"{entry_path}.pad_ids[{pad_index}]")
                for pad_index, pad_id in enumerate(
                    _array(entry["pad_ids"], f"{entry_path}.pad_ids")
                )
            ),
            courtyards=tuple(
                _ring(courtyard, f"{entry_path}.courtyards[{courtyard_index}]")
                for courtyard_index, courtyard in enumerate(courtyard_values)
            ),
            courtyard_circles=tuple(
                decode_courtyard_circle(circle, f"{entry_path}.courtyard_circles[{circle_index}]")
                for circle_index, circle in enumerate(circle_values)
            ),
            locked=_boolean(entry["locked"], f"{entry_path}.locked"),
            far_side_courtyards=tuple(
                _ring(courtyard, f"{entry_path}.far_side_courtyards[{courtyard_index}]")
                for courtyard_index, courtyard in enumerate(far_courtyard_values)
            ),
            far_side_courtyard_circles=tuple(
                decode_courtyard_circle(
                    circle, f"{entry_path}.far_side_courtyard_circles[{circle_index}]"
                )
                for circle_index, circle in enumerate(far_circle_values)
            ),
        )

    footprints = tuple(
        decode_footprint(raw, f"content.items.footprints[{index}]")
        for index, raw in enumerate(_array(items["footprints"], "content.items.footprints"))
    )
    pads = tuple(
        Pad(
            id=_string(entry["id"], f"{entry_path}.id"),
            net_id=_optional_string(entry["net_id"], f"{entry_path}.net_id"),
            center=_point(entry["center"], f"{entry_path}.center"),
            rotation_udeg=_integer(entry["rotation_udeg"], f"{entry_path}.rotation_udeg"),
            shape=_enum_value(PadShape, entry["shape"], f"{entry_path}.shape"),
            kind=_enum_value(PadKind, entry["kind"], f"{entry_path}.kind"),
            size_x_nm=_integer(entry["size_x_nm"], f"{entry_path}.size_x_nm"),
            size_y_nm=_integer(entry["size_y_nm"], f"{entry_path}.size_y_nm"),
            roundrect_radius_nm=_optional_integer(
                entry["roundrect_radius_nm"], f"{entry_path}.roundrect_radius_nm"
            ),
            drill_x_nm=_optional_integer(entry["drill_x_nm"], f"{entry_path}.drill_x_nm"),
            drill_y_nm=_optional_integer(entry["drill_y_nm"], f"{entry_path}.drill_y_nm"),
            layer_ids=tuple(
                _string(layer_id, f"{entry_path}.layer_ids[{layer_index}]")
                for layer_index, layer_id in enumerate(
                    _array(entry["layer_ids"], f"{entry_path}.layer_ids")
                )
            ),
            locked=_boolean(entry["locked"], f"{entry_path}.locked"),
        )
        for index, raw in enumerate(_array(items["pads"], "content.items.pads"))
        for entry_path in (f"content.items.pads[{index}]",)
        for entry in (
            _object(
                raw,
                required={
                    "id",
                    "net_id",
                    "center",
                    "rotation_udeg",
                    "shape",
                    "kind",
                    "size_x_nm",
                    "size_y_nm",
                    "roundrect_radius_nm",
                    "drill_x_nm",
                    "drill_y_nm",
                    "layer_ids",
                    "locked",
                },
                path=entry_path,
            ),
        )
    )
    vias = tuple(
        Via(
            id=_string(entry["id"], f"{entry_path}.id"),
            net_id=_optional_string(entry["net_id"], f"{entry_path}.net_id"),
            center=_point(entry["center"], f"{entry_path}.center"),
            diameter_nm=_integer(entry["diameter_nm"], f"{entry_path}.diameter_nm"),
            drill_nm=_integer(entry["drill_nm"], f"{entry_path}.drill_nm"),
            start_layer_id=_string(entry["start_layer_id"], f"{entry_path}.start_layer_id"),
            end_layer_id=_string(entry["end_layer_id"], f"{entry_path}.end_layer_id"),
            kind=_enum_value(ViaKind, entry["kind"], f"{entry_path}.kind"),
            locked=_boolean(entry["locked"], f"{entry_path}.locked"),
        )
        for index, raw in enumerate(_array(items["vias"], "content.items.vias"))
        for entry_path in (f"content.items.vias[{index}]",)
        for entry in (
            _object(
                raw,
                required={
                    "id",
                    "net_id",
                    "center",
                    "diameter_nm",
                    "drill_nm",
                    "start_layer_id",
                    "end_layer_id",
                    "kind",
                    "locked",
                },
                path=entry_path,
            ),
        )
    )

    def line_items(
        key: str, constructor: Callable[..., Segment | Arc]
    ) -> tuple[Segment | Arc, ...]:
        decoded: list[Segment | Arc] = []
        for index, raw in enumerate(_array(items[key], f"content.items.{key}")):
            entry_path = f"content.items.{key}[{index}]"
            required = {"id", "net_id", "layer_id", "start", "end", "width_nm", "locked"}
            if key == "arcs":
                required.add("mid")
            entry = _object(raw, required=required, path=entry_path)
            arguments: dict[str, Any] = {
                "id": _string(entry["id"], f"{entry_path}.id"),
                "net_id": _optional_string(entry["net_id"], f"{entry_path}.net_id"),
                "layer_id": _string(entry["layer_id"], f"{entry_path}.layer_id"),
                "start": _point(entry["start"], f"{entry_path}.start"),
                "end": _point(entry["end"], f"{entry_path}.end"),
                "width_nm": _integer(entry["width_nm"], f"{entry_path}.width_nm"),
                "locked": _boolean(entry["locked"], f"{entry_path}.locked"),
            }
            if key == "arcs":
                arguments["mid"] = _point(entry["mid"], f"{entry_path}.mid")
            decoded.append(constructor(**arguments))
        return tuple(decoded)

    segments_raw = line_items("segments", Segment)
    arcs_raw = line_items("arcs", Arc)
    segments = tuple(item for item in segments_raw if isinstance(item, Segment))
    arcs = tuple(item for item in arcs_raw if isinstance(item, Arc))
    zones = tuple(
        Zone(
            id=_string(entry["id"], f"{entry_path}.id"),
            net_id=_string(entry["net_id"], f"{entry_path}.net_id"),
            layer_id=_string(entry["layer_id"], f"{entry_path}.layer_id"),
            boundary=_ring(entry["boundary"], f"{entry_path}.boundary"),
            clearance_nm=_integer(entry["clearance_nm"], f"{entry_path}.clearance_nm"),
            min_thickness_nm=_integer(entry["min_thickness_nm"], f"{entry_path}.min_thickness_nm"),
            thermal_gap_nm=_integer(entry["thermal_gap_nm"], f"{entry_path}.thermal_gap_nm"),
            thermal_bridge_width_nm=_integer(
                entry["thermal_bridge_width_nm"], f"{entry_path}.thermal_bridge_width_nm"
            ),
            priority=_integer(entry["priority"], f"{entry_path}.priority"),
            pad_connection=_enum_value(
                ZonePadConnection, entry["pad_connection"], f"{entry_path}.pad_connection"
            ),
            island_removal=_enum_value(
                ZoneIslandRemoval, entry["island_removal"], f"{entry_path}.island_removal"
            ),
            fill_mode=_string(entry["fill_mode"], f"{entry_path}.fill_mode"),
            locked=_boolean(entry["locked"], f"{entry_path}.locked"),
        )
        for index, raw in enumerate(_array(items["zones"], "content.items.zones"))
        for entry_path in (f"content.items.zones[{index}]",)
        for entry in (
            _object(
                raw,
                required={
                    "id",
                    "net_id",
                    "layer_id",
                    "boundary",
                    "clearance_nm",
                    "min_thickness_nm",
                    "thermal_gap_nm",
                    "thermal_bridge_width_nm",
                    "priority",
                    "pad_connection",
                    "island_removal",
                    "fill_mode",
                    "locked",
                },
                path=entry_path,
            ),
        )
    )
    keepouts = tuple(
        Keepout(
            id=_string(entry["id"], f"{entry_path}.id"),
            layer_ids=tuple(
                _string(layer_id, f"{entry_path}.layer_ids[{layer_index}]")
                for layer_index, layer_id in enumerate(
                    _array(entry["layer_ids"], f"{entry_path}.layer_ids")
                )
            ),
            boundary=_ring(entry["boundary"], f"{entry_path}.boundary"),
            prohibit_tracks=_boolean(entry["prohibit_tracks"], f"{entry_path}.prohibit_tracks"),
            prohibit_vias=_boolean(entry["prohibit_vias"], f"{entry_path}.prohibit_vias"),
            prohibit_pads=_boolean(entry["prohibit_pads"], f"{entry_path}.prohibit_pads"),
            prohibit_zones=_boolean(entry["prohibit_zones"], f"{entry_path}.prohibit_zones"),
            prohibit_footprints=_boolean(
                entry["prohibit_footprints"], f"{entry_path}.prohibit_footprints"
            ),
            locked=_boolean(entry["locked"], f"{entry_path}.locked"),
        )
        for index, raw in enumerate(_array(items["keepouts"], "content.items.keepouts"))
        for entry_path in (f"content.items.keepouts[{index}]",)
        for entry in (
            _object(
                raw,
                required={
                    "id",
                    "layer_ids",
                    "boundary",
                    "prohibit_tracks",
                    "prohibit_vias",
                    "prohibit_pads",
                    "prohibit_zones",
                    "prohibit_footprints",
                    "locked",
                },
                path=entry_path,
            ),
        )
    )
    return BoardIRContent(
        units=units,
        source=source,
        constraint_digest=_string(item["constraint_digest"], "content.constraint_digest"),
        outline=outline,
        copper_layers=layers,
        nets=nets,
        constraints=_constraints(item["constraints"], "content.constraints"),
        footprints=footprints,
        pads=pads,
        vias=vias,
        segments=segments,
        arcs=arcs,
        zones=zones,
        keepouts=keepouts,
    )


def _validate_structure(value: object, limits: ParseLimits) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            raise BoardIRValidationError(
                ParseBudget.NODES.value, "JSON node budget exceeded", "json"
            )
        if depth > limits.max_depth:
            raise BoardIRValidationError(
                ParseBudget.DEPTH.value, "JSON depth budget exceeded", "json"
            )
        if isinstance(item, str) and len(item) > limits.max_atom_chars:
            raise BoardIRValidationError(
                ParseBudget.ATOM_CHARS.value, "JSON string budget exceeded", "json"
            )
        if isinstance(item, list):
            if len(item) > limits.max_children_per_list:
                raise BoardIRValidationError(
                    ParseBudget.CHILDREN_PER_LIST.value, "JSON array child budget exceeded", "json"
                )
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, dict):
            if len(item) > limits.max_children_per_list:
                raise BoardIRValidationError(
                    ParseBudget.CHILDREN_PER_LIST.value, "JSON object child budget exceeded", "json"
                )
            for key in item:
                if len(key) > limits.max_atom_chars:
                    raise BoardIRValidationError(
                        ParseBudget.ATOM_CHARS.value, "JSON string budget exceeded", "json"
                    )
            stack.extend((child, depth + 1) for child in item.values())


def decode_snapshot_json(payload: bytes, limits: ParseLimits | None = None) -> BoardIRSnapshot:
    """Decode, validate, and verify one untrusted Board IR JSON envelope."""

    limits = limits or ParseLimits()
    validation_code = "validation.failed"
    if not isinstance(payload, bytes) or len(payload) > limits.max_input_bytes:
        raise BoardIRValidationError(
            ParseBudget.INPUT_BYTES.value, "JSON input byte budget exceeded", "json"
        )
    try:
        text = payload.decode("utf-8", errors="strict")
        _preflight_json(text, limits)
        decoded = json.loads(
            text,
            parse_int=_parse_integer,
            parse_float=_reject_float,
            parse_constant=_reject_float,
            object_pairs_hook=_object_pairs,
        )
        _validate_structure(decoded, limits)
        envelope = _object(
            decoded,
            required={"schema", "schema_version", "snapshot_digest", "content"},
            path="snapshot",
        )
        if _string(envelope["schema"], "snapshot.schema") != BOARD_IR_SCHEMA:
            raise ValueError("snapshot schema discriminator is unsupported")
        if (
            _string(envelope["schema_version"], "snapshot.schema_version")
            != BOARD_IR_SCHEMA_VERSION
        ):
            raise ValueError("snapshot schema version is unsupported")
        content = _decode_content(envelope["content"])
        validate_content(content, limits)
        content = normalize_content(content)
        validate_content(content, limits)
        snapshot = BoardIRSnapshot(
            snapshot_digest=_string(envelope["snapshot_digest"], "snapshot.snapshot_digest"),
            content=content,
        )
        verify_snapshot(snapshot)
        return snapshot
    except BoardIRValidationError as error:
        validation_code = error.code
    except RecursionError as error:
        raise BoardIRValidationError(
            ParseBudget.DEPTH.value, "JSON nesting exceeds the decoder budget", "json"
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise BoardIRValidationError(
            "schema.invalid", "JSON does not conform to Board IR v0.2", "json"
        ) from error
    # Prefix, not equality: the re-raise has to carry whichever discriminated budget code the
    # inner failure chose, and a future budget must not silently fall through to the semantic
    # branch and be reported as failed validation.
    if validation_code.startswith(BUDGET_EXCEEDED_PREFIX):
        raise BoardIRValidationError(
            validation_code, "Board IR input exceeded the decoder budget", "json"
        )
    raise BoardIRValidationError(
        validation_code, "Board IR content failed semantic validation", "content"
    )
