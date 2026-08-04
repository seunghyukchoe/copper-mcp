# ADR-0051: Narrow exact obstacle queries with a conservative spatial index

- Status: Accepted
- Date: 2026-08-05
- Owners: `@seunghyukchoe`
- Related: [ADR-0006](0006-bounded-deterministic-astar.md), [ADR-0041](0041-routing-safety-remediation.md), [B-033](../ledgers/benchmark-ledger.md)

## Context

The integer A* and benchmark Dijkstra oracle previously scanned every prepared rectangle and
polygon for every edge-legality and proximity query. That keeps the predicates simple, but the
number of exact relations grows with the product of search states and board obstacles. The
router needs a bounded acceleration that cannot turn a missed candidate into a falsely legal
route or make candidate ordering depend on hash iteration.

## Decision

Build one immutable, query-only `ConservativeSpatialIndex` per obstacle kind during request
preparation. It is a deterministic uniform grid over conservative closed bounds. Entries retain
their canonical source ordinal, buckets are sorted, and query results are returned in that same
ordinal order. Boards with fewer than eight entries, or whose objects would exceed a bounded
bucket budget, use the canonical linear sequence instead.

The index is only a candidate filter. Rectangle crossing, polygon offset, and proximity
predicates remain the exact integer authorities and continue to charge the existing
`max_obstacle_checks` budget and cancellation checkpoints. The same ceiling remains fail-closed;
because an indexed query can avoid irrelevant exact predicates, a board may now complete under a
ceiling that the legacy scan would exhaust. Candidate identity records the change as
`astar-grid/0.6.0` and `orthogonal-a-star-spatial-index-v1`.

## Evidence and limits

B-033 differentially replays indexed and forced-linear A*/Dijkstra searches over a deterministic
16-obstacle detour fixture. Route geometry, cost, and expanded-state semantics match while exact
checks fall from 19,982 to 308 for A*. A 512-entry/256-query microbenchmark records 131,072
legacy relations versus 31 indexed relations (99.9763% fewer) and 256/256 exact query matches;
the measured wall-clock speedup is fixture- and host-specific. This is not a congestion,
rip-up/reroute, FreeRouting, KiCad DRC, electrical, or fabrication claim.

## Consequences

- Dense boards spend exact predicate work on spatially plausible objects only.
- Small and pathological boards retain the old deterministic linear behavior through fallback.
- Candidate IDs and router policy/version change because recorded exact-check metrics can change.
- A future bounded rip-up/reroute coordinator must preserve this index's immutable snapshot and
  candidate-only validation boundary rather than mutating buckets during search.

## References

- [Guttman, *R-trees: A Dynamic Index Structure for Spatial Searching*](https://doi.org/10.1145/602259.602266)
- [FreeRouting architecture](https://raw.githubusercontent.com/freerouting/freerouting/master/docs/architecture.md)
