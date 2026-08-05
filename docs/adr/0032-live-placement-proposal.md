# ADR-0032: Keep live placement proposals read-only and revision-bound

**Status:** Accepted

## Context

The live route proposal closes the active KiCad snapshot → Board IR → deterministic router path,
but placement still only accepts a workspace file. KiCad's official Python Board API exposes the
complete board serialization, while write APIs such as `update_items`, `push_commit`, and `save`
change the editor and require a separate undo, compare-and-swap, and post-action observation
contract. Board IR 0.2 also intentionally omits several footprint-carrying fields needed for a
faithful rewrite (properties, graphics, library identity, and 3D pose).

## Decision

Add `preview_live_placement` as a read-only MCP/service surface. Its request:

- uses the literal `board: "live"`;
- copies footprint/pad references from Circuit Scene;
- requires both the live board digest and Board IR snapshot digest;
- reuses the existing seven ref-anchored rules, proposal language, placement view, legalizer, and
  immutable candidate contract; and
- has no DRC, fill, apply-token, editor-write, or raw-source fields.

The service captures one byte-confirmed IPC snapshot, refuses a stale board digest before Board IR
conversion, refuses a stale snapshot digest before placement-view/legalizer work, and returns
`board_path: "live"` only as a label. A candidate remains a proposal, not an instruction to KiCad.

## Evidence and limits

The fake-client B-014 oracle must prove deterministic replay, equality with the file-backed
placement oracle, stale precondition refusals, zero mutating IPC calls, and no raw source in the
structured output. It does not claim a running GUI session, KiCad DRC, placement mutation, undo,
or fabrication readiness. The official API documents `get_as_string()` as the complete board
serialization and `get_active_layer`/`get_selection` as read APIs; mutation methods remain outside
this ADR's authority.

## Consequences

AI can propose a placement against the board currently open in KiCad without an intermediate file
export, while the deterministic core remains the only geometry and legality authority. A future
write contract must add a disposable projection/re-observation oracle and explicit single-undo
authorization before it can call KiCad mutation APIs.

## Boundary amendment — 2026-08-05

The placement operation deadline is created before opening IPC. The capture receives both the
absolute deadline and a remaining timeout clamped to the adapter's bounded maximum, so a slow live
snapshot cannot consume more than the placement preview budget. This remains cooperative timing;
blocking official IPC work is not hard process-preemptible.

## References

- [KiCad Python Board API](https://docs.kicad.org/kicad-python-main/board.html)
- [KiCad IPC API](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/)
- [ADR-0024: Typed placement intent](0024-placement-intent-and-legalization.md)
- [ADR-0026: First-class Board IR footprints](0026-first-class-footprints-in-board-ir.md)
- [ADR-0031: Live route proposal](0031-live-ipc-route-proposal.md)
