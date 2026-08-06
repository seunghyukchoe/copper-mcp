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
  bounded passive subset now has exact deterministic schematic replay, authoritative `kicad-cli sch
  erc` evidence bound to the generated schematic digest, and a live KiCad round trip that exports a
  `kicadxml` netlist and checks recovered components and nets against the source intent through the
  reusable `kicad_schematic_parity` verifier. Source-to-PCB connectivity parity and broader
  symbol/library coverage remain open.
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
  orthogonal courtyard rings: unfilled `fp_rect`, `fp_poly`, and unordered complete `fp_line`
  cycles. The immutable v0.1 schema remains as legacy compatibility evidence.
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
- [x] Attachment to existing same-net copper and bounded partial-route completion.
- [ ] Multilayer vias and keepouts.
- [~] Negotiated-congestion multi-net routing.
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
- [~] Incremental spatial index and bounded rip-up/reroute.
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

- [~] Emit candidate DRC evidence as a deterministic, unsigned in-toto Statement payload using
  Link v0.3, with digest-bound subjects/materials and aggregate redacted byproducts. DSSE signing,
  verification, persistence, and remote transport remain open before this roadmap item can close.

## M3 — Safe candidate application

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
  containment, keepout respect, exact same-side simple closed orthogonal-courtyard overlap, and
  dual-digest binding are implemented, including stationary supported courtyards from padless
  footprints while keeping those footprints out of candidate manifests. Board IR accepts unfilled
  `fp_rect`, `fp_poly`, and unordered complete `fp_line` cycles only when they normalize to simple
  closed horizontal/vertical rings; B-073 measures the resulting exact positive-area legality.
  Pad-net connectivity after a placement, nonzero custom clearance, and general topology remain
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
