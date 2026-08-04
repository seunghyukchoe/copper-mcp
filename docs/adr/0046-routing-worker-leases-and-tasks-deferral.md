# ADR-0046: Execute durable routing jobs with bounded leases; defer MCP Tasks

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** CopperMCP maintainers
- **Related:** ADR-0043, ADR-0044, ADR-0045

## Context

`RoutingJobStore` already persists a redacted, revision-bound lifecycle record, but it did not
own an execution boundary. A queued job could not be claimed safely by one worker, a crashed
worker could leave a record in `running`, and an executor returning malformed candidate data could
leave the job non-terminal. Those gaps prevent a reliable asynchronous route proposal even though
the deterministic router and candidate verifier are available.

The current MCP Tasks ecosystem is not the historical 2025-11-25 core API. The current Tasks
extension is negotiated per request, uses `tasks/get`, `tasks/update`, and `tasks/cancel`, has no
`tasks/list` or `tasks/result`, and requires durable creation before returning a task handle. Its
task IDs are unguessable bearer handles. The installed Python SDK still exposes legacy types and
does not provide a compatible dispatcher. Primary references:

- [MCP Tasks extension overview](https://modelcontextprotocol.io/extensions/tasks/overview)
- [Tasks extension specification](https://tasks.extensions.modelcontextprotocol.io/specification/draft/tasks)
- [SEP-2663: Tasks extension](https://tasks.extensions.modelcontextprotocol.io/seps/2663-tasks-extension)
- [2026-07-28 MCP release candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)
- [Historical 2025-11-25 Tasks utilities](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)

## Decision

Add a protocol-independent `RoutingJobWorker` with a single active lease per worker:

1. Claim only `queued` records through the store's revision-qualified CAS transition.
2. Expose an opaque in-memory lease and a read-only `CancellationProbe`; executors cannot mutate
   the store or receive board bytes through this seam.
3. Bound lease and cancellation-poll intervals; detect expired `running` records and close them
   as a generic `worker_error` rather than silently retrying a potentially changed board.
4. Publish only a verified candidate identity/base revision through `RoutingJobStore.complete`.
   Invalid, stale, or malformed executor output becomes a terminal generic `invalid_request`
   diagnostic; executor exception text is never persisted and terminal messages are stable
   code-based literals.
5. Map cooperative cancellation to the existing CAS cancellation states and keep all terminal
   transitions revision-safe.

Do **not** expose MCP Tasks yet. First add bounded request/result or candidate-manifest storage,
ordinary `start_routing`/`get_routing_job`/`cancel_routing_job` tools, and an authorization-bound
random task handle distinct from deterministic job IDs. Then pin a target MCP extension/SDK matrix
and implement only the negotiated `tools/call` augmentation behind a feature flag. The adapter
must not implement the superseded `tasks/list` or `tasks/result` methods, and must not return a
task handle when the client has not declared the extension.

## Consequences

Positive:

- A single local worker cannot double-claim a job, and a process restart can close stale leases.
- Cancellation and malformed candidate output have bounded terminal states instead of ambiguous
  `running` records.
- The worker remains transport-independent and candidate-first, so future stdio/loopback tools
  or a version-gated Tasks adapter can reuse the same lifecycle contract.

Trade-offs and residuals:

- Candidate geometry and normalized request payloads are not persisted by this slice; durable
  export and ordinary MCP job tools remain open.
- A deterministic `RoutingJobSpec.job_id` is an idempotency key, not an MCP Tasks bearer handle.
  A future adapter must mint a separate cryptographically random, owner/context-bound task ID.
- Worker recovery closes an expired lease as failure rather than retrying automatically; retry
  requires a new immutable request or an explicitly designed replay policy.
- No live KiCad IPC, DRC, apply, electrical, fabrication, or FreeRouting claim is added.

## Evidence

- `src/copper_mcp/routing/job_worker.py` provides the bounded worker/lease seam.
- `tests/test_routing_job_worker.py` covers claim races, lease recovery, cancellation races,
  successful candidate identity publication, bounded executor failure, invalid candidate refusal,
  and redacted SQLite storage.
- B-028 records deterministic worker outcomes and the no-board-content persistence check.
