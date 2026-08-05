# ADR-0062: Keep padless courtyards in placement collision evidence

- **Status:** Accepted
- **Date:** 2026-08-05

## Decision

Retain rectangular courtyard geometry from graphics-only (padless) footprints as immutable,
stationary collision envelopes in the placement view. Such footprints remain unavailable as
placement subjects, anchors, or rule references because this version has no copper hull for their
placement semantics. A movable footprint that overlaps their same-side courtyard must nevertheless
be refused with `courtyard_overlap=violated`; the fixed footprint is not included in the candidate
manifest.

This matches KiCad's placement model: its documentation describes courtyards as the physical
placement extents that generally should not intersect and reports a courtyard-overlap violation
when two footprint courtyards overlap. The implementation remains intentionally narrower: only
the Board IR v0.2 rectangular courtyard subset is modeled, with zero custom courtyard-clearance
support and no claim about arbitrary polygon/line-chain topology.

## Consequences

- Stationary padless geometry is immutable and cannot grant an apply capability.
- Placement and rule resolution continue to reject padless references as unsupported.
- Placement preflight also checks declared subject references in request order before it makes a
  syntactic infeasibility claim, so an unrelated front/back contradiction cannot mask a known
  padless subject.
- The candidate contains only movable, pad-owning footprints, while legality considers all supported
  same-side courtyard envelopes.
- The regression and B-050 replay measure this boundary without invoking KiCad or mutating a board.

## Sources

- [KiCad Getting Started: placing footprints](https://docs.kicad.org/9.0/en/getting_started_in_kicad/getting_started_in_kicad.html)
- [KiCad DRC courtyard-clearance constraint](https://docs.kicad.org/doxygen/panel__setup__rules__help__2constraints_8h.html)
