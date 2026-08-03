# Deterministic A* Routing Baseline

## Status

`routing/astar.py` is a candidate-only CPU reference for one exact two-pin route. The separate
`adapters/kicad_route_patch.py` bridge can now serialize that exact replayed candidate into a
disposable KiCad board, and the internal KiCad service can bind that private derivative to
authoritative DRC evidence. A bounded, non-mutating preview now exposes that pipeline through MCP
and the CLI. The slice remains smaller than issue #10's complete acceptance target: it does not
route multiple nets, run durable jobs, persist or export candidate boards, or apply copper.

## Accepted input

| Surface | First-slice contract |
|---|---|
| Snapshot | Canonical, digest-verified Board IR `0.1.0` |
| Request | One stable net ID, one stable signal-layer ID, exact base revision, seed, integer settings |
| Connectivity | Exactly two pads belonging to the net; both accessible on the selected layer |
| Constraints | One net-class width/clearance assignment; no selected-net length or differential-pair rule |
| Board | One hole-free, axis-aligned rectangular outline |
| Obstacles | Rectangular track keepouts; foreign-net pads, orthogonal segments, and through vias; conservative foreign-net solid-zone polygon envelopes |
| Attachment | Orthogonal same-net selected-layer segments, as connectable copper rather than obstacles |
| Existing geometry | No selected-layer arcs, off-axis pads, or diagonal segments on any net; no same-net vias or zones |
| Search | Four-neighbour orthogonal grid; east, north, west, south expansion order; multi-source and multi-target when same-net copper is present |
| Output | Immutable orthogonal `RoutePatch` tied to the unchanged snapshot digest, or a typed already-connected record |

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

Selected-layer copper outside the routed net is an obstacle rather than a rejection. A foreign pad
contributes a rectangle centred on the pad, with its sizes swapped on a quarter turn; an orthogonal
segment contributes the rectangle its centreline sweeps, grown by the ceiling of half its width.
Each is inflated by the routed half-width plus the stricter of the routed net's and the obstacle
net's class clearance, so a board mixing net classes cannot be routed to the looser rule.

A through via outside the routed net contributes the bounding box of its outer diameter; Board IR
v0.1 admits through vias only, so every via provably crosses the routed layer. Drill diameter is
ignored because copper, not the hole, is what a track must clear. A via on the routed net is still
rejected as partial routing, because a layer change is not something this single-layer contract can
model.

A foreign-net solid zone on the selected layer contributes its exact simple polygon as a
conservative boundary envelope. The entire interior is treated as potentially occupied because
Board IR deliberately does not make KiCad's derived `filled_polygon` cache authoritative. The open
centreline margin is the routed half-width plus the maximum of routed-net class clearance,
zone-net class clearance, and the zone's own clearance. Exact integer containment, inclusive segment
intersection, and rational squared point-to-edge distance support concave and diagonal boundaries;
the bounding box is only a pruning device. Exact offset equality is legal. A same-net zone remains
unsupported, because a conservative envelope cannot say which part of it is actually filled copper,
and useful routing through real fill voids requires a separate freshness-bound refill contract.

Round pad and via shapes use their bounding box. That over-approximates only: it can refuse a route a
rounder shape would allow, never permit a clearance violation. Arcs, off-axis pad rotations, and
diagonal segments still fail closed. Every obstacle, including one polygon zone regardless of vertex
count, counts against `max_obstacles`, and so does every same-net attachment segment.

## Same-net attachment

An orthogonal same-net segment on the selected layer is attachment copper: never an obstacle, and a
legal place for the proposal to begin or end. Deciding that requires a second rectangle model that
errs the opposite way from the obstacle model. Obstacle rectangles over-approximate copper so a
clearance is never understated; connectivity rectangles under-approximate it, because claiming
copper that is not there would assert an electrical connection the board does not have.

A track's connectivity core drops its round end caps and floors the half width. A pad's core is the
largest axis-aligned rectangle provably inside its shape: the whole rectangle for `rect`, inset by
the corner radius for `roundrect`, the central band for `oval`, and a centre line for `circle`.
Every core is a subset of real copper, so overlapping cores prove overlapping copper, while the
residual error can only fail to notice a connection.

