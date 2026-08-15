# Deterministic A* Routing Baseline

## Status

`routing/astar.py` is a candidate-only CPU reference for one exact two-pin route. The separate
`adapters/kicad_route_patch.py` bridge can now serialize that exact replayed candidate into a
disposable KiCad board, and the internal KiCad service can bind that private derivative to
authoritative DRC evidence. A bounded, non-mutating preview now exposes that pipeline through MCP
and the CLI. The slice remains smaller than issue #10's complete acceptance target: it does not
route multiple nets, persist or export candidate boards, or apply copper. The protocol-independent
`RoutingJobWorker` can execute one redacted job under a bounded CAS lease, but request/result
persistence and MCP job tools remain separate roadmap gates.

The internal `LayeredBoardRouter` is a separate Board IR-bound proposal seam. It accepts only the
narrow two-signal-layer matrix in [ADR-0036](../adr/0036-board-ir-layered-proposal-adapter.md),
emits immutable paths and through-vias, and is covered by B-018. The read-only MCP
`preview_layered_route` surface now exposes that proposal seam with pad-reference net inference
and double compare-and-swap binding. A disposable serializer replays the request, emits
source-preserving segments and canonical full-stack through-vias, and proves a Board IR round
trip. `verify_layered_candidate` now gates that serializer with explicit path/via topology,
endpoint-layer, duplicate/crossing, and endpoint-via checks; its result keeps physical validation
explicitly `not_modelled`. The public tool deliberately does not invoke the serializer.
Authoritative DRC remains required before routing through vias can be marked complete.

## Accepted input

| Surface | First-slice contract |
|---|---|
| Snapshot | Canonical, digest-verified Board IR `0.4.0` |
| Request | One stable net ID, one stable signal-layer ID, exact base revision, seed, integer settings |
| Connectivity | Exactly two pads belonging to the net; both accessible on the selected layer |
| Constraints | One net-class width/clearance assignment; no selected-net length or differential-pair rule |
| Board | One hole-free, axis-aligned rectangular outline |
| Obstacles | Track keepouts of any simple polygon outline; foreign-net pads, segments at any angle, and through vias; conservative foreign-net solid-zone polygon envelopes; conservative foreign-net envelopes for arcs spanning at most half a turn |
| Attachment | Same-net selected-layer segments at any angle, as connectable copper rather than obstacles |
| Existing geometry | No off-axis pads; no same-net vias, zones, or arcs; no arc of any net spanning more than half a turn on the selected layer |
| Search | Four-neighbour orthogonal grid; east, north, west, south expansion order; multi-source and multi-target when same-net copper is present |
| Nets | Any pad count; two pads route as one path, more as a spanning tree over components |
| Output | Immutable orthogonal `RoutePatch` of one or more paths tied to the unchanged snapshot digest, or a typed already-connected record |

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

A keepout carries no net and no clearance of its own, so the routed net's class clearance is the
only rule that can apply — there is no second value to be stricter than. That margin is also
deliberately stricter than KiCad itself: KiCad's rule area prohibits tracks that *intersect* it and
applies no clearance, so a track 0.2 mm outside the boundary passes KiCad DRC while this model
demands the full half-width-plus-clearance offset. The error direction is refusing a route KiCad
would accept, never proposing one it would reject.

An axis-aligned rectangular keepout keeps its exact square-cornered inflation. A keepout with any
other simple polygon outline — the octagons KiCad emits for mounting-hole rule areas, or a concave
outline — becomes a `_PolygonObstacle` under the same conservative envelope model, exact integer
containment, inclusive intersection, and rational squared-distance geometry the foreign-net zone
envelopes use. The two paths are deliberately not unified: the polygon path offsets by Euclidean
distance and therefore rounds corners, which would be a strictly looser obstacle for a rectangle
than the exact square-cornered inflation, so rectangles keep the fast path and a regression test
pins both the rectangle's inflated bounds and the polygon's margin. Board IR admits only
`(polygon (pts (xy ...)))` outlines with at least three distinct vertices and non-zero area, so
curved and multi-loop rule areas are already refused by the adapter and no keepout that reaches the
router is unmodeled. Polygon-keepout bounds construction charges one obstacle check per inspected
vertex, and each keepout counts against `max_obstacles` exactly like every other modeled object.

