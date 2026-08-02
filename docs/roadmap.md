# Roadmap

Roadmap items describe outcomes, not promises about dates. Each milestone requires tests,
documentation, ledger updates, and benchmark evidence.

## M0 — Repository foundation (`0.1.x`, current)

- [x] Secure workspace and file boundary.
- [x] Content-addressed board manifests.
- [x] Candidate schemas and comparison.
- [x] MCP and CLI adapters over shared services.
- [x] Governance, security, CI, releases, and ledgers.
- [ ] First public GitHub release.

## M1 — KiCad inspection and validation

- [ ] Official `kicad-python` IPC plugin.
- [ ] Canonical Board IR with integer units and typed constraints.
- [ ] Headless `kicad-cli pcb drc --format json` validation.
- [ ] Candidate preview without mutation.
- [ ] Version-skew and stale-board tests.

## M2 — Deterministic routing baseline

- [ ] Single two-pin A* routing with exact connectivity.
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
