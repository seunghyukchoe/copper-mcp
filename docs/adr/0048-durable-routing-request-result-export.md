# ADR-0048: Durable layered routing request, result, and export boundary

- Status: Accepted
- Date: 2026-08-05
- Owners: CopperMCP maintainers
- Relates to: [ADR-0043](0043-durable-routing-job-ledger.md), [ADR-0046](0046-routing-worker-leases-and-tasks-deferral.md), [ADR-0047](0047-redacted-candidate-manifest-persistence.md)

## Context

The routing ledger, worker lease, and redacted candidate manifest are useful only if a process
restart can recover the request that produced a job and a caller can explicitly retrieve the
candidate geometry. The MCP Tasks extension is not a substitute for that internal contract: the
current draft requires per-request negotiation of `io.modelcontextprotocol/tasks`, durable task
creation before a handle is returned, and a polymorphic result with `tasks/get`, `tasks/update`,
and `tasks/cancel`. Client support remains a compatibility concern. The authoritative overview and
draft specification are [MCP Tasks](https://modelcontextprotocol.io/extensions/tasks/overview) and
the [Tasks extension draft](https://github.com/modelcontextprotocol/ext-tasks/blob/main/specification/draft/tasks.md).

The existing deterministic layered router already accepts a bounded two-signal Board IR request,
and the worker already provides a CAS-backed single-worker lease. The missing slice is therefore a
protocol-independent request/result repository plus ordinary MCP operations, not an MCP Tasks
implementation or a general job scheduler.

## Decision

Implement a local-first `RoutingJobRepository` composed of four bounded SQLite stores:

1. lifecycle records from `RoutingJobStore`;
2. a normalized, deep-frozen request envelope bound to a caller-provided context digest;
3. a redacted `CandidateManifestStore` record; and
4. a separate, explicitly authorized candidate-geometry export store.

The first queue accepts only the file-backed two-signal layered request already supported by the
Board IR router. It rejects `live` requests and does not expose single-layer jobs, general
multilayer jobs, DRC, refill, serializer output, apply authority, remote authentication, or MCP
Tasks negotiation.

`start_routing` persists the request before dispatching one local worker. `get_routing_job`,
`cancel_routing_job`, and `export_routing_candidate` require the same caller-context digest. The
content-addressed job ID is an idempotency key only; it is never a bearer handle. Unknown, expired,
and wrong-context requests share the same unavailable response. Request envelopes reject board
bytes, prompts, credentials, DRC findings, and token-like fields; limits include maximum records,
TTL, nesting, field count, and serialized bytes. Candidate exports are separately bounded by
record count, TTL, and serialized geometry bytes, and their stored content address is recomputed on
read.

The worker rechecks the board-byte and Board IR snapshot CAS values before invoking the existing
deterministic router. It writes the export and redacted manifest before the final lifecycle CAS
completion; if a process fails between those writes and completion, the bounded orphan expires and
cannot be retrieved without a completed job record and matching caller context. No board file is
mutated by any of these operations.

## Alternatives considered

### Return raw geometry in the job record

Rejected. It would mix a redacted lifecycle surface with a geometry disclosure and make it harder
to audit caller authorization and retention independently.

### Use the job ID as authorization

Rejected. Deterministic IDs are intentionally replayable idempotency keys. Authorization is a
separate caller-context digest, and every lookup/cancel/export operation verifies it.

### Implement MCP Tasks first

Rejected for this increment. Tasks negotiation and client support are still version-sensitive, and
the protocol does not define CopperMCP's board/session authorization. The ordinary API provides the
stable internal contract needed to add a negotiated Tasks adapter later without changing routing
semantics.

## Consequences

The MVP gains restart-safe queued layered proposals and an explicit geometry handoff that can be
benchmarked and inspected through ordinary MCP clients. It is still not a general-purpose routing
queue: there is one local worker, no progress stream, no remote principal model, and no Tasks
capability advertisement. Candidate geometry remains proposal data; `apply_candidate` is a
separate explicit operation and is not reachable through this job surface.

## Verification

- `tests/test_routing_job_repository.py` covers restart, deep-freeze, authorization, expiry,
  candidate export, manifest binding, and redacted persistence.
- `tests/test_routing_job_mcp.py` covers closed ordinary tool arguments, lifecycle transitions,
  wrong-context refusal, worker completion, and explicit geometry export.
- Benchmark evidence is recorded as B-030 and the security review as SEC-046.