Components are exact integer union-find over the two pad cores and every same-net segment core, with
closed rectangle intersection as the connection test — exact contact counts. The lowest index always
wins a union, so a component's root never depends on discovery order. Each pair comparison charges
the obstacle-check budget, so the every-64-checks cancellation cadence applies unchanged.

If both pads land in one component the router returns a typed `RouteConnection` instead of a
candidate: the net is already connected on the selected layer and there is nothing to route. That is
a terminal success, not a `RouteFailureCode`. Otherwise the search is seeded from every lattice node
the source component's segment cores cover and terminates on any node the target component's cores
cover; a rectangle's covered index range is solved directly rather than by scanning the lattice, and
each emitted node charges the obstacle budget. Pads contribute only their centre node, so a board
without same-net copper produces byte-identical geometry to the single-source contract.

Because two components cannot both contain the same node without having been unioned, the seed and
target sets are provably disjoint and the emitted patch always has at least one edge. The heuristic
becomes the Manhattan distance to the target bounding box: every target lies inside that box, every
grid edge costs at least one step, and the bend and proximity terms are non-negative, so it stays
admissible; one unit step changes it by at most one step, so it stays consistent. With a single
target the box is degenerate and the estimate is exactly the original two-pin heuristic.

The candidate remains new segments only. Attachment is geometric overlap with existing same-net
copper, which KiCad accepts for the same net; the committed `partial-route.kicad_pcb` fixture is
checked against KiCad 10.0.5 for zero errors, warnings, and unconnected items. When the cheapest
attachment is mid-stub rather than at a stub endpoint, the leftover tail is copper with an
unconnected end and KiCad reports `track_dangling` at warning severity — not a hard-correctness
failure, but a real quality consequence. A diagonal same-net segment, a same-net via, a same-net
zone, and an endpoint pad whose shape is not modeled exactly all still fail closed. The component
analysis is skipped entirely when the net carries no same-net selected-layer segment, so two
overlapping same-net pads with no track between them are still routed redundantly.

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
`max_obstacles` caps selected-layer obstacles, and `max_obstacle_checks` directly bounds the otherwise
multiplicative edge/proximity work. Cancellation is checked during preparation, between expansions,
and at most every 64 obstacle checks during a long evaluation. Polygon bounds construction charges
each inspected vertex, and legality/proximity charge their pruning bound and every examined edge.
Node-budget, obstacle-budget,
search-budget, cancellation, unsupported constraint, unsupported geometry, stale revision, invalid
snapshot/request, off-grid endpoint, and no-path outcomes remain distinct. Failure diagnostics carry
deterministic expanded-state and obstacle-check counts.

## Benchmark oracle

`routing/oracle.py` provides a benchmark-only Dijkstra oracle that sets the heuristic to zero while
reusing the exact bounded preparation, edge-legality, proximity, and additive-cost evaluators. It
returns only an optimal cost or typed diagnostic—never a route patch—and is intentionally excluded
from the supported routing API. It shares the same seeding and termination sets, so multi-source and
multi-target searches stay comparable, and reports exact optimal cost zero for an already-connected
net. Tests compare A* and Dijkstra completion, total cost, bends, and proximity steps on straight,
detour, exact-clearance, attachment, and no-path cases.

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

## Disposable KiCad bridge

`render_kicad_candidate_board()` first reproduces the supplied Board IR from the original KiCad bytes
and constraint profile. It verifies candidate identity, reruns the bounded A* request, and requires an
exact candidate match. Every modeled source geometry object must have a native UUID/tstamp; the
bridge rejects revision-derived geometry IDs because rewriting derivative metadata would otherwise
change them. Each compressed route edge becomes one root-level KiCad segment with exact decimal
units and a deterministic UUIDv5 derived from candidate identity and edge order. Native UUID/tstamp
identities are collected once for constant-time collision checks, and the derivative records
`copper-mcp` plus its package version as its KiCad writer.

The rendered bytes stay under the same parser, byte, and total-object budgets and are parsed back
through the supported KiCad adapter. The complete modeled content must equal the base snapshot after
replacing only source revision and writer provenance and appending the expected segments. The
function performs no file write, durable export, subprocess call, preview, MCP action, or board
mutation. DRC orchestration remains a separate adapter boundary.

## Candidate-bound authoritative DRC

