# Migrating Placement 0.1 clients to 0.2

Placement 0.2 makes rectangular courtyard legality a required candidate gate. Requests remain
source-compatible, but clients generated from the closed Placement 0.1 output contract must
regenerate and handle the new evidence. Candidate bytes and identifiers intentionally change.

## Output changes

- `placement_version` is exactly `0.2.0`; a 0.1 candidate is not accepted as a 0.2 candidate.
- `legality.courtyard_overlap` changes from the sole value `not_modelled` to the required values
  `proven_clear | violated`.
- Candidate evidence adds the literal `courtyard_policy`, `courtyard_footprints_checked`,
  `courtyard_pairs_checked`, and `missing_courtyard_footprints`.
- A courtyard collision now produces `illegal_placement` and no candidate. Unsupported topology,
  a non-orthogonal moved pose, or a rectangle below 10,051 nm on either axis produces typed
  `unsupported_geometry` and no candidate.

## Client actions

1. Regenerate bindings from the current MCP output schema and add exhaustive handling for both
   courtyard verdicts and the existing typed refusal codes.
2. Treat coverage counts as evidence, not decoration. `proven_clear` with missing courtyards does
   not mean the separate KiCad `missing_courtyard` check passed.
3. Discard persisted 0.1 placement candidates and cached candidate IDs. Re-preview against the
   exact current board and Board IR revisions.
4. Do not infer project custom `courtyard_clearance`, general courtyard topology, full KiCad DRC,
   or apply authorization from the 0.2 result.

Request fields, `validate-snap-v1` ordering, Board IR `0.2.0`, and Circuit Scene `0.2.0` are
unchanged by this placement contract bump. There is still no placement apply consumer, so this
migration never mutates a board.

See [ADR-0027](../adr/0027-kicad-courtyard-legality.md) and the
[pinned KiCad source/oracle matrix](../research/courtyard-legality-references.md) for the exact
supported subset.
