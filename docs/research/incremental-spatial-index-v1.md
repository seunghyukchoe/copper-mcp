# Incremental spatial indexing for bounded rip-up and reroute

> Survey date: 2026-08-06. Every claim below is attributed to a URL a reader can open. Where a
> claim comes from reading source code rather than a paper, the file and the exact declaration are
> named so the reading can be checked rather than trusted.

## Why this survey exists

[ADR-0073](../adr/0073-declared-negotiation-policy-slots.md) shipped three declared negotiation
policy slots and recorded, in its own words, the gap it did not close:

> Issue #64's incremental spatial index is **not** built here … every retained candidate is
> re-added to the congestion ledger from scratch at the start of each pass, so ledger
> reconstruction is linear in the retained unit-resource count per iteration rather than
> incremental … it is a constant-factor cost on fixtures of the size this repository measures, and
> an untested one at scale.

That is the problem statement. A `conflicted-only-v1` or `top-conflict-only-v1` rip-up rule exists
precisely so that most nets are *retained* between passes; the coordinator then throws that
retention away at the ledger boundary and rebuilds it. Rebuilding a structure whose contents you
just proved unchanged is the definition of non-incremental work.

[ADR-0051](../adr/0051-conservative-spatial-index.md) already ships a *query-only* conservative
uniform grid for the router's obstacle predicates, and closes with a constraint this slice has to
honour:

> A future bounded rip-up/reroute coordinator must preserve this index's immutable snapshot and
> candidate-only validation boundary rather than mutating buckets during search.

So whatever is built here must be mutated **between** negotiation passes, never during an A*
expansion. That constraint is what separates the two structures: ADR-0051's index is immutable for
the lifetime of one search; this one is mutable for the lifetime of one negotiation.

## 1. What production routers actually index with

### 1.1 TritonRoute / OpenROAD `drt`: a Boost R-tree, mutated per worker

