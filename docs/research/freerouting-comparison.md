# FreeRouting comparison and benchmark boundary

Research date: 2026-08-04

## Finding

FreeRouting is not an exhaustive brute-force enumerator. Its own architecture describes a two-stage
pipeline: an autorouter searches for legal paths, then an optimizer temporarily rips up and
reroutes selected connections, keeping improvements by score. The implementation uses a sorted
maze/wave expansion queue with destination-distance and obstacle/via costs, bounded passes, and
stagnation detection. In casual conversation it can feel brute-force because it tries many passes,
but algorithmically it is heuristic search with backtracking and local optimization.

Primary sources:

- [FreeRouting architecture](https://raw.githubusercontent.com/freerouting/freerouting/master/docs/architecture.md)
- [MazeSearchAlgo.java](https://raw.githubusercontent.com/freerouting/freerouting/master/src/main/java/app/freerouting/autoroute/MazeSearchAlgo.java)
- [MazeListElement.java](https://raw.githubusercontent.com/freerouting/freerouting/master/src/main/java/app/freerouting/autoroute/MazeListElement.java)
- [BatchAutorouter.java](https://raw.githubusercontent.com/freerouting/freerouting/master/src/main/java/app/freerouting/autoroute/BatchAutorouter.java)
- [FreeRouting README and MCP guide](https://github.com/freerouting/freerouting)
- [FreeRouting releases](https://github.com/freerouting/freerouting/releases)

## What is different today

| Dimension | FreeRouting | CopperMCP (current evidence) |
|---|---|---|
| Primary job | Complete and optimize a board through DSN/SES exchange | Produce bounded, immutable proposals over Board IR and KiCad IPC snapshots |
| Search | Maze/wave/A*-style expansion, rip-up/backtracking, multi-pass optimization | Deterministic bounded orthogonal A*; multi-pin component MST; first bounded present/history congestion coordinator (B-036), not general parity |
| State | Stateful routing jobs and mutable in-memory board during a run | Candidate-only services; file apply is explicit and token-gated; live tools are read-only |
| Validation | FreeRouting DRC and host re-import workflow | KiCad DRC is authoritative for supported file-backed route candidates; live route/placement proposals carry no DRC authority |
| AI/editor grounding | DSN/SES or MCP job API; current editor focus is not CopperMCP's contract | Exact KiCad snapshot digest, Circuit Scene refs, active layer and native selection context; stale reads fail closed |
| Claim supported by current tests | Routing quality and completion require a common-board benchmark | Safety, determinism, revision binding, and read-only observe-to-propose closure on fake IPC clients |

The honest near-term advantage is therefore a safer AI/editor control plane, not routing-quality
superiority. CopperMCP must not claim to beat FreeRouting on general-board completion until both
systems produce outputs from the same KiCad-authored corpus and the outputs are re-imported and
checked by the same KiCad version.

## Acceleration track toward comparable routing fundamentals

The first capability gap is not unbounded brute force. It is a bounded two-signal-layer search over
`(x, y, layer)` with explicit through-via transitions and a positive via cost. CopperMCP now has
that search, a Board IR binding, source-preserving segment/via serialization, and an internal
replay-bound KiCad DRC gate. This gives a fair, evidence-producing chance on boards where a
single-layer route is impossible while preserving deterministic budgets and the candidate-first
boundary. A bounded two-net negotiated-congestion slice now exists, while multilayer capacity,
fanout, post-route optimization, and broad quality comparison remain the next gaps.

Acceptance for the current narrow gate is recorded in B-020: ten fresh private-workspace runs,
10/10 zero-error/zero-unconnected KiCad 10.0.5 reports, deterministic redacted evidence, and
unchanged source/workspace state. The held-out corpus requirement remains for a production claim;
this is a capability step, not FreeRouting parity. The current source-preserving placement
projection is a prerequisite for high-fidelity editor communication but does not change this
routing comparison.

## Fair comparison protocol

1. Start with KiCad-authored unrouted `.kicad_pcb` files and preserve their project/rule context.
2. Export DSN using KiCad's own Specctra exporter; do not hand-author a parallel DSN.
3. Run a pinned FreeRouting release and import its SES output into a disposable board copy.
4. Run CopperMCP's supported route path on the same source board; mark unsupported multilayer,
via-through, or unsupported congestion cases as `not_applicable`, not as failures. B-036 is only a
structural two-net crossing fixture; it is not a claim that CopperMCP now matches FreeRouting.
5. Run the same `kicad-cli pcb drc --format json` version and refill policy on both outputs.
6. Publish per-board results, not only averages: completion/unconnected count, hard DRC, clearance
   and dangling items, total length, vias/layer changes, bends, wall time, peak memory, deterministic
   replay hashes, and safety/editor-closure evidence.

The first corpus should include FreeRouting's public DAC2020 fixtures and issue regression boards,
plus the repository's open audio boards and bounded synthetic stress families. Pin source, tool,
KiCad, and machine digests; retain timeouts and no-path results.

## Planning estimate

A defensible scoped claim (for example, revision-bound read-only proposal safety on a specified
two-pin class) needs roughly 150–300 additional agent-hours including the adapter and evidence
harness. A broad claim of better routing across general boards needs roughly 400–700+ agent-hours,
plus 25–80 human/lab hours for GUI, fabrication, and electrical checks. Current evidence supports
the safety/reproducibility/editor-context distinction only; it does not support a general
FreeRouting quality win.
