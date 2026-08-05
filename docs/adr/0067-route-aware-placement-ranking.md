# ADR-0067: Keep route-aware placement ranking private, bounded, and opt-in

- Status: Accepted
- Date: 2026-08-05
- Related: ADR-0001, ADR-0005, ADR-0006, ADR-0024, ADR-0034, ADR-0055

## Context

The bounded placement solver ranks legalizer-issued candidates by violated intent rules, a
same-net all-pairs Manhattan proxy, and moved-footprint count.  The proxy is deterministic and
cheap but is not routing evidence: it ignores pad access, obstacles, exact A* completion, and
routed length.  Replacing it globally would silently alter existing advisory solver behavior and
would risk giving policy code authority to invent placement or copper geometry.

Published PCB placement work evaluates routability using post-route metrics such as unrouted nets,
design-rule violations, vias, and routed wire length; it also retains a separate legalization
step.  That supports using bounded downstream-router evidence as a ranking signal, but does not
make a one-probe result a DRC or whole-board completion proof.

## Decision

Add a closed `PlacementScoringPolicy` enum:

- `same-net-manhattan-v1` remains the default and preserves the prior ranking fields and order;
- `route-aware-astar-v1` is explicit Python-level opt-in only, with closed integer
  `RouteProbeSettings` and the existing `AStarSettings` work ceilings.

For an opt-in score, the solver first obtains an immutable legal candidate from
`evaluate_placement`, then verifies its self-authenticating identity and exact base-snapshot and
placement-view revision bindings before any projection or router work.  A narrow read-only adapter projects only that candidate's derived
footprint origins/orientations, owned pad centres/angles, and courtyard rings into a fresh
in-memory, self-verifying Board IR snapshot.  It does not render KiCad, write a board, produce a
route patch for publication, or support side flips.  The existing deterministic A* router then
independently probes a stable, bounded set of two- through nine-pad nets sharing a signal layer.
Every scored candidate shares one operation-wide probe meter; the fixed per-candidate and total
caps are recorded with the evidence and refuse additional router work deterministically.

The lexicographic route-aware rank keeps violated intent rules first, then minimizes unrouted
probes, internal router violations, exact independent routed wire length, the historical Manhattan
proxy, and moved-footprint count.  A cancellation/deadline during projection or probing withholds
the complete score.  A router diagnostic counts as an unrouted probe; no fallback geometry is
manufactured.

### Tier semantics

The tiers are not interchangeable and each says exactly one thing.

- **`unrouted_probes`** counts every probe that produced no route.  It is the superset and it is
  deliberately *not* a routability verdict.
- **`refused_probes`** is the subset of those where the bounded router never finished its search: a
  grid, budget, support, or cancellation refusal.  Only `no_path` - a completed search over the
  reachable space - is left outside it.  The distinction is load-bearing rather than theoretical:
  on the B-078 fixture at the committed 1,000,000 nm probe grid, **all four** non-completing probes
  are `off_grid`, meaning the pad-centre delta is not divisible by the grid step.  A single
  collapsed counter would have presented a router limitation as "this placement cannot be routed".
- **A candidate the projection cannot represent** is recorded as having refused *every* probe it
  would have attempted, and is charged for them.  Recording one failed probe instead was a
  lexicographic inversion: `wire_length_nm` is a minimize tier, so the zero emitted by an
  unrepresentable candidate is the best possible value, and it outranked a candidate that was
  genuinely probed and tied on unrouted probes.  The probe set is a function of net membership and
  layer stackup only, both invariant under a pose projection, so the count is exact and needs no
  projection to compute.
- **Structural-integrity violations are not probe failures.** A candidate whose placements are not
  unique, that does not cover the view footprint set, or whose snapshot and view footprint sets
  disagree, refuses as a binding error and escapes the failed-probe handler entirely.  Only the
  narrow-support limits - a side flip - are scored as refused probes.

### Score provenance

Following ADR-0024's `ordering_policy` precedent, evidence names what produced it.
`PlacementSolveResult` and every `RankedPlacement` record their `PlacementScoringPolicy`, and
`RouteAwareEvidence` carries `estimator_id` plus a digest over the whole `RouteProbeSettings`,
including the nested `AStarSettings`.  Without this, a one-probe observation and an eleven-probe
observation of the same candidate are indistinguishable after the fact, which - as the B-078
correction below shows - is exactly the confusion that produced an overstated claim.  The benchmark
report binds the same configuration into its `run_id`.

