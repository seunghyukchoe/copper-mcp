# Where the `off_grid` refusal actually binds on real boards

**Research date:** 2026-08-12
**Reviewed against:** CopperMCP at `48037d0`, KiCad board format version 20260206
**Covers:** every `not_routed/off_grid` refusal the single-layer route preview produces on a live
private audio-project tree; which pad misses the routing lattice and by exactly how many
nanometres; where the nanometre residues in those coordinates come from; and what happens when
each of those nets is re-previewed on a lattice fine enough to represent it exactly.
**Refuses to claim:** that these boards are a sample of KiCad boards in the world; that the
counts here reproduce against a different snapshot of the same tree, which is a live working copy;
that any net measured here is routable by some other means; or anything electrical, thermal, DRC
or manufacturing. Nothing was DRC-checked, nothing was applied, no board was written, and no board
or derivative of one is committed.

## 1. Why this measurement exists

[ADR-0089](../adr/0089-region-scoped-obstacle-model.md) scoped the obstacle model to a routing
region. Before it, every net on these boards refused `obstacle_budget_exceeded` before the router
ever asked a lattice question, so the lattice-class refusals — `off_grid`, `no_path`,
`grid_budget_exceeded` — had **never been observed on a real board**. [B-096](../ledgers/benchmark-ledger.md)
recorded the class becoming visible and deliberately did not investigate it.

[B-088](../ledgers/benchmark-ledger.md) had already recorded a hypothesis from an external corpus:
that the routing constraint is localised to lattice alignment and the grid-node budget rather than
to the obstacle model. That hypothesis was written down before anything could test it here. This
note tests it.

## 2. Method

Every `.kicad_pcb` under the tree excluding `.history/` and derived stems, 11 of 17 convertible;
for each, the first 40 named nets in Board IR canonical order on `F.Cu`, at one net class
throughout (clearance 200,000 nm, track width 250,000 nm, via 600,000/300,000 nm) — 385 previews,
the selection fixed before the run and never adjusted by retrying. Every setting other than the
net class is at its default, so `grid_step_nm` is **250,000 nm** throughout. `Settings` is
constructed in process with every apply and live flag false.

The corpus is a live working copy. The counts below therefore describe **this** snapshot and are
not comparable row-for-row with B-096's, which was taken against an earlier one; the before and
after runs here were taken back to back against byte-identical sources so that the comparison
between them is a comparison of code.

| Verdict | Count |
|---|---:|
| `already_connected` | 288 |
| `not_routed/invalid_two_pin_net` | 43 |
| `not_routed/no_path` | 27 |
| `not_routed/off_grid` | **18** |
| `not_routed/no_path_in_region` | 3 |
| `not_routed/obstacle_budget_exceeded` | 3 |
| `not_routed/unsupported_geometry` | 3 |
| `routed` | 0 |

Eighteen `off_grid` refusals, on **three** boards. Every one is a genuine two-pad net.

## 3. The per-pad measurement

For each refusal, the router is asked for the signed nanometre displacement from the nearest
lattice line to the off-lattice pad centre, on each axis, and for the greatest common divisor of
the two pad-centre deltas — the largest lattice step at which the pair is representable at all.

| Quantity | min | median | max |
|---|---:|---:|---:|
| \|miss\| on an axis that misses (33 of 36 axes) | 7,500 nm | 55,000 nm | **120,001 nm** |
| \|miss_x\| across all 18 | 0 nm | 57,499 nm | 120,001 nm |
| \|miss_y\| across all 18 | 0 nm | 49,999 nm | 89,999 nm |

Fifteen of the eighteen miss on **both** axes. **The miss is not trivial.** The median axis miss is
55 µm against a 250 µm pitch — 22 % of a step, not a rounding artefact. These boards are simply not
laid out on the router's default lattice, and no pad is within a few nanometres of one.

The largest representable step, across the eighteen:

| Largest representable step | Refusals |
|---|---:|
| 1 nm | 8 |
| 3 nm | 2 |
| 5,000 nm | 4 |
| 10,000 nm | 1 |
| 15,000 nm | 1 |
| 20,000 nm | 1 |
| 30,000 nm | 1 |

Not one is a multiple of 250,000 nm, which is what makes each refusal correct.

## 4. The nanometre residues are KiCad's, not ours

