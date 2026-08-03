# ADR-0010: Read-only Board IR inspection and a shared request boundary

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

ADR-0009 made routing reachable, but a host still had no way to learn whether a board is
representable at all without choosing a net and a layer and attempting a full preview. That is a bad
trade: the caller pays for conversion plus a search only to learn the board was never convertible,
and the returned diagnostics describe the route rather than the board.

The preview also introduced the project's first untrusted JSON request parser. A second public
service that re-implemented those field, type, range, and character rules would let the two drift,
which is precisely how validation gaps appear.

## Decision

Two changes, taken together.

**A shared request boundary.** `request_boundary.py` owns the generic primitives — bounded object
parsing, unknown-field rejection that reports a count instead of echoing caller-supplied names,
missing-field rejection, integer range checks that treat booleans as non-integers, control-character-free text, real booleans, board paths, copper layer names, and typed
net-class constraints — and raises `RequestError`. Every public service parses through it, and each
keeps its own `RequestError` subclass so callers can still discriminate, translating at its own
parse entry point.

**A Board IR inspection service.** `summarize_board_ir(request, settings)` is a public application
service exposed as the `inspect_board_ir` MCP tool and the `copper-mcp board-ir` command. It
resolves and reads one workspace board, converts it under caller-supplied typed constraints, and
returns a frozen `BoardIrSummary` describing structure only:

- board revision, Board IR snapshot digest, and constraint digest;
- Board IR schema, schema version, and distance/angle units;
- copper layer identities, which are standard KiCad layer names;
- object counts per collection, including constraint-set collections; and
- bounded conversion diagnostic-code counts when the board is not representable.

`BoardIrSummary` enforces that a supported board describes its snapshot and reports no diagnostics,
and that an unsupported board reports diagnostics and describes no snapshot, layers, or counts.

## Consequences

- A host can now cheaply answer "is this board in the supported subset, and what is in it?" before
  committing to a route preview, and the narrow Board IR subset becomes visible rather than being
  discovered through preview failures.
- Structural counts are a genuine but deliberately shallow disclosure: how many pads, nets, zones,
  and layers exist. No coordinates, net names, pad or net identities, UUIDs, reference designators,
  or source bytes are returned, and a regression test asserts their absence from the serialized
  document.
- The constraint digest and snapshot digest change with caller constraints, which is correct — the
  Board IR snapshot is the board plus its typed constraints — and is covered by a test so the
  binding cannot silently become constraint-independent.
- Validation rules now have one home. Tightening a rule tightens it for every public service at
  once, and the CLI can build both request shapes from the same constraint field list.
- The service still converts the whole board to answer a structural question. That cost is bounded
  by the existing `ParseLimits` and board byte ceiling, but it is not free; a cheaper header-only
  probe would need its own parser and is not worth a second parsing surface.

## Alternatives considered

- Return the full Board IR snapshot: rejected because it is the board, restated. Exposing exact
  geometry through a routine inspection tool is a much larger disclosure than any current surface.
- Report net names so hosts can pick a net: rejected because net names are a listed protected asset.
  Callers already know the net they want to route.
- Add an `analyze_routability` precheck instead: rejected for now because answering it accurately
  means duplicating the router's preparation logic, and duplicated geometry rules drift.
- Leave validation duplicated per service: rejected for the drift reason above.

## References

- [ADR-0005](0005-canonical-board-ir.md)
- [ADR-0009](0009-non-mutating-route-preview.md)
- [Board IR and KiCad adapter contract](../architecture/board-ir.md)
