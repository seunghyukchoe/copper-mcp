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

- [ ] Official `kicad-python` IPC plugin.
- [x] Canonical Circuit Intent IR `0.1.0` and deterministic in-memory KiCad schematic generation
  contract for a bounded two-pin passive subset.
- [x] Bounded Circuit Intent build service, explicit create-new CLI schematic export, and
  stdio-only opaque MCP resource delivery with redacted verification metadata.
- [x] Deterministic passive-layout readability baseline with wider grid placement, extended leads,
  separated labels/properties, real KiCad SVG inspection, and a structural regression.
- [x] Descriptor-anchored workspace reads and exact-lowercase create-only schematic output.
- [ ] Schematic round trip, authoritative ERC, and source-to-board connectivity parity.
- [x] Canonical Board IR v0.1 contract with integer units, typed constraints, strict codecs, and
  content digests.
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
  - [ ] Durable routing jobs, candidate persistence, and export.
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
- [ ] Fill-aware zone *routing*, using verified fill as a tighter obstacle than the conservative
  boundary envelope. Connectivity uses exact fill today; the routing obstacle model does not.
- [~] Multi-pin nets, since most real nets have more than two pads.
  - [x] Connectivity analysis for nets of any width, so an already-connected multi-pin net
    is recognised rather than refused.
  - [x] Routing a multi-pin net as a deterministic spanning tree over its components
    (`component-mst-v1`); Steiner-optimal topology is explicitly not claimed.
  - [ ] Steiner-quality topology (FLUTE-guided or learned ordering) behind the recorded
    `ordering_policy` seam.
- [x] Via-aware connectivity, so a net joined through another layer is recognised rather than
  refused. On CopperTone this resolved `VCC` and `L_OUT`; `GND` remains refused because it carries a
  same-net zone, whose fill is not trusted.
- [ ] Routing *through* vias, which needs a layer-aware lattice, a via-insertion cost, and a
  via-placement contract. Connectivity is multilayer today; path search is not.
- [x] Attachment to existing same-net copper and bounded partial-route completion.
- [ ] Multilayer vias and keepouts.
- [ ] Negotiated-congestion multi-net routing.
- [ ] Incremental spatial index and bounded rip-up/reroute.
- [ ] Benchmark comparison against established open baselines.

- [ ] Emit candidate DRC evidence in the in-toto Statement envelope, so an attestation this
  project already produces in substance is also machine-checkable by standard tooling.

## M3 — Safe candidate application

- [ ] Durable routing jobs and cancellation.
- [ ] MCP Tasks progressive enhancement.
- [~] Immutable route patch format. A byte-preserving span-splice CST and a pure apply engine
  exist: given board bytes and a verified candidate they return the bytes an apply would write,
  proven by a three-part assertion (untouched bytes bit-identical, result reparses fail-closed,
  resulting Board IR equals source plus patch exactly). Verified against real KiCad — the applied
  board opens, the previously unconnected net becomes connected, and no DRC error is introduced.
  **Nothing writes to disk yet.**
- [ ] Explicit, separately authorized `apply_candidate`. The mutating path is designed —
  operator opt-in flag, single-use HMAC apply token, `.lck` hard refusal, whole-file
  compare-and-swap under a held lock, timestamped pre-apply copy, and O_EXCL temp + fsync +
  rename + fsync(dir) — but none of it is implemented and there is no tool or CLI command.
- [ ] One KiCad undo commit and revision-race protection. File-level apply gives a pre-apply copy
  the user restores manually, not a KiCad undo step; a real single-undo transaction needs the IPC
  API, which is deferred because it mutates an in-memory document whose state cannot be bound to
  a file digest.

## M4 — High-fidelity Circuit Scene and AI policy plugins

Circuit Scene IR covers both semantic and visual observation, and placement has a public preview
surface judged by a deterministic legalizer. What remains is applying a placement, solving for one,
and the policy-plugin work.

- [x] Versioned Circuit Scene IR for bounded semantic and visual observation. Semantic observation
  is `observe_board_scene` (Circuit Scene IR 0.1.0): region-scoped, exact integer geometry,
  static/mutable partition, stable Board IR references with declared durability, explicit
  truncation, and board text quarantined in a separately typed untrusted collection. Visual
  observation is the opt-in `include_render` flag: a deterministic, digest-bound, copper-only SVG
  delivered as an ephemeral capability, subordinate to the scene by construction and whole-board
  rather than region-scoped. A human-facing thumbnail remains unimplemented.
- [x] Typed placement-intent contract and immutable placement preview/candidates. The seven-rule
  intent language, the out-of-band footprint view, the deterministic legalizer, the
  dual-digest-bound `PlacementCandidate` and the `preview_placement` MCP tool and CLI command are
  implemented. Nothing applies a placement.
- [ ] Authoritative KiCad DRC binding for placement candidates. Deferred deliberately: a
  footprint-move serializer would have to rewrite roughly twice as many pose-carrying nodes as
  Board IR can verify, so the round-trip assertion that makes the route patch trustworthy would be
  blind to most of the edit. Revisit once Board IR models footprints.
- [~] Deterministic snapping, connectivity, clearance, rule, provenance, and revision validation for
  every placement candidate. Grid snapping, rule residuals, three-valued pad overlap, outline
  containment, keepout respect and dual-digest binding are implemented. Courtyard overlap is
  reported as `not_modelled` because Board IR carries no courtyard geometry, and connectivity
  after a placement is future work.
- [ ] Separately authorized placement apply; direct AI mutation of KiCad remains prohibited.
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
