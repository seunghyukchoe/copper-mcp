# Roadmap

Roadmap items describe outcomes, not promises about dates. Each milestone requires tests,
documentation, ledger updates, and benchmark evidence.

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

## M1 — KiCad inspection and validation (`0.4.x`, current)

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
- [~] Schematic round trip, authoritative ERC, and source-to-board connectivity parity. The
  bounded passive subset now has exact deterministic schematic replay plus real KiCad `kicadxml`
  component/connectivity parity through the reusable `kicad_schematic_parity` verifier. Authoritative
  ERC, source-to-PCB connectivity parity, and broader symbol/library coverage remain open.
- [~] Live KiCad IPC snapshot to Circuit Scene and route-proposal binding. `observe_live_board_scene`
  converts the exact captured IPC serialization through Board IR, and `preview_live_route` now
  returns a deterministic read-only candidate from a scene `net_ref_id` with both stale-session
  digests. `preview_live_placement` now reuses the same exact snapshot → Board IR → legalizer
  path for a ref-anchored, read-only placement candidate. `preview_live_layered_route` now adds a
  session-token, source-digest, and Board IR-digest-bound via-capable proposal with fake-IPC
  replay evidence. A real running-editor oracle and live action compare-and-swap before placement
  or routing remain open because the workstation IPC server is disabled.
- [x] Canonical Board IR v0.2 contract with integer units, typed constraints, strict codecs,
  content digests, first-class footprint pose/side/lock/pad ownership, and bounded rectangular
  courtyard rings. The immutable v0.1 schema remains as legacy compatibility evidence.
- [x] Board IR application-service and MCP exposure as a read-only structural summary.
- [ ] Broader KiCad geometry and rule coverage.
- [x] Headless `kicad-cli pcb drc --format json` validation.
- [x] Minimal KiCad child environment, private working directory, bounded private
  global-configuration/state roots, and snapshot-confined file-table dependencies.
- [x] Candidate preview without mutation.
- [x] Version-skew and stale-board tests for the DRC adapter.

## M2 — Deterministic routing baseline

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
- [~] Board IR-bound two-layer proposal adapter: exact grid attachment, conservative foreign
  copper/zone/pad envelopes, separate track/via keepouts, immutable candidate digests, and
  fail-closed stale/off-grid/unsupported diagnostics are benchmarked in B-018. Source-preserving
  segment/via serialization and Board IR replay are covered by an internal disposable serializer;
  B-020 now binds that exact replay to private authoritative KiCad DRC, and B-024 exposes a
  separate read-only candidate preview through MCP. A bounded topology verifier now gates the
  serializer and refuses endpoint-via geometry; exact padstack/clearance, durable export,
  multilayer generalization, and apply authority remain open gates. B-032 now covers the narrow
  public file-backed DRC evidence binding.
  - [x] Candidate topology gate: revision/endpoint binding, path-via adjacency, two-layer
    full-stack transitions, duplicate/crossing rejection, bounded pair checks, and explicit
    physical-validation non-claim.
- [x] Attachment to existing same-net copper and bounded partial-route completion.
- [ ] Multilayer vias and keepouts.
- [~] Negotiated-congestion multi-net routing.
  - [x] Bounded candidate-only present/history congestion ledger with exact lattice edge/vertex
    occupancy, deterministic rip-up order, policy-digest-bound candidates, cancellation, and
    fixed iteration/routing budgets.
  - [x] B-036 KiCad-fixture replay: sequential baseline overflow `1` versus negotiated overflow
    `0` across three deterministic replays; physical clearance, multilayer capacity, KiCad DRC,
    and held-out corpus comparison remain open.
- [~] Incremental spatial index and bounded rip-up/reroute.
  - [x] Immutable conservative obstacle index for exact A*/Dijkstra query narrowing, with
    canonical linear fallback, differential route tests, and B-033 evidence.
  - [x] Candidate-only bounded rip-up/reroute coordination is covered by the negotiated
    congestion ledger and B-036; incremental obstacle updates and broader multi-net repair remain
    open.