`run_route_candidate_drc()` captures the source board and its bounded project/rule/library context
before parsing. It builds the Board IR snapshot only from those captured bytes and invokes only the
replay-verified serializer above. The patched board replaces the original board payload in memory;
the adapter rechecks the per-file, file-count, and cumulative context budgets before writing a
path-preserving private temporary snapshot.

Candidate DRC and ordinary board DRC use the same fixed KiCad argument vector, POSIX file ceiling,
timeout, discarded output, accepted result codes, bounded report reader, and strict JSON parser. The
complete private board/rule/library input context is recaptured and hashed after the subprocess
exits. A final recapture of the original board, rules, and workspace-local libraries must match the
initial context revision or the evidence is discarded.

The frozen evidence record binds the candidate ID, its Board IR base revision, original KiCad source
revision, patched board revision, complete patched context revision, and nested aggregate
`DrcSummary`. The summary's base and context revisions must match the patched revisions, its
violation-type total must equal its aggregate finding counts, and KiCad exit code `0` or `5` must
agree with the absence or presence of report findings. Exit code `5` is valid evidence rather than
an adapter failure; warning-only or exclusion-only evidence may still have a hard-pass flag. No
candidate bytes, raw descriptions, coordinates, UUIDs, or net names are returned. A KiCad 10.0.5
integration test runs this path from the original synthetic two-pad fixture and verifies that source
bytes, inode, modification time, and workspace entries remain unchanged.

## Public non-mutating preview

`route_preview.preview_route()` is the first public routing surface, exposed as the `preview_route`
MCP tool and the `copper-mcp preview-route` command. It parses one untrusted request, resolves a
single `.kicad_pcb` beneath the workspace, converts it through the fail-closed Board IR adapter,
proposes one candidate, and optionally binds ADR-0008 DRC evidence.

Requests carry a workspace-relative board path, a KiCad net name, a copper layer name, integer
net-class constraints, and optional seed, A* settings, and `include_drc` fields. Unknown fields,
non-integer or out-of-range budgets, booleans supplied as integers, control characters, oversized
net names, and non-copper layer names are rejected before any file is read. Routing constraints come
only from the caller, never from untrusted board content, so a board file cannot widen its own
clearance.

Every outcome is one of four statuses. `routed` carries a candidate whose base revision must equal
the previewed Board IR snapshot digest. `already_connected` carries a `RouteConnection` bound to the
same digest, naming both endpoint pads and counting the attachment segments and component objects;
it is a terminal success with nothing to propose, not a failure. `not_routed` carries exactly one
typed, non-echoing diagnostic. `unsupported_board` carries bounded conversion diagnostic-code counts
and no snapshot digest; any conversion diagnostic, including a warning, produces this status. A
wall-clock deadline
(`COPPER_MCP_MAX_ROUTE_PREVIEW_SECONDS`, default 30 s) starts at the operation boundary, before the
board is resolved, read, or converted, and bounds the whole call rather than only the search. It is
checked after conversion, consulted during search, and clamps the KiCad timeout for optional DRC to
whatever budget remains. Exceeding it surfaces as the ordinary `cancelled` diagnostic, and it never
participates in candidate identity.

`include_drc` runs the candidate-bound authoritative path and returns the same aggregate, redacted
`DrcSummary` plus the candidate, source, patched-board, and patched-context revisions. A missing
KiCad CLI or non-binding evidence fails the whole call rather than returning an unverified
candidate. On an `already_connected` net the flag is skipped rather than failed: that rule protects
a proposal, and no copper is being proposed. `RoutePreview` revalidates all of these bindings on construction and serializes to a
detached plain dictionary.

The preview writes no file, creates no job, persists nothing, and applies no copper. It does return
the integer geometry and endpoint pad IDs it generated, which is an intentional and documented
disclosure; source board bytes and unrelated board objects are never returned.

## Measured coverage on a real board

The polygon model is validated by exact synthetic geometry cases; the rectangular existing-copper
model also has a purpose-built blocked-pad KiCad DRC fixture. It is therefore worth stating plainly
what neither yet reaches. Measured against the repository's own
[CopperTone](../../hardware/coppertone-buffer/README.md) board source at SHA-256
`3bcd01ec4942fccabfaf1c21bdae050a31a7bf99af7ab1bcb0dbb3d0aabcfb94`:

| Stage | Result |
|---|---|
| Board IR conversion | Supported — 2 copper layers, 14 nets, 55 pads, 53 segments, 9 vias, 2 zones, 2 keepouts |
| Nets previewable on `F.Cu` | 0 of 14 |

Conversion is not the blocker; the router's contract is.

ADR-0016 removed the same-net partial-routing veto, and the honest result is that **the coverage
number does not move**. It was worth measuring precisely why. The veto fired early in preparation,
so it was masking every later refusal, and earlier releases credited it with more than it deserved.
Re-measured with the veto gone:

- 9 of 14 nets have more than two `F.Cu` pads, the largest at twelve, and still fail as
  `invalid_two_pin_net`;
- 3 of the 5 two-pin nets — `9V_RAW`, `L_IN_RAW`, `R_IN_RAW` — carry diagonal same-net `F.Cu`
  segments, which are still not modeled exactly;
- the remaining 2, `L_ISO` and `R_ISO`, now pass same-net classification with one orthogonal stub
  each and resolve into two components apiece, so they are genuinely attachable — and are then
  refused by the board's two **octagonal** mounting-hole track keepouts, which are not axis-aligned
  rectangles;
- behind that, the foreign GND zone boundary spans `(0.5, 0.5)`–`(51.5, 29.5)` mm, and its
  conservative envelope contains all four of those pad centres; and
- behind that, both nets' pad-centre deltas are 2.1 mm by 3.6 mm, which is off grid at the default
  250 µm step.

So the previous release note — "all five two-pin nets already carry same-net copper" — was true but
misleading, because removing that one rule reveals four further contracts standing between this
board and a routed net. The honest summary is unchanged: Board IR handles a real two-layer audio
board today, and the router does not route one. Attachment is validated instead by purpose-built
fixtures whose KiCad DRC evidence is real. The next useful steps are multi-pin routing,
non-rectangular keepouts, and a freshness-bound fill authority that can distinguish an outline from
its current copper. The [roadmap](../roadmap.md) records those as separate contracts.

## Safety boundary

Zero `hard_internal_violations` means only that this implementation's supported-grid post-checks
passed. Successful serialization alone is not a KiCad DRC result. Candidate-bound DRC evidence
applies to one private replayed derivative plus its recorded context; it is not a durable candidate
file, a whole-board routing result, or production approval. A preview is a proposal, not an applied
route.
It says nothing about electrical behavior, SI/PI, EMC, thermal performance, DFM, fabrication
readiness, or hardware safety. Preview, persistence, and application require separate contracts.

A committed `blocked-pad.kicad_pcb` fixture places a 2 mm x 8 mm foreign-net pad between the two
endpoints. The router detours around it, and a KiCad 10.0.5 integration test asserts the resulting
board reports zero DRC errors and zero unconnected items, so that rectangular obstacle path is
checked against the authoritative tool rather than only against itself. No zone-specific KiCad DRC
claim is made: candidate DRC intentionally does not refill zones, and cached fill is not authority.
The separate `blocked-zone.kicad_pcb` fixture exercises KiCad parsing through deterministic,
read-only public preview and workspace-preservation checks, not authoritative zone-fill DRC.

A committed `partial-route.kicad_pcb` fixture carries a same-net stub across half the gap; the
router proposes only the remaining 10 mm, and a KiCad 10.0.5 integration test asserts the patched
board reports zero errors, zero warnings, and zero unconnected items, so attachment copper is
checked against the authoritative tool rather than only against itself. The `connected-net` and
`diagonal-stub` fixtures cover the already-connected outcome and the diagonal same-net refusal
through the public preview and need no KiCad.

See [ADR-0006](../adr/0006-bounded-deterministic-astar.md),
[ADR-0007](../adr/0007-disposable-kicad-candidate-snapshot.md),
[ADR-0008](../adr/0008-candidate-bound-kicad-drc.md),
[ADR-0009](../adr/0009-non-mutating-route-preview.md),
[ADR-0011](../adr/0011-existing-copper-obstacles.md),
[ADR-0012](../adr/0012-via-obstacles.md),
[ADR-0013](../adr/0013-polygon-zone-obstacles.md),
[ADR-0016](../adr/0016-same-net-attachment.md), and the [roadmap](../roadmap.md).
