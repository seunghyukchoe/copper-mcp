# ADR-0009: Bounded non-mutating route preview as the first public routing surface

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

ADR-0006 produced candidates, ADR-0007 serialized exact replays into disposable KiCad bytes, and
ADR-0008 bound one such replay to authoritative DRC evidence. All three boundaries were reachable
only from Python, so no host could obtain a routing result, and the honest capability inventory in
`server_info` could not name routing at all.

The remaining question was not whether to expose routing, but what the smallest safe public surface
is. A durable job model, candidate persistence, export, and apply each need their own authorization
and lifecycle contracts. A read-only preview needs none of them: it can answer "what would this
router propose, and does KiCad agree?" within one bounded call.

## Decision

`preview_route(request, settings)` is a public application service exposed as the `preview_route`
MCP tool and the `copper-mcp preview-route` command. It returns a frozen `RoutePreview` and creates
no file, job, export, or board mutation.

The service:

1. parses the untrusted request through a strict boundary that rejects unknown fields, non-integer
   or out-of-range budgets, booleans supplied as integers, control characters, oversized net names,
   and any layer name outside the documented KiCad copper set;
2. builds the typed constraint profile from caller-supplied integer net-class values only, so board
   files never supply their own routing constraints;
3. resolves and reads exactly one `.kicad_pcb` beneath the configured workspace through the existing
   symlink- and traversal-safe boundary, bounded by `max_board_bytes`;
4. converts those bytes through the fail-closed Board IR adapter and returns
   `unsupported_board` with bounded diagnostic-code counts whenever any conversion diagnostic is
   present, including warnings;
5. runs the ADR-0006 router under a wall-clock deadline (`COPPER_MCP_MAX_ROUTE_PREVIEW_SECONDS`,
   default 30 s) layered on top of the existing grid, expansion, obstacle, and obstacle-check
   ceilings, and returns `not_routed` with one typed non-echoing diagnostic on any expected failure;
   and
6. binds ADR-0008 evidence only when the caller sets `include_drc`, and fails the whole call rather
   than returning a candidate with missing or mismatched evidence.

`RoutePreview` validates its own bindings: a routed preview must carry exactly one candidate whose
base revision equals the previewed Board IR digest; an unrouted preview must carry exactly one
diagnostic; an unsupported board carries neither and must report conversion codes; and DRC evidence
must match the candidate ID, its base revision, and the previewed source revision. `to_dict()`
returns a detached plain dictionary.

## Consequences

- Routing is reachable from MCP hosts and CI for the first time, and `server_info` can name it
  without overstating maturity.
- Candidate-bound DRC evidence becomes publicly reachable, but only through this opt-in flag and
  still only as aggregate counts. Raw findings, coordinates, UUIDs, and net names remain redacted.
- A preview returns the exact integer geometry it generated, plus the two endpoint pad IDs. This is
  an intentional, documented disclosure: a candidate that cannot be read cannot be reviewed. Hosts
  that must not disclose generated geometry to a model should not enable this tool. The source
  board's other nets, pads, copper, and bytes are still never returned.
- Every unsupported board is reported as bounded diagnostic-code counts rather than raw adapter
  messages, so the narrow Board IR subset stays visible without echoing board content.
- The wall-clock deadline makes preview latency bounded even when the integer work ceilings would
  admit a long search, at the cost of a `cancelled` diagnostic that depends on host speed. Candidate
  identity never depends on it.
- Preview implies nothing about durability. Nothing is stored, so a caller that wants the same
  candidate again must re-run the same request against the same board revision.

## Alternatives considered

- Ship durable routing jobs first: rejected because job identity, per-principal authorization, and
  cancellation semantics are a larger security surface than the routing result itself.
- Expose the router without DRC: rejected because internal grid post-checks are not authoritative,
  and a public routing surface without an authoritative check invites exactly that confusion.
- Always run DRC: rejected because it would make every preview depend on a local KiCad installation
  and a subprocess.
- Return only metrics and cost, hiding geometry: rejected because it produces an unreviewable
  result while still disclosing that a route exists and how long it is.
- Accept a constraint profile parsed from the board's own project files: rejected because ADR-0005
  keeps constraints typed, caller-supplied, and separate from untrusted board content.

## References

- [ADR-0005](0005-canonical-board-ir.md)
- [ADR-0006](0006-bounded-deterministic-astar.md)
- [ADR-0008](0008-candidate-bound-kicad-drc.md)
- [MCP API contract](../architecture/mcp-api.md)
