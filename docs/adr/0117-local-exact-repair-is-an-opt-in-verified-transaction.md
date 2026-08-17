# ADR-0117: Local exact repair is an opt-in verified transaction

- **Status:** Accepted
- **Date:** 2026-08-17
- **Related:** [ADR-0055](0055-bounded-negotiated-congestion.md),
  [ADR-0064](0064-policy-bound-initial-negotiated-order.md),
  [ADR-0112](0112-external-route-candidates-enter-through-a-disposer.md),
  [integration gate](../research/exact-local-repair-negotiated-integration-gate.md), issue #90

## Context

The deterministic local exact-repair operator could solve one bounded abstract lattice window, but
the negotiated coordinator could not publish its geometry safely. A `RepairWindowCandidate` did
not bind a Board IR snapshot, world grid, rejected allocation, physical occupancy model, or
validator work. The existing policy transaction deliberately supplied no repair windows and
rejected any policy that returned one. Directly replacing a rejected route would therefore have
made the proposal its own authority.

The candidate-path validator and external-candidate disposer now establish the missing acceptance
vocabulary. The remaining decision is how the coordinator derives, meters and binds a repair
without changing legacy route identity or treating repair work as free.

## Decision

### Separate opt-in transaction

`negotiate_routes` accepts an internal `repair_settings` selector. Absence preserves the historic
result type, router calls, serialized fields and `negotiated-congestion-v2` candidate identity. The
transaction runs at most once and only after one complete candidate allocation is rejected by the
existing candidate-set physical-clearance gate. It does not run for malformed, cancelled,
incomplete or budget-exhausted allocations.

The coordinator derives one immutable provenance record from the verified snapshot and negotiated
envelope. It binds the snapshot and envelope digests, iteration, target request, conflicting
candidate IDs, world-coordinate grid, deterministic bounded window, Board IR projection and
same-pass conflict projection. The latter expands candidate copper by track width and the stricter
resolved net-class clearance; centreline occupancy is insufficient.

Board IR occupancy reuses the reference router's width-and-clearance-aware preparation and edge
predicate behind one narrow internal projection adapter. The window area is checked before unit
cells are materialized. Duplicate candidates, foreign layers, grid disagreement, malformed
digests, stale snapshots and forged provenance fail closed. Converting provenance into a local
request additionally requires the coordinator's private capability.

### Policy and work authority

An optional second-stage in-process policy may select only the exact coordinator-supplied repair
window. It cannot create, move or widen a window. The isolated-worker profile is refused because
its current protocol does not version this second transaction.

The transaction carries fixed bounds for attempts, projection cells, local expansions, validator
path edges and validator obstacle checks. Area and conflict bounds are checked before expansion;
the local operator and candidate-path validator consume their declared ceilings; success evidence
records bounded consumed work. Conversion errors, budget exhaustion and cancellation publish no
partial repaired allocation.

### Disposal and identity

The exact operator returns an abstract path whose request and result digests are rechecked. The
coordinator constructs a provisional immutable candidate, then requires `validate_candidate_path`
against the original Board IR. Accepted geometry is reidentified with a composite digest binding
the negotiated envelope, repair provenance, local result, policy selection when present and
validator evidence. The coordinator replaces only the selected candidate and reruns the ordinary
whole-set physical-clearance gate.

Only a completed replacement publishes `RepairNegotiatedRoutingResult` with redacted digests and
bounded work counts. Every refusal publishes no repaired candidate or evidence and follows the
defined legacy rejected-allocation semantics. No board bytes, geometry, paths, pad/net names,
apply token or mutation capability are added.

## Consequences

- Exact repair becomes a bounded proposer whose output is independently disposed before
  publication.
- The legacy no-opt-in coordinator and candidate identities remain unchanged.
- This is one deterministic one-window transaction, not a general repair scheduler.
- The reference A* obstacle model remains an internal dependency localized behind a projection
  adapter and pinned by repair, validator and physical-clearance tests.
- No routing-quality improvement is claimed. The existing predeclared fixture already completes
  without repair; broader held-out evidence remains required before closing issue #90.
- No MCP, CLI, persistence, external process, live editor, apply or physical-board authority is
  added.

## Rejected alternatives

- **Use congestion-ledger cells as physical occupancy:** rejected because the ledger records
  allocation pressure, not width-and-clearance-aware Board IR exclusion.
- **Accept policy-authored windows:** rejected because selection is advisory and geometry authority
  remains with the coordinator.
- **Publish after local digest verification:** rejected because abstract-lattice validity is not
  Board IR obstacle or candidate-pair physical validity.
- **Charge repair to no budget:** rejected because bounded work is part of the capability contract.
- **Change the default candidate identity:** rejected because an unused opt-in transaction cannot
  invalidate existing replayable candidates.

## Verification

Focused tests cover deterministic provenance, hostile and forged inputs, Board IR and same-pass
clearance projection, preflight bounds, atomic conversion refusal, policy subset enforcement,
isolated-profile refusal, local and validator budgets, cancellation, candidate-path disposal,
whole-set physical recheck, redacted evidence and legacy replay/identity compatibility. The local
suite, Ruff, repository lint, strict type checking and diff validation pass. Held-out quality and
physical-board calibration are explicit future gates.
