# ADR-0069: Gate live KiCad IPC on an operator opt-in and establish document type at the observer

- Status: Accepted
- Date: 2026-08-06
- Owners: `@seunghyukchoe`
- Related: ADR-0029, ADR-0030, ADR-0031, ADR-0032, ADR-0033, ADR-0044, ADR-0063, SEC-118, D-141,
  issues #75, #76, #77, #78

## Context

ADR-0029 established the live IPC observer and ADR-0030 through ADR-0044 built five more surfaces
on top of it. Four properties of that boundary turned out to be weaker than the surrounding
contracts claimed.

The observation layer parses whatever KiCad hands back with the bounded S-expression reader and
classifies heads — `footprint`, `pad`, `via`, `segment`, `net` — wherever they occur in the tree.
It never establishes that the tree is a board. `parse_kicad_bytes` does check the root, but nothing
downstream of the observer re-derives the counts from that check, so a foreign S-expression is
summarised as a PCB with plausible topology. On the Circuit Scene side the same fact was flattened
into a generic `syntax.invalid` diagnostic and returned as `supported: false`, which puts "this is
not a board" in the same bucket as "this is a board we cannot convert" — two different answers a
caller has to act on differently.

The compare-and-swap that makes an observation revision-bound reads the board twice. The first read
is charged against `max_board_bytes`; the second was encoded to UTF-8 first and length-checked
never, so an oversized second read materialised in full and was then reported as a concurrent board
edit rather than as the budget refusal it is.

Capture itself required no operator consent. `KICAD_API_SOCKET` was read from the ambient
environment, and with nothing set the adapter fell through to whatever socket the official binding
defaults to. Reaching a running editor is an outbound action against the operator's machine; the
apply surface already treats a comparable action as opt-in, and this one did not.

Finally, redaction — the property the whole surface rests on — was asserted by a single sentinel
substring, and several refusal paths were marked as covered only by the real binding.

## Decision

**Live IPC capture is operator-gated.** `Settings.allow_live_ipc` is read from
`COPPER_MCP_ALLOW_LIVE_IPC` with the same exact `{"0", "1"}` membership rule as
`COPPER_MCP_ALLOW_APPLY`: no case folding, no truthiness, default off. Both capture chokepoints —
`capture_live_board` and `capture_live_editor_context` — refuse with `KicadIpcDisabledError` before
the endpoint is read, so a disabled deployment discovers no ambient socket and opens none. Every
live surface is downstream of those two functions and is therefore gated by one check. The MCP
tools stay **listed** and answer with a refusal naming the flag; hiding them would make the
capability undiscoverable and invite retry loops. The opt-in enables local IPC only. TCP endpoints
remain refused, and `KICAD_API_TOKEN` is still never passed to the binding and never serialized.

**Both observers establish the document type themselves.** The counter refuses a serialization
whose root is not `kicad_pcb` before it classifies anything. The Board IR adapter reports a foreign
root under its own diagnostic code, `unsupported.document`, and both Circuit Scene observer paths —
file-backed and live — turn that code into a typed `CircuitSceneError` rather than a
`supported: false` document. A KiCad board carrying an unsupported version or construct is
unaffected and still returns a truthful unsupported result with its diagnostic counts.

**The confirmation read is charged against the same budget as the first read.** It is length-checked
before any encode and compared as text rather than as bytes, so an oversized second read is a
payload-budget refusal and never materialises an unbudgeted encoded copy. An in-budget change is
still the connection-class refusal it was, because the budget check must not swallow the
compare-and-swap it sits in front of.

## Consequences

- A deployment that has not set `COPPER_MCP_ALLOW_LIVE_IPC=1` cannot reach a running editor at all.
  This is a behavior change for any existing configuration that relied on ambient discovery; the
  refusal names the flag so the fix is one environment variable.
- A foreign document can no longer obtain a board digest, a snapshot digest, or a topology summary
  from either observer. The two paths now refuse for the same reason with the same shape.
- `unsupported.document` is a new diagnostic code on the Board IR conversion surface. Callers that
  matched `syntax.invalid` to detect a wrong document type must match the new code.
- The refusal paths for a closed editor, a refused socket, an unreadable selection, and an
  unreadable selected-item identity are exercised by fakes rather than carrying a coverage pragma.
  Those fakes are locally defined stand-ins for `kipy.errors.ApiError` and
  `kipy.errors.ConnectionError`: `kicad-python` is an optional dependency and is absent from CI, and
  the adapter catches `Exception` at those boundaries, so what the tests pin is the typed CopperMCP
  refusal and its message, not the binding's class identity. **This is not a claim that the real
  binding raises at exactly those points.**
- Redaction is now asserted by a whole-response grep over a hostile fixture carrying a distinct
  marker in each author-controlled slot, plus a guard that the fixture's objects were actually read.
  A single sentinel could only ever have proved one slot.
- No live mutation, DRC, fill, apply, electrical, or fabrication authority is added or implied. The
  opt-in enables observation only.

## Alternatives considered

- **Hide the live tools when the flag is off.** Rejected for the reason the apply surface already
  records: an absent tool is indistinguishable from an unimplemented one, so a client cannot explain
  the situation to the operator and will retry.
- **Gate only the ambient-environment path and leave an injected client ungated.** Rejected. The
  boundary that matters is "may this process talk to an editor", not "how was the endpoint found".
- **Re-parse the source in the Circuit Scene observer to check the root.** Rejected: it doubles the
  parse cost on every board to re-derive a fact the adapter already computed. Giving that fact its
  own diagnostic code carries it out at no cost.
- **Keep the confirmation comparison in bytes and add a byte-length check after encoding.** Rejected:
  the encode is exactly the allocation the budget exists to prevent.

## References

- [ADR-0029: Add a redacted, read-only KiCad IPC observer](0029-read-only-kicad-ipc-observer.md)
- [ADR-0030: Bind a bounded KiCad IPC snapshot to Circuit Scene](0030-live-ipc-circuit-scene-binding.md)
- [KiCad IPC observer research](../research/kicad-ipc-references.md)
- [Safe candidate application references](../research/safe-apply-references.md)
