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

## M1 — KiCad inspection and validation (`0.2.x`, current)

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

- [ ] Single two-pin A* routing with exact connectivity.
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
- [ ] Diagonal copper on the routed net, which needs an exact integer inner core before it can be
  attachment copper rather than only an obstacle.
- [ ] Fill-aware zone routing with a freshness-bound KiCad refill/fill-digest authority contract.
- [ ] Multi-pin nets, since most real nets have more than two pads.
- [x] Attachment to existing same-net copper and bounded partial-route completion.
- [ ] Multilayer vias and keepouts.
- [ ] Negotiated-congestion multi-net routing.
- [ ] Incremental spatial index and bounded rip-up/reroute.
- [ ] Benchmark comparison against established open baselines.

## M3 — Safe candidate application

- [ ] Durable routing jobs and cancellation.
- [ ] MCP Tasks progressive enhancement.
- [ ] Immutable route patch format.
- [ ] Explicit, separately authorized `apply_candidate`.
- [ ] One KiCad undo commit and revision-race protection.

## M4 — High-fidelity Circuit Scene and AI policy plugins

Every item in this milestone remains future work; the current MVP exposes no Circuit Scene or
placement surface.

- [ ] Versioned Circuit Scene IR for bounded semantic and visual observation.
- [ ] Typed placement-intent contract and immutable placement preview/candidates.
- [ ] Deterministic snapping, connectivity, clearance, rule, provenance, and revision validation for
  every placement candidate.
- [ ] Separately authorized placement apply; direct AI mutation of KiCad remains prohibited.
- [ ] Heuristic policy baseline and trace dataset.
- [ ] Typed net-ordering, corridor, and repair policy interface.
- [ ] Optional local GNN/RL reference policy.
- [ ] Prompt-injection and excessive-agency tests.
- [ ] Held-out project-family evaluation.

## M5 — Performance and physics

- [ ] Profile-guided Rust acceleration.
- [ ] Conflict-aware multicore scheduling.
- [ ] Optional GPU candidate search after end-to-end profiling.
- [ ] Local exact repair for bounded congestion windows.
- [ ] SI/PI/thermal/DFM surrogate hooks with authoritative signoff.
