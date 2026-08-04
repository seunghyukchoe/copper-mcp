# ADR-0033: Keep live editor context read-only and revision-bound

Status: Accepted
Date: 2026-08-04

## Context

An AI placement or routing proposal needs to know what the operator is looking at in KiCad.
The official `kicad-python` Board API exposes `get_active_layer()`, `get_layer_name()`, and
`get_selection()` as structured reads. Its `get_selection_as_string()` endpoint returns board
file text and is therefore not an acceptable model-facing boundary.

## Decision

Add `inspect_live_editor_context` as a read-only MCP/service tool. The request must use
`board: "live"` and both board/snapshot SHA-256 preconditions; an optional context digest binds
follow-up calls to the same active layer and selected-item set. The adapter confirms the board
serialization and reads the layer/selection twice, refusing changes during observation. Only
whitelisted official wrapper types with a validated native UUID become typed refs such as
`pad:kicad:<uuid>`; empty, unknown, malformed, or over-budget selections fail closed. No raw
board text, selection text, coordinates, net names, project tokens, or mutation API is read or
returned.

The current context adapter aliases the snapshot digest to the confirmed board serialization
digest because it does not yet require a caller-supplied constraint profile for Board IR
canonicalization. The field remains explicit so a future semantic snapshot can replace the alias
without changing the MCP shape.

## Consequences

- AI policy can ground proposals in the operator's active layer and selected native refs.
- Context changes are detectable without granting editor mutation authority.
- The official binding remains optional; fake-client tests prove the contract when KiCad IPC is
  unavailable.
- A live KiCad session, placement transaction, routing transaction, DRC, ERC, and electrical or
  fabrication claim remain out of scope.
