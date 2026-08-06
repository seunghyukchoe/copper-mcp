"""Fail-closed, read-only KiCad S-expression to Board IR v0.2 adapter."""

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
    JSON_SAFE_INTEGER,
    Arc,
    ConstraintSet,
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
KICAD_PCB_ROOT_HEAD = "kicad_pcb"
# "This is not a KiCad board document" is a different fact from "this board uses something we
# cannot convert", and observers need to tell them apart: the first is a refusal, the second is
# a truthful unsupported result. It used to be reported as a generic ``syntax.invalid``.
FOREIGN_ROOT_DIAGNOSTIC_CODE = "unsupported.document"
_COURTYARD_LAYERS = frozenset({"F.CrtYd", "B.CrtYd"})
# KiCad's copper layer IDs as written into the board file, for the format versions above.
# ``F.Cu=0``, ``B.Cu=2``, ``InN.Cu=2+2N`` for 1 <= N <= 30 - copper takes the even values and the
# technical layers are interleaved on the odd ones.  This is *not* the numbering KiCad used before
# version 9, which was consecutive with the back layer last (``F.Cu=0``, ``In1..In30 = 1..30``,
# ``B.Cu=31``) and which KiCad itself converts via ``BoardLayerFromLegacyId``.  Only one numbering
# can be correct at a time: under the current scheme, ID 1 is ``F.Mask``, so a validator that
# accepted both would accept boards whose stack it had misread.  The choice is made for us by
# ``_SUPPORTED_KICAD_PCB_VERSIONS`` above, which refuses pre-V9 documents with a typed
# ``unsupported.version`` diagnostic before this table is ever consulted - so widening that set
# past a V9 boundary is not a version-string edit, it needs a second numbering rule here.
# Derivation and citations: docs/research/kicad-copper-layer-numbering-v1.md.
_KICAD_COPPER_LAYER_IDS: dict[str, int] = {"F.Cu": 0, "B.Cu": 2} | {
    f"In{inner}.Cu": 2 + 2 * inner for inner in range(1, 31)
}
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
# The only root graphics that may sit on ``Edge.Cuts``: one unfilled rectangle, or straight
# segments that chain into exactly one closed simple loop.  Both carry the board outline as an
# exact integer polygon whose vertices are drawn points, so neither can model more room than the
# board has.  See docs/research/edge-cuts-outline-assembly-v1.md and ADR-0077.
_EDGE_CUTS_OUTLINE_HEADS = frozenset({"gr_line", "gr_rect"})
# Curved outline primitives, refused separately so the diagnostic names the curve rather than
# reporting the same message as an unsupported layer.  An outline curve needs an *inscribed*
# approximation, which is not ADR-0072's conservative arc envelope run backwards.
_EDGE_CUTS_CURVE_HEADS = frozenset({"gr_arc", "gr_bezier", "gr_circle", "gr_curve"})
_EDGE_CUTS_LINE_FIELDS = frozenset({"end", "layer", "locked", "start", "stroke", "tstamp", "uuid"})
_SETUP_METADATA_HEADS = frozenset(
    {
        "allow_soldermask_bridges_in_footprints",
        "capping",
        "covering",
        "filling",
        "pad_to_mask_clearance",
        "pcbplotparams",
        "plugging",
        # Soldermask sliver minimum. Like `pad_to_mask_clearance` above it constrains mask
        # generation, not copper: it bounds how thin a mask web may get between apertures.
        # CopperMCP models copper geometry and makes no soldermask claim, so accepting it as
        # metadata ignores nothing it would otherwise have honoured. Found on real boards that
        # were refused outright for carrying it.
        "solder_mask_min_width",
        "tenting",
    }
)
_FOOTPRINT_METADATA_HEADS = frozenset(
    {
        "at",
        "attr",
        # Library documentation strings KiCad copies into every placed footprint: a human
        # description and search tags. They carry no geometry, no layer, and no constraint, so
        # refusing them refused essentially every real board -- they appeared 2,518 times each
        # across the 23 boards this gap was found on.
        "descr",
        "duplicate_pad_numbers_are_jumpers",
        "embedded_fonts",
        "layer",
        "locked",
        "model",
        "net_tie_pad_groups",
        "pad",
        "path",
        "property",
        "tags",
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
        # Largest single roundrect radius rounding, in nanometres, measured rather than asserted.
        self.max_roundrect_rounding_nm = 0
        if self.root.head != KICAD_PCB_ROOT_HEAD:
            self.fail(
                FOREIGN_ROOT_DIAGNOSTIC_CODE,
                "source root must be kicad_pcb",
                "kicad_pcb",
            )
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
        """Reject physical semantics that the v0.2 model cannot preserve."""

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
                if layer == "Edge.Cuts" and head not in _EDGE_CUTS_OUTLINE_HEADS:
                    self.fail(
                        "unsupported.construct",
                        (
                            "Edge.Cuts outline arcs, circles and curves are unsupported"
                            if head in _EDGE_CUTS_CURVE_HEADS
                            else "Edge.Cuts graphic is not a supported outline primitive"
                        ),
                        "kicad_pcb.graphic",
                        object_kind="outline",
                    )
                if self._is_routing_layer(layer) and layer != "Edge.Cuts":
                    self.fail(
                        "unsupported.construct",
                        "root graphic on copper is unsupported",
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
                # `point` is layer-bearing like the `fp_*` primitives -- it carries `at`, `size`
                # and `layer` -- so it goes through the same layer-aware path rather than the
                # metadata allowlist. That keeps the copper question decided by the layer, not by
                # the head: a `point` on a routing layer is refused exactly as a stray `fp_line`
                # is, and one on a documentation layer is ignored exactly as silkscreen is.
                if head.startswith("fp_") or head in {"property", "point"}:
                    layer = self._graphic_layer(item, f"{locator}.graphic")
                    if layer in _COURTYARD_LAYERS:
                        if head not in {"fp_line", "fp_poly", "fp_rect"}:
                            self.fail(
                                "unsupported.construct",
                                "courtyard primitive is unsupported by Board IR v0.2",
                                f"{locator}.courtyard",
                                object_kind="footprint",
                            )
                        continue
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
        return self._derived_identity(kind, locator)

    def _derived_identity(self, kind: str, locator: str) -> str:
        """Name an object that carries no native KiCad identity of its own."""

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
            # Two independent invariants, and conflating them is what produced issue #104.
            # ``ordinal`` is the *declaration position*: KiCad writes copper front-to-back, so the
            # name a position must carry is positional.  The ID that name must carry is not - it
            # comes from KiCad's own enumeration, in which copper takes the even values with the
            # technical layers interleaved on the odd ones.  The declared IDs of a four-layer
            # board are therefore 0, 4, 6, 2 and do not ascend.
            expected_name = (
                "F.Cu"
                if ordinal == 0
                else "B.Cu"
                if ordinal == len(copper_entries) - 1
                else f"In{ordinal}.Cu"
            )
            # A name KiCad's table does not carry (``In31.Cu`` and beyond) yields a sentinel no
            # unsigned source index can equal, so a deeper stack is refused rather than
            # extrapolated - even though ``2 + 2N`` would happily keep counting.
            expected_index = _KICAD_COPPER_LAYER_IDS.get(expected_name, -1)
            if name != expected_name or source_index != expected_index:
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
                "Board IR v0.2 adapter supports orthogonal footprint transforms only",
                locator,
            )
        return (rotation_udeg // quarter) % 4

    def _footprint_side(self, layer: str, locator: str) -> FootprintSide:
        """Map a KiCad footprint layer to the immutable Board IR side.

        KiCad stores a flipped footprint's child coordinates in the saved file already.  The
        adapter must therefore only observe the side here and must not mirror local geometry a
        second time.  Keeping the mapping explicit also makes unsupported inner-layer
        footprint placement fail closed instead of silently being treated as front-side.
        """

        if layer == "F.Cu":
            return FootprintSide.FRONT
        if layer == "B.Cu":
            return FootprintSide.BACK
        self.fail(
            "unsupported.transform",
            "Board IR v0.2 adapter supports only front- and back-side footprints",
            locator,
            object_kind="footprint",
        )

    def _transform(self, local: PointNM, origin: PointNM, turn: int, locator: str) -> PointNM:
        """Place one footprint-local point using KiCad's own rotation convention.

        KiCad stores board coordinates with y increasing downward while its ``(at x y angle)``
        angle is counter-clockwise *on screen*, so a positive angle is clockwise in the raw
        stored coordinates: ``x' = x cos t + y sin t`` and ``y' = -x sin t + y cos t``. A
        quarter turn is therefore ``(x, y) -> (y, -x)``, not the ``(-y, x)`` that a y-up
        reading would give. The two disagree by a mirror, which silently swaps the pads of
        every rotated two-pad footprint, so the table below is pinned by tests derived from
        KiCad's own connectivity engine rather than from this reasoning.
        """

        rotated = (
            local,
            PointNM(local.y, -local.x),
            PointNM(-local.x, -local.y),
            PointNM(-local.y, local.x),
        )[turn]
        x = origin.x + rotated.x
        y = origin.y + rotated.y
        if (
            not -JSON_SAFE_INTEGER <= x <= JSON_SAFE_INTEGER
            or not -JSON_SAFE_INTEGER <= y <= JSON_SAFE_INTEGER
        ):
            self.fail("integer.overflow", "transformed point exceeds the integer range", locator)
        return PointNM(x, y)

    def _roundrect_radius(self, ratio: str, short_side_nm: int, locator: str) -> tuple[int, int]:
        """Return the modelled corner radius and the nanometres it was rounded up by.

        KiCad stores a roundrect corner as a dimensionless *ratio* of the pad's shorter side and
        recomputes the radius on every read as ``KiROUND(ratio * min(size.x, size.y))``
        (``PADSTACK::RoundRectRadius``), while writing the ratio back with only ten significant
        digits.  The product of an ordinary ratio and an ordinary pad side is therefore routinely
        a *fractional* nanometre: 0.203125 of a 650,000 nm side is 132,031.25 nm.  Refusing that
        - which this adapter used to do - rejected five of twenty-three real boards for a
        quarter-nanometre, which is a precision artifact and not a modelling gap (issue #116).

        **The rounding is up, and the reason is not the obvious one.**  A pad is copper, so the
        instinct is to over-approximate it; but a *larger* radius means *more* corner rounding and
        therefore a *smaller* pad, so "round the copper outward" and "round the radius up" are
        opposite instructions.  The two roles resolve it separately, and they do not conflict:

        - As an **obstacle**, a pad is over-approximated by its full axis-aligned bounding box -
          ``_pad_extent`` in the router, ``pad_half_extents`` in placement and the scene, none of
          which consult the radius at all.  Corner rounding is discarded, conservatively, before
          the radius could matter.  So no rounding of the radius can shrink an obstacle.
        - As **attachment copper**, the radius is consumed only by the under-approximating inner
          core, which is the full-width band ``half_y - radius`` (``pad_core``,
          ``_pad_core_extent``).  A larger radius shrinks that band, and a band strictly inside
          the true pad is exactly what keeps the router from asserting a connection the board does
          not have.

        Rounding *up* is therefore the safe direction for the only role that reads the value, and
        it is safe against both candidate references without having to adjudicate between them:
        ``ceil(x) >= x`` covers the exact real radius, and ``ceil(x) >= KiROUND(x)`` covers the
        integer radius KiCad itself renders.  The modelled core is never taller than the true one
        under either reading.

        The arithmetic is exact integer rational - the ratio's own numerator and denominator over
        a pad side that ``mm_to_nm`` already produced exactly - so no binary float and no decimal
        precision context is involved, and ``remainder`` decides the rounding with no tolerance.
        """

        if len(ratio) > 64 or not _PLAIN_DECIMAL.fullmatch(ratio):
            self.fail("integer.precision", "roundrect ratio is malformed", locator)
        whole, _, fraction = ratio.partition(".")
        denominator: int = pow(10, len(fraction))
        numerator = int(whole) * denominator + (int(fraction) if fraction else 0)
        if numerator <= 0 or numerator * 2 > denominator:
            self.fail("geometry.invalid", "roundrect ratio must be in (0, 0.5]", locator)
        scaled_radius = numerator * short_side_nm
        radius, remainder = divmod(scaled_radius, denominator)
        rounding_nm = 0
        if remainder:
            radius += 1
            rounding_nm = 1
        if radius * 2 > short_side_nm:
            # Only reachable for a ratio within a half-nanometre of 0.5 on an odd-nanometre side,
            # where the exact pad has a sub-nanometre middle band. Rounding down instead would
            # give the pad a core taller than its real copper, and clamping to the representable
            # maximum would do the same, so this refuses rather than picking either.
            self.fail(
                "integer.precision",
                "roundrect radius rounds up beyond half the short pad side",
                locator,
            )
        return radius, rounding_nm

    def _courtyards(
        self,
        footprint: SExpr,
        *,
        footprint_locator: str,
        origin: PointNM,
        turn: int,
        side: FootprintSide,
    ) -> tuple[Ring, ...]:
        """Import exact closed orthogonal courtyard centerlines in the board frame.

        KiCad treats every closed shape on the matching courtyard layer as part of the footprint
        envelope.  The bounded Board-IR subset accepts unfilled rectangles and polygons plus
        complete ``fp_line`` cycles, but only when every edge is horizontal or vertical.  Empty
        still means that no courtyard was present; a malformed or unsupported shape is never
        silently omitted.
        """

        expected_layer = "F.CrtYd" if side is FootprintSide.FRONT else "B.CrtYd"
        result: list[Ring] = []
        line_segments: list[tuple[PointNM, PointNM, str]] = []

        def append(local_points: tuple[PointNM, ...], locator: str) -> None:
            if len(result) >= 64:
                self.fail(
                    "budget.exceeded",
                    "footprint courtyard limit exceeded",
                    locator,
                    object_kind="footprint",
                )
            try:
                result.append(
                    Ring(
                        tuple(
                            self._transform(point, origin, turn, locator) for point in local_points
                        )
                    )
                )
            except ValueError as error:
                self.fail("geometry.invalid", str(error), locator, object_kind="footprint")

        courtyard_index = 0
        for item in footprint.items[1:]:
            if not isinstance(item, SExpr) or item.head not in {"fp_line", "fp_poly", "fp_rect"}:
                continue
            locator = f"{footprint_locator}.courtyard[{courtyard_index}]"
            courtyard_index += 1
            layer = self._graphic_layer(item, locator)
            if layer not in _COURTYARD_LAYERS:
                continue
            if layer != expected_layer:
                self.fail(
                    "unsupported.transform",
                    "courtyard layer does not match its footprint side",
                    locator,
                    object_kind="footprint",
                )
            if item.head == "fp_rect":
                self._reject_unknown_children(
                    item,
                    frozenset(
                        {"end", "fill", "layer", "locked", "start", "stroke", "tstamp", "uuid"}
                    ),
                    locator,
                )
                self._validate_direct_atoms(
                    item,
                    positional_atoms=0,
                    allowed=frozenset({"locked"}),
                    locator=locator,
                )
                fill = self._values(item, "fill", locator, minimum=1, maximum=1, required=False)
                if fill and fill not in {("none",), ("no",)}:
                    self.fail(
                        "unsupported.construct",
                        "filled courtyard rectangle is unsupported",
                        locator,
                        object_kind="footprint",
                    )
                start = self._point(item, "start", locator)
                end = self._point(item, "end", locator)
                if start.x == end.x or start.y == end.y:
                    self.fail(
                        "geometry.invalid",
                        "courtyard rectangle must have non-zero width and height",
                        locator,
                        object_kind="footprint",
                    )
                append((start, PointNM(end.x, start.y), end, PointNM(start.x, end.y)), locator)
            elif item.head == "fp_poly":
                append(self._courtyard_polygon_points(item, locator), locator)
            else:
                line_segments.append(self._courtyard_line_segment(item, locator))

        for index, ring in enumerate(
            self._closed_courtyard_line_rings(line_segments, footprint_locator)
        ):
            append(ring, f"{footprint_locator}.line_chain[{index}]")
        return tuple(result)

    def _courtyard_polygon_points(self, polygon: SExpr, locator: str) -> tuple[PointNM, ...]:
        """Read one unfilled orthogonal ``fp_poly`` without inventing a closing point."""

        self._reject_unknown_children(
            polygon,
            frozenset({"fill", "layer", "locked", "pts", "stroke", "tstamp", "uuid"}),
            locator,
        )
        self._validate_direct_atoms(
            polygon, positional_atoms=0, allowed=frozenset({"locked"}), locator=locator
        )
        fill = self._values(polygon, "fill", locator, minimum=1, maximum=1, required=False)
        if fill and fill not in {("none",), ("no",)}:
            self.fail(
                "unsupported.construct",
                "filled courtyard polygon is unsupported",
                locator,
                object_kind="footprint",
            )
        points_expression = self._one(polygon, "pts", locator)
        assert points_expression is not None
        self._reject_unknown_children(points_expression, frozenset({"xy"}), f"{locator}.pts")
        self._validate_direct_atoms(
            points_expression, positional_atoms=0, allowed=frozenset(), locator=f"{locator}.pts"
        )
        point_expressions = children(points_expression, "xy")
        if len(point_expressions) > self.limits.max_vertices_per_ring + 1:
            self.fail("budget.exceeded", "ring vertex budget exceeded", locator)
        points: list[PointNM] = []
        for index, point in enumerate(point_expressions):
            values = atoms(point)
            if len(values) != 2:
                self.fail(
                    "syntax.invalid",
                    "courtyard polygon point is malformed",
                    f"{locator}.point[{index}]",
                )
            points.append(
                PointNM(
                    self._mm(values[0], f"{locator}.point[{index}].x"),
                    self._mm(values[1], f"{locator}.point[{index}].y"),
                )
            )
        if len(points) >= 2 and points[0] == points[-1]:
            points.pop()
        self._require_orthogonal_chain(tuple(points), locator)
        return tuple(points)

    def _courtyard_line_segment(self, line: SExpr, locator: str) -> tuple[PointNM, PointNM, str]:
        """Read one line which must later join one complete courtyard cycle."""

        self._reject_unknown_children(
            line,
            frozenset({"end", "layer", "locked", "start", "stroke", "tstamp", "uuid"}),
            locator,
        )
        self._validate_direct_atoms(
            line, positional_atoms=0, allowed=frozenset({"locked"}), locator=locator
        )
        start = self._point(line, "start", locator)
        end = self._point(line, "end", locator)
        self._require_orthogonal_chain((start, end), locator)
        return (start, end, locator)

    def _closed_courtyard_line_rings(
        self, segments: list[tuple[PointNM, PointNM, str]], footprint_locator: str
    ) -> tuple[tuple[PointNM, ...], ...]:
        """Reconstruct unordered ``fp_line`` graphics into exact simple closed cycles."""

        if not segments:
            return ()
        adjacency: dict[PointNM, list[tuple[int, PointNM]]] = {}
        seen: set[tuple[PointNM, PointNM]] = set()
        for index, (start, end, locator) in enumerate(segments):
            key = (start, end) if start < end else (end, start)
            if key in seen:
                self.fail(
                    "geometry.invalid",
                    "courtyard line chain has a duplicate edge",
                    locator,
                    object_kind="footprint",
                )
            seen.add(key)
            adjacency.setdefault(start, []).append((index, end))
            adjacency.setdefault(end, []).append((index, start))
        for _point, links in adjacency.items():
            if len(links) != 2:
                self.fail(
                    "geometry.invalid",
                    "courtyard line chain must form closed non-branching loops",
                    footprint_locator,
                    object_kind="footprint",
                )

        visited: set[int] = set()
        rings: list[tuple[PointNM, ...]] = []
        for seed in range(len(segments)):
            if seed in visited:
                continue
            component_points: set[PointNM] = set()
            pending = [segments[seed][0]]
            while pending:
                point = pending.pop()
                if point in component_points:
                    continue
                component_points.add(point)
                pending.extend(other for _, other in adjacency[point])
            start = min(component_points)
            current = start
            previous_edge: int | None = None
            points: list[PointNM] = [start]
            while True:
                choices = sorted(
                    (edge, other) for edge, other in adjacency[current] if edge != previous_edge
                )
                if not choices:
                    self.fail(
                        "geometry.invalid",
                        "courtyard line chain cannot close",
                        footprint_locator,
                        object_kind="footprint",
                    )
                edge, next_point = choices[0]
                if edge in visited and next_point != start:
                    self.fail(
                        "geometry.invalid",
                        "courtyard line chain reuses an edge before closure",
                        footprint_locator,
                        object_kind="footprint",
                    )
                visited.add(edge)
                previous_edge, current = edge, next_point
                if current == start:
                    break
                points.append(current)
                if len(points) > self.limits.max_vertices_per_ring:
                    self.fail("budget.exceeded", "ring vertex budget exceeded", footprint_locator)
            self._require_orthogonal_chain(tuple(points), footprint_locator)
            rings.append(tuple(points))
        return tuple(rings)

    def _require_orthogonal_chain(self, points: tuple[PointNM, ...], locator: str) -> None:
        """Fail closed unless every supplied edge is a non-zero orthogonal segment."""

        if len(points) < 2:
            self.fail("geometry.invalid", "courtyard chain has too few points", locator)
        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            if (start.x == end.x) == (start.y == end.y):
                self.fail(
                    "unsupported.topology",
                    "courtyard edges must be non-zero and axis-aligned",
                    locator,
                    object_kind="footprint",
                )

    def _footprints_and_pads(self) -> tuple[tuple[Footprint, ...], tuple[Pad, ...]]:
        footprints: list[Footprint] = []
        pads: list[Pad] = []
        for footprint_index, footprint in enumerate(children(self.root, "footprint")):
            footprint_locator = f"kicad_pcb.footprint[{footprint_index}]"
            if children(footprint, "net_tie_pad_groups"):
                self.fail(
                    "unsupported.construct",
                    "net-tie footprints are unsupported in Board IR adapter v0.2",
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
                    "jumper pad-number semantics are unsupported in Board IR adapter v0.2",
                    footprint_locator,
                    object_kind="footprint",
                )
            layer = self._values(footprint, "layer", footprint_locator, minimum=1, maximum=1)[0]
            side = self._footprint_side(layer, footprint_locator)
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
            footprint_id = self._identity("footprint", footprint, footprint_locator)
            owned_pad_ids: list[str] = []
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
                # A pad's *position* is footprint-local and must be transformed by the
                # footprint's placement, which `_transform` above does. Its *orientation* is
                # not: KiCad stores the pad angle already resolved into the board frame and
                # rewrites every pad angle when a footprint is rotated, so adding the
                # footprint rotation here counted it twice and transposed the extents of
                # every non-square pad on a rotated footprint.
                #
                # Established by experiment against KiCad 10.0.5 rather than from the format
                # documentation: a 4mm x 1mm pad at `(at 3 0)` inside a footprint placed at
                # 90 degrees is drawn by `kicad-cli pcb export svg` at the rotated position
                # (30, 7) but with its extents still 4mm x 1mm. Position rotates, shape does
                # not. `tests/test_kicad_board_ir.py` pins this against KiCad's own renderer.
                rotation = self._rotation(
                    pad_at[2] if len(pad_at) == 3 else "0", f"{locator}.at.rotation"
                )
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
                    radius, rounding_nm = self._roundrect_radius(
                        ratio, min(size_x, size_y), locator
                    )
                    self.max_roundrect_rounding_nm = max(
                        self.max_roundrect_rounding_nm, rounding_nm
                    )
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
                pad_item = Pad(
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
                pads.append(pad_item)
                owned_pad_ids.append(pad_item.id)
            footprints.append(
                Footprint(
                    id=footprint_id,
                    origin=origin,
                    rotation_udeg=footprint_rotation,
                    side=side,
                    pad_ids=tuple(owned_pad_ids),
                    courtyards=self._courtyards(
                        footprint,
                        footprint_locator=footprint_locator,
                        origin=origin,
                        turn=turn,
                        side=side,
                    ),
                    locked=footprint_locked,
                )
            )
        return tuple(footprints), tuple(pads)

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

    def _edge_cuts_line_segments(self) -> list[tuple[PointNM, PointNM, str]]:
        """Read every root ``gr_line`` drawn on ``Edge.Cuts`` as an exact integer segment."""

        segments: list[tuple[PointNM, PointNM, str]] = []
        for index, expression in enumerate(children(self.root, "gr_line")):
            locator = f"kicad_pcb.gr_line[{index}]"
            layer_values = self._values(
                expression,
                "layer",
                locator,
                minimum=1,
                maximum=1,
                required=False,
            )
            if layer_values != ("Edge.Cuts",):
                continue
            self._reject_unknown_children(expression, _EDGE_CUTS_LINE_FIELDS, locator)
            self._validate_direct_atoms(
                expression,
                positional_atoms=0,
                allowed=frozenset({"locked"}),
                locator=locator,
            )
            start = self._point(expression, "start", locator)
            end = self._point(expression, "end", locator)
            if start == end:
                self.fail(
                    "geometry.invalid",
                    "Edge.Cuts outline carries a zero-length segment",
                    locator,
                    object_kind="outline",
                )
            segments.append((start, end, locator))
        return segments

    def _edge_cuts_segment_ring(self, segments: list[tuple[PointNM, PointNM, str]]) -> Ring:
        """Assemble unordered ``Edge.Cuts`` segments into exactly one closed simple ring.

        The board outline is routing *room*, not an obstacle, so the direction of error here is
        the opposite of the one an obstacle envelope takes: the modelled contour must be
        **contained within** the outline the board draws, never larger, or the router is handed
        copper the fabricated board does not have.  Straight segments make that containment
        exact - every ring vertex is a drawn endpoint and nothing is synthesized - and the only
        way to break it is to *repair* the input, so this method never does.

        Endpoints must coincide exactly.  KiCad chains its own outline with a non-zero epsilon
        (``ConvertOutlineToPolygon``'s ``aChainingEpsilon``) and will close a sub-tolerance gap
        for you; closing a gap adds area no drawn segment encloses, so a near-miss is refused
        here instead.  Duplicate segments, a vertex of degree other than two, a second disjoint
        loop, and anything past the ring budget are refusals for the same reason: each has more
        than one plausible repair and every repair invents board.
        """

        if len(segments) > self.limits.max_vertices_per_ring:
            self.fail(
                "budget.exceeded",
                "Edge.Cuts outline segment budget exceeded",
                "kicad_pcb",
                object_kind="outline",
            )
        adjacency: dict[PointNM, list[tuple[int, PointNM]]] = {}
        seen: set[tuple[PointNM, PointNM]] = set()
        for index, (start, end, locator) in enumerate(segments):
            key = (start, end) if start < end else (end, start)
            if key in seen:
                self.fail(
                    "geometry.invalid",
                    "Edge.Cuts outline has a duplicate segment",
                    locator,
                    object_kind="outline",
                )
            seen.add(key)
            adjacency.setdefault(start, []).append((index, end))
            adjacency.setdefault(end, []).append((index, start))
        for links in adjacency.values():
            if len(links) != 2:
                self.fail(
                    "geometry.invalid",
                    "Edge.Cuts outline must be one closed non-branching loop",
                    "kicad_pcb",
                    object_kind="outline",
                )

        origin = min(adjacency)
        points: list[PointNM] = [origin]
        current, previous_edge = origin, -1
        while True:
            choices = sorted(
                (edge, other) for edge, other in adjacency[current] if edge != previous_edge
            )
            previous_edge, current = choices[0]
            if current == origin:
                break
            points.append(current)
        if len(points) != len(segments):
            self.fail(
                "unsupported.topology",
                "multiple disjoint Edge.Cuts loops are unsupported",
                "kicad_pcb",
                object_kind="outline",
            )
        # Two segments cannot close a loop without repeating an edge, which the duplicate check
        # above already refuses, so ``Ring`` sees at least three points here.  Its own contract -
        # three distinct vertices, no repeated closing point, non-zero area - is what rejects a
        # degenerate all-collinear cycle, and ``validate_content`` is what rejects a ring that
        # crosses itself away from a shared vertex.
        return Ring(tuple(points))

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
        segments = self._edge_cuts_line_segments()
        if segments:
            contours.append(
                OutlineContour(
                    id=self._derived_identity("contour", "kicad_pcb.edge_cuts"),
                    outer=self._edge_cuts_segment_ring(segments),
                )
            )
        for head in ("gr_arc", "gr_circle", "gr_poly", "gr_curve"):
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
                        "Edge.Cuts outline arcs, circles, polygons and curves are unsupported",
                        locator,
                        object_kind="outline",
                    )
        if not contours:
            self.fail("geometry.missing", "board has no supported Edge.Cuts outline", "kicad_pcb")
        if len(contours) != 1:
            self.fail(
                "unsupported.construct",
                "the board must carry exactly one Edge.Cuts outline contour",
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
        footprints, pads = self._footprints_and_pads()
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
            footprints=footprints,
            pads=pads,
            vias=self._vias(),
            segments=self._segments(),
            arcs=self._arcs(),
            zones=zones,
            keepouts=keepouts,
        )
        return ConversionResult(
            snapshot=make_snapshot(content),
            max_roundrect_rounding_nm=self.max_roundrect_rounding_nm,
        )


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
