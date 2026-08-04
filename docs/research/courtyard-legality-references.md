# KiCad courtyard-legality references

**Evidence snapshot:** 2026-08-04
**Pinned oracle:** KiCad CLI 10.0.5
**CopperMCP policy:** `kicad-10.0.5-rect-cache-v1`

This note grounds Placement 0.2's courtyard check in versioned upstream behavior and a reproducible
local oracle. It covers the strict Board IR 0.2 rectangle subset only. It is not evidence for
general KiCad courtyard topology, project-specific clearance rules, placement apply, or full DRC.

## Upstream findings

- KiCad reports `courtyards_overlap` when two footprint courtyards collide. Missing courtyards are
  handled by a separate `missing_courtyard` check whose default severity is Ignore; a missing shape
  does not participate in `courtyard_clearance`.
- The 10.0.5 DRC provider compares `F.CrtYd` only with `F.CrtYd` and `B.CrtYd` only with
  `B.CrtYd`. Coincident shapes on opposite sides do not collide. Multiple disconnected closed
  shapes are valid, and a collision with any shape reports the footprint pair once.
- Same-footprint rectangles are first converted into one polygon set. Their relationship is
  therefore semantic: a shared boundary can be malformed, intersecting outlines can merge, and a
  nested outline can become a hole. Treating every ring as an independent solid is not equivalent.
- KiCad polygonizes a footprint's courtyard with `maxError = 0.005 mm`, then contracts the cached
  front and back outlines by that same amount. The source comment states the intent: touching
  courtyards, including shapes exactly at their required clearance, are legal.
- KiCad's own QA matrix independently pins empty/single footprints, disjoint rectangles, exact
  edge touch, positive overlap, opposite sides, and multiple courtyard shapes.

Primary sources:

- [KiCad 10.0 PCB Editor DRC documentation](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#design-rule-checking)
- [KiCad 10.0 courtyard-clearance constraint](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#drc-rule-editor-constraint-reference)
- [KiCad 10.0.5 courtyard DRC provider](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/drc/drc_test_provider_courtyard_clearance.cpp#L91-275)
- [KiCad 10.0.5 courtyard-cache construction](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/footprint.cpp#L3664-3762)
- [KiCad courtyard-overlap QA matrix](https://docs.kicad.org/doxygen/test__drc__courtyard__overlap_8cpp_source.html)
- [KiCad 10 command-line reference](https://docs.kicad.org/10.0/en/cli/cli.html)

## Reproduced boundary behavior

The local oracle used `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli` 10.0.5 with isolated
configuration/document/runtime/temp directories and fixed JSON DRC arguments. Each threshold case
was repeated five times during discovery; every repetition agreed.

| Nominal relationship between two front rectangles | KiCad 10.0.5 result |
|---|---|
| 1 mm gap | Clear |
| 1 nm gap | Clear |
| Exact edge touch | Clear |
| 1 nm penetration | Clear |
| 9,999 nm penetration | Clear |
| 10,000 nm penetration | `courtyards_overlap` |
| 10,001 nm penetration | `courtyards_overlap` |
| 1 mm penetration | `courtyards_overlap` |
| Nested/coincident rectangles | `courtyards_overlap` |
| Coincident front/back rectangles | Clear |

The first rectangular zero-clearance collision is therefore exactly 10,000 nm of nominal
penetration, inclusive: each opposing cached boundary moves inward 5,000 nm, then KiCad's
zero-clearance collision predicate counts contact between those contracted caches.

The cache has a separate tiny-shape transition. With `missing_courtyard` explicitly elevated to
error, coincident same-side shapes produced:

| Rectangle dimensions | KiCad 10.0.5 result | Repetitions |
|---|---|---:|
| 1 × 1 nm; 9,999 × 9,999 nm; 10,000 × 10,000 nm | Two `missing_courtyard` errors | 5/5 each |
| 10,001 × 10,001 nm; 10,002 × 10,002 nm; 10,050 × 10,050 nm | No violation | 5/5 each |
| 10,051 × 10,051 nm | `courtyards_overlap` | 5/5 |
| 10,050 nm wide × 1 mm tall | No violation | 5/5 |
| 10,051 nm wide × 1 mm tall | `courtyards_overlap` | 5/5 |
| 1 mm wide × 10,000 nm tall | Two `missing_courtyard` errors | 5/5 |
| 1 mm wide × 10,001 nm tall | No violation | 5/5 |
| 1 mm wide × 10,031 nm tall | No violation | 5/5 |
| 1 mm wide × 10,037 nm tall | `courtyards_overlap` | 5/5 |
| 1 mm wide × 10,049, 10,050, or 10,051 nm tall | `courtyards_overlap` | 5/5 each |

The Y-short transition is therefore orientation-sensitive within a narrow band; it was not
over-fitted into the deterministic model. Placement instead requires **both dimensions to be at
least 10,051 nm**, the larger adjacent boundary reproduced on square and X-short cases. Smaller
rectangles remain valid Board IR observations but fail placement with typed `unsupported_geometry`.
This is a conservative supported-subset gate, not a claim that the source courtyard is invalid.

The same isolated CLI also pinned the topology gate that protects the rectangle-only model:

| Two rectangles on one front footprint | KiCad 10.0.5 result | Board IR 0.2 |
|---|---|---|
| Strictly disjoint | Valid disconnected shapes | Accepted |
| Touching along an edge | `malformed_courtyard` | Rejected |
| Partially overlapping | Valid merged outline | Rejected |
| One nested inside the other | Valid outer boundary plus hole; a second footprint inside that hole is clear | Rejected |

The rejections are deliberate subset boundaries, not claims that every rejected form is invalid
KiCad. Board IR currently carries canonical rings but no union/hole relationship, while the
Placement evaluator operates on independent cached rectangles. Semantic validation therefore
requires same-footprint rectangle bounds to be strictly disjoint instead of issuing an unsound
`proven_clear` or `violated` result.

Run the committed comparison with:

```bash
.venv/bin/python scripts/benchmark_placement_courtyards.py
```

The script synthesizes nine Apache-2.0 public-contract boards plus twelve tiny-cache boards, repeats
every tiny case five times with `missing_courtyard` explicitly enabled, and runs isolated KiCad JSON
DRC. It refuses to emit a passing artifact unless every expected/public/KiCad verdict and every
tiny repetition agrees. Placement 0.1's determinate coverage on the main question was 0/9 because
its only permitted result was `not_modelled`; Placement 0.2's acceptance target is 9/9 determinate,
zero false positives, zero false negatives, and 12/12 repeated tiny-cache cases on this pinned
matrix.

## Contract boundary

Placement 0.2 transforms every supported Board IR courtyard rectangle from its revision-bound
board pose into the proposed pose, contracts it by 5,000 nm per KiCad 10.0.5, and compares distinct
footprints only on the same side. Padless and locked footprints remain fixed collision obstacles.
Unmoved courtyard points stay in their already-canonical board frame, including padless footprints
whose stored pose is non-orthogonal. Every scanned footprint, transformed vertex, and pair
predicate consumes the shared placement work/deadline budget.

`proven_clear` means no overlap under that named policy. Evidence separately reports how many
footprints carried supported courtyards, how many same-side footprint pairs were evaluated, and how
many footprints had no courtyard. It does not mean:

- a positive or negative custom `courtyard_clearance` rule was loaded;
- a missing courtyard passed the separate KiCad check;
- non-rectangular, open, arc, polygon, mixed-layer, or back-side source import is supported;
- touching, overlapping, or nested same-footprint rectangle topology is supported;
- a full KiCad DRC run was bound to the placement candidate; or
- the proposed placement can be applied.
