# ADR-0047: Persist redacted candidate manifests, not route geometry

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** CopperMCP maintainers
- **Related:** ADR-0043, ADR-0046

## Context

The durable routing ledger records only a candidate ID and base revision. That is enough for a
compare-and-swap transition, but a restarted worker or future job-status tool cannot inspect a
bounded candidate summary without reopening the original in-memory result. Persisting the full
route patch would turn a job database into a hidden board export and could retain proprietary
geometry beyond the caller's intended lifetime.

## Decision

Add `CandidateManifestStore`, a local SQLite store for immutable, content-addressed summaries:

- candidate ID, base revision, pad references, candidate kind, router, policy, path/via counts,
  bounded numeric cost/metric fields, and an optional job ID;
- canonical manifest digest independent of store timestamps;
- injected-clock TTL, record-count and payload ceilings, restart persistence, idempotent put, and
  uniform unknown/expired lookup errors;
- optional binding to a `RoutingJobSpec` so base revision, endpoints, kind, router, policy, and job
  identity cannot drift;
- tamper detection through the stored digest and closed JSON shape.

The manifest schema rejects geometry, vertices, board bytes, raw net names, prompts, credentials,
DRC findings, and diagnostic-like metric keys. It does not serialize or export the candidate patch.
Future `start_routing`/`get_routing_job` tools may return this summary only after an explicit
authorization and result contract is added.

## Consequences

Positive:

- A worker restart can recover a safe candidate summary without retaining board geometry.
- The manifest is independently addressable and bound to the same immutable source/job identity
  used by the routing ledger.
- TTL, capacity, closed schemas, and tamper checks are testable without KiCad or a remote service.

Residuals:

- Durable route geometry export, candidate rehydration, ordinary MCP job tools, and MCP Tasks remain
  open. A future Tasks handle must be random and authorization-bound, not the deterministic
  candidate/job digest.
- A manifest is not DRC, electrical, fabrication, or FreeRouting evidence.

## Evidence

- `src/copper_mcp/routing/candidate_store.py` implements the bounded store.
- `tests/test_routing_candidate_store.py` covers reopen/idempotency, TTL/capacity, tamper and job
  binding refusal, injected clocks, and no-geometry/no-board payload assertions.
- B-029 records the redacted persistence replay.
