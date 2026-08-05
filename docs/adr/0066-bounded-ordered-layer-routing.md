# ADR-0066: Keep ordered-layer routing bounded and non-serializing

- Status: Accepted
- Date: 2026-08-05
- Related: ADR-0035, ADR-0036, ADR-0037, ADR-0045

## Decision

Generalize the internal Board-IR layered proposal seam from exactly two to two through eight
ordered **signal** copper layers. The immutable search state is `(x, y, layer)`; cardinal moves
have a positive `move_cost`, and each cross-layer transition has the explicit positive `via_cost`.
The only accepted transition is the Board IR v0.2 full-stack through-via: it can enter or leave on
any supported signal layer but its recorded physical span is the first-to-last layer of the ordered
stack. Blind, buried, microvia, padstack-specific, plane/mixed-layer, and per-span cost modes fail
closed.

All track and via keepouts remain layer-scoped. The stack has a hard 2..8 layer budget plus bounded
router node, expansion, obstacle, and obstacle-check work. An omitted `max_vias` preserves the
historical two-layer behavior (no newly introduced via-policy cap); an explicit cap is finite. For
three through eight layers, an omitted cap has the deterministic effective limit of 64 vias. The
candidate constructor and structural verifier apply the same effective rule, and the verifier
replays the full-stack span and path/via chain before accepting a candidate. Existing two-layer
request defaults omit the optional cap from canonical bytes, preserving their candidate identities
and output.

No KiCad serializer, DRC path, MCP route-preview contract, durable job, or apply flow is widened.
File-backed preview, live preview, and durable-job preparation explicitly reject stacks other than
exactly two signal layers before invoking the generalized router. The existing source-preserving
serializer/replay and candidate-bound DRC evidence prove exactly two signal layers; the generalized
candidate therefore remains internal proposal data.

## Promotion gates

Promotion requires a KiCad 2..8-layer fixture suite that proves source-preserving segment and via
serialization, Board-IR reparse equality, span/padstack presence on every traversed layer, refill
handling, and candidate-bound KiCad CLI DRC for each supported stack width. Public request schemas,
durable exports, and apply then need their own contract/version review.

## Evidence

The committed three-layer oracle blocks both layers available to the two-layer configuration but
deterministically traverses the clear inner layer with two full-stack transitions. Separate
regressions preserve a 65-via two-layer route with an omitted cap, reject a restamped 66-via
candidate under an explicit 65-via cap, and prove the file, live, and durable boundaries reject an
internal three-layer snapshot. Negative tests also cover over-wide stacks, zero via budget, invalid
full-stack span, stale revision, cancellation, and layer-scoped keepouts.

## References

- https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/
- https://docs.kicad.org/kicad-python-main/board.html
- https://doi.org/10.1109/TSSC.1968.300136
- https://doi.org/10.1145/800260.809014
