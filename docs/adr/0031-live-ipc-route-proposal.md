# ADR-0031: Keep live KiCad route proposals read-only and revision-bound

**Status:** Accepted
**Date:** 2026-08-04

## Context

`observe_live_board_scene` binds the exact UTF-8 serialization returned by the official KiCad IPC
API to Circuit Scene `0.2.0`. An AI client needs a way to ask the deterministic router what it
would propose for one visible net without copying the board to disk or guessing a private KiCad
net name. A live editor is mutable and the IPC server is synchronous, so a proposal must not imply
that the editor accepted, displayed, or can safely apply the result.

## Decision

Add `preview_live_route` as a read-only MCP/application surface with these invariants:

1. The request uses literal `board: "live"`, a Circuit Scene `net_ref_id`, and both the observed
   board digest and Board IR snapshot digest. Raw `net` names are not accepted.
2. The request is parsed and all action flags are rejected before opening IPC. DRC, zone refill,
   apply-token issuance, and every mutation are separate future contracts.
3. One bounded `capture_live_board` call supplies the exact source bytes. The bytes are converted
   through the existing fail-closed KiCad adapter; the candidate is bound to that Board IR
   snapshot and the returned board revision is the captured source digest.
4. A stale board digest returns a typed refusal before conversion. A stale snapshot digest returns
   a typed refusal before A* search. The result uses the existing immutable route-preview status
   union and contains no raw board text, names, UUIDs, or IPC client object.
5. The MCP tool is annotated read-only and idempotent. Its advertised request is closed and
   requires both compare-and-swap preconditions, while runtime validation remains the shared
   non-echoing boundary.

This is a proposal oracle, not live action authority. Applying it requires a future IPC session
compare-and-swap, KiCad transaction/undo semantics, post-action observation, and authoritative
validation evidence.

## Boundary amendment — 2026-08-05

The route operation deadline is created before opening IPC. The capture receives both the absolute
deadline and a remaining timeout clamped to the adapter's bounded maximum, so connection and
serialization work cannot silently consume time beyond the proposal budget. This remains
cooperative timing; a blocking official IPC call is not hard process-preemptible.

## Consequences

An AI/MCP client can complete the loop “observe active editor → select an opaque net reference →
receive a deterministic route candidate” with exact provenance and stale-session refusal. The same
candidate can be compared with the file-backed route oracle when the serialized bytes are
identical. The implementation still requires the optional `kicad-python` binding and a running IPC
server for production; CI uses a deterministic fake client and claims no live GUI, DRC, placement,
electrical, or fabrication result.

Related: [ADR-0028](0028-revision-bound-scene-route-references.md),
[ADR-0029](0029-read-only-kicad-ipc-observer.md),
[ADR-0030](0030-live-ipc-circuit-scene-binding.md),
[B-013](../ledgers/benchmark-ledger.md), and [SEC-028](../ledgers/security-ledger.md).