Selected-layer copper outside the routed net is an obstacle rather than a rejection. A foreign pad
contributes a rectangle centred on the pad, with its sizes swapped on a quarter turn; an orthogonal
segment contributes the rectangle its centreline sweeps, grown by the ceiling of half its width.
Each is inflated by the routed half-width plus the stricter of the routed net's and the obstacle
net's class clearance, so a board mixing net classes cannot be routed to the looser rule.

A through via outside the routed net contributes the bounding box of its outer diameter; Board IR
v0.2 admits through vias only, so every via provably crosses the routed layer. Drill diameter is
ignored because copper, not the hole, is what a track must clear. A via on the routed net still
refuses *routing*, because a layer change is not something this single-layer search can model, but
it no longer hides the net: it is a connectivity joint, as described under vias below.

A foreign pad carrying a custom copper envelope contributes the envelope's transformed containing
box as an obstacle. The anchor remains the only under-approximating attachment core; the router
never terminates in primitive-only copper. Arbitrary pad rotation uses the documented containing
farthest-corner circle, so it may refuse a legal route but cannot cross-read copper. Exact primitive
parity with KiCad is not claimed.

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
diagonal segments *on the routed net* still fail closed. Every obstacle, including one polygon zone
regardless of vertex count, counts against `max_obstacles`, and so does every same-net attachment
segment.

A foreign-net segment at any angle is an obstacle rather than a refusal. An orthogonal one keeps the
exact swept-rectangle fast path. A diagonal one contributes a conservative six-vertex envelope: the
Minkowski sum of its centreline with an axis-aligned square of its half width, which is the convex
hull of the two squares at its endpoints. Because the true track is the centreline swept with a
*disc* of that half width, and the disc is inscribed in the square, the envelope provably contains
the track — and every vertex is an exact integer with no rounding step anywhere, rather than a
float offset rounded outward and argued about afterwards. The price is over-approximating the
perpendicular extent by at most `(sqrt(2) - 1)` half widths, roughly 41%, which can only refuse a
route and never permit a violation. Offsetting that envelope by the routed half-width plus the
stricter of the two class clearances is a superset of offsetting the true stadium by the same
margin, so the inflation rule is identical to the orthogonal path. Diagonal envelopes charge one
obstacle check per vertex and count against `max_obstacles` like any other object.

Diagonal copper on the *routed* net is handled by the mirror-image construction, described under
same-net attachment below. The direction of error differs because the purpose differs: an obstacle
is over-approximated, attachment copper is under-approximated.

## Same-net attachment

A same-net segment on the selected layer is attachment copper: never an obstacle, and a legal place
for the proposal to begin or end. Deciding that requires a second rectangle model that errs the
opposite way from the obstacle model. Obstacle rectangles over-approximate copper so a clearance is
never understated; connectivity rectangles under-approximate it, because claiming copper that is not
there would assert an electrical connection the board does not have.

A track's connectivity core drops its round end caps and floors the half width. A pad's core is the
largest axis-aligned rectangle provably inside its shape: the whole rectangle for `rect`, inset by
the corner radius for `roundrect`, the central band for `oval`, and a centre line for `circle`.
Every core is a subset of real copper, so overlapping cores prove overlapping copper, while the
residual error can only fail to notice a connection.

