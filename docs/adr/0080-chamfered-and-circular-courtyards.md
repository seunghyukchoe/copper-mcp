# ADR-0080: Bracket chamfered and circular courtyards instead of widening them

- Status: Accepted
- Date: 2026-08-06
- Owners: CopperMCP maintainers
- Related: [ADR-0058](0058-rectangular-courtyard-legality.md); [ADR-0065](0065-orthogonal-courtyard-chains.md);
  [ADR-0072](0072-conservative-arc-track-envelopes.md); [ADR-0075](0075-courtyard-oracle-parity.md);
  [curved courtyard research](../research/courtyard-curved-shapes-v1.md); issue #116

## Context

The #116 real-board survey refused 14 of 23 boards at the courtyard stage: ten at
`courtyard edges must be non-zero and axis-aligned` and four at
`courtyard primitive is unsupported by Board IR v0.2`. The survey's own hypothesis for the first
group — rotated footprints producing rotated courtyard rectangles — turned out to be wrong when
measured. Every diagonal edge in the corpus is an exact 45-degree chamfer with integer-nanometre
endpoints (bevelled electrolytic-capacitor courtyards, drawn in the footprint's local frame), and
every affected footprint sits at a quarter-turn pose. The second group is 104 `fp_circle`
courtyards (radial capacitors, test points, mounting holes), every one with an axis-aligned
radius point and therefore an exact integer radius. The corpus contains **no** rotated rectangle
and **no** `fp_arc` on a courtyard layer.

A courtyard is a keep-out, so widening it outward is conservative *for collision*. But ADR-0075
made `courtyard_overlap` published per-rule evidence: a conservative verdict that is not KiCad's
verdict is a false claim, not a safe one. Development measurement made this concrete: an early
circle predicate claimed `violated` at 10,001 nm of penetration, and real `kicad-cli` 10.0.5
reported clear — because a circle's cache is polygonised *inward* (chords sag by up to
`maxError`) before the 5,000 nm contraction, so its collision boundary sits somewhere in a
vertex-placement-dependent band, measured between 12,000 and 15,000 nm for one real pair.

## Decision

Board IR 0.2.0 widens its courtyard value to exactly the two measured classes, and legality
brackets every claim in the direction that licenses it.

1. **Octilinear rings are modelled exactly.** Courtyard edges may be horizontal, vertical, or
   exact 45-degree diagonals (`|dx| == |dy|`); the class has integer vertices, is closed under
   quarter-turn transforms, and admits every chamfer the corpus carries. Arbitrary-slope edges
   and non-quarter-turn poses remain typed refusals.
2. **Circles are modelled exactly as a new `CourtyardCircle` (centre, integer radius).** An
   inexact radius is refused (`integer.precision`), never rounded. The canonical encoder emits
   `courtyard_circles` only when non-empty, so every previously minted snapshot digest — and
   every committed golden identity — still verifies without a schema-version bump. A circle
   whose bounding box meets a sibling courtyard shape of the same footprint is refused, keeping
   even-odd pooling equal to plain union.
3. **Three-valued legality comes from direction-typed brackets.** `proven_clear` may only be
   proved by an *outer* bound (chamfer corner restored, circle bounding box): every KiCad cache
   is a subset of its raw region, so raw disjointness proves cache disjointness. `violated` may
   only be proved by an *inner* bound (chamfer corner cut out, inscribed rectilinear cross, or
   the exact circle-pair distance predicate), witnessed past each side's worst-case cache loss —
   one inset for polygonal sides, **two** insets, strictly, for circular sides. Everything
   between is `inconclusive`, including the chamfer corner triangles and the measured circle
   polygonisation band, which is deliberately conceded rather than fitted.
4. **Uncertifiable arrangements degrade, never guess.** The chamfer corner replacement is applied
   only when exact integer checks prove the corner triangle touches nothing else; otherwise the
   outer bound falls back to a bounding box and the inner bound to empty. An all-diagonal ring or
   a diagonal-plus-nesting region can therefore produce a concession but never a fabricated
   verdict. Purely orthogonal regions take the unchanged ADR-0075 single-scan path, bit for bit.
5. **`fp_arc` stays refused.** An arc is a fragment of a chain with no closed region to bound;
   ADR-0072's outward arc envelope was for *obstacles*, where over-refusal is safe because
   nothing publishes a legality claim from it. On this surface the same envelope would publish
   `violated` evidence KiCad does not share, so the honest unit of future support is the closed
   curved chain, not the primitive. No measured board loses anything: the corpus has zero
   courtyard arcs.

## Evidence

`scripts/benchmark_courtyard_curved_oracle_parity.py` extends B-089's methodology to the new
shapes against real `kicad-cli` 10.0.5: 23 cases, **12 exact parity, 11 conceded
`inconclusive`, 0 contradictions, 0 false-positive violations, 0 false-negative clears**.
Chamfered rings reproduce the exact 10,000 nm threshold (9,999 clear / 10,000 violation); the
circle band cases are conceded by design. Six hand-applied mutants — each direction guard
inverted, each demotion guard hardened into a verdict, and both circle-loss doublings removed —
are all caught by the committed tests. On the #116 survey tree, courtyard-stage refusals drop
from 13 boards to zero; remaining refusals are the survey's other named gaps (via nets,
roundrect radii, copper graphics, one oversized board, one duplicate-identity board).

## Consequences

- Board IR snapshots, scene payloads, and the MCP placement contract are digest-stable for every
  board without circles; boards with circles gain an optional `courtyard_circles` /
  `courtyard_circles_nm` field.
- The placement apply path is unchanged and still refuses to rewrite any courtyard topology
  beyond rectangles (ADR-0065), so chamfered- and circle-courtyard footprints are previewable
  but not yet movable through the serializer. That asymmetry is recorded as a residual risk
  rather than silently narrowed.
- Overlaps confined to a chamfer triangle or a circle's box corner are conceded, not refused;
  callers that want the stricter reading must treat `inconclusive` as a failure, which the
  three-valued vocabulary makes expressible.

## References

- [KiCad 10.0.5 courtyard cache construction](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/footprint.cpp)
- [KiCad 10.0.5 courtyard DRC provider](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/drc/drc_test_provider_courtyard_clearance.cpp)
- [KiCad footprint file format](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html)
- [Chamfered and circular courtyard research](../research/courtyard-curved-shapes-v1.md)
