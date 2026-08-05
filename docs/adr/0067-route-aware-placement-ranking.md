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
  10%, or strictly reduce unrouted probes.  Failure would be recorded as a negative result and
  would not justify integration.

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
