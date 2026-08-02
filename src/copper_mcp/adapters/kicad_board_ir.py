"""Fail-closed, read-only KiCad S-expression to Board IR v0.1 adapter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Never

from copper_mcp.adapters.sexpr import SExpr, SExprError, atoms, child, children, parse_sexpr
from copper_mcp.board_ir.canonical import make_content, make_snapshot
from copper_mcp.board_ir.diagnostics import ConversionResult, Diagnostic, Severity
from copper_mcp.board_ir.limits import ParseLimits
from copper_mcp.board_ir.types import (
    FULL_ROTATION_UDEG,
    JSON_SAFE_INTEGER,
    Arc,
    ConstraintSet,
    DifferentialPairRule,
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
    Via,
    ViaKind,
    Zone,
    mm_to_nm,
    normalize_rotation_udeg,
)
from copper_mcp.board_ir.validation import BoardIRValidationError

_PLAIN_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


@dataclass(frozen=True, slots=True)
class KiCadConstraintProfile:
    """Typed constraints supplied separately from a KiCad board file."""

    net_classes: tuple[NetClass, ...]
    default_net_class_id: str
    net_class_by_name: tuple[tuple[str, str], ...] = ()
    differential_pairs: tuple[DifferentialPairRule, ...] = ()
    length_rules: tuple[LengthRule, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.net_classes, tuple) or not all(
            isinstance(item, NetClass) for item in self.net_classes
        ):
            raise ValueError("constraint profile net classes must be an immutable tuple")
        if not isinstance(self.default_net_class_id, str):
            raise ValueError("constraint profile default net-class ID is malformed")
        if not isinstance(self.net_class_by_name, tuple) or not all(
            isinstance(item, tuple)
            and len(item) == 2
            and all(isinstance(value, str) for value in item)
            for item in self.net_class_by_name
        ):
            raise ValueError("constraint profile assignments must be immutable string pairs")
        if not isinstance(self.differential_pairs, tuple) or not all(
            isinstance(item, DifferentialPairRule) for item in self.differential_pairs
        ):
            raise ValueError("constraint profile differential pairs must be immutable")
        if not isinstance(self.length_rules, tuple) or not all(
            isinstance(item, LengthRule) for item in self.length_rules
        ):
            raise ValueError("constraint profile length rules must be immutable")
        class_ids = {item.id for item in self.net_classes}
        if len(class_ids) != len(self.net_classes) or self.default_net_class_id not in class_ids:
            raise ValueError("constraint profile net classes are malformed")
        names = [name for name, _ in self.net_class_by_name]
        if len(names) != len(set(names)):
            raise ValueError("constraint profile contains duplicate net-name assignments")
        if any(class_id not in class_ids for _, class_id in self.net_class_by_name):
            raise ValueError("constraint profile references an unknown net class")


@dataclass(frozen=True, slots=True)
class _ConversionError(ValueError):
    code: str
    message: str
    locator: str
    object_kind: str | None = None
    object_id: str | None = None


def net_id_for_name(name: str) -> str:
    """Return the stable Board IR identity used for a KiCad named net."""

    validated = Net(
        id=f"net:name:{hashlib.sha256(name.encode('utf-8')).hexdigest()[:32]}", name=name
    )
    return validated.id


class _Converter:
    def __init__(
        self,
        payload: bytes,
        root: SExpr,
        profile: KiCadConstraintProfile,
        limits: ParseLimits,
    ) -> None:
        self.payload = payload
        self.root = root
        self.profile = profile
        self.limits = limits
        self.source_revision = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        self.layers = self._layers()
        self.layer_by_name = {layer.name: layer for layer in self.layers}
        self.legacy_nets = self._legacy_nets()

    def fail(
        self,
        code: str,
        message: str,
        locator: str,
        *,
        object_kind: str | None = None,
        object_id: str | None = None,
    ) -> Never:
        raise _ConversionError(
            code[:96] or "conversion.failed",
            message[:512] or "conversion failed",
            locator[:256] or "kicad_pcb",
            object_kind[:192] if object_kind is not None else None,
            object_id[:192] if object_id is not None else None,
        )

    def _one(
        self, expression: SExpr, head: str, locator: str, *, required: bool = True
    ) -> SExpr | None:
        try:
            result = child(expression, head)
        except SExprError as error:
            self.fail(error.code, error.message, f"byte:{error.offset}")
        if result is None and required:
            self.fail("syntax.missing_field", f"missing {head} field", locator)
        return result

    def _values(
        self,
        expression: SExpr,
        head: str,
        locator: str,
        *,
        minimum: int = 1,
        maximum: int | None = None,
        required: bool = True,
    ) -> tuple[str, ...]:
        field = self._one(expression, head, locator, required=required)
        if field is None:
            return ()
        try:
            values = atoms(field)
        except SExprError as error:
            self.fail(error.code, error.message, f"byte:{error.offset}")
        maximum = minimum if maximum is None else maximum
        if not minimum <= len(values) <= maximum:
            self.fail("syntax.invalid", f"{head} field has an invalid arity", locator)
        return values

    def _mm(self, token: str, locator: str) -> int:
        try:
            return mm_to_nm(token)
        except ValueError as error:
            self.fail("integer.precision", str(error), locator)

    def _rotation(self, token: str, locator: str) -> int:
        try:
            return normalize_rotation_udeg(token)
        except (InvalidOperation, ValueError) as error:
            self.fail("integer.precision", str(error), locator)

    def _point(self, expression: SExpr, head: str, locator: str) -> PointNM:
        values = self._values(expression, head, locator, minimum=2, maximum=2)
        return PointNM(
            self._mm(values[0], f"{locator}.{head}.x"), self._mm(values[1], f"{locator}.{head}.y")
        )

    def _locked(self, expression: SExpr) -> bool:
        if any(item == "locked" for item in expression.items[1:] if isinstance(item, str)):
            return True
        field = self._one(expression, "locked", "locked", required=False)
        if field is None:
            return False
        values = atoms(field)
        if not values:
            return True
        if values == ("yes",):
            return True
        if values == ("no",):
            return False
        self.fail("syntax.invalid", "locked field must be yes or no", "locked")

    def _identity(self, kind: str, expression: SExpr, locator: str) -> str:
        for head in ("uuid", "tstamp"):
            value = self._values(
                expression,
                head,
                locator,
                minimum=1,
                maximum=1,
                required=False,
            )
            if value:
                return f"{kind}:kicad:{value[0].lower()}"
        material = f"{self.source_revision}\0{kind}\0{locator}".encode()
        return f"{kind}:derived:{hashlib.sha256(material).hexdigest()[:32]}"

    def _layers(self) -> tuple[Layer, ...]:
        layers_expression = self._one(self.root, "layers", "kicad_pcb.layers")
        assert layers_expression is not None
        result: list[Layer] = []
        for item in layers_expression.items[1:]:
            if not isinstance(item, SExpr) or item.head is None:
                self.fail("syntax.invalid", "layer entry is malformed", "kicad_pcb.layers")
            values = atoms(item)
            if len(values) < 2:
                self.fail("syntax.invalid", "layer entry is malformed", "kicad_pcb.layers")
            name, kind = values[0], values[1]
            if name.endswith(".Cu"):
                layer_kind = {"signal": "signal", "power": "plane", "mixed": "mixed"}.get(kind)
                if layer_kind is None:
                    self.fail(
                        "unsupported.construct",
                        "copper layer kind is unsupported",
                        "kicad_pcb.layers",
                        object_kind="layer",
                    )
                result.append(
                    Layer(id=f"layer:{name}", name=name, index=len(result), kind=layer_kind)
                )
        if not result:
            self.fail("unknown.layer", "board has no canonical copper layers", "kicad_pcb.layers")
        return tuple(result)

    def _legacy_nets(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for index, expression in enumerate(children(self.root, "net")):
            values = atoms(expression)
            if len(values) != 2 or not values[0].isdigit():
                self.fail(
                    "net.ambiguous", "root net declaration is malformed", f"kicad_pcb.net[{index}]"
                )
            if values[0] == "0" or values[1] == "":
                continue
            if values[0] in result and result[values[0]] != values[1]:
                self.fail(
                    "net.ambiguous", "numeric net ID has multiple names", f"kicad_pcb.net[{index}]"
                )
            result[values[0]] = values[1]
        return result

    def _net_name(self, expression: SExpr, locator: str) -> str | None:
        values = self._values(
            expression,
            "net",
            locator,
            minimum=1,
            maximum=2,
            required=False,
        )
        if not values:
            return None
        if len(values) == 2:
            numeric, name = values
            if not numeric.isdigit():
                self.fail("net.ambiguous", "two-field net reference requires a numeric ID", locator)
            declared = self.legacy_nets.get(numeric)
            if declared is not None and declared != name:
                self.fail("net.ambiguous", "net reference conflicts with root declaration", locator)
            return name or None
        net_reference = values[0]
        if net_reference.isdigit():
            if net_reference == "0":
                return None
            if net_reference not in self.legacy_nets:
                self.fail("net.unknown", "numeric net reference has no declaration", locator)
            return self.legacy_nets[net_reference]
        return net_reference or None

    def _iter_copper_items(self) -> tuple[tuple[SExpr, str], ...]:
        result: list[tuple[SExpr, str]] = []
        for footprint_index, footprint in enumerate(children(self.root, "footprint")):
            for pad_index, pad in enumerate(children(footprint, "pad")):
                result.append((pad, f"kicad_pcb.footprint[{footprint_index}].pad[{pad_index}]"))
        for head in ("segment", "arc", "via", "zone"):
            result.extend(
                (expression, f"kicad_pcb.{head}[{index}]")
                for index, expression in enumerate(children(self.root, head))
            )
        return tuple(result)

    def _nets(self) -> tuple[Net, ...]:
        names = set(self.legacy_nets.values())
        for expression, locator in self._iter_copper_items():
            name = self._net_name(expression, locator)
            if name is not None:
                names.add(name)
        return tuple(Net(id=net_id_for_name(name), name=name) for name in sorted(names))

    def _constraints(self, nets: tuple[Net, ...]) -> ConstraintSet:
        mapping = dict(self.profile.net_class_by_name)
        unknown = sorted(mapping.keys() - {net.name for net in nets})
        if unknown:
            self.fail(
                "constraint.unknown_net",
                "constraint profile references a net absent from the board",
                f"constraints.net_class_by_name[{unknown[0]}]",
            )
        assignments = tuple(
            NetClassAssignment(
                net_id=net.id,
                net_class_id=mapping.get(net.name, self.profile.default_net_class_id),
            )
            for net in nets
        )
        return ConstraintSet(
            net_classes=self.profile.net_classes,
            assignments=assignments,
            differential_pairs=self.profile.differential_pairs,
            length_rules=self.profile.length_rules,
        )

    def _layer_ids(self, expression: SExpr, locator: str) -> tuple[str, ...]:
        layer_values = self._values(
            expression,
            "layer",
            locator,
            minimum=1,
            maximum=1,
            required=False,
        )
        layers_values = self._values(
            expression,
            "layers",
            locator,
            minimum=1,
            maximum=64,
            required=False,
        )
        if layer_values and layers_values:
            self.fail("syntax.invalid", "item has both layer and layers fields", locator)
        values = layer_values or layers_values
        if not values:
            self.fail("syntax.missing_field", "item has no layer reference", locator)
        result: list[str] = []
        for name in values:
            if name in {"*.Cu", "F&B.Cu"}:
                result.extend(layer.id for layer in self.layers)
            elif name in self.layer_by_name:
                result.append(self.layer_by_name[name].id)
            elif name.endswith(".Cu"):
                self.fail("unknown.layer", "item references an unknown copper layer", locator)
        if not result:
            self.fail("unknown.layer", "item has no copper-layer reference", locator)
        return tuple(dict.fromkeys(result))

    def _quarter_turn(self, rotation_udeg: int, locator: str) -> int:
        quarter = 90_000_000
        if rotation_udeg % quarter:
            self.fail(
                "unsupported.transform",
                "Board IR v0.1 supports orthogonal footprint transforms only",
                locator,
            )
        return (rotation_udeg // quarter) % 4

    def _transform(self, local: PointNM, origin: PointNM, turn: int, locator: str) -> PointNM:
        rotated = (
            local,
            PointNM(-local.y, local.x),
            PointNM(-local.x, -local.y),
            PointNM(local.y, -local.x),
        )[turn]
        x = origin.x + rotated.x
        y = origin.y + rotated.y
        if (
            not -JSON_SAFE_INTEGER <= x <= JSON_SAFE_INTEGER
            or not -JSON_SAFE_INTEGER <= y <= JSON_SAFE_INTEGER
        ):
            self.fail("integer.overflow", "transformed point exceeds the integer range", locator)
        return PointNM(x, y)

    def _roundrect_radius(self, ratio: str, short_side_nm: int, locator: str) -> int:
        if not _PLAIN_DECIMAL.fullmatch(ratio):
            self.fail("integer.precision", "roundrect ratio is malformed", locator)
        try:
            decimal = Decimal(ratio)
        except InvalidOperation as error:
            self.fail("integer.precision", "roundrect ratio is malformed", locator)
            raise AssertionError from error
        if decimal <= 0 or decimal > Decimal("0.5"):
            self.fail("geometry.invalid", "roundrect ratio must be in (0, 0.5]", locator)
        scaled = decimal * short_side_nm
        if scaled != scaled.to_integral_value():
            self.fail("integer.precision", "roundrect radius is not an exact nanometre", locator)
        return int(scaled)

    def _pads(self) -> tuple[Pad, ...]:
        result: list[Pad] = []
        for footprint_index, footprint in enumerate(children(self.root, "footprint")):
            footprint_locator = f"kicad_pcb.footprint[{footprint_index}]"
            if children(footprint, "net_tie_pad_groups"):
                self.fail(
                    "unsupported.construct",
                    "net-tie footprints are unsupported in Board IR adapter v0.1",
                    footprint_locator,
                    object_kind="footprint",
                )
            jumper_values = self._values(
                footprint,
                "duplicate_pad_numbers_are_jumpers",
                footprint_locator,
                minimum=1,
                maximum=1,
                required=False,
            )
            if jumper_values and jumper_values != ("no",):
                self.fail(
                    "unsupported.construct",
                    "jumper pad-number semantics are unsupported in Board IR adapter v0.1",
                    footprint_locator,
                    object_kind="footprint",
                )
            layer = self._values(footprint, "layer", footprint_locator, minimum=1, maximum=1)[0]
            if layer != "F.Cu":
                self.fail(
                    "unsupported.transform",
                    "Board IR v0.1 supports front-side footprints only",
                    footprint_locator,
                    object_kind="footprint",
                )
            at = self._values(footprint, "at", footprint_locator, minimum=2, maximum=3)
            origin = PointNM(
                self._mm(at[0], f"{footprint_locator}.at.x"),
                self._mm(at[1], f"{footprint_locator}.at.y"),
            )
            footprint_rotation = self._rotation(
                at[2] if len(at) == 3 else "0", f"{footprint_locator}.at.rotation"
            )
            turn = self._quarter_turn(footprint_rotation, footprint_locator)
            footprint_locked = self._locked(footprint)
            for pad_index, pad in enumerate(children(footprint, "pad")):
                locator = f"{footprint_locator}.pad[{pad_index}]"
                header = tuple(item for item in pad.items[1:4] if isinstance(item, str))
                if len(header) != 3:
                    self.fail(
                        "syntax.invalid", "pad header is malformed", locator, object_kind="pad"
                    )
                _, raw_kind, raw_shape = header
                try:
                    kind = {
                        "smd": PadKind.SMD,
                        "thru_hole": PadKind.THROUGH_HOLE,
                        "np_thru_hole": PadKind.NPTH,
                    }[raw_kind]
                    shape = PadShape(raw_shape)
                except (KeyError, ValueError):
                    self.fail(
                        "unsupported.construct",
                        "pad kind or shape is unsupported",
                        locator,
                        object_kind="pad",
                    )
                pad_at = self._values(pad, "at", locator, minimum=2, maximum=3)
                local = PointNM(
                    self._mm(pad_at[0], f"{locator}.at.x"),
                    self._mm(pad_at[1], f"{locator}.at.y"),
                )
                center = self._transform(local, origin, turn, locator)
                pad_rotation = self._rotation(
                    pad_at[2] if len(pad_at) == 3 else "0", f"{locator}.at.rotation"
                )
                rotation = (footprint_rotation + pad_rotation) % FULL_ROTATION_UDEG
                size = self._values(pad, "size", locator, minimum=2, maximum=2)
                size_x = self._mm(size[0], f"{locator}.size.x")
                size_y = self._mm(size[1], f"{locator}.size.y")
                radius: int | None = None
                if shape is PadShape.ROUNDRECT:
                    ratio = self._values(
                        pad,
                        "roundrect_rratio",
                        locator,
                        minimum=1,
                        maximum=1,
                    )[0]
                    radius = self._roundrect_radius(ratio, min(size_x, size_y), locator)
                remove_unused = self._values(
                    pad,
                    "remove_unused_layers",
                    locator,
                    minimum=1,
                    maximum=1,
                    required=False,
                )
                if remove_unused and remove_unused != ("no",):
                    self.fail(
                        "unsupported.construct",
                        "pads with removed copper layers are unsupported",
                        locator,
                        object_kind="pad",
                    )
                for unsupported_head in (
                    "clearance",
                    "offset",
                    "options",
                    "primitives",
                    "thermal_bridge_angle",
                    "thermal_bridge_width",
                    "thermal_gap",
                    "zone_connect",
                ):
                    if children(pad, unsupported_head):
                        self.fail(
                            "unsupported.construct",
                            f"pad field {unsupported_head!r} is unsupported",
                            locator,
                            object_kind="pad",
                        )
                drill_values = self._values(
                    pad,
                    "drill",
                    locator,
                    minimum=1,
                    maximum=3,
                    required=False,
                )
                drill_x: int | None = None
                drill_y: int | None = None
                if drill_values:
                    if drill_values[0] == "oval":
                        if len(drill_values) != 3:
                            self.fail("syntax.invalid", "oval pad drill is malformed", locator)
                        drill_x = self._mm(drill_values[1], f"{locator}.drill.x")
                        drill_y = self._mm(drill_values[2], f"{locator}.drill.y")
                    elif len(drill_values) == 1:
                        drill_x = drill_y = self._mm(drill_values[0], f"{locator}.drill")
                    else:
                        self.fail("syntax.invalid", "pad drill is malformed", locator)
                net_name = self._net_name(pad, locator)
                result.append(
                    Pad(
                        id=self._identity("pad", pad, locator),
                        net_id=net_id_for_name(net_name) if net_name is not None else None,
                        center=center,
                        rotation_udeg=rotation,
                        shape=shape,
                        kind=kind,
                        size_x_nm=size_x,
                        size_y_nm=size_y,
                        roundrect_radius_nm=radius,
                        drill_x_nm=drill_x,
                        drill_y_nm=drill_y,
                        layer_ids=self._layer_ids(pad, locator),
                        locked=footprint_locked or self._locked(pad),
                    )
                )
        return tuple(result)

    def _segments(self) -> tuple[Segment, ...]:
        result: list[Segment] = []
        for index, expression in enumerate(children(self.root, "segment")):
            locator = f"kicad_pcb.segment[{index}]"
            net_name = self._net_name(expression, locator)
            if net_name is None:
                self.fail("net.unknown", "segment has no routable net", locator)
            layer_ids = self._layer_ids(expression, locator)
            if len(layer_ids) != 1:
                self.fail("unknown.layer", "segment must reference one copper layer", locator)
            result.append(
                Segment(
                    id=self._identity("segment", expression, locator),
                    net_id=net_id_for_name(net_name),
                    layer_id=layer_ids[0],
                    start=self._point(expression, "start", locator),
                    end=self._point(expression, "end", locator),
                    width_nm=self._mm(
                        self._values(expression, "width", locator, minimum=1, maximum=1)[0],
                        f"{locator}.width",
                    ),
                    locked=self._locked(expression),
                )
            )
        return tuple(result)

    def _arcs(self) -> tuple[Arc, ...]:
        result: list[Arc] = []
        for index, expression in enumerate(children(self.root, "arc")):
            locator = f"kicad_pcb.arc[{index}]"
            net_name = self._net_name(expression, locator)
            if net_name is None:
                self.fail("net.unknown", "track arc has no routable net", locator)
            layer_ids = self._layer_ids(expression, locator)
            if len(layer_ids) != 1:
                self.fail("unknown.layer", "track arc must reference one copper layer", locator)
            result.append(
                Arc(
                    id=self._identity("arc", expression, locator),
                    net_id=net_id_for_name(net_name),
                    layer_id=layer_ids[0],
                    start=self._point(expression, "start", locator),
                    mid=self._point(expression, "mid", locator),
                    end=self._point(expression, "end", locator),
                    width_nm=self._mm(
                        self._values(expression, "width", locator, minimum=1, maximum=1)[0],
                        f"{locator}.width",
                    ),
                    locked=self._locked(expression),
                )
            )
        return tuple(result)

    def _vias(self) -> tuple[Via, ...]:
        result: list[Via] = []
        stack_order = {layer.id: layer.index for layer in self.layers}
        for index, expression in enumerate(children(self.root, "via")):
            locator = f"kicad_pcb.via[{index}]"
            bare_via_types = {
                value
                for value in expression.items[1:]
                if isinstance(value, str) and value in {"blind", "micro"}
            }
            if bare_via_types:
                self.fail(
                    "unsupported.construct",
                    "blind, buried, and microvias are unsupported in Board IR adapter v0.1",
                    locator,
                    object_kind="via",
                )
            via_type = self._values(
                expression,
                "type",
                locator,
                minimum=1,
                maximum=1,
                required=False,
            )
            if via_type and via_type[0] not in {"through", "through_hole"}:
                self.fail(
                    "unsupported.construct",
                    "blind, buried, and microvias are unsupported in Board IR adapter v0.1",
                    locator,
                    object_kind="via",
                )
            net_name = self._net_name(expression, locator)
            if net_name is None:
                self.fail("net.unknown", "via has no routable net", locator)
            layer_ids = self._layer_ids(expression, locator)
            if len(layer_ids) != 2:
                self.fail("unknown.layer", "through via must reference two copper layers", locator)
            ordered = sorted(layer_ids, key=stack_order.__getitem__)
            if ordered != [self.layers[0].id, self.layers[-1].id]:
                self.fail(
                    "unsupported.construct",
                    "Board IR adapter v0.1 accepts through vias only",
                    locator,
                    object_kind="via",
                )
            result.append(
                Via(
                    id=self._identity("via", expression, locator),
                    net_id=net_id_for_name(net_name),
                    center=self._point(expression, "at", locator),
                    diameter_nm=self._mm(
                        self._values(expression, "size", locator, minimum=1, maximum=1)[0],
                        f"{locator}.size",
                    ),
                    drill_nm=self._mm(
                        self._values(expression, "drill", locator, minimum=1, maximum=1)[0],
                        f"{locator}.drill",
                    ),
                    start_layer_id=ordered[0],
                    end_layer_id=ordered[-1],
                    kind=ViaKind.THROUGH,
                    locked=self._locked(expression),
                )
            )
        return tuple(result)

    def _ring_from_polygon(self, expression: SExpr, locator: str) -> Ring:
        polygons = children(expression, "polygon")
        if len(polygons) != 1:
            self.fail("unsupported.construct", "exactly one polygon loop is required", locator)
        points_expression = self._one(polygons[0], "pts", f"{locator}.polygon")
        assert points_expression is not None
        point_expressions = children(points_expression, "xy")
        if len(point_expressions) > self.limits.max_vertices_per_ring:
            self.fail("budget.exceeded", "ring vertex budget exceeded", locator)
        points: list[PointNM] = []
        for index, point in enumerate(point_expressions):
            values = atoms(point)
            if len(values) != 2:
                self.fail(
                    "syntax.invalid", "polygon point is malformed", f"{locator}.point[{index}]"
                )
            points.append(
                PointNM(
                    self._mm(values[0], f"{locator}.point[{index}].x"),
                    self._mm(values[1], f"{locator}.point[{index}].y"),
                )
            )
        return Ring(tuple(points))

    def _keepout_flag(self, expression: SExpr, head: str, locator: str) -> bool:
        values = self._values(expression, head, locator, minimum=1, maximum=1)
        if values[0] == "not_allowed":
            return True
        if values[0] == "allowed":
            return False
        self.fail("syntax.invalid", f"keepout {head} flag is malformed", locator)

    def _zones_and_keepouts(
        self, constraints: ConstraintSet
    ) -> tuple[tuple[Zone, ...], tuple[Keepout, ...]]:
        zones: list[Zone] = []
        keepouts: list[Keepout] = []
        net_class_by_id = {item.id: item for item in constraints.net_classes}
        class_by_net_id = {
            item.net_id: net_class_by_id[item.net_class_id] for item in constraints.assignments
        }
        for index, expression in enumerate(children(self.root, "zone")):
            locator = f"kicad_pcb.zone[{index}]"
            keepout = self._one(expression, "keepout", locator, required=False)
            if keepout is not None:
                keepouts.append(
                    Keepout(
                        id=self._identity("keepout", expression, locator),
                        layer_ids=self._layer_ids(expression, locator),
                        boundary=self._ring_from_polygon(expression, locator),
                        prohibit_tracks=self._keepout_flag(keepout, "tracks", locator),
                        prohibit_vias=self._keepout_flag(keepout, "vias", locator),
                        prohibit_pads=self._keepout_flag(keepout, "pads", locator),
                        prohibit_zones=self._keepout_flag(keepout, "copperpour", locator),
                        prohibit_footprints=self._keepout_flag(keepout, "footprints", locator),
                        locked=self._locked(expression),
                    )
                )
                continue
            layer_ids = self._layer_ids(expression, locator)
            if len(layer_ids) != 1:
                self.fail(
                    "unsupported.construct", "solid zone must reference one copper layer", locator
                )
            net_name = self._net_name(expression, locator)
            if net_name is None:
                self.fail("net.unknown", "copper zone has no net", locator)
            net_id = net_id_for_name(net_name)
            connect_pads = self._one(expression, "connect_pads", locator, required=False)
            clearance_values = (
                self._values(
                    connect_pads,
                    "clearance",
                    locator,
                    minimum=1,
                    maximum=1,
                    required=False,
                )
                if connect_pads is not None
                else ()
            )
            clearance = (
                self._mm(clearance_values[0], f"{locator}.clearance")
                if clearance_values
                else class_by_net_id[net_id].clearance_nm
            )
            fill = self._one(expression, "fill", locator)
            assert fill is not None
            fill_values = tuple(item for item in fill.items[1:] if isinstance(item, str))
            if fill_values and fill_values[0] not in {"yes"}:
                self.fail(
                    "unsupported.construct", "hatched or non-solid zones are unsupported", locator
                )
            if children(fill, "mode"):
                self.fail(
                    "unsupported.construct", "explicit zone fill modes are unsupported", locator
                )
            thermal_gap = self._values(
                fill,
                "thermal_gap",
                locator,
                minimum=1,
                maximum=1,
                required=False,
            )
            thermal_bridge = self._values(
                fill,
                "thermal_bridge_width",
                locator,
                minimum=1,
                maximum=1,
                required=False,
            )
            zones.append(
                Zone(
                    id=self._identity("zone", expression, locator),
                    net_id=net_id,
                    layer_id=layer_ids[0],
                    boundary=self._ring_from_polygon(expression, locator),
                    clearance_nm=clearance,
                    min_thickness_nm=self._mm(
                        self._values(
                            expression,
                            "min_thickness",
                            locator,
                            minimum=1,
                            maximum=1,
                        )[0],
                        f"{locator}.min_thickness",
                    ),
                    thermal_gap_nm=(
                        self._mm(thermal_gap[0], f"{locator}.thermal_gap") if thermal_gap else 0
                    ),
                    thermal_bridge_width_nm=(
                        self._mm(thermal_bridge[0], f"{locator}.thermal_bridge_width")
                        if thermal_bridge
                        else 0
                    ),
                    locked=self._locked(expression),
                )
            )
        return tuple(zones), tuple(keepouts)

    def _outline(self) -> tuple[OutlineContour, ...]:
        contours: list[OutlineContour] = []
        for index, expression in enumerate(children(self.root, "gr_rect")):
            locator = f"kicad_pcb.gr_rect[{index}]"
            layer = self._values(expression, "layer", locator, minimum=1, maximum=1)[0]
            if layer != "Edge.Cuts":
                continue
            start = self._point(expression, "start", locator)
            end = self._point(expression, "end", locator)
            contours.append(
                OutlineContour(
                    id=self._identity("contour", expression, locator),
                    outer=Ring(
                        (
                            start,
                            PointNM(end.x, start.y),
                            end,
                            PointNM(start.x, end.y),
                        )
                    ),
                )
            )
        for head in ("gr_line", "gr_arc", "gr_circle", "gr_poly", "gr_curve"):
            for index, expression in enumerate(children(self.root, head)):
                locator = f"kicad_pcb.{head}[{index}]"
                layer_values = self._values(
                    expression,
                    "layer",
                    locator,
                    minimum=1,
                    maximum=1,
                    required=False,
                )
                if layer_values == ("Edge.Cuts",):
                    self.fail(
                        "unsupported.construct",
                        "Board IR adapter v0.1 accepts rectangular Edge.Cuts only",
                        locator,
                        object_kind="outline",
                    )
        if not contours:
            self.fail("geometry.missing", "board has no supported Edge.Cuts outline", "kicad_pcb")
        if len(contours) != 1:
            self.fail(
                "unsupported.construct",
                "Board IR adapter v0.1 requires exactly one rectangular Edge.Cuts contour",
                "kicad_pcb",
                object_kind="outline",
            )
        return tuple(contours)

    def convert(self) -> ConversionResult:
        if self.root.head != "kicad_pcb":
            self.fail("syntax.invalid", "source root must be kicad_pcb", "kicad_pcb")
        version = self._values(self.root, "version", "kicad_pcb", minimum=1, maximum=1)[0]
        generator_values = self._values(
            self.root,
            "generator",
            "kicad_pcb",
            minimum=1,
            maximum=1,
            required=False,
        )
        nets = self._nets()
        constraints = self._constraints(nets)
        zones, keepouts = self._zones_and_keepouts(constraints)
        content = make_content(
            source=SourceInfo(
                format="kicad_pcb",
                revision=self.source_revision,
                format_version=version,
                generator=generator_values[0] if generator_values else None,
            ),
            outline=self._outline(),
            copper_layers=self.layers,
            nets=nets,
            constraints=constraints,
            pads=self._pads(),
            vias=self._vias(),
            segments=self._segments(),
            arcs=self._arcs(),
            zones=zones,
            keepouts=keepouts,
        )
        return ConversionResult(snapshot=make_snapshot(content))


def parse_kicad_bytes(
    source: bytes,
    profile: KiCadConstraintProfile,
    limits: ParseLimits | None = None,
) -> ConversionResult:
    """Convert a documented KiCad subset without mutating or retaining source bytes."""

    limits = limits or ParseLimits()
    try:
        root = parse_sexpr(source, limits)
        return _Converter(source, root, profile, limits).convert()
    except _ConversionError as error:
        return ConversionResult(
            snapshot=None,
            diagnostics=(
                Diagnostic(
                    code=error.code,
                    severity=Severity.ERROR,
                    message=error.message,
                    source_locator=error.locator,
                    object_kind=error.object_kind,
                    object_id=error.object_id,
                ),
            ),
        )
    except SExprError as error:
        return ConversionResult(
            snapshot=None,
            diagnostics=(
                Diagnostic(
                    code=error.code,
                    severity=Severity.ERROR,
                    message=error.message,
                    source_locator=f"byte:{error.offset}",
                ),
            ),
        )
    except BoardIRValidationError as error:
        return ConversionResult(
            snapshot=None,
            diagnostics=(
                Diagnostic(
                    code=error.code,
                    severity=Severity.ERROR,
                    message=error.message,
                    source_locator=error.source_locator,
                ),
            ),
        )
    except ValueError as error:
        return ConversionResult(
            snapshot=None,
            diagnostics=(
                Diagnostic(
                    code="geometry.invalid",
                    severity=Severity.ERROR,
                    message=str(error)[:512] or "invalid Board IR geometry",
                    source_locator="kicad_pcb",
                ),
            ),
        )