A diagonal track has no single axis-aligned inner rectangle, so it contributes a **chain of squares**
instead. Centres are placed at `start + (delta * i) // steps`; flooring moves a centre less than a
nanometre per axis off the exact centreline point, so it stays within `sqrt(2) < 2` of the track, and
a two-nanometre tolerance is subtracted from the usable half width to absorb that. The square half
side `s` then satisfies `2 * s^2 <= (radius - 2)^2`, which by the triangle inequality on
distance-to-a-set puts every point of every square inside the real copper. `steps` is chosen so
consecutive centres differ by at most `2 * s` per axis, which is exactly when two closed squares of
that size still touch — so the chain is one connected component by construction. The first and last
squares are centred exactly on the track's endpoints, which is what lets a diagonal stub reach the
pad it is soldered to and be picked up at its far end. Endpoints are canonically ordered first, so a
track recorded in either direction produces the identical chain, and squares are emitted in ascending
order. Each square charges the obstacle-check budget as it is generated, so a track too long for the
budget fails closed; one whose floored half width does not exceed the tolerance cannot be modelled at
all and is refused with a distinct diagnostic. The chain is deliberately coarser than the copper —
squares reach about `0.7 * radius` — so attachment is possible only near sampled points, and the
residual error is under-connection, never a claimed connection that does not exist.

The standard way to decide connectivity is **exact shape intersection**: extraction and LVS engines
such as Magic, and detailed routers such as TritonRoute, test the real copper shapes against one
another and call any overlap a connection. This implementation deliberately diverges by testing
*under-approximating* cores instead. The trade is asymmetric on purpose — an exact test is right in
both directions, whereas a core can only ever miss a connection that exists, never invent one that
does not, and a router that under-connects proposes redundant copper while one that over-connects
claims a net is finished when it is open. The pad cores also play the role TritonRoute calls **pin
access points**: the finite set of places a search may legally enter a pad. Everything here is exact
integer arithmetic on nanometres, which sidesteps the floating-point robustness problem that
Shewchuk's adaptive predicates and CGAL's exact-predicate kernels exist to solve; the cost is that
every construction must be expressible in integers, which is why cores are inscribed rectangles
rather than offset curves.

Components are exact integer union-find over the two pad cores and every same-net segment core, with
closed rectangle intersection as the connection test — exact contact counts. The lowest index always
wins a union, so a component's root never depends on discovery order. Each pair comparison charges
the obstacle-check budget, so the every-64-checks cancellation cadence applies unchanged.

Connectivity is asked of nets of any width. When every pad of the net lands in one component the
router returns a typed `RouteConnection` instead of a candidate, whatever the pad count; its
`pad_count` field distinguishes the cases, and its `start_pad_id`/`end_pad_id` are the
lexicographically first and last pads, which bound the set rather than naming a route. A multi-pin
net that is not fully connected now enters the bounded tree path, which supports up to nine evolving
components. Larger or unsupported cases still fail closed with the typed `invalid_two_pin_net`
diagnostic. A net carrying a same-net via or zone is never claimed connected, because that copper
is not represented here; a two-pin net names the via or zone directly, while a wider one is refused
for its pad count, which is the more useful fact about it.

If both pads land in one component the router returns a typed `RouteConnection` instead of a
candidate: the net is already connected on the selected layer and there is nothing to route. That is
a terminal success, not a `RouteFailureCode`. Otherwise the search is seeded from every lattice node
the source component's segment cores cover and terminates on any node the target component's cores
cover; a rectangle's covered index range is solved directly rather than by scanning the lattice, and
each emitted node charges the obstacle budget. Pads contribute only their centre node for two-pin
routing, so a board without same-net copper produces byte-identical geometry to the single-source
contract. Multi-pin attachment seeds from pad cores so off-grid pad centres do not make a valid tree
impossible.

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
failure, but a real quality consequence. A same-net via, a same-net zone, and an endpoint pad whose
shape is not modeled exactly all still fail closed. The component analysis is skipped entirely when
the net carries no same-net selected-layer segment, so two overlapping same-net pads with no track
between them are still routed redundantly.

