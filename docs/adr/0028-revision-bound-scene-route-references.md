# ADR-0028: Make Circuit Scene net references directly actionable for routing

- Status: Accepted
- Date: 2026-08-04
- Owners: `@seunghyukchoe`
- Related: ADR-0005, ADR-0009, ADR-0022, B-007

## Context

Circuit Scene deliberately withholds KiCad net names and exposes only Board IR net identities. The
original `preview_route` contract nevertheless accepted only a raw KiCad net name and hashed it
internally. Passing a scene's opaque `net_id` through that field hashed the identity a second time,
so the apparently composable observation-to-action path was not composable at all.

The failure was reproduced through the real MCP tool surface on the licensed
`rc-low-pass-routing-v1` audio fixture. Treating each of the scene's three references as a raw name
routed 0/3 nets; supplying the three hidden source names routed 3/3. An AI client could therefore
observe the exact relevant objects but could not act on any of them without an out-of-band secret.

A reference also must not float across board edits or conversion-policy changes. The board file
digest identifies the captured source bytes, while the Board IR snapshot digest binds those bytes
to the conversion inputs and canonical structure that produced the reference.

## Decision

`preview_route` accepts exactly one of two selector variants:

- `net` is the compatibility path for a caller that already knows a private KiCad net name.
- `net_ref_id` is copied verbatim from Circuit Scene and must be accompanied by both
  `expect_board_revision` and `expect_snapshot_digest` copied from the same response.

The reference variant validates the identifier as a Board IR net reference and checks the board
revision immediately after its bounded read. A mismatch is refused without parsing the changed
board. Only matching source bytes are converted under the supplied constraints; the resulting
snapshot digest is then checked before search, fill authority, token issuance, or DRC. The route
uses the reference directly; it never re-hashes it and never resolves it by disclosing or
reconstructing the raw net name. Either mismatch returns the typed, non-mutating `stale_revision`
outcome. `snapshot_digest` is null only when stale source bytes were refused before conversion.

The MCP input schema is a closed two-variant union and the route result is a closed five-variant
status union. A routed response cannot carry a connection or diagnostic; connected, refused,
pre-conversion-stale, and unsupported outcomes likewise advertise only their possible evidence.
Runtime input acceptance intentionally remains broad at the SDK boundary so the application's
bounded, non-echoing validation handles malformed private values. The handler then validates every
successful service result against the typed response before it becomes MCP `structuredContent`.

This does not grant mutation authority. Route preview remains read-only, and `apply_candidate`
remains a separate operator-enabled, candidate-bound operation.

## Consequences

- An MCP client can now chain `observe_board_scene` to `preview_route` without learning a KiCad net
  name. The benchmark routes 3/3 observed references, with canonical candidate JSON identical to
  the hidden-name oracle; the former contract routes 0/3 under the same counterfactual.
- Stale source bytes fail before Board IR conversion; stale converted meaning fails immediately
  after conversion. Neither can produce route search, DRC work, fill work, or apply tokens. Tests
  and the benchmark pin both preconditions independently.
- Schema-driven MCP clients can distinguish the two legal request shapes and exhaustively consume
  route output instead of receiving a vacuous open object.
- A raw net name remains available for compatibility and local operator workflows. It is not the
  preferred AI observation-to-action contract.
- The reference proves identity only inside the captured Board IR snapshot. It does not prove
  route quality, KiCad legality, electrical correctness, or fabrication readiness.

## Alternatives considered

- **Expose net names in Circuit Scene:** rejected. It would reverse the scene's text-minimization
  boundary and is unnecessary when the deterministic core already has a canonical identity.
- **Accept a scene reference in `net` and guess whether it is a name or an identity:** rejected.
  Ambiguous interpretation makes typos and future identifier formats dangerous and prevents an
  exact schema.
- **Require only the board revision:** rejected. The same file bytes can be converted under
  different constraint inputs, producing a different routing snapshot.
- **Require only the snapshot digest:** rejected. The public contract should make both the source
  capture and its converted meaning explicit, and the two checks produce a clearer failure mode.
- **Let the MCP SDK reject malformed inputs directly:** rejected for this boundary. Generic model
  validation errors can include attacker-controlled values; CopperMCP's service boundary returns a
  fixed non-echoing diagnostic instead.

## References

- [ADR-0009](0009-non-mutating-route-preview.md)
- [ADR-0022](0022-circuit-scene-observation.md)
- [Scene/action closure research](../research/scene-action-closure-references.md)
- [MCP API](../architecture/mcp-api.md)
