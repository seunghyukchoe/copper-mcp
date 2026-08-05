# KiCad orthogonal courtyard topology

Research date: 2026-08-05.  This note supports [ADR-0065](../adr/0065-orthogonal-courtyard-chains.md).
No external code is copied.

## Official source findings

KiCad's S-expression documentation defines `fp_line` with explicit `start`, `end`, and `layer`
fields, and `fp_poly` as a footprint graphic with a point list, layer, stroke, and optional fill.
It also specifies millimetres in source files and one-nanometre internal PCB/footprint resolution.
Those facts support exact decimal-to-nanometre conversion and topology reconstruction without
float arithmetic.  Source:
[KiCad S-expression format](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#footprint-line).

KiCad's PCB Editor documentation states that closed shapes on `F.Courtyard`/`B.Courtyard` form
the front/back courtyard and that DRC reports a courtyard overlap when two footprint courtyards
overlap.  It also reports malformed courtyards for non-closed shapes, while allowing multiple
unconnected shapes when each is closed.  Source:
[KiCad 10 PCB Editor](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#footprint-graphics-and-text).

## Bounded design chosen

The implementation accepts only simple closed orthogonal contours.  It reconstructs unordered
`fp_line` segments into independent degree-two cycles and accepts unfilled orthogonal `fp_poly`
and `fp_rect` contours on the footprint's matching courtyard side.  A doubled-integer horizontal
strip sweep pairs vertical-edge crossings using even-odd parity; two rings collide exactly when
one pair of open x-intervals overlaps in any strip.  This covers boundary crossings and
containment, while an edge/corner touch has zero area and remains clear at the current zero
clearance contract.

The choice is intentionally narrower than KiCad: no arcs, curves, diagonal edges, filled shapes,
nonzero custom `courtyard_clearance`, side flips, or placement mutation.  Each strip, edge, and
interval comparison consumes the existing legalizer check budget, so hostile high-vertex input
cannot bypass the operation ceiling.

## Measurable acceptance criterion

The committed, KiCad-resaved held-out fixture contains both a concave eight-vertex polygon and an
unordered four-line closed chain.  The predeclared result is:

| Measurement | Before | Required after |
| --- | --- | --- |
| Board IR conversion | `unsupported.construct` | snapshot succeeds |
| Unmoved same-side courtyards | not evaluable | `proven_clear` |
| `-20,000,000 nm` line-chain proposal | not evaluable | `courtyard_overlap=violated` |
| KiCad 10.0.5 DRC of source fixture | n/a | 0 violations, 0 unconnected |

The replay artifact is B-073.  It is evidence for this exact topology subset, not arbitrary
courtyards, DRC of a moved candidate, placement apply, electrical correctness, fabrication, or
FreeRouting parity.
