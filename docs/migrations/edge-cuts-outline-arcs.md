# `Edge.Cuts` outline arcs, and one new number that can close the placement surface

Board IR now converts boards whose board outline is drawn with `Edge.Cuts` arcs — which is what a
rounded corner is, and what most real boards have. No schema version moves:
`BOARD_IR_SCHEMA_VERSION` stays `0.4.0` and `schemas/board-ir/0.4.0.schema.json` is byte-unchanged,
because nothing new enters `BoardIRSnapshot` (ADR-0105, D-229, ADR-0124).

## What newly converts

A root `gr_arc` on `Edge.Cuts` is now an outline edge, chained with `gr_line` segments into the
same single closed loop the adapter has always required. The arc is modelled as an **inscribed**
polyline: every vertex is an exact integer nanometre point inside the region the arc and its chord
bound, so the modelled board is never larger than the drawn one.

Four things still refuse, now each by its own sentence rather than one shared "unsupported curve":

| Construct | Message |
|---|---|
| an arc cutting *into* the board | `Edge.Cuts outline arcs cutting into the board are unsupported` |
| an arc spanning more than a half turn | `Edge.Cuts outline major arcs are unsupported` |
| `gr_circle` | `Edge.Cuts outline circles are unsupported` |
| `gr_curve` / `gr_bezier` | `Edge.Cuts outline Bezier curves are unsupported` |
| `gr_poly` | `Edge.Cuts outline polygons are unsupported` |

**If you match on refusal text**, the old string `Edge.Cuts outline arcs, circles and curves are
unsupported` is gone. So is `Edge.Cuts outline arcs, circles, polygons and curves are unsupported`.

Unchanged: the chaining epsilon is still **zero**. Two endpoints that miss by any distance at all —
including the 17 nm and 19 nm near-misses measured on real public boards — still refuse with
`Edge.Cuts outline must be one closed non-branching loop`, because closing a gap adds area no
drawn shape encloses. Footprint-local `Edge.Cuts` graphics still refuse.

## One new published number

`inspect_board_ir`'s `unmodelled_counts` map grows from nine entries to ten with
**`outline_inward_deviation_nm`**: an upper bound, in nanometres, on how far inside the drawn board
boundary the modelled one runs. It is bounded by 5,000 nm — KiCad's own `maxError` for polygonising
board graphics — and it is **0** for every outline drawn as a rectangle or as straight segments,
which is every board that converted before this change.

It is not a count. It is the same shape of quantity as `max_roundrect_rounding_nm`: a measured
approximation bound a caller can read and decline.

## What a caller must do

**If you read `unmodelled_counts` as a fixed key set**, add the new key. If you sum the map,
note that this entry is a *distance*, not a cardinality — summing it with counts was already
wrong for `max_roundrect_rounding_nm` and is wrong here for the same reason.

**If you call `preview_placement` or `preview_live_placement`**, a board whose outline is
approximated is now refused with `unsupported_geometry` and the message *"placement needs an exact
board outline and this board's is approximated"*. The response still carries a `snapshot_digest`,
because the board itself converted — this is a refusal about one surface, not about the board.

**The refusal is ordered after the snapshot compare-and-swap.** If you bind a request with
`expect_snapshot_digest` and that digest is stale, you get `stale_revision` — not
`unsupported_geometry` — even on an arc-outline board. A stale digest means your world-view is
wrong, and that is the fact you need first; a geometry verdict about a board you were not
looking at would be misleading. This matches `live_layered_route_preview`, which orders its own
board-property refusal after its snapshot CAS for the same reason.

The reason is a false-claim risk and not caution. `PlacementLegality.outline_containment` is
three-valued and publishes in **both** directions: `proven_inside` is sound against an
under-approximated boundary, and `violated` is not — copper sitting in the sliver between the
inscribed polygon and the true arc is inside the fabricated board and would be reported as crossing
its edge. Edge rules and region rules measure against the same boundary and have no `inconclusive`
value to degrade into. Refusing is the only answer that makes no claim.

**Routing is unaffected.** `preview_route` and the layered route preview read the outline as
routing *room*, where an under-approximation is the safe direction — less room is never a false
claim about where copper may go. Both already require an axis-aligned rectangular outline, which an
arc-derived ring is not, so neither changes behaviour on any board.

## What did not change

- No Board IR snapshot digest moves. No committed golden identity moves.
- No apply, token, CLI, DRC, routing, or board-write behaviour changes.
- No board with a segment- or rectangle-drawn outline behaves differently in any way.
- The frozen own 18-save corpus converts 15/18 before and after, with the new number **0** on all
  15 (B-135).

## What this did not buy

**No public board converts.** The ten-board licence-clean public cohort was 0/10 before and is
0/10 after; the eight boards that refused at this gate now refuse behind it, six of them at
ADR-0095's copper-text wall. That was predicted before the code was written and is recorded in
B-134 and B-135. This migration note describes a real capability with a real proof, and it is not
a claim that any third-party board converts today.