- [ ] Benchmark comparison against established open baselines.

- [~] Emit candidate DRC evidence as a deterministic, unsigned in-toto Statement payload using
  Link v0.3, with digest-bound subjects/materials and aggregate redacted byproducts. DSSE signing,
  verification, persistence, and remote transport remain open before this roadmap item can close.

## M3 — Safe candidate application

- [x] Durable routing jobs and cancellation. The bounded internal ledger, single-worker lease
  recovery, redacted candidate manifests, file-backed layered request/result persistence,
  authorization-bound geometry export, and ordinary MCP tools now exist. Single-layer/live jobs
  and MCP Tasks negotiation remain open.
- [ ] MCP Tasks progressive enhancement. The current protocol is an experimental extension and
  remains deferred until a pinned client/server compatibility matrix, random task handles, and
  authorization-bound result storage exist; see ADR-0046.
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
  restores manually and never appears in KiCad's undo stack. A real single-undo transaction
  needs the IPC API, deferred because it mutates an in-memory document whose state cannot be
  bound to a file digest.
- [~] Placement apply. `apply_placement_candidate` is now separately authorized from route apply:
  file-backed previews may explicitly request a placement-scoped single-use token, and the pure
  source-preserving replay plus atomic file service applies front-side orthogonal footprints with
  native identity and supported rectangular `F.CrtYd` syntax. Unsupported properties/text/
  fabrication graphics/library identity/3D-model pose, side flips, live-editor DRC/scene evidence,
  and live IPC mutation remain fail-closed open gates.

## M4 — High-fidelity Circuit Scene and AI policy plugins

Circuit Scene IR covers both semantic and visual observation, and placement has a public preview
surface judged by a deterministic legalizer. The bounded file-backed placement apply gate is now
implemented; what remains is broader source fidelity, post-action/editor authority, solving for a
placement, and the policy-plugin work.

- [x] Versioned Circuit Scene IR for bounded semantic and visual observation. Semantic observation
  is `observe_board_scene` (Circuit Scene IR 0.2.0): region-scoped, exact integer geometry,
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
  containment, keepout respect, exact same-side rectangular-courtyard overlap, and dual-digest
  binding are implemented, including stationary supported courtyards from padless footprints while
  keeping those footprints out of candidate manifests. Board IR now carries the supported
  rectangular courtyard rings; pad-net connectivity after a placement and non-rectangular topology
  remain future work.
- [~] General courtyard line-chain/polygon topology and back-side footprint observation, pinned to
  KiCad-authored front/back flip and DRC oracle fixtures without applying a second mirror. The
  adapter now observes bounded `F.Cu`/`B.Cu` footprints with rectangular courtyards and a real
  KiCad CLI DRC fixture; GUI-authored flip serialization, line-chain/polygon topology, and the
  no-second-mirror claim remain open until a live desktop oracle is available.
- [~] Separately authorized placement apply; direct AI mutation of KiCad remains prohibited. The
  bounded file-level surface is implemented and measured, and file-backed post-placement
  DRC/scene observation is revision-bound; general footprint fidelity and live-editor action CAS
  remain open.
- [ ] Heuristic policy baseline and trace dataset.
- [ ] Typed net-ordering, corridor, and repair policy interface.
- [ ] Optional local GNN/RL reference policy.
- [~] Prompt-injection and excessive-agency tests. Board-author text is quarantined in the scene
  and asserted absent by a whole-response grep against a hostile fixture, and every request
  boundary has a hostile-input suite; a systematic excessive-agency evaluation is still missing.
- [ ] Held-out project-family evaluation.

## M5 — Performance and physics

- [ ] Profile-guided Rust acceleration.
- [ ] Conflict-aware multicore scheduling.
- [ ] Optional GPU candidate search after end-to-end profiling.
- [ ] Local exact repair for bounded congestion windows.
- [ ] SI/PI/thermal/DFM surrogate hooks with authoritative signoff.
