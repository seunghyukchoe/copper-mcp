# Chamfered and circular courtyards: measured shapes, KiCad semantics, direction of error

Research date: 2026-08-06. Pinned oracle: KiCad 10.0.5
(`/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`, reported version `10.0.5`).
This note supports the curved-courtyard ADR and issue #116's causes 1 and 3. No external code is
copied; cited lines are identifiers only. It extends
[courtyard oracle parity](./courtyard-oracle-parity-v1.md) (ADR-0075) to two new shape classes and
changes nothing about the orthogonal subset it pinned.

## 1. What the refused boards actually carry — the measurement comes first

Issue #116 hypothesised that the ten `courtyard edges must be non-zero and axis-aligned` refusals
came from *rotated footprints producing rotated courtyard rectangles*. Measured against the same
23-board tree (read-only), that hypothesis is wrong:

- **Every diagonal courtyard edge in the corpus is an exact 45-degree chamfer**: `|dx| == |dy|`
  in integer nanometres, with all endpoints exact at the file's own precision. They are the
  bevelled polarity corners of SMD electrolytic capacitor courtyards (`CP_Elec_8x10`,
  `CP_Elec_10x10` — 60 such edges), drawn as `fp_line` chains in the footprint's *local* frame.
- **Every affected footprint sits at a quarter-turn pose** (`0`, `90`, `-90`). KiCad stores
  footprint-local graphics unrotated, and a non-quarter `(at x y angle)` is refused earlier by
  the `unsupported.transform` guard — a refusal the survey never produced. There is no rotated
  rectangle anywhere in the corpus.
- **Every unsupported courtyard primitive is an `fp_circle`** (104 of them: radial capacitors,
  loop test points, mounting holes), and every one has an axis-aligned radius point, hence an
  exact integer-nanometre radius. There is **no `fp_arc`** on a courtyard layer in any of the
  23 boards.

Designing for the general rotated case would therefore have solved a problem no board has, at the
cost of the exactness the evidence surface depends on. The supported classes are chosen to be
exactly what the boards carry: octilinear rings (horizontal, vertical, exact 45-degree edges) and
exact-radius circles.

## 2. What KiCad compares for these shapes

ADR-0075 established the mechanism: courtyard DRC collides *cached* polygon sets built by
`FOOTPRINT::BuildCourtyardCaches`, which converts the courtyard graphics at
`maxError = pcbIUScale.mmToIU( 0.005 )` = 5,000 nm and then applies
`Inflate( -maxError, CORNER_STRATEGY::CHAMFER_ACUTE_CORNERS, maxError )`
([`pcbnew/footprint.cpp`, KiCad 10.0.5](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/footprint.cpp),
lines 3701 and 3712). Two consequences differ by shape class:

- **A polygonal courtyard (orthogonal or chamfered) polygonises exactly** — there is no arc to
  approximate — so its cache is the region contracted by exactly one inset. The measured
  10,000 nm collision threshold from ADR-0075 carries over unchanged, and the curved-shape
  benchmark reproduces it for chamfered rings: 9,999 nm penetration clear, 10,000 nm violation.
- **A circular courtyard is polygonised before contraction**, and the polygon's chords sag
  *inward* by up to `maxError`. Its cache is therefore sandwiched between the disc eroded by
  `2 * maxError` and the disc itself, and where the collision boundary falls inside that band
  depends on where the segment vertices happen to land. Measured with two 1.8 mm-radius circle
  courtyards approaching on the x axis (`kicad-cli` 10.0.5, isolated config):

  | nominal penetration | `courtyards_overlap` |
  |---|---|
  | 10,000 nm | no |
  | 10,001 nm | no |
  | 12,000 nm | no |
  | 15,000 nm | **yes** |
  | 20,000 nm | **yes** |

  The boundary for this pair falls between 12,000 and 15,000 nm and is not a constant of the
  format — it is a vertex-placement artifact. It would be false precision to fit it.

## 3. Direction of error, restated for a keep-out on an evidence surface

A courtyard is a keep-out, so an outward (larger) approximation is *conservative for collision*:
it refuses more and never permits a real overlap. ADR-0072 used exactly that direction for arc
**obstacles**, where the consumer is a router that only needs "never route through copper".

