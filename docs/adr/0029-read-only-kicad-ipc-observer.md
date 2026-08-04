# ADR-0029: Add a redacted, read-only KiCad IPC observer

- Status: Accepted
- Date: 2026-08-04
- Owners: `@seunghyukchoe`
- Related: ADR-0002, ADR-0005, ADR-0022, SEC-023, SEC-025, B-008, B-010

## Context

The file-backed Board IR and Circuit Scene are deterministic and testable, but an AI client
cannot yet tell whether the board open in a user's KiCad editor is the same revision it just
inspected. KiCad's official IPC API is the correct editor boundary: KiCad 9/10 exposes a
request/reply socket to a running PCB Editor, and `kicad-python` is the official Python wrapper.
The API is synchronous and versioned, so an adapter must bound each call and fail closed on
version skew.

The first live surface must not become an accidental mutation channel. Returning raw board text,
net names, UUIDs, or geometry would also bypass Circuit Scene's author-text and revision-bound
contracts. The current environment has KiCad 10.0.5 installed but its IPC server is disabled;
the integration therefore needs a deterministic fake-client oracle and an explicitly unverified
real-session probe rather than a false live claim.

## Decision

Add `copper_mcp.kicad_ipc.inspect_live_board` and the read-only MCP tool `inspect_live_board`.
The adapter lazily imports the optional `kicad-python` package, accepts only local IPC endpoints,
uses a bounded request timeout, checks the binding version against KiCad, and obtains one live
board serialization. Object counts are derived from that captured serialization rather than ten
mutable collection getters, then a second serialization must match byte-for-byte before the
summary is returned. The response contains only numeric versions, a SHA-256 board digest, byte
count, object counts, endpoint class, and explicit read-only/source markers. It never returns the
serialized board or board-controlled strings.

The official Python wrapper returns a complete string before this adapter can inspect its size;
it does not expose a count-only or streaming request. The adapter therefore refuses an oversized
response immediately after return, parses counts with bounded S-expression token/node budgets,
and avoids additional per-object materialization. An isolated worker remains necessary before
claiming a hard pre-allocation memory ceiling for untrusted sessions.

The default path refuses a connected KiCad newer than the installed binding. A deliberately
operator-invoked development probe may opt into `future_api_unverified`, but that switch is not
an MCP argument and does not unlock any write API. The companion `hardware/kicad-ipc-plugin`
directory follows KiCad's official `plugin.json` schema and exposes one PCB action that prints
the same redacted record; it does not call `begin_commit`, `push_commit`, or item mutation APIs.

Live observation is evidence about the open editor only. It is not yet a Circuit Scene snapshot,
route input, placement input, DRC result, or revision-bound apply authority. Those consumers must
wait for an explicit live-to-scene binding and a separate candidate transaction ADR.

## Consequences

- MCP can now confirm that a local KiCad session is reachable and report a digest-bound board
  summary without exposing private design content.
- CI remains dependency-light because `kicad-python` is optional; deterministic fake-client tests
  cover the complete adapter contract, including endpoint refusal, payload ceilings, source-bound
  counts, revision-change refusal, false/future API refusal, and redaction.
- KiCad 9/10 GUI-only IPC limitations and version skew remain visible in the contract instead of
  being hidden behind a best-effort socket call.
- The roadmap's IPC-plugin item is closed for the read-only observation milestone; live scene
  binding, placement apply, and KiCad undo transactions remain separate roadmap work.

## References

- [KiCad IPC API for add-on developers](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/)
- [KiCad IPC API overview](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/)
- [Official kicad-python bindings](https://gitlab.com/kicad/code/kicad-python)
- [KiCad API plugin schema](https://gitlab.com/kicad/code/kicad/-/raw/master/api/schemas/api.v1.schema.json)
- [KiCad IPC research](../research/kicad-ipc-references.md)
