# ADR-0072: Conservative integer envelopes for foreign arc tracks

- Status: Accepted
- Date: 2026-08-06
- Owners: `@seunghyukchoe`
- Related: [Issue #67](https://github.com/seunghyukchoe/copper-mcp/issues/67), ADR-0011, ADR-0017,
  ADR-0018, [Arc track research](../research/kicad-arc-track-obstacles-v1.md)

## Context

The Board IR adapter has parsed KiCad's `(arc …)` copper track into a first-class `Arc` since
v0.1, and validates that its three control points are distinct and non-collinear. The single-layer
router then refused it: **any** arc on the requested layer produced a board-level
`unsupported_geometry` refusal, whatever net it belonged to.

That is the same shape of gap ADR-0017 closed for diagonal segments. The refusal was correct in
direction — it never claimed a wrong answer — but an arc track is not exotic. It is what KiCad
produces whenever a router or a human rounds a corner, and it is the first item named in the
milestone issue for widening the fail-closed Board IR subset.

ADR-0017 also supplied the shape of the answer: a conservative polygon envelope with exact integer
vertices, built as the Minkowski sum of the centreline with an axis-aligned square, is an honest
model for a shape the router cannot represent exactly, precisely because the over-approximation
errs toward refusal. What was missing was an envelope construction for a curve, and a rule for
which curves admit one.

## Decision

A **foreign-net arc spanning at most half a turn**, on the routed layer, becomes a conservative
polygon envelope obstacle instead of a board-level refusal.

- Every point of a minor arc projects onto its own chord segment and lies within the **sagitta**
  of it. So the arc track — the arc swept with a disc of its half width — is contained in the
  chord swept with a disc of *half width plus sagitta*, and therefore in the chord swept with an
  axis-aligned **square** of that radius. The envelope is exactly ADR-0017's construction with a
  larger radius, so it inherits that proof and its integer-vertex property unchanged.
- The half-turn test is **one integer dot product**, `(start − mid) · (end − mid) ≤ 0`. By the
  inscribed-angle theorem the angle at `mid` is at least a right angle exactly when the arc through
  `mid` is the minor one. No division, no square root, no tolerance. A semicircle gives exactly
  zero and is admitted; a degenerate full circle is refused by the same test with no special case.
- The sagitta is `r − h`, a difference of two square roots of rationals. Rather than evaluate it
  and then argue a rounding rule correct — the objection ADR-0017 raised against the oriented
  bounding box — the bound is the smallest integer satisfying a **sufficient integer condition**
  in which every substitution can only make it larger. It is an upper bound by construction, and
  measured within a nanometre of the true sagitta.
- The margin is unchanged from the straight-track path: the routed half width plus the stricter of
  the routed and obstacle net-class clearances. Offsetting an envelope that already contains the
  arc track is a superset of offsetting the arc track itself, so the inflation composes without a
  separate proof.
- Envelope vertices charge the obstacle-check budget one per inspected vertex, and each envelope
  counts against `max_obstacles`, exactly as zones, keepouts and diagonal segments do.

Two cases remain typed refusals, with distinct diagnostics.

- An arc spanning **more than half a turn** leaves its chord's own span, so the projection argument
  fails and a chord-based envelope would silently under-cover real copper. There is no honest
  envelope to build from three points alone.
- An arc on the **routed net**, on any copper layer. An obstacle may be over-approximated, but
  attachment copper must be under-approximated or the router would assert a connection the board
  does not have. An arc has no exact integer inner core yet, exactly as a diagonal segment had none
  before ADR-0018. The gate covers every copper layer, matching the existing same-net zone gate,
  because connectivity is a multilayer question.

The layered proposal adapter (ADR-0036) keeps its stricter blanket arc refusal. Its obstacle model
is rectangles only — it refuses even a diagonal foreign segment — so the blanket refusal is what
makes it safe. Relaxing it without first giving it an arc obstacle would let it route through arcs
on every layer silently.

## Consequences

- Boards with arc copper on other nets are routable instead of refused. A committed
  `arc-blocker.kicad_pcb` fixture detours around a `POWER` semicircle with zero KiCad 10.0.5
  errors, warnings and unconnected items, and is checked to be discriminating: routed straight, the
  same board makes KiCad report `shorting_items` as an error.
- **No Board IR field, schema, digest or diagnostic code changes.** The `Arc` was already in the
  IR and already canonicalized; this decision changes only how the router *models* it. Every
  pinned identity in `tests/test_golden_identities.py` is unaffected, and `ROUTER_VERSION` does not
  move, because no board the router already accepted changes geometry or identity.
- The envelope is loose for a large-sagitta arc. The square sweep extends the sagitta along the
  chord as well as across it, so on the committed semicircle the envelope is roughly four times the
  area of the arc's own bounding box. On a dense board that can refuse a corridor a tighter model
  would allow. That is the accepted direction of error. Splitting each arc at its own `mid` — a
  point already on the curve — and enveloping the two halves separately would cut this materially
  at the cost of two objects per arc, and is the natural next slice.
- The asymmetry between obstacle and attachment copper, first recorded in ADR-0017, now governs a
  second construct. Supplying an exact integer inner core for an arc is what would let a
  partially-arc-routed net be completed rather than refused.
- Three surfaces inherit the change without their own geometry, because `_prepare` is the single
  chokepoint they share: the Dijkstra oracle, the candidate path validator, and negotiated
  congestion. The candidate-pair clearance gate is unaffected — it compares candidates to each
  other and has never read board copper.

## Alternatives considered

- **Keep refusing arcs**: rejected. Arc copper is ordinary routed output, and ADR-0017 already
  established that a conservative envelope is the honest model for a shape the router cannot
  represent exactly.
- **Reuse `circuit_scene._arc_bounds` as a rectangle obstacle**: rejected as the primary model. It
  is already proven and handles arcs of any span, but it yields an axis-aligned bounding box, and
  ADR-0017 rejected the bounding box of a long diagonal as far too loose for exactly this reason.
  It remains the right tool for scene observation, where a bounding box is what the caller wants.
- **Convex hull of the three control points**: rejected as unsound. The arc bulges *outside* the
  triangle its control points span, so the hull is not a superset — it is the failure mode this
  envelope exists to avoid.
- **Compute the sagitta and round it outward**: rejected. It needs an explicit rounding rule over a
  difference of two irrational quantities, whose correctness has to be argued separately. The
  sufficient-condition formulation is an upper bound by construction and needs no such argument.
- **Subdivide the arc into many sub-arcs**: rejected for now. It multiplies the object count
  against `max_obstacles`, which turns a geometry question into a budget question — the same
  objection ADR-0017 raised against staircase decomposition. The two-way split at `mid` is the
  bounded version of this idea and is named above as the next slice.
- **Envelope major arcs too, using the companion arc's chord**: rejected. Past half a turn the
  containment argument does not hold, and an envelope that is not provably a superset is worse than
  a refusal.

## References

- [ADR-0011](0011-existing-copper-obstacles.md)
- [ADR-0017](0017-diagonal-segment-envelopes.md)
- [ADR-0018](0018-diagonal-attachment-cores.md)
- [ADR-0036](0036-board-ir-layered-proposal-adapter.md)
- [KiCad arc track research](../research/kicad-arc-track-obstacles-v1.md)
- [Deterministic A* routing baseline](../architecture/routing-baseline.md)