Ten of the eighteen report a largest representable step of 1 or 3 nm. That is the signature of a
units defect, and it is worth being explicit that **it is not one of ours**.

Of the 72 pad-centre coordinates involved, 55 are exact micrometre multiples, 16 sit exactly
999 nm past one — that is, one nanometre short of the next — and one sits at 500 nm. Reading the
source bytes, the boards themselves carry millimetre literals one nanometre short of the round
value the designer placed the part at — the shape is `(at X.YZ9999 ...)` where `X.YZ` was
intended, so a literal of the illustrative form `12.339999` mm is 12,339,999 nm exactly, and
CopperMCP converts it to exactly that through `decimal.Decimal` on the literal token. (The
example is synthetic; no coordinate from the corpus is reproduced here.) The residue is
authored by KiCad's own float round-trip into the file, arrives in the board bytes, and is
reproduced faithfully.

So a single nanometre of board-authored coordinate noise collapses the largest representable step
from a real 1,000–150,000 nm to 1 nm, without moving the pad by any amount a designer could see.
The number is reported anyway, because it is true, and because a caller that snaps its own
coordinates can act on it.

## 5. The decisive test: does a finer lattice route any of them?

Each of the eighteen was re-previewed at exactly its own largest representable step — the finest
question worth asking, since no coarser step represents the pair and no finer one adds anything —
with `max_grid_nodes` raised to its **ceiling** of 500,000.

| Outcome at the finest representable step | Refusals |
|---|---:|
| `routed` | **0** |
| `not_routed/no_path` | 13 |
| `not_routed/grid_budget_exceeded` | 5 |

**Not one net routes.** The refusal moves; the outcome does not.

The two groups fail for different reasons, and both are structural:

- **Five** exceed the node budget. A lattice at 1, 3 or 5,000 nm needs between 1.8 × 10⁷ and
  3.3 × 10¹⁴ nodes to span the pad-to-pad bounding box alone — before the 10 mm region margin is
  added — against a ceiling of 5 × 10⁵. Lowering `grid_step_nm` trades one refusal for another.
- **Thirteen** refuse `no_path` with "the start pad cannot contain the routed width", and the cause
  is not the lattice at all: on that board the `Edge.Cuts` outline bounds a rectangle that does not
  contain the pads of these nets. It is a layout in progress whose footprints have not been brought
  inside its own outline, so no route can be proposed there at any pitch.

## 6. Verdict on B-088's hypothesis

**Refuted, in the form that mattered.** B-088's hypothesis was that the constraint is localised to
the lattice. On real boards the lattice is where the refusal is *reported* and not where the
constraint *is*: change the lattice to the best value the geometry allows and zero of eighteen
nets route. Thirteen are blocked by a pad outside the board outline and five by the node budget at
the pitch their own geometry demands.

This is the same shape of finding as ADR-0089's: one refusal standing in front of another and
being read as the whole answer. There it was `obstacle_budget_exceeded` hiding the lattice class;
here it is `off_grid` hiding an outline containment problem on thirteen nets.

Two secondary conclusions follow, and both are negative:

1. **There is no default `grid_step_nm` that fixes this corpus.** The greatest common divisor
   across all eighteen pairs is 1 nm, and 1,000 nm even after the KiCad residues are snapped away.
   A lattice that fine cannot span a routing region on any budget worth having.
2. **This is not a units or rounding defect on our side.** Section 4 traces every residue to the
   board bytes. There is no conversion bug to fix here, and a change that "fixed" it would be
   changing the board.

## 7. What this note changes, and what it does not

It changes the **refusal**, not the router. [ADR-0093](../adr/0093-actionable-off-grid-refusals.md)
makes `off_grid` carry the pad, the pitch and the exact miss, so a caller can distinguish the
situations this note had to run a separate experiment to distinguish. Routing semantics, the
lattice, `ROUTER_VERSION` and every published content address are untouched, and the 385 verdicts
are byte-identical before and after.

It deliberately does **not** propose a lattice change, a coordinate-snapping step, or an outline
inference. Snapping a pad centre onto a lattice would move copper the caller did not ask to move,
and would be the one direction of error this project refuses everywhere else: connectivity and
board outline under-approximate. The outline finding on thirteen nets is recorded here and left
for its own change.
