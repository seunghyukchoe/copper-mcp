# ADR-0049: Add a bounded one-Steiner topology ordering policy

- Status: Accepted
- Date: 2026-08-05
- Owners: `@seunghyukchoe`
- Related: [ADR-0019](0019-multi-pin-component-merging.md), [multi-pin routing references](../research/multi-pin-routing-references.md)

## Context

Multi-pin routing now produces a valid tree by routing each component-MST merge with the exact
obstacle-aware A* search.  The measured baseline is safe and reproducible, but a fixed MST order
can route a later leg from a terminal instead of allowing it to attach to a useful earlier trunk.
The candidate already records an `ordering_policy`, so topology guidance can improve wire length
without allowing a model or a heuristic to emit copper directly.

FLUTE is a strong low-degree rectilinear Steiner reference: its published algorithm is optimal for
low-degree point nets and uses lookup tables.  CopperMCP does not vendor those tables or claim
FLUTE equivalence.  Takahashi–Matsuyama's grow-from-tree family and the project's existing
multi-source A* merge shape provide a clean-room, obstacle-aware seam instead.

## Decision

For nets with at most nine evolving components, the router uses the recorded policy
`batched-1-steiner-v1`.  It evaluates each possible merge using integer component envelopes and a
coordinate-wise median-point three-way guide.  The pair's direct envelope gap is reduced only by a
non-negative estimated local one-Steiner saving; direct gap and component indices provide stable
tie-breakers.  After each merge, the exact core union becomes the next component envelope.

The policy is an ordering guide only:

- the existing A* search remains the sole geometry constructor;
- all obstacle, cancellation, and shared work budgets remain authoritative;
- candidate identity includes the policy string and router version;
- nets above nine components retain `component-mst-v1` until a separately budgeted decomposition
  policy exists;
- the result claims determinism and measured wire-length reduction on the fixture, not Steiner
  optimality, an approximation ratio, or FreeRouting parity.

## Evidence

Benchmark `B-031` compares both orders through the same file-backed preview service on the
four-pad `tree-star.kicad_pcb` fixture.  The one-Steiner order reduces wire length from 48 mm to
42 mm (12.5%), keeps the source bytes/inode/mtime unchanged, and is deterministic across replay.
The real KiCad 10.0.5 multi-pin DRC regression accepts the new tree with zero reported violations
and zero unconnected items.

## Consequences

- Multi-pin candidates can use a shorter trunk while preserving the candidate-first safety model.
- Existing `component-mst-v1` candidates remain replayable because the policy and router version
  are content-addressed; old manifests are not silently reinterpreted.
- The guide performs cubic comparisons only for low-degree nets and charges each comparison to the
  existing obstacle-check budget.  Budget exhaustion is therefore an explicit refusal, not an
  unbounded topology phase.
- A future FLUTE-compatible or learned policy can occupy the same seam after independent license,
  benchmark, and safety review.

## References

- [FLUTE: Fast Lookup Table Based Rectilinear Steiner Minimal Tree Algorithm](https://ieee-ceda.org/media/flute-fast-lookup-table-based-rectilinear-steiner-minimal-tree-algorithm-vlsi-design)
- Takahashi & Matsuyama, *An approximate solution for the Steiner problem in graphs*, Mathematica Japonica 24 (1980)
- [Multi-pin routing research and licensing survey](../research/multi-pin-routing-references.md)
