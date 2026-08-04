# ADR-0038: Candidate-bound KiCad DRC for layered proposals

- Status: Accepted
- Date: 2026-08-05
- Owners: `@seunghyukchoe`

## Context

ADR-0036 bound a two-signal-layer A* proposal to a narrow Board IR subset, and ADR-0037 added a
source-preserving disposable serializer for its segments and full-stack through-vias. Those checks
prove deterministic geometry and Board IR round-trip equality, but they do not prove that KiCad
accepts the resulting S-expression, applies the board's clearance rules, or sees the net as
connected. The existing candidate-bound DRC path only accepts the legacy single-layer
`RouteCandidate`, so using it for layered output would either bypass the replay contract or weaken
the evidence type.

KiCad documents `pcb drc --format json --exit-code-violations` as the authoritative command-line
check: exit code `0` means no violations and exit code `5` means violations were found. The command
accepts a board file, so CopperMCP must still keep the derivative in the private snapshot boundary
and must not publish a candidate file to the caller workspace.

## Decision

Add the internal `run_layered_route_candidate_drc(requested_path, candidate, profile, settings,
request)` boundary. It:

1. captures the board and bounded project/rule/library context through the existing descriptor-
   anchored DRC context reader;
2. parses only the captured board bytes and requires the candidate base revision to match the
   resulting Board IR snapshot;
3. requires the original `LayeredRouteRequest`, replays it through `LayeredBoardRouter`, and
   serializes only the exact replay through ADR-0037;
4. replaces the board payload in the private context, rechecks the context budgets, and invokes the
   same fixed KiCad DRC command vector and bounded child process as ordinary candidate DRC;
5. recaptures the private context after KiCad exits, strictly parses the redacted JSON summary, and
   rejects any report/process or revision mismatch; and
6. recaptures the caller workspace and discards evidence if any board, rule, project, or local
   library byte or membership changed during the run.

`LayeredRouteCandidateDrcEvidence` binds the candidate identity, Board IR base revision, original
source revision, patched-board revision, complete patched-context revision, and immutable aggregate
`DrcSummary`. It contains no raw report descriptions, coordinates, UUIDs, net names, or board bytes.
The boundary is internal and read-only: no MCP or CLI exposure, durable export, apply token, editor
mutation, refill, or undo authority is added.

## Evidence

The blocked-pad fixture provides a narrow two-layer, via-required case. Five focused tests cover
private-context binding, stale/malformed refusal before KiCad, source preservation, and a real KiCad
10.0.5 run. Benchmark B-020 repeats the candidate-bound run ten times and records deterministic
candidate/serializer inputs, zero DRC errors, zero unconnected items, source preservation, and the
absence of workspace writes.

## Consequences

- The layered proposal can now carry authoritative KiCad DRC evidence without widening the public
  action surface.
- KiCad remains the source of truth for rule and connectivity checks; the lattice's conservative
  envelopes are not promoted to a fabrication or electrical guarantee.
- The supported subset is still exactly two signal layers, full-stack through-vias, bounded
  rectangular Board IR, and one candidate. Multilayer stacks, blind/buried vias, filled-zone
  routing, negotiated congestion, durable jobs, MCP exposure, and apply remain future contracts.
- A clean DRC result is evidence for this disposable candidate and captured context only; it is not
  whole-board completion, SI/PI, EMC, thermal, DFM, fabrication, or safety approval.

## References

- [KiCad command-line interface: PCB DRC](https://docs.kicad.org/10.0/en/cli/cli.html#pcb_drc)
- [KiCad board file format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/)
- [ADR-0036: Board IR layered proposal adapter](0036-board-ir-layered-proposal-adapter.md)
- [ADR-0037: Layered KiCad serialization](0037-layered-kicad-serialization.md)
- [B-020](../ledgers/benchmark-ledger.md)
