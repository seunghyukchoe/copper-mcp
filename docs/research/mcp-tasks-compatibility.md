# MCP Tasks compatibility — 2026-08-05

## Recorded result

CopperMCP's reference environment used **`mcp 2.0.0`** when this result was
recorded. The supported dependency range is broader, so the runtime probe records
the actually installed capabilities and tests the 2.0.0 facts through injected
module observations rather than pinning CI to one resolver outcome.

That runtime exposes the generic SEP-2133 extension API (`Extension`,
`MethodBinding`, and a `tools/call` interceptor), but its Task classes are the
incompatible historical nested-`task` shapes rather than the current draft's
direct `CreateTaskResult` fields, and it has no Tasks dispatcher. The runtime probe in
`copper_mcp.routing.task_bridge` reports this combination as unsupported and
does not advertise `io.modelcontextprotocol/tasks`.

| Requirement | `mcp 2.0.0` result | CopperMCP result | Decision |
| --- | --- | --- | --- |
| Generic extension capability negotiation | Present | Not sufficient alone | Do not advertise Tasks |
| `tools/call` polymorphic task result types | Absent | No safe serializer contract | Do not implement |
| `tasks/get`, `tasks/update`, `tasks/cancel` dispatcher | Absent | No lifecycle protocol contract | Do not implement |
| Owner-bound result lookup | Tasks `get` takes only `taskId` | Existing routing jobs require `authorization_digest` | Blocked |
| Durable task-handle lookup after server restart | No SDK support | Handle storage would require a new approved durable store | Blocked |

## Protocol facts and resulting gate

The official draft defines Tasks as a negotiated extension named
`io.modelcontextprotocol/tasks`; a client declares support in *per-request*
capabilities and the server may replace a `tools/call` result with a task
result.  It also requires the task to be durable before that result is
returned.  The current supported augmented method is `tools/call`.

The same draft defines `tasks/get` with only `{ "taskId": string }`, and uses
that ID again for `tasks/cancel`.  CopperMCP's durable job service deliberately
requires the caller's `authorization_digest` for lookup, cancellation, and
candidate export.  Returning a draft task today would therefore make deferred
result retrieval depend only on possession of a task ID, weakening ADR-0046's
caller-context authorization gate.  A client-declared extension capability is
not an authenticated owner identity.

Consequently, this slice implements no wire methods and returns no task handle
from ordinary MCP tools.  `start_routing`, `get_routing_job`,
`cancel_routing_job`, and `export_routing_candidate` remain the ordinary-tool
fallback.  No HTTP transport or remote authentication has been added.

## Implemented protocol-independent seam

`RoutingTaskHandleBroker` is an in-memory progressive-enhancement primitive,
not an MCP Tasks adapter.  It:

- mints a new 256-bit `secrets.token_urlsafe(32)` handle distinct from a
  deterministic routing job ID;
- binds every resolution and cancellation attempt to the original SHA-256
  caller-context digest using constant-time comparison;
- uses one non-echoing unavailable error for malformed, unknown, expired, and
  unauthorized handles;
- bounds retention to 1 second–24 hours (900 seconds by default) and purges on
  all access; and
- caps live in-memory handles (1,024 by default; 4,096 maximum).

It is intentionally unsuitable as a `CreateTaskResult` store: memory-only
records disappear on restart and cannot satisfy the draft's durable
`tasks/get` requirement.  A future adapter must add a reviewed durable,
authorization-bound task-handle repository and an authenticated session/owner
binding before switching this probe to supported.

## Official references

- [MCP Tasks extension overview](https://modelcontextprotocol.io/extensions/tasks/overview)
- [MCP Tasks draft specification](https://tasks.extensions.modelcontextprotocol.io/specification/draft/tasks)
- [MCP Tasks SEP-2663](https://tasks.extensions.modelcontextprotocol.io/seps/2663-tasks-extension)
