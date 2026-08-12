# Roadmap

Roadmap items describe outcomes, not promises about dates. Each milestone requires tests,
documentation, ledger updates, and benchmark evidence.

## Milestone state

**The GitHub milestones are the source of truth, not the checkboxes below.** A checkbox records
engineering sub-state and is written by hand; a milestone's issue counts are derived. Where the two
disagree, believe this table and file the discrepancy. Read live with
`gh issue list -R seunghyukchoe/copper-mcp` and
`gh api repos/seunghyukchoe/copper-mcp/milestones`.

As of 2026-08-12:

| Milestone | Closed | Open | State |
|---|---|---|---|
| M1 — KiCad inspection completion | 7 | 1 | Open. The sole remaining issue is [#116](https://github.com/seunghyukchoe/copper-mcp/issues/116), the real-board conversion survey. |
| M2 — Routing depth | 3 | 2 | Open: [#63](https://github.com/seunghyukchoe/copper-mcp/issues/63) fill-aware zone routing obstacles and [#65](https://github.com/seunghyukchoe/copper-mcp/issues/65) benchmark comparison against open baselines. |
| M3 — Safe application completion | 0 | 1 | Open: [#68](https://github.com/seunghyukchoe/copper-mcp/issues/68), IPC one-undo-commit apply. |
| M4 — Scene, policy, and evaluation | 3 | 0 | **Complete.** Every tracked issue is closed. |
| M5 — Performance and physics | 0 | 6 | Not started. |

Two cautions about this table, because both have misled a reader before:

- **"M4 complete" means every issue tracked under M4 is closed. It does not mean the M4 section
  below has no `[~]` items left.** Several genuinely remain — broader source fidelity, editor
  authority, solving for a placement, and the policy-plugin work — and they are untracked rather
  than done. A milestone closing is an accounting fact about the tracker.
- Three conversion gaps filed after the M1 survey —
  [#138](https://github.com/seunghyukchoe/copper-mcp/issues/138),
  [#140](https://github.com/seunghyukchoe/copper-mcp/issues/140) and
  [#141](https://github.com/seunghyukchoe/copper-mcp/issues/141) — carry **no milestone** and so
  appear in none of the counts above. They are real open work; see
  [what does not convert](#what-does-not-convert).

## M0 — Repository foundation (`0.1.x`, complete)

- [x] Secure workspace and file boundary.
- [x] Content-addressed board manifests.
- [x] Candidate schemas and comparison.
- [x] MCP and CLI adapters over shared services.
- [x] Governance, security, CI, releases, and ledgers.
- [x] Reproducible KiCad audio-board preview and artifact-validation workflow.
- [x] Licence-aware, network-free audio capability corpus with original/open fixtures and
  reference-only external source metadata.
- [x] First public GitHub release.

## M1 — KiCad inspection and validation (current)

### What does not convert

The one open M1 issue is the real-board conversion survey. Measured on the private working corpus
on 2026-08-12, before and after ADR-0096 back to back: **12 of the 12 boards in the
[#116](https://github.com/seunghyukchoe/copper-mcp/issues/116)
survey set convert, which is 12 of all 17 boards in that corpus as saved today** — up from 11 and
11, the one board gained being the phono-preamp save that carried `connect`-kind pads
([#138](https://github.com/seunghyukchoe/copper-mcp/issues/138), resolved by [ADR-0096](adr/0096-edge-connector-pads-convert-as-smd.md)). Five saves still
refuse, each for exactly one named construct:

| Refusing saves | Construct | Issue |
|---|---|---|
| 4 | root board `property` text variables | [#140](https://github.com/seunghyukchoe/copper-mcp/issues/140) |
| 1 | a root copper graphic (`gr_text` on `F.Cu`) — **answered and staying refused**, see [ADR-0095](adr/0095-copper-text-has-no-derivable-envelope.md) | [#141](https://github.com/seunghyukchoe/copper-mcp/issues/141) |

**No "converts every board" result is claimed at any count**, and none should be stated until a
re-measured survey supports it. The counts above supersede earlier survey figures — including
#116's own title, written before four of the five newer saves existed — rather than correcting
them in place. Dated research notes and benchmark-ledger rows keep whatever they measured on the
day they measured it.

- [x] Official `kicad-python` IPC plugin and redacted live-board observer. The optional
  `inspect_live_board` MCP tool and `hardware/kicad-ipc-plugin` action use only local KiCad IPC,
  refuse future binding versions by default, and return a digest plus bounded counts without
  board text, net names, UUIDs, or geometry. Live editor-to-Circuit-Scene binding remains a
  separate item.
- [x] Canonical Circuit Intent IR `0.1.0` and deterministic in-memory KiCad schematic generation
  contract for a bounded two-pin passive subset.
- [x] Bounded Circuit Intent build service, explicit create-new CLI schematic export, and
  stdio-only opaque MCP resource delivery with redacted verification metadata.
- [x] Deterministic passive-layout readability baseline with wider grid placement, extended leads,
  separated labels/properties, real KiCad SVG inspection, and a structural regression.
- [x] Descriptor-anchored workspace reads and exact-lowercase create-only schematic output.
- [x] Schematic round trip, authoritative ERC, and source-to-board connectivity parity
  (issue #66, closed). The
  bounded passive subset now has exact deterministic schematic replay, authoritative `kicad-cli sch
  erc` evidence bound to the generated schematic digest, and a live KiCad round trip that exports a
  `kicadxml` netlist and checks recovered components and nets against the source intent through the
  reusable `kicad_schematic_parity` verifier. Source-to-board connectivity parity now ships as
  `verify_source_to_board_parity`, running the authoritative `kicad-cli pcb drc --schematic-parity`
  against a **board-eligible projection** of the intent under its own digest, and refusing a
  verdict outright unless KiCad demonstrably accounted for every component — an empty parity array
  is also what a check that never ran produces. A `passed` verdict is deliberately **not** a claim
  that the delivered schematic file matches the board: that file marks every symbol `on_board no`
  and never enters KiCad's board-side netlist. Broader symbol/library coverage remains open, and
  parity is not ERC, footprint correctness, electrical validation, or board readiness.
- [~] Live KiCad IPC snapshot to Circuit Scene and route-proposal binding. `observe_live_board_scene`
  converts the exact captured IPC serialization through Board IR, and `preview_live_route` now
  returns a deterministic read-only candidate from a scene `net_ref_id` with both stale-session
  digests. `preview_live_placement` now reuses the same exact snapshot → Board IR → legalizer
  path for a ref-anchored, read-only placement candidate. `preview_live_layered_route` now adds a
  session-token, source-digest, and Board IR-digest-bound via-capable proposal with fake-IPC
  replay evidence. A local fidelity oracle now exercises source → Board IR → Circuit Scene digest
  binding, redacted capability outcomes, and one cooperative deadline through a fake official-client
  seam; the ordinary shell receives the canonical plugin-credential-absent skip. No successful
  real-editor oracle run has been recorded, and live action compare-and-swap before placement or
  routing remains open because the workstation IPC server is disabled.
- [x] Canonical Board IR v0.2 contract with integer units, typed constraints, strict codecs,
  content digests, first-class footprint pose/side/lock/pad ownership, and bounded simple closed
  courtyard shapes: unfilled `fp_rect`, `fp_poly`, and unordered complete `fp_line` cycles whose
  every edge is horizontal, vertical, or an exact 45-degree chamfer, plus unfilled `fp_circle`
  outlines of exact integer radius (ADR-0080). The immutable v0.1 schema remains as legacy
  compatibility evidence.
- [x] Board IR application-service and MCP exposure as a read-only structural summary.
- [x] Broader KiCad geometry and rule coverage (issue #67, closed). Foreign-net arc tracks spanning
  at most half a turn are a conservative integer polygon envelope obstacle on the single-layer
  router, with distinct typed refusals for an arc past half a turn and for an arc on the routed
  net. Since the issue was written the adapter has additionally accepted oval pad drills,
  45-degree-chamfered and exact-integer-radius circular courtyards (ADR-0080), copper carrying no
  routable net as a netless obstacle (ADR-0078), net-tie `fp_poly` copper as netless obstacle
  copper (ADR-0092), unlocked root groups (ADR-0090), and attaching pad `zone_connect` overrides
  (ADR-0091). **Still refused:** curved board outlines (`Edge.Cuts` arcs, circles, polygons,
  béziers), pad shapes outside `circle`/`rect`/`oval`/`roundrect` and custom pad primitives,
  `connect`-kind pads, placement-enabled rule areas,
  `fp_arc` courtyards, outline holes, and blind/buried/microvias. The residual real-board
  conversion gaps are counted in [what does not convert](#what-does-not-convert), and the
  authoritative accepted/rejected matrix is
  [the Board IR contract](architecture/board-ir.md#kicad-read-only-subset), not this list.
- [x] Copper stack validated against KiCad's own layer numbering rather than a synthesized
  arithmetic rule, correcting a defect that had refused every real board with more than two copper
  layers behind the same fail-closed diagnostic a two-layer board also uses. Declaration position
  fixes the layer name; the name fixes KiCad's own declared ID (`F.Cu=0`, `B.Cu=2`,
  `In{N}.Cu=2+2N`), so a four-layer board's IDs deliberately do not ascend. Board IR's own
  `Layer.index` and every two-layer content address are unchanged.
- [x] Board outlines assembled from `Edge.Cuts` `gr_line` segments. An outline may now be one
  unfilled `gr_rect` **or** `gr_line` segments that chain, by exact endpoint coincidence, into one
  closed simple loop, taking a composite native identity from its members' UUIDs (ADR-0076,
  ADR-0087, issue #111). The outline is routing room, so it is never repaired into something
  larger than what was drawn: an open contour, a near-miss gap, a spur, a duplicate or zero-length
  segment, a self-intersection, or two disjoint loops each still refuse.
- [x] Operator-configurable parse budgets. Six structural budgets — `max_tokens`, `max_nodes`,
  `max_children_per_list`, `max_objects`, `max_total_vertices`, `max_intersection_tests` — are now
  taken as configured through the matching `COPPER_MCP_MAX_PARSE_*` variables and one
  `parse_limits_for()` seam, so a budget moves for every board-reading service at once or for none
  (ADR-0079, issue #112). Previously thirteen call sites hardcoded them and only the byte ceiling
  moved. `max_input_bytes` deliberately keeps `min` semantics.
- [x] Headless `kicad-cli pcb drc --format json` validation.
- [x] Minimal KiCad child environment, private working directory, bounded private
  global-configuration/state roots, and snapshot-confined file-table dependencies.
- [x] Candidate preview without mutation.
- [x] Version-skew and stale-board tests for the DRC adapter.

## M2 — Deterministic routing baseline

Tracker state: 3 closed, 2 open — [#63](https://github.com/seunghyukchoe/copper-mcp/issues/63)
fill-aware zone routing obstacles and
[#65](https://github.com/seunghyukchoe/copper-mcp/issues/65) benchmark comparison against open
baselines. Note that the fill-aware routing item below is `[x]` for the single-layer A* core while
#63 stays open for the layered seam's public contract; the two are not the same scope.

- [~] Single two-pin A* routing with exact connectivity.
  - [x] Candidate-only integer four-neighbour reference with exact revision binding, deterministic
    identity, rectangular keepouts, bounded search, and cancellation.
  - [x] Reproducible synthetic A*/Dijkstra completion and optimal-cost oracle baseline.
  - [x] Replay-bound, read-only disposable KiCad patch serialization with Board IR round-trip checks.
  - [x] Internal candidate-bound authoritative KiCad DRC evidence over a private disposable board.
  - [x] Bounded, non-mutating public route preview over MCP and the CLI, with opt-in authoritative
    candidate DRC evidence.
  - [~] Durable routing jobs, candidate persistence, and export.
    - [x] Transport-independent redacted job records with revision CAS, SQLite reopen, bounded
      TTL/capacity, idempotent creation, and cooperative cancellation.
    - [~] Worker execution/leases, candidate persistence, durable export, and ordinary MCP
      start/get/cancel tools.
      - [x] Single-worker CAS leases, cooperative cancellation, stale-lease recovery, and
        fail-closed invalid-candidate publication are covered by ADR-0046 and B-028.
      - [x] Bounded redacted candidate-manifest persistence with restart, TTL, tamper, and
        RoutingJobSpec binding checks is covered by ADR-0047 and B-029.
      - [x] Bounded file-backed layered request/result persistence, authorization-bound route-
        geometry export, and ordinary MCP `start_routing`/`get_routing_job`/`cancel_routing_job`
        tools. Single-layer/live jobs and MCP Tasks negotiation remain open.
- [x] Existing selected-layer copper as exact rectangular obstacles.
- [x] Via obstacles on the selected layer, the first limit a real board hit.
- [x] Conservative polygon zone-boundary envelope obstacles with exact integer concave/diagonal
  geometry and vertex-level work accounting.
- [x] Non-rectangular selected-layer track keepouts, including the octagonal mounting-hole rule
  areas KiCad emits, under that same envelope model.
- [x] Diagonal foreign-net copper as conservative exact-integer swept-square envelopes.
- [x] Diagonal copper on the routed net as attachment copper, using a chain of exact integer squares
  that is provably inside the track and provably self-connected.
- [x] Foreign-net KiCad arc tracks spanning at most half a turn as a conservative integer polygon
  envelope obstacle on the single-layer router, using the same swept-square construction as
  diagonal segments with a larger radius (ADR-0072). An arc past half a turn and an arc on the
  routed net are each a distinct typed refusal; the layered proposal adapter's blanket arc refusal
  is unchanged, since its obstacle model is rectangles only.
- [x] Freshness-bound zone fill authority: cached fill is admitted as connectivity evidence only
  when a fresh KiCad refill on a disposable copy reproduces it exactly.
- [x] Fill-aware zone *routing*: the deterministic A* core replaces a matching foreign-zone
  envelope with freshness-verified exact fill islands, fails closed on stale/unmatched evidence,
  and the public preview advertises the evidence on routed candidates with a typed
  `routing_effect`; B-021 measures the route-quality improvement and B-022 measures the MCP
  provenance contract.
- [~] Multi-pin nets, since most real nets have more than two pads.
  - [x] Connectivity analysis for nets of any width, so an already-connected multi-pin net
    is recognised rather than refused.
  - [x] Routing a multi-pin net as a deterministic spanning tree over its components
    (`component-mst-v1`); Steiner-optimal topology is explicitly not claimed.
  - [x] Bounded one-Steiner-quality topology ordering behind the recorded `ordering_policy` seam
    (`batched-1-steiner-v1` for at most nine evolving components, with a measured 12.5% wire-
    length reduction on the four-pad fixture). This is not a FLUTE implementation or optimality
    claim; higher-degree decomposition and learned policies remain future work.
  - [x] Original audio multi-pin service benchmark: an Apache-2.0, zero-copper NE5532-class
    topology has 14 synthetic footprints, 35 pads, 11 nets, and eight independently replayed
    public-service candidates (four two-pad and four three-/four-pad). B-074 pins their identities
    and route metrics; optional KiCad 10.0.5 DRC runs only on independent disposable derivatives,
    reducing the selected net's unconnected count but remaining non-clean and non-combined.
- [x] Via-aware connectivity, so a net joined through another layer is recognised rather than
  refused. On CopperTone this resolved `VCC` and `L_OUT`; `GND` remains refused because it carries a
  same-net zone, whose fill is not trusted.
- [ ] Routing *through* vias, which needs a layer-aware lattice, a via-insertion cost, and a
  via-placement contract. Connectivity is multilayer today; path search is not.
  - [x] First algorithmic acceleration milestone: bounded two-signal-layer `(x, y, layer)` A*
    with explicit through-via transitions, positive via cost, per-layer obstacles, and
    deterministic budgets, delivered as an internal oracle only. Board IR binding, physical
    clearance/via legality, KiCad serialization, and DRC remain required before this becomes a
    routing capability.
  - [x] Internal Board-IR ordered-stack proposal seam for two through eight all-signal copper
    layers: deterministic full-stack-through-via transitions, layer-scoped track/via keepouts,
    stack/via/search budgets, topology replay, and a committed three-layer completion oracle
    (B-076). Omitted via policy preserves the historical two-layer behavior while 3..8 layers
    receive a deterministic 64-via effective cap. File, live, and durable public entry points
    explicitly reject non-two-layer stacks; generalized serialization, DRC, refill, and apply
    remain explicit promotion gates.
  - [x] Public, file-backed `preview_layered_route` MCP proposal with pad-reference net inference,
    double CAS, closed structured output, candidate-only full-stack vias, and explicit opt-in
    candidate-bound authoritative DRC evidence. This remains a read-only two-signal-layer surface;
    durable export, multilayer generalization, refill, and apply remain open.
  - [x] Live `preview_live_layered_route` proposal over the exact byte-confirmed KiCad IPC
    snapshot, with a redacted `KICAD_API_TOKEN` session CAS and file-oracle equality benchmark;
    real GUI, live DRC, serializer, and apply evidence remain open.
  - [x] Structural candidate verification and endpoint-via avoidance now gate the internal
    serializer; exact padstack treatment, edge/hole clearance, refill, and fabrication evidence
    remain open.
- [~] Board IR-bound ordered-layer proposal adapter (currently two through eight all-signal
  layers): exact grid attachment, conservative foreign
  copper/zone/pad envelopes, separate track/via keepouts, immutable candidate digests, and
  fail-closed stale/off-grid/unsupported diagnostics are benchmarked in B-018. Source-preserving
  segment/via serialization and Board IR replay are covered by an internal disposable serializer;
  B-020 now binds that exact replay to private authoritative KiCad DRC, and B-024 exposes a
  separate read-only candidate preview through MCP. A bounded topology verifier now gates the
  serializer and refuses endpoint-via geometry; exact padstack/clearance, durable export,
  generalized KiCad serialization/DRC, and apply authority remain open gates. B-032 now covers the narrow
  public file-backed DRC evidence binding.
  - [x] Candidate topology gate: revision/endpoint binding, path-via adjacency, ordered-stack
    full-stack transitions, duplicate/crossing rejection, bounded pair checks, and explicit
    physical-validation non-claim.
  - [x] Freshness-verified same-layer zone fill now replaces the layered adapter's conservative
    zone-outline envelope with verified fill islands, carried as bounding boxes since the layered
    lattice model is rectangular, behind four ordered fail-closed gates (ADR-0070, B-086). A public
    `preview_layered_route` fill-authority contract with an ADR-0040-style `routing_effect` label,
    same-net poured attachment in the layered seam, and exact polygon layered collision remain
    open.
- [x] Attachment to existing same-net copper and bounded partial-route completion.
- [ ] Multilayer vias and keepouts.
- [x] Negotiated-congestion multi-net routing (issue #62, closed). The sub-items below record what
  the closed issue delivered and, where a sub-item is `[~]`, what it explicitly did not.
  - [x] Read-only route-bundle preview: two through eight known net references now compose into
    one immutable revision-bound plan only after deterministic whole-composition replay and the
    existing exact cross-net clearance gate. B-079 records a KiCad-checked private combined
    derivative; apply/export, multilayer, and general-board claims remain separate.
  - [x] Bounded candidate-only present/history congestion ledger with exact lattice edge/vertex
    occupancy, deterministic rip-up order, policy-digest-bound candidates, cancellation, and
    fixed iteration/routing budgets.
  - [x] B-036 KiCad-fixture replay: sequential baseline overflow `1` versus negotiated overflow
    `0` across three deterministic replays; physical clearance, multilayer capacity, KiCad DRC,
    and held-out corpus comparison remain open.
  - [x] Bounded same-layer candidate-pair physical acceptance gate: exact integer swept-disc
    clearance over orthogonal segments, assigned-width checks, the stricter pairwise net-class
    clearance, pair-check/cancellation budgets, and atomic discard of an invalid allocation.
    Generic router output is independently replayed by the deterministic reference core under a
    shared half-budget before publication. B-058 records a lattice-clean synthetic pair with
    `300,000 nm` available copper-edge clearance versus `500,000 nm` governing clearance,
    rejected deterministically.
    KiCad DRC, existing-board copper, pads, vias, zones, custom rules, multilayer geometry, and
    physical-conflict-guided rerouting remain open.
  - [x] Three separately declared, separately digest-bound negotiation policy slots — net order,
    per-iteration cost-update rule, and rip-up selection — each a closed enumeration member plus
    bounded integer weights that compose into one plan digest the published evidence re-derives
    (ADR-0073). This is opt-in behind a distinct `negotiated-congestion-plan-v4` identity; absent a
    declared plan, the existing coordinator, ordering, accounting, and `negotiated-congestion-v2`
    identities are byte-for-byte unchanged. B-087 sweeps ten declared plans over 330 replays with
    zero divergence and finds six worse than the default on the one fixture where negotiation
    genuinely iterates, including both rip-up rules and history decay, which fail to converge;
    multilayer/via negotiation, KiCad DRC, electrical, and fabrication authority are unchanged.
- [x] Incremental spatial index and bounded rip-up/reroute (issue #64, closed).
  - [x] Immutable conservative obstacle index for exact A*/Dijkstra query narrowing, with
    canonical linear fallback, differential route tests, and B-033 evidence.
  - [x] Candidate-only bounded rip-up/reroute coordination is covered by the negotiated
    congestion ledger and B-036; incremental obstacle updates and broader multi-net repair remain
    open.
- [~] Benchmark comparison against established open baselines. B-069 records one real smoke run
  using the official FreeRouting v2.2.2 JAR and real KiCad 10.0.5 GUI DRC on a licensed,
  CopperMCP-original two-pad fixture. Both observed output boards had zero hard violations, zero
  unconnected items, zero vias, and 20.0 mm routed length. The source/report and source/DSN-export
  relationships are `self_attested_unverified` and non-causal, as are the import and runner
  workflow receipts; CopperMCP's result came from a pure-kernel runner rather than MCP or the
  authorized apply service. The artifact therefore retains `comparison_closed=false` and
  `unavailable_or_incomplete`. A harness-owned SES-import transaction, constrained candidate
  runner, broader common corpus, and equivalent performance protocol remain required; bounded
  external execution is not sandbox containment.
  - [x] External-corpus intake and an in-repo harness. A benchmark-only SimpleRouteJson import seam
    converts tscircuit interchange problems into ordinary verified Board IR snapshots and route
    requests, over-approximating every obstacle and refusing typed rather than dropping anything it
    cannot represent. 20 of the 36 MIT-licensed `dwiel/tscircuit-benchmark` boards ship under
    `benchmarks/corpora/` with attribution and digests for all 36; `tscircuit/autorouting` carries
    no licence at all and is cited but not redistributed, and PCBWorld is announced but unreleased.
    B-088 records the first run on data this project did not author: 70 of 117 nets routed at
    1.1711× a provable lower bound, with every two-pin net refused because the reference lattice
    requires the pad-centre delta to divide by the grid step. **The cross-router comparison this
    item asks for is still not measured** — FreeRouting is recorded as `not_run`, and a
    SimpleRouteJson-to-DSN bridge, a common corpus neither router helped define, and an equivalent
    performance protocol all remain required.
  - [ ] Position CopperMCP as a verification harness for externally generated route candidates
    (starting with tscircuit / SimpleRouteJson solution output): convert an external candidate to
    CopperMCP's own candidate identity and run the full verification stack — exact clearance,
    structural verification, real KiCad DRC — returning accepted/refused with typed diagnostics
    and evidence, never a silently repaired route.

- [~] Emit candidate DRC evidence as a deterministic, unsigned in-toto Statement payload using
  Link v0.3, with digest-bound subjects/materials and aggregate redacted byproducts. DSSE signing,
  verification, persistence, and remote transport remain open before this roadmap item can close.

## M3 — Safe candidate application

Tracker state: 1 open — [#68](https://github.com/seunghyukchoe/copper-mcp/issues/68), the IPC
one-undo-commit apply. Both file-backed applies ship; the live one does not.

- [x] Durable routing jobs and cancellation. The bounded internal ledger, single-worker lease
  recovery, redacted candidate manifests, file-backed layered request/result persistence,
  authorization-bound geometry export, and ordinary MCP tools now exist. Single-layer/live jobs
  and MCP Tasks negotiation remain open. Candidate artifacts are preflighted against immutable job
  bindings and the exact `RUNNING` revision before storage, then persisted before the completion
  CAS; capacity or persistence failure cannot create a completed job without its export. A later
  completion/cancellation race can still leave only an inaccessible TTL-bounded orphan, and TTL is
  not secure erasure.
- [~] MCP Tasks progressive enhancement. The reference environment observed `mcp 2.0.0` while
  the supported dependency range remains `<3`; a runtime probe finds generic extension support
  but no compatible current Tasks wire/dispatcher contract. A bounded, process-local 256-bit
  owner-context-bound handle broker is available as a future seam, but it is not durable and no
  `io.modelcontextprotocol/tasks` methods or advertisements ship. A pinned client/server matrix,
  session-authenticated owner binding, and durable task-handle repository remain required; the
  ordinary routing-job tools are the fallback. See [MCP Tasks compatibility research](research/mcp-tasks-compatibility.md).
- [x] Immutable route patch format. A byte-preserving span-splice CST and a pure apply engine:
  given board bytes and a verified candidate they return the bytes an apply would write, proven
  by a three-part assertion (untouched bytes bit-identical, result reparses fail-closed,
  resulting Board IR equals source plus patch exactly).
- [x] Explicit, separately authorized `apply_candidate`, for **route patches only**. Operator
  opt-in flag defaulting off, single-use HMAC apply token issued by the preview and enforced
  server-side, `.lck` hard refusal, double compare-and-swap on the file and Board IR digests,
  timestamped pre-apply copy, atomic replace with fsync on both file and directory, and
  restore-and-report if post-publication verification fails. Verified against real KiCad: the
  applied board opens, the previously unconnected net becomes connected, and no DRC error is
  introduced.
- [~] Revision-race protection is implemented (double compare-and-swap, typed `stale_candidate`,
  never auto-refreshed). **One KiCad undo commit is not**: the pre-apply copy is a file the user
  restores manually and never appears in KiCad's undo stack. A real single-undo transaction needs
  the IPC API's `begin_commit`/`push_commit`/`create_items`/`update_items` primitives, which are
  documented and not experimental. This corrects an earlier assumption: an in-memory document
  *can* be bound to a content identity through `get_as_string`, just never to the on-disk file
  digest, since the gap between the two is exactly the dirty flag the protocol omits (ADR-0074).
  `apply_live_candidate` now ships every precondition a live mutation would need — an operator
  opt-in `COPPER_MCP_ALLOW_LIVE_APPLY` deliberately independent of the two existing consent flags,
  a session/board/snapshot triple compare-and-swap bound under its own HMAC domain, and a full
  candidate replay against the live board — then refuses with `capability_not_implemented` from
  the exact point `begin_commit` would be called. The mutation itself still waits on adversarial
  review: the IPC protocol exposes no revision, dirty flag, or conditional write, and `kipy`
  discards the per-item status a caller would need to confirm a partial write, so these hazards are
  recorded rather than mitigated.
- [~] Placement apply. `apply_placement_candidate` is now separately authorized from route apply:
  file-backed previews may explicitly request a placement-scoped single-use token, and the pure
  source-preserving replay plus atomic file service applies front-side orthogonal footprints with
  native identity and supported rectangular `F.CrtYd` syntax. Unsupported properties/text/
  fabrication graphics/library identity/3D-model pose, side flips, live-editor DRC/scene evidence,
  and live IPC mutation remain fail-closed open gates.

## M4 — High-fidelity Circuit Scene and AI policy plugins

Tracker state: **complete** — 3 closed, 0 open (issues #69, #72, #74). Read that as an accounting
fact about the milestone, not as a claim that every `[~]` below is finished; the remaining ones are
untracked, not done.

Circuit Scene IR covers both semantic and visual observation, and placement has a public preview
surface judged by a deterministic legalizer. The bounded file-backed placement apply gate is now
implemented; what remains is broader source fidelity, post-action/editor authority, solving for a
placement, and the policy-plugin work.

- [x] Versioned Circuit Scene IR for bounded semantic and visual observation. Semantic observation
  is `observe_board_scene` (Circuit Scene IR 0.3.0): region-scoped, exact integer geometry,
  first-class footprint pose/pad ownership/courtyard observation, a static/mutable partition,
  stable Board IR references with declared durability, relationship-aware explicit truncation,
  and board text quarantined in a separately typed untrusted collection. Visual
  observation is the opt-in `include_render` flag: a deterministic, digest-bound, copper-only SVG
  delivered as an ephemeral capability, subordinate to the scene by construction and whole-board
  rather than region-scoped. A human-facing thumbnail remains unimplemented.
- [x] Referentially closed Scene-to-route MCP edge. `preview_route` accepts an observed opaque net
  reference without requiring the hidden KiCad net name, binds it to both scene revisions, refuses
  stale captures before candidate work, and advertises closed input/output schemas. B-007 measures
  0/3 actionable references through the former name-only shape versus 3/3 through the reference
  shape, with exact candidate equality to the hidden-name oracle on one licensed audio fixture.
- [x] Typed placement-intent contract and immutable placement preview/candidates. The seven-rule
  intent language, the revision-bound Board IR footprint view, the deterministic legalizer, the
  dual-digest-bound `PlacementCandidate` and the `preview_placement` MCP tool and CLI command are
  implemented. A locked footprint cannot be moved. `preview_live_placement` adds the same
  candidate-only pipeline over a byte-confirmed active KiCad snapshot; it requires both scene
  digests and never writes or grants apply authority. File-backed placement apply is tracked as a
  separately authorized gate below; live placement remains proposal-only.
- [~] Authoritative KiCad DRC binding for placement candidates. The narrow internal
  source-preserving serializer covers front-side, orthogonal, unfilled-courtyard footprints and
  reparses its disposable result; `run_placement_candidate_drc` now binds that exact replay to a
  private KiCad 10.0.5 DRC context with source/context CAS and redacted aggregate evidence.
  File-backed `preview_placement` now exposes this evidence only through explicit
  `include_drc: true`, with candidate/source/patched-board/context bindings and distinct hard-gate
  `passed` versus warning-aware `clean` semantics. Unsupported properties/text/fabrication
  graphics/library identity/3D-model pose, live compare-and-swap, placement apply, and broader
  geometry remain open gates.
- [~] Deterministic snapping, connectivity, clearance, rule, provenance, and revision validation for
  every placement candidate. Grid snapping, rule residuals, three-valued pad overlap, outline
  containment, keepout respect, and dual-digest binding are implemented, including stationary
  supported courtyards from padless footprints while keeping those footprints out of candidate
  manifests. Same-side courtyard overlap is now three-valued (`proven_clear`/`violated`/
  `inconclusive`) and bound to what KiCad 10.0.5 actually compares — a cached `SHAPE_POLY_SET`
  contracted by a 5,000 nm `BuildCourtyardCaches` inset, so a collision needs 10,000 nm of nominal
  penetration, and an even-odd ring-nesting rule under which a donut courtyard's centre is
  occupiable (ADR-0075, closing issues #72 and #74). This corrects, rather than restates, an
  earlier "exact" claim: the prior model treated every courtyard ring as an independent solid and
  falsely refused donut courtyards, a topology most RF shield-can footprints use. B-089 records 0
  false-positive violations and 0 false-negative clears over 15 real-KiCad cases, with the
  sub-10,000 nm band where the two models genuinely disagree reported as `inconclusive` rather than
  rounded either way. Board IR accepts unfilled `fp_rect`, `fp_poly`, and unordered complete
  `fp_line` cycles only when they normalize to simple closed horizontal/vertical rings; B-073
  measures the resulting exact positive-area legality. Pad-net connectivity after a placement,
  nonzero custom clearance, arcs and non-orthogonal courtyard geometry, and general topology remain
  future work.
- [~] Broaden courtyard geometry and side-aware placement safely. Bounded `F.Cu`/`B.Cu`
  observation imports simple closed orthogonal rectangles, polygons, and unordered line cycles
  without a second mirror; malformed chains and unsupported fields fail closed. Diagonal edges,
  curves, arcs, fills, open/branching/self-intersecting contours, holes or multi-loop contour
  semantics, nonzero custom clearance, GUI-authored flip serialization, side-aware placement apply, and a
  live desktop no-second-mirror oracle remain open.
- [~] Separately authorized placement apply; direct AI mutation of KiCad remains prohibited. The
  bounded file-level surface is implemented and measured, and file-backed post-placement
  DRC/scene observation is revision-bound; general footprint fidelity and live-editor action CAS
  remain open.
- [~] Heuristic policy baseline and trace dataset. An internal deterministic local/beam placement
  search evaluates legalizer-issued immutable candidates only and improves the same-net Manhattan
  proxy by `2,000,000 nm` (`12,000,000 → 10,000,000 nm`) on three deterministic committed-fixture
  replays (B-059). Its `O(n log n)` scoring, deadline, cancellation, and pad-subject normalization
  are bounded; it is not an optimizer, KiCad mutation path, DRC result, or routing-quality claim.
  - [x] An explicit private `route-aware-astar-v1` scoring policy now independently probes a
    bounded A* route on an in-memory Board IR projection of each already-legal placement candidate,
    after identity and exact snapshot/view binding checks. One operation-wide probe meter caps all
    solver evaluations. The default Manhattan ranking and public candidate shape remain unchanged.
    B-078 records three deterministic replays of the original Apache-2.0 NE5532 fixture: the
    selected candidate reduced the **single** independent probe's exact routed length from
    `42,000,000 nm` to `32,000,000 nm` (23.8095%) with zero unrouted probes **on that one net**.
    B-082 corrects the interpretation: the two policies run two different bounded searches rather
    than re-ranking one shared set, and probed against all eleven of the fixture's probeable nets
    both chosen candidates leave four unrouted and the ordering reverses. This is not combined-net
    routing, congestion/overflow, KiCad DRC, external-router validation, placement optimality, or
    apply authority.
- [~] Typed net-ordering, corridor, and repair policy interface. The closed advisory
  `routing.policy` contract has deterministic reference decisions, bounded hostile JSON handling,
  canonical digest binding, and redacted ordinal-only action traces (B-060). It can only order
  known nets and select coordinator-supplied options; it cannot emit copper. The exact internal
  `deterministic-reference-v1` profile and its separately named fixed-worker equivalent
  `deterministic-reference-worker-v1` may influence only the initial negotiated net order;
  no-profile v2 result shape and candidate identity are unchanged, and retry ordering remains
  coordinator-owned. The worker receives only the same neutral scalar, no-window input and fails
  closed before router construction. It is defense in depth for the fixed reference backend, not
  an OS sandbox or admission path for a model, plugin, endpoint, corridor, repair, MCP, or apply
  authority. The content digests are linkable bindings, not secret redactions. B-063 records only
  the in-process profile's synthetic order effect, not worker performance or routing quality.
- [ ] Optional local GNN/RL reference policy.
- [~] Prompt-injection and excessive-agency tests. Board-author text is quarantined in the scene
  and asserted absent by a whole-response grep against a hostile fixture, and every request
  boundary has a hostile-input suite. B-065 now adds seven deterministic offline MCP cases for
  schema closure, stale revisions, quotas, report disclosure, and unauthorized route/placement
  apply; all 7/7 reached their predeclared safe disposition with no workspace change. This remains
  partial because no model, network, remote principal/authorization, application logger, host log,
  provider telemetry, or unknown-attack campaign was evaluated.
- [~] Held-out project-family evaluation. B-066 records three exact replays over one independently
  authored Apache-2.0 audio family that is hash-separated from the declared training family; the
  evaluator reads neither training nor tuning fixtures. It is a first split/evidence baseline, not
  a corpus: more independently licensed families, a frozen policy comparison, and separate KiCad
  DRC evidence are required before this item can close.

## M5 — Performance and physics

Tracker state: 0 closed, 6 open (issues #87, #88, #89, #90, #91, #99). Nothing in this milestone
has landed; B-068 establishes only the measurement prerequisite.

- [~] Profile-guided Rust acceleration. B-068 establishes only the clean-worktree measurement
  prerequisite: fixed routing, placement, and Circuit Scene fixtures; invariant output digests;
  unprofiled timing samples; and a separate bounded cumulative profile. No Rust, SIMD, GPU, speedup,
  cross-machine comparison, or public-contract change exists. Any acceleration experiment must
  preserve the recorded outputs and beat a same-manifest baseline before this item advances.
- [ ] Conflict-aware multicore scheduling.
- [ ] Optional GPU candidate search after end-to-end profiling.
- [~] Local exact repair for bounded congestion windows. The standalone deterministic operator and
  predeclared 5 × 5 detour regression now exist (B-067), but negotiated-router integration does
  not. Coordinator-derived window provenance, Board IR/candidate binding, physical-clearance and
  reference replay gates, plus broader held-out evidence remain required.
- [ ] SI/PI/thermal/DFM surrogate hooks with authoritative signoff.

## M6 — Sustainable open-source project and supply-chain maturity (OpenSSF-informed)

This is a cross-cutting milestone for the 0.5.x+ line. OpenSSF Criticality Score is useful as a
diagnostic for project activity and adoption, but it is not a quality target by itself. The work
below is deliberately tied to healthier engineering, contributor experience, and release safety;
synthetic commits, releases, issues, or comments are prohibited. The baseline and source links are
recorded in [the OpenSSF research note](research/openssf-criticality-and-supply-chain.md).

- [x] Record a dated, reproducible baseline: an estimated Criticality Score of about `0.23/1.00`
  from the available public signals (the official public row did not yet include this new
  repository) and a distinct OpenSSF Scorecard snapshot of `5.8/10` on 2026-08-04. Keep the two
  measures separate and label estimates as estimates.
- [~] Make contribution onboarding real rather than nominal. `CONTRIBUTING.md`, the Code of
  Conduct, issue templates, labels, CODEOWNERS, reproducible fixtures, and good-first issues exist;
  add a small contributor validation path, explicit maintainer/reviewer roles, and documented
  review expectations as the contributor base grows.
- [~] Operate issues and discussions as an engineering feedback loop. Keep triage labels and
  response/closure reasons, publish a lightweight monthly review of open/closed/security issues,
  and use Discussions or an equivalent RFC surface for public-contract and KiCad compatibility
  questions without closing issues merely to improve a metric.
- [~] Make every release useful and verifiable. Continue SemVer, Keep a Changelog, release-ledger,
  CI, dependency updates, and tag-only attestations; add signed-tag or independently verifiable
  release provenance, a documented support window, and a material-change release cadence before
  treating the release gate as complete.
- [~] Close repository-level supply-chain gaps surfaced by Scorecard: configure and continuously
  verify branch protection/code-owner review, improve packaging metadata, add signed-release
  verification, and complete the CII/OpenSSF best-practices profile. Do not claim a check is fixed
  until a hosted run or API response proves it.
- [x] Ship a tested agent-facing usage contract, `docs/agents.md`, that restates every typed
  refusal as the next action an agent should take — for example, `stale_revision` means re-observe
  and rebuild the candidate, and `apply_disabled` is a question for the operator, never something
  to route around. `tests/test_agents_doc.py` mechanically asserts every MCP tool it lists is still
  registered, every registered tool appears in it, and every diagnostic code it names still exists,
  so the document cannot silently drift from the implementation; a root `llms.txt` points an LLM at
  it first.
- [x] Package the KiCad IPC plugin for the official Plugin and Content Manager (PCM), so
  `hardware/kicad-ipc-plugin` no longer needs a manual install, while keeping the
  token-never-leaves-the-plugin property and `COPPER_MCP_ALLOW_LIVE_IPC` default-off documented in
  the listing (issue #98, closed). It installs as
  `com.github.seunghyukchoe.coppermcp-live-observer`, `kicad_version` `9.0.1`, built reproducibly
  by `make pcm`. Installing it grants nothing on its own: the operator flag still has to be set in
  the environment KiCad was launched from.
- [ ] Build an adoption and evidence path: versioned audio-board examples, reproducible benchmark
  commands, downstream smoke tests, citations, and a small set of independent users or projects
  that can validate the documented MCP/KiCad contracts without uploading proprietary boards.
- [ ] Reduce maintainer bus factor for security and releases with at least two active reviewers,
  documented succession/security contacts, and CODEOWNERS coverage for routing, MCP, KiCad, and
  release workflows. This is a people/process gate, not something an agent can self-certify.
- [ ] Add a monthly project-health snapshot to the append-only ledgers: official Criticality Score
  when published, otherwise the exact input snapshot and reconstruction method; Scorecard result;
  CI/release/issue/contributor counts; and links to the underlying public evidence. Never put
  credentials, private boards, or private contributor data in the snapshot.

### 0.4 target and exit criteria

The project may report “Criticality Score ≥ 0.4” only after the official public service reports at
least `0.40` for two consecutive monthly snapshots, or after two independently reproducible
reconstructions are available while the official row is still delayed. The milestone is not closed
by the number alone: the contributor, review, release-provenance, supply-chain, and adoption gates
above must have current evidence, and the project must remain useful even if OpenSSF changes its
formula or publication cadence.
