# `Edge.Cuts` outline arcs: what the boards carry, what KiCad means, and which way the error must go

Research date: 2026-08-28. This note supports
[ADR-0124](../adr/0124-an-outline-arc-is-inscribed-and-a-cut-is-refused.md),
[D-229](../ledgers/decision-ledger.md), [SEC-166](../ledgers/security-ledger.md) and
[R-180](../ledgers/risk-register.md), and it extends
[the segment-assembly note](./edge-cuts-outline-assembly-v1.md) (ADR-0076) to the one construct
that note deliberately left out. No external code is copied; cited identifiers and line references
exist so each claim can be re-derived rather than trusted.

It answers three questions and refuses everything else: **what does KiCad write for a curved board
outline, what do ten real boards actually write, and in which direction may each of those shapes be
approximated.** It claims nothing about ellipses, holes, multiple board outlines, the physical
position of the milled edge, footprint-local `Edge.Cuts` graphics, or the legacy board format.

## 1. KiCad's arc, with citations

The board S-expression format defines a graphical arc with three control points and no angle field:

```
(gr_arc (start X Y) (mid X Y) (end X Y) (stroke …) (layer L) [(locked)] (uuid UUID))
```

`mid` is described as the point of the arc **between** `start` and `end` — a point on the curve,
not a centre and not a control handle — and source files are written in millimetres against a
one-nanometre internal resolution, so "there is maximum resolution of six decimal places". Sources:
[S-expression board format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/) and
[S-expression common definitions](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/).

Two consequences carry the whole design, and they are the same two
[the copper-arc note](./kicad-arc-track-obstacles-v1.md) recorded for `(arc …)` tracks:

1. **Three integer points determine the circle exactly.** The centre is *rational* and the radius
   is the square root of a rational. No polygon is equal to the arc, and any predicate written
   against `r` rather than `r²` needs a rounding rule that has to be argued.
2. **The file never records the sweep direction.** The arc that is meant is precisely the one
   passing through `mid`, which is what distinguishes it from its companion arc on the same circle.

`ConvertOutlineToPolygon` in `pcbnew/convert_shape_list_to_polygon.cpp` is what KiCad itself does
with these: it chains shapes into closed polygons under an `aChainingEpsilon` proximity test, and
tessellates arcs and Béziers to polylines with `ConvertToPolyline( aErrorMax )`. Source:
[`pcbnew/convert_shape_list_to_polygon.cpp`](https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/convert_shape_list_to_polygon.cpp).
`aErrorMax` bounds the deviation **without fixing its sign**, which is exactly why KiCad's
tessellation cannot be reused here.

The numeric value borrowed for the sagitta bound is KiCad's own
`maxError = pcbIUScale.mmToIU( 0.005 )` = 5,000 nm, the same constant
[the curved-courtyard note](./courtyard-curved-shapes-v1.md) pinned against KiCad 10.0.5 in
`FOOTPRINT::BuildCourtyardCaches`
([`pcbnew/footprint.cpp`](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/footprint.cpp),
line 3701).

## 2. What ten real boards carry — the measurement comes before the design

Measured read-only over the digest-bound ten-board public cohort under predeclared fingerprint
`sha256:bfec8210d6d4eb746ffdbfb3b70309ce` (B-134). No board byte, path, digest or coordinate is
published here.

**The 97 curve primitives B-114 counted are all arcs.** Not "arcs, circles and beziers" — 97
`gr_arc`, zero `gr_circle`, zero `gr_curve`, zero `gr_bezier`, zero `gr_poly`, on any of the ten.
One board additionally carries footprint-local `Edge.Cuts` graphics (6 `fp_line`, 3 `fp_arc`),
which a different gate refuses.

The child grammar is closed and uniform: `start`, `mid`, `end`, `stroke`, `layer`, `uuid` on all 97,
with **no positional atom on any of them**, and `stroke` carrying `width` and `type` (61 `solid`,
36 `default`). Nothing writes the legacy scalar `width` and nothing writes `angle`.

Counting arcs as edges and chaining by exact endpoint coincidence:

| | boards |
|---|---|
| exactly one closed non-branching loop | **7** of 10 |
| unpaired endpoints from sub-micron near-misses | 2 (smallest separations **17 nm** and **19 nm**) |
| unpaired endpoints from a genuine opening | 1 (**69.6 mm**) |

Of the 51 arcs on the seven boards that close: **all 51 minor** (span at most a half turn), all 51
with rational non-integer circumcentres, radii 1.0–5.0 mm, sweeps 45°–90°, **38 convex** and
**13 concave**, the 13 falling on exactly two boards.