That `track_dangling` behaviour is also what makes the diagonal fixture's DRC evidence mean
something. The committed `diagonal-stub.kicad_pcb` carries a stub running diagonally from one pad to
`(16, 19)`; the router picks it up at that far end and adds 18 mm where an empty board needs 20 mm,
and KiCad reports nothing. Displacing the same proposal by 0.5 mm so it misses the stub end produces
two `track_dangling` warnings and one unconnected item, so a clean report is positive evidence that
the under-approximating chain put the attachment point on real copper rather than merely near it.

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

The off-grid diagnostic additionally carries a typed `OffGridEvidence`: the off-lattice pad, its
lattice anchor, the pitch in use, the signed per-axis miss to the nearest lattice line, and the
greatest common divisor of the two pad-centre deltas. `RouteDiagnostic` enforces a biconditional —
this evidence appears on the off-grid code and on no other, and never appears absent from it — so
the oracle, which shares `_prepare` and therefore raises the same failure, passes it through rather
than rebuilding a diagnostic the constructor would reject
([ADR-0093](../adr/0093-actionable-off-grid-refusals.md)).

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
exact candidate match. Every modeled source geometry object, including a footprint, must have a
native UUID/tstamp; the bridge rejects revision-derived geometry IDs because rewriting derivative
metadata would otherwise change them. Each compressed route edge becomes one root-level KiCad
segment with exact decimal units and a deterministic UUIDv5 derived from candidate identity and edge
order. Native UUID/tstamp identities are collected once for constant-time collision checks, and the
derivative records `copper-mcp` plus its package version as its KiCad writer.

The rendered bytes stay under the same parser, byte, and total-object budgets and are parsed back
through the supported KiCad adapter. The complete modeled content must equal the base snapshot after
replacing only source revision and writer provenance and appending the expected segments. The
function first runs the pure bounded layered-candidate topology verifier; it refuses disconnected,
crossing, duplicate, stale, and unsupported endpoint-via geometry before rendering. It performs no
file write, durable export, subprocess call, preview, MCP action, or board mutation.
`run_layered_route_candidate_drc()` is the separate internal boundary that invokes
authoritative KiCad DRC against this exact replay; it does not make the layered candidate public or
grant apply authority.

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

The layered counterpart, `run_layered_route_candidate_drc()`, accepts the original
`LayeredRouteRequest`, replays it through `LayeredBoardRouter`, renders the two-layer derivative with
full-stack through-vias, and feeds the same private context and fixed CLI vector to KiCad. Its frozen
evidence binds the layered candidate, Board IR base revision, source bytes, patched board, complete
DRC context, and aggregate summary. A KiCad 10.0.5 run against the blocked-pad fixture reports zero
errors and zero unconnected items while preserving the source inode, mtime, and bytes. This is a
candidate-evidence gate, not multilayer completion, negotiated congestion, or FreeRouting parity.

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

## Zone fill as connectivity evidence

A `filled_polygon` node records where KiCad poured copper at some past moment, and nothing in the
file says whether it still matches the board around it. Board IR therefore discards it, and a
same-net zone vetoes any connectivity claim by default. That default is lifted only by evidence: with
`include_fill_authority`, KiCad refills a private disposable copy and the recomputed pour must
reproduce the board's cache exactly. Matching means the two *are* the same geometry, so there is no
ambiguity about which one the claim describes; a mismatch is the typed `stale_fill` refusal, and
neither version is silently preferred.

The comparison is over canonical geometry rather than file bytes, because KiCad rewrites and
reorders a board wholesale on save — a byte diff says nothing about whether the fill changed.
Islands are sorted and digested by layer, net and exact integer vertices, and the digest sorts its
input rather than trusting it to arrive canonical.