That direction cannot be copied here. This surface publishes per-rule *evidence*
(`courtyard_overlap` in placement legality, ADR-0060/0075), so a conservative answer that is not
KiCad's answer is a **false claim**, not a safe one — in either direction:

- reporting `violated` from an outward bound asserts a collision the authoritative tool does not
  report (the exact failure a 10,001 nm circle case produced during development, caught by the
  oracle benchmark before landing);
- reporting `proven_clear` from an inward bound asserts a clearance nobody proved.

The resolution is the same bracketing discipline `pad_bounds`/`pad_core` already use:

- **`proven_clear` only from an outer bound.** Raw-region disjointness proves cache disjointness
  because every cache is a subset of its raw region (polygon caches by contraction; circle caches
  because inward polygonisation then contraction only shrinks). A chamfer's outer bound restores
  the corner square; a circle's outer bound is its bounding box.
- **`violated` only from an inner bound**, witnessed by an axis-aligned rectangle that survives
  both sides' worst-case cache loss: one inset per polygonal side (measured exact at the
  threshold), *two* insets per circular side (sagitta plus contraction, strict because the
  exact-threshold contact is unmeasured for circles). A chamfer's inner bound cuts the corner
  square out; a circle's inner bound is an inscribed rectilinear cross, and a lone circle pair is
  decided by the exact integer distance predicate instead.
- **Everything between is `inconclusive`** — the chamfer corner triangles, the circle
  bounding-box corners, the sub-threshold cache band ADR-0075 already concedes, and the circle
  polygonisation band measured above. `inconclusive` is not a violation, matching the
  `PlacementLegality.legal` contract.
- **Typed refusals stay for what has no honest model**: `fp_arc` (a fragment of a chain, with no
  closed region to bound until curved chains are modelled), inexact circle radii (either rounding
  direction misstates the keep-out), non-quarter-turn poses (irrational vertices), arbitrary-slope
  edges, and circles overlapping sibling courtyard shapes (even-odd pooling would silently delete
  keep-out area).

## 4. Soundness conditions the implementation enforces

The corner replacement that orthogonalises a chamfer is sound only when the closed corner
triangle touches nothing else: then the even-odd region changes by exactly that triangle. The
implementation verifies this exactly in integers (vertex-in-closed-triangle, edge-versus-side
intersection, collinear doubling-back through shared vertices, cross-chamfer interference) and
**degrades** rather than guesses when it cannot certify an arrangement — outer falls back to a
bounding box (still a superset), inner falls back to empty (claims nothing). A ring made entirely
of diagonals (a diamond) therefore can never produce `violated`, only a concession; the same
holds for multi-ring regions that mix diagonals with nesting.

Circles pool with rings as even-odd contours, which equals plain union only for disjoint shapes;
Board IR validation and the adapter refuse a circle whose bounding box meets any sibling
courtyard shape of the same footprint, so the pooled region is always a union.

## 5. What this note is not evidence for

Arcs and general curved chains; nonzero or negative `courtyard_clearance` rules; footprint poses
off the quarter-turn grid; the exact location of the circle polygonisation boundary (deliberately
conceded as a band); placement apply, which remains rectangular-courtyard-only per ADR-0065; and
full-board DRC.

## References

- [KiCad 10.0.5 `pcbnew/footprint.cpp` — courtyard cache construction](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/footprint.cpp)
- [KiCad 10.0.5 `pcbnew/drc/drc_test_provider_courtyard_clearance.cpp`](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/drc/drc_test_provider_courtyard_clearance.cpp)
- [KiCad 10.0.5 `pcbnew/convert_shape_list_to_polygon.cpp` — contour hierarchy](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/convert_shape_list_to_polygon.cpp)
- [KiCad footprint file format — `fp_circle`, `fp_arc`, `fp_poly`](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html)
- [Courtyard oracle parity research](./courtyard-oracle-parity-v1.md) and [ADR-0075](../adr/0075-courtyard-oracle-parity.md)
- [ADR-0072 — conservative arc track envelopes](../adr/0072-conservative-arc-track-envelopes.md) (the obstacle direction this note deliberately does not copy)