TritonRoute is the open-source detailed router in OpenROAD. Its published description of the flow
is that the router "consists of several main building blocks, including pin access analysis, track
assignment, initial detailed routing, search and repair, and a DRC engine"
([Kahng, Wang, Xu, *TritonRoute: The Open-Source Detailed Router*](https://vlsicad.ucsd.edu/Publications/Journals/j133.pdf)).

The spatial structure is not described in the paper prose but is unambiguous in the source. A
GitHub code search for `rtree` under `src/drt` returns ten files; the relevant ones are
`src/drt/src/frRTree.h`, `src/drt/src/frRegionQuery.cpp`, `src/drt/src/dr/FlexDR_rq.cpp` (detailed
routing), `src/drt/src/ta/FlexTA_rq.cpp` (track assignment), and `src/drt/src/gc/FlexGC_rq.cpp`
(design-rule checking). The single alias every one of them uses is in
[`frRTree.h`](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/drt/src/frRTree.h):

```cpp
template <typename T, typename Key = odb::Rect>
using RTree = bgi::rtree<std::pair<Key, T>, bgi::quadratic<16>>;
```

Three things are load-bearing for this repository:

- It is a **Boost.Geometry R-tree of `(Rect, payload)` pairs** — the indexed key is an
  axis-aligned integer rectangle, not the exact shape. The exact shape check happens afterwards.
  That is the same over-approximate-then-decide discipline ADR-0051 already adopted.
- It is **`quadratic<16>`**, Guttman's original quadratic split with a node fan-out of 16 — not
  `bgi::rstar`, and not a bulk-loading `bgi::linear` pack. Quadratic split is the variant that
  supports cheap single-element insertion and deletion; `rstar` gives better query quality at
  higher insert cost, and packing algorithms want the whole set up front. The choice is legible as
  "this index is going to be *mutated*, one shape at a time".
- [`FlexDR_rq.cpp`](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/drt/src/dr/FlexDR_rq.cpp)
  exposes exactly the three operations this slice needs:
  `FlexDRWorkerRegionQuery::add(drConnFig*)`, `FlexDRWorkerRegionQuery::remove(drConnFig*)`, and
  two `query(const odb::Rect& box, const frLayerNum layerNum, …)` overloads. The index is
  partitioned **per layer**, so a query is bounded in a second dimension before it is bounded in
  area.

The reading to take away: the industry-reference open-source detailed router keeps a *mutable*
region query alongside its search, adds and removes shapes as nets are ripped up and rerouted, and
uses the index only to narrow candidates for an exact checker.

### 1.2 TritonRoute's rip-up window is a fixed-size box that moves, not a growing one

The issue asks for a "bounded rip-up window contract". TritonRoute's own schedule is worth reading
before inventing one. `FlexDR::strategy()` in
[`FlexDR.cpp`](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/drt/src/dr/FlexDR.cpp)
returns a 65-row table of `SearchRepairArgs`, whose field order is declared in
[`FlexDR.h`](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/drt/src/dr/FlexDR.h)
as `{size, offset, mazeEndIter, workerDRCCost, workerMarkerCost, workerFixedShapeCost,
workerMarkerDecay, ripupMode, followGuide}`. Reading the table against that field order:

| Iteration | `size` | `offset` | `mazeEndIter` | `ripupMode` |
|---|---|---|---|---|
| 0 | 7 | 0 | 3 | `ALL` |
| 1 | 7 | −2 | 3 | `ALL` |
| 2 | 7 | −5 | 3 | `ALL` |
| 3 | 7 | 0 | 8 | `DRC` |
| … | 7 | 0 / −2 / −5 / −6 | 8 … 64 | `DRC` / `NEARDRC` / `ALL` |
| 64 | 7 | −6 | 64 | `DRC` |

The window **`size` is constant at 7 gcells for all 65 iterations**. What varies is the `offset`
that shifts the tiling (so a violation sitting on a worker boundary lands in a worker interior on
the next pass), the maze effort ceiling (3 → 8 → … → 64), and the cost weights. The first three
passes rip up everything (`RipUpMode::ALL`); every later pass rips up only what the DRC engine
flagged (`RipUpMode::DRC`) or what is near it (`NEARDRC`).

Two design conclusions follow, and neither is what a naive reading would predict:

1. **A bounded rip-up window is bounded by a *constant*, not by a schedule that grows.** Effort
   inside the window grows; the window does not. A contract whose window widens per iteration
   eventually degenerates into full rip-up and stops being a bound.
2. **Full rip-up is the warm-up, not the fallback.** TritonRoute rips up everything for three
   passes and then never does so unconditionally again. This is the same asymmetry PathFinder §3.5
   reports and [ADR-0073](../adr/0073-declared-negotiation-policy-slots.md) already quoted.

### 1.3 VPR: a dense array, deliberately not a tree

VPR's routing-resource lookup is `RRSpatialLookup`
([VPR RR graph API](https://docs.verilogtorouting.org/en/latest/api/vpr/rr_graph/)), backed by a
dense multi-dimensional matrix keyed by `(layer, x, y, rr_type, side)` rather than by any tree. The
FPGA case is degenerate in a way an ASIC or PCB case is not: routing resources sit at integer grid
coordinates by construction, so the "index" is a direct address computation and both insert and
lookup are O(1) with no rebalancing at all.

This is a genuine data point rather than a curiosity: **when the coordinate space is already an
integer lattice, a dense or hashed grid is not an approximation of a spatial index — it is the
exact one.** CopperMCP's congestion ledger is in exactly that position. Its resources are lattice
vertices and lattice edges at exact nanometre multiples of `grid_step_nm`.

### 1.4 FastRoute: uniform bins, and incrementality as a first-class mode

FastRoute models global routing over "global bins with a corresponding grid graph"
([Pan & Xu, *FastRoute: An Efficient and High-Quality Global Router*](https://onlinelibrary.wiley.com/doi/10.1155/2012/608362)),
a uniform partition rather than an adaptive tree. OpenROAD's `grt` module, which is derived from
FastRoute 4.1, ships **incremental global routing** as an explicit supported mode
([OpenROAD `grt` README](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/grt/README.md)) —
i.e. the congestion structure is updated in place after a design change rather than recomputed.

### 1.5 The primary source for R-trees

[Guttman, *R-trees: A Dynamic Index Structure for Spatial Searching*, SIGMOD 1984](https://doi.org/10.1145/602259.602266)
is the origin of the structure TritonRoute uses, and is already cited by ADR-0051. Its relevant
property here is the one in the title: it is *dynamic*. Insertion and deletion are defined
operations with node splitting and condensation, which is exactly what the query-only index
ADR-0051 built does not have.

## 2. R-tree vs uniform grid vs interval tree, for integer rectangles under heavy update

The three candidates, judged against this repository's constraints rather than in the abstract.

| | R-tree (quadratic split) | Uniform grid buckets | Interval / segment tree |
|---|---|---|---|
| Query | O(log n) expected, degrades with MBR overlap | O(cells touched + entries in them) | O(log n + k) per axis, needs a second stage for 2-D |
| Insert | Descend + possible node split, rebalancing | Hash-insert into each covered cell | Rebalance, or static-only for segment trees |
| Remove | Descend + condense tree, may re-insert orphans | Discard from each covered cell | Rebalance |
| Determinism of *structure* | Depends on insertion order | Independent of insertion order | Depends on insertion order |
| Behaviour on clustered data | Adapts | Degrades (hot cells) | Adapts on the split axis only |
| Behaviour on wildly varying object sizes | Adapts | Degrades (one object in many cells) | Adapts |

The decisive row is **determinism of structure**. An R-tree built by inserting the same set in two
different orders is two different trees with two different node boundaries. Its *query results* are
still the same set, so an R-tree is not incorrect here — but proving "an index after N inserts and
removes answers identically to a freshly built one" becomes a property of the query implementation
rather than a property of the structure, and CopperMCP's determinism requirement is absolute
([project charter](../project-charter.md), [ADR-0006](../adr/0006-bounded-deterministic-astar.md)).
A uniform grid with a **cell size fixed at construction** has no such freedom: the set of cells an
entry occupies is a pure function of its bounds and the cell size, so an insert cannot perturb any
other entry, and incremental-equals-rebuilt holds structurally rather than by test.

The empirical literature agrees for update-heavy workloads. Šidlauskas, Šaltenis, Christiansen,
Johansen and Šaulys,
[*Trees or Grids? Indexing Moving Objects in Main Memory*, ACM SIGSPATIAL 2009, pp. 236–245](https://dl.acm.org/doi/10.1145/1653771.1653805)
([open preprint](https://dbtr.cs.aau.dk/wp-content/uploads/2022/11/DBTR-26.pdf)) builds
update-optimised variants of *both* structures and reports that once fewer than 66% of updates are
local, **any** grid-based index outperforms the R-tree-based one. A negotiation pass that rips up a
net and reroutes it elsewhere is close to the opposite of a local update.

Nagel/Kipf-style follow-up work summarised in the same literature reaches the compatible weaker
conclusion that "the grid can compete with the R-tree in terms of query performance and is
surprisingly robust to varying parameters of the workloads"
([survey of two-level in-memory spatial indexes](https://arxiv.org/pdf/2005.08600)). The honest
reading is *not* "grids are faster"; it is "grids are not slower enough to pay for
non-determinism".

Interval and segment trees are ruled out on a different ground: they index one axis. A 2-D
rectangle query needs either a second-level structure per node or a priority-search-tree variant,
which reintroduces order-dependent structure without buying anything a grid does not already give
on an integer lattice.

**Conclusion.** A uniform grid with a cell size declared at construction, hashed buckets, and a
bounded oversize fallback. This is the same family as ADR-0051's existing query-only index — which
is deliberate: two indexes with different failure directions in one router would be a correctness
hazard, not a feature.

## 3. Incremental-update strategies, and the one this repository can accept

Three strategies appear in the literature and in the routers above.

1. **Rebuild.** What CopperMCP does today. Correct, trivially deterministic, and linear in the
   retained set per pass — the cost ADR-0073 recorded.
2. **Mutate in place (insert/remove).** TritonRoute's `add`/`remove` on a Boost R-tree; OpenROAD
   `grt`'s incremental global routing. Cost is proportional to the *change*, not to the retained
   set. Risk: a structure that drifts from what a rebuild would produce is a silent correctness
   defect, because it is invisible until a query misses.
3. **Deferred / batched invalidation** (mark dirty, rebuild lazily). Common in GIS. Rejected here:
   a lazily rebuilt index makes the amount of work done depend on *when* a query happens, which is
   a timing dependence, and this repository's outputs must not depend on timing.

Strategy 2 is the one to take, with a specific mitigation for its specific risk: because the cell
size is fixed at construction and an entry's cells are a pure function of `(bounds, cell_size)`,
"mutate in place" and "rebuild" are the same computation reached by different routes. The
differential test (`index after N mutations` ≡ `index built fresh from the surviving entries`) is
therefore checking an invariant the design already guarantees, which is the correct relationship
between a test and a design — the test exists to catch an *implementation* mistake, not to
compensate for a design that could be wrong.

The second mitigation is direction. Every published index above narrows candidates for an exact
checker and never decides legality itself. An index that misses an obstacle would make an illegal
route look legal. So the contract is stated as a **superset**, not as an equality:

> `query(b)` returns at least every entry whose stored bounds intersect `b`.

An implementation is free to return more. It is never free to return fewer. Stating it this way
means a future looser or coarser index is a performance change, while a future *tighter* one that
drops a touching rectangle is a contract violation the tests catch.

## 4. What this argues for, concretely

Applied to CopperMCP's congestion coordinator:

- **A `IncrementalSpatialIndex` with `insert`, `remove`, and `query`**, uniform grid, cell size
  fixed at construction, deterministic sorted results, bounded entry count and bounded cells per
  entry, with an oversize set that every query returns (conservative in the safe direction, per
  §3). Mutated between passes only, never during a search — ADR-0051's constraint.
- **Exact incremental retention in `CongestionLedger`.** Because the ledger's resources are exact
  integer lattice keys and occupancy is an additive per-net count, removing one net's contribution
  is exact subtraction, not an approximation. Retaining `S` by removing `A \ S` yields byte-identical
  counters to clearing and re-adding `S`, so the coordinator's published bytes cannot move. This is
  §1.3's observation applied: on an integer lattice the "index" is exact.
- **A bounded rip-up window**, sized by a constant number of lattice cells rather than a growing
  schedule (§1.2), selecting the conflicted nets plus every retained net whose copper lies within
  that window of a conflicted net. The index narrows the candidate nets; an exact integer
  rectangle predicate decides membership, so the selected set is independent of the index's
  internal parameters (§3, direction).

## 5. What this survey does not establish

- No claim that a grid beats an R-tree for CopperMCP's workload. The argument for the grid is
  determinism first and adequacy second; §2's citations support "adequate", not "better".
- No claim that TritonRoute's 7-gcell window is the right size for a PCB negotiated coordinator.
  It is evidence that a *constant* window is what a production router uses, not evidence about the
  constant.
- No performance claim of any kind. This is a design survey; the measurement lives in the
  benchmark ledger and must be made on the same fixtures before and after.
- Nothing here is a KiCad DRC, electrical, multilayer, fabrication, apply, or general-board claim.

## Sources

- [Kahng, Wang, Xu, *TritonRoute: The Open-Source Detailed Router*](https://vlsicad.ucsd.edu/Publications/Journals/j133.pdf)
- [OpenROAD `src/drt/src/frRTree.h`](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/drt/src/frRTree.h)
- [OpenROAD `src/drt/src/dr/FlexDR_rq.cpp`](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/drt/src/dr/FlexDR_rq.cpp)
- [OpenROAD `src/drt/src/dr/FlexDR.cpp`](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/drt/src/dr/FlexDR.cpp)
- [OpenROAD `src/drt/src/dr/FlexDR.h`](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/drt/src/dr/FlexDR.h)
- [OpenROAD `grt` (FastRoute-derived global router) README](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/grt/README.md)
- [OpenROAD detailed-routing documentation](https://openroad.readthedocs.io/en/latest/main/src/drt/README.html)
- [VPR routing-resource graph API (`RRSpatialLookup`)](https://docs.verilogtorouting.org/en/latest/api/vpr/rr_graph/)
- [VPR router lookahead](https://docs.verilogtorouting.org/en/latest/api/vprinternals/router_lookahead/)
- [Pan & Xu, *FastRoute: An Efficient and High-Quality Global Router*, VLSI Design 2012](https://onlinelibrary.wiley.com/doi/10.1155/2012/608362)
- [Guttman, *R-trees: A Dynamic Index Structure for Spatial Searching*, SIGMOD 1984](https://doi.org/10.1145/602259.602266)
- [Šidlauskas et al., *Trees or Grids? Indexing Moving Objects in Main Memory*, SIGSPATIAL 2009](https://dl.acm.org/doi/10.1145/1653771.1653805)
- [Šidlauskas et al., preprint of the above](https://dbtr.cs.aau.dk/wp-content/uploads/2022/11/DBTR-26.pdf)
- [*A Two-level Spatial In-Memory Index*](https://arxiv.org/pdf/2005.08600)