An **island** is the unit, not a zone. Verified against a board authored to force two disjoint
regions, KiCad 10.0.5 emits one `filled_polygon` node per connected region rather than one node per
zone stitched together by keyhole seams. Copper touching *different* islands is therefore not
connected, and the committed `zone-fill-islands.kicad_pcb` fixture exists to keep that honest.

Freshness is a constructor invariant: `ZoneFillAuthority` refuses to exist when its two digests
differ, so a stale record cannot be built and then misread. The workspace board is never refilled —
`--refill-zones --save-board` reaches only the disposable copy, every other DRC path in this
repository omits those flags, and the source is recaptured and compared afterwards. Fill never
enters Board IR: the router accepts verified islands as a parameter and never fetches them, so
snapshots and their digests are unchanged and KiCad execution stays out of the search.

For a foreign net, a matching fresh island set replaces that zone's conservative outline on the
selected layer; each island is then an exact polygon obstacle with the strictest governing
zone/net clearance plus the candidate track half-width. When multiple zones share a net/layer, the
strictest zone clearance is used because island records do not carry a zone identifier. A fill
island without a matching Board IR zone, with a different source revision, with fewer than three
vertices, or whose bounding box escapes the bounding box of a backing zone of the same net and
layer, is refused before search. The last of those is the gate ADR-0070 added to the ordered-layer
adapter, applied here by [ADR-0101](../adr/0101-fill-currency-is-not-in-the-document.md) so both
routers refuse the same evidence. It is a consistency cross-check, not the soundness proof: the
shrink is sound because `run_zone_fill_authority` returns a **board-complete** island set that
KiCad has just recomputed, and containment cannot detect a caller that omits an island. That is
also why the single-layer router still performs no shape validation of evidence supplied at its
typed in-process seam, where the layered adapter does — recorded as `R-147`, not closed.

Once freshness-bound, the pour is KiCad's own authority on where that copper is, so contact testing
uses the polygon itself with exact integer geometry. Pad and track cores stay under-approximating;
only the pour is exact. Reading is bounded by `max_fill_vertices`, default **500,000** — 16 MiB of
source at the densest pour measured, 29,503 vertices per mebibyte, following ADR-0079's rule. It
replaces the 50,000 ADR-0021 derived from CopperTone's 4,314-vertex pour, which refused seven of
eighteen real zoned boards (50,482–130,305 vertices) before freshness could be considered. The
budget sits *behind* the parse it appears to guard — refusing the largest board still costs 86 % of
what reading it costs — so the defence against an unbounded vertex list is `ParseLimits`, which also
caps the population at 741,375 vertices; ADR-0104 prices the change at 6.5 s and 21.6 MiB of
adversarial headroom. Note that it meters a board's **total** pour while the ordered-layer adapter
refuses any single island above 4,096 vertices, a ceiling real boards reach (widest observed 43,889)
and R-150 tracks. B-021 measures the narrow fill-aware routing core on a
synthetic corridor: ten deterministic replays reduce wire length from 14,000 nm to 8,000 nm. The
public preview now carries the same freshness evidence on routed candidates when
`include_fill_authority` is set, with a typed `routing_effect` so an AI host can distinguish exact
foreign-zone obstacles from same-net connectivity evidence. This is provenance, not a DRC or
fabrication guarantee.

A fill-routed candidate records the model that produced it, as `fill_binding` — the content
address of the fill the router was handed — and every verifier that replays a candidate refuses
any other model (ADR-0103). Before that, `replay` dropped the fill and searched the envelope, so a
fill-routed candidate disagreed with itself: on B-021's fixture a route solving at 8,000 nm
replayed at 14,000 nm, and `preview_route(include_fill_authority=True, include_drc=True)` refused
a legitimate candidate. The ordering of the two models is what made the failure benign — the
envelope over-approximates the pour, so the replay was *stricter* than the route — and the binding
equality now forbids the looser direction as well, which is the one that would confirm geometry the
router never proved.

