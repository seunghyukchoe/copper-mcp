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

    ``unmodelled_group_count`` counts the root ``(group ...)`` expressions the KiCad adapter
    accepted and did not model.  An *unlocked* group is editor organisation with no geometry, no
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
    expressions than entries.  It is a count for the same reason the two fields above are: a
    diagnostic would refuse the board.  See ADR-0094 and R-139.
    """

    snapshot: BoardIRSnapshot | None
    diagnostics: tuple[Diagnostic, ...] = ()
    max_roundrect_rounding_nm: int = 0
    unmodelled_group_count: int = 0
    edge_connector_pad_count: int = 0
    unmodelled_board_property_count: int = 0

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
