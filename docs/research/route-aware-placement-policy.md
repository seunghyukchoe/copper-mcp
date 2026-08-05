# Route-aware placement-policy research

**Reviewed:** 2026-08-05

## Question

Can a bounded placement search use downstream routing results instead of only same-net Manhattan
distance without turning placement policy into a geometry writer or claiming board signoff?

## Sources actually used

- Cheng, Ho, and Holtz, [*Net Separation-Oriented Printed Circuit Board Placement via Margin
  Maximization*](https://arxiv.org/abs/2210.14259) is primary PCB-placement literature.  It uses a
  routing-aware placement flow with a separate legalization stage and reports routed wire length,
  rule violations, vias, and unrouted nets.  This supports the narrower choice here: retain an
  independent legalizer and inspect a bounded post-placement routing signal.  It does **not**
  validate a one-net A* probe as whole-board routability.
- KiCad's official [PCB S-expression format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/)
  was used to bound the in-memory transform to footprint/pad placement information already modeled
  by Board IR.  The adapter intentionally does not serialize this projection.
- KiCad's official [Design Rules Checker documentation](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#design-rules-checking)
  was used to distinguish real DRC—connections, clearance, and configured constraints—from this
  policy's private A* evidence.  No KiCad CLI run was needed or claimed for B-078.

## Design conclusion

Use an explicit `route-aware-astar-v1` scorer only after `evaluate_placement` has issued a legal,
immutable candidate.  Verify the candidate's self-authenticating identity plus its exact snapshot
and placement-view revision bindings before rebuilding a virtual canonical Board IR snapshot from
its derived poses.  Then call the existing bounded A* router independently for a deterministic
subset of nets.  Each solve shares one deterministic operation-wide probe cap as well as its
per-candidate cap, and records both the meter use and limit in its evidence.  Rank fewer unrouted
probes first, followed by internal violations and exact routed length.  Keep the historical proxy
and default policy intact.

The probe is deliberately independent per net.  It cannot measure negotiated congestion, overflow,
combined route conflicts, external-router behavior, KiCad DRC, or electrical/fabrication quality.
It also cannot flip a footprint side; unsupported poses produce no invented completion evidence.
Tampered or stale candidates do not reach this projection or the meter, and cancellation/deadline
checks withhold partial scoring before each charged router call.

## Predeclared B-078 criterion

Fixture: `benchmarks/audio/fixtures/ne5532-stereo-summing-routing-v1.kicad_pcb`, a
CopperMCP-original Apache-2.0 zero-copper NE5532-class topology fixture.  Three deterministic
replays use identical bounded placement and A* settings.

The policy is accepted only if every retained placement candidate remains legal and, relative to
the default Manhattan-selected candidate, the route-aware-selected candidate either:

1. reduces independent bounded-A* routed wire length by **at least 10%**, or
2. strictly reduces the independent probe's unrouted count.

The committed result passes the first condition (42,000,000 nm to 32,000,000 nm; 23.8095%).  This
is a benchmark-specific selection result, not a universal policy-quality guarantee.

### What the comparison is, corrected 2026-08-06

The two policies do **not** rank one shared candidate set.  The score orders the solver beam, so it
decides which successors are explored; the two retained sets are disjoint at the committed
`max_ranked=8` and intersect in one candidate at `max_ranked=64`.  The number above is therefore a
different-search-trajectory result.

B-082 records two further measurements alongside it.  A genuine re-ranking over one fixed
16-candidate set - the union of what both searches retained - independently reproduces the same two
choices and the same 23.8095%.  And because the ranked search probes **one** of the fixture's
**eleven** probeable nets, "zero unrouted probes" is a one-net statement: probed against all eleven,
both chosen candidates leave four unrouted, every one of them an `off_grid` refusal rather than a
proven-unroutable net, and the wire-length ordering reverses (359,000,000 nm baseline against
391,000,000 nm route-aware).  A one-probe ranking signal is a search heuristic, and this fixture
shows it does not generalize to the broader question by itself.
