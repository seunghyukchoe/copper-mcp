# Roadmap

Roadmap items describe outcomes, not promises about dates. Each milestone requires tests,
documentation, ledger updates, and benchmark evidence.

## M0 — Repository foundation (`0.1.x`, current)

- [x] Secure workspace and file boundary.
- [x] Content-addressed board manifests.
- [x] Candidate schemas and comparison.
- [x] MCP and CLI adapters over shared services.
- [x] Governance, security, CI, releases, and ledgers.
- [x] Reproducible KiCad audio-board preview and artifact-validation workflow.
- [x] First public GitHub release.

## M1 — KiCad inspection and validation

- [ ] Official `kicad-python` IPC plugin.
- [x] Canonical Board IR v0.1 contract with integer units, typed constraints, strict codecs, and
  content digests.
- [x] Board IR application-service and MCP exposure as a read-only structural summary.
- [ ] Broader KiCad geometry and rule coverage.
- [x] Headless `kicad-cli pcb drc --format json` validation.
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
- [ ] Via obstacles on the selected layer, the first limit a real board hits.
- [ ] Polygon zone obstacles; a bounding box is useless for a pour covering most of a board.
- [ ] Multi-pin nets, since most real nets have more than two pads.
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

## M4 — AI policy plugins

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
