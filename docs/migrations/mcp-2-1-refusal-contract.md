# The `mcp` 2.1 refusal contract, and the lifted 2.1 cap

`0.10.0` pinned `mcp>=2.0.0,<2.1.0`. The next release declares `mcp>=2.0.0,<2.2.0`, so a
deployment that resolves dependencies afresh may land on the 2.1 line for the first time.

**There is no required deployer action.** This note exists because the *reason* the cap stood was
caller-visible, and so is the reason it no longer does.

## What changes for a client

Nothing, if the client was on 2.0.x and stays there. Nothing observable changes on that line: the
server raised typed refusals before and raises them now, and `mcp` 2.0.x rewrapped every escaping
exception as an anticipated `ToolError` with its text preserved either way.

If a deployment moves to `mcp` 2.1.x, two things become true that were not:

- **A deliberate refusal keeps its reason.** `path must stay inside the configured workspace`,
  `constraints.clearance_nm must be between 0 and 1000000000`,
  `live KiCad IPC observation is disabled; set COPPER_MCP_ALLOW_LIVE_IPC=1 to enable it` and every
  other audited refusal arrive as `ToolError` with the message intact, exactly as on 2.0.x.
- **An unhandled server defect no longer leaks its text.** On 2.1 the SDK classifies anything that
  is not `ToolError`/`ResourceError`/`MCPError` as a crash, replaces the message with a bare
  `Error executing tool <name>`, and keeps the traceback in the server log. A client that was
  parsing exception text out of a crash — which was never a supported contract — will see that
  text stop arriving. Refusal messages are unaffected.

A client that wants to tell the two apart on the 2.1 line can catch
`mcp.server.mcpserver.exceptions.UnexpectedToolError`, which subclasses `ToolError`. Note the
subclass relationship: `isinstance(error, ToolError)` is true for a crash as well, so an
"is this a refusal?" check must exclude `UnexpectedToolError` explicitly rather than rely on
`isinstance`.

## What does not change

- No schema version moves. No response shape, field, or literal changes.
- No refusal *message* changes. Every string that reaches a caller after this change already
  reached it on the 2.0 line; the translation restores that surface on 2.1 rather than opening one.
- Apply flags, single-use tokens, revision checks, and every board-write gate are untouched.
- The excessive-agency evaluation artifact is unchanged and replays byte-identically under both
  `mcp` 2.0.0 and 2.1.1.

## Choosing a resolution

Both ends of the declared range are exercised by the full test suite: `mcp==2.0.0` and
`mcp==2.1.1`. The upper bound is `<2.2.0` rather than `<3.0.0` on purpose — the contract this note
is about was changed by a *minor* release inside 2.x, so the bound tracks what has been tested
rather than what semantic versioning permits. Pin either end with confidence; nothing here is a
claim about a future 2.2 or about 3.x.

See [ADR-0121](../adr/0121-a-refusal-is-an-answer-and-a-crash-is-not.md) for the per-type audit,
`D-226` for the decision, and `SEC-163` for the review of the refusal-message surface.
