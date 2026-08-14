# ADR-0110: Placement boundary verdicts bracket KiCad parity

- Status: Accepted
- Date: 2026-08-15
- Owners: `@seunghyukchoe`
- Related: [D-206](../ledgers/decision-ledger.md), [R-159](../ledgers/risk-register.md),
  [B-116](../ledgers/benchmark-ledger.md),
  [issue #187](https://github.com/seunghyukchoe/copper-mcp/issues/187),
  [ADR-0024](0024-placement-intent-and-legalization.md),
  [ADR-0075](0075-courtyard-oracle-parity.md),
  [ADR-0080](0080-chamfered-and-circular-courtyards.md),
  [pad geometry reader survey](../research/pad-geometry-reader-survey-v1.md),
  [`2026-08-15-placement-direction-brackets.json`](../mutants/2026-08-15-placement-direction-brackets.json)

## Context

The reader survey found three placement readers whose geometry direction was wrong before custom
pads made the error larger. `_PlacedPad.bounds` over-approximates copper, but
`_outline_containment` and `_keepout_respect` used a bounds miss or contact to publish
`violated`. A circle near a diagonal boundary supplies the counterexample: its box corner can cross
the boundary while its copper does not. The same `_resolve_bounds` also served region keep-in,
region keep-out, alignment and symmetry even though those questions need, respectively, an
under-approximation, an over-approximation and an exact position.

The predeclared corpus measurement found one board on which three placement batches were reported
`outline_containment: violated` while KiCad 10.0.5 reported zero `copper_edge_clearance`
violations. The first repair tried the obvious two-valued substitution: use the pad core and call a
core outside the outline `violated`. Measurement stayed **1 false violation, not 0**.

That failed result exposed a second mismatch. KiCad's official 10.0.5
[`drc_test_provider_edge_clearance.cpp`](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/drc/drc_test_provider_edge_clearance.cpp#L172-443)
builds an index of `Edge.Cuts`/`Margin` shapes and tests copper items against nearby edges with
`Collide`; it does not perform a global point-in-board test. Copper wholly beyond an edge but remote
from it therefore produces no `copper_edge_clearance` finding. Calling that placement a KiCad edge
violation is another parity divergence, even when the core is exact enough to prove the copper is
outside.

These verdicts name KiCad parity, not a conservative routing obstacle. Neither false `violated` nor
false `proven_*` has a safe direction. The model needs a third value for the gap.

## Decision

`outline_containment` and `keepout_respect` are three-valued.

| Check | Positive proof | Negative proof | Neither |
|---|---|---|---|
| Outline | Every over-approximating pad bound is inside the outline and clear of its boundaries → `proven_inside` | An under-approximating pad core crosses an outline boundary → `violated` | `inconclusive` |
| Footprint keepout | Every applicable over-approximating pad bound is disjoint from the keepout → `proven_clear` | An under-approximating pad core meets the keepout → `violated` | `inconclusive` |

`inconclusive` is a disclosed absence of proof, not an alias for either endpoint. As with the
existing pad-overlap and courtyard brackets, it does not itself make a candidate illegal; a
candidate is published when no check is `violated`, and the full legality record travels with it.
Consumers that require an all-proven placement must reject an inconclusive field or request the
existing candidate-bound KiCad DRC path.

Rule readers are split by requirement instead of sharing `_resolve_bounds`:

- region keep-in resolves an under-approximating pad core;
- region keep-out, proximity and edge-offset rules retain over-approximating bounds;
- alignment and symmetry resolve the exact footprint origin or pad centre, never an envelope
  midpoint.

Footprint subjects retain their existing hull semantics for region rules because Board IR does not
model a separate footprint attachment core. A pad whose core is unavailable refuses a core-requiring
rule as `unsupported_geometry`; it is never answered from its envelope.

No Board IR field, schema, codec or accepted board construct changes. The public placement output
contract gains the `inconclusive` literal for the two legality fields, and apply re-validates the
same closed vocabulary when it reads an untrusted candidate.

## Consequences

The repeated corpus measurement in B-116 moves false published violations from **1 → 0** for
outline containment and remains **0 → 0** for keepouts, while conversion stays **13/18**. The three
affected batches move from refused to previewed with `outline_containment: inconclusive`. Each
relevant KiCad violation-type count agreed over two invocations on every converting board, and
every corpus source hash remained unchanged.

Synthetic diagonal-boundary fixtures establish both directions against real KiCad 10.0.5: a
bounds-only outline or keepout contact is inconclusive and has no corresponding violation, while
the existing core-crossing fixtures remain `violated` and retain their KiCad errors. Sixteen
committed mutants are killed, including the original bounds-only defects, the failed core-only
repair, lost true violations, collapsed third values, wrong rule regions and envelope-derived
positions.

The remaining cost is explicit. `inconclusive` can include copper wholly remote from the board
edge, because that is also not a KiCad edge-clearance violation. This decision does not turn KiCad's
edge provider into a fabrication-containment proof. A separate strict fabrication rule would need
its own name and semantics rather than overloading `outline_containment` with a claim KiCad does not
make. R-159 carries the risk that a caller mistakes “not proven illegal” for “proven inside.”

## Alternatives considered

- **Keep the two-valued bounds verdicts.** Rejected by the measured false violation and by the
  bounds/core direction contract.
- **Replace bounds with the core and stay two-valued.** Implemented and measured first; it left the
  false-violation count at one because “core outside” is not “core crosses an Edge.Cuts boundary.”
- **Model exact copper polygons now.** Deferred. It would still not make global containment a KiCad
  edge-clearance claim, and custom-pad exact geometry is the next schema-bearing step rather than a
  prerequisite for naming the current uncertainty honestly.
- **Treat every inconclusive result as illegal.** Rejected for the preview contract. It would retain
  the false refusal under a different label and make the third value operationally indistinguishable
  from `violated`; strict consumers can already require all-proven evidence explicitly.
