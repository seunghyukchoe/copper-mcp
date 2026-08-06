# ADR-0075: Model KiCad's courtyard cache, and make courtyard legality three-valued

- Status: Accepted
- Date: 2026-08-06
- Owners: CopperMCP maintainers
- Related: [ADR-0004](0004-authoritative-kicad-drc.md); [ADR-0058](0058-rectangular-courtyard-legality.md);
  [ADR-0062](0062-stationary-padless-courtyard-envelope.md); [ADR-0065](0065-orthogonal-courtyard-chains.md);
  [courtyard oracle parity research](../research/courtyard-oracle-parity-v1.md); issues #72, #74

## Context

ADR-0058 introduced same-side courtyard legality and described it as *exact*. Two measurements
against real `kicad-cli` 10.0.5 show that word was overstated in two different ways, and that the
published per-rule evidence was consequently making claims the tool does not support.

**The cached-courtyard inset (#72).** KiCad's courtyard DRC does not compare footprint graphics. It
compares a cached `SHAPE_POLY_SET` that `FOOTPRINT::BuildCourtyardCaches` contracts by
`maxError = pcbIUScale.mmToIU( 0.005 )` — exactly 5,000 nm — via
`Inflate( -maxError, ... )` (`pcbnew/footprint.cpp:3701`, `:3712`). Both footprints are contracted,
so a zero-clearance collision needs 10,000 nm of nominal penetration. Measured on two 10 mm
squares: 9,999 nm is clear, 10,000 nm reports `courtyards_overlap`, and the same threshold applies
independently to each axis for corner-only overlap. CopperMCP's `orthogonal_rings_overlap_open`
reported a violation from 1 nm, so every verdict in that band was a refusal KiCad does not share.

**Ring nesting (#74).** `buildContourHierarchy` counts containing contours and makes an odd count a
hole (`pcbnew/convert_shape_list_to_polygon.cpp:373-400`, `:411`, `:446`). A courtyard drawn as an
outer boundary plus an inner ring — a donut — is an outline **with a hole**. CopperMCP compared
rings pairwise as independent solids, so it reported an overlap for a part placed in the hole. Real
KiCad reports zero violations for exactly that arrangement.

This is not a hypothetical topology. The official KiCad footprint library ships **31 footprints with
nested `F.CrtYd` rings**, almost all RF shielding cans: `Wuerth_36103205_20x20mm` has a 22 × 22 mm
outer courtyard, a 19 × 19 mm inner courtyard, and a 20 × 20 mm body, so the ring *is* the can wall
and the interior is deliberately left occupiable. Treating those rings as solids refuses every part
placed under every shield can in the library.

The direction of error differed between the two, and that is what made this worth resolving
together rather than separately. Treating a donut as solid refuses *more*, which is safe for
collision but is still a **false claim** about legality on a surface that publishes per-rule
evidence. Adopting KiCad's inset wholesale would refuse *less*, and asserting `proven_clear` at
9,999 nm of real geometric interference would be a different false claim in the opposite direction.

## Decision

Courtyard legality is evaluated over a footprint's **whole courtyard region** rather than
ring-by-ring, and its published result becomes **three-valued**.

1. **Rings of one footprint form a single even-odd region.** Crossings from every ring are pooled
   before pairing on each scanline, which reproduces KiCad's contour hierarchy exactly for the
   disjoint and strictly nested rings Board IR admits. A nested ring is a hole, not a second solid.
2. **The 5,000 nm cache inset is modelled**, as the constant `COURTYARD_CACHE_INSET_NM`, with the
   derived `COURTYARD_COLLISION_THRESHOLD_NM = 10,000`.
3. **`courtyard_overlap` gains `inconclusive`**, joining `pad_overlap` as three-valued:
   - `proven_clear` — the raw regions share no positive area. Contraction only ever shrinks a
     region (the outline moves in, any hole grows), so this is a proof for *any* ring shape.
   - `violated` — the shared area contains an axis-aligned rectangle at least the threshold on both
     axes, so the contracted regions provably meet. Exact parity with KiCad.
   - `inconclusive` — the regions overlap but no such witness exists. This is the sub-threshold band
     where the two models genuinely disagree, plus the tiny-shape band whose behaviour was measured
     as orientation-dependent and is deliberately not fitted.
4. **`inconclusive` is not a violation**, matching the existing `pad_overlap` convention and the
   `PlacementLegality.legal` docstring: legality means nothing was *proven* illegal. It is never
   rewritten to `proven_clear`; the distinction survives into `to_dict()`, the MCP contract, and the
   apply path.

## Consequences

A donut courtyard no longer produces a refusal KiCad contradicts, and the exactness claim in
ADR-0058 is corrected rather than restated. `scripts/benchmark_courtyard_oracle_parity.py` measures
the result against the real tool over 15 cases — 11 penetration offsets, the donut fixture, both
inset-boundary fixtures, and the CopperTone buffer board — recording **10/15 exact parity, 5/15
conceded `inconclusive`, 0 contradictions, 0 false-positive violations, 0 false-negative clears**.
It refuses to emit an artifact if any contradiction appears.

Two costs are accepted deliberately:

- **Sub-threshold interference no longer refuses a candidate.** A placement with under 10,000 nm of
  courtyard penetration is now previewed with `courtyard_overlap: inconclusive` instead of refused.
  That matches ADR-0004's authority ordering — KiCad calls it clear — and the non-claim is recorded
  in the evidence rather than dropped. Callers that need the stricter reading must treat
  `inconclusive` as a failure themselves, which the three-valued vocabulary makes expressible.
- **The witness search is sound but not complete.** It assembles a rectangle inside one horizontal
  strip, so a shared rectangle straddling several strips is not found. That can only report
  `inconclusive` where `violated` was provable, never the reverse.

Board IR is unchanged: no snapshot digest moves, and the golden placement candidate identity is
unaffected because its fixture carries no courtyard. Unsupported topology — arcs, non-orthogonal
edges, and same-footprint rings that touch or properly intersect — is still rejected by the Board IR
contract before a placement view exists, and remains outside this decision.

## References

- [KiCad 10.0.5 courtyard cache construction](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/footprint.cpp)
- [KiCad 10.0.5 courtyard DRC provider](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/drc/drc_test_provider_courtyard_clearance.cpp)
- [KiCad 10.0.5 outline-to-polygon conversion](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/convert_shape_list_to_polygon.cpp)
- [KiCad 10.0 PCB Editor DRC](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#design-rule-checking)
