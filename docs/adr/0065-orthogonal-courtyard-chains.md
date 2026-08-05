# ADR-0065: Observe and legalize bounded orthogonal courtyard chains

- Status: Accepted
- Date: 2026-08-05
- Owners: CopperMCP maintainers
- Related: ADR-0026, ADR-0057, ADR-0058, ADR-0062

## Context

KiCad forms a footprint courtyard from every closed graphic shape on its matching front or back
courtyard layer.  Rectangles are only one encoding: a footprint author may use an unfilled
`fp_poly` or a collection of `fp_line` segments.  Rejecting those boards leaves a visual/semantic
gap that an AI placement client cannot work around safely.  Accepting arbitrary graphics would be
worse: curves, arcs, diagonal edges, open chains, branches, and self-intersections do not have a
bounded exact collision interpretation in the current deterministic legalizer.

## Decision

Keep Board IR `0.2.0` and expand its already-ring-shaped courtyard value only to a closed,
simple, orthogonal subset:

- accept an unfilled `fp_rect`, an unfilled `fp_poly`, or one or more complete `fp_line` cycles;
- require every segment to be non-zero, horizontal, or vertical, and require line endpoints to
  form degree-two, non-duplicate closed components irrespective of their file ordering;
- normalize every accepted contour to immutable board-frame integer-nanometre `Ring` geometry;
- decide same-side overlap with an exact doubled-integer horizontal-strip scan.  It detects
  positive-area intersection and strict containment while allowing an edge/corner touch at zero
  clearance;
- charge every strip, scanned edge, and interval comparison to the existing placement budget;
- reject arcs, curves, diagonals, fills, open/branching/duplicate line chains, layer mismatches,
  and invalid/self-intersecting rings before a placement view exists.

The adapter accepts both `fill none` and KiCad 10's reserialized `fill no` spelling.  This is
observation and candidate legality only.  The current placement serializer/apply path remains
rectangular-courtyard-only and must refuse this new source topology rather than rewrite it.

## Evidence

`tests/fixtures/board-ir-v0.2/courtyard-orthogonal-chains.kicad_pcb` was saved by KiCad 10.0.5
and contains a concave eight-vertex `fp_poly` and an intentionally unordered four-segment
`fp_line` cycle.  KiCad CLI DRC reports zero violations and zero unconnected items.  The held-out
fixture used to stop at `unsupported.construct`; it now produces an immutable snapshot, proves the
unchanged separation clear, and refuses an overlapping `-20,000,000 nm` placement proposal.

## References

- [KiCad footprint file format: fp_line and fp_poly](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html)
- [KiCad PCB Editor: courtyard shapes and DRC](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html)
- [Courtyard topology research](../research/kicad-orthogonal-courtyard-topology.md)
