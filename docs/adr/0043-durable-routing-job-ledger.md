# ADR-0043: Durable routing-job ledger before protocol tasks

## Status

Accepted for the internal lifecycle subset. Ordinary MCP job tools, worker execution,
candidate persistence/export, and the MCP Tasks extension remain separate follow-up gates.

## Context

CopperMCP already produces immutable, revision-bound route candidates, but a long-running AI
workflow has no durable handle for queued work, cancellation, or restart recovery. The MCP Tasks
protocol is moving from the 2025 core design to an experimental `io.modelcontextprotocol/tasks`
extension, and the installed Python SDK exposes types without a ready task store or dispatcher.
Binding the core to either wire shape would make the lifecycle fragile and could advertise a
capability the host cannot negotiate.

## Decision

Add a transport-independent `RoutingJobSpec`, immutable `RoutingJobRecord`, and SQLite-backed
`RoutingJobStore` with these invariants:

- job identity is deterministic and content-addressed, so a repeated validated request is
  idempotent without persisting a caller prompt or board bytes;
- source and optional Board IR snapshot digests, endpoint pad references, router/policy identity,
  seed, and explicit resource ceilings are immutable job inputs;
- transitions are queued → running → cancel-requested/cancelled, completed, or failed;
  every transition requires the observed record revision and is committed transactionally;
- completion verifies either legacy or layered candidate identity, endpoint binding, and base
  revision, while storing only the candidate ID and base digest in the job record;
- the SQLite ledger is bounded by record count and TTL, reopens after process restart, and uses
  one indistinguishable unavailable error for unknown and expired IDs;
- stored JSON is closed, canonical, redacted, and capped; board bytes, geometry, prompts,
  credentials, raw net names, and DRC findings are never accepted by this API.

The store is a lifecycle primitive, not an executor. Worker leases, candidate manifests/export,
ordinary MCP start/get/cancel tools, and an extension adapter are deferred until each has its own
authorization, replay, and compatibility tests.

## Consequences

AI hosts can safely retain and recover a bounded job handle without treating a candidate ID as
apply authority. A worker can claim and publish through a compare-and-swap record, while a stale
worker cannot overwrite a newer transition. The current slice does not claim background routing,
MCP Tasks support, candidate export, DRC evidence persistence, or placement/live-editor actions.

## References

- [MCP Tasks (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
- [MCP Tasks extension draft](https://raw.githubusercontent.com/modelcontextprotocol/ext-tasks/main/specification/draft/tasks.md)
- [Python `sqlite3` transactions](https://docs.python.org/3/library/sqlite3.html)
- [SQLite atomic commit](https://www2.sqlite.org/atomiccommit.html)
