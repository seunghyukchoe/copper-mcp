# Deterministic A* Routing Baseline

## Status

`routing/astar.py` is a candidate-only CPU reference for one exact two-pin route. It is intentionally
smaller than issue #10's complete acceptance target: it does not serialize a KiCad patch, invoke
authoritative KiCad DRC, expose an MCP tool, route multiple nets, or apply copper.

## Accepted input

| Surface | First-slice contract |
|---|---|
| Snapshot | Canonical, digest-verified Board IR `0.1.0` |
| Request | One stable net ID, one stable signal-layer ID, exact base revision, seed, integer settings |
| Connectivity | Exactly two pads belonging to the net; both accessible on the selected layer |
| Constraints | One net-class width/clearance assignment; no selected-net length or differential-pair rule |
| Board | One hole-free, axis-aligned rectangular outline |
| Obstacles | Axis-aligned rectangular keepouts that prohibit tracks on the selected layer |
| Existing geometry | No vias, selected-layer segments/arcs/zones, or additional selected-layer pads |
| Search | Four-neighbour orthogonal grid; east, north, west, south expansion order |
| Output | Immutable orthogonal `RoutePatch` tied to the unchanged snapshot digest |

Anything outside this matrix returns a typed diagnostic. Unsupported objects and selected-net
constraints are never silently ignored.

## Exact geometry

All coordinates, costs, widths, clearances, and penalties are integers in nanometres. The lattice is
anchored at the lexicographically first pad ID; the other pad-center delta must be exactly divisible
by `grid_step_nm`.

The board is inset by the ceiling of half the route width. Track keepouts are expanded by that
half-width plus the selected net-class clearance. A centerline on the resulting boundary is legal;
entering its open interior by one nanometre is not. Every complete grid edge is checked, not only its
endpoints. These semantics are covered by exact-boundary and one-nanometre regression tests.

Other pads and existing copper are currently rejected because approximating their clearance would
create a false correctness claim. They will become obstacles only after the Board IR geometry model
and differential tests can represent them exactly.

## Objective and determinism

The primary scalar objective is:

```text
total_cost_nm = wire_length_nm
              + bend_count * bend_penalty_nm
              + proximity_steps * proximity_penalty_nm
```

The Manhattan heuristic omits non-negative penalties and remains admissible. Search state includes
incoming direction so bend cost is exact. Equal-cost states use stable integer tuple ordering; no
wall clock, hash randomization, process memory, or floating-point value participates. The seed is
recorded in candidate identity for parity with future policies but does not randomize this baseline.

`max_grid_nodes` bounds the position lattice before allocation, `max_expansions` bounds state search,
`max_obstacles` caps selected track keepouts, and `max_obstacle_checks` directly bounds the otherwise
multiplicative edge/proximity work. Cancellation is checked during preparation, between expansions,
and at most every 64 obstacle checks during a long evaluation. Node-budget, obstacle-budget,
search-budget, cancellation, unsupported constraint, unsupported geometry, stale revision, invalid
snapshot/request, off-grid endpoint, and no-path outcomes remain distinct. Failure diagnostics carry
deterministic expanded-state and obstacle-check counts.

## Benchmark oracle

`routing/oracle.py` provides a benchmark-only Dijkstra oracle that sets the heuristic to zero while
reusing the exact bounded preparation, edge-legality, proximity, and additive-cost evaluators. It
returns only an optimal cost or typed diagnostic—never a route patch—and is intentionally excluded
from the supported routing API. Tests compare A* and Dijkstra completion, total cost, bends, and
proximity steps on straight, detour, exact-clearance, and no-path cases.

`scripts/benchmark_routing.py` repeats those generated fixtures, verifies deterministic outcomes,
and emits content-addressed JSON with raw timing and incremental-memory samples. The tiny synthetic
suite is an optimality and reproducibility check, not evidence of whole-board quality, production
throughput, KiCad DRC, or superiority over another router.

## Candidate identity

The route is compressed to omit collinear interior points and post-validated against the same exact
board and obstacle bounds. Canonical JSON identity bytes contain:

- base revision and endpoint pad IDs;
- net, layer, width, and integer vertices;
- exact cost decomposition and deterministic search metrics;
- router version, policy, seed, and all A* settings, including every resource ceiling.

The circular `candidate_id` field is excluded from those bytes, then set to their SHA-256 digest.
`verify_candidate_id()` rechecks that binding. Runtime and memory measurements do not affect the ID.

## Safety boundary

Zero `hard_internal_violations` means only that this implementation's supported-grid post-checks
passed. It is not a KiCad DRC result and says nothing about electrical behavior, SI/PI, EMC, thermal
performance, DFM, or fabrication readiness. A future candidate pipeline must export the patch to a
private board snapshot and pass the authoritative KiCad gate before preview or application.

See [ADR-0006](../adr/0006-bounded-deterministic-astar.md) and the
[roadmap](../roadmap.md).
