# Migrating Board IR 0.1 snapshots to 0.2

Board IR 0.2 is intentionally not auto-migrated from serialized 0.1 JSON. Version 0.1 flattened
pads into board space and did not retain footprint identity, origin, side, lock state, ownership, or
courtyard geometry. Inventing any of those fields would create a digest-valid snapshot that was not
actually observed from the source board.

To migrate:

1. Recover the exact original `.kicad_pcb` bytes named by `content.source.revision` and the same
   typed constraint profile used for the old snapshot.
2. Re-run the current `parse_kicad_bytes` converter or the `inspect_board_ir` service.
3. Resolve every conversion diagnostic. Unsupported courtyard primitives, back-side footprints,
   and non-orthogonal footprint transforms must be changed at the source or left on a compatible
   older consumer; they are never omitted automatically.
4. Store the newly encoded 0.2 snapshot and invalidate every candidate or cached scene bound to the
   old snapshot digest.

Expected compatibility behavior:

- `schemas/board-ir/0.1.0.schema.json` continues to validate historical 0.1 documents.
- The active runtime decoder rejects 0.1 and unknown versions.
- `schemas/board-ir/0.2.0.schema.json` requires a total `items.footprints` collection and exact pad
  ownership.
- A successful reconversion changes `snapshot_digest`; `constraint_digest` remains stable when the
  constraint profile and net set are unchanged.

No source board is mutated by migration.
