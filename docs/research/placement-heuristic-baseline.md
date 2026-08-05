# Bounded deterministic placement-heuristic baseline

## Decision

`copper_mcp.placement.solver` is an internal proposal generator. It performs a bounded
lexicographic local/beam search: evaluate the supplied immutable `PlacementIntent` first, then
try one-grid-step cardinal moves for unlocked, pad-owning intent subjects. Search order is fixed
by footprint reference and direction; ties use candidate IDs. The work budget, rounds, beam
width, candidate rank cap, legalizer check cap, deadline, and cancellation callback are explicit.

The implementation performs no board or KiCad mutation and does not create placement candidates
itself. Each state is encoded as an existing ref-anchored `PlacementProposal` and is retained only
when `evaluate_placement` returns the existing immutable candidate plus its evidence. This makes
the existing stale-revision, snapshot-digest, locked-footprint, padless-footprint, and physical
legality checks the sole admission gate.

## Objective and reproducibility

The rank key is `(violated intent rules, same-net pairwise Manhattan pad distance, moved
footprints, candidate ID)`. The middle term is an exact-integer connectivity proxy rebuilt from a
legalizer-issued candidate's footprint poses. It is intentionally not a routed length estimate.
The canonical replay benchmark uses the committed
`tests/fixtures/board-ir-v0.1/footprint-rotation.kicad_pcb` fixture and records a strict proxy
improvement over its no-proposal initial placement across three identical replays. The benchmark
artifact is `benchmarks/results/placement/2026-08-05-placement-solver-baseline-v1.json`.

The solver is a heuristic, not an optimizer: no optimality or approximation guarantee is claimed.
It also makes no DRC, electrical, timing, signal-integrity, thermal, fabrication, routing,
congestion, KiCad-file, or live-editor claim. Wall-clock deadlines are operational fail-closed
ceilings and can vary with host scheduling; deterministic test/benchmark runs use a comfortably
larger deadline and demonstrate reproducibility through the fixed evaluation ceiling.

## Research basis

The baseline follows the longstanding CAD distinction between a search policy and a legality
admission stage. VPR documents placement as iterative candidate moves scored against a placement
objective, while keeping placement and routing as separate stages; this supports using a modest
placement proxy without presenting it as a route proof. The benchmark uses deterministic bounded
local improvement rather than simulated annealing because repeatable evidence and bounded work
are required at this public safety boundary.

The local-improvement shape is also consistent with Kernighan and Lin's original heuristic
framing: select bounded discrete changes using an explicit cost while recognizing that the
underlying combinatorial problem does not make the heuristic an optimizer. CopperMCP's added
constraint is stricter: a move must pass the repository's existing legalizer before it can be
scored or returned.

Primary sources:

- V. Betz and J. Rose, [VPR: A New Packing, Placement and Routing Tool for FPGA Research](https://www.eecg.utoronto.ca/~vaughn/papers/fpl97_abstract.html), University of Toronto (1997).
- A. Marquardt, V. Betz, and J. Rose, [Timing-Driven Placement for FPGAs](https://www.eecg.utoronto.ca/~jayar/pubs/marq/fpga2000_arm.pdf), FPGA 2000. This primary paper describes an iterative placement objective and explicitly distinguishes placement quality from post-routing results.
- B. W. Kernighan and S. Lin, [An Efficient Heuristic Procedure for Partitioning Graphs](https://doi.org/10.1002/j.1538-7305.1970.tb01770.x), *Bell System Technical Journal* 49(2), 1970.

Repository contracts:

- [ADR-0024](../adr/0024-placement-intent-and-legalization.md) establishes generator-proposes / deterministic-legalizer-disposes, immutable candidates, and no v0.1 apply claim.
- [ADR-0034](../adr/0034-source-preserving-placement-candidates.md) keeps any later projection separate from solver output and still non-mutating.
