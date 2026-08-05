# ADR-0058: Check same-side rectangular courtyard legality

- Status: Accepted
- Date: 2026-08-05
- Owners: CopperMCP maintainers
- Related: M4 placement legality; Board IR v0.2; KiCad courtyard rules

## Context

KiCad describes courtyards as the keep-out envelope used to prevent footprint overlaps and exposes
`courtyard_clearance` as a placement rule. CopperMCP already imports bounded rectangular courtyard
rings, but the placement legalizer reported `not_modelled`, allowing a candidate to look complete
while silently skipping a high-value physical placement check.

## Decision

Evaluate every proposed pair of rectangular courtyard rings on the same physical side with exact
integer rectangle predicates. Edge contact is not overlap at the default zero clearance. Front and
back courtyards are independent layers, so cross-side overlap is not reported as a same-side
courtyard collision. A Board IR v0.2 conversion rejects non-rectangular courtyard topology before
the placement view exists. The closed result vocabulary is `proven_clear` or `violated`, and a
violation makes the candidate illegal.

## Consequences

Placement previews now report an actual side-aware courtyard result and can refuse an overlapping
candidate without depending on a model or a GUI. The check is exact only for the current rectangular
subset; configurable nonzero courtyard clearance, line-chain/polygon topology, thermal/mechanical
rules, post-placement connectivity, live CAS, and apply remain separate gates.

## References

- [KiCad placement guidance](https://docs.kicad.org/8.0/en/getting_started_in_kicad/getting_started_in_kicad.html)
- [KiCad courtyard-clearance rule](https://docs.kicad.org/doxygen/panel__setup__rules__help__2constraints_8h.html)
- [KiCad PCB Editor DRC descriptions](https://docs.kicad.org/9.0/en/pcbnew/pcbnew.pdf)
