# ADR-0158: Layered trees bind complete pad connectivity

- Status: Proposed; scoped correctness review accepted, native/full validation pending
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0133](0133-native-optimization-execution-and-host-confirmation.md)

## Decision

Add a private layered-tree candidate and router for a complete two-through-thirty-two-pad net
on two through eight supported signal layers. Existing public pair candidate/request identities
and schemas remain unchanged. Native optimization uses the tree only when single-layer routing
does not complete a multi-pin target; two-pad layered routing retains its existing path.

Each terminal is an actual source pad on an admitted copper layer. Deterministic branches attach
to the connected source pads or earlier generated copper. True source-pad contact can require
zero added geometry; equal XY on disjoint layers never supplies that contact. Same-layer endpoints
may still need a layered detour. Shared through-vias are one physical object, and later searches
receive only the remaining cumulative via, expansion, node and obstacle-check allowances.

The candidate identity covers every terminal, branch, attachment, physical dimension, setting,
metric and policy/version/seed field. Bound nested collections before traversal or hashing. Replay
must reproduce the candidate against the exact source snapshot and request. Structural verification
requires the complete source net-pad set, layer-aware connectivity, valid attachments and complete
through-via spans. Exact source-pad unions consume pair work and observe cancellation.

Serialization preserves unrelated native objects, checks source/native identity and revision,
replays and verifies the candidate, then reparses and checks the complete net. Cancellation and
callback failure cannot publish a late result; fixed errors retain no private exception context.
The resulting bytes remain a private derivative. The existing final composed-board KiCad DRC and
job evidence gates remain mandatory before an optimization package can be reviewed.

## Limits and acceptance

This is deterministic nearest-prior-copper search, not a Steiner-optimal or negotiated general
autorouter. Zones, arcs and pre-existing selected-net copper remain explicit unsupported cases in
this private path; broader source conversion, candidate-bound refill and repair are separate work.
It does not add external-router conversion, physics judgement, host consent, apply or saving.

Owned unit layouts exercise three through thirty-two pads and two/four/six/eight layers, shared
vias, same-layer detours, existing source-pad contact, byte-preserving replay and adversarial
budget/cancellation/identity cases. They are not held-out routing quality evidence. The inner-only
SMD fixture is explicitly synthetic, not a valid native manufacturing fixture. A separate ordinary
outer-pad fixture prepares complete-board native DRC checks. Actual native execution, full
integration, protected hosted/main validation and held-out acceptance remain pending.
