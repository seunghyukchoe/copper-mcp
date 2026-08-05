# ADR-0061: Observe post-placement state through one captured scene and DRC context

- **Status:** Accepted
- **Date:** 2026-08-05

## Decision

Add read-only, file-backed `observe_post_placement`. It requires an expected board digest,
captures the workspace board plus permitted project/rule/library context once, builds a bounded
Circuit Scene from those bytes, and runs fixed private KiCad DRC against that same capture. It
returns only the scene and aggregate redacted DRC summary; stale, malformed, unavailable, or
changed context discards the whole response.

It accepts no candidate manifest or apply token and emits none. It proves current file state, not
mutation provenance. Rendering, live IPC, apply changes, ERC/electrical/fabrication signoff, and
general footprint fidelity remain out of scope.

## Boundary amendment — 2026-08-05

The request is parsed through the shared Circuit Scene boundary before opening the workspace.
The expected board digest is checked against the descriptor-captured board bytes before reading
project/rule/library sidecars for DRC. The context capture must still reproduce those bytes before
scene/DRC work proceeds, and the final context re-capture remains mandatory. Placement rules naming
padless footprints are refused before syntactic contradiction checks so unsupported references
cannot be hidden behind an `infeasible_constraints` result.
