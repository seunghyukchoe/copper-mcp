# ADR-0057: Observe bounded front/back footprint poses

- Status: Accepted
- Date: 2026-08-05
- Owners: CopperMCP maintainers
- Related: M4 courtyard/footprint observation roadmap item; Board IR v0.2

## Context

The Board IR already models footprint side and board-frame child geometry, but the KiCad adapter was
front-side-only. Treating every back-side footprint as unsupported prevents useful observation, while
mirroring coordinates again during import would silently corrupt asymmetric pads and courtyards.

## Decision

Accept a narrow `F.Cu`/`B.Cu` observation subset with orthogonal footprint rotations, native identity,
owned pads, and matching unfilled rectangular `F.CrtYd`/`B.CrtYd` centerlines. Import KiCad's authored
board-frame child coordinates as written and do not apply a second back-side mirror. Keep inner-layer
footprints, non-orthogonal transforms, and non-rectangular courtyard topology fail-closed. Pin the
subset with a front/back asymmetric source fixture and a headless KiCad DRC run.

## Consequences

Board IR scenes can now represent both signal sides for the bounded rectangular-courtyard subset,
including side-aware pad and courtyard observation. The fixture is a valid KiCad source/CLI oracle,
not evidence of a GUI flip-save serialization round trip. General courtyard topology, side-aware
placement legality, live editor CAS, and placement apply remain separate roadmap gates.

## References

- [KiCad PCB Editor documentation](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html)
- [KiCad FOOTPRINT API](https://docs.kicad.org/doxygen/classFOOTPRINT.html)
