# ADR-0050: Expose opt-in DRC evidence for file-backed layered proposals

- Status: Accepted
- Date: 2026-08-05
- Owners: `@seunghyukchoe`
- Related: [ADR-0008](0008-candidate-bound-kicad-drc.md), [ADR-0035](0035-internal-layered-search-oracle.md), [ADR-0048](0048-durable-routing-request-result-export.md), [B-032](../ledgers/benchmark-ledger.md)

## Context

The layered Board IR router can now produce a two-signal-layer candidate with full-stack vias,
and the private serializer already replays that candidate through KiCad's fixed-argument DRC
boundary.  Keeping this evidence entirely internal leaves an MCP host unable to distinguish a
candidate that was checked from one that was only structurally verified.  Conversely, exposing a
whole patched board or raw DRC findings would disclose design data and imply an apply capability.

KiCad documents `kicad-cli pcb drc` as a report-producing validation command, and its Python Board
API provides the revision-aware primitives needed by a later apply transaction.  This decision
uses only the existing disposable, read-only DRC path; it does not add a Board API mutation.

## Decision

`preview_layered_route` accepts an explicit boolean `include_drc` for file-backed requests.  When
the result contains a candidate, the service replays the exact candidate with the same bounded
deadline and returns only `RouteCandidateDrcEvidence`: candidate ID, base/source/patched/context
digests, KiCad version, and an aggregate redacted summary.  The summary's compatibility `passed`
field means no active errors or unconnected items; its stricter `clean` field additionally
requires zero warnings, exclusions, ignored checks, and violation types.  This distinction keeps
warning-only evidence from being advertised as a clean board.  The service fails closed when the
authoritative check cannot run, the evidence is malformed or candidate-unbound, or the deadline
expires before it starts.  The workspace board is not modified, and no raw report, board bytes,
net names, or geometry outside the existing candidate response crosses the boundary.

`preview_live_layered_route` and durable routing jobs force `include_drc` to `false`; direct job
preparation rejects a true value as well, so a caller cannot persist an option that a worker would
silently ignore.  `already_connected`, `not_routed`, `stale`, and `unsupported_board` responses
carry `drc_evidence: null`.

## Evidence and limits

`B-032` measures the public schema and binding/replay contract without invoking KiCad.  `B-038`
adds warning-only and malformed-authority regression evidence, including the strict `clean`
signal and structured-output validator.  The
blocked-pad integration fixture separately exercises the public service with KiCad 10.0.5 and
records `passed=true`, zero errors, zero warnings, and zero unconnected items while preserving
source bytes, inode, and mtime.  This is evidence for the supported two-layer, full-stack-via
proposal subset only.  It is not whole-board DRC, refill authority, multilayer generalization,
fabrication approval, electrical validation, placement validation, or FreeRouting parity.

## Consequences

- MCP hosts can request a machine-checkable, candidate-bound authority signal without receiving
  private KiCad diagnostics or mutation authority.
- The preview deadline now covers both route search and the optional DRC replay; cancellation is
  passed into the layered search so a slow route cannot consume the DRC budget silently.
- Live and durable routing remain candidate-only until their own session/worker evidence contracts
  exist.
- A future serializer/export/apply flow must preserve these bindings and add its own review rather
  than treating this evidence as authorization.

## References

- [KiCad CLI documentation: `pcb drc`](https://docs.kicad.org/10.0/en/cli/cli.html#pcb_drc)
- [KiCad Python Board API](https://docs.kicad.org/kicad-python-main/board.html)
- [ADR-0008: Candidate-bound authoritative KiCad DRC evidence](0008-candidate-bound-kicad-drc.md)