Designing for circles, Béziers, major arcs or integer circumcentres would therefore have been
designing for a problem no measured board has — the same conclusion, reached the same way, as the
curved-courtyard note's "there is no `fp_arc` on a courtyard layer in any of the 23 boards".

## 3. Direction of error, per shape and per consumer

The rule ADR-0076 fixed: **the board outline is routing room, not an obstacle.** An obstacle may be
over-approximated because a larger obstacle only refuses more. An outline may only be
*under*-approximated, because a larger outline hands a caller area the fabricated board does not
have — a fabrication-affecting error.

### 3.1 The convex region that makes it decidable

For an arc with chord `start`–`end` and control point `mid`, let `S` be the region bounded by the
chord and the arc. Then

    S  =  disc(O, r)  ∩  half-plane containing `mid`

an intersection of two convex sets, hence **convex**, for a minor arc and a major one alike. So a
polyline whose *vertices* lie in `S` has all of its *edges* in `S`: containment is two exact
integer predicates per vertex, and never a segment-versus-circle test.

- **Convex arc** (bulges away from the interior): `S` is board material. A polyline through `S`
  gives a region contained in the true one. **Safe.**
- **Concave arc** (cuts into the board): `S` is exactly the material the cut removed. A polyline
  through `S` claims it back. **Forbidden.** The safe construction is a polyline *outside* the
  circle along tangent segments, whose safe region `complement(disc) ∩ half-plane` is **not
  convex** — so it needs an exact per-edge distance test, a different obligation, and it is refused
  by name instead.

Rounding survives this because it is only ever applied toward the safe side: a candidate vertex is
computed in floating point, then verified in exact integers, then pulled toward the centre if it
missed, and **dropped** if it still misses. There is deliberately no operation that moves a vertex
outward, which is the same discipline as
[ADR-0072](../adr/0072-conservative-arc-track-envelopes.md)'s ceiling square root, pointed the
other way.

### 3.2 Which way is "out" is a property of the ring, not the arc

Convexity is not readable from `start`, `mid` and `end` alone; it needs to know where the board is.
The cycle is therefore assembled from **chords** — chord and arc share endpoints, so topology cannot
depend on the bend — and the chord ring's signed area gives the interior side.

That reading is only sound if the chord ring is simple, so the chord ring is checked for
self-intersection **before** its orientation is trusted. A non-simple ring has no consistent
interior, and misreading one would classify a concave arc as convex: the exact
over-approximation this whole path exists to prevent.

### 3.3 Per consumer, traced rather than assumed

| Consumer | What it needs from the outline | An inscribed outline is |
|---|---|---|
| `routing/astar.py` route preview | routing room ⊆ true board; and an axis-aligned rectangle, or it refuses | **safe** (less room is never a false claim) |
| `routing/layered_board_adapter.py` | same | **safe** |
| `placement/legalizer.py` `outline_containment` → `proven_inside` | modelled ⊆ true | **safe** |
| `placement/legalizer.py` `outline_containment` → `violated` | modelled ⊇ true | **unsound** |
| `placement/legalizer.py` `EdgeRule` residual | the board's exact bounding box, two-sided | **unsound** |
| `placement/legalizer.py` `RegionRule` keyed to the contour | exact boundary, two-sided | **unsound** |
| `circuit_scene.py` `around_ref` bounds index | bounds that **over**-approximate | **unsound**, bounded by the deviation ([R-180](../ledgers/risk-register.md)) |
| apply patch paths | contour *identity* only, no geometry | unaffected |

Three of those want the outline in both directions, which under this project's own rule leaves
exact representation or refusal. The whole placement request is therefore refused when the
conversion reports a non-zero deviation — refused rather than degraded, because two of the three
have no "not proven" value in their three-valued status to degrade into.

## 4. Refusals

This note does not claim: where the physical board edge lands after milling relative to the
`Edge.Cuts` centreline; the numeric default of `aChainingEpsilon` or `ARC_HIGH_DEF` in any given
KiCad version; anything about outline holes, disjoint boards, or `aAllowDisjoint`; anything about
ellipse outline shapes; how KiCad tessellates an arc beyond the fact that `aErrorMax` bounds the
deviation without fixing its sign; anything about footprint-local `Edge.Cuts` graphics, which are
refused by a different gate and are a frame-transform problem rather than an approximation one; or
that the ten measured boards are a sample of anything — they were selected for licence clarity and
KiCad-native development, which is a selection on the property under test.
