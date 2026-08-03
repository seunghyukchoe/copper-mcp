# ADR-0024: Typed placement intent, validated by a deterministic legalizer

- Status: Accepted
- Date: 2026-08-04
- Owners: `@seunghyukchoe`
- Related: ADR-0005, ADR-0015, ADR-0019, ADR-0022

## Context

Everything this server can change on a board, it changes by routing copper between pads that
are already where someone else put them. Placement is the other half, and ADR-0015 reserved it
as the second part of the long-term perception/action contract.

The field has converged on generator-proposes / deterministic-legalizer-disposes, and the
reinforcement-learning placement literature is explicit that learned policies need legalization
most. What none of it does is expose the *intent* as a fail-closed typed schema across a
request boundary. That is the unoccupied ground here, in the same way that treating DRC as
retained evidence was.

Two facts about this repository shaped what v0.1 can honestly claim. Board IR has **no
footprint object and no courtyard geometry** - pads are flattened to board level and carry no
parent reference. And this repository's own board draws **no courtyards at all**: its 26
footprints emit only `fp_rect` and `fp_circle` on `F.Fab`, and its project sets
`missing_courtyard: ignore`, so KiCad's own courtyard check is equally blind on it.

## Decision

Placement intent is a **typed rule language that cannot express an illegal result**, and v0.1
**validates and snaps model proposals** rather than solving for positions.

- **Seven rule kinds**: proximity, alignment, symmetry, edge, region keep-in/keep-out, discrete
  orientation, and side. Every rule names objects by the references a scene already handed out
  and carries exact integer parameters. There is no rule that places something at a coordinate
  and none that permits an overlap - legality is simply not vocabulary in this language.
- **Proposals are ref-anchored.** A model says "put this 2.5mm right of that's east edge",
  never "put this at (48200000, 17000000)". Absolute coordinates in a candidate are always
  derived here, from geometry this code read itself, then snapped to `placement_grid_nm`.
- **Orientation is four-valued** because the Board IR adapter already rejects non-orthogonal
  footprint transforms. That is an existing invariant being reused, not a new restriction.
- **Footprint identity is recovered out of band** and joined to Board IR pads, exactly as the
  scene reads board text out of band. The join is **total**: native UUIDs join directly, and
  pads without one are matched by reproducing the adapter's documented derived-id hash from the
  same source bytes. A view that cannot account for every pad refuses rather than guessing.
  Candidates therefore bind to **both** digests - `snapshot_digest` for the geometry and
  `board_revision` for the grouping, which is not covered by the snapshot.
- **Pad overlap is three-valued.** Bounds over-approximate a pad and cores under-approximate
  it, so disjoint bounds *prove* clearance and overlapping cores *prove* collision. Everything
  between is `inconclusive` and says so. Reporting inconclusive as a violation would reject
  legal boards; reporting it as clear would claim a proof nobody has.
- **Courtyard overlap is a one-value `not_modelled` literal.** There is no vocabulary for a
  courtyard that was checked, so a candidate can never imply a check this version does not
  perform - the same device as the scene's `trust` field.
- **Tolerance is explicit or absent.** `satisfied_within_tolerance` is reported only when the
  caller supplied a `tolerance_nm`. An unstated tolerance means exact, so a one-nanometre
  residual is a violation and says so rather than being quietly absorbed.
- **`infeasible_constraints` and `budget_exhausted` never collapse.** The first is a proof that
  no placement satisfies the rules as written; the second is an admission that the work ran out.
  Only *syntactic* contradictions are claimed as infeasible - two side rules on one object, two
  disjoint orientation sets, opposing edge rules - because anything needing search would be
  reporting ignorance as certainty.
- Resolution order is by reference, lowest first, recorded as `ordering_policy`
  **`validate-snap-v1`** so a later solver can never be mistaken for this one.

## Consequences

- **The inconclusive rate is negligible in practice.** Measured on CopperTone: of 1,360
  different-net same-layer pad pairs, bounding boxes settle **1,359** and exactly **one** is
  inconclusive - 0.07%. That single pair is an oval against a roundrect whose boxes clip at a
  corner both shapes round away, on a board `kicad-cli pcb drc` calls clean. **Exact pad-shape
  geometry is therefore not worth its complexity in v0.1**, and a test pins the rate so that
  conclusion is revisited if it ever moves.
- All 55 CopperTone pads have a modelled core. Circular pads needed the inscribed-square
  construction the router already uses; the stadium formula degenerates to a zero-width line
  for a circle, which would have left ten pads unable to prove any collision at all.
- **The three legality checks are independently corroborated by KiCad.** On the committed
  fixtures KiCad names a different violation type for each - `shorting_items`,
  `items_not_allowed`, `copper_edge_clearance` - which is evidence that they are genuinely
  separate checks rather than one flag wearing three names. The binding direction is one-way:
  anything called `violated` must also be a DRC error, while `inconclusive` may map either way.
- **v0.1 previews only and never applies.** Moving a footprint moves its pads, which changes
  connectivity and invalidates every route bound to the same base revision. Applying a
  placement is a separate operation that does not exist, and observing a scene *after* a
  hypothetical placement is future work - it needs the moved board rendered and re-converted,
  which is the apply path.
- **A side change is refused** as `unsupported_geometry`. Flipping a footprint mirrors it, which
  is a different transform from the rigid moves modelled here; a half-correct mirror would put
  copper on the wrong side of the board.
- There is **no MCP or CLI surface yet**. The contract and legalizer land first so the rule
  vocabulary is exercised before it is published.

## Prior art

The split is the **generator-proposes / legalizer-disposes** pipeline that the analytical and
learned placement literature has converged on, including the DAC'24 complex-constraints work.
The contribution here is not the pipeline but the boundary: a typed, fail-closed intent schema
whose vocabulary makes an illegal request unrepresentable, rather than a free-form objective
handed to a solver that then has to be policed.

The three-valued verdict is the **direction-of-error** discipline this repository already uses
for routing obstacles, applied to a decision rather than to a search: over-approximate when
being wrong means being cautious, under-approximate when being wrong means making a false
accusation, and name the gap between them instead of collapsing it.

## Alternatives considered

- **Solve for positions in v0.1**: rejected for now, not forever. A legalizer is needed under
  either architecture, and a solver written before a trustworthy legalizer has nothing to check
  itself against. The `ordering_policy` field and a future backend seam keep the door open.
- **Add footprints and courtyards to Board IR now**: rejected for v0.1. Under ADR-0005 it costs
  a schema version bump, new fixtures, migration guidance, and changes the `snapshot_digest` of
  every board ever converted - for geometry this repository's own board does not contain. The
  right moment to pay that is when apply lands.
- **Two-valued pad overlap**: rejected. Measured, a bounding-box-only rule would call a
  DRC-clean board illegal, and a core-only rule would miss real collisions between pads whose
  cores happen not to meet.
- **Let a rule carry an absolute position**: rejected, and a test asserts the field cannot be
  smuggled in. It would make the legalizer advisory rather than authoritative.
- **Report a violated *rule* as an illegal placement**: rejected. Rules express preference and
  legality expresses possibility; conflating them would make a candidate impossible to produce
  for any board that does not already satisfy every stated wish.
- **Treat an unstated tolerance as "close enough"**: rejected. A default tolerance is a silent
  licence to be wrong by an amount nobody agreed to.

## References

- [ADR-0005](0005-canonical-board-ir.md)
- [ADR-0015](0015-bounded-circuit-schematic-delivery.md)
- [ADR-0022](0022-circuit-scene-observation.md)
- [Roadmap M4](../roadmap.md)
