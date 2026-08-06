# Assembling a KiCad board outline from Edge.Cuts segments

Research date: 2026-08-06.  This note supports [D-154](../ledgers/decision-ledger.md),
[R-117](../ledgers/risk-register.md) and [ADR-0076](../adr/0076-segment-assembled-edge-cuts-outline.md),
and grounds the outline assembly in `src/copper_mcp/adapters/kicad_board_ir.py`.  No external code
is copied; the quoted fragments below are short excerpts from KiCad's published documentation and
source, cited so each claim can be re-derived rather than trusted.

It answers exactly two questions — **what does KiCad write on `Edge.Cuts` when a user draws a board
outline, and what does KiCad itself require of those shapes before it will treat them as a board**
— and refuses to claim anything about arcs, Bézier curves, ellipses, holes, multiple board
outlines, the physical position of the milled edge, or the pre-4.0 legacy board format.

## The question, and why the rectangle was not enough

CopperMCP's adapter accepted exactly one root graphic on `Edge.Cuts`: a single unfilled `gr_rect`.
Everything else — including the `gr_line` segments KiCad writes when you draw an outline with the
line tool — was refused with `unsupported.construct` (issue #111).  A real four-layer board drawn
by hand carries four `gr_line` expressions on `Edge.Cuts` and no `gr_rect` at all, so it was
refused, and the refusal read as a capability limit rather than as the defect it was.

`gr_rect` is what KiCad writes only when the outline was drawn with the *rectangle* tool.  It is a
special case of the general thing, not the general thing.

## Finding 1 — the file format writes an outline as independent graphic shapes

The board S-expression format defines the graphic items separately from any notion of an outline.
A graphical line is:

```
(gr_line
  (start X Y)
  (end X Y)
  (layer LAYER)
  (width WIDTH)
  (uuid UUID)
)
```

and a graphical arc carries `(start …) (mid …) (end …)`, with `gr_rect` carrying `(start …)`
(upper-left) and `(end …)` (lower-right).  Coordinates are millimetres, and the format states that
"the minimum internal unit for printed circuit board and footprint files is one nanometer so there
is maximum resolution of six decimal places".  Sources:
[S-expression board format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/) and
[S-expression common definitions](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/), the
latter carrying the graphic-item grammar and the coordinate-resolution statement.  The board format
page's own worked example writes an Edge.Cuts edge as
`(gr_line (start 58 42) (end 58 29) (angle 90) (layer Edge.Cuts) (width 0.15))`.

Two consequences matter here.  First, **nothing in the file says which shapes form the outline, in
what order, or in which direction**: the shapes are an unordered set and each was drawn in whatever
direction the user dragged.  The outline is a property KiCad *derives*.  Second, the six-decimal
millimetre resolution is exactly the integer-nanometre grid CopperMCP already parses through
`board_ir.mm_to_nm`, so an outline assembled from endpoints needs no rounding rule at all — the
vertices are the drawn points.

## Finding 2 — KiCad derives the outline by chaining endpoints, with a tolerance

`ConvertOutlineToPolygon` in `pcbnew/convert_shape_list_to_polygon.cpp` takes the shape list and
builds closed polygons from it.  Its signature carries both an approximation error and a chaining
tolerance:

```cpp
bool ConvertOutlineToPolygon( std::vector<PCB_SHAPE*>& aShapeList,
                              SHAPE_POLY_SET& aPolygons,
                              int aErrorMax, int aChainingEpsilon,
                              bool aAllowDisjoint,
                              OUTLINE_ERROR_HANDLER* aErrorHandler,
                              bool aAllowUseArcsInPolygons )
```

Endpoints are joined when they are merely *close*, not equal — the proximity test is
`( aLeft - aRight ).SquaredEuclideanNorm() <= SEG::Square( aLimit )` with `aLimit` derived from
`aChainingEpsilon` — and shapes that are already closed (circle, rectangle, polygon) bypass chaining
entirely.  Arcs and Béziers are tessellated to polylines with `ConvertToPolyline( aErrorMax )`.
Source: [`pcbnew/convert_shape_list_to_polygon.cpp`](https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/convert_shape_list_to_polygon.cpp).

## Finding 3 — KiCad's own refusals name exactly the four malformations

The same file reports outline failures with these strings, which surface in the UI as
"Board has malformed outline (…)":

```cpp
_( "(not a closed shape)" )
_( "(self-intersecting)" )
_( "(multiple board outlines not supported)" )
_( "(segment has null or very small length: %d nm)" )
```

with sibling messages for a null rectangle, circle, arc, or ellipse, checked by
`TestBoardOutlinesGraphicItems( BOARD* aBoard, int aMinDist, OUTLINE_ERROR_HANDLER* aErrorHandler )`
against `int min_dist = std::max( 0, aMinDist );`.  These are the DRC errors users hit in practice
— "not a closed shape" and "self-intersecting" are both common enough to have their own forum
threads, and the standard advice is to zoom in and snap the endpoints together
([not a closed shape](https://forum.kicad.info/t/error-board-has-malformed-outline-not-a-closed-shape/40462),
[self-intersecting](https://groups.io/g/kicad-users/topic/error_board_has_malformed/94046770),
[no edges found](https://forum.kicad.info/t/board-has-malformed-outline-no-edges-found-on-edge-cuts-layer/34691)).

So the set of malformations CopperMCP must have an answer for is not a guess: it is KiCad's own
list — open contour, self-intersection, more than one loop, and a degenerate zero-length shape —
plus the branching and duplicate-edge cases that fall out of assembling a graph rather than a path.

## What this means for the adapter, and where it deliberately differs

The adapter accepts an `Edge.Cuts` outline drawn as `gr_line` segments, assembled by exact endpoint
coincidence into exactly one closed simple loop, and keeps everything else a typed refusal.  Two
deliberate differences from KiCad:

- **Chaining epsilon is zero.**  KiCad closes a sub-tolerance gap for you.  CopperMCP refuses it
  (`geometry.invalid`, "must be one closed non-branching loop").  This is the direction-of-error
  rule and it is the crux of this slice: **the board outline is routing *room*, not an obstacle**.
  An obstacle may be over-approximated, because a larger obstacle only makes the router refuse
  more.  An outline may only be **under**-approximated, because a larger outline hands the router
  area the fabricated board does not have — a fabrication-affecting error.  Closing a 10 µm gap
  adds area that no drawn segment encloses.  Every other "helpful" repair fails the same test:
  dropping a spur, keeping the largest of several loops, or snapping a near-miss each invent board.
  Assembled from straight segments with exact coincidence, the modelled ring's vertices *are* the
  drawn endpoints, so containment holds with equality — the strongest form of "never larger".
- **Arcs, circles, polygons and curves on `Edge.Cuts` stay refused**, with a diagnostic that names
  the curve.  KiCad tessellates an arc to a polyline within `aErrorMax` in *either* direction; that
  is fine for rendering and wrong for this contract.  An outline arc needs an *inscribed*
  approximation, and whether the chord is inscribed depends on which side of the ring the arc
  bulges toward: an arc bulging outward loses area when replaced by its chord (safe), while an arc
  bulging inward — a concave bite out of the board — *gains* the bitten area back (unsafe).  This is
  not [ADR-0072](../adr/0072-conservative-arc-track-envelopes.md)'s conservative sagitta envelope
  run backwards: that bound is an upper bound by construction, which is the wrong direction here,
  and a single chord is a poor inscribed model for a large-radius arc while subdividing it requires
  exact integer points on the arc, which generally do not exist.  Arcs are therefore their own
  slice, not a parameter of this one.

The stroke width of the `Edge.Cuts` graphics is ignored, exactly as it already was for `gr_rect`,
because KiCad's own outline is built from shape centrelines.

## Refusals

This note does not claim: where the physical board edge lands after milling relative to the
`Edge.Cuts` centreline; the numeric default of `aChainingEpsilon` or `ARC_HIGH_DEF` in any given
KiCad version (only that the epsilon is non-zero and configurable); anything about outline holes,
disjoint boards, or `aAllowDisjoint`; anything about ellipse or Bézier outline shapes; how KiCad
tessellates an arc beyond the fact that `aErrorMax` bounds the deviation without fixing its sign;
or anything about the pre-4.0 legacy board format documented separately at
[legacy PCB format](https://dev-docs.kicad.org/en/file-formats/legacy-pcb/).