The ordered-layer path carries the same binding and the same equality
([ADR-0106](../adr/0106-layered-fill-authority-is-public-and-bound.md)), computed by the same
function over the same `VerifiedFill` values, so the two paths cannot disagree about what "the same
fill" is. `LayeredBoardRouter.replay` refuses `fill_evidence_mismatch` before it searches, and
`render_kicad_layered_candidate_board` goes through it. That landed **before** the public layered
flag rather than alongside it, because the flag is what would have made the gap reachable. With the
binding in place, `preview_layered_route` accepts `include_fill_authority` and reports a
`routing_effect` over the signal layers the search reached. Two honest limits: no layered candidate
identity moves, because the binding enters the canonical payload only when it exists; and the
per-island 4,096-vertex ceiling above means the flag refuses the whole request on the 14 of 18
corpus boards that exceed it, which is `R-152`.

## Multi-pin trees

A net with more than two pads is routed by sequential component merging. Connectivity analysis
produces the net's initial components. For at most nine evolving components, the clean-room
`batched-1-steiner-v1` policy scores component pairs by the exact bounding-box gap minus a bounded
median-point one-Steiner savings term, with stable index tie-breaks. It is a topology *ordering*
guide, not a FLUTE lookup-table implementation or an optimality certificate. Larger nets retain the
`component-mst-v1` order until a separately budgeted decomposition policy exists. Each ordered edge
is one leg, routed by the same multi-source/multi-target search a two-pin stub attachment uses: the
source component's copper supplies the seeds and the target component's the goals. A routed leg's
copper joins the merged component, so later legs may attach anywhere along it, and because legs are
same-net copper they are never obstacles to one another.

What that claims is narrow and worth stating exactly: every pad ends in one component, each leg is
optimal for the obstacles present *at the time that leg was routed*, and the whole result is
reproducible. It does not claim Steiner optimality, a global tree optimum, or FreeRouting parity,
and it does not revisit an earlier leg once a later one is routed. The selected ordering policy is
recorded in candidate identity, so a future FLUTE-guided, learned, or decomposed topology can be
added as a new policy without changing the contract.

Multi-pin legs seed from pad **cores** rather than pad centres. Requiring every pad centre to sit on
one lattice is unworkable in practice: on this repository's own CopperTone board the largest grid
step that puts all pads of a multi-pin net on one lattice is 5 um for six of the nine such nets,
which is a 62-million-node lattice against a 500,000 ceiling. Seeding from cores removes the
constraint for every pad but the anchor, and a round pad only offers usable core area because of the
inscribed square described under same-net attachment. Two-pin nets keep centre seeding, so their
geometry is unchanged.

Budgets are shared across the whole tree rather than allocated per leg, because one candidate should
honour one caller ceiling. Merge order and budget consumption are both deterministic, so budget
exhaustion fails at a reproducible leg with reproducible counts. Any leg failing refuses the whole
call: a partial tree is not a candidate, and emitting one would break the candidate invariant that a
proposal has no unrouted connections.

A committed `tree-star.kicad_pcb` fixture places four pads with no existing copper; the router
returns a three-leg tree that KiCad 10.0.5 accepts with zero errors, warnings and unconnected items.
That evidence is discriminating rather than merely green: rendering the same board with any single
leg removed makes KiCad report an unconnected item.

## Measured coverage on a real board

The polygon model is validated by exact synthetic geometry cases; the rectangular existing-copper
model also has a purpose-built blocked-pad KiCad DRC fixture. It is therefore worth stating plainly
what neither yet reaches. Measured against the repository's own
[CopperTone](../../hardware/coppertone-buffer/README.md) board source at SHA-256
`3bcd01ec4942fccabfaf1c21bdae050a31a7bf99af7ab1bcb0dbb3d0aabcfb94`:

