# ADR-0040: Advertise freshness-bound fill provenance on routed previews

- Status: Accepted
- Date: 2026-08-05
- Owners: `@seunghyukchoe`

## Context

ADR-0039 made freshness-verified KiCad fill islands exact foreign-zone obstacles in the pure A*
core, but the public `preview_route` contract only returned fill evidence for an
`already_connected` outcome. An AI/MCP caller therefore could not tell whether a routed candidate
was shaped by exact pour geometry or by the conservative zone envelope. That gap made the new route
quality result useful internally but not safely explainable at the tool boundary.

The MCP tools specification requires structured results to conform to the advertised output schema;
the output schema is also how clients discover which evidence is present. See the [official MCP
tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

## Decision

When `include_fill_authority` is requested and the selected layer contains a zone, `preview_route`
continues to refill only a private disposable copy and requires exact cached/refilled fill-digest
equality. It now passes every freshness-verified island to the deterministic router, including
foreign-zone islands, and a routed response may carry the same redacted `fill_authority` record.

The record adds one closed `routing_effect` literal:

- `foreign_zone_obstacles`: selected-layer foreign islands replaced a conservative zone envelope;
- `connectivity_evidence`: same-net islands were available for connectivity evidence;
- `both`: both roles were present;
- `verified_context`: the selected-layer fill was verified but no island had either role.

`already_connected` responses use `connectivity_evidence` when fill evidence is returned. Stale,
orphaned, and source-mismatched islands remain fail-closed in the router. No Board IR digest,
candidate identity, apply token, DRC authority, durable job, or mutation semantics change.

## Evidence

- `tests/test_route_preview.py` proves a synthetic foreign-zone preview returns a routed candidate
  and the `foreign_zone_obstacles` provenance label without changing workspace bytes.
- `tests/test_mcp_server.py` and its closed output-schema assertions cover the connected response
  and the new nested literal.
- B-022 records ten deterministic contract replays and confirms the routed response is schema-valid
  and source-preserving. B-021 remains the route-quality measurement for the underlying core.

## Consequences

AI clients can distinguish exact fill-aware routing from conservative routing without seeing raw
KiCad fill geometry. The evidence remains aggregate and revision-bound; it does not claim whole-board
completion, electrical behavior, DFM, fabrication readiness, or FreeRouting parity. The public
contract still exposes only the single-layer route preview; layered routing and durable export stay
separate gates.
