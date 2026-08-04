# ADR-0044: Live layered route proposals are session- and snapshot-bound

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** CopperMCP maintainers
- **Related:** ADR-0030, ADR-0031, ADR-0036, ADR-0042, ADR-0043

## Context

CopperMCP can already observe an active KiCad PCB through the official `kicad-python` IPC
binding, and it can propose a bounded two-signal-layer route from a file-backed Board IR
snapshot. The missing fidelity seam was a via-capable proposal against the exact board that an AI
has just inspected in the open editor.

KiCad documents `Board.get_as_string()` as the complete board serialization and describes IPC as
a synchronous, GUI-only request/reply API. The add-on guidance also states that
`KICAD_API_TOKEN` is unique to a running KiCad instance and changes on restart:

- [KiCad Board API](https://docs.kicad.org/kicad-python-main/board.html)
- [KiCad IPC add-on guidance](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/)
- [KiCad IPC API](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/)

Board and Board IR digests alone cannot distinguish two editor instances that contain identical
bytes. Conversely, a live IPC read can race a GUI save unless the serialization is confirmed.

## Decision

Add `preview_live_layered_route` as a read-only, idempotent MCP proposal surface with this
pipeline:

1. Require the literal `board: "live"`, two typed pad references, bounded net-class/grid/search
   settings, and **three** compare-and-swap values: source board digest, converted Board IR
   snapshot digest, and a redacted SHA-256 digest of `KICAD_API_TOKEN`.
2. Reject unknown fields and raw `net`/`net_ref_id` selectors before opening IPC. The net is
   inferred only after both pads are resolved in the converted snapshot and prove one non-null
   net.
3. Capture one bounded official IPC serialization, confirm a byte-identical second serialization,
   verify the token digest did not change during capture, and close the client in all success and
   failure paths. The token itself never enters a response, log, candidate, or ledger.
4. Convert those exact bytes through the existing Board IR 0.2 adapter and invoke the pure
   two-layer A* router with the remaining request deadline. The capture deadline is checked
   between synchronous IPC calls and throughout bounded serialized-item counting. A stale source
   or session stops before conversion; a stale Board IR snapshot stops before search.
5. Return the existing closed layered candidate/refusal union. A candidate is immutable and
   content-addressed, but remains **unverified proposal geometry**: this surface does not serialize
   KiCad objects, run DRC or refill, persist geometry, mint an apply token, or mutate the editor.
   Endpoint-via legality and full electrical/fabrication validity remain explicitly unclaimed.

The common response accepts either the file-backed or live request echo, with the live schema
preserving `board: "live"` and the session digest precondition. No raw board source, net name,
socket path, token, or unvalidated diagnostic text crosses MCP.

## Consequences

Positive:

- AI can propose through-via geometry against the same live editor instance and exact bytes it
  observed, closing the highest-value observe → understand → propose fidelity gap.
- File-backed and live paths share candidate canonicalization; the fake-IPC benchmark can compare
  candidate identity directly with the file oracle.
- IPC client closure and bounded remaining timeout prevent repeated proposals from leaking sockets
  or silently exceeding a short route budget.

Trade-offs and residuals:

- The token digest is only a session CAS signal; KiCad exposes no atomic board revision/event API,
  so an ABA change that returns to identical bytes between confirmations remains possible.
- The official synchronous wrapper may block inside a call and allocates the returned serialization
  before Python can enforce the byte ceiling; cooperative checks cannot forcibly pre-empt that
  call. An isolated worker/process boundary remains open for hostile or unresponsive sessions.
- The supported geometry is intentionally narrow (two signal layers, conservative envelopes,
  full-stack vias). Real GUI IPC, KiCad DRC, serializer round-trip, endpoint-via legality,
  electrical behavior, fabrication readiness, and FreeRouting parity require separate evidence.

## Evidence

- `tests/test_live_layered_route_preview.py`: deterministic file-oracle equality, three CAS paths,
  timeout bounding, source immutability, no raw-net selector, and via-required fixture.
- `tests/test_kicad_ipc.py`: client closure after success/failure and editor-context capture.
- `scripts/benchmark_live_layered_route_preview.py` and
  `benchmarks/results/routing/2026-08-05-live-layered-route-preview.json`: ten fake-IPC replays,
  deterministic candidate IDs, stale/capture-race refusal, closure, and explicit no-GUI/no-DRC
  metrics.