| Stage | Result |
|---|---|
| Board IR conversion | Supported — 2 copper layers, 14 nets, 26 footprints, 55 pads, 53 segments, 9 vias, 2 zones, 2 keepouts |
| Nets reaching a terminal outcome on `F.Cu` | 14 of 14, all `already_connected` (`GND` needs `include_fill_authority`) |
| Nets routed on `F.Cu` | 0 of 14 |

Conversion is not the blocker; the router's contract is. Six refusals have been removed in
succession — the same-net partial-routing veto (ADR-0016), non-rectangular track keepouts,
foreign-net diagonal segments (ADR-0017), a mirrored footprint-rotation defect in the adapter,
diagonal copper on the routed net (ADR-0018), and the assumption that connectivity is only a
two-pin question. The first three moved nothing. The fourth moved the number, because it was not a
router limitation at all: pads on every rotated footprint were placed at their mirror image, so the
router was being asked about a board that did not exist. The fifth resolved the rest of the two-pin
surface, and the sixth extended the same analysis to nets of any width.

Measured per net at the default 250 µm grid step, twice, with identical results:

| Nets | Outcome |
|---|---|
| 5 of 14 — `9V_RAW`, `L_IN_RAW`, `R_IN_RAW`, `L_ISO`, `R_ISO` | **`already_connected`**, two pads each |
| 6 of 14 — `L_BUF`, `R_BUF`, `L_IN_BIASED`, `R_IN_BIASED`, `R_OUT`, `VREF` | **`already_connected`**, three to seven pads each |
| 2 of 14 — `VCC`, `L_OUT` | **`already_connected`** across layers, through two vias and one via respectively |
| 1 of 14 — `GND` | **`already_connected`** through two fill islands and six vias, **only with `include_fill_authority`**; refused without it |

Every net on this board is one the designer already routed, and the router now recognises eleven of
the fourteen. The widest is `VREF` at seven pads joined by ten segments. There is nothing to route on
any of them, and saying so is the correct answer rather than a consolation prize.

The three refusals are honest rather than incidental. `GND`, `VCC` and `L_OUT` carry same-net vias,
and a via is copper on another layer that this single-layer model does not represent. Their pads may
well be connected through it, but nothing here can show that, so the router declines to claim it and
falls back to the pad-count refusal — which is also true, since routing a multi-pin net remains
unsupported. `GND` additionally carries a same-net zone, which is refused for the same reason.

The claim is bound to authoritative evidence, though not the usual kind: an already-connected
preview emits no candidate, so candidate-bound DRC has nothing to replay. The available
authoritative check is the board-level one — `kicad-cli pcb drc` reports **zero unconnected items**
on this board, so KiCad agrees every net is fully connected. A regression test asserts both halves
together across all eleven nets.

It is worth being precise about what this is not. Zero nets are *routed*: no copper has been proposed
for CopperTone, and none is needed for the nets it can currently reason about. Multi-pin **routing**
now exists, and it changes nothing here — every net on this board is already routed by its designer,
so there is no tree left to build. That is why the multi-pin slice is validated by purpose-built
fixtures rather than by this board, and the roadmap's old framing of multi-pin routing as the single
remaining contract for CopperTone was simply wrong.

Via-aware connectivity resolved two of the three, and the zone fill authority resolved the last:
`VCC` and `L_OUT` are joined across the back layer through their vias, and `GND`'s twelve pads are
joined by its ground pour. Every net on this board is now recognised — but `GND` only when the
caller passes `include_fill_authority`, because believing a pour costs a KiCad refill and a claim
that the board's cached fill is what KiCad recomputes from it today. Without that flag `GND` is
still refused, which is the honest default rather than a regression.

Behind that sit routing *through* vias, which needs Board IR-bound layer-aware geometry this router
does not yet have. An internal abstract two-layer A* oracle now exercises the maze-level primitive
with explicit via transitions and deterministic budgets, but it does not model nanometre trace
width/clearance, via annuli/drills, keepouts, net ownership, or KiCad serialization/DRC. The
general production route contract therefore remains single-layer; the separate layered preview
supports only its documented two-signal-layer subset and does not make this board a fully routed
production design.

