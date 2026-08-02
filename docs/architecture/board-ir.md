# Board IR and Candidate Contracts

## Goals

The Board IR must be precise enough for geometry and stable enough for tools, datasets, and replay.
The current manifest is deliberately small; it will evolve through versioned schemas and ADRs.

Required invariants:

- Coordinates use an explicitly declared integer unit; floating-point coordinates are not canonical.
- Every board snapshot, constraint set, policy, model, and candidate is content-addressed.
- Identifiers remain stable within one base revision.
- Locked items and existing user routes are explicit, not inferred.
- Electrical and manufacturing constraints remain typed data rather than prompt text.
- Candidate patches contain operations and provenance, never a silent full-file overwrite.
- Unknown schema fields are rejected at mutation boundaries and preserved only where explicitly
  designed for forward compatibility.

## Current schemas

- [`schemas/board-manifest.schema.json`](../../schemas/board-manifest.schema.json)
- [`schemas/candidate.schema.json`](../../schemas/candidate.schema.json)

Schema versions and software versions are independent. Compatible software releases may support
multiple schema versions. Breaking schema migrations require an ADR, migration utility, fixtures,
and changelog entry.
