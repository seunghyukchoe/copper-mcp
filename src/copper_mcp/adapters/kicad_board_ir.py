"""Fail-closed, read-only KiCad S-expression to Board IR v0.1 adapter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Never

from copper_mcp.adapters.sexpr import (
    SExpr,
    SExprError,
    atoms,
    child,
    children,
    is_quoted_atom,
    parse_sexpr,
)
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
    ZoneIslandRemoval,
    ZonePadConnection,
    mm_to_nm,
    normalize_rotation_udeg,
)
from copper_mcp.board_ir.validation import BoardIRValidationError, validate_content

_PLAIN_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_UNSIGNED_INTEGER = re.compile(r"^(?:0|[1-9][0-9]*)$")
_SIGNED_INTEGER_TOKEN = re.compile(r"^[+-]?[0-9]+$")
_SUPPORTED_KICAD_PCB_VERSIONS = frozenset({"20260206"})
_ROOT_METADATA_HEADS = frozenset(
    {
        "embedded_fonts",
        "general",
        "generator",
        "generator_version",
        "layers",
        "net",
        "paper",
        "setup",
        "title_block",
        "version",
    }
)
_ROOT_ROUTING_HEADS = frozenset({"arc", "footprint", "segment", "via", "zone"})
_SETUP_METADATA_HEADS = frozenset(
    {
        "allow_soldermask_bridges_in_footprints",
        "capping",
        "covering",
        "filling",
        "pad_to_mask_clearance",
        "pcbplotparams",
        "plugging",
        "tenting",
    }
)
_FOOTPRINT_METADATA_HEADS = frozenset(
    {
        "at",
        "attr",
        "duplicate_pad_numbers_are_jumpers",
        "embedded_fonts",
        "layer",
        "locked",
        "model",
        "net_tie_pad_groups",
        "pad",
        "path",
        "property",
        "tstamp",
        "uuid",
    }
)


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
        if self.root.head != "kicad_pcb":
            self.fail("syntax.invalid", "source root must be kicad_pcb", "kicad_pcb")
        self.version = self._values(self.root, "version", "kicad_pcb", minimum=1, maximum=1)[0]
        if self.version not in _SUPPORTED_KICAD_PCB_VERSIONS:
            self.fail(
                "unsupported.version",
                "KiCad board format version is unsupported",
                "kicad_pcb.version",
            )
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
        except ValueError as error:
            self.fail("integer.precision", str(error), locator)

    def _nonnegative_integer(self, token: str, locator: str) -> int:
        if len(token) > 16 or not _UNSIGNED_INTEGER.fullmatch(token):
            self.fail("integer.precision", "integer token is malformed", locator)
        value = int(token)
        if value > JSON_SAFE_INTEGER:
            self.fail("integer.overflow", "integer token exceeds the supported range", locator)
        return value

    def _reject_unknown_children(
        self, expression: SExpr, allowed: frozenset[str], locator: str
    ) -> None:
        if any(
            isinstance(item, SExpr) and item.head not in allowed for item in expression.items[1:]
        ):
            self.fail(
                "unsupported.construct",
                "expression contains an unsupported semantic field",
                locator,
            )

    def _validate_direct_atoms(
        self,
        expression: SExpr,
        *,
        positional_atoms: int,
        allowed: frozenset[str],
        locator: str,
    ) -> None:
        direct_atoms = tuple(item for item in expression.items[1:] if isinstance(item, str))
        if len(direct_atoms) < positional_atoms or any(
            is_quoted_atom(item) or item not in allowed for item in direct_atoms[positional_atoms:]
        ):
            self.fail(
                "unsupported.construct",
                "expression contains unsupported positional semantics",
                locator,
            )

    @staticmethod
    def _is_routing_layer(name: str) -> bool:
        return name == "Edge.Cuts" or name in {"*.Cu", "F&B.Cu"} or name.endswith(".Cu")

    def _graphic_layer(self, expression: SExpr, locator: str) -> str:
        values = self._values(
            expression,
            "layer",
            locator,
            minimum=1,
            maximum=1,
            required=False,
        )
        if not values:
            self.fail(
                "unsupported.construct",
                "graphic without one explicit layer is unsupported",
                locator,
            )
        return values[0]

    def _semantic_preflight(self) -> None:
        """Reject physical semantics that the v0.1 model cannot preserve."""

        for item in self.root.items[1:]:
            if not isinstance(item, SExpr) or item.head is None:
                self.fail(
                    "syntax.invalid", "root expression contains a malformed item", "kicad_pcb"
                )
            head = item.head
            if head in _ROOT_METADATA_HEADS or head in _ROOT_ROUTING_HEADS:
                continue
            if head.startswith("gr_"):
                layer = self._graphic_layer(item, "kicad_pcb.graphic")
                if self._is_routing_layer(layer) and not (
                    head == "gr_rect" and layer == "Edge.Cuts"
                ):
                    self.fail(
                        "unsupported.construct",
                        "root graphic on copper or Edge.Cuts is unsupported",
                        "kicad_pcb.graphic",
                        object_kind="graphic",
                    )
                continue
            self.fail(
                "unsupported.construct",
                "root expression contains an unsupported semantic construct",
                "kicad_pcb.unsupported",
            )

        general = self._one(self.root, "general", "kicad_pcb", required=False)
        if general is not None:
            legacy_teardrops = self._values(
                general,
                "legacy_teardrops",
                "kicad_pcb.general",
                minimum=1,
                maximum=1,
                required=False,
            )
            if legacy_teardrops and legacy_teardrops != ("no",):
                self.fail(
                    "unsupported.construct",
                    "legacy teardrop copper is unsupported",
                    "kicad_pcb.general.legacy_teardrops",
                )

        setup = self._one(self.root, "setup", "kicad_pcb", required=False)
        if setup is not None:
            self._reject_unknown_children(setup, _SETUP_METADATA_HEADS, "kicad_pcb.setup")
            self._validate_direct_atoms(
                setup,
                positional_atoms=0,
                allowed=frozenset(),
                locator="kicad_pcb.setup",
            )
            self._validate_neutral_via_treatment(setup, "kicad_pcb.setup")

        for footprint_index, footprint in enumerate(children(self.root, "footprint")):
            locator = f"kicad_pcb.footprint[{footprint_index}]"
            self._validate_direct_atoms(
                footprint,
                positional_atoms=1,
                allowed=frozenset({"locked"}),
                locator=locator,
            )
            for item in footprint.items[1:]:
                if not isinstance(item, SExpr) or item.head is None:
                    continue
                head = item.head
                if head == "zone":
                    self.fail(
                        "unsupported.construct",
                        "footprint-local zones are unsupported",
                        f"{locator}.zone",
                        object_kind="zone",
                    )
                if head.startswith("fp_") or head == "property":
                    layer = self._graphic_layer(item, f"{locator}.graphic")
                    if self._is_routing_layer(layer):
                        self.fail(
                            "unsupported.construct",
                            "footprint graphic on copper or Edge.Cuts is unsupported",
                            f"{locator}.graphic",
                            object_kind="graphic",
                        )
                    continue
                if head not in _FOOTPRINT_METADATA_HEADS:
                    self.fail(
                        "unsupported.construct",
                        "footprint contains an unsupported semantic field",
                        f"{locator}.unsupported",
                        object_kind="footprint",
                    )

    def _validate_neutral_via_treatment(self, expression: SExpr, locator: str) -> None:
        """Reject board-level or per-via fabrication treatment that Board IR omits."""

        for neutral_head in ("capping", "filling"):
            neutral = self._values(
                expression,
                neutral_head,
                locator,
                minimum=1,
                maximum=1,
                required=False,
            )
            if neutral and (neutral != ("no",) or is_quoted_atom(neutral[0])):
                self.fail(
                    "unsupported.construct",
                    "non-neutral via fabrication treatment is unsupported",
                    f"{locator}.{neutral_head}",
                    object_kind="via",
                )
        tenting = self._one(expression, "tenting", locator, required=False)
        if tenting is not None:
            self._reject_unknown_children(
                tenting, frozenset({"back", "front"}), f"{locator}.tenting"
            )
            self._validate_direct_atoms(
                tenting,
                positional_atoms=0,
                allowed=frozenset(),
                locator=f"{locator}.tenting",
            )
            for side in ("front", "back"):
                side_values = self._values(
                    tenting,
                    side,
                    f"{locator}.tenting",
                    minimum=1,
                    maximum=1,
                )
                if side_values != ("yes",) or is_quoted_atom(side_values[0]):
                    self.fail(
                        "unsupported.construct",
                        "non-default via tenting is unsupported",
                        f"{locator}.tenting.{side}",
                        object_kind="via",
                    )
        for side_head in ("covering", "plugging"):
            side_setting = self._one(expression, side_head, locator, required=False)
            if side_setting is None:
                continue
            self._reject_unknown_children(
                side_setting, frozenset({"back", "front"}), f"{locator}.{side_head}"
            )
            self._validate_direct_atoms(
                side_setting,
                positional_atoms=0,
                allowed=frozenset(),
                locator=f"{locator}.{side_head}",
            )
            for side in ("front", "back"):
                side_values = self._values(
                    side_setting,
                    side,
                    f"{locator}.{side_head}",
                    minimum=1,
                    maximum=1,
                )
                if side_values != ("no",) or is_quoted_atom(side_values[0]):
                    self.fail(
                        "unsupported.construct",
                        "non-neutral via fabrication treatment is unsupported",
                        f"{locator}.{side_head}.{side}",
                        object_kind="via",
                    )

    def _point(self, expression: SExpr, head: str, locator: str) -> PointNM:
        values = self._values(expression, head, locator, minimum=2, maximum=2)
        return PointNM(
            self._mm(values[0], f"{locator}.{head}.x"), self._mm(values[1], f"{locator}.{head}.y")
        )

    def _locked(self, expression: SExpr, *, positional_atoms: int = 0) -> bool:
        direct_atoms = tuple(item for item in expression.items[1:] if isinstance(item, str))
        bare_lock_count = sum(
            item == "locked" and not is_quoted_atom(item)
            for item in direct_atoms[positional_atoms:]
        )
        if bare_lock_count > 1:
            self.fail("syntax.duplicate_field", "duplicate locked state", "locked")
        field = self._one(expression, "locked", "locked", required=False)
        if bare_lock_count and field is not None:
            self.fail("syntax.invalid", "locked state is ambiguous", "locked")
        if bare_lock_count:
            return True
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
        identities: list[tuple[str, str]] = []
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
                identities.append((head, value[0]))
        if len(identities) > 1:
            self.fail(
                "identity.ambiguous",
                "object contains multiple native identity fields",
                locator,
                object_kind=kind,
            )
        if identities:
            return f"{kind}:kicad:{identities[0][1].lower()}"
        material = f"{self.source_revision}\0{kind}\0{locator}".encode()
        return f"{kind}:derived:{hashlib.sha256(material).hexdigest()[:32]}"

    def _layers(self) -> tuple[Layer, ...]:
        layers_expression = self._one(self.root, "layers", "kicad_pcb.layers")
        assert layers_expression is not None
        copper_entries: list[tuple[int, str, str]] = []
        for item in layers_expression.items[1:]:
            if not isinstance(item, SExpr) or item.head is None:
                self.fail("syntax.invalid", "layer entry is malformed", "kicad_pcb.layers")
            values = atoms(item)
            if len(values) not in {2, 3}:
                self.fail("syntax.invalid", "layer entry is malformed", "kicad_pcb.layers")
            name, kind = values[0], values[1]
            if name.endswith(".Cu"):
                if not _UNSIGNED_INTEGER.fullmatch(item.head):
                    self.fail(
                        "syntax.invalid", "copper layer index is malformed", "kicad_pcb.layers"
                    )
                copper_entries.append((int(item.head), name, kind))
        if len(copper_entries) < 2:
            self.fail(
                "unknown.layer",
                "board must declare front and back copper layers",
                "kicad_pcb.layers",
            )
        result: list[Layer] = []
        for ordinal, (source_index, name, kind) in enumerate(copper_entries):
            expected_name = (
                "F.Cu"
                if ordinal == 0
                else "B.Cu"
                if ordinal == len(copper_entries) - 1
                else f"In{ordinal}.Cu"
            )
            if source_index != ordinal * 2 or name != expected_name:
                self.fail(
                    "unsupported.construct",
                    "copper layer IDs, names, or declaration order are unsupported",
                    "kicad_pcb.layers",
                    object_kind="layer",
                )
            layer_kind = {"signal": "signal", "power": "plane", "mixed": "mixed"}.get(kind)
            if layer_kind is None:
                self.fail(
                    "unsupported.construct",
                    "copper layer kind is unsupported",
                    "kicad_pcb.layers",
                    object_kind="layer",
                )
            result.append(Layer(id=f"layer:{name}", name=name, index=ordinal, kind=layer_kind))
        return tuple(result)

    def _legacy_nets(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for index, expression in enumerate(children(self.root, "net")):
            values = atoms(expression)
            if (
                len(values) != 2
                or is_quoted_atom(values[0])
                or not values[0].isdigit()
                or len(values[0]) > 16
            ):
                self.fail(
                    "net.ambiguous", "root net declaration is malformed", f"kicad_pcb.net[{index}]"
                )
            net_code = int(values[0])
            canonical_code = str(net_code)
            if net_code == 0 or values[1] == "":
                continue
            if canonical_code in result and result[canonical_code] != values[1]:
                self.fail(
                    "net.ambiguous", "numeric net ID has multiple names", f"kicad_pcb.net[{index}]"
                )
            result[canonical_code] = values[1]
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
            if is_quoted_atom(numeric) or not numeric.isdigit() or len(numeric) > 16:
                self.fail("net.ambiguous", "two-field net reference requires a numeric ID", locator)
            net_code = int(numeric)
            if net_code == 0:
                if name:
                    self.fail(
                        "net.ambiguous", "net reference conflicts with root declaration", locator
                    )
                return None
            declared = self.legacy_nets.get(str(net_code))
            if declared is not None and declared != name:
                self.fail("net.ambiguous", "net reference conflicts with root declaration", locator)
            return name or None
        net_reference = values[0]
        if not is_quoted_atom(net_reference) and _SIGNED_INTEGER_TOKEN.fullmatch(net_reference):
            if len(net_reference) > 16:
                self.fail("integer.precision", "numeric net reference is malformed", locator)
            net_code = int(net_reference)
            if net_code <= 0:
                return None
            canonical_code = str(net_code)
            if canonical_code not in self.legacy_nets:
                self.fail("net.unknown", "numeric net reference has no declaration", locator)
            return self.legacy_nets[canonical_code]
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
                "constraints.net_class_by_name",
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
            if name == "*.Cu":
                result.extend(layer.id for layer in self.layers)
            elif name == "F&B.Cu":
                result.append(self.layers[0].id)
                if len(self.layers) > 1:
                    result.append(self.layers[-1].id)
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
        if len(ratio) > 64 or not _PLAIN_DECIMAL.fullmatch(ratio):
            self.fail("integer.precision", "roundrect ratio is malformed", locator)
        whole, _, fraction = ratio.partition(".")
        denominator: int = pow(10, len(fraction))
        numerator = int(whole) * denominator + (int(fraction) if fraction else 0)
        if numerator <= 0 or numerator * 2 > denominator:
            self.fail("geometry.invalid", "roundrect ratio must be in (0, 0.5]", locator)
        scaled_radius = numerator * short_side_nm
        radius = scaled_radius // denominator
        remainder = scaled_radius % denominator
        if remainder:
            self.fail("integer.precision", "roundrect radius is not an exact nanometre", locator)
        return radius

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
            footprint_locked = self._locked(footprint, positional_atoms=1)
            for pad_index, pad in enumerate(children(footprint, "pad")):
                locator = f"{footprint_locator}.pad[{pad_index}]"
                self._reject_unknown_children(
                    pad,
                    frozenset(
                        {
                            "at",
                            "drill",
                            "layer",
                            "layers",
                            "locked",
                            "net",
                            "pinfunction",
                            "pintype",
                            "remove_unused_layers",
                            "roundrect_rratio",
                            "size",
                            "tstamp",
                            "uuid",
                        }
                    ),
                    locator,
                )
                self._validate_direct_atoms(
                    pad,
                    positional_atoms=3,
                    allowed=frozenset({"locked"}),
                    locator=locator,
                )
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
                        locked=footprint_locked or self._locked(pad, positional_atoms=3),
                    )
                )
        return tuple(result)

    def _segments(self) -> tuple[Segment, ...]:
        result: list[Segment] = []
        for index, expression in enumerate(children(self.root, "segment")):
            locator = f"kicad_pcb.segment[{index}]"
            self._reject_unknown_children(
                expression,
                frozenset({"end", "layer", "locked", "net", "start", "tstamp", "uuid", "width"}),
                locator,
            )
            self._validate_direct_atoms(
                expression,
                positional_atoms=0,
                allowed=frozenset({"locked"}),
                locator=locator,
            )
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
            self._reject_unknown_children(
                expression,
                frozenset(
                    {"end", "layer", "locked", "mid", "net", "start", "tstamp", "uuid", "width"}
                ),
                locator,
            )
            self._validate_direct_atoms(
                expression,
                positional_atoms=0,
                allowed=frozenset({"locked"}),
                locator=locator,
            )
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
            self._reject_unknown_children(
                expression,
                frozenset(
                    {
                        "at",
                        "capping",
                        "covering",
                        "drill",
                        "filling",
                        "layers",
                        "locked",
                        "net",
                        "plugging",
                        "size",
                        "tstamp",
                        "type",
                        "uuid",
                    }
                ),
                locator,
            )
            self._validate_direct_atoms(
                expression,
                positional_atoms=0,
                allowed=frozenset({"blind", "locked", "micro"}),
                locator=locator,
            )
            bare_via_types = {
                value
                for value in expression.items[1:]
                if isinstance(value, str)
                and not is_quoted_atom(value)
                and value in {"blind", "micro"}
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
            self._validate_neutral_via_treatment(expression, locator)
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
        self._reject_unknown_children(polygons[0], frozenset({"pts"}), f"{locator}.polygon")
        self._validate_direct_atoms(
            polygons[0], positional_atoms=0, allowed=frozenset(), locator=f"{locator}.polygon"
        )
        points_expression = self._one(polygons[0], "pts", f"{locator}.polygon")
        assert points_expression is not None
        self._reject_unknown_children(
            points_expression, frozenset({"xy"}), f"{locator}.polygon.pts"
        )
        self._validate_direct_atoms(
            points_expression,
            positional_atoms=0,
            allowed=frozenset(),
            locator=f"{locator}.polygon.pts",
        )
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

    def _zones_and_keepouts(self) -> tuple[tuple[Zone, ...], tuple[Keepout, ...]]:
        zones: list[Zone] = []
        keepouts: list[Keepout] = []
        for index, expression in enumerate(children(self.root, "zone")):
            locator = f"kicad_pcb.zone[{index}]"
            self._validate_direct_atoms(
                expression,
                positional_atoms=0,
                allowed=frozenset({"locked"}),
                locator=locator,
            )
            keepout = self._one(expression, "keepout", locator, required=False)
            if keepout is not None:
                self._reject_unknown_children(
                    expression,
                    frozenset(
                        {
                            "connect_pads",
                            "fill",
                            "filled_polygon",
                            "hatch",
                            "keepout",
                            "layer",
                            "layers",
                            "locked",
                            "min_thickness",
                            "name",
                            "placement",
                            "polygon",
                            "property",
                            "tstamp",
                            "uuid",
                        }
                    ),
                    locator,
                )
                self._reject_unknown_children(
                    keepout,
                    frozenset({"tracks", "vias", "pads", "copperpour", "footprints"}),
                    f"{locator}.keepout",
                )
                placement = self._one(expression, "placement", locator, required=False)
                if placement is not None:
                    self._reject_unknown_children(
                        placement, frozenset({"enabled", "sheetname"}), f"{locator}.placement"
                    )
                    self._validate_direct_atoms(
                        placement,
                        positional_atoms=0,
                        allowed=frozenset(),
                        locator=f"{locator}.placement",
                    )
                    if self._values(
                        placement,
                        "enabled",
                        f"{locator}.placement",
                        minimum=1,
                        maximum=1,
                    ) != ("no",):
                        self.fail(
                            "unsupported.construct",
                            "placement-enabled rule areas are unsupported",
                            locator,
                        )
                    self._values(
                        placement,
                        "sheetname",
                        f"{locator}.placement",
                        minimum=1,
                        maximum=1,
                        required=False,
                    )
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
            self._reject_unknown_children(
                expression,
                frozenset(
                    {
                        "connect_pads",
                        "fill",
                        "filled_polygon",
                        "hatch",
                        "layer",
                        "locked",
                        "min_thickness",
                        "name",
                        "net",
                        "net_name",
                        "polygon",
                        "priority",
                        "property",
                        "tstamp",
                        "uuid",
                    }
                ),
                locator,
            )
            layer_ids = self._layer_ids(expression, locator)
            if len(layer_ids) != 1:
                self.fail(
                    "unsupported.construct", "solid zone must reference one copper layer", locator
                )
            net_name = self._net_name(expression, locator)
            if net_name is None:
                self.fail("net.unknown", "copper zone has no net", locator)
            declared_net_name = self._values(
                expression,
                "net_name",
                locator,
                minimum=1,
                maximum=1,
                required=False,
            )
            if declared_net_name and declared_net_name != (net_name,):
                self.fail(
                    "net.ambiguous", "zone net name conflicts with its net reference", locator
                )
            net_id = net_id_for_name(net_name)
            priority_values = self._values(
                expression,
                "priority",
                locator,
                minimum=1,
                maximum=1,
                required=False,
            )
            priority = (
                self._nonnegative_integer(priority_values[0], f"{locator}.priority")
                if priority_values
                else 0
            )
            connect_pads = self._one(expression, "connect_pads", locator)
            assert connect_pads is not None
            self._reject_unknown_children(
                connect_pads, frozenset({"clearance"}), f"{locator}.connect_pads"
            )
            connection_values = tuple(
                item for item in connect_pads.items[1:] if isinstance(item, str)
            )
            if any(is_quoted_atom(item) for item in connection_values):
                self.fail(
                    "unsupported.construct", "zone pad connection mode is unsupported", locator
                )
            try:
                pad_connection = {
                    (): ZonePadConnection.THERMAL,
                    ("thru_hole_only",): ZonePadConnection.THROUGH_HOLE_THERMAL,
                    ("yes",): ZonePadConnection.SOLID,
                    ("no",): ZonePadConnection.NONE,
                }[connection_values]
            except KeyError:
                self.fail(
                    "unsupported.construct", "zone pad connection mode is unsupported", locator
                )
            clearance_values = self._values(
                connect_pads,
                "clearance",
                locator,
                minimum=1,
                maximum=1,
            )
            clearance = self._mm(clearance_values[0], f"{locator}.clearance")
            fill = self._one(expression, "fill", locator)
            assert fill is not None
            fill_values = tuple(item for item in fill.items[1:] if isinstance(item, str))
            if (
                len(fill_values) > 1
                or any(value != "yes" for value in fill_values)
                or any(is_quoted_atom(value) for value in fill_values)
            ):
                self.fail(
                    "unsupported.construct", "hatched or non-solid zones are unsupported", locator
                )
            self._reject_unknown_children(
                fill,
                frozenset({"thermal_gap", "thermal_bridge_width", "island_removal_mode"}),
                f"{locator}.fill",
            )
            island_values = self._values(
                fill,
                "island_removal_mode",
                locator,
                minimum=1,
                maximum=1,
                required=False,
            )
            island_mode = island_values[0] if island_values else "0"
            if island_mode == "0":
                island_removal = ZoneIslandRemoval.ALWAYS
            elif island_mode == "1":
                island_removal = ZoneIslandRemoval.NEVER
            elif island_mode == "2":
                self.fail(
                    "unsupported.construct",
                    "minimum-area island removal is unsupported",
                    locator,
                )
            else:
                self.fail("syntax.invalid", "zone island removal mode is malformed", locator)
            thermal_required = pad_connection in {
                ZonePadConnection.THERMAL,
                ZonePadConnection.THROUGH_HOLE_THERMAL,
            }
            thermal_gap = self._values(
                fill,
                "thermal_gap",
                locator,
                minimum=1,
                maximum=1,
                required=thermal_required,
            )
            thermal_bridge = self._values(
                fill,
                "thermal_bridge_width",
                locator,
                minimum=1,
                maximum=1,
                required=thermal_required,
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
                    priority=priority,
                    pad_connection=pad_connection,
                    island_removal=island_removal,
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
            self._reject_unknown_children(
                expression,
                frozenset({"end", "fill", "layer", "locked", "start", "stroke", "tstamp", "uuid"}),
                locator,
            )
            self._validate_direct_atoms(
                expression,
                positional_atoms=0,
                allowed=frozenset({"locked"}),
                locator=locator,
            )
            if self._values(expression, "fill", locator, minimum=1, maximum=1) != ("no",):
                self.fail(
                    "unsupported.construct",
                    "Edge.Cuts rectangle must use an unfilled outline",
                    locator,
                    object_kind="outline",
                )
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
        self._semantic_preflight()
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
        zones, keepouts = self._zones_and_keepouts()
        content = make_content(
            source=SourceInfo(
                format="kicad_pcb",
                revision=self.source_revision,
                format_version=self.version,
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
        conversion = _Converter(source, root, profile, limits).convert()
        assert conversion.snapshot is not None
        validate_content(conversion.snapshot.content, limits)
        return conversion
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
                    message="converted Board IR content failed semantic validation",
                    source_locator="kicad_pcb",
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