Board IR handles a real two-layer audio board today, and the layered preview now routes only a
bounded fixture subset with opt-in candidate-bound DRC evidence. Attachment, polygon keepouts, and
diagonal envelopes remain validated by purpose-built fixtures whose KiCad DRC evidence is real and
which are checked to be discriminating. The
[roadmap](../roadmap.md) records the remainder as separate contracts.

## Safety boundary

Zero `hard_internal_violations` means only that this implementation's supported-grid post-checks
passed. Successful serialization alone is not a KiCad DRC result. Candidate-bound DRC evidence
applies to one private replayed derivative plus its recorded context; it is not a durable candidate
file, a whole-board routing result, or production approval. A preview is a proposal, not an applied
route.
It says nothing about electrical behavior, SI/PI, EMC, thermal performance, DFM, fabrication
readiness, or hardware safety. Preview, persistence, and application require separate contracts.

A committed `blocked-pad.kicad_pcb` fixture places a 2 mm x 8 mm foreign-net pad between the two
endpoints. The router detours around it, and the public `preview_layered_route` path can now opt in
to the same private candidate replay: a KiCad 10.0.5 integration test asserts zero DRC errors, zero
warnings, and zero unconnected items, with candidate/source/patched/context binding and unchanged
source bytes, inode, and mtime. No zone-specific KiCad DRC claim is made: candidate DRC intentionally
does not refill zones, and cached fill is not authority.
The separate `blocked-zone.kicad_pcb` fixture exercises KiCad parsing through deterministic,
read-only public preview and workspace-preservation checks, not authoritative zone-fill DRC.

A committed `partial-route.kicad_pcb` fixture carries a same-net stub across half the gap; the
router proposes only the remaining 10 mm, and a KiCad 10.0.5 integration test asserts the patched
board reports zero errors, zero warnings, and zero unconnected items, so attachment copper is
checked against the authoritative tool rather than only against itself. The `diagonal-stub.kicad_pcb`
fixture does the same for a stub that leaves its pad diagonally, and carries its own KiCad
integration test. The `connected-net` fixture covers the already-connected outcome through the
public preview and needs no KiCad.

A committed `octagon-keepout.kicad_pcb` fixture places an octagonal track rule area between the
endpoints. The router detours around it, and a KiCad 10.0.5 integration test asserts zero errors,
warnings, and unconnected items. That fixture is checked to be discriminating rather than merely
green: a straight track through the same rule area makes KiCad report `items_not_allowed` as an
error.

A committed `diagonal-blocker.kicad_pcb` fixture runs a foreign `POWER` track diagonally across the
straight corridor between the endpoints. The router detours, and a KiCad 10.0.5 integration test
asserts zero errors, warnings, and unconnected items. It is discriminating in the same way: the
straight route the router used to be unable to consider makes KiCad report `tracks_crossing` as an
error. The envelope's superset property is separately covered by a test that samples the exact
integer stadium across seven orientations and requires every copper point to fall inside the
envelope.

See [ADR-0006](../adr/0006-bounded-deterministic-astar.md),
[ADR-0007](../adr/0007-disposable-kicad-candidate-snapshot.md),
[ADR-0008](../adr/0008-candidate-bound-kicad-drc.md),
[ADR-0009](../adr/0009-non-mutating-route-preview.md),
[ADR-0011](../adr/0011-existing-copper-obstacles.md),
[ADR-0012](../adr/0012-via-obstacles.md),
[ADR-0013](../adr/0013-polygon-zone-obstacles.md),
[ADR-0016](../adr/0016-same-net-attachment.md),
[ADR-0035](../adr/0035-internal-layered-search-oracle.md),
[ADR-0036](../adr/0036-board-ir-layered-proposal-adapter.md), and the [roadmap](../roadmap.md).
