"""Fail-closed, read-only KiCad S-expression to Board IR v0.2 adapter."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from math import isqrt
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
from copper_mcp.board_ir.limits import ParseBudget, ParseLimits
from copper_mcp.board_ir.types import (
    JSON_SAFE_INTEGER,
    Arc,
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
# KiCad's board-level grouping construct, and one of the root sections the board file format
# enumerates ("Header, General, Layers, Setup, Properties, Nets, Footprints, Graphic Items, Images,
# Tracks, Zones, Groups").  A group is organisation, not geometry: KiCad's own class comment reads
# "A set of BOARD_ITEMs ... The group is transparent container - e.g., its position is derived from
# the position of its members", `PCB_GROUP::SetLayer` is a no-op, and `IsOnCopperLayer` returns
# false because "a group might have members on a copper layer, but isn't itself on any layer".
# Every member is named by UUID and is a board object this adapter already converts on its own, so
# an *unlocked* group adds no copper, no outline, no net and no constraint.  Ignoring one therefore
# cannot shrink an obstacle or grow the routing room: both directions of error are untouched
# because nothing geometric is read from it in the first place.  See
# docs/research/kicad-board-groups-v1.md and ADR-0090.
#
# A *locked* group is a different construct and is refused.  `BOARD_ITEM::IsLocked()`
# (pcbnew/board_item.cpp) begins `if( EDA_GROUP* group = GetParentGroup() ) { if(
# group->AsEdaItem()->IsLocked() ) return true; }` -- so a locked group makes every member locked in
# KiCad's own model, transitively through nested groups, without any member's own s-expression
# saying so.  Lock is a hard authorization gate here, not a hint, and a member read as unlocked
# would authorize a move KiCad forbids.  This is the one asymmetric direction in the construct, and
# it is exactly the direction that must fail closed.  See `_check_root_group`.
_ROOT_GROUP_HEAD = "group"
# The child heads `PCB_IO_KICAD_SEXPR::format( const PCB_GROUP* )` can emit, after the quoted group
# name: the group's own uuid, an optional `locked` flag, an optional design-block `lib_id`, and the
# member list.  This is a *depth-one head* allowlist, not a full grammar -- it constrains which
# children may appear, not what nests inside them.  That is sufficient here because no child of a
# group carries geometry, connectivity or a constraint that Board IR models, with `locked` the one
# exception, which is read and refused rather than allowlisted through.  A group carrying any other
# head is a construct this adapter has not read, and is refused rather than assumed inert.
_ROOT_GROUP_HEADS = frozenset({"lib_id", "locked", "members", "uuid"})
# The only `locked` value a group may carry and still be read past.  KiCad writes the flag only
# when the group *is* locked (`FormatBool`), so this is a defensive accept rather than an observed
# form; every other value, `yes` included, refuses.
_UNLOCKED_GROUP_VALUES = ("no",)
# Root sections the KiCad board file format defines and Board IR v0.2 does not model, mapped to
# the refusal each one earns.  The message is a *value from this table*, selected by an equality
# test against the source token and never built from it, so a refusal can name the construct
# without echoing one byte of the board -- the board's own text is untrusted data and a head is
# board bytes like any other.  A head absent from this table is not a documented root section at
# all; it is refused without being named, and the indexed locator still says where it sits.  The
# table is deliberately partial: an entry is added when the construct is cited, not guessed.
_UNMODELLED_ROOT_HEADS: dict[str, str] = {
    "dimension": "root dimension objects are unsupported",
    "image": "root embedded images are unsupported",
    "property": "root board properties are unsupported",
}
# There is deliberately no `_UNMODELLED_PAD_KINDS` table any more, and its absence is the
# statement.  KiCad's pad attribute is a *closed* four-token vocabulary: `parsePAD` switches on
# exactly `thru_hole`, `smd`, `connect` and `np_thru_hole` and calls `Expecting(...)` on anything
# else (`pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:6411-6444`), and the writer emits
# exactly those four (`…/pcb_io_kicad_sexpr.cpp:1875-1882`).  ADR-0096 mapped the fourth,
# `connect`, so all four documented kinds are now modelled and no documented-but-unmodelled kind
# remains.  A one-entry table with nothing left to name would have been the same dead code
# ADR-0091 found behind the pad-field allowlist -- a lookup that can never miss the default -- so
# it is deleted rather than kept empty.  A token that reaches the refusal below is not a
# documented pad kind at all: it refuses unnamed, without echoing one byte of the board, and the
# indexed locator still says which pad.
#
# The mapping is a module constant rather than a dict rebuilt inside the pad loop so that a test
# can assert its *whole domain* against KiCad's four tokens. That is the partition test ADR-0091
# needed for `zone_connect`: a behavioural test can only probe tokens someone thought to write
# down, and a fifth key quietly added here would change no board that exists. Mutation found
# exactly that survivor.
_PAD_KIND_BY_TOKEN: dict[str, PadKind] = {
    "smd": PadKind.SMD,
    # KiCad's `PAD_ATTRIB::CONN`, the edge-card connector finger. See ADR-0096 and the comment at
    # the mapping's use site for why this is `SMD` and not a member of its own.
    "connect": PadKind.SMD,
    "thru_hole": PadKind.THROUGH_HOLE,
    "np_thru_hole": PadKind.NPTH,
}
# The only root graphics that may sit on ``Edge.Cuts``: one unfilled rectangle, or straight
# segments that chain into exactly one closed simple loop.  Both carry the board outline as an
# exact integer polygon whose vertices are drawn points, so neither can model more room than the
# board has.  See docs/research/edge-cuts-outline-assembly-v1.md and ADR-0080.
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
        # KiCad's placement status flag: "the optional `placed` token defines a flag to indicate
        # that the footprint has not been placed" (KiCad S-expression format, footprint). It is
        # editor bookkeeping for the autoplacer -- no geometry, no layer, no constraint -- and it
        # is emphatically *not* `locked`, which is a real constraint and is modelled separately.
        # Accepting it therefore ignores nothing CopperMCP would otherwise have honoured. Found on
        # a real board that carried `(placed yes)` on all 31 of its footprints and was refused
        # outright for it. See docs/research/kicad-aperture-pads-and-net-ties-v1.md.
        "placed",
        "property",
        "tags",
        "tstamp",
        "uuid",
    }
)
# The non-copper technical layers a KiCad *aperture* pad may occupy. KiCad defines an aperture pad
# as one with no copper layer assigned: a solder-paste stencil opening or mask opening that is not
# an electrical connection point and cannot even carry a pad number. See
# docs/research/kicad-aperture-pads-and-net-ties-v1.md.
_APERTURE_PAD_LAYERS = frozenset({"B.Mask", "B.Paste", "F.Mask", "F.Paste"})

# Pad fields that change the pad's own copper, its clearance, or its thermal-relief geometry.
# Each one would make Board IR describe copper it cannot derive, so each refuses by name.
# `zone_connect` deliberately is *not* in this tuple; see ADR-0091 and
# `_require_attaching_pad_zone_connection`.
_UNSUPPORTED_PAD_FIELDS = (
    "clearance",
    "offset",
    "options",
    "primitives",
    "thermal_bridge_angle",
    "thermal_bridge_width",
    "thermal_gap",
)

# KiCad's `ZONE_CONNECTION` enum as a pad writes it: 0 NONE, 1 THERMAL, 2 FULL, 3 THT_THERMAL.
# `INHERITED` (-1) is never written -- an absent token *is* inheritance. 1, 2 and 3 all attach the
# pad to a same-net pour (3 resolves to 1 on a plated through-hole pad and to 2 otherwise), so
# discarding them can only under-state attachment. 0 detaches, which is the one direction Board IR
# may not lose. See ADR-0091 and docs/research/kicad-pad-zone-connect-v1.md.
_ATTACHING_PAD_ZONE_CONNECTIONS = frozenset({"1", "2", "3"})
_DETACHING_PAD_ZONE_CONNECTION = "0"


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
        ambiguous_native_identities: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        self.payload = payload
        self.root = root
        self.profile = profile
        self.limits = limits
        # Native KiCad identities this file reuses across two or more objects of one kind, keyed
        # as ``(kind, value)``.  Empty on the first pass; ``convert`` re-runs with the measured
        # set so that a reused identity is never projected as a Board IR identity.
        self.ambiguous_native_identities = ambiguous_native_identities
        self.native_identity_uses: Counter[tuple[str, str]] = Counter()
        self.source_revision = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        # Largest single roundrect radius rounding, in nanometres, measured rather than asserted.
        self.max_roundrect_rounding_nm = 0
        # Root ``(group ...)`` expressions accepted as editor organisation and not modelled.
        # Measured in the preflight and reported rather than dropped in silence.
        self.root_group_count = 0
        # ``connect``-token pads converted as ``PadKind.SMD`` (ADR-0096).  Counted for the same
        # reason ``root_group_count`` is: the conversion discards a distinction the source made,
        # and a count is the only way to say so without a diagnostic that would refuse the board.
        self.edge_connector_pad_count = 0
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

    def _check_root_group(self, expression: SExpr, locator: str) -> None:
        """Accept one *unlocked* root ``(group ...)`` as editor organisation, on a closed shape.

        The acceptance argument is about what a group *is*, and it is conditional, so each
        condition is checked and refuses rather than being assumed:

        - **It is not locked.** This is the condition the first version of this change missed, and
          it is the only one that carries a safety consequence. ``BOARD_ITEM::IsLocked()`` opens
          with ``if( EDA_GROUP* group = GetParentGroup() ) { if( group->AsEdaItem()->IsLocked() )
          return true; }``, so a locked group makes every member locked in KiCad's own model --
          transitively, and without any member's own s-expression saying so. Lock is a hard
          authorization gate in this project, not a hint: ``placement/solver.py`` will not select a
          locked footprint as a subject, ``placement/legalizer.py`` raises "moving a locked
          footprint is not authorized", and ``kicad_placement_patch.py`` refuses "locked footprint
          movement is unsupported". Reading a locked group past would present its members at
          ``locked=False`` and authorize a move KiCad forbids. That is the unsafe direction, so it
          fails closed.
        - **It carries no geometry of its own.** KiCad models a group as a "transparent container"
          whose position "is derived from the position of its members", with a no-op ``SetLayer``
          and an ``IsOnCopperLayer`` that is false by construction. Every member is named by UUID
          and is itself a root object this adapter converts on its own terms. So the copper this
          board contains, the outline it contains and the nets it contains are exactly the same
          set with an unlocked group read as with it ignored. There is no quantity to round the
          wrong way -- which is exactly why *lock* had to be found by reading KiCad's model rather
          than by comparing two conversions, since a constraint that lives in a runtime derivation
          is invisible to any equality between two outputs of the same reader.
        - **Its children are only the heads KiCad's writer emits.** A group carrying an unknown
          child is a construct that has not been read, and it refuses. Widening the root allowlist
          by one head does not open it. This is a depth-one head check, not a full grammar: it
          constrains which children may appear, not what nests inside them, which is sufficient
          because no group child carries geometry or connectivity Board IR models, and the one
          child that carries a constraint is read rather than allowlisted through.
        - **It has the writer's leading name atom and no other positional semantics.** A bare
          ``(group)`` or a group with stray trailing atoms is malformed for this adapter and
          refuses rather than being waved through as inert.

        What is *not* claimed is that the grouping survives an edit. A caller that moves one member
        of a group the designer meant to keep together breaks the designer's intent, and Board IR
        has no field in which to hold that intent. That is a modelling gap, so it is recorded --
        ``ConversionResult.unmodelled_group_count`` -- rather than being silently dropped or
        allowed to masquerade as a modelled constraint. See R-134 and ADR-0090.
        """

        self._reject_unknown_children(expression, _ROOT_GROUP_HEADS, locator)
        self._validate_direct_atoms(
            expression,
            positional_atoms=1,
            allowed=frozenset(),
            locator=locator,
        )
        locked = self._values(expression, "locked", locator, minimum=1, maximum=1, required=False)
        if locked and locked != _UNLOCKED_GROUP_VALUES:
            self.fail(
                "unsupported.construct",
                "a locked group locks its members and is unsupported",
                locator,
                object_kind="group",
            )

    def _semantic_preflight(self) -> None:
        """Reject physical semantics that the v0.2 model cannot preserve."""

        groups = 0
        for index, item in enumerate(self.root.items[1:]):
            # The index is computed here, not read from the board, so it names the position of the
            # offending child without echoing anything the board author controls.  Before this,
            # every root refusal reported the constant ``kicad_pcb.unsupported`` and an operator
            # could not locate the construct without a debugger.
            root_locator = f"kicad_pcb.child[{index}]"
            if not isinstance(item, SExpr) or item.head is None:
                self.fail(
                    "syntax.invalid", "root expression contains a malformed item", root_locator
                )
            head = item.head
            if head in _ROOT_METADATA_HEADS or head in _ROOT_ROUTING_HEADS:
                continue
            if head == _ROOT_GROUP_HEAD:
                self._check_root_group(item, root_locator)
                groups += 1
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
                _UNMODELLED_ROOT_HEADS.get(
                    head, "root expression contains an unsupported semantic construct"
                ),
                root_locator,
            )
        self.root_group_count = groups

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
                        # ``fp_arc`` stays refused: an arc is a fragment of a chain rather
                        # than a closed shape, so no region exists to bound until the whole
                        # curved chain is modelled - an outward guess would publish
                        # ``violated`` evidence KiCad does not share (ADR-0072 discusses the
                        # obstacle direction; a keep-out on an evidence surface cannot copy
                        # it).
                        if head not in {"fp_circle", "fp_line", "fp_poly", "fp_rect"}:
                            self.fail(
                                "unsupported.construct",
                                "courtyard primitive is unsupported by Board IR v0.2",
                                f"{locator}.courtyard",
                                object_kind="footprint",
                            )
                        continue
                    if self._is_routing_layer(layer):
                        if (
                            layer != "Edge.Cuts"
                            and head == "fp_poly"
                            and children(footprint, "net_tie_pad_groups")
                        ):
                            # Declared net-tie copper: the polygon *is* the deliberate short
                            # between the tied pad groups, and it converts -- fully validated
                            # -- in `_footprints_and_pads` rather than being refused here as a
                            # stray drawing. See `_net_tie_copper_segments` and ADR-0092.
                            continue
                        self._refuse_footprint_routing_graphic(footprint, layer, locator)
                    continue
                if head not in _FOOTPRINT_METADATA_HEADS:
                    self.fail(
                        "unsupported.construct",
                        "footprint contains an unsupported semantic field",
                        f"{locator}.unsupported",
                        object_kind="footprint",
                    )

    def _refuse_footprint_routing_graphic(
        self, footprint: SExpr, layer: str, locator: str
    ) -> Never:
        """Refuse a footprint graphic on a routing layer, naming what it actually is.

        All three cases refuse, and that is the point: a graphic on a copper layer *is copper*, so
        it is an obstacle, and the one outcome forbidden here is dropping it. What differs is the
        reason, and the three reasons ask for different fixes:

        - **A net-tie primitive that is not a filled polygon.** `net_tie_pad_groups` declares that
          "nets attached to pads within a single pad-group are allowed to short" (KiCad
          S-expression format), and the copper on `F.Cu`/`B.Cu` is the short. A net-tie `fp_poly`
          no longer reaches this method: the preflight passes it through and
          `_net_tie_copper_segments` converts it as a netless obstacle with no connectivity
          contribution (ADR-0092, the same contract net-0 copper has under ADR-0078). Every other
          primitive a net-tie footprint could draw its short with -- `fp_line`, `fp_arc`,
          `fp_rect`, `fp_circle` -- is unobserved on real boards and refuses here by name.
        - **`Edge.Cuts`.** A footprint graphic contributing to the board outline is the opposite
          direction of error from copper: the outline is routing *room* and may only be
          under-approximated (ADR-0076), so it is a separate question from an obstacle and is
          named separately rather than sharing copper's message.
        - **Any other copper layer.** Copper the adapter does not model. Refused, never ignored: a
          conservative envelope would be admissible here (ADR-0072's direction), but no real board
          surveyed carries one, so inventing the envelope would be modelling a case that has not
          been observed.
        """

        if layer == "Edge.Cuts":
            self.fail(
                "unsupported.construct",
                "footprint graphic on Edge.Cuts is unsupported",
                f"{locator}.graphic",
                object_kind="outline",
            )
        if children(footprint, "net_tie_pad_groups"):
            self.fail(
                "unsupported.construct",
                "net-tie copper must be a filled polygon; other primitives are unsupported",
                f"{locator}.graphic",
                object_kind="footprint",
            )
        self.fail(
            "unsupported.construct",
            "footprint graphic on a copper layer is unmodelled copper",
            f"{locator}.graphic",
            object_kind="graphic",
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
            native = identities[0][1].lower()
            self.native_identity_uses[(kind, native)] += 1
            # A KiCad UUID "should be globally unique" but is not required to be, and real boards
            # reuse one value across every instance of a footprint type.  Board IR identity is a
            # per-object invariant, so a reused value is not an identity here: it degrades to the
            # revision-derived name, which write-back already refuses.  See
            # docs/research/kicad-uuid-uniqueness-v1.md.
            if (kind, native) not in self.ambiguous_native_identities:
                return f"{kind}:kicad:{native}"
        return self._derived_identity(kind, locator)

    def _derived_identity(self, kind: str, locator: str) -> str:
        """Name an object that carries no usable native KiCad identity of its own."""

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
            if net_code == 0:
                # KiCad's net 0 is the "unconnected" net: real copper with no netlist claim.
                return None
            if net_code < 0:
                self.fail("net.unknown", "numeric net reference is negative", locator)
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

    def _layer_names(self, expression: SExpr, locator: str) -> tuple[str, ...]:
        """Return an item's declared layer names, from either the `layer` or `layers` field."""

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
        return values

    def _layer_ids(self, expression: SExpr, locator: str) -> tuple[str, ...]:
        values = self._layer_names(expression, locator)
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

    def _is_aperture_pad(self, pad: SExpr, number: str, raw_kind: str, locator: str) -> bool:
        """Report whether a `pad` expression is a KiCad *aperture* pad, and refuse if it is not.

        KiCad defines an aperture pad as a pad **with no copper layer assigned**: a solder-paste
        stencil opening, most often used to subdivide the paste over an exposed thermal tab. It is
        not an electrical connection point, and KiCad will not even let it carry a pad number
        (`PAD::CanHaveNumber` is false for one). The real board that found this gap carries eight
        of them -- four each on two `TO-252-2` transistors, `(pad "" smd roundrect …
        (layers "F.Paste"))` -- alongside the ordinary copper pad they sit on top of.

        The adapter's expectation was the wrong half of the pair: it required every `pad` to
        resolve to at least one copper layer, and refused the whole board when one did not. The
        item is not wrong -- KiCad wrote exactly what it meant.

        Dropping one is safe in the one direction that matters. Attachment copper may only be
        *under*-approximated and obstacle copper only over-approximated, and an aperture pad is
        neither: it has no copper at all, so it removes no obstacle and no attachment point. The
        copper the paste actually sits on is a *separate* pad in the same footprint and is
        converted normally. The conditions below are what make that claim true rather than assumed,
        and each one that fails is a refusal rather than a silent drop:

        - no declared layer is copper, so there is nothing to lose;
        - every declared layer is a paste or mask layer, so this is a stencil or mask opening and
          not, say, a stray pad on a courtyard layer whose meaning we have not established;
        - the **source token** is literally `smd`, since a drilled pad is a hole through copper
          whatever its layers claim. This tests the token and not the resolved `PadKind`, and the
          difference is load-bearing after ADR-0096: `connect` now resolves to `PadKind.SMD` too,
          and a paste-bearing `connect` pad is a construct KiCad's own padstack test calls an
          error (`pad.cpp:3252-3257`, `DRCE_PADSTACK`). Its meaning is not established, so it
          keeps refusing here rather than being read past as an aperture. Nothing measurable is
          lost -- no board in the surveyed corpus carries one -- and over-refusal is the
          conservative direction;
        - it declares no net, so no routable attachment is being discarded; and
        - it carries no pad number, matching KiCad's own rule for an aperture.

        See docs/research/kicad-aperture-pads-and-net-ties-v1.md.
        """

        names = self._layer_names(pad, locator)
        if any(name.endswith(".Cu") or name in {"*.Cu", "F&B.Cu"} for name in names):
            return False
        if (
            raw_kind == "smd"
            and not number
            and all(name in _APERTURE_PAD_LAYERS for name in names)
            and not children(pad, "net")
            and not children(pad, "drill")
        ):
            return True
        self.fail(
            "unknown.layer",
            "pad references no copper layer and is not a paste or mask aperture",
            locator,
            object_kind="pad",
        )

    def _require_attaching_pad_zone_connection(self, pad: SExpr, locator: str) -> None:
        """Accept a pad `zone_connect` override only when it *attaches* the pad to its pour.

        `zone_connect` is an input to KiCad's own zone filler, and the filler is the only thing
        that turns it into copper. (Two other places *read* it -- the starved-thermal DRC test and
        the UI inspection tool -- and neither produces geometry.) It does not move the pad, change
        the pad's shape, change any clearance the router honours, or change the zone outline.
        In `ZONE_FILLER::knockoutThermalReliefs` the resolved value selects one
        of three treatments -- `THERMAL` knocks out a thermal-gap annulus and adds spokes back,
        `NONE` knocks the pad out with clearance, `FULL` knocks nothing out -- and the finished
        fill is intersected with the zone's own extents, so poured copper stays a subset of the
        zone boundary for *every* value. That is what keeps the boundary obstacle of ADR-0013 an
        over-approximation here, and the exact-fill obstacle of ADR-0039/ADR-0070 is KiCad's own
        recomputed polygon, which already has the value applied.

        So the field cannot break the obstacle direction. What it can break is the connectivity
        direction, and only in one of its values. Board IR already publishes a pad-to-pour
        attachment statement -- `Zone.pad_connection`, parsed from the zone's `connect_pads`,
        carried into every snapshot digest but not into Circuit Scene -- and a pad's
        `zone_connect` overrides that statement for one pad:

        - `1` (thermal relief), `2` (solid fill) and `3` (through-hole thermal, which KiCad's
          `DRC_ENGINE::EvalZoneConnection` resolves to `1` on a plated through-hole pad and to `2`
          on any other) all *attach*. Discarding one never turns `Zone.pad_connection` into a claim
          of attachment where there is none. It can leave the published *mode* wrong in either
          direction -- a zone saying `solid` over a pad overridden to `1` overstates the copper, a
          zone saying `no` over a pad overridden to `2` understates it -- but both readings still
          answer "attached", so no connection the board lacks is ever claimed.
        - `0` *detaches*. Discarding it can leave Board IR publishing `solid` or `thermal`
          attachment for a pad its designer deliberately isolated -- a claimed connection the
          board does not have, which is the one direction this project forbids. It is also the
          only value whose information no other Board IR field records, so it is refused even
          where the zone itself already says `no` and the loss would be provably harmless.

        Nothing here models the value. Board IR carries no pad-level zone-connection field, and a
        board carrying `1`, `2` or `3` converts to content equal to the same board without it in
        every field but `source.revision`, which is the digest of the file bytes and must move
        because they did. That equality measures a no-op and schema stability; it is *not* a
        soundness argument, because the converter propagates nothing and the equality would hold
        just as well if `0` were admitted. Soundness rests on the KiCad semantics above plus
        ADR-0021's rule that pad-to-pour attachment comes only from verified fill. If a future
        surface ever infers it from anything else, this acceptance becomes unsound and must be
        revisited -- see R-135.

        See ADR-0091 and docs/research/kicad-pad-zone-connect-v1.md.
        """

        values = self._values(
            pad,
            "zone_connect",
            locator,
            minimum=1,
            maximum=1,
            required=False,
        )
        if not values:
            return
        value = values[0]
        if is_quoted_atom(value) or (
            value != _DETACHING_PAD_ZONE_CONNECTION and value not in _ATTACHING_PAD_ZONE_CONNECTIONS
        ):
            self.fail(
                "unsupported.construct",
                "pad zone connection mode is unsupported",
                locator,
                object_kind="pad",
            )
        if value == _DETACHING_PAD_ZONE_CONNECTION:
            self.fail(
                "unsupported.construct",
                "pad zone_connect 0 detaches the pad from its pour and is unsupported",
                locator,
                object_kind="pad",
            )

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
    ) -> tuple[tuple[Ring, ...], tuple[CourtyardCircle, ...]]:
        """Import exact closed courtyard centerlines and circles in the board frame.

        KiCad treats every closed shape on the matching courtyard layer as part of the footprint
        envelope.  The bounded Board-IR subset accepts unfilled rectangles and polygons plus
        complete ``fp_line`` cycles - with every edge horizontal, vertical, or an exact
        45-degree chamfer - and unfilled ``fp_circle`` outlines whose radius is an exact
        integer nanometre.  Empty still means that no courtyard was present; a malformed or
        unsupported shape is never silently omitted.
        """

        expected_layer = "F.CrtYd" if side is FootprintSide.FRONT else "B.CrtYd"
        result: list[Ring] = []
        circles: list[CourtyardCircle] = []
        line_segments: list[tuple[PointNM, PointNM, str]] = []

        def append(local_points: tuple[PointNM, ...], locator: str) -> None:
            if len(result) + len(circles) >= 64:
                # A fixed schema ceiling, not an operator budget: the Board IR decoder refuses the
                # very same 64-courtyard rule under `schema.limit`, and the two paths disagreeing
                # about the code for one rule was a defect. Every `budget.exceeded.*` code now
                # names a `ParseLimits` field an operator can actually move; this is not one.
                self.fail(
                    "schema.limit",
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
            if not isinstance(item, SExpr) or item.head not in {
                "fp_circle",
                "fp_line",
                "fp_poly",
                "fp_rect",
            }:
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
            elif item.head == "fp_circle":
                if len(result) + len(circles) >= 64:
                    self.fail(
                        # The same fixed 64-courtyard schema ceiling the ring path above
                        # refuses under, and the Board IR decoder enforces. It is not an
                        # operator budget -- no `ParseLimits` field moves it -- so it keeps
                        # `schema.limit` rather than a `budget.exceeded.*` code.
                        "schema.limit",
                        "footprint courtyard limit exceeded",
                        locator,
                        object_kind="footprint",
                    )
                circles.append(self._courtyard_circle(item, locator, origin=origin, turn=turn))
            else:
                line_segments.append(self._courtyard_line_segment(item, locator))

        for index, ring in enumerate(
            self._closed_courtyard_line_rings(line_segments, footprint_locator)
        ):
            append(ring, f"{footprint_locator}.line_chain[{index}]")
        self._require_disjoint_courtyard_circles(tuple(result), tuple(circles), footprint_locator)
        return tuple(result), tuple(circles)

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
            self.fail(ParseBudget.VERTICES_PER_RING.value, "ring vertex budget exceeded", locator)
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
                    self.fail(
                        ParseBudget.VERTICES_PER_RING.value,
                        "ring vertex budget exceeded",
                        footprint_locator,
                    )
            self._require_orthogonal_chain(tuple(points), footprint_locator)
            rings.append(tuple(points))
        return tuple(rings)

    def _require_orthogonal_chain(self, points: tuple[PointNM, ...], locator: str) -> None:
        """Fail closed unless every edge is non-zero and axis-aligned or an exact chamfer.

        The accepted diagonal class is exactly ``|dx| == |dy|``: a 45-degree chamfer keeps
        integer-nanometre vertices and stays in-class under every quarter-turn transform the
        placement model supports.  Real refused boards carried nothing but such chamfers
        (electrolytic-capacitor courtyards); a rectangle rotated by any other angle has
        irrational vertices in the source's own frame and still fails closed here.
        """

        if len(points) < 2:
            self.fail("geometry.invalid", "courtyard chain has too few points", locator)
        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            delta_x = end.x - start.x
            delta_y = end.y - start.y
            if (delta_x == 0 and delta_y == 0) or (
                delta_x != 0 and delta_y != 0 and abs(delta_x) != abs(delta_y)
            ):
                self.fail(
                    "unsupported.topology",
                    "courtyard edges must be non-zero and axis-aligned or 45-degree chamfers",
                    locator,
                    object_kind="footprint",
                )

    def _courtyard_circle(
        self, circle: SExpr, locator: str, *, origin: PointNM, turn: int
    ) -> CourtyardCircle:
        """Read one unfilled ``fp_circle`` courtyard outline as an exact integer circle.

        The radius is the distance from ``center`` to ``end``.  It is imported only when that
        distance is an exact integer nanometre; rounding it in either direction would misstate
        the keep-out on an evidence-publishing surface, so an inexact radius is a typed
        refusal rather than an approximation.  Every circle in the measured refused boards has
        an axis-aligned radius point and therefore an exact radius.
        """

        self._reject_unknown_children(
            circle,
            frozenset({"center", "end", "fill", "layer", "locked", "stroke", "tstamp", "uuid"}),
            locator,
        )
        self._validate_direct_atoms(
            circle, positional_atoms=0, allowed=frozenset({"locked"}), locator=locator
        )
        fill = self._values(circle, "fill", locator, minimum=1, maximum=1, required=False)
        if fill and fill not in {("none",), ("no",)}:
            self.fail(
                "unsupported.construct",
                "filled courtyard circle is unsupported",
                locator,
                object_kind="footprint",
            )
        center = self._point(circle, "center", locator)
        end = self._point(circle, "end", locator)
        delta_x = end.x - center.x
        delta_y = end.y - center.y
        radius_squared = delta_x * delta_x + delta_y * delta_y
        radius = isqrt(radius_squared)
        if radius * radius != radius_squared:
            self.fail(
                "integer.precision",
                "courtyard circle radius is not an exact nanometre",
                locator,
                object_kind="footprint",
            )
        if radius == 0:
            self.fail(
                "geometry.invalid",
                "courtyard circle must have a non-zero radius",
                locator,
                object_kind="footprint",
            )
        try:
            return CourtyardCircle(
                center=self._transform(center, origin, turn, locator), radius_nm=radius
            )
        except ValueError as error:
            self.fail("geometry.invalid", str(error), locator, object_kind="footprint")

    def _require_disjoint_courtyard_circles(
        self,
        rings: tuple[Ring, ...],
        circles: tuple[CourtyardCircle, ...],
        footprint_locator: str,
    ) -> None:
        """Refuse a courtyard circle whose box meets any other courtyard shape's box.

        One footprint's courtyard region is filled even-odd across its contours, which equals
        the plain union only when the contours are disjoint or strictly nested.  A circle
        cannot join the ring nesting hierarchy, so overlap here would silently subtract area
        from the keep-out.  The box test is conservative: it can refuse an exotic legal
        arrangement, never admit an unsound one, and no measured board comes near it.
        """

        if not circles:
            return
        boxes = [
            (
                circle.center.x - circle.radius_nm,
                circle.center.y - circle.radius_nm,
                circle.center.x + circle.radius_nm,
                circle.center.y + circle.radius_nm,
            )
            for circle in circles
        ]
        boxes.extend(
            (
                min(point.x for point in ring.points),
                min(point.y for point in ring.points),
                max(point.x for point in ring.points),
                max(point.y for point in ring.points),
            )
            for ring in rings
        )
        for first in range(len(circles)):
            for second in range(first + 1, len(boxes)):
                a, b = boxes[first], boxes[second]
                if a[2] > b[0] and b[2] > a[0] and a[3] > b[1] and b[3] > a[1]:
                    self.fail(
                        "unsupported.topology",
                        "courtyard circles must be disjoint from other courtyard shapes",
                        footprint_locator,
                        object_kind="footprint",
                    )

    def _net_tie_pad_group(self, footprint: SExpr, locator: str) -> tuple[str, str] | None:
        """Return the single two-pad net-tie group, or None when the footprint declares none.

        KiCad's format defines ``net_tie_pad_groups`` as "a space-separated list of quoted
        strings, each containing a comma-separated list of pad names. Nets attached to pads
        within a single pad-group are allowed to short." The one real construct measured (the
        issue #116 survey's `NetTie-2_THT_Pad1.0mm`) declares exactly one group of exactly two
        pads, written ``"1, 2"`` — with a space after the comma, which KiCad's own reader
        tolerates, so names are stripped of surrounding whitespace here. Every wider shape —
        several groups, a group of one or of three or more pads — is unobserved and refuses
        typed rather than being modelled on a guess. See
        docs/research/kicad-net-tie-modelling-v1.md and ADR-0092.
        """

        declarations = children(footprint, "net_tie_pad_groups")
        if not declarations:
            return None
        if len(declarations) > 1:
            self.fail(
                "syntax.duplicate_field",
                "footprint declares net_tie_pad_groups more than once",
                locator,
                object_kind="footprint",
            )
        try:
            values = atoms(declarations[0])
        except SExprError as error:
            self.fail(error.code, error.message, f"byte:{error.offset}")
        if not values:
            # `(net_tie_pad_groups)` with no group at all. Refusing it as "more than one pad
            # group" was the right refusal under the wrong words, which is the same defect as
            # a message that names nothing: a reader fixes what the message describes.
            self.fail(
                "syntax.invalid",
                "net_tie_pad_groups declares no pad group",
                locator,
                object_kind="footprint",
            )
        if len(values) != 1:
            self.fail(
                "unsupported.construct",
                "net-tie footprints with more than one pad group are unsupported",
                locator,
                object_kind="footprint",
            )
        names = tuple(name.strip() for name in values[0].split(","))
        if len(names) != 2:
            self.fail(
                "unsupported.construct",
                "net-tie pad groups of other than two pads are unsupported",
                locator,
                object_kind="footprint",
            )
        if "" in names or names[0] == names[1]:
            self.fail(
                "syntax.invalid",
                "net-tie pad group is malformed",
                locator,
                object_kind="footprint",
            )
        return (names[0], names[1])

    def _footprints_and_pads(
        self,
    ) -> tuple[tuple[Footprint, ...], tuple[Pad, ...], tuple[Segment, ...]]:
        footprints: list[Footprint] = []
        pads: list[Pad] = []
        tie_segments: list[Segment] = []
        for footprint_index, footprint in enumerate(children(self.root, "footprint")):
            footprint_locator = f"kicad_pcb.footprint[{footprint_index}]"
            tie_group = self._net_tie_pad_group(footprint, footprint_locator)
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
            # Copper layers per pad number, intersected across duplicates, so a net-tie pad
            # group resolves against the layers *every* pad of that number actually occupies.
            pad_layers_by_number: dict[str, frozenset[str]] = {}
            for pad_index, pad in enumerate(children(footprint, "pad")):
                locator = f"{footprint_locator}.pad[{pad_index}]"
                # The named refusals run *before* the closed allowlist below, not after it.
                # Placed after, every one of them was unreachable: the allowlist rejected the
                # same heads first with a message that named no field, so a board carrying an
                # overridden pad clearance was told only that some field was unsupported.
                for unsupported_head in _UNSUPPORTED_PAD_FIELDS:
                    if children(pad, unsupported_head):
                        self.fail(
                            "unsupported.construct",
                            f"pad field {unsupported_head!r} is unsupported",
                            locator,
                            object_kind="pad",
                        )
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
                            "zone_connect",
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
                number, raw_kind, raw_shape = header
                # Kind and shape refuse separately. One message covering both named neither, so
                # a caller reading it could not tell which of the two positional tokens was the
                # problem -- and on the one real board that then reached it, the answer (a
                # `connect` pad) was recoverable only by reading the file. Same defect class as
                # the seven pad refusals D-178 made reachable, in the message rather than in the
                # control flow. That board no longer reaches it; see below.
                #
                # `connect` is KiCad's `PAD_ATTRIB::CONN`, and it converts as `PadKind.SMD`
                # because that is what KiCad's own model says it is, not as a convenience. The
                # claim that matters is universal and was established by sweeping *two* literals,
                # `PAD_ATTRIB::CONN` and `PAD_ATTRIB::SMD` -- the second because a site testing
                # `== SMD` alone contains no `CONN` token and is invisible to a `CONN` grep, which
                # is how the first version of this work missed one. **No site anywhere gives a
                # `CONN` pad different copper, a different layer span, a different hole, or
                # different connectivity from an `SMD` pad.** `connectivity_items.cpp:164-176`
                # puts `SMD`, `CONN` and `NPTH` in one case that pins the item to the front of
                # its copper stack; `pns_kicad_iface.cpp:1631-1648` gives `CONN` and `SMD` one
                # shared case; `pad.cpp:1626-1641` trims both to at most one copper layer;
                # `pad.cpp:2886-2891` and the parser at `…_sexpr_parser.cpp:6433-6437` force the
                # drill to zero for both.
                #
                # At least ten things *do* differ -- a lower bound, not an enumeration -- and none
                # is geometry or connectivity: solder paste (`pad.cpp:3252-3257`), the Gerber
                # aperture attribute (`plot_brditems_plotter.cpp:206-227`), pick-and-place
                # "exclude all TH" (`footprint.cpp:4451-4460` via
                # `place_file_exporter.cpp:145`), the Edge.Cuts clearance DRC exemption
                # (`drc_test_provider_edge_clearance.cpp:431-439`), a distinct property-system
                # value user-authored DRC rules can name (`pad.cpp:3665-3671`), and four
                # reporting surfaces. Board IR models no paste, emits no Gerber and no position
                # file, evaluates no rule expressions, and derives no edge clearance of its own
                # (ADR-0004 delegates DRC to KiCad), so every one is outside what a `Pad` claims
                # -- and the outputs that do change are produced by KiCad from a file in which
                # the `connect` token survives, because both patch adapters are source-preserving
                # splices that never rewrite a pad header.
                #
                # The distinction is therefore *discarded*, not preserved. It is counted rather
                # than dropped in silence -- see `edge_connector_pad_count` below -- but that
                # count is in-process only and reaches no published surface (R-141). ADR-0096 and
                # D-186 record why a new `PadKind` member was rejected, and what that alternative
                # actually costs.
                if raw_kind not in _PAD_KIND_BY_TOKEN:
                    self.fail(
                        "unsupported.construct",
                        "pad kind is unsupported",
                        locator,
                        object_kind="pad",
                    )
                kind = _PAD_KIND_BY_TOKEN[raw_kind]
                try:
                    shape = PadShape(raw_shape)
                except ValueError:
                    self.fail(
                        "unsupported.construct",
                        "pad shape is unsupported",
                        locator,
                        object_kind="pad",
                    )
                # Validated before the aperture skip below, not after it: `zone_connect` is on the
                # pad allowlist now, so a skipped pad would otherwise carry it past every check.
                # The value is inert on an aperture, which has no copper for a pour to reach --
                # but skipping a pad must not become a way to smuggle an unvalidated token in.
                self._require_attaching_pad_zone_connection(pad, locator)
                # A pad with no copper is a stencil aperture, not copper the router may attach to
                # or must avoid. It is skipped only once every condition in `_is_aperture_pad`
                # holds; anything else with no copper layer refuses there. The skip sits after the
                # structural checks above, so a malformed aperture is still a typed refusal.
                if self._is_aperture_pad(pad, number, raw_kind, locator):
                    continue
                # Counted only once the pad is known to be real copper this board actually
                # carries: after the aperture skip, and after every refusal above. The count is a
                # disclosure that a distinction was discarded, so it must not report a pad that
                # was never converted.
                if raw_kind == "connect":
                    self.edge_connector_pad_count += 1
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
                if number:
                    layer_set = frozenset(pad_item.layer_ids)
                    previous = pad_layers_by_number.get(number)
                    pad_layers_by_number[number] = (
                        layer_set if previous is None else previous & layer_set
                    )
            if tie_group is not None:
                missing = [name for name in tie_group if name not in pad_layers_by_number]
                if missing:
                    self.fail(
                        "syntax.invalid",
                        "net-tie pad group references a pad the footprint does not carry",
                        footprint_locator,
                        object_kind="footprint",
                    )
                tie_segments.extend(
                    self._net_tie_copper_segments(
                        footprint,
                        footprint_locator,
                        origin=origin,
                        turn=turn,
                        footprint_locked=footprint_locked,
                        shared_layer_ids=(
                            pad_layers_by_number[tie_group[0]] & pad_layers_by_number[tie_group[1]]
                        ),
                    )
                )
            courtyards, courtyard_circles = self._courtyards(
                footprint,
                footprint_locator=footprint_locator,
                origin=origin,
                turn=turn,
                side=side,
            )
            footprints.append(
                Footprint(
                    id=footprint_id,
                    origin=origin,
                    rotation_udeg=footprint_rotation,
                    side=side,
                    pad_ids=tuple(owned_pad_ids),
                    courtyards=courtyards,
                    courtyard_circles=courtyard_circles,
                    locked=footprint_locked,
                )
            )
        return tuple(footprints), tuple(pads), tuple(tie_segments)

    def _net_tie_copper_segments(
        self,
        footprint: SExpr,
        footprint_locator: str,
        *,
        origin: PointNM,
        turn: int,
        footprint_locked: bool,
        shared_layer_ids: frozenset[str],
    ) -> tuple[Segment, ...]:
        """Convert a net tie's copper polygons into netless obstacle segments.

        The polygon is the deliberate short `net_tie_pad_groups` declares, and it plays two
        roles that the direction-of-error rules resolve separately (ADR-0092):

        - **Obstacle: over-approximate.** The copper is real for every net, including the two
          it ties, so it becomes a full-width `Segment` along the rectangle's long midline.
          Both routers model an orthogonal segment as its endpoint bounding box grown by
          ``(width_nm + 1) // 2`` on all four sides, and that contains the drawn rectangle:
          the ceil-rounded half width absorbs an odd short side, the floor-rounded midline
          costs at most one nanometre of slack on one edge, and the square caps extend past
          the short edges. So the model can only refuse a route, never permit one through
          the tie.
        - **Connectivity: no claim.** ``net_id`` is ``None``, the same contract net-0 copper
          has (ADR-0078): the tied nets are deliberately *under*-approximated as unconnected
          through the tie, because a joined-nets claim could not be test-bound without
          verifying the polygon actually bridges both pad groups. The identity is
          revision-derived on purpose — an `fp_poly` is not a KiCad track, so its UUID names
          no `Segment` — and every source-preserving patch path already refuses a snapshot
          containing a derived identity, which is what keeps the short unbreakable by
          write-back (ADR-0026).

        Each polygon must be filled, unstroked, on one declared copper layer that every pad
        of the tied group occupies, and an axis-aligned rectangle after the footprint
        transform. Only the *corner set* is checked: the four distinct corners of a rectangle
        admit reorderings whose even-odd fill is a subset of the rectangle, and a subset is
        covered by the same over-approximating obstacle.
        """

        segments: list[Segment] = []
        copper_index = 0
        for item in footprint.items[1:]:
            if not isinstance(item, SExpr) or item.head != "fp_poly":
                continue
            probe_locator = f"{footprint_locator}.net_tie_copper[{copper_index}]"
            layer_name = self._graphic_layer(item, probe_locator)
            if not self._is_routing_layer(layer_name) or layer_name == "Edge.Cuts":
                continue
            locator = probe_locator
            copper_index += 1
            layer = self.layer_by_name.get(layer_name)
            if layer is None:
                self.fail(
                    "unknown.layer",
                    "net-tie copper must name one declared copper layer",
                    locator,
                    object_kind="footprint",
                )
            if layer.id not in shared_layer_ids:
                self.fail(
                    "unsupported.construct",
                    "net-tie copper lies on a copper layer its tied pads do not occupy",
                    locator,
                    object_kind="footprint",
                )
            self._reject_unknown_children(
                item,
                frozenset({"fill", "layer", "locked", "pts", "stroke", "tstamp", "uuid"}),
                locator,
            )
            self._validate_direct_atoms(
                item, positional_atoms=0, allowed=frozenset({"locked"}), locator=locator
            )
            fill = self._values(item, "fill", locator, minimum=1, maximum=1)
            if fill != ("yes",):
                self.fail(
                    "unsupported.construct",
                    "net-tie copper polygon must be filled",
                    locator,
                    object_kind="footprint",
                )
            # `stroke` is **required**, not optional. An omitted field is not an explicitly
            # zero one: the whole width argument for modelling this polygon as its long
            # midline is that the drawn copper is the rectangle and nothing more, and the only
            # thing establishing that is `(width 0)`. Reading a missing `stroke` as zero would
            # let any non-zero default -- KiCad's, a future writer's, or a hand-edited file's --
            # widen the real copper past the modelled segment, which **understates** the
            # obstacle. That is the one direction the invariant forbids.
            #
            # It costs nothing measurable: across the survey corpus, all 331 `fp_poly`
            # expressions in 20 board files carry an explicit stroke, the net-tie polygons
            # among them written by KiCad 10 as `(stroke (width 0) (type solid))`. So the
            # omitted form is unobserved, and refusing it is D-178's rule applied -- accept
            # only what is provably free of copper, refuse the rest, and pin the refusal.
            stroke = self._one(item, "stroke", locator, required=False)
            if stroke is None:
                self.fail(
                    "unsupported.construct",
                    "net-tie copper polygon must declare its outline stroke",
                    locator,
                    object_kind="footprint",
                )
            self._reject_unknown_children(stroke, frozenset({"type", "width"}), f"{locator}.stroke")
            stroke_width = self._values(stroke, "width", f"{locator}.stroke", minimum=1, maximum=1)
            if self._mm(stroke_width[0], f"{locator}.stroke.width") != 0:
                self.fail(
                    "unsupported.construct",
                    "net-tie copper polygon with a stroked outline is unsupported",
                    locator,
                    object_kind="footprint",
                )
            points_expression = self._one(item, "pts", locator)
            assert points_expression is not None
            self._reject_unknown_children(points_expression, frozenset({"xy"}), f"{locator}.pts")
            self._validate_direct_atoms(
                points_expression,
                positional_atoms=0,
                allowed=frozenset(),
                locator=f"{locator}.pts",
            )
            point_expressions = children(points_expression, "xy")
            if len(point_expressions) > 5:
                self.fail(
                    "unsupported.construct",
                    "net-tie copper polygon must be an axis-aligned rectangle",
                    locator,
                    object_kind="footprint",
                )
            local_points: list[PointNM] = []
            for index, point in enumerate(point_expressions):
                values = atoms(point)
                if len(values) != 2:
                    self.fail(
                        "syntax.invalid",
                        "net-tie copper polygon point is malformed",
                        f"{locator}.point[{index}]",
                    )
                local_points.append(
                    PointNM(
                        self._mm(values[0], f"{locator}.point[{index}].x"),
                        self._mm(values[1], f"{locator}.point[{index}].y"),
                    )
                )
            if len(local_points) == 5 and local_points[0] == local_points[-1]:
                local_points.pop()
            board_points = tuple(
                self._transform(point, origin, turn, locator) for point in local_points
            )
            corner_xs = {point.x for point in board_points}
            corner_ys = {point.y for point in board_points}
            if (
                len(local_points) != 4
                or len(set(board_points)) != 4
                or len(corner_xs) != 2
                or len(corner_ys) != 2
            ):
                self.fail(
                    "unsupported.construct",
                    "net-tie copper polygon must be an axis-aligned rectangle",
                    locator,
                    object_kind="footprint",
                )
            x_min, x_max = min(corner_xs), max(corner_xs)
            y_min, y_max = min(corner_ys), max(corner_ys)
            if x_max - x_min >= y_max - y_min:
                width_nm = y_max - y_min
                midline = (y_min + y_max) // 2
                start = PointNM(x_min, midline)
                end = PointNM(x_max, midline)
            else:
                width_nm = x_max - x_min
                midline = (x_min + x_max) // 2
                start = PointNM(midline, y_min)
                end = PointNM(midline, y_max)
            segments.append(
                Segment(
                    id=self._derived_identity("segment", locator),
                    net_id=None,
                    layer_id=layer.id,
                    start=start,
                    end=end,
                    width_nm=width_nm,
                    locked=footprint_locked or self._locked(item),
                )
            )
        if not segments:
            self.fail(
                "unsupported.construct",
                "net-tie footprint carries no supported tie copper",
                footprint_locator,
                object_kind="footprint",
            )
        return tuple(segments)

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
            # KiCad stores stitching and orphaned copper on net 0 ("no net"). That copper is
            # physically present whatever its net, so it converts as an obstacle with no
            # connectivity contribution (net_id None) instead of refusing the document.
            net_name = self._net_name(expression, locator)
            layer_ids = self._layer_ids(expression, locator)
            if len(layer_ids) != 1:
                self.fail("unknown.layer", "segment must reference one copper layer", locator)
            result.append(
                Segment(
                    id=self._identity("segment", expression, locator),
                    net_id=net_id_for_name(net_name) if net_name is not None else None,
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
            # Net-0 arcs convert as netless obstacles for the same reason segments do.
            net_name = self._net_name(expression, locator)
            layer_ids = self._layer_ids(expression, locator)
            if len(layer_ids) != 1:
                self.fail("unknown.layer", "track arc must reference one copper layer", locator)
            result.append(
                Arc(
                    id=self._identity("arc", expression, locator),
                    net_id=net_id_for_name(net_name) if net_name is not None else None,
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
            # A stitching via saved on KiCad's net 0 is real copper: barrel and annulus occupy
            # space on every layer they cross. It converts as an obstacle with no connectivity
            # contribution (net_id None); every geometric check below still applies to it.
            net_name = self._net_name(expression, locator)
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
                    net_id=net_id_for_name(net_name) if net_name is not None else None,
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
            self.fail(ParseBudget.VERTICES_PER_RING.value, "ring vertex budget exceeded", locator)
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

    def _edge_cuts_line_segments(self) -> list[tuple[PointNM, PointNM, str, SExpr]]:
        """Read every root ``gr_line`` drawn on ``Edge.Cuts`` as an exact integer segment."""

        segments: list[tuple[PointNM, PointNM, str, SExpr]] = []
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
            segments.append((start, end, locator, expression))
        return segments

    def _assembled_contour_identity(self, members: list[SExpr], locator: str) -> str:
        """Name a contour assembled from many segments by its members' own native identities.

        A contour chained from ``gr_line`` segments has no single native KiCad identity, but its
        members each carry one, and those uuids are the file's own durable names for exactly the
        source expressions the contour was assembled from.  Hashing the *sorted set* of member
        identities therefore names the member set — not any one segment, which is the mistake
        ADR-0076 refused — and the result survives every edit that leaves the member set alone,
        including the pose splices and segment appends the apply paths perform.  Resolution runs
        the derivation backwards: collect the root ``Edge.Cuts`` ``gr_line`` identities from the
        source file and recompute the hash.  See ADR-0087.

        The fallback is load-bearing: any member without exactly one usable native identity, or a
        value repeated inside the member set, degrades the whole contour to the revision-derived
        name that every source-preserving patch path refuses (ADR-0026).  Degrading — never
        guessing a member's name — is what keeps the apply gates' invariant intact: an identity
        that cannot be resolved back to specific source objects never stops looking derived.
        """

        values: list[str] = []
        for expression in members:
            identities: list[str] = []
            for head in ("uuid", "tstamp"):
                fields = children(expression, head)
                if len(fields) != 1:
                    continue
                atoms_found = atoms(fields[0])
                if len(atoms_found) != 1:
                    continue
                identities.append(atoms_found[0].lower())
            if len(identities) != 1:
                return self._derived_identity("contour", locator)
            values.append(identities[0])
        if len(values) != len(set(values)):
            return self._derived_identity("contour", locator)
        material = "\0".join(["contour", "assembled", *sorted(values)]).encode()
        return f"contour:assembled:{hashlib.sha256(material).hexdigest()[:32]}"

    def _edge_cuts_segment_ring(self, segments: list[tuple[PointNM, PointNM, str, SExpr]]) -> Ring:
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
                # The outline ring is bounded by the same per-ring vertex budget every other
                # ring uses, so it refuses under that budget's own code rather than a bare one.
                # This path landed after the discriminated codes were written, which is exactly
                # what the no-bare-code invariant test exists to catch.
                ParseBudget.VERTICES_PER_RING.value,
                "Edge.Cuts outline segment budget exceeded",
                "kicad_pcb",
                object_kind="outline",
            )
        adjacency: dict[PointNM, list[tuple[int, PointNM]]] = {}
        seen: set[tuple[PointNM, PointNM]] = set()
        for index, (start, end, locator, _expression) in enumerate(segments):
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
                    id=self._assembled_contour_identity(
                        [expression for _start, _end, _locator, expression in segments],
                        "kicad_pcb.edge_cuts",
                    ),
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
        footprints, pads, tie_segments = self._footprints_and_pads()
        source_info = SourceInfo(
            format="kicad_pcb",
            revision=self.source_revision,
            format_version=self.version,
            generator=generator_values[0] if generator_values else None,
        )
        outline = self._outline()
        vias = self._vias()
        # Net-tie copper joins the segment collection here rather than in `_segments`, which
        # reads only root `segment` expressions; canonicalization orders by ID, so placement
        # in this tuple carries no meaning.
        segments = self._segments() + tie_segments
        arcs = self._arcs()
        # Every identity is now assigned, so reuse is measurable rather than guessed.  Re-running
        # the whole conversion with the measured set is what keeps the fallback symmetric: all the
        # objects sharing a reused value degrade together, and none of them keeps a claim to it.
        reused = (
            frozenset(key for key, uses in self.native_identity_uses.items() if uses > 1)
            - self.ambiguous_native_identities
        )
        if reused:
            return _Converter(
                self.payload,
                self.root,
                self.profile,
                self.limits,
                ambiguous_native_identities=self.ambiguous_native_identities | reused,
            ).convert()
        content = make_content(
            source=source_info,
            outline=outline,
            copper_layers=self.layers,
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
        return ConversionResult(
            snapshot=make_snapshot(content),
            max_roundrect_rounding_nm=self.max_roundrect_rounding_nm,
            unmodelled_group_count=self.root_group_count,
            edge_connector_pad_count=self.edge_connector_pad_count,
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
        # Name the invariant, never the board.  Every message raised by Board IR validation is a
        # fixed string chosen by `copper_mcp.board_ir`; anything derived from the source travels
        # in the error's locator, which this refusal deliberately does not echo.
        return ConversionResult(
            snapshot=None,
            diagnostics=(
                Diagnostic(
                    code=error.code,
                    severity=Severity.ERROR,
                    message=(
                        "converted Board IR content failed semantic validation: "
                        f"{error.message[:256]}"
                    ),
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
