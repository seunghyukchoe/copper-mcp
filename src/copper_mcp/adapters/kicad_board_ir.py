"""Fail-closed, read-only KiCad S-expression to Board IR v0.2 adapter."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, replace
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
from copper_mcp.board_ir.outline_arc import (
    OutlineArcError,
    arc_is_minor,
    chord_side,
    inscribe_outline_arc,
)
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
    PadCopperEnvelope,
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
    signed_double_area,
)
from copper_mcp.board_ir.validation import (
    BoardIRValidationError,
    validate_content,
    validate_ring_topology,
)

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
# it is exactly the direction that must fail closed.  See `_check_group`.
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
# The board file format's "Properties" root section -- one of the root sections the format
# enumerates, documented since KiCad 6.0 as "a key value pair for storing user defined
# information" with a string key and a string value and nothing else.  It is one entry of
# `BOARD::m_properties`, a `std::map<wxString, wxString>`, written by
# `PCB_IO_KICAD_SEXPR::formatProperties` as `(property %s %s)` through `Quotew`, which quotes
# unconditionally.  See docs/research/kicad-root-board-properties-v1.md and ADR-0094.
#
# The accept is NOT "a property is cosmetic", and that claim would be false.  KiCad's only reader
# of the map is `BOARD::ResolveTextVar`, which substitutes `${KEY}` -- and substitution has real
# termini.  Six were found, and the research note says plainly that six is what a sweep of the
# `ResolveTextVar`/`ExpandTextVars` call sites found rather than a proof of completeness:
# `PCB_TEXT`, `PCB_TEXTBOX` and `PCB_TABLECELL` all resolve `GetShownText` through it, so text on
# a copper layer is plotted copper whose glyphs depend on a property value; `PCB_BARCODE`
# assembles its module pattern from the shown text; `DRC_ENGINE::loadRules` expands the same
# tokens over a `.kicad_dru` file, so a property can supply a clearance to a custom rule; and the
# IPC `ExpandTextVariables` endpoint hands an expanded value straight to a client.  The accept is
# sound because **this adapter already refuses or already excludes every one of those termini, for
# its own reasons and independently of any property**: a root graphic on a copper layer refuses, a
# footprint graphic on a copper layer refuses, `barcode` and `table` are not in the root
# vocabulary, and `.kicad_dru` is a separate file this adapter has never parsed (ADR-0005) -- while
# the
# authoritative DRC surface hands the real `.kicad_dru` and `.kicad_pro` to KiCad itself over
# source bytes that the write-back path preserves verbatim.  So no substitution can reach Board IR
# content, no obstacle shrinks and no connectivity or outline grows.  Board IR carries no text at
# all: a `Footprint` has an id, an origin, a rotation, a side, pads and courtyards, and no string
# from the document but a layer name.
#
# Every key is accepted, and that is a decision rather than an omission.  The question ADR-0090 had
# to answer for a group -- is there a value that changes what the board *is* rather than what it is
# *called*? -- is asked here over the key, and the answer is that no key is special-cased anywhere
# in KiCad's board-properties code: the format's only reserved property keys (`ki_keywords`,
# `ki_description`, `ki_locked`, `ki_fp_filters`) are *symbol* properties, and a key colliding with
# a built-in text-variable token is shadowed by the resolver rather than given any new power.
# Refusing a *specific* key would therefore be a rule with no domain behind it.
_ROOT_PROPERTY_HEAD = "property"
# Pad kinds the KiCad board format defines (`PAD_ATTR_T`: PTH, SMD, CONN, NPTH) and Board IR
# v0.2 does not model, under exactly the rule `_UNMODELLED_ROOT_HEADS` documents above: the
# message is a *value from this table*, selected by an equality test against the source token
# and never built from it, so the refusal names the construct without echoing one byte of the
# board.  A token absent from this table is not a documented pad kind at all and refuses
# unnamed, with the indexed locator still saying which pad.
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
# segments and circular arcs that chain into exactly one closed simple loop.  A rectangle and a
# segment carry the outline as an exact integer polygon whose vertices are drawn points.  An arc
# does not, and cannot: its circle has a rational centre and an irrational radius, so no polygon
# is equal to it.  It is admitted as an *inscribed* polyline instead — every vertex an exact
# integer point inside the region the chord and the arc bound — which is the only direction of
# error the outline permits, and the direction is decided per arc against the ring's own interior.
# See ADR-0124, docs/research/edge-cuts-outline-arcs-v1.md, ADR-0076 and ADR-0080.
_EDGE_CUTS_OUTLINE_HEADS = frozenset({"gr_arc", "gr_line", "gr_rect"})
# Outline primitives that stay refused, each with its own sentence, because "unsupported curve"
# told an operator which *category* was refused and never which construct or why.  Every message
# is a constant selected by an equality test against the source token, so a refusal names the
# construct without echoing one byte of the board.
#
# None of these four appears on any board in the measured cohort (B-134: all 97 ``Edge.Cuts``
# curve primitives across ten public boards are ``gr_arc``), so each is refused on its own
# unresolved argument rather than on cost:
#
#   * ``gr_circle`` is a *closed* shape.  It is not an edge that chains with anything, it is a
#     whole contour, so admitting one is a topology decision — how it composes with the other
#     shapes, and whether a second closed shape is a hole or a second board — and not an
#     approximation decision.  ADR-0076 left multiple contours refused and this does not reopen it.
#   * ``gr_curve`` and ``gr_bezier`` are cubic Béziers, whose convexity is **not global**: a single
#     curve may have an inflection, so one arc's one-line convex/concave verdict does not exist for
#     it.  Each monotone span would need its own direction, which is a different construction.
#   * ``gr_poly`` already *is* a polygon, so it needs no approximation at all — but it is a closed
#     shape like ``gr_circle``, and it carries a ``fill`` whose meaning for an outline this project
#     has never decided.  It is refused as topology, not as geometry.
_EDGE_CUTS_REFUSED_OUTLINE_HEADS: dict[str, str] = {
    "gr_bezier": "Edge.Cuts outline Bezier curves are unsupported",
    "gr_circle": "Edge.Cuts outline circles are unsupported",
    "gr_curve": "Edge.Cuts outline Bezier curves are unsupported",
    "gr_poly": "Edge.Cuts outline polygons are unsupported",
}
_EDGE_CUTS_LINE_FIELDS = frozenset({"end", "layer", "locked", "start", "stroke", "tstamp", "uuid"})
# An arc is a segment plus the one point that says which way it bends.  The closed set is the
# segment's own, plus ``mid``, and it is exactly what the cohort writes: ``start``, ``mid``,
# ``end``, ``stroke``, ``layer`` and ``uuid`` on all 97 arcs, with no positional atom on any of
# them (B-134).  ``width`` is absent deliberately — KiCad 10 writes the stroke and not the legacy
# scalar, and the outline ignores stroke width anyway because KiCad builds its own outline from
# shape centrelines.
_EDGE_CUTS_ARC_FIELDS = _EDGE_CUTS_LINE_FIELDS | {"mid"}
# Root graphics on a copper layer, mapped to the refusal each earns.  Every one of them is real
# copper and therefore an obstacle, and an obstacle may only be over-approximated -- so all of
# them refuse.  What this table changes is only *what the refusal says*, under exactly the rule
# `_UNMODELLED_ROOT_HEADS` documents above: the sentence is a value from this table, selected by
# an equality test against the source token and never built from it, so the refusal can name the
# construct without echoing one byte of the board.  A head absent from the table falls back to
# `_UNNAMED_COPPER_GRAPHIC` and is refused without being named -- still located, still refused,
# just not named, exactly as an undocumented root head is.
#
# `gr_text` and `gr_text_box` are separated out because they refuse for a *different and stronger*
# reason than a stray drawn line does, and an operator who reads "root graphic on copper is
# unsupported" cannot tell which they are looking at.  A drawn primitive carries its own geometry
# in the document and could in principle be enveloped the way ADR-0013 envelopes a zone outline.
# Copper lettering cannot: the glyph run KiCad plots is not a function of the board document's
# bytes.  Four independent mechanisms establish that, each measured against KiCad 10.0.5's own
# plotter in docs/research/kicad-copper-text-envelope-v1.md:
#
#   * `${...}` in the string is expanded by `PCB_TEXT::GetShownText` through
#     `BOARD::ResolveTextVar`, which reads the *project* file's `text_variables` -- a second
#     document CopperMCP neither reads nor digests -- and `${FILENAME}`, `${PROJECTNAME}` and
#     `${CURRENT_DATE}` resolve from the path and the clock, which are in no document at all.
#   * `(face "...")` selects a TrueType outline font resolved through the host's font cache, and
#     fontconfig silently substitutes when it is missing ("Font '%s' not found; substituting
#     '%s'."), so the plotted copper is a function of the rendering machine.
#   * Even for the built-in stroke font with a literal ASCII string, the glyph extents live in
#     KiCad's compiled-in `newstroke_font` table, not in the board, and KiCad's own text box is
#     *not* a containing box: at 1.27 mm, `(g)pqy` plots 0.3995 mm past its bottom edge and an
#     overbar run 0.5957 mm past its top.
#   * `gr_text_box` is in this table for a reason that had to be measured rather than assumed by
#     analogy with `gr_text`, because it is the one text head that *does* carry two exact corners
#     in the document -- which would have been a derivable envelope.  It is not one: the corners
#     bound neither axis.  A 40x2 mm declared box at 1.27 mm is overflowed 0.1425 mm below by a
#     descender, 3.7479 mm below and 3.8594 mm above by thirty wrapping words, and 11.1037 mm to
#     each side by one unbreakable 52-character word that cannot wrap at all -- both overflows
#     growing linearly and without bound in a string length the first bullet already shows is not
#     derivable.
#
# So there is no box derivable from `at`, `start`/`end`, `size`, `thickness` and the string that
# is provably containing, and an obstacle that is not provably containing is an
# under-approximation waiting to happen.  See ADR-0095 for the decision and for what would have
# to become true to accept it.
_UNNAMED_COPPER_GRAPHIC: tuple[str, str] = ("root graphic on copper is unsupported", "graphic")
_COPPER_TEXT_REFUSAL: tuple[str, str] = (
    "copper text has no envelope derivable from the board and is unsupported",
    "text",
)
_UNMODELLED_COPPER_GRAPHIC_HEADS: dict[str, tuple[str, str]] = {
    "gr_text": _COPPER_TEXT_REFUSAL,
    "gr_text_box": _COPPER_TEXT_REFUSAL,
}
# The same table one structural level down, for a *footprint* graphic on a copper layer that
# `_footprint_copper_obstacle_segments` does not bound.  Separate sentences from the root table on
# purpose: a reader of a diagnostic must be able to tell a footprint's copper from the board's, and
# a shared sentence would also silently re-bucket the frozen B-129 masking instrument, whose
# vocabulary matches on message *and* locator.
#
# Every entry names the primitive kind and says which of three different unmet conditions it is
# waiting on, because they ask for different fixes and one sentence for all of them is the defect
# ADR-0123 names:
#
# - `fp_line` and `fp_arc` are stroked open primitives, and Board IR *already has their exact
#   model*: a stroked line is geometrically a `Segment` and a stroked minor arc is an `Arc`, with
#   ADR-0072's envelope covering the second.  What is missing is not geometry, it is an observation
#   -- B-136 finds 32,532 `fp_line` and 23 `fp_arc` on these boards and **not one on copper**.
# - `fp_rect` and `fp_circle` are closed primitives whose filled forms are a rectangle and a disc.
#   The rectangle has the net-tie polygon's exact midline model (ADR-0092); the disc has no Board
#   IR obstacle type at all, since a `Segment` needs two distinct endpoints and a `CourtyardCircle`
#   is a placement keep-out on an evidence surface that must not borrow an obstacle's direction of
#   error (ADR-0075, ADR-0080).
# - `fp_curve` is a cubic Bezier.  Its convex hull *is* derivable -- a Bezier lies inside the hull
#   of its four control points -- so unlike copper text this one is not undecidable, and the
#   sentence says "unmodelled" rather than "underivable" to keep those two apart.
# - `fp_text`, `fp_text_box` and a footprint `property` are text, and text is ADR-0095: there is no
#   envelope derivable from the document, five exit conditions are recorded there, and none is met.
#   This is the one entry whose refusal is a *conclusion* rather than a backlog item.
# - `point` carries `at`/`size`/`layer` and is routed here by layer rather than by head, so it is
#   named for the same reason the others are.
_FOOTPRINT_COPPER_TEXT_REFUSAL: tuple[str, str] = (
    "footprint copper text has no envelope derivable from the board and is unsupported",
    "text",
)
_UNNAMED_FOOTPRINT_COPPER_GRAPHIC: tuple[str, str] = (
    "footprint graphic on a copper layer is unmodelled copper",
    "graphic",
)
_UNMODELLED_FOOTPRINT_COPPER_HEADS: dict[str, tuple[str, str]] = {
    "fp_arc": ("footprint copper arc is unmodelled copper", "graphic"),
    "fp_circle": ("footprint copper circle is unmodelled copper", "graphic"),
    "fp_curve": ("footprint copper curve is unmodelled copper", "graphic"),
    "fp_line": ("footprint copper line is unmodelled copper", "graphic"),
    "fp_rect": ("footprint copper rectangle is unmodelled copper", "graphic"),
    "fp_text": _FOOTPRINT_COPPER_TEXT_REFUSAL,
    "fp_text_box": _FOOTPRINT_COPPER_TEXT_REFUSAL,
    "point": ("footprint copper point is unmodelled copper", "graphic"),
    "property": _FOOTPRINT_COPPER_TEXT_REFUSAL,
}
# The closed child grammar of an `fp_poly`, from `PCB_IO_KICAD_SEXPR_PARSER::parseFOOTPRINT`'s
# `T_fp_poly` arm on the KiCad 9.0 and 10.0 release branches.  `tstamp` is the pre-KiCad-8 spelling
# of `uuid` and still parses.
_FOOTPRINT_POLYGON_CHILDREN = frozenset(
    {"fill", "layer", "locked", "pts", "stroke", "tstamp", "uuid"}
)
_SETUP_METADATA_HEADS = frozenset(
    {
        "allow_soldermask_bridges_in_footprints",
        # The drill-and-place file origin. It is a *reporting* origin: KiCad subtracts it when
        # writing drill and component-placement files, and nothing on the board moves. Board IR
        # carries absolute board coordinates and makes no fabrication-output claim, so the value
        # changes no pad, track, via, zone or outline this adapter reads. Counted, not modelled --
        # a caller regenerating fab output from a snapshot alone would emit a different origin.
        # See D-227 and the setup-field research note.
        "aux_axis_origin",
        "capping",
        "covering",
        "filling",
        # The editor's grid anchor. Pure editor state: it moves where KiCad's grid dots land and
        # what a coordinate readout displays, and no board object's stored position depends on it.
        # Counted for symmetry with `aux_axis_origin` rather than because anything can read it.
        "grid_origin",
        "pad_to_mask_clearance",
        # Solder-paste stencil aperture defaults, the paste twins of `pad_to_mask_clearance` and
        # `solder_mask_min_width` below. They shrink or grow the F.Paste/B.Paste aperture derived
        # from a pad; the pad's own copper, hole, layer span and clearance are untouched. Board IR
        # models copper and makes no paste claim, so accepting them ignores nothing it would have
        # honoured -- exactly the argument the mask pair already stands on. KiCad's own proof is
        # `FOOTPRINT::TransformPadsToPolySet`, which adds the paste margin only under
        # `case F_Paste: case B_Paste:` and falls through `default:` -- every copper layer -- with
        # no adjustment at all.
        "pad_to_paste_clearance",
        "pad_to_paste_clearance_ratio",
        "pcbplotparams",
        "plugging",
        # Soldermask sliver minimum. Like `pad_to_mask_clearance` above it constrains mask
        # generation, not copper: it bounds how thin a mask web may get between apertures.
        # CopperMCP models copper geometry and makes no soldermask claim, so accepting it as
        # metadata ignores nothing it would otherwise have honoured. Found on real boards that
        # were refused outright for carrying it.
        "solder_mask_min_width",
        # The physical stack. It is accepted as *metadata with a closed inner grammar*, never as a
        # whole: see `_validate_stackup`. The stack describes the board in Z -- layer order,
        # thickness, material, dielectric constant, loss tangent, surface finish -- and Board IR is
        # an XY copper model whose layer set comes from the root `(layers ...)` section, not from
        # here. What the stackup can carry that XY copper *does* care about is three fabrication
        # attributes naming plated material at the board edge, and those are refused one by one
        # rather than read past.
        "stackup",
        "tenting",
    }
)
# Deliberately **absent**, and named so the absence reads as a decision rather than an oversight.
# `zone_defaults` is new in KiCad 10 and carries the default hatch *phase* of a hatched zone fill
# -- copper geometry for anything that re-fills, so it stays refused. It is also the head that
# tests whether B-130's closed vocabulary was closed for the right reason: the census buckets any
# unlisted head as `other`, and `other` came back 0 on all six boards, so no board of that cohort
# writes one. A seventh board that did would refuse here and be reported by that bucket, which is
# what the bucket is for.
_REFUSED_SETUP_HEADS_ON_RECORD = frozenset({"zone_defaults"})
# Stackup children KiCad writes, and the only ones this adapter reads past.  `layer`,
# `copper_finish` and `dielectric_constraints` describe the board in Z and are counted; the three
# in `_COPPER_BEARING_STACKUP_HEADS` below are refused unless explicitly neutral.
_STACKUP_HEADS = frozenset(
    {
        "castellated_pads",
        "copper_finish",
        "dielectric_constraints",
        "edge_connector",
        "edge_plating",
        "layer",
    }
)
# The three stackup attributes that assert something about conductive or removed material at the
# board edge rather than about the stack's Z geometry.  Each names plated or bevelled material that
# no pad, track, via, zone or graphic in the document represents, so reading past one would report
# a board edge this adapter has understated.  A neutral `no` asserts the absence and is accepted;
# anything else refuses, at the field rather than at the block.
#
# **This is deliberate over-refusal and the counter-evidence is recorded rather than omitted.**
# KiCad derives *no geometry at all* from these three: `m_EdgePlating` and
# `m_EdgeConnectorConstraints` are read only by `gerber_jobfile_writer.cpp`, the stackup text
# report, the board-characteristics table and the GUI panel, with no reference anywhere in
# `pcbnew/drc/`, the plotters, `zone_filler.cpp`, the 3D viewer or the STEP exporter; the 9.0 and
# 10.0 manuals both say these settings "only impact the board attributes output as part of Gerber
# job files at this time".  Refusing them is therefore a claim about the *physical* board, not
# about KiCad's model -- and it is exactly the claim `_validate_neutral_via_treatment` already
# makes for `capping` and `filling`, which are likewise conductive fabrication treatment KiCad
# derives no geometry from.  Consistency with that precedent decides it, and B-130 measures the
# price: all three occur 0 times on 0 boards of the six-board public setup-terminal cohort.
#
# `castellated_pads` is additionally moot at the one board format version this adapter accepts:
# KiCad 10 removed `m_CastellatedPads` (commit `09e1fca7e4`, 2025-08-11), never emits the token,
# and its parser consumes it as legacy compatibility.  Castellation is now derived from the pad
# `pad_prop_castellated` property -- which this adapter already refuses -- so the stackup flag is
# kept here to fail closed on a hand-edited or third-party-written document, not because KiCad 10
# can produce one.
_COPPER_BEARING_STACKUP_HEADS = ("castellated_pads", "edge_connector", "edge_plating")
# The `setup` children D-227 newly admits and does not model, and the exact set
# `unmodelled_setup_field_count` reports.  Deliberately *not* every accepted setup head: the count
# discloses the erasure this decision creates, not the ones already recorded elsewhere.
_UNMODELLED_SETUP_HEADS = frozenset(
    {
        "aux_axis_origin",
        "grid_origin",
        "pad_to_paste_clearance",
        "pad_to_paste_clearance_ratio",
        "stackup",
    }
)
# Stackup layer children KiCad writes.  Every one is a Z-axis or appearance property of one entry
# in the physical stack: none carries an XY coordinate, a net, or a clearance.
_STACKUP_LAYER_HEADS = frozenset(
    {
        "color",
        "epsilon_r",
        "loss_tangent",
        "material",
        "thickness",
        "type",
    }
)
# A decimal token KiCad can write for a dimension, a ratio or a material constant.  Deliberately
# looser than `board_ir.types._DECIMAL`, which additionally enforces the nanometre precision this
# adapter needs from a coordinate it is about to *convert*.  Nothing here is converted -- these
# values are validated and discarded -- so the check must not refuse a well-formed board over a
# precision this decision never claims.  Leading `+`, a bare fraction and an exponent are all
# accepted for that reason; a quoted atom is not a number and is rejected by the caller.
_PAYLOAD_DECIMAL = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")
_PAYLOAD_FLAGS = frozenset({"no", "yes"})
# The closed payload grammar of every accepted non-container `setup` field, and of every leaf
# inside an accepted `stackup`.  `(head, locator suffix) -> (minimum atoms, maximum atoms, kind,
# trailing flag)`.
#
# **A counted non-claim is still a validated construct.** Accepting a field means "well formed and
# deliberately not modelled", never "bytes nobody read". Without these grammars an accepted
# container was an open door: `(grid_origin (zone_defaults ...))` smuggled past the very head
# `_REFUSED_SETUP_HEADS_ON_RECORD` exists to refuse, because the head allowlist above only
# constrains *which* children may appear and says nothing about what nests inside one.
_NUMBER, _TEXT, _FLAG = "number", "text", "flag"
_SETUP_SCALAR_PAYLOADS: dict[str, tuple[int, int, str, str | None]] = {
    # KiCad stores both origins as a `VECTOR2I`, so exactly two coordinates and never a third.
    "aux_axis_origin": (2, 2, _NUMBER, None),
    "grid_origin": (2, 2, _NUMBER, None),
    "pad_to_paste_clearance": (1, 1, _NUMBER, None),
    "pad_to_paste_clearance_ratio": (1, 1, _NUMBER, None),
}
_STACKUP_SCALAR_PAYLOADS: dict[str, tuple[int, int, str, str | None]] = {
    # One quoted name from KiCad's predefined finish list; the value is discarded, so the check is
    # arity and shape rather than membership.
    "copper_finish": (1, 1, _TEXT, None),
    # Written through `FormatBool`, so exactly one bare `yes` or `no`.
    "dielectric_constraints": (1, 1, _FLAG, None),
}
_FOOTPRINT_SCALAR_PAYLOADS: dict[str, tuple[int, int, str, str | None]] = {
    # `parseBoardUnits` then `NeedRIGHT`: exactly one bare decimal in millimetres, no trailing
    # token.  The ratio is a fraction rather than a percent, and both spellings share KiCad's arm.
    "sheetfile": (1, 1, _TEXT, None),
    "sheetname": (1, 1, _TEXT, None),
    "solder_mask_margin": (1, 1, _NUMBER, None),
    "solder_paste_margin": (1, 1, _NUMBER, None),
    "solder_paste_margin_ratio": (1, 1, _NUMBER, None),
    "solder_paste_ratio": (1, 1, _NUMBER, None),
}
_STACKUP_LAYER_PAYLOADS: dict[str, tuple[int, int, str, str | None]] = {
    "color": (1, 1, _TEXT, None),
    "epsilon_r": (1, 1, _NUMBER, None),
    "loss_tangent": (1, 1, _NUMBER, None),
    "material": (1, 1, _TEXT, None),
    # One dimension, plus the optional trailing `locked` KiCad writes on a dielectric whose
    # thickness the stackup editor may not redistribute.
    "thickness": (1, 2, _NUMBER, "locked"),
    "type": (1, 1, _TEXT, None),
}
_FOOTPRINT_METADATA_HEADS = frozenset(
    {
        "at",
        "attr",
        # A footprint-local group, parsed by KiCad's *same* `parseGROUP` as a root one
        # (`pcb_io_kicad_sexpr_parser.cpp:6283` dispatches to `:7704`) and written from
        # `aFootprint->Groups()` (`pcb_io_kicad_sexpr.cpp:1611`).  It goes through the identical
        # validator and the identical counter as a root group -- including the lock refusal, which
        # is the one condition carrying a safety consequence.  See D-228 and ADR-0090.
        "group",
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
        # Schematic provenance KiCad has written as first-class tokens since the 8.0 dev cycle
        # (before that, `(property "Sheetfile" …)`, which the parser still upgrades).  `sheetfile`
        # has six read sites in KiCad and not one is geometric; `sheetname` has twelve, of which
        # the only ones that can reach copper are the custom-DRC-rule predicates
        # `memberOfSheet()`/`hasComponentClass()` -- a selector for a clearance, never a value, and
        # a `.kicad_dru` surface this adapter does not read at all.  See D-228 and R-179.
        "sheetfile",
        "sheetname",
        # The footprint-wide solder-mask and solder-paste defaults for its pads.  They move stencil
        # apertures and mask openings and **no copper**: `FOOTPRINT::TransformPadsToPolySet` adds
        # mask expansion only under `case F_Mask: case B_Mask:` and paste only under
        # `case F_Paste: case B_Paste:`, and every copper layer falls through `default: break;`
        # with no adjustment (`footprint.cpp:5027-5045`).  That is the same argument D-227 already
        # accepted for the board-level pair one level up.
        "solder_mask_margin",
        "solder_paste_margin",
        "solder_paste_margin_ratio",
        # KiCad's own `// legacy token` for the ratio: the 8.0 footprint writer emitted this
        # spelling and the 9.0 writer emits `solder_paste_margin_ratio`, while both still parse
        # into one arm (`pcb_io_kicad_sexpr_parser.cpp:5909-5910`).  A reader that knows only the
        # new spelling silently mis-reads every 8.0-written board, so both are carried.
        "solder_paste_ratio",
        "tags",
        "tstamp",
        "uuid",
        # Accepted only in its *attaching* modes; `_require_attaching_footprint_zone_connection`
        # refuses `0` and anything outside the written domain at the field's own locator.
        "zone_connect",
    }
)
# Footprint children this adapter admits without modelling, and the exact set
# `unmodelled_footprint_field_count` reports.  `group` is deliberately absent: it is counted by
# `unmodelled_group_count`, which already means exactly this for a root group, and splitting one
# construct across two counters would make neither answer "how many groupings did I lose".
# `zone_connect` is absent too -- it is *validated to be inert for the claim Board IR publishes*
# rather than merely discarded, which is a different disclosure and is carried by ADR-0091's
# existing reasoning.
_UNMODELLED_FOOTPRINT_HEADS = frozenset(
    {
        "sheetfile",
        "sheetname",
        "solder_mask_margin",
        "solder_paste_margin",
        "solder_paste_margin_ratio",
        "solder_paste_ratio",
    }
)
# Every remaining head KiCad's own `parseFOOTPRINT_unchecked` accepts that Board IR does not model,
# so that a footprint refusal **names the field it refused** instead of saying that *some* field was
# unsupported.  That field-less sentence is the defect issue #188 tracks, and it is precisely why
# B-129's masker could not decompose this wall: the diagnostic named the container and no field.
#
# The table is the union of the top-level `case T_…` arms of `parseFOOTPRINT_unchecked` on the
# KiCad `9.0` and `10.0` release branches, minus the heads above, minus the layer-routed
# `fp_*`/`property`/`point` branch, minus `zone`, which has its own sentence.  Adding a head here
# **changes a message and never a verdict** -- every one already refused through the allowlist --
# which is why this table needs no direction-of-error argument of its own.  A head absent from it
# still refuses, unnamed, and no board byte is ever interpolated into a message: the interpolated
# token is a literal from this tuple, selected by lookup.
#
# `clearance` is the one entry that is *not* merely unmodelled.  It is a **replacement, not a
# maximum**: `DRC_ENGINE::EvalRules` resolves it through `GetClearanceOverrides` at `:1146`, before
# custom-rule iteration at `:1922`, so it beats netclass *and* rules and can lower effective
# clearance to the board minimum.  It sizes the void `ZONE_FILLER::buildCopperItemClearances`
# leaves around every pad of the footprint (`zone_filler.cpp:2195-2209`) and seeds the router's
# worst-case radius (`pns_kicad_iface.cpp:2361`).  Ignoring a non-zero one is the forbidden
# direction.  B-132 measured `clearance_zero: 0` on the cohort, so the narrowed acceptance that
# would admit only the provably inert zero was declined on evidence -- it clears no board.
_UNSUPPORTED_FOOTPRINT_FIELDS = (
    "autoplace_cost180",
    "autoplace_cost90",
    "barcode",
    "clearance",
    "component_classes",
    "dimension",
    "embedded_files",
    "generator",
    "generator_version",
    "image",
    "jumper_pad_groups",
    "private_layers",
    "stackup",
    "table",
    "tedit",
    "thermal_gap",
    "thermal_width",
    "units",
    "variant",
    "version",
)
# The non-copper technical layers a KiCad *aperture* pad may occupy. KiCad defines an aperture pad
# as one with no copper layer assigned: a solder-paste stencil opening or mask opening that is not
# an electrical connection point and cannot even carry a pad number. See
# docs/research/kicad-aperture-pads-and-net-ties-v1.md.
_APERTURE_PAD_LAYERS = frozenset({"B.Mask", "B.Paste", "F.Mask", "F.Paste"})

# Every pad field KiCad's own `parsePAD` accepts that Board IR does not model, so that a pad
# refusal names the field it refused instead of saying that *some* field was unsupported.
#
# ADR-0091 made seven of these reachable by running this loop ahead of the pad allowlist, and left
# the other nineteen behind the allowlist's field-less sentence -- which is the defect issue #152
# reported again, one head further along.  The table is now the whole top-level switch of
# `PCB_IO_KICAD_SEXPR_PARSER::parsePAD` (`pcb_io_kicad_sexpr_parser.cpp:6397-7114`, 39 heads on
# KiCad master) minus the thirteen this adapter models and minus `property`, which ADR-0100
# decides on a closed value table of its own.  `offset` is the one entry KiCad master's pad
# grammar no longer has -- modern KiCad parses `(offset …)` inside `(drill …)` -- and it is kept
# because ADR-0091 pinned its sentence and an older file can still carry it.
#
# **Adding a head here changes a message and never a verdict.**  Every one of these already
# refused through `_reject_unknown_children`; the loop only reaches them earlier and names them.
# That is why this table needs no direction-of-error argument, and why no board's conversion
# outcome moves with it.  A head absent from the table is one KiCad cannot write: it still
# refuses, unnamed, through the allowlist, and no board byte is ever interpolated into a message.
#
# `zone_connect` deliberately is *not* in this tuple; see ADR-0091 and
# `_require_attaching_pad_zone_connection`.
_UNSUPPORTED_PAD_FIELDS = (
    "back_post_machining",
    "backdrill",
    "chamfer",
    "chamfer_ratio",
    "clearance",
    "die_delay",
    "die_length",
    "front_post_machining",
    "keep_end_layers",
    "offset",
    "padstack",
    "rect_delta",
    "sim_electrical_type",
    "solder_mask_margin",
    "solder_paste_margin",
    "solder_paste_margin_ratio",
    "teardrops",
    "tenting",
    "tertiary_drill",
    "thermal_bridge_width",
    "thermal_gap",
    "thermal_width",
    "zone_layer_connections",
)

# KiCad's `PAD_PROP` fabrication property, written as `(property <bare token>)` by
# `PCB_IO_KICAD_SEXPR::format( const PAD* )` (`pcb_io_kicad_sexpr.cpp:1886-1901, 2015-2016`) and
# read by `parsePAD`'s `T_property` arm.  The two sets below partition the eight tokens the writer
# can emit; `none` is in neither, because the writer emits the token only for a non-`NONE` value
# and `(property none)` is a form KiCad's reader accepts but its writer cannot produce.
#
# Not one of the eight changes a pad's copper, its hole, its layer span, its clearance or its
# connectivity.  A complete sweep of `PAD_PROP` over KiCad master -- every enumerator literal and
# every `GetProperty()` call site, swept in both directions so a site testing `== NONE` is not
# invisible -- finds fabrication-file attributes (Gerber apertures, drill files), padstack
# advisories, a footprint type hint, board statistics, the 3D exporter and the property manager.
# `CASTELLATED` is the single exception, and the reason is **not** the one an earlier revision of
# this comment gave.  `PNS_KICAD_IFACE::syncWorld` registers a castellated pad's hole with
# `AddEdgeExclusion` (`pns_kicad_iface.cpp:2366-2371`), and that is a *forgiveness* region, not an
# obstacle: `Edge.Cuts` itself syncs as a non-routable solid (`:2044-2076`), and
# `ITEM::collideSimple` waives a collision with an `Edge_Cuts`-parented obstacle when the collision
# point lands inside one of those shapes (`pns_item.cpp:226-252`, via the point-in-shape scan at
# `pns_node.cpp:797-806`).  The DRC provider waives it twice over
# (`drc_test_provider_edge_clearance.cpp:209-215` for a track, `:434-438` for the pad).  So the
# token **grants** routing space near the edge, and discarding it leaves CopperMCP *stricter* than
# KiCad -- over-refusal, the allowed direction.
#
# It is refused anyway, on a different and weaker argument, stated as the caution it is: fabrication
# routes the half-holes out of the physical board and `Edge.Cuts` does not, so Board IR's outline
# claims board area that will not exist -- and KiCad's DRC is *more permissive* exactly there, so
# ADR-0004's authoritative-DRC backstop is at its weakest precisely where Board IR would over-claim.
# See ADR-0100 and docs/research/kicad-pad-fabrication-property-v1.md.
_ACCEPTED_PAD_PROPERTIES = frozenset(
    {
        "pad_prop_bga",
        "pad_prop_fiducial_glob",
        "pad_prop_fiducial_loc",
        "pad_prop_heatsink",
        "pad_prop_mechanical",
        "pad_prop_pressfit",
        "pad_prop_testpoint",
    }
)
# The closed pad allowlist, hoisted out of the conversion loop so that the property it and
# `_UNSUPPORTED_PAD_FIELDS` hold jointly -- together they cover every head KiCad's `parsePAD`
# accepts -- is a testable statement rather than two lists a reader has to diff by eye.  `layer` is
# the one entry outside that grammar: KiCad master writes `(layers …)`, and the singular is kept
# for older files.
_SUPPORTED_PAD_FIELDS = frozenset(
    {
        "at",
        "drill",
        "layer",
        "layers",
        "locked",
        "net",
        "options",
        "pinfunction",
        "pintype",
        "property",
        "primitives",
        "remove_unused_layers",
        "roundrect_rratio",
        "size",
        "thermal_bridge_angle",
        "tstamp",
        "uuid",
        "zone_connect",
    }
)

#: The one writable token that is refused, and the literal the refusal names it by.  The message
#: emits *this* constant after an equality test, never the source atom, so a board cannot steer one
#: byte into a diagnostic -- SEC-133 and SEC-136, same mechanism.
_REFUSED_PAD_PROPERTY = "pad_prop_castellated"

# Pad shape tokens KiCad's writer emits that Board IR does not model, mapped to the refusal
# each earns.  Same rule as `_UNMODELLED_ROOT_HEADS` and `_UNMODELLED_COPPER_GRAPHIC_HEADS`: the
# sentence is a *value from this table*, selected by an equality test against the source token and
# never built from it, so the refusal names the construct without echoing one byte of the board.
# A token absent from this table and from `PadShape` is not a documented pad shape at all and
# refuses unnamed through `PadShape(...)` below, with the indexed locator still saying which pad.
#
# The domain is KiCad's six writer tokens minus Board IR's four anchor-shape members and the
# separately dispatched `custom` token.
# `pcb_io_kicad_sexpr.cpp:1643-1649` is the whole vocabulary -- `circle`, `rect`, `oval`,
# `trapezoid`, `roundrect` (written for both `ROUNDRECT` and `CHAMFERED_RECT`) and `custom` -- so
# the name below is the complete unmodelled remainder, and
# `test_the_unmodelled_pad_shape_table_is_kicads_tokens_minus_board_irs` asserts that partition
# rather than probing tokens someone thought to write down. `custom` is handled before this table:
# its anchor remains under-approximating attachment geometry while its primitive union is carried
# separately as a containing obstacle envelope.
#
# `custom` was the entry this table originally existed for. Board IR 0.4 resolves that refusal by
# carrying the two direction-typed regions separately. A
# KiCad custom pad is an anchor rect-or-circle of `size` **unioned** with a list of drawn
# primitives -- established twice over in `pad.cpp:3275-3315`, where `MergePrimitivesAsPolygon`
# seeds the polygon with the anchor at 3284-3296 and then `BooleanAdd`s the primitives at 3312,
# and in `pad.cpp:1278-1292`, where `buildEffectiveShape` adds every non-proxy primitive on top of
# the anchor shape it already added.  Measured against KiCad 10.0.5's own plotter, a custom pad
# whose single primitive starts 5 mm past its anchor's edge plots **both** shapes.  So the
# primitives do not replace the anchor, and reading the anchor alone would drop real copper.
#
# Unlike copper text (ADR-0095) an envelope *is* derivable: every one of KiCad's six copper
# primitive heads carries exact millimetre geometry in the document and admits an exact integer
# nanometre containing box, `gr_curve` included -- a cubic Bezier is a convex combination of its
# four control points at every parameter value, so it cannot leave their bounding box.  The
# earlier refusal was therefore **not** ADR-0095's "no envelope exists". It was that the prior
# Board IR `Pad` had nowhere to put one: `routing/astar.py::_pad_extent` and
# `routing/layered_board_adapter.py::_pad_bounds` read `shape`, `size_x_nm` and `size_y_nm` in the
# **over**-approximating direction to build the obstacle, while
# `routing/astar.py::_pad_core_extent` reads *the same three fields* in the **under**-approximating
# direction to build the attachment core -- and for `PadShape.RECT` the two collapse to one
# rectangle to within a nanometre.  Any `Pad` accepted for a custom pad would therefore have to
# both contain and be contained by the pad's copper, which forced the copper to *be* that
# axis-aligned rectangle.  Sizing it to the union's bounding box keeps the obstacle sound and makes
# the attachment core claim copper that is not there, which is the defect `_pad_cores` already
# names as "the one direction an attachment core may never err in"; sizing it to the anchor keeps
# the core sound and lets the obstacle miss real metal.  See ADR-0100 and
# docs/research/kicad-custom-pad-envelope-v1.md.
#
# Those three call sites are the *clearest* readers, not the only ones.  The P3.3a survey
# (2026-08-14, docs/research/pad-geometry-reader-survey-v1.md, `B-112`) enumerated all of them by
# field access **and** by accessor call: 23 sites across 14 modules, of which 8 need the
# over-approximating direction, 3 the under-approximating one, and 4 need an exact region that
# neither answers -- `canonical.py::_pad` feeds the snapshot digest, `codec.py` feeds a published
# schema closed by `additionalProperties: false`, `Pad.__post_init__` binds drill and radius
# invariants to `size`, and `circuit_scene.py::_pad_object` publishes `size_nm` on the scene
# contract.  Three more read pad geometry in a direction they do not get today, all in
# `placement/legalizer.py` and all reached through the stored `_PlacedPad.bounds` rather than
# through any field name here.  The argument above is strengthened by that count and not weakened:
# one rectangle cannot serve 18 readers if it cannot serve 3. Board IR 0.4 performs that reader
# split: obstacle consumers use `copper_envelope`, attachment consumers keep the anchor, and
# canonical/codec/scene consumers carry or disclose both without claiming exact primitive parity.
#
# `trapezoid` earns a different sentence for a weaker reason and the difference is deliberate:
# a trapezoid is a convex quadrilateral derivable from `size` and `(rect_delta ...)`, so both
# directions are available and it is unmodelled rather than unmodellable.  A reader must be able
# to tell those two apart from the message alone, which is the whole complaint issue #153 opens
# with.
_UNMODELLED_PAD_SHAPES: dict[str, str] = {
    "trapezoid": "trapezoid pad shapes are unsupported in Board IR adapter v0.2",
}

_CUSTOM_PAD_PRIMITIVE_HEADS = frozenset(
    {"gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly", "gr_curve", "gr_bbox", "gr_vector"}
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


@dataclass(frozen=True, slots=True)
class _OutlineEdge:
    """One ``Edge.Cuts`` shape read as an edge of the outline cycle.

    ``mid`` is the arc's third drawn point and ``None`` for a straight segment.  Holding both in
    one type is what lets the cycle be assembled once, from endpoints, before anything asks which
    way an edge bends -- the topology of an outline cannot depend on the bend, and reading the two
    heads into two collections is what previously made it look as though it did.
    """

    start: PointNM
    end: PointNM
    mid: PointNM | None
    locator: str
    expression: SExpr


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
        # ``(group ...)`` expressions accepted as editor organisation and not modelled, at board
        # root **and inside a footprint** -- KiCad parses and writes both through the same code, so
        # one number answers "how many groupings did I lose" and two would answer neither.
        # Measured in the preflight and reported rather than dropped in silence.
        self.group_count = 0
        # ``connect``-token pads converted as ``PadKind.SMD`` (ADR-0096).  Counted for the same
        # reason ``group_count`` is: the conversion discards a distinction the source made,
        # and a count is the only way to say so without a diagnostic that would refuse the board.
        self.edge_connector_pad_count = 0
        self.unmodelled_pad_property_count = 0
        # Pads carrying a validated `thermal_bridge_angle` token.  The angle affects KiCad's
        # derived zone-fill spokes, not the pad envelope represented by Board IR, so the token is
        # accepted as a typed non-claim and disclosed rather than silently discarded.
        self.unmodelled_thermal_bridge_angle_pad_count = 0
        # `setup` children D-227 admits without modelling: the stackup, the two origins and the
        # two paste clearances.  Counted as expressions, so a document writing one twice is
        # refused by `child()` before this ever runs and cannot inflate the number.
        self.unmodelled_setup_field_count = 0
        # `footprint` children D-228 admits without modelling: the two schematic provenance strings
        # and the four stencil/mask defaults.  Counted as expressions per footprint, so the number
        # is a cardinality over the whole board rather than a per-footprint flag.
        self.unmodelled_footprint_field_count = 0
        # `(layer …)` entries inside an accepted stackup, dielectric entries included.  It is a
        # separate number from the one above because it answers a separate question: the field
        # count says a stack was dropped, this says how big it was, and a caller comparing it
        # against `len(copper_layers)` can see the physical entries it never received.
        self.unmodelled_stackup_layer_count = 0
        # How far inside the drawn boundary the modelled one runs, in nanometres.  Zero for a
        # board whose outline is drawn entirely with segments or as a rectangle, because every
        # vertex of those is a drawn point.  Non-zero only when an arc was inscribed, and then it
        # is an upper bound rather than the achieved figure.  See D-229 and ADR-0124.
        self.outline_inward_deviation_nm = 0
        # Custom-pad primitive vertices are reduced to an envelope and therefore disappear before
        # Board IR validation counts serialized rings.  Retain only their count so caller-provided
        # vertex budgets still cover the complete accepted source geometry.
        self.custom_pad_primitive_vertex_count = 0
        # Stray copper-graphic vertices, charged for exactly the reason above: they are reduced to
        # a bounding envelope and therefore disappear before Board IR validation counts serialized
        # rings, so a caller's vertex budget would otherwise not cover them.  A separate number
        # from the pad one because the two answer different questions and merging them would let a
        # board with no custom pads report custom-pad vertices.
        self.graphic_envelope_vertex_count = 0
        # Stray copper `fp_poly` expressions D-230 converts as a conservative bounding envelope
        # rather than as exact copper.  The obstacle is a superset of the drawn shape, so this is
        # the typed disclosure of an *approximation* rather than of an erasure -- the same footing
        # `max_roundrect_rounding_nm` is on.  Net-tie copper never contributes: it takes the
        # pre-existing ADR-0092 path and is counted nowhere.
        self.footprint_copper_graphic_envelope_count = 0
        # Root ``(property ...)`` expressions accepted as board metadata and not modelled, on the
        # same measured-and-reported footing as the group count above.
        self.root_board_property_count = 0
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

    def _check_root_property(self, expression: SExpr, locator: str) -> None:
        """Accept one root ``(property "<key>" "<value>")`` as board metadata, on a closed shape.

        The accepted subset is stated as a **closed field table** rather than as prose, because
        ADR-0092's prose subset admitted two forms it did not mean and neither was found by
        reading it. Exactly this and nothing else is accepted:

        =========================  ========  ====================================================
        Field                      Required  Permitted
        =========================  ========  ====================================================
        positional atom 0 (key)    yes       exactly one atom, quoted in the source
        positional atom 1 (value)  yes       exactly one atom, quoted in the source
        further positional atoms   --        none; any third direct atom refuses
        child expressions          --        none; any ``(...)`` child refuses
        =========================  ========  ====================================================

        Every form outside that table is one KiCad's own parser rejects: ``parseBoardProperty`` is
        ``NeedSYMBOL(); NeedSYMBOL(); NeedRIGHT();``, so a third atom or a nested expression is a
        hard parse error there too. The one place this is *narrower* than KiCad is quoting --
        ``NeedSYMBOL`` accepts a bare token, while ``formatProperties`` writes both halves through
        ``Quotew``, which quotes unconditionally. An unquoted atom is therefore a form KiCad's
        writer cannot emit, it appears nowhere in the surveyed corpus, and refusing it is the
        conservative direction, so it refuses rather than being assumed equivalent.

        The safety argument is not the with/without equality -- that equality measures schema
        stability and would hold for an unsafe value too (D-178, and ADR-0090 for the case where it
        hid a real defect). It is KiCad's own model, and the honest version of that model is *not*
        "a property is cosmetic". ``BOARD::ResolveTextVar`` substitutes ``${KEY}``, and substitution
        has termini that are real board content: text on a copper layer is plotted copper,
        ``PCB_BARCODE`` builds its pattern from shown text, and ``DRC_ENGINE::loadRules`` expands
        the same tokens over a ``.kicad_dru``, so a property can supply a clearance to a custom
        rule. What makes the accept sound is that **every one of those termini is already refused
        or already outside this adapter, independently of any property**: a root graphic on copper
        refuses, a footprint graphic on copper refuses, ``barcode`` is not in the root vocabulary,
        and ``.kicad_dru`` is a file this adapter has never parsed -- while the authoritative DRC
        surface hands that file to KiCad itself, over source bytes the write-back path preserves
        verbatim. Nothing reachable from a property reaches Board IR content, so neither the
        over-approximated obstacle set nor the under-approximated connectivity and outline moves.

        There is no reserved key, which is ADR-0090's locked-group question asked over the key
        rather than skipped: no board property key is special-cased anywhere in KiCad, the format's
        reserved property keys are *symbol* properties, and a key colliding with a built-in
        text-variable token is shadowed by the resolver rather than empowered. The key and the
        value are board bytes and are read past without being echoed into a diagnostic, an identity
        or a snapshot.

        What is *not* claimed is that the map survives a round trip through Board IR. A caller that
        rebuilt a board from a snapshot alone would lose it, and a caller that rendered board text
        would render ``${KEY}`` unexpanded. That is a modelling gap, so it is recorded --
        ``ConversionResult.unmodelled_board_property_count`` -- rather than dropped in silence.
        See R-139 and ADR-0094.
        """

        self._reject_unknown_children(expression, frozenset(), locator)
        self._validate_direct_atoms(
            expression,
            positional_atoms=2,
            allowed=frozenset(),
            locator=locator,
        )
        direct_atoms = tuple(item for item in expression.items[1:] if isinstance(item, str))
        if not all(is_quoted_atom(atom) for atom in direct_atoms[:2]):
            self.fail(
                "unsupported.construct",
                "a root board property must be two quoted strings",
                locator,
                object_kind="property",
            )

    def _check_group(self, expression: SExpr, locator: str) -> None:
        """Accept one *unlocked* ``(group ...)`` as editor organisation, on a closed shape.

        Serves a group at board root **and one inside a footprint**, because KiCad dispatches both
        to the same ``parseGROUP`` and writes both through the same formatter -- there is no
        separate footprint-group grammar to write a second validator against (D-228).

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
        board_properties = 0
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
                self._check_group(item, root_locator)
                groups += 1
                continue
            if head == _ROOT_PROPERTY_HEAD:
                self._check_root_property(item, root_locator)
                board_properties += 1
                continue
            if head.startswith("gr_"):
                layer = self._graphic_layer(item, "kicad_pcb.graphic")
                if layer == "Edge.Cuts" and head not in _EDGE_CUTS_OUTLINE_HEADS:
                    self.fail(
                        "unsupported.construct",
                        _EDGE_CUTS_REFUSED_OUTLINE_HEADS.get(
                            head, "Edge.Cuts graphic is not a supported outline primitive"
                        ),
                        "kicad_pcb.graphic",
                        object_kind="outline",
                    )
                if self._is_routing_layer(layer) and layer != "Edge.Cuts":
                    message, kind = _UNMODELLED_COPPER_GRAPHIC_HEADS.get(
                        head, _UNNAMED_COPPER_GRAPHIC
                    )
                    self.fail(
                        "unsupported.construct",
                        message,
                        "kicad_pcb.graphic",
                        object_kind=kind,
                    )
                continue
            self.fail(
                "unsupported.construct",
                _UNMODELLED_ROOT_HEADS.get(
                    head, "root expression contains an unsupported semantic construct"
                ),
                root_locator,
            )
        self.group_count = groups
        self.root_board_property_count = board_properties

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
            self._validate_leaf_payloads(setup, "kicad_pcb.setup", _SETUP_SCALAR_PAYLOADS)
            self._count_unmodelled_setup_fields(setup)
            self._validate_stackup(setup)

        for footprint_index, footprint in enumerate(children(self.root, "footprint")):
            locator = f"kicad_pcb.footprint[{footprint_index}]"
            self._validate_direct_atoms(
                footprint,
                positional_atoms=1,
                allowed=frozenset({"locked"}),
                locator=locator,
            )
            # Named-field refusals run *before* the allowlist, exactly as the pad path does, so a
            # refusal says which field it refused. Widening this table changes a message and never
            # a verdict: every head in it already refused through the allowlist below.
            for unsupported_head in _UNSUPPORTED_FOOTPRINT_FIELDS:
                if children(footprint, unsupported_head):
                    self.fail(
                        "unsupported.construct",
                        f"footprint field {unsupported_head!r} is unsupported",
                        f"{locator}.unsupported",
                        object_kind="footprint",
                    )
            self._validate_leaf_payloads(
                footprint,
                locator,
                _FOOTPRINT_SCALAR_PAYLOADS,
                message="unsupported footprint field value",
            )
            self._require_attaching_footprint_zone_connection(footprint, f"{locator}.zone_connect")
            for unmodelled_head in _UNMODELLED_FOOTPRINT_HEADS:
                self.unmodelled_footprint_field_count += len(children(footprint, unmodelled_head))

            for item in footprint.items[1:]:
                if not isinstance(item, SExpr) or item.head is None:
                    continue
                head = item.head
                if head == "group":
                    # The same validator, the same closed child grammar, the same lock refusal and
                    # the same counter a root group gets. `BOARD_ITEM::IsLocked()` consults
                    # `GetParentGroup()` at query time, so a locked group locks its members
                    # transitively whatever the load-time pass ordering happens to do.
                    self._check_group(item, f"{locator}.group")
                    self.group_count += 1
                    continue
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
                        if layer != "Edge.Cuts" and head == "fp_poly":
                            # Stray filled copper: bounded here rather than refused, and
                            # *validated* here rather than at conversion time so the diagnostic
                            # keeps its position in this walk. The reader is the same one
                            # `_footprint_copper_obstacle_segments` uses, so there is one
                            # grammar with two callers rather than two grammars. Anything the
                            # reader cannot bound refuses inside it, by name. See D-230.
                            self._read_footprint_copper_polygon(item, layer, f"{locator}.graphic")
                            continue
                        self._refuse_footprint_routing_graphic(footprint, layer, locator, head)
                    continue
                if head not in _FOOTPRINT_METADATA_HEADS:
                    self.fail(
                        "unsupported.construct",
                        "footprint contains an unsupported semantic field",
                        f"{locator}.unsupported",
                        object_kind="footprint",
                    )

    def _refuse_footprint_routing_graphic(
        self, footprint: SExpr, layer: str, locator: str, head: str
    ) -> Never:
        """Refuse a footprint graphic on a routing layer, naming what it actually is.

        Every case here refuses, and that is the point: a graphic on a copper layer *is copper*, so
        it is an obstacle, and the one outcome forbidden here is dropping it. What differs is the
        reason, and the reasons ask for different fixes:

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
        - **Any other copper layer.** A filled `fp_poly` no longer reaches this method either: D-230
          bounds it as a netless obstacle in `_footprint_copper_obstacle_segments`. What is left is
          the remainder, and it refuses **naming its own primitive kind** rather than through one
          sentence for all of them -- ADR-0123's rule applied one structural level down, and the
          reason it is worth applying here is that the kinds ask for genuinely different fixes. A
          stroked `fp_line` or `fp_arc` is geometrically a track and an *exact* `Segment`/`Arc`
          model exists for it; a filled `fp_circle` is a disc that no Board IR obstacle type
          represents; footprint text on copper is ADR-0095's refusal, where the five exit
          conditions are still unmet. B-136 measured every one of these at **zero occurrences on
          copper** across the two boards that reach this method, while finding 32,532 `fp_line`,
          1,064 `property`, 243 `fp_rect`, 184 `fp_text`, 50 `fp_circle` and 23 `fp_arc` on layers
          that are *not* copper -- so modelling any of them would be modelling a case that has not
          been observed, and naming them is all this change is entitled to do.

        The sentence is always a **value from a closed table**, selected by an equality test against
        the source token and never built from it, so a refusal names the construct without echoing
        one byte of the board. Same rule as `_UNMODELLED_COPPER_GRAPHIC_HEADS` at the root.
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
        message, kind = _UNMODELLED_FOOTPRINT_COPPER_HEADS.get(
            head, _UNNAMED_FOOTPRINT_COPPER_GRAPHIC
        )
        self.fail(
            "unsupported.construct",
            message,
            f"{locator}.graphic",
            object_kind=kind,
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

    def _validate_leaf_payloads(
        self,
        parent: SExpr,
        locator: str,
        grammars: dict[str, tuple[int, int, str, str | None]],
        message: str = "unsupported setup field value",
    ) -> None:
        """Close the payload of every accepted leaf: no children, exact arity, checked tokens.

        The head allowlists above decide *which* children may appear and say nothing about what
        nests inside one, so an accepted field was an open container until this ran. Three checks,
        and the first is the one that mattered: a leaf may hold **no child expression at all**, so
        `(grid_origin (zone_defaults ...))` can no longer carry the exact construct
        `_REFUSED_SETUP_HEADS_ON_RECORD` refuses one level up. Then exact arity, then the token
        kind KiCad writes. Every refusal names the field's own locator, never the container's.

        Direction of error: every one of these values is discarded, so the check can only ever
        over-refuse. It is deliberately looser than the nanometre-precision decimal the conversion
        path uses, because refusing a well-formed board over a precision this decision does not
        claim would be a cost with nothing bought.
        """

        for head, (minimum, maximum, kind, trailing_flag) in grammars.items():
            field = self._one(parent, head, locator, required=False)
            if field is None:
                continue
            field_locator = f"{locator}.{head}"
            # An empty allowlist refuses *any* child, which is what makes the leaf a leaf.
            self._reject_unknown_children(field, frozenset(), field_locator)
            values = self._values(
                parent,
                head,
                field_locator,
                minimum=minimum,
                maximum=maximum,
            )
            for index, token in enumerate(values):
                if trailing_flag is not None and index >= minimum:
                    if token != trailing_flag or is_quoted_atom(token):
                        self.fail(
                            "unsupported.construct",
                            message,
                            field_locator,
                        )
                    continue
                if kind is _NUMBER and (
                    is_quoted_atom(token)
                    or len(token) > 64
                    or not _PAYLOAD_DECIMAL.fullmatch(token)
                ):
                    self.fail(
                        "unsupported.construct",
                        message,
                        field_locator,
                    )
                if kind is _FLAG and (is_quoted_atom(token) or token not in _PAYLOAD_FLAGS):
                    self.fail(
                        "unsupported.construct",
                        message,
                        field_locator,
                    )
                if kind is _TEXT and len(token) > 512:
                    self.fail(
                        "unsupported.construct",
                        message,
                        field_locator,
                    )

    def _count_unmodelled_setup_fields(self, setup: SExpr) -> None:
        """Count the accepted `setup` children D-227 admits and does not model.

        It counts *expressions*, and it counts only the five heads this slice newly admits --
        never the heads that were already accepted before it. Two reasons, and the second is why
        the partition is not arbitrary. A count is a disclosure of a specific erasure, and the
        erasure D-227 creates is these five: `stackup`, the two origins, and the two paste
        clearances. The heads accepted earlier already have their own recorded reasoning, and
        folding them in would silently restate a number whose meaning nobody agreed to.
        """

        for item in setup.items[1:]:
            if isinstance(item, SExpr) and item.head in _UNMODELLED_SETUP_HEADS:
                self.unmodelled_setup_field_count += 1

    def _validate_stackup(self, setup: SExpr) -> None:
        """Read past the physical stack, refusing only what asserts copper at the board edge.

        The whole point of doing this per field rather than per block is direction of error. The
        stack's Z geometry -- order, thickness, material, `epsilon_r`, `loss_tangent`, finish,
        colour -- describes the board perpendicular to everything Board IR models, so ignoring it
        cannot shrink an obstacle or widen the routing room. `castellated_pads`, `edge_connector`
        and `edge_plating` are different in kind: each asserts plated or removed material at the
        board edge that no pad, track, via, zone or graphic in the document represents, so reading
        one past would under-report copper. Those refuse.

        The copper layer *set* is not read from here. It comes from the root `(layers ...)`
        section, which this adapter already validates; a stackup entry carries no XY coordinate,
        no net and no clearance, so it cannot introduce a layer's geometry either way. KiCad
        agrees on the direction: its stackup parser matches each `(layer "NAME")` against the
        already-enabled layers and `BuildDefaultStackupList` reads `GetEnabledLayers()` and
        `GetCopperLayerCount()`, so the stack is derived *from* the layer set rather than the
        other way round.

        **One path does make a layer `thickness` load-bearing for copper, and this decision rests
        on that path already being closed.** In KiCad 10 a pad or via carrying
        `front_post_machining`/`back_post_machining` (counterbore or countersink) has its copper
        on a given layer knocked out, or its countersink diameter computed, by comparing the
        machining depth against `BOARD_STACKUP::GetLayerDistance()` -- and that result reaches
        `GetEffectiveShape()`, every DRC clearance test, `zone_filler.cpp` and connectivity. Were
        a post-machined pad or via ever accepted while the stack was read past, the copper this
        adapter reports would be *larger* than the board's, which is the forbidden direction.
        It cannot happen: `front_post_machining` and `back_post_machining` are in
        `_UNSUPPORTED_PAD_FIELDS`, and the via allowlist refuses them too. That coupling is not
        an incidental fact -- it is the premise -- so it is pinned by a test in both directions
        rather than left to be rediscovered. See ADR-0122.
        """

        stackup = self._one(setup, "stackup", "kicad_pcb.setup", required=False)
        if stackup is None:
            return
        locator = "kicad_pcb.setup.stackup"
        self._reject_unknown_children(stackup, _STACKUP_HEADS, locator)
        self._validate_direct_atoms(
            stackup,
            positional_atoms=0,
            allowed=frozenset(),
            locator=locator,
        )
        self._validate_leaf_payloads(stackup, locator, _STACKUP_SCALAR_PAYLOADS)
        for head in _COPPER_BEARING_STACKUP_HEADS:
            values = self._values(stackup, head, locator, minimum=1, maximum=1, required=False)
            if values and (values != ("no",) or is_quoted_atom(values[0])):
                self.fail(
                    "unsupported.construct",
                    "non-neutral board-edge fabrication treatment is unsupported",
                    f"{locator}.{head}",
                )
        for layer_index, layer in enumerate(children(stackup, "layer")):
            layer_locator = f"{locator}.layer[{layer_index}]"
            self._reject_unknown_children(layer, _STACKUP_LAYER_HEADS, layer_locator)
            # One positional atom -- the layer's name -- and no bare flags after it. A stackup
            # entry that carried a second positional token would be a construct this grammar has
            # not read, so it refuses rather than being counted as one that was.
            self._validate_direct_atoms(
                layer,
                positional_atoms=1,
                allowed=frozenset(),
                locator=layer_locator,
            )
            self._validate_leaf_payloads(layer, layer_locator, _STACKUP_LAYER_PAYLOADS)
            self.unmodelled_stackup_layer_count += 1

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

    def _require_attaching_footprint_zone_connection(self, footprint: SExpr, locator: str) -> None:
        """Accept a footprint `zone_connect` default only in the modes that *attach*.

        This is ADR-0091's pad rule, one level up and widened in scope: `PAD::
        GetZoneConnectionOverrides` consults the pad's own value first and falls back to the parent
        footprint's (`pad.cpp:2139-2151`), so the footprint default governs exactly those pads that
        omit their own. The value domain, the writer's suppression of `INHERITED`, and the absence
        of any range check on read (`(ZONE_CONNECTION) parseInt(…)`, `parser:5931`) are all
        identical to the pad form.

        The direction of error is identical too, and only one value breaks it. The finished fill is
        intersected with the zone's own extents (`zone_filler.cpp:3147`), so poured copper stays a
        subset of the zone boundary for *every* value and the obstacle direction cannot break here.
        What breaks is connectivity: `1`, `2` and `3` all attach -- `DRC_ENGINE::EvalZoneConnection`
        collapses `3` to thermal on a plated through-hole pad and to solid on any other -- so
        discarding one never turns `Zone.pad_connection` into a claim of attachment where there is
        none. `0` detaches, and discarding it would leave Board IR publishing attachment for every
        non-overriding pad of a footprint whose designer deliberately isolated them all. That is the
        one direction this project forbids, so it fails closed.

        **One thing does not transfer from the pad, and it is recorded rather than smoothed over.**
        The pad override is consumed at `drc_engine.cpp:1211`, *before* custom-rule iteration, so a
        rule cannot detach a pad the file attached. The footprint override is consumed at `:2089`,
        *after* it, so a custom rule can override this default -- including detaching pads the
        footprint declared `2`. That does not make accepting the attaching modes unsound, because a
        rule can detach any pad whether or not the footprint writes this token, so the exposure is
        the existing unread-`.kicad_dru` one (R-179) and is not widened by reading this field. It
        does mean the pad note's load-bearing sentence must not be restated for footprints.
        """

        values = self._values(
            footprint,
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
                "footprint zone connection mode is unsupported",
                locator,
                object_kind="footprint",
            )
        if value == _DETACHING_PAD_ZONE_CONNECTION:
            self.fail(
                "unsupported.construct",
                "footprint zone_connect 0 detaches its pads from their pour and is unsupported",
                locator,
                object_kind="footprint",
            )

    def _require_valid_thermal_bridge_angle(self, pad: SExpr, locator: str) -> bool:
        """Validate and disclose a pad-level thermal spoke angle without modelling it.

        KiCad parses this field as one decimal degree value and applies it only while deriving
        thermal-relief spokes.  It does not change the pad envelope, hole, layers, clearance, or
        zone outline.  Conservative routing therefore keeps its over-approximating zone boundary,
        while exact-fill routing consumes KiCad's freshness-verified polygons with this value
        already applied.  Board IR cannot reproduce the fill from a snapshot alone, so presence
        is returned to the caller for measured MCP disclosure rather than treated as an inert
        field.  See issue #186 and D-205.

        The accepted numeric language is CopperMCP's existing exact rotation boundary: bare,
        non-exponent decimal degrees with at most microdegree precision.  KiCad itself does not
        impose a 45/90 enum or a 0..360 range, so values outside either are deliberately accepted.
        """

        values = self._values(
            pad,
            "thermal_bridge_angle",
            locator,
            minimum=1,
            maximum=1,
            required=False,
        )
        if not values:
            return False
        value = values[0]
        if is_quoted_atom(value):
            self.fail(
                "integer.precision",
                "thermal bridge angle must be a bare decimal degree value",
                locator,
                object_kind="pad",
            )
        self._rotation(value, f"{locator}.thermal_bridge_angle")
        return True

    def _require_supported_pad_property(self, pad: SExpr, locator: str) -> bool:
        """Accept a pad fabrication property only in the closed shape and value table below.

        `(property <token>)` is KiCad's `PAD_PROP`: a fabrication-file annotation on one pad. The
        accepted subset is a table, not a sentence, because ADR-0092's prose subset admitted two
        forms it did not mean:

        - exactly one positional atom, and
        - that atom is **bare**, because `format( const PAD* )` prints `(property %s)` from a
          `const char*` table and can emit nothing quoted, and
        - no child expression, and
        - the atom is in `_ACCEPTED_PAD_PROPERTIES`.

        Everything else refuses, including three forms KiCad's own reader tolerates. Its
        `T_property` arm loops `while( token != T_RIGHT )` with the `Expecting(...)` compiled out,
        so it silently accepts `(property)`, accepts an unknown token, and accepts several tokens
        with the *last* one winning. That last one matters here rather than being pedantry: a
        `(property pad_prop_heatsink pad_prop_castellated)` resolves in KiCad to the one value this
        adapter must not discard, so admitting multi-atom forms would have been a way to smuggle a
        castellated pad past an equality test on the first atom.

        **What acceptance discards, and why the discard is safe.** Nothing here is modelled: no
        `Pad` field, no `canonical._pad()` field, no schema bump. The seven accepted tokens reach
        Gerber aperture attributes, drill-file attributes, KiCad's padstack advisories, the
        footprint type hint `FOOTPRINT::GetLikelyAttribute`, board statistics and the property
        manager -- and not one of them reaches a pad's copper, hole, layer span, clearance or
        connectivity. Board IR emits no Gerber, no drill file and no position file, and ADR-0004
        delegates DRC to KiCad, which runs over the original board bytes where the token survives.

        Two consequences are worth naming rather than leaving implied, because both are constraints
        and a reading of "fabrication annotations are inert" has to survive them:

        - `HEATSINK` makes `DRC_TEST_PROVIDER_COURTYARD_CLEARANCE` skip a holed pad's
          PTH/NPTH-in-courtyard test (`drc_test_provider_courtyard_clearance.cpp:324-325`). That is
          a DRC verdict, produced by KiCad from the unmodified file, and courtyards convert here as
          OVER-approximating obstacles either way, so nothing this adapter claims moves.
        - The property is a *named* term in KiCad's rule language -- `A.Fabrication_Property`, and
          KiCad's own rule help ships `(constraint zone_connection solid)` conditioned on
          `'Heatsink pad'`. This is D-184's custom-rule problem again and it takes D-184's answer:
          `.kicad_dru` has never been parsed here (ADR-0005), no expression is evaluated, and the
          one surface that honours a custom rule hands the original bytes to KiCad itself.

        `CASTELLATED` is refused as a **caution and not as a geometry requirement**, and the
        distinction is recorded because an earlier revision of this docstring had the mechanism
        backwards. KiCad's edge exclusion forgives collisions rather than forbidding them, so
        discarding the token leaves this adapter stricter than KiCad, which is the allowed
        direction. What justifies the refusal is that fabrication removes the half-holes from the
        physical board while `Edge.Cuts` keeps them, so the outline claims board that will not
        exist, and KiCad's DRC waives exactly that region -- the backstop is weakest where the
        over-claim is. See the `_REFUSED_PAD_PROPERTY` comment, ADR-0100 and
        docs/research/kicad-pad-fabrication-property-v1.md.

        Returns whether an accepted property was present, so the caller can count it *after* the
        aperture skip while this check still runs before it. Validating and counting at one point
        would either let a stencil opening smuggle an unvalidated token through or make
        `ConversionResult.unmodelled_pad_property_count` report a pad that never converted.
        """

        declarations = children(pad, "property")
        if not declarations:
            return False
        if len(declarations) > 1:
            self.fail(
                "unsupported.construct",
                "pad declares more than one fabrication property",
                locator,
                object_kind="pad",
            )
        declaration = declarations[0]
        # Shape before value, and the child test before `_values`: `atoms()` raises on a nested
        # expression with a byte locator, which would refuse the board without saying which pad.
        payload = declaration.items[1:]
        if len(payload) != 1:
            self.fail(
                "unsupported.construct",
                "pad fabrication property is not a single bare token",
                locator,
                object_kind="pad",
            )
        value = payload[0]
        if not isinstance(value, str) or is_quoted_atom(value):
            self.fail(
                "unsupported.construct",
                "pad fabrication property is not a single bare token",
                locator,
                object_kind="pad",
            )
        if value == _REFUSED_PAD_PROPERTY:
            self.fail(
                "unsupported.construct",
                (
                    f"pad fabrication property {_REFUSED_PAD_PROPERTY!r} removes board area the "
                    "outline still claims and is unsupported"
                ),
                locator,
                object_kind="pad",
            )
        if value not in _ACCEPTED_PAD_PROPERTIES:
            self.fail(
                "unsupported.construct",
                "pad fabrication property is unsupported",
                locator,
                object_kind="pad",
            )
        return True

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
    ) -> tuple[
        tuple[Ring, ...],
        tuple[CourtyardCircle, ...],
        tuple[Ring, ...],
        tuple[CourtyardCircle, ...],
    ]:
        """Import exact closed courtyard centerlines and circles, grouped by courtyard layer.

        KiCad treats every closed shape on a courtyard layer as part of the footprint envelope
        **for the side that layer names**, and never consults the footprint's own side to decide
        it: ``FOOTPRINT::BuildCourtyardCaches`` files each shape by its own ``F_CrtYd`` /
        ``B_CrtYd`` layer into a front or back cache, and the DRC provider compares front against
        front and back against back.  A footprint on ``F.Cu`` may therefore legitimately carry
        ``B.CrtYd`` geometry, and the stock KiCad library ships exactly that for feed-through
        parts: `Connector_Wire:SolderWire-*_Relief` draws its full envelope on ``F.CrtYd`` and the
        strain-relief slot that passes through the board on ``B.CrtYd``.  Measured against real
        ``kicad-cli`` 10.0.5, that back rectangle collides with a *back-side* footprint's back
        courtyard and does not collide with any front courtyard, whichever side either footprint
        sits on (ADR-0097).

        The two layers are therefore returned as two separate sets and are never pooled: they are
        distinct even-odd regions on distinct physical layers.  Returned as
        ``(near_rings, near_circles, far_rings, far_circles)``, where *near* is the layer matching
        ``side``.

        The bounded Board-IR shape subset is unchanged and identical on both layers: unfilled
        rectangles and polygons plus complete ``fp_line`` cycles - with every edge horizontal,
        vertical, or an exact 45-degree chamfer - and unfilled ``fp_circle`` outlines whose radius
        is an exact integer nanometre.  Empty still means that no courtyard was present; a
        malformed or unsupported shape is never silently omitted.
        """

        near_layer = "F.CrtYd" if side is FootprintSide.FRONT else "B.CrtYd"
        far_layer = "B.CrtYd" if side is FootprintSide.FRONT else "F.CrtYd"
        rings: dict[str, list[Ring]] = {near_layer: [], far_layer: []}
        circles: dict[str, list[CourtyardCircle]] = {near_layer: [], far_layer: []}
        line_segments: dict[str, list[tuple[PointNM, PointNM, str]]] = {
            near_layer: [],
            far_layer: [],
        }

        def accepted() -> int:
            return sum(len(value) for value in rings.values()) + sum(
                len(value) for value in circles.values()
            )

        def require_room(locator: str) -> None:
            if accepted() >= 64:
                # A fixed schema ceiling, not an operator budget: the Board IR decoder refuses the
                # very same 64-courtyard rule under `schema.limit`, and the two paths disagreeing
                # about the code for one rule was a defect. Every `budget.exceeded.*` code now
                # names a `ParseLimits` field an operator can actually move; this is not one. It
                # counts both courtyard layers against one ceiling, matching the decoder.
                self.fail(
                    "schema.limit",
                    "footprint courtyard limit exceeded",
                    locator,
                    object_kind="footprint",
                )

        def append(local_points: tuple[PointNM, ...], locator: str, layer: str) -> None:
            require_room(locator)
            try:
                rings[layer].append(
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
                append(
                    (start, PointNM(end.x, start.y), end, PointNM(start.x, end.y)), locator, layer
                )
            elif item.head == "fp_poly":
                append(self._courtyard_polygon_points(item, locator), locator, layer)
            elif item.head == "fp_circle":
                require_room(locator)
                circles[layer].append(
                    self._courtyard_circle(item, locator, origin=origin, turn=turn)
                )
            else:
                line_segments[layer].append(self._courtyard_line_segment(item, locator))

        # Chains are assembled per courtyard layer.  Pooling the segments would let a front edge
        # and a back edge meeting at a shared vertex join into one ring that exists on neither
        # layer, or make a legitimately closed front loop look like a branching chain because a
        # back segment touches it.  The near layer is walked first so the `line_chain[N]` locators
        # of a board whose courtyards all match its footprint sides are unchanged.
        chain_index = 0
        for layer in (near_layer, far_layer):
            for ring in self._closed_courtyard_line_rings(line_segments[layer], footprint_locator):
                append(ring, f"{footprint_locator}.line_chain[{chain_index}]", layer)
                chain_index += 1
        for layer in (near_layer, far_layer):
            self._require_disjoint_courtyard_circles(
                tuple(rings[layer]), tuple(circles[layer]), footprint_locator
            )
        return (
            tuple(rings[near_layer]),
            tuple(circles[near_layer]),
            tuple(rings[far_layer]),
            tuple(circles[far_layer]),
        )

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

    def _pad_kind(self, raw_kind: str, locator: str) -> PadKind:
        """Resolve one pad's KiCad attribute token, or refuse without naming it.

        Kind and shape refuse separately, through this method and `_pad_shape`. One message
        covering both named neither, so a caller reading it could not tell which of the two
        positional tokens was the problem -- and on the one real board that then reached it, the
        answer (a `connect` pad) was recoverable only by reading the file. Same defect class as
        the seven pad refusals D-178 made reachable, in the message rather than in the control
        flow. That board no longer reaches it.

        `connect` is KiCad's `PAD_ATTRIB::CONN`, and it converts as `PadKind.SMD` because that is
        what KiCad's own model says it is, not as a convenience. The claim that matters is
        universal and was established by sweeping *two* literals, `PAD_ATTRIB::CONN` and
        `PAD_ATTRIB::SMD` -- the second because a site testing `== SMD` alone contains no `CONN`
        token and is invisible to a `CONN` grep, which is how the first version of this work
        missed one. **No site anywhere gives a `CONN` pad different copper, a different layer
        span, a different hole, or different connectivity from an `SMD` pad.**
        `connectivity_items.cpp:164-176` puts `SMD`, `CONN` and `NPTH` in one case that pins the
        item to the front of its copper stack; `pns_kicad_iface.cpp:1631-1648` gives `CONN` and
        `SMD` one shared case; `pad.cpp:1626-1641` trims both to at most one copper layer;
        `pad.cpp:2886-2891` and the parser at `…_sexpr_parser.cpp:6433-6437` force the drill to
        zero for both.

        At least ten things *do* differ -- a lower bound, not an enumeration -- and none is
        geometry or connectivity: solder paste (`pad.cpp:3252-3257`), the Gerber aperture
        attribute (`plot_brditems_plotter.cpp:206-227`), pick-and-place "exclude all TH"
        (`footprint.cpp:4451-4460` via `place_file_exporter.cpp:145`), the Edge.Cuts clearance DRC
        exemption (`drc_test_provider_edge_clearance.cpp:431-439`), a distinct property-system
        value user-authored DRC rules can name (`pad.cpp:3665-3671`), and four reporting surfaces.
        Board IR models no paste, emits no Gerber and no position file, evaluates no rule
        expressions, and derives no edge clearance of its own (ADR-0004 delegates DRC to KiCad),
        so every one is outside what a `Pad` claims -- and the outputs that do change are produced
        by KiCad from a file in which the `connect` token survives, because both patch adapters
        are source-preserving splices that never rewrite a pad header.

        The distinction is therefore *discarded*, not preserved. It is counted rather than dropped
        in silence -- see `edge_connector_pad_count` -- but that count is in-process only and
        reaches no published surface (R-141). ADR-0096 and D-186 record why a new `PadKind` member
        was rejected, and what that alternative actually costs.
        """

        if raw_kind not in _PAD_KIND_BY_TOKEN:
            self.fail(
                "unsupported.construct",
                "pad kind is unsupported",
                locator,
                object_kind="pad",
            )
        return _PAD_KIND_BY_TOKEN[raw_kind]

    def _custom_pad_anchor_shape(self, pad: SExpr, locator: str) -> PadShape:
        options = self._one(pad, "options", locator)
        assert options is not None
        self._validate_direct_atoms(
            options, positional_atoms=0, allowed=frozenset(), locator=locator
        )
        self._reject_unknown_children(options, frozenset({"clearance", "anchor"}), locator)
        clearance = self._values(options, "clearance", locator, minimum=1, maximum=1)
        if clearance[0] not in {"outline", "convexhull"}:
            self.fail(
                "unsupported.construct",
                "custom pad zone-clearance option is unsupported",
                locator,
                object_kind="pad",
            )
        anchor = self._values(options, "anchor", locator, minimum=1, maximum=1)
        if anchor == ("rect",):
            return PadShape.RECT
        if anchor == ("circle",):
            return PadShape.CIRCLE
        self.fail(
            "unsupported.construct",
            "custom pad anchor shape is unsupported",
            locator,
            object_kind="pad",
        )

    def _charge_custom_pad_vertices(self, count: int, locator: str) -> None:
        self.custom_pad_primitive_vertex_count += count
        if self.custom_pad_primitive_vertex_count > self.limits.max_total_vertices:
            self.fail(
                ParseBudget.TOTAL_VERTICES.value,
                "total vertex budget exceeded",
                locator,
                object_kind="pad",
            )

    def _charge_graphic_envelope_vertices(self, count: int, locator: str) -> None:
        """Charge stray copper-graphic vertices against the budget custom-pad primitives charge.

        Called from the conversion pass only, never from the preflight, even though both run the
        same reader: the budget covers *accepted* source geometry, and charging twice per polygon
        would halve the ceiling for a reason that is an implementation detail rather than a fact
        about the board.
        """

        self.graphic_envelope_vertex_count += count
        if self.graphic_envelope_vertex_count > self.limits.max_total_vertices:
            self.fail(
                ParseBudget.TOTAL_VERTICES.value,
                "total vertex budget exceeded",
                locator,
                object_kind="graphic",
            )

    def _primitive_point(self, expression: SExpr, head: str, locator: str) -> tuple[int, int]:
        values = self._values(expression, head, locator, minimum=2, maximum=2)
        self._charge_custom_pad_vertices(1, locator)
        return (
            self._mm(values[0], f"{locator}.{head}.x"),
            self._mm(values[1], f"{locator}.{head}.y"),
        )

    def _primitive_points(
        self,
        expression: SExpr,
        locator: str,
        *,
        minimum: int,
        maximum: int | None = None,
        ring_budget: bool = False,
    ) -> tuple[tuple[int, int], ...]:
        points = self._one(expression, "pts", locator)
        assert points is not None
        self._validate_direct_atoms(
            points, positional_atoms=0, allowed=frozenset(), locator=locator
        )
        self._reject_unknown_children(points, frozenset({"xy"}), locator)
        xy_items = children(points, "xy")
        if ring_budget and len(xy_items) > self.limits.max_vertices_per_ring:
            self.fail(
                ParseBudget.VERTICES_PER_RING.value,
                "ring vertex budget exceeded",
                locator,
                object_kind="pad",
            )
        upper = self.limits.max_vertices_per_ring if ring_budget else maximum
        upper = minimum if upper is None else upper
        if not minimum <= len(xy_items) <= upper:
            self.fail("syntax.invalid", "custom pad primitive point count is invalid", locator)
        self._charge_custom_pad_vertices(len(xy_items), locator)
        result: list[tuple[int, int]] = []
        for index, point in enumerate(xy_items):
            try:
                values = atoms(point)
            except SExprError as error:
                self.fail(error.code, error.message, f"byte:{error.offset}")
            if len(values) != 2:
                self.fail("syntax.invalid", "custom pad primitive point is malformed", locator)
            result.append(
                (
                    self._mm(values[0], f"{locator}.pts[{index}].x"),
                    self._mm(values[1], f"{locator}.pts[{index}].y"),
                )
            )
        return tuple(result)

    @staticmethod
    def _ceil_sqrt(value: int) -> int:
        root = isqrt(value)
        return root if root * root == value else root + 1

    @staticmethod
    def _ceil_div(numerator: int, denominator: int) -> int:
        return -((-numerator) // denominator)

    def _primitive_width(self, expression: SExpr, locator: str) -> int:
        value = self._mm(
            self._values(expression, "width", locator, minimum=1, maximum=1)[0],
            f"{locator}.width",
        )
        if value < 0:
            self.fail("syntax.invalid", "custom pad primitive width cannot be negative", locator)
        return value

    def _validate_primitive_fill(self, expression: SExpr, locator: str) -> None:
        fill = self._values(expression, "fill", locator, minimum=1, maximum=1, required=False)
        if fill and fill[0] not in {"yes", "no"}:
            self.fail("syntax.invalid", "custom pad primitive fill is malformed", locator)

    def _primitive_envelope(
        self, primitive: SExpr, locator: str
    ) -> tuple[int, int, int, int] | None:
        head = primitive.head
        if head not in _CUSTOM_PAD_PRIMITIVE_HEADS:
            self.fail(
                "unsupported.construct",
                "custom pad primitive is unsupported",
                locator,
                object_kind="pad",
            )
        self._validate_direct_atoms(
            primitive, positional_atoms=0, allowed=frozenset(), locator=locator
        )
        if head in {"gr_bbox", "gr_vector"}:
            # KiCad marks these as proxy items and both effective-shape builders skip them.
            # They still cross the trust boundary, so validate their closed syntax before
            # discarding them; a proxy must not become a container for arbitrary child heads.
            self._reject_unknown_children(
                primitive, frozenset({"start", "end", "width", "fill"}), locator
            )
            self._primitive_point(primitive, "start", locator)
            self._primitive_point(primitive, "end", locator)
            proxy_width = self._values(
                primitive, "width", locator, minimum=1, maximum=1, required=False
            )
            if proxy_width and self._mm(proxy_width[0], f"{locator}.width") < 0:
                self.fail(
                    "syntax.invalid", "custom pad primitive width cannot be negative", locator
                )
            self._validate_primitive_fill(primitive, locator)
            return None

        common = frozenset({"width", "fill"})
        points: tuple[tuple[int, int], ...]
        if head == "gr_line":
            self._reject_unknown_children(primitive, common | {"start", "end"}, locator)
            points = (
                self._primitive_point(primitive, "start", locator),
                self._primitive_point(primitive, "end", locator),
            )
        elif head == "gr_rect":
            self._reject_unknown_children(primitive, common | {"start", "end"}, locator)
            points = (
                self._primitive_point(primitive, "start", locator),
                self._primitive_point(primitive, "end", locator),
            )
        elif head == "gr_poly":
            self._reject_unknown_children(primitive, common | {"pts"}, locator)
            points = self._primitive_points(primitive, locator, minimum=3, ring_budget=True)
        elif head == "gr_curve":
            self._reject_unknown_children(primitive, common | {"pts"}, locator)
            points = self._primitive_points(primitive, locator, minimum=4, maximum=4)
        elif head == "gr_circle":
            self._reject_unknown_children(primitive, common | {"center", "end"}, locator)
            center = self._primitive_point(primitive, "center", locator)
            end = self._primitive_point(primitive, "end", locator)
            radius = self._ceil_sqrt((end[0] - center[0]) ** 2 + (end[1] - center[1]) ** 2)
            points = (
                (center[0] - radius, center[1] - radius),
                (center[0] + radius, center[1] + radius),
            )
        else:
            assert head == "gr_arc"
            self._reject_unknown_children(primitive, common | {"start", "mid", "end"}, locator)
            start = self._primitive_point(primitive, "start", locator)
            mid = self._primitive_point(primitive, "mid", locator)
            end = self._primitive_point(primitive, "end", locator)
            determinant = 2 * (
                start[0] * (mid[1] - end[1])
                + mid[0] * (end[1] - start[1])
                + end[0] * (start[1] - mid[1])
            )
            if determinant == 0:
                points = (start, mid, end)
            else:
                start_sq = start[0] ** 2 + start[1] ** 2
                mid_sq = mid[0] ** 2 + mid[1] ** 2
                end_sq = end[0] ** 2 + end[1] ** 2
                center_x_num = (
                    start_sq * (mid[1] - end[1])
                    + mid_sq * (end[1] - start[1])
                    + end_sq * (start[1] - mid[1])
                )
                center_y_num = (
                    start_sq * (end[0] - mid[0])
                    + mid_sq * (start[0] - end[0])
                    + end_sq * (mid[0] - start[0])
                )
                if determinant < 0:
                    determinant = -determinant
                    center_x_num = -center_x_num
                    center_y_num = -center_y_num
                dx_num = center_x_num - start[0] * determinant
                dy_num = center_y_num - start[1] * determinant
                radius_num = self._ceil_sqrt(dx_num * dx_num + dy_num * dy_num)
                points = (
                    (
                        (center_x_num - radius_num) // determinant,
                        (center_y_num - radius_num) // determinant,
                    ),
                    (
                        self._ceil_div(center_x_num + radius_num, determinant),
                        self._ceil_div(center_y_num + radius_num, determinant),
                    ),
                )

        width = self._primitive_width(primitive, locator)
        self._validate_primitive_fill(primitive, locator)
        inflation = (width + 1) // 2
        return (
            min(point[0] for point in points) - inflation,
            min(point[1] for point in points) - inflation,
            max(point[0] for point in points) + inflation,
            max(point[1] for point in points) + inflation,
        )

    def _custom_pad_envelope(
        self, pad: SExpr, locator: str, *, size_x_nm: int, size_y_nm: int
    ) -> PadCopperEnvelope:
        primitives = self._one(pad, "primitives", locator)
        assert primitives is not None
        self._validate_direct_atoms(
            primitives, positional_atoms=0, allowed=frozenset(), locator=locator
        )
        self._reject_unknown_children(primitives, _CUSTOM_PAD_PRIMITIVE_HEADS, locator)
        half_x = (size_x_nm + 1) // 2
        half_y = (size_y_nm + 1) // 2
        bounds = (-half_x, -half_y, half_x, half_y)
        for index, primitive in enumerate(
            item for item in primitives.items[1:] if isinstance(item, SExpr)
        ):
            primitive_bounds = self._primitive_envelope(primitive, f"{locator}.primitives[{index}]")
            if primitive_bounds is None:
                continue
            bounds = (
                min(bounds[0], primitive_bounds[0]),
                min(bounds[1], primitive_bounds[1]),
                max(bounds[2], primitive_bounds[2]),
                max(bounds[3], primitive_bounds[3]),
            )
        try:
            return PadCopperEnvelope(*bounds)
        except ValueError as error:
            self.fail("integer.overflow", str(error), locator, object_kind="pad")

    def _pad_shape(self, raw_shape: str, locator: str) -> PadShape:
        """Resolve one pad's KiCad shape token, or refuse -- by name where there is a name.

        A token KiCad's writer emits but Board IR does not model is refused with the sentence
        `_UNMODELLED_PAD_SHAPES` holds for it, so the message names the construct.  A token that
        is in neither table is not a documented pad shape at all: it refuses through the
        `PadShape` lookup below, unnamed and without echoing one byte of the board, with the
        indexed locator still saying which pad.  The named lookup runs first because `PadShape`
        would otherwise reject both cases with the same anonymous sentence, which is exactly the
        defect issue #153 reports one level up.
        """

        named = _UNMODELLED_PAD_SHAPES.get(raw_shape)
        if named is not None:
            self.fail("unsupported.construct", named, locator, object_kind="pad")
        try:
            return PadShape(raw_shape)
        except ValueError:
            self.fail(
                "unsupported.construct",
                "pad shape is unsupported",
                locator,
                object_kind="pad",
            )

    def _footprints_and_pads(
        self,
    ) -> tuple[tuple[Footprint, ...], tuple[Pad, ...], tuple[Segment, ...]]:
        footprints: list[Footprint] = []
        pads: list[Pad] = []
        tie_segments: list[Segment] = []
        copper_segments: list[Segment] = []
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
                # What the pad *is* is decided before what fields it carries, and the ordering is
                # the whole of issue #153's first question.  A `custom` pad must carry `(options
                # (anchor ...))` -- KiCad's writer emits `(options` under `GetShape() ==
                # PAD_SHAPE::CUSTOM` and under no other condition
                # (`pcb_io_kicad_sexpr.cpp:2050-2062`) -- so with the field loop first, every
                # custom pad on a real board was told that `options` is unsupported.  That is a
                # true sentence about the wrong object: `options` is a mandatory sub-field of the
                # shape, and a reader who goes and removes it gets a malformed pad rather than a
                # converting board. Deciding kind and shape first now dispatches the closed custom
                # subgrammar before the general pad-field allowlist.
                #
                # KiCad's parser accepts `options` and `primitives` on any pad shape, so the
                # explicit non-custom branch below keeps those hand-edited forms fail-closed even
                # though both heads belong to the global closed allowlist.
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
                kind = self._pad_kind(raw_kind, locator)
                is_custom = raw_shape == "custom"
                shape = (
                    self._custom_pad_anchor_shape(pad, locator)
                    if is_custom
                    else self._pad_shape(raw_shape, locator)
                )
                if not is_custom and (children(pad, "options") or children(pad, "primitives")):
                    self.fail(
                        "unsupported.construct",
                        "custom pad fields are unsupported on a non-custom pad",
                        locator,
                        object_kind="pad",
                    )
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
                    _SUPPORTED_PAD_FIELDS,
                    locator,
                )
                # Validated before the aperture skip below, not after it: `zone_connect` is on the
                # pad allowlist now, so a skipped pad would otherwise carry it past every check.
                # The value is inert on an aperture, which has no copper for a pour to reach --
                # but skipping a pad must not become a way to smuggle an unvalidated token in.
                self._require_attaching_pad_zone_connection(pad, locator)
                # Like `zone_connect`, this allowlisted value must be validated before the
                # aperture skip.  Its disclosure count is incremented only after that skip, so
                # it describes converted copper pads rather than syntax merely encountered.
                has_thermal_bridge_angle = self._require_valid_thermal_bridge_angle(pad, locator)
                # Validated before the aperture skip for the same reason `zone_connect` is: the
                # head is on the pad allowlist now, so a skipped aperture pad would otherwise
                # carry an unvalidated -- possibly castellated -- token past every check.  It is
                # *counted* after the skip, below, which is why validation and counting are two
                # steps rather than one.
                has_fabrication_property = self._require_supported_pad_property(pad, locator)
                # Custom aperture pads are discarded below, but their primitive geometry still
                # crosses the trust boundary. Validate and budget-charge it before the discard;
                # copper custom pads reuse the same envelope so it is parsed exactly once.
                size_x: int | None = None
                size_y: int | None = None
                copper_envelope: PadCopperEnvelope | None = None
                if is_custom:
                    size = self._values(pad, "size", locator, minimum=2, maximum=2)
                    size_x = self._mm(size[0], f"{locator}.size.x")
                    size_y = self._mm(size[1], f"{locator}.size.y")
                    copper_envelope = self._custom_pad_envelope(
                        pad, locator, size_x_nm=size_x, size_y_nm=size_y
                    )
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
                # Counted, not dropped in silence: ADR-0096 counted the discarded `connect` token
                # and ADR-0094 the discarded root property, and a fabrication annotation is the
                # same kind of loss -- the value is validated and then thrown away, so a caller
                # reading a `Pad` cannot tell the designer marked it a heatsink.  Counted here, at
                # the same point and for the same reason as the line above: after the aperture
                # skip and after every refusal, so it reports copper pads this board actually
                # carries and never a pad that was skipped or refused.  R-141 applies to it too --
                # the count is in-process and reaches no MCP contract, so from a client the
                # discard is still silent.
                if has_fabrication_property:
                    self.unmodelled_pad_property_count += 1
                if has_thermal_bridge_angle:
                    self.unmodelled_thermal_bridge_angle_pad_count += 1
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
                if size_x is None or size_y is None:
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
                    copper_envelope=copper_envelope,
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
            elif not children(footprint, "net_tie_pad_groups"):
                # Stray copper: this footprint declares no net tie, so a filled `fp_poly` of its
                # own on a copper layer is drawing the adapter must bound rather than drop (D-230).
                #
                # The predicate is `net_tie_pad_groups` and **not** `tie_group is None`, and the
                # difference is deliberate even though the two agree today. `_net_tie_pad_group`
                # currently returns `None` only when the head is absent and refuses every other
                # shape, so `else` would be equivalent -- but that is a property of one function's
                # current contract, and if it ever gained a `None` return for a declaration it
                # could not resolve, `else` would hand that footprint's copper to this path while
                # the net-tie path had already declined it. Writing the same test the preflight
                # branch writes keeps the two halves agreeing about which polygons each owns
                # without either depending on the other's internals.
                copper_segments.extend(
                    self._footprint_copper_obstacle_segments(
                        footprint,
                        footprint_locator,
                        origin=origin,
                        turn=turn,
                        footprint_locked=footprint_locked,
                    )
                )
            (
                courtyards,
                courtyard_circles,
                far_side_courtyards,
                far_side_courtyard_circles,
            ) = self._courtyards(
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
                    far_side_courtyards=far_side_courtyards,
                    far_side_courtyard_circles=far_side_courtyard_circles,
                )
            )
        return tuple(footprints), tuple(pads), tuple(tie_segments) + tuple(copper_segments)

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

    def _read_footprint_copper_polygon(
        self, item: SExpr, layer_name: str, locator: str
    ) -> tuple[Layer, tuple[PointNM, ...], int]:
        """Validate one stray copper `fp_poly` and return its layer, ring and stroke half width.

        One grammar with two callers. `_semantic_preflight` calls it and discards the result, so a
        polygon this reader cannot bound refuses **in the preflight walk**, keeping its position
        among that walk's other diagnostics; `_footprint_copper_obstacle_segments` calls it again
        and builds the obstacle. Splitting the rules across the two would be two grammars that
        agreed until someone edited one.

        Every refusal names what it refused, and each is the conservative direction rather than a
        taste:

        - **One declared copper layer.** `*.Cu` and `F&B.Cu` pass `_is_routing_layer` and name more
          than one layer, while a Board IR `Segment` names exactly one. Neither appears in
          `layer_by_name`, which lists only the layers the document declares, so the same lookup
          refuses the wildcards and an undeclared name together. B-136 measured `multi_copper` at
          **0 of 56**.
        - **Filled.** An unfilled polygon's copper is the stroked outline only, and while the same
          envelope would contain it, accepting it would be modelling a form B-136 measured at
          **0 of 56** -- the rule this adapter applies to the courtyard `fp_arc` and to every
          net-tie primitive that is not a polygon.
        - **An explicit stroke.** Required, never defaulted, for the reason
          `_net_tie_copper_segments` records one class up: an omitted field is not an explicitly
          zero one, and reading a
          missing `stroke` as zero would let any non-zero writer default put real copper outside
          the modelled envelope. That is the one direction the obstacle invariant forbids. B-136
          measured `stroke_absent` at **0 of 56** and every occurrence carrying an explicit
          *non-zero* width, which is why this reader returns a half width instead of demanding
          `(width 0)` as the net-tie path does.
        - **Straight sides only.** KiCad 9 writes `(arc …)` inside a `pts` list for a curved
          polygon side. A curved side bulges *outside* the hull of the listed vertices, so a
          vertex-derived envelope would not contain it -- the one failure mode that would make this
          whole conversion unsound. `_reject_unknown_children` refuses it by name. B-136 measured
          `pts_with_curved_child` at **0 of 56**.
        - **Three distinct vertices.** Fewer bounds no region. The repeated closing vertex KiCad
          writes is dropped first, exactly as the net-tie reader drops it.

        What it deliberately does **not** check is that the ring is simple, or that its vertices are
        distinct from one another beyond the count. It cannot: B-136 measured **26 of 56
        self-intersecting** and **0 of 56 with an all-distinct vertex ring**, so a `Ring` would
        reject every one of these polygons outright and a self-intersecting outline's filled area
        is not determined by the document at all -- it depends on a fill rule the source never
        names. The envelope this reader feeds is correct under *every* fill rule, which is the
        property that makes those two measurements a reason to bound rather than a reason to refuse.
        """

        self._reject_unknown_children(item, _FOOTPRINT_POLYGON_CHILDREN, locator)
        self._validate_direct_atoms(
            item, positional_atoms=0, allowed=frozenset({"locked"}), locator=locator
        )
        layer = self.layer_by_name.get(layer_name)
        if layer is None:
            self.fail(
                "unknown.layer",
                "footprint copper polygon must name one declared copper layer",
                locator,
                object_kind="graphic",
            )
        # `required=False` and then a named refusal, rather than letting `_values` raise
        # `syntax.missing_field`: an absent `fill` is a real form a hand-edited board can carry,
        # and answering it with a field-less sentence is precisely the defect ADR-0123 names.
        fill = self._values(item, "fill", locator, minimum=1, maximum=1, required=False)
        if fill != ("yes",) or is_quoted_atom(fill[0]):
            self.fail(
                "unsupported.construct",
                "footprint copper polygon must be filled",
                locator,
                object_kind="graphic",
            )
        stroke = self._one(item, "stroke", locator, required=False)
        if stroke is None:
            self.fail(
                "unsupported.construct",
                "footprint copper polygon must declare its outline stroke",
                locator,
                object_kind="graphic",
            )
        self._reject_unknown_children(stroke, frozenset({"type", "width"}), f"{locator}.stroke")
        stroke_width = self._values(stroke, "width", f"{locator}.stroke", minimum=1, maximum=1)
        width_nm = self._mm(stroke_width[0], f"{locator}.stroke.width")
        if width_nm < 0:
            self.fail(
                "unsupported.construct",
                "footprint copper polygon stroke width must not be negative",
                f"{locator}.stroke.width",
                object_kind="graphic",
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
        local_points: list[PointNM] = []
        for index, point in enumerate(point_expressions):
            values = atoms(point)
            if len(values) != 2:
                self.fail(
                    "syntax.invalid",
                    "footprint copper polygon point is malformed",
                    f"{locator}.point[{index}]",
                )
            local_points.append(
                PointNM(
                    self._mm(values[0], f"{locator}.point[{index}].x"),
                    self._mm(values[1], f"{locator}.point[{index}].y"),
                )
            )
        if len(local_points) >= 2 and local_points[0] == local_points[-1]:
            local_points.pop()
        if len(set(local_points)) < 3:
            self.fail(
                "unsupported.construct",
                "footprint copper polygon must carry three distinct vertices",
                locator,
                object_kind="graphic",
            )
        # `(width_nm + 1) // 2` and never `width_nm // 2`: KiCad centres a stroke on its path, so
        # half the width lies outside the outline, and a floored half would leave up to a
        # nanometre of real copper outside the envelope on an odd width.
        return layer, tuple(local_points), (width_nm + 1) // 2

    def _footprint_copper_obstacle_segments(
        self,
        footprint: SExpr,
        footprint_locator: str,
        *,
        origin: PointNM,
        turn: int,
        footprint_locked: bool,
    ) -> tuple[Segment, ...]:
        """Bound a footprint's stray copper polygons as netless obstacle segments.

        A filled `fp_poly` on a copper layer *is copper*, so the one outcome forbidden here is
        dropping it. It asks the same three questions net-tie copper does, and the
        direction-of-error rules answer them separately (ADR-0078, ADR-0092, and D-230 here):

        - **Obstacle: over-approximate.** The copper is real for every net, so it becomes a
          full-width `Segment` across the polygon's board-coordinate bounding box, inflated by the
          stroke half width. The containment argument is below and is a proof, not an estimate.
        - **Connectivity: no claim.** `net_id` is `None`, the contract net-0 copper has under
          ADR-0078: nothing is claimed to connect through this copper, which is the required
          direction for a connectivity claim that could not be test-bound.
        - **Identity: derived.** An `fp_poly` is not a KiCad track, so its `uuid` names no
          `Segment`. The identity is revision-derived on purpose, and both source-preserving patch
          paths -- `kicad_route_patch` and `kicad_placement_patch` -- refuse a snapshot containing
          one. A board carrying stray copper therefore converts and routes but **cannot be written
          back**, which is the same contract net-tie copper has (ADR-0026) and is stated here
          rather than discovered later.

        **Why the bounding box and not the polygon.** Not for convenience. B-136 measured **0 of
        56** of these polygons with an all-distinct vertex ring and **26 of 56 self-intersecting**,
        so a Board IR `Ring` -- which rejects a repeated vertex -- cannot represent a single one of
        them, and a self-intersecting outline's filled area is not determined by the source: it
        depends on a fill rule the document never names. The bounding box is the model that is
        correct under *every* fill rule, and that is a stronger property than a tighter model that
        had to guess one. `_net_tie_copper_segments` already relies on the same reasoning for its
        rectangles, where "the four distinct corners admit reorderings whose even-odd fill is a
        subset of the rectangle"; this is that argument with the rectangle replaced by the box,
        which needs no rectangle test at all. The cost was measured rather than assumed: every one
        of the 56 envelopes covers under 5% of its board's `Edge.Cuts` bounding box, and so does
        each board's whole envelope union.

        **Containment, in full.** Write `P` for the polygon's transformed vertices, `B` for their
        axis-aligned bounding box, `W` for the stroke width and `h = (W + 1) // 2 >= W / 2`.

        1. The filled region lies in `conv(P)` under **any** fill rule: the outline is a closed
           polyline through `P`, so it lies in the convex hull of `P`, and the region a closed
           curve encloses lies inside any convex set containing the curve.
        2. The stroke lies in the outline path dilated by `W / 2`. KiCad centres a stroke on its
           path: `STROKE_PARAMS::GetWidth()` is the full width and
           `PCB_SHAPE::TransformShapeToPolygon` inflates a polygon shape by half of it
           (`SHAPE_POLY_SET::Inflate( GetWidth() / 2 )`)
           before adding it to the layer, so no drawn copper is further than `W / 2` outside the
           outline. Steps 1 and 2 give: all copper lies within `h` of `conv(P)`.
        3. `conv(P) ⊆ B`, so all copper lies in `E = [x0 - h, x1 + h] x [y0 - h, y1 + h]`, where
           `B = [x0, x1] x [y0, y1]`.
        4. `E` lies inside the emitted segment's modelled extent. Take the long axis of `E` as the
           segment axis -- say `X1 - X0 >= Y1 - Y0` -- and emit endpoints `(X0, m)`, `(X1, m)` with
           `m = (Y0 + Y1) // 2` and width `2 * wh`, `wh = (Y1 - Y0 + 1) // 2`. Every consumer
           models an orthogonal segment as its endpoint bounding box grown by `(width + 1) // 2`,
           which is exactly `wh`, on all four sides: `routing.astar._segment_extent`,
           `routing.layered_board_adapter._segment_bounds` and the axis-aligned branch of
           `_swept_square_envelope` all compute that same rectangle. So the modelled extent is
           `[X0 - wh, X1 + wh] x [m - wh, m + wh]`. It contains `E`'s x-range because `wh >= 0`,
           and it contains `E`'s y-range because `m <= (Y0 + Y1) / 2` and `wh >= (Y1 - Y0) / 2`
           give `m - wh <= Y0`, while `m >= (Y0 + Y1 - 1) / 2` gives `m + wh >= Y1 - 1/2` and both
           sides are integers.

        The envelope is therefore a **superset** of the real copper, so the model can only refuse a
        route, never permit one through the polygon.

        **The emitted segment is always axis-aligned**, whatever the footprint's rotation, because
        the box is taken in board coordinates after `_transform` and non-quarter-turn rotations
        refuse earlier in `_quarter_turn`. That is load-bearing rather than incidental:
        `layered_board_adapter._segment_bounds` returns `None` for a diagonal foreign segment and
        the layered router answers `diagonal foreign segments are not modeled`, so an envelope that
        could come out diagonal would have traded one refusal for another.

        **No parity surface reads these.** The placement legalizer's four verdicts -- `pad_overlap`,
        `outline_containment`, `keepout_respect`, `courtyard_overlap` -- read `pads`, `outline` and
        `keepouts`, and never `segments` or `arcs`. An over-approximated obstacle therefore cannot
        turn a `proven_clear` into a `violated`, which is the ADR-0075/ADR-0080 rule this change has
        to satisfy and the reason a `Segment` is admissible here where a synthetic `Keepout` --
        which `keepout_respect` *does* read -- would not be, quite apart from `Ring` rejecting all
        56 outright.
        """

        segments: list[Segment] = []
        copper_index = 0
        for item in footprint.items[1:]:
            if not isinstance(item, SExpr) or item.head != "fp_poly":
                continue
            probe_locator = f"{footprint_locator}.copper_graphic[{copper_index}]"
            layer_values = self._values(
                item, "layer", probe_locator, minimum=1, maximum=1, required=False
            )
            if not layer_values:
                continue
            layer_name = layer_values[0]
            if not self._is_routing_layer(layer_name) or layer_name == "Edge.Cuts":
                continue
            locator = probe_locator
            copper_index += 1
            layer, local_points, half_width_nm = self._read_footprint_copper_polygon(
                item, layer_name, locator
            )
            self._charge_graphic_envelope_vertices(len(local_points), locator)
            board_points = tuple(
                self._transform(point, origin, turn, locator) for point in local_points
            )
            x_min = min(point.x for point in board_points) - half_width_nm
            x_max = max(point.x for point in board_points) + half_width_nm
            y_min = min(point.y for point in board_points) - half_width_nm
            y_max = max(point.y for point in board_points) + half_width_nm
            if min(x_max - x_min, y_max - y_min) <= 0:
                # Collinear vertices with a zero stroke draw copper of no area at all. There is no
                # positive-width segment to emit and nothing to bound, so it refuses rather than
                # being silently dropped -- the only outcome this whole path forbids.
                self.fail(
                    "unsupported.construct",
                    "footprint copper polygon encloses no area to bound",
                    locator,
                    object_kind="graphic",
                )
            if x_max - x_min >= y_max - y_min:
                half_span = (y_max - y_min + 1) // 2
                midline = (y_min + y_max) // 2
                start = PointNM(x_min, midline)
                end = PointNM(x_max, midline)
            else:
                half_span = (x_max - x_min + 1) // 2
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
                    width_nm=2 * half_span,
                    locked=footprint_locked or self._locked(item),
                )
            )
        self.footprint_copper_graphic_envelope_count += len(segments)
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

    def _edge_cuts_edges(self) -> list[_OutlineEdge]:
        """Read every root ``gr_line`` and ``gr_arc`` drawn on ``Edge.Cuts`` as one typed edge.

        The two heads are read together because the outline is a *cycle of edges*, and reading
        them apart is what made the arc look like a different kind of object rather than the same
        object with one more control point.  Each head keeps its own closed payload grammar, so an
        arc carrying a field a segment may not carry is still refused by name.
        """

        edges: list[_OutlineEdge] = []
        heads = (("gr_line", _EDGE_CUTS_LINE_FIELDS), ("gr_arc", _EDGE_CUTS_ARC_FIELDS))
        for head, allowed in heads:
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
                if layer_values != ("Edge.Cuts",):
                    continue
                self._reject_unknown_children(expression, allowed, locator)
                self._validate_direct_atoms(
                    expression,
                    positional_atoms=0,
                    allowed=frozenset({"locked"}),
                    locator=locator,
                )
                start = self._point(expression, "start", locator)
                end = self._point(expression, "end", locator)
                mid = self._point(expression, "mid", locator) if head == "gr_arc" else None
                if start == end or (mid is not None and (mid == start or mid == end)):
                    # KiCad's own outline checker reports "segment has null or very small length"
                    # for exactly this, and a zero-length edge has no direction to chain along.
                    self.fail(
                        "geometry.invalid",
                        "Edge.Cuts outline carries a zero-length segment",
                        locator,
                        object_kind="outline",
                    )
                if mid is not None and chord_side(start, end, mid) == 0:
                    # Three collinear points name no circle.  KiCad writes this for a "null arc"
                    # and there is no curve here to approximate in either direction.
                    self.fail(
                        "geometry.invalid",
                        "Edge.Cuts outline arc is degenerate",
                        locator,
                        object_kind="outline",
                    )
                edges.append(
                    _OutlineEdge(
                        start=start,
                        end=end,
                        mid=mid,
                        locator=locator,
                        expression=expression,
                    )
                )
        return edges

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

    def _edge_cuts_edge_ring(self, edges: list[_OutlineEdge]) -> tuple[Ring, int]:
        """Assemble unordered ``Edge.Cuts`` edges into one closed ring, and bound its shrinkage.

        The board outline is routing *room*, not an obstacle, so the direction of error here is
        the opposite of the one an obstacle envelope takes: the modelled contour must be
        **contained within** the outline the board draws, never larger, or the router is handed
        copper the fabricated board does not have.  Straight segments make that containment
        exact - every ring vertex is a drawn endpoint and nothing is synthesized - and the only
        way to break it is to *repair* the input, so this method never does.

        Endpoints must coincide exactly.  KiCad chains its own outline with a non-zero epsilon
        (``ConvertOutlineToPolygon``'s ``aChainingEpsilon``) and will close a sub-tolerance gap
        for you; closing a gap adds area no drawn segment encloses, so a near-miss is refused
        here instead.  That refusal is not hypothetical: two of the ten public boards in B-134's
        cohort miss by **17 nm** and **19 nm**, and one misses by 69.6 mm.  Duplicate edges, a
        vertex of degree other than two, a second disjoint loop, and anything past the ring budget
        are refusals for the same reason: each has more than one plausible repair and every repair
        invents board.

        **An arc is chained by its endpoints and only then judged.**  The cycle is built from the
        chords, because the chord and the arc share their endpoints and topology cannot depend on
        the bend.  The chord ring's own orientation is then what says which side of each chord the
        board is on, which is the only way to know whether an arc bulges out of the board (safe to
        inscribe) or bites into it (not).  That ring is therefore checked for self-intersection
        *before* it is trusted for the verdict: a non-simple chord ring has no consistent interior,
        and reading a direction out of one could classify a concave arc as convex — which is
        precisely the over-approximation this whole path exists to prevent.

        Returns the ring and an upper bound, in nanometres, on how far inside the drawn boundary
        the modelled one runs.  Zero for a board drawn entirely with segments.
        """

        if len(edges) > self.limits.max_vertices_per_ring:
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
        for index, edge in enumerate(edges):
            key = (edge.start, edge.end) if edge.start < edge.end else (edge.end, edge.start)
            if key in seen:
                # Two shapes on the same pair of endpoints is how a circle drawn as two arcs, or a
                # lens, reaches here.  Both are a second contour's worth of ambiguity rather than
                # one ring, and the chord cycle they produce repeats an edge, so they stay refused.
                self.fail(
                    "geometry.invalid",
                    "Edge.Cuts outline has a duplicate segment",
                    edge.locator,
                    object_kind="outline",
                )
            seen.add(key)
            adjacency.setdefault(edge.start, []).append((index, edge.end))
            adjacency.setdefault(edge.end, []).append((index, edge.start))
        for links in adjacency.values():
            if len(links) != 2:
                self.fail(
                    "geometry.invalid",
                    "Edge.Cuts outline must be one closed non-branching loop",
                    "kicad_pcb",
                    object_kind="outline",
                )

        origin = min(adjacency)
        ordered: list[tuple[int, PointNM, PointNM]] = []
        current, previous_edge = origin, -1
        while True:
            choices = sorted(
                (edge, other) for edge, other in adjacency[current] if edge != previous_edge
            )
            next_edge, following = choices[0]
            ordered.append((next_edge, current, following))
            previous_edge, current = next_edge, following
            if current == origin:
                break
        if len(ordered) != len(edges):
            self.fail(
                "unsupported.topology",
                "multiple disjoint Edge.Cuts loops are unsupported",
                "kicad_pcb",
                object_kind="outline",
            )
        # Two edges cannot close a loop without repeating one, which the duplicate check above
        # already refuses, so ``Ring`` sees at least three points here.  Its own contract - three
        # distinct vertices, no repeated closing point, non-zero area - is what rejects a
        # degenerate all-collinear cycle, and ``validate_content`` is what rejects a ring that
        # crosses itself away from a shared vertex.
        chords = Ring(tuple(tail for _index, tail, _head in ordered))
        if not any(edges[index].mid is not None for index, _tail, _head in ordered):
            return chords, 0
        self._require_simple_ring(chords, "kicad_pcb.edge_cuts")
        # Positive doubled area means the cycle runs counter-clockwise in this coordinate frame,
        # and the interior of a counter-clockwise ring is on the left of travel.  ``chord_side``
        # returns +1 for a point on that same left, so an arc whose ``mid`` matches the interior's
        # sign is bending *into* the board.
        interior_side = 1 if signed_double_area(chords.points) > 0 else -1
        points: list[PointNM] = []
        deviation = 0
        for index, tail, head in ordered:
            points.append(tail)
            edge = edges[index]
            if edge.mid is None:
                continue
            if not arc_is_minor(edge.start, edge.mid, edge.end):
                # A major arc's chord is a poor stand-in for its own edge, and worse, the chord
                # ring whose orientation decides the verdict above is no longer a small
                # perturbation of the true region when one edge doubles back past the centre.
                # Refused by name rather than approximated on an argument that stops holding.
                # Zero of the 51 arcs on the seven closing boards in B-134's cohort are major.
                self.fail(
                    "unsupported.construct",
                    "Edge.Cuts outline major arcs are unsupported",
                    edge.locator,
                    object_kind="outline",
                )
            if chord_side(tail, head, edge.mid) == interior_side:
                # A concave cut.  Its safe polyline runs *outside* the circle, in a region that is
                # not convex, so it needs an exact per-edge distance test rather than the two
                # per-vertex ones this path is built on.  Refused by name: an inscribed chord here
                # would hand back material the cut removed, which is the one error the outline
                # may never make.  See ADR-0124 for the exit condition.
                self.fail(
                    "unsupported.construct",
                    "Edge.Cuts outline arcs cutting into the board are unsupported",
                    edge.locator,
                    object_kind="outline",
                )
            try:
                inscription = inscribe_outline_arc(
                    tail,
                    edge.mid,
                    head,
                    max_points=max(0, self.limits.max_vertices_per_ring - len(points) - 1),
                )
            except OutlineArcError:
                self.fail(
                    "geometry.invalid",
                    "Edge.Cuts outline arc is degenerate",
                    edge.locator,
                    object_kind="outline",
                )
            points.extend(inscription.points)
            deviation = max(deviation, inscription.inward_deviation_nm)
        return Ring(tuple(points)), deviation

    def _require_simple_ring(self, ring: Ring, locator: str) -> None:
        """Refuse a ring that crosses itself, in the adapter's own typed vocabulary."""

        try:
            validate_ring_topology(ring, locator=locator, limits=self.limits)
        except BoardIRValidationError as error:
            self.fail(error.code, error.message, locator, object_kind="outline")

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
        edges = self._edge_cuts_edges()
        if edges:
            ring, deviation = self._edge_cuts_edge_ring(edges)
            self.outline_inward_deviation_nm = deviation
            contours.append(
                OutlineContour(
                    id=self._assembled_contour_identity(
                        [edge.expression for edge in edges],
                        "kicad_pcb.edge_cuts",
                    ),
                    outer=ring,
                )
            )
        for head in _EDGE_CUTS_REFUSED_OUTLINE_HEADS:
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
                    # Unreachable through ``convert``: ``_semantic_preflight`` refuses these four
                    # heads before any geometry is read, and with the same sentence.  Kept because
                    # ``_outline`` is the method that owns the outline's closed shape set, and a
                    # future caller reaching it directly must not find an open container here.
                    self.fail(
                        "unsupported.construct",
                        _EDGE_CUTS_REFUSED_OUTLINE_HEADS[head],
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
        footprints, pads, footprint_segments = self._footprints_and_pads()
        source_info = SourceInfo(
            format="kicad_pcb",
            revision=self.source_revision,
            format_version=self.version,
            generator=generator_values[0] if generator_values else None,
        )
        outline = self._outline()
        vias = self._vias()
        # Net-tie copper and bounded stray copper both join the segment collection here rather
        # than in `_segments`, which reads only root `segment` expressions; canonicalization
        # orders by ID, so placement in this tuple carries no meaning.
        segments = self._segments() + footprint_segments
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
            unmodelled_group_count=self.group_count,
            edge_connector_pad_count=self.edge_connector_pad_count,
            unmodelled_board_property_count=self.root_board_property_count,
            unmodelled_pad_property_count=self.unmodelled_pad_property_count,
            unmodelled_thermal_bridge_angle_pad_count=(
                self.unmodelled_thermal_bridge_angle_pad_count
            ),
            unmodelled_setup_field_count=self.unmodelled_setup_field_count,
            unmodelled_footprint_field_count=self.unmodelled_footprint_field_count,
            unmodelled_stackup_layer_count=self.unmodelled_stackup_layer_count,
            outline_inward_deviation_nm=self.outline_inward_deviation_nm,
            footprint_copper_graphic_envelope_count=(self.footprint_copper_graphic_envelope_count),
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
        converter = _Converter(source, root, profile, limits)
        conversion = converter.convert()
        assert conversion.snapshot is not None
        remaining_vertices = (
            limits.max_total_vertices
            - converter.custom_pad_primitive_vertex_count
            - converter.graphic_envelope_vertex_count
        )
        if remaining_vertices < 1:
            raise BoardIRValidationError(
                ParseBudget.TOTAL_VERTICES.value, "total vertex budget exceeded"
            )
        validate_content(
            conversion.snapshot.content,
            replace(limits, max_total_vertices=remaining_vertices),
        )
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