## Consequences

- Placement legality, candidate identity/shape, source/revision binding, and default behavior are
  unchanged.  Stale or tampered candidates refuse before virtual-snapshot projection; the
  legalizer remains the only component that can issue a placement candidate.
- The result records `RouteAwareEvidence` alongside an internal ranked entry, not in the public
  placement candidate.  No MCP schema, route-bundle contract, apply authority, or board mutation
  is added.
- Evidence is deliberately per-net and independent.  It is not negotiated congestion, overflow,
  multi-net routing, DRC, electrical validation, manufacturing validation, or an optimal-placement
  claim.
- B-078 predeclares a three-replay acceptance criterion on a CopperMCP-original Apache-2.0 audio
  fixture: route-aware selection must improve the measured independent A* wire length by at least
  10%, or strictly reduce unrouted probes.  Failure is recorded as a negative result - the report
  is written with `criterion.passed` false and the script exits non-zero - and does not justify
  integration.  Harness integrity failures (fixture provenance, replay determinism, a retained
  candidate escaping legality) still refuse outright, because a broken harness measures nothing.

## Correction, 2026-08-06: what the B-078 comparison actually compares

The original wording of this ADR, of `benchmark_route_aware_placement.py`, and of the B-078 ledger
row described the measurement as two policies ranking **one shared legalizer-issued candidate set**.
That is not what happens, and the difference is not cosmetic.

The score feeds `solver._state_key`, which orders the beam.  The surviving beam decides which
successors are generated in the next round, so the scoring policy changes **which candidates are
ever explored**, not merely how a fixed set is presented.  Measured on the B-078 fixture: at
`max_ranked=64` the two retained sets intersect in **one** candidate; at the committed
`max_ranked=8` they intersect in **zero** - entirely disjoint.  The recorded 23.81% improvement is
therefore a different-search-trajectory result.

The architecture is not the defect; the claim was.  A route-aware score *should* steer the search -
that is the point of adopting it - and the numbers are reproducible.  What was wrong was calling a
trajectory difference a re-ranking result.  Three things follow.

- **The benchmark now records both measurements separately.** `search_comparison` is the two
  bounded searches, labelled as such.  `rerank_comparison` is the genuine re-ranking: one fixed
  candidate set - the union of what the two searches retained - scored under both policies, with
  the argmin of each compared.  On this fixture the re-ranking measurement independently reproduces
  the same two choices and the same 23.81%.  Note that the union is itself drawn from the two
  searches, so it is a shared-set comparison, not a search-independent one.
- **`score_placement_candidate` exists so the re-ranking question can be asked at all.**
  `solve_placement` structurally cannot answer it.
- **Single-probe numbers are labelled with their probe count, and the honest whole-fixture number
  is recorded beside them.** The ranked search probes one net per candidate while the fixture has
  eleven probeable nets, so "zero unrouted probes" was a one-net statement.  Probed against every
  net, both chosen candidates show 4 unrouted probes - all four `off_grid` refusals - and the
  ordering **reverses**: the route-aware choice routes 391,000,000 nm against the baseline's
  359,000,000 nm.  The one-probe signal that steered the search does not survive being asked a
  broader question, and the ledger now says so in the claim itself.

## Sources and verification

- Cheng, Ho, and Holtz, [*Net Separation-Oriented Printed Circuit Board Placement via Margin
  Maximization*](https://arxiv.org/abs/2210.14259), motivates retaining legalization while judging
  PCB placement using routed wire length, violations, vias, and unrouted nets.  This decision uses
  only the bounded router's completion count and exact wire length; it does not claim the paper's
  global/multilayer results.
- KiCad's [PCB S-expression board-format reference](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/)
  documents footprint/pad placement structure used by the Board IR projection boundary.
- KiCad's [PCB Editor DRC reference](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#design-rules-checking)
  distinguishes connection and clearance checks, and explains why the route probe is not presented
  as DRC evidence.
- [`route-aware-placement-policy.md`](../research/route-aware-placement-policy.md) records the
  source interpretation and benchmark scope.  Tests cover default compatibility, virtual-snapshot
  projection, legal-candidate-only ranking, deterministic replays, and B-078.
