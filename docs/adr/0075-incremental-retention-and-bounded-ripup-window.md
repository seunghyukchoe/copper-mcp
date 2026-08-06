# ADR-0075: Reconstruct the congestion ledger incrementally and bound rip-up by a spatial window

- **Status:** Accepted
- **Date:** 2026-08-06
- **Owners:** `@seunghyukchoe`
- **Related:** [Issue #64](https://github.com/seunghyukchoe/copper-mcp/issues/64),
  [ADR-0051](0051-conservative-spatial-index.md),
  [ADR-0055](0055-bounded-negotiated-congestion.md),
  [ADR-0073](0073-declared-negotiation-policy-slots.md),
  [Incremental spatial index research](../research/incremental-spatial-index-v1.md),
  D-152, B-089, R-115

## Context

ADR-0073 shipped three declared negotiation policy slots and wrote down the gap it did not close:

> every retained candidate is re-added to the congestion ledger from scratch at the start of each
> pass, so ledger reconstruction is linear in the retained unit-resource count per iteration rather
> than incremental … it is a constant-factor cost on fixtures of the size this repository measures,
> and an untested one at scale.

Two of its rip-up rules — `conflicted-only-v1` and `top-conflict-only-v1` — exist precisely so most
nets are retained between passes. The coordinator then discarded that retention at the ledger
boundary: `clear_present()`, then `add_candidate()` for every retained net, each call re-deriving
that candidate's unit lattice resources from its path geometry.

ADR-0073 also recorded a second, sharper problem. On this repository's one congested fixture,
`conflicted-only-v1` **does not converge**: it reaches the eight-iteration ceiling with `no_path`
where the default converges in five. So the slot that motivates incremental retention is also the
slot that does not work, and the honest reading was that partial rip-up needed something between
"only the nets that conflict" and "everything".

[The research survey](../research/incremental-spatial-index-v1.md) supplies both answers.
TritonRoute's detailed-routing worker keeps a mutable Boost R-tree region query with `add`,
`remove`, and a bounded `query(box, layer)` (`src/drt/src/dr/FlexDR_rq.cpp`), and its
search-and-repair schedule rips up a **constant** 7-gcell worker box on every one of 65 iterations,
varying the box's offset and the effort inside it but never its size.

## Decision

### 1. `IncrementalSpatialIndex`: a uniform grid whose cell size is fixed at construction

`copper_mcp.routing.spatial_index` gains a mutable sibling to ADR-0051's query-only index.
It supports `insert(key, bounds)`, `remove(key)`, and `query(bounds)`.

The cell size is **declared at construction and never re-derived**. That single choice is what
makes the structure safe to mutate: the set of cells an entry occupies is a pure function of
`(bounds, cell_size_nm)`, so an insert cannot perturb any other entry, and "mutate in place" and
"rebuild from the survivors" are the same computation reached by two routes. An R-tree — the
structure TritonRoute uses — would have been defensible for query performance and was rejected on
determinism: two R-trees built by inserting the same set in two orders have different node
boundaries, so incremental-equals-rebuilt becomes a property of the query implementation rather
than of the structure. Šidlauskas et al. (SIGSPATIAL 2009) additionally report that once fewer than
66% of updates are local, any grid-based index outperforms the R-tree-based one; a negotiation pass
that reroutes a net elsewhere is the opposite of a local update.

The contract is a **superset**, never an equality:

> `query(b)` returns at least every entry whose stored bounds intersect `b`.

An implementation may return more; it may never return fewer. This is the same direction every
obstacle bound in this repository rounds, and it is what makes the two bounded-work fallbacks safe:
an entry occupying more cells than the per-entry ceiling is held in an oversize set that *every*
query returns, and a query rectangle sweeping more cells than the ceiling degrades to a full scan.
Both add candidates. Neither can remove one.

ADR-0051's constraint is preserved verbatim: this index is mutated **between** negotiation passes,
never during an A* expansion. The immutable index remains what a single search sees.

### 2. The ledger retains incrementally, and costs the smaller of the two reconstructions

`CongestionLedger` caches each added net's exact unit-resource set and gains `remove_net`,
`retain_only`, and `nets_within_window`. Because occupancy is an additive per-net integer count
over exact lattice keys, removing one net's contribution is exact subtraction, not an
approximation. A resource whose count reaches zero is deleted rather than left at zero — a
**memory** guard rather than an output guard, since every reader of the present overlay filters on
`usage > 1` or reads a `Counter` whose default is already zero. B-089's mutation check is what
established which of the two it is.

`retain_only(S)` does **not** always subtract. B-089 measured that subtracting the departures is
cheaper only when there are fewer of them than there are survivors, so the implementation costs
`min(ripped-up units, retained units)`, and a pass that retains nothing takes a bare clear. Three
branches, one result: every branch reaches counters a rebuild would reach, and the tests assert
byte equality of everything a router or a published response can read off a ledger.

In unit work this never exceeds the path it replaces, which always paid the retained units **and**
re-derived every retained candidate's resources from its geometry. In wall clock it is faster
everywhere a net is retained and slightly *slower* where none is; the exception is measured, not
waved away, and it is quantified below.

### 3. A fourth rip-up literal: `conflict-window-v1`

`RipUpRule` gains `CONFLICT_WINDOW`, with one bounded weight `ripup_window_cells` in `1..64`. It
selects every conflicted net plus every retained net whose copper envelope lies within that many
lattice cells of a conflicted net's.

The window is a **constant**, not a schedule that widens. TritonRoute's is constant across all 65
of its iterations, and a window that grew would eventually be full rip-up again and stop being a
bound.

The index narrows the candidate nets; an exact integer rectangle predicate then decides membership.
That ordering is load-bearing rather than defensive: it makes the selected set a function of the
stored envelopes alone, so the index's cell size, capacity, and oversize fallback are free to
change how fast the selection runs and cannot change which nets it names. A digest-bound contract
whose behaviour depended on an internal acceleration parameter would be a contract in name only.

`negotiation_plan` stays geometry-free. It receives `window_nets` from the coordinator rather than
computing it: a slot decides *which* nets, never *where* they are. Supplying a window to any rule
that does not read one is refused rather than ignored.

### 4. No already-published digest moves

`RipUpSlot.as_json()` includes `ripup_window_cells` **only** for the rule that reads it. The three
existing literals keep the exact canonical bytes they published before the window existed, so no
issued rip-up slot digest, plan digest, or plan-bound candidate identity moves. `RipUpSlot()` is
still `sha256:871de3d6…` and `NegotiationPlan()` is still `sha256:b3d090ed…`, both pinned by test.

This is the narrow reading of ADR-0073's no-inert-parameter rule — a weight a rule does not read
may not vary a digest — and the cheapest way to guarantee it for a *new* weight is for the weight
not to appear at all. The general precedent is deliberately narrow: an existing literal's published
canonical bytes are immutable, and a new literal may carry the parameter it reads.

Nothing else moves. The no-plan coordinator path keeps `clear_present()`, because it rips up every
net on every pass and a bare clear is already the cheapest correct reconstruction for that.
`tests/test_golden_identities.py` and `tests/test_benchmark_negotiated_plan_slots.py` — which
re-runs the B-087 harness and requires every fresh case to equal the committed one — both pass
unchanged.

## Consequences and acceptance evidence

`tests/test_routing_incremental_spatial_index.py` demonstrates:

- **Conservatism.** Over randomized integer rectangles across twelve seeds, four cell sizes, three
  object-size regimes and three per-entry ceilings, every query result is a superset of a
  brute-force linear scan and a subset of the entry set. Touching rectangles intersect.
- **Incremental equals rebuilt.** After 200 mixed inserts and removes across eight seeds, the
  mutated index equals a freshly built one in keys, entry count, **bucket count**, oversize count,
  and every query answer including its examined-candidate count. Bucket count is asserted because
  an emptied bucket left behind would make two indexes holding the same entries distinguishable.
- **Order independence.** Inserting the same set forwards and backwards yields the same structure.
- **Determinism.** An identical mutation-and-query sequence replays to an identical SHA-256.
- **Ledger equivalence.** Across five retention fractions — exercising all three reconstruction
  branches — the incrementally retained ledger and the cleared-and-re-added one are byte-identical
  in added nets, conflict scores, overflow resources, and 144 probed edge penalties.
- **Bounded work and typed refusals.** A capacity ceiling raises `SpatialIndexCapacityError` (a
  `ValueError`); a malformed cell size, a duplicate key, an absent removal, a reversed rectangle, a
  negative margin, an inert or out-of-range window weight, and a window supplied to a rule that
  does not read one are each refused.
- **The window slot is non-vacuous.** End to end on the congested fixture it rips up strictly more
  than `conflicted-only-v1` and publishes a distinct rip-up slot digest while the other two slot
  digests are unchanged.
- **Determinism end to end.** Each of the no-plan path, the legacy-equivalent plan,
  `conflicted-only-v1`, and `conflict-window-v1` replays to identical canonical candidate bytes,
  status, iteration count, rip-up count, and plan evidence.

**Mutation check.** Five load-bearing guards were mutated one at a time. Four were caught
immediately: treating touching rectangles as disjoint, the recount branch leaving a stale spatial
index entry, emitting the window weight in every rip-up slot's canonical bytes, and the
conflict-window rule ignoring its window. The fifth — an emptied resource count left at zero
instead of deleted — **survived**. It turned out to be output-inert but not memory-inert: every
reader of the present overlay filters `usage > 1` or reads a `Counter` whose default is already
zero, so nothing observable moved, while the overlay would have grown pass by pass without bound.
A `live_resource_count` property and an assertion that an incrementally retained ledger tracks
exactly as many resources as a rebuilt one were added, and the mutant is now caught.

**Measured, with the regression stated.** B-089 replays both reconstructions against one recorded
candidate set, in one process, on the same fixtures: the B-087 congested channel, a synthetic
parallel-track sweep at 4/8/16/32 nets, and 16 real MIT-licensed SimpleRouteJson corpus boards
contributing 67 candidates and 13,194 unit resources. Across 105 A/B points every one left the
ledger byte-identical. At the 78 points where any net is retained, the new path performs equal or
fewer exact resource operations and is **11.8% to 99.94% faster, median 74.8%**. At the 27 points
where **nothing** is retained it is up to **21.97% slower** — 4.8 µs against 4.3 µs on the
smallest fixture — because a bare clear is hard to beat and the residual gap is the set
construction that chooses the branch. The first implementation, which always subtracted, was
60–130% slower there; that measurement is why the bare-clear branch exists, and both numbers are
recorded rather than quietly designed around.

The proportion matters more than either number: on the congested fixture the whole ledger
reconstruction is single-digit microseconds against 58–75 ms of routing, so the incremental ledger
is a **constant-factor improvement to a term that is not the bottleneck** — exactly what ADR-0073
predicted. What moves the end-to-end number is the window rule reducing router calls from 30 to 22
(−27%) while converging in the same five iterations at the same 56,000,000 nm of copper, where
`conflicted-only-v1` does not converge at all. A 16-cell window makes 30 calls again, because a
wide enough window *is* full rip-up; B-089 records that too.

Nothing here claims KiCad DRC, electrical, multilayer, fabrication, apply, whole-board, or general
scaling validity. The corpus boards are real but small; no scaling result is claimed from them. No
MCP surface, job, or public contract changes, and no default changes — `all-nets-v1` remains the
rip-up default.

## Alternatives considered

- **A Boost-style R-tree, as TritonRoute uses.** Rejected on determinism, not on performance. See
  §1; the survey's §2 records that the empirical case for a grid here is "adequate", not "better".
- **An interval or segment tree.** Rejected: it indexes one axis, and a 2-D rectangle query needs a
  second-level structure that reintroduces order-dependent state for nothing a grid does not give
  on an integer lattice.
- **Deferred or lazily rebuilt invalidation.** Rejected: it makes the work done depend on *when* a
  query happens, and this repository's outputs must not depend on timing.
- **Always subtract the departures.** Rejected by measurement: 60–130% slower at zero retention.
- **Widen the rip-up window per iteration.** Rejected: a window that grows is not a bound, and
  TritonRoute's own 65-iteration schedule holds its worker box size constant.
- **Let the window rule read the index directly, without an exact re-check.** Rejected: it would
  make a digest-bound contract's behaviour depend on an internal acceleration parameter.
- **Make `conflict-window-v1` the default because it beats `all-nets-v1` on router calls here.**
  Rejected: one synthetic fixture is not a criterion. B-089 was not predeclared and classifies
  itself accordingly, and ADR-0073's own precedent is that a default follows measurement on
  reserved fixtures, not on the fixture that motivated the change.
- **Version the rip-up slot schema to `v2` and include the window unconditionally.** Rejected: it
  would move every already-published rip-up and plan digest, including the ones the committed B-087
  artifact records, to express a fourth literal.
- **Also make the router's per-request obstacle index incremental.** Deferred, and named here so it
  is not mistaken for done. That index is rebuilt once per `propose()` call and its contents are
  per-request (a net's own copper is excluded, and clearances are net-class-dependent), so sharing
  it across a negotiation needs its own decision and its own evidence.
