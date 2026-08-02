# ADR-0001: Candidate-first mutation model

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

Routing is long-running, board state can change concurrently, and AI output is untrusted. Directly
editing the live board during search would make cancellation, comparison, provenance, and recovery
unsafe.

## Decision

Every routing or placement operation produces an immutable candidate tied to a content-addressed
base revision. Validation and comparison operate on candidates. Applying a candidate is a separate,
explicitly authorized operation that rechecks the live board revision and commits one undoable patch.

## Consequences

The project gains reproducibility, safe cancellation, human review, and multi-candidate comparison.
It must maintain a candidate store, patch schema, garbage collection, and revision-rebase strategy.

## Alternatives considered

- Live incremental mutation: rejected because partial and stale results are hard to audit or undo.
- Full-file replacement: rejected because it risks unsaved state and obscures exact changes.
