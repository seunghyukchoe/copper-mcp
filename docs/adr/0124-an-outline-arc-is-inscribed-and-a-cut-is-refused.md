# ADR-0124: An outline arc is inscribed, and an arc that cuts into the board is refused

- Status: Accepted
- Date: 2026-08-28
- Owners: `@seunghyukchoe`
- Related: [Issue #188](https://github.com/seunghyukchoe/copper-mcp/issues/188),
  [D-229](../ledgers/decision-ledger.md), [R-180](../ledgers/risk-register.md),
  [SEC-166](../ledgers/security-ledger.md),
  [B-134](../ledgers/benchmark-ledger.md) and B-135,
  [the outline-arc research note](../research/kicad-edge-cuts-outline-arcs-v1.md),
  [ADR-0076](0076-segment-assembled-edge-cuts-outline.md) (the segment ring this extends, and the
  zero chaining epsilon it fixed),
  [ADR-0072](0072-conservative-arc-track-envelopes.md) (the same arc, approximated in the
  *opposite* direction, for the opposite reason),
  [ADR-0080](0080-chamfered-and-circular-courtyards.md) (the bracketing discipline this declines
  to reuse, and why),
  [ADR-0095](0095-copper-text-has-no-derivable-envelope.md) (the wall behind this one),
  [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md) (invoked and found not to
  apply)

## Context

`Edge.Cuts` outline curves are the front gate of issue #188: eight of ten licence-clean public
boards refuse there and nowhere earlier. [B-114](../ledgers/benchmark-ledger.md) counted **97**
curve primitives across the cohort and could say no more about them than "arc, circle and bezier".
[ADR-0076](0076-segment-assembled-edge-cuts-outline.md) named the reason arcs were left out and
left the reason standing:

> An outline arc needs an *inscribed* approximation, and whether the chord is inscribed depends on
> which side of the ring the arc bulges toward.

This decision answers that, and answers it with the cohort measured first rather than with a
general algorithm written for boards nobody has.

### What the boards actually carry (B-134)

- **All 97 of the curve primitives are `gr_arc`.** Zero `gr_circle`, zero `gr_curve`, zero
  `gr_bezier`, zero `gr_poly` on `Edge.Cuts`, on any of the ten boards.
- Their child grammar is closed and uniform: `start`, `mid`, `end`, `stroke`, `layer`, `uuid` on
  all 97, with **no positional atom on any of them**.
- Counting arcs as edges and chaining by exact endpoint coincidence, **7 of 10** boards form
  exactly one closed non-branching loop. Of the 51 arcs on those seven, **all 51 are minor**, all
  51 have rational (non-integer) circumcentres, radii run 1.0–5.0 mm and sweeps 45°–90°.
- **38 of the 51 are convex** with respect to the ring's interior and **13 are concave** — arcs
  cutting *into* the board — falling on exactly two boards.

## Decision

### 1. The direction of error is fixed before the geometry

The board outline is routing **room**, not an obstacle. An obstacle may be over-approximated,
because a larger obstacle only refuses more. An outline may only be **under**-approximated,
because a larger outline hands a caller area the fabricated board does not have. This is
[ADR-0072](0072-conservative-arc-track-envelopes.md)'s sagitta envelope run in the opposite
direction, and it is not that envelope inverted — an upper bound turned upside down is not a lower
bound, it is an unproved guess.

### 2. A convex arc is inscribed, and one convex region carries the whole proof

Let `S` be the region bounded by an arc's chord and the arc itself. It is exactly

    S  =  disc(O, r)  ∩  half-plane containing `mid`

an intersection of two convex sets, therefore **convex** — for a minor arc and a major one alike.
That single fact is what makes this tractable: **a polyline whose vertices all lie in `S` has all
of its edges in `S` too**, so containment is decided per vertex by two exact integer predicates and
never needs a segment-versus-circle test.

For a **convex** arc — bulging away from the interior — `S` is board material, so a polyline
through `S` yields a region contained in the true one. Vertices are positioned with floating point,
because a *count* and a *position* need no proof, and then **verified** in exact integer
arithmetic. A vertex that fails verification is pulled toward the centre, and if it still fails it
is **dropped**. Dropping is the only correction offered and there is deliberately no step that
moves a vertex outward, so a bug in this module can lose a vertex and cannot grow a board.

Subdivision is sized by a sagitta bound of **5,000 nm**, which is KiCad's own
`pcbIUScale.mmToIU( 0.005 )` `maxError` for polygonising board graphics. Borrowing the constant is
deliberate — it is the deviation KiCad itself works with when it polygonises the same shape for
its own DRC. The difference is the **sign**: KiCad's bound is two-sided and this one is strictly
inward.

### 3. A concave arc is refused by name

For a **concave** arc — one cutting into the board — `S` is exactly the material the cut removed. A
polyline through `S` claims removed material as board: the forbidden direction. The safe
construction is the mirror image, a polyline running *outside* the circle along tangent segments,
and its safe region is `complement(disc) ∩ half-plane`, which is **not convex**. Vertex-level
predicates are therefore not sufficient there; every *edge* would need an exact
segment-to-circle distance test. That is a different proof obligation, and

**it would have bought nothing measurable.** Both boards carrying concave arcs refuse *in front of*
the outline assembly — one on copper text, one on copper layer kind — so B-135 measured concave
support as worth exactly zero conversions on this cohort. Refusing it by name, with the exit
condition written down, is the honest trade; building it would have been a proof obligation taken
on for no measured gain.

**Exit condition.** Implement the tangent construction when a board exists whose *only* remaining
refusal is a concave `Edge.Cuts` arc. The obligation is an exact per-edge test — for each polyline
edge `(P, Q)`, `dist²(O, segment PQ) ≥ r²` in exact rationals, with the foot-of-perpendicular case
separated from the endpoint case — plus the same integer verification and the same
drop-never-nudge failure mode.

### 4. Which side is the interior is read from the chord ring, and the ring is checked first

Convex and concave are not properties of an arc. They are properties of an arc *and the ring it
belongs to*. The cycle is therefore assembled from chords — the chord and the arc share their
endpoints, so topology cannot depend on the bend — and the chord ring's signed area is what says
which side of each chord the board is on.

That reading is only trustworthy if the chord ring is simple, so **the chord ring is validated for
self-intersection before its orientation is used**. A non-simple ring has no consistent interior,
and reading a direction out of one could classify a concave arc as convex, which is precisely the
over-approximation the whole path exists to prevent. This is the one place where skipping a check
would have produced a wrong answer rather than a refusal.

### 5. Major arcs, and the three closed shapes, stay refused — each by its own sentence

- **Major arcs** are refused because the premise of §4 stops holding: a chord ring is a
  small perturbation of the true region only while each edge stays on one side of its chord, and a
  major arc doubles back past the centre. Zero of the 51 measured arcs are major.
- **`gr_circle` and `gr_poly`** are *closed shapes*, not edges. Admitting one is a topology
  question — how it composes with the other shapes, and whether a second closed shape is a hole or
  a second board — which [ADR-0076](0076-segment-assembled-edge-cuts-outline.md) left refused and
  this does not reopen.
- **`gr_curve` / `gr_bezier`** are cubic Béziers, whose convexity is **not global**: one curve may
  carry an inflection, so the single-verdict test of §4 does not exist for it.

Each refusal is a constant string selected by an equality test against the source token, so it
names the construct without echoing a byte of the board. "Unsupported curve" named a category; these
name a construct and a reason.

### 6. The zero chaining epsilon stays zero, and the measurement is why it is worth restating

KiCad closes a sub-tolerance gap for you; closing one adds area no drawn shape encloses, so this
adapter refuses. B-134 makes that concrete rather than theoretical: two of the ten boards leave
endpoints unpaired by **17 nm** and **19 nm**, and one by **69.6 mm**. Supporting arcs does not
soften the rule — it only means an arc's endpoints are held to it too. There is no
under-approximating repair for a gap: every closure adds area.

### 7. The shrinkage is published, and the one surface that cannot live with it refuses

`ConversionResult.outline_inward_deviation_nm` is an upper bound on how far inside the drawn
boundary the modelled one runs — computed with the radius rounded up and the polyline's closest
approach rounded down, so it can overstate and never understate. It is **zero** for every outline
drawn as a rectangle or as segments, because each of those vertices *is* a drawn point.

`placement_preview` refuses a non-zero value. Its legality contract publishes `outline_containment`
in **both** directions — `proven_inside` from an over-approximating pad box, `violated` from an
under-approximating pad core crossing the boundary — and only the first survives a boundary that is
itself under-approximated: copper in the sliver between the inscribed polygon and the true arc is
inside the fabricated board and would be reported as crossing its edge. Edge rules and region rules
measure against the same boundary and fail the same way. The request is refused whole rather than
three verdicts being quietly degraded, because a degraded verdict is a claim and a refusal is not.

**Where that refusal sits is part of the contract, not an implementation detail.** It is ordered
*after* the snapshot compare-and-swap, never before it. A caller holding a stale snapshot digest
has a wrong world-view, and the first thing it must learn is *that* — told
`unsupported_geometry` instead, it would conclude the board it thinks it holds cannot be placed,
which is a false statement about a board it was not looking at. The rule is not invented here:
`live_layered_route_preview` states it in prose ("a stale converted snapshot is rejected before
routing") and orders its own board-property refusal, `has_exactly_two_signal_layers`, after its
own snapshot CAS. The general form is that a **compare-and-swap precondition outranks every
refusal about the board's content**, because the CAS answers "are we even talking about the same
board" and nothing downstream means anything until it does.

Routing does not read the number and does not need to: less room is never a false claim about
where copper may go.

## Alternatives rejected

**Carry the arc exactly in Board IR, as a first-class outline edge.** This is the *right* long
answer and it is what a consumer needing the outline in both directions would require. It is a
schema move — `additionalProperties: false` on the contour — and every consumer would have to learn
a second geometry kind. It was rejected for this slice on measurement: B-135 measured the whole
capability at **zero conversions**, so paying a schema version and a migration for a field whose
only reader today would be the same refusal is a cost with nothing bought. The exit condition is a
consumer that needs the number rather than the flag.

**Bracket the outline the way [ADR-0080](0080-chamfered-and-circular-courtyards.md) brackets a
courtyard** — carry an inner and an outer ring and answer `inconclusive` in the band. Rejected
because a courtyard is one object with one consumer, while the outline is a *single* contour the
Board IR schema pins to exactly one ring; a second ring is the same schema move as above with twice
the surface.

**Degrade `outline_containment` from `violated` to `inconclusive` and keep the placement surface
open.** Rejected because it is only two thirds of a fix: edge rules and region rules read the same
boundary and have no `inconclusive` value in their three-valued status to degrade into. A surface
that is honest in one verdict and silently wrong in two others is worse than one that declines.

**Put the deviation on the Board IR content instead of the conversion result.** Every consumer that
publishes a two-directional outline claim reaches the outline through a live conversion —
`placement_preview` converts the board itself and is the only caller of `build_placement_view` —
so the result carries the fact to exactly the place that needs it, with no schema move. The residual
is recorded rather than hidden: a caller that round-trips a snapshot through JSON loses the
disclosure ([R-180](../ledgers/risk-register.md)).

## Consequences

- Boards with rounded corners — which is most boards — convert. The eight public boards that
  refused at this gate no longer do, and B-135 measures where each goes instead.
- **No public board converts.** Six of the eight land on ADR-0095's copper-text refusal, which is
  where B-114 predicted they would land in 2026-08-14 and where B-118 measured them behind a mask.
  This slice replaces that mask with a real implementation and reaches the same wall.
- The placement surface declines boards it would previously never have seen, and says why.
- Concave outline arcs, major outline arcs, outline circles, polygons and Béziers, and
  footprint-local `Edge.Cuts` graphics all remain refused, now each by its own sentence.
