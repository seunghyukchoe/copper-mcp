"""Bounded, structured conversion diagnostics for Board IR adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from copper_mcp.board_ir.types import BoardIRSnapshot


class Severity(StrEnum):
    """Stable diagnostic severity values."""

    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One machine-readable adapter diagnostic without source-board disclosure."""

    code: str
    severity: Severity
    message: str
    source_locator: str
    object_kind: str | None = None
    object_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not 1 <= len(self.code) <= 96:
            raise ValueError("diagnostic code is malformed")
        if not isinstance(self.severity, Severity):
            raise ValueError("diagnostic severity is malformed")
        if not isinstance(self.message, str) or not 1 <= len(self.message) <= 512:
            raise ValueError("diagnostic message is malformed")
        if not isinstance(self.source_locator, str) or not 1 <= len(self.source_locator) <= 256:
            raise ValueError("diagnostic source locator is malformed")
        for name, value in (("object_kind", self.object_kind), ("object_id", self.object_id)):
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 192):
                raise ValueError(f"diagnostic {name} is malformed")


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Fail-closed adapter result: any error suppresses the snapshot.

    ``max_roundrect_rounding_nm`` is the largest number of nanometres any single roundrect
    corner radius was rounded *up* by, and is zero for a board whose radii are already exact
    nanometres.  It is deliberately **not** a diagnostic: every caller of ``parse_kicad_bytes``
    treats a non-empty ``diagnostics`` tuple as a refusal, so reporting a rounding this way
    would refuse the board it exists to admit.  It is the same measured-rather-than-asserted
    quantity the SimpleRouteJson importer reports as ``max_outward_rounding_nm``: a caller that
    needs bit-exact pad geometry can read it and decline, instead of being told nothing.

    ``unmodelled_group_count`` counts the ``(group ...)`` expressions the KiCad adapter accepted
    and did not model, at board root **and inside a footprint**.  D-228 widened it to the second
    case rather than adding a counter beside it: KiCad dispatches a footprint's group to the *same*
    ``parseGROUP`` and writes it through the same formatter, so the two are one construct, and a
    caller asking "how many groupings did I lose" wants one number.  Two would answer neither.  The
    widening is deployer-visible and carries a migration note.
    An *unlocked* group is editor organisation with no geometry, no
    layer and no net, so it is read past rather than refused -- but Board IR has no field for
    "these objects belong together", so a caller that moves one member breaks a grouping nothing
    told it about.  It is a count for the same reason the rounding above is: a diagnostic would
    refuse the board.  A *locked* group is refused instead, because KiCad derives every member's
    lock from it, so this count never includes one.  Zero on every currently-saved board in the
    surveyed corpus: the only groups found there today are in two ``pcbnew`` backup files, though
    B-096 measured the same tree a day earlier when one board's live save still carried three.
    See ADR-0090 and R-134.

    ``edge_connector_pad_count`` counts the pads whose source token was ``connect`` -- KiCad's
    ``PAD_ATTRIB::CONN``, the edge-connector finger -- and which converted as ``PadKind.SMD``.
    KiCad's own model makes the two the same pad geometrically and electrically, and the three
    things that differ (no solder paste, a distinct Gerber aperture attribute, and an exemption
    from the Edge.Cuts clearance DRC test) are all outside what a Board IR ``Pad`` claims, so the
    conversion loses nothing Board IR was modelling. What it does lose is the *token*: a caller
    reading ``kind == "smd"`` cannot tell that the designer wrote ``connect``, and a caller that
    generates fabrication output, or that reasons about copper running to the board edge, needs
    to know. This count is the disclosure. It is a count and not a diagnostic for the same reason
    the two above are -- every caller of ``parse_kicad_bytes`` treats a non-empty ``diagnostics``
    tuple as a refusal -- and it counts converted pads only, so an aperture-skipped or refused pad
    never appears in it. See ADR-0096 and R-141.

    ``unmodelled_board_property_count`` counts the root ``(property "<key>" "<value>")``
    expressions the KiCad adapter accepted and did not model.  A root board property is one entry
    of ``BOARD::m_properties``, the board's text-variable map: two strings, read only by
    ``BOARD::ResolveTextVar`` to substitute ``${KEY}``.  Every terminus of that substitution which
    could become board content -- copper text, a barcode pattern, a custom DRC rule -- is already
    refused by or already outside this adapter, so nothing a property could reach is modelled here
    and the pair is inert on the read side.  What is lost is the map itself: a caller that rebuilt
    a board from a snapshot alone would drop it, and text rendered from Board IR would leave
    ``${KEY}`` unexpanded.  It counts *expressions*, not KiCad map entries -- ``std::map::insert``
    silently keeps the first value for a repeated key, so a document with a duplicate has more
    expressions than entries.  It is a count for the same reason the three fields above are: a
    diagnostic would refuse the board.  See ADR-0094 and R-139.

    ``unmodelled_pad_property_count`` counts the pad ``(property <token>)`` expressions the KiCad
    adapter accepted and did not model -- KiCad's ``PAD_PROP`` fabrication annotation, in the seven
    of its eight writable values that convert.  None of the seven changes a pad's copper, hole,
    layer span, clearance or connectivity; they reach Gerber aperture attributes, drill-file
    attributes, KiCad's padstack advisories, its footprint type hint and its board statistics, all
    of which KiCad itself produces from the unmodified file.  What is lost is the *token*: a caller
    reading a ``Pad`` cannot tell that the designer marked it a heatsink, a test point or a BGA
    ball, and one generating fabrication output from a snapshot alone would emit the wrong aperture
    attribute.  This count is the disclosure, following ``edge_connector_pad_count`` exactly.  It
    counts expressions on **converted** pads only, so an aperture-skipped pad never appears in it,
    and the eighth value, ``pad_prop_castellated``, never does either -- it refuses the board.  It
    is a count and not a diagnostic for the same reason the four above are.  See ADR-0099 and
    R-144.

    ``unmodelled_thermal_bridge_angle_pad_count`` counts converted copper pads carrying a
    validated ``(thermal_bridge_angle <degrees>)`` override.  KiCad uses the value only while
    deriving thermal-relief spokes: it does not change the pad envelope or zone outline, and an
    exact-fill route consumes KiCad's saved, freshness-verified fill polygons with the angle
    already applied.  The value is therefore accepted without adding a lossy Board IR field, but
    a snapshot alone cannot reproduce those spokes.  This count is the typed disclosure of that
    non-claim.  Aperture-skipped and refused pads never contribute.  See D-205 and R-158.

    ``unmodelled_setup_field_count`` counts the ``(setup …)`` children D-227 admits without
    modelling: ``stackup``, ``grid_origin``, ``aux_axis_origin``, ``pad_to_paste_clearance`` and
    ``pad_to_paste_clearance_ratio``.  None of the five constrains copper geometry or electrical
    clearance -- the two origins are reporting and editor anchors that move no board object, and
    the two paste clearances adjust stencil apertures on ``F.Paste``/``B.Paste`` exactly as the
    already-accepted mask pair adjusts soldermask.  What a snapshot alone therefore cannot
    reproduce is a board's drill-and-place origin and its stencil defaults, and this count is the
    typed disclosure of that.  It counts expressions and only the five heads this decision
    admits, never the setup heads accepted before it.  See D-227 and R-178.

    ``unmodelled_footprint_field_count`` counts the ``(footprint …)`` children D-228 admits without
    modelling: ``sheetfile``, ``sheetname``, ``solder_mask_margin``, ``solder_paste_margin`` and
    both spellings of the paste ratio.  None constrains copper geometry or electrical clearance --
    the two provenance strings have no geometric read site in KiCad at all, and the four margins
    move solder-mask and solder-paste apertures only, because ``FOOTPRINT::TransformPadsToPolySet``
    adds them under the mask and paste layer arms and lets every copper layer fall through
    ``default:`` with no adjustment.  What a snapshot alone therefore cannot reproduce is a
    footprint's schematic origin and its per-footprint stencil defaults, and this count is the typed
    disclosure of that.  It counts expressions across every footprint on the board, and only the
    heads this decision admits.  ``group`` is deliberately *not* counted here: a footprint-local
    group is the same construct as a root one and is counted by ``unmodelled_group_count``.
    ``zone_connect`` is not counted either -- it is validated to be inert for the attachment claim
    Board IR publishes rather than merely discarded, which ADR-0091's reasoning already carries.
    See D-228 and R-179.

    ``unmodelled_stackup_layer_count`` counts the ``(layer …)`` entries inside an accepted
    ``(setup (stackup …))``, dielectric entries included.  It is separate from the field count
    above because it answers a separate question: that count says a stack was dropped, this one
    says how large it was, so a caller can compare it against ``len(copper_layers)`` and see the
    physical entries -- thicknesses, materials, dielectric constants, loss tangents -- it never
    received.  The stackup's three edge attributes are *not* counted here: ``castellated_pads``,
    ``edge_connector`` and ``edge_plating`` each assert plated or removed material that nothing
    else in the document represents, so a non-neutral value refuses the board rather than
    contributing to a number.  See D-227, ADR-0122 and R-178.

    ``footprint_copper_graphic_envelope_count`` counts the stray copper ``fp_poly`` expressions
    D-230 converts as a conservative bounding envelope rather than as exact copper.  Unlike every
    count above it this one does **not** disclose an erasure: the geometry is modelled, and the
    emitted ``Segment`` is a proven superset of the drawn shape, so it is the typed disclosure of
    an *approximation* -- the footing ``max_roundrect_rounding_nm`` is already on.  What a caller
    learns from a non-zero value is that this many obstacles are looser than the copper they stand
    for, which matters because an over-approximated obstacle can make a routable board report
    unroutable.  Net-tie copper never contributes: it takes the pre-existing ADR-0092 path and is
    counted nowhere.  Neither does a polygon on a documentation layer, which is read past as
    silkscreen always was.  See D-230, ADR-0125 and R-181.

    **Adding another measured field is one line here and one line in**
    ``board_ir_service._MEASURED_COUNT_FIELDS``, **and the test will tell you.**  Every measured
    field on this dataclass is published to MCP clients through ``BoardIrSummary``'s single
    ``unmodelled_counts`` map, and ``test_board_ir_service`` reflects over these fields to assert
    the map covers all of them -- so a counter added here and not mapped there fails a test rather
    than becoming the fifth invisible disclosure.  That is the shape the fix took because this set
    grew from two to five without anyone noticing that none of them reached a client:
    ``R-134``, ``R-139``, ``R-141`` and ``R-144`` each record an erasure whose *disclosure* is the
    count, and until 0.9.0 the disclosure went nowhere.
    """

    snapshot: BoardIRSnapshot | None
    diagnostics: tuple[Diagnostic, ...] = ()
    max_roundrect_rounding_nm: int = 0
    unmodelled_group_count: int = 0
    edge_connector_pad_count: int = 0
    unmodelled_board_property_count: int = 0
    unmodelled_pad_property_count: int = 0
    unmodelled_thermal_bridge_angle_pad_count: int = 0
    unmodelled_setup_field_count: int = 0
    unmodelled_stackup_layer_count: int = 0
    unmodelled_footprint_field_count: int = 0
    footprint_copper_graphic_envelope_count: int = 0

    def __post_init__(self) -> None:
        if self.snapshot is not None and not isinstance(self.snapshot, BoardIRSnapshot):
            raise ValueError("conversion snapshot is malformed")
        if not isinstance(self.diagnostics, tuple) or not all(
            isinstance(item, Diagnostic) for item in self.diagnostics
        ):
            raise ValueError("conversion diagnostics must be an immutable tuple")
        if (
            isinstance(self.max_roundrect_rounding_nm, bool)
            or not isinstance(self.max_roundrect_rounding_nm, int)
            or self.max_roundrect_rounding_nm < 0
        ):
            raise ValueError("conversion rounding must be a non-negative integer nanometre count")
        if (
            isinstance(self.unmodelled_group_count, bool)
            or not isinstance(self.unmodelled_group_count, int)
            or self.unmodelled_group_count < 0
        ):
            raise ValueError("conversion group count must be a non-negative integer")
        if (
            isinstance(self.edge_connector_pad_count, bool)
            or not isinstance(self.edge_connector_pad_count, int)
            or self.edge_connector_pad_count < 0
        ):
            raise ValueError("conversion edge-connector pad count must be a non-negative integer")
        if (
            isinstance(self.unmodelled_board_property_count, bool)
            or not isinstance(self.unmodelled_board_property_count, int)
            or self.unmodelled_board_property_count < 0
        ):
            raise ValueError("conversion board property count must be a non-negative integer")
        if (
            isinstance(self.unmodelled_pad_property_count, bool)
            or not isinstance(self.unmodelled_pad_property_count, int)
            or self.unmodelled_pad_property_count < 0
        ):
            raise ValueError("conversion pad property count must be a non-negative integer")
        if (
            isinstance(self.unmodelled_thermal_bridge_angle_pad_count, bool)
            or not isinstance(self.unmodelled_thermal_bridge_angle_pad_count, int)
            or self.unmodelled_thermal_bridge_angle_pad_count < 0
        ):
            raise ValueError(
                "conversion thermal-bridge-angle pad count must be a non-negative integer"
            )
        if (
            isinstance(self.unmodelled_setup_field_count, bool)
            or not isinstance(self.unmodelled_setup_field_count, int)
            or self.unmodelled_setup_field_count < 0
        ):
            raise ValueError("conversion setup field count must be a non-negative integer")
        if (
            isinstance(self.unmodelled_stackup_layer_count, bool)
            or not isinstance(self.unmodelled_stackup_layer_count, int)
            or self.unmodelled_stackup_layer_count < 0
        ):
            raise ValueError("conversion stackup layer count must be a non-negative integer")
        if (
            isinstance(self.unmodelled_footprint_field_count, bool)
            or not isinstance(self.unmodelled_footprint_field_count, int)
            or self.unmodelled_footprint_field_count < 0
        ):
            raise ValueError("conversion footprint field count must be a non-negative integer")
        has_error = any(item.severity is Severity.ERROR for item in self.diagnostics)
        if has_error and self.snapshot is not None:
            raise ValueError("conversion errors cannot accompany a snapshot")
        if not has_error and self.snapshot is None:
            raise ValueError("a failed conversion must include an error diagnostic")
        if self.snapshot is None and self.max_roundrect_rounding_nm:
            raise ValueError("a failed conversion cannot report a rounding")
        if self.snapshot is None and self.unmodelled_group_count:
            raise ValueError("a failed conversion cannot report a group count")
        if self.snapshot is None and self.edge_connector_pad_count:
            raise ValueError("a failed conversion cannot report an edge-connector pad count")
        if self.snapshot is None and self.unmodelled_board_property_count:
            raise ValueError("a failed conversion cannot report a board property count")
        if self.snapshot is None and self.unmodelled_pad_property_count:
            raise ValueError("a failed conversion cannot report a pad property count")
        if self.snapshot is None and self.unmodelled_thermal_bridge_angle_pad_count:
            raise ValueError("a failed conversion cannot report a thermal-bridge-angle pad count")
        if self.snapshot is None and self.unmodelled_setup_field_count:
            raise ValueError("a failed conversion cannot report a setup field count")
        if self.snapshot is None and self.unmodelled_stackup_layer_count:
            raise ValueError("a failed conversion cannot report a stackup layer count")
        if self.snapshot is None and self.unmodelled_footprint_field_count:
            raise ValueError("a failed conversion cannot report a footprint field count")
        if (
            isinstance(self.footprint_copper_graphic_envelope_count, bool)
            or not isinstance(self.footprint_copper_graphic_envelope_count, int)
            or self.footprint_copper_graphic_envelope_count < 0
        ):
            raise ValueError(
                "conversion copper-graphic envelope count must be a non-negative integer"
            )
        if self.snapshot is None and self.footprint_copper_graphic_envelope_count:
            raise ValueError("a failed conversion cannot report a copper-graphic envelope count")
