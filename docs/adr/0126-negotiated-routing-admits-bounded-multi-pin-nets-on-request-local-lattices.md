# ADR-0126: Negotiated routing admits bounded multi-pin nets on request-local lattices

- Status: Accepted
- Date: 2026-08-29
- Owners: `@seunghyukchoe`
- Related: [Issue #90](https://github.com/seunghyukchoe/copper-mcp/issues/90),
  [ADR-0019](0019-multi-pin-component-merging.md),
  [ADR-0055](0055-bounded-negotiated-congestion.md),
  [ADR-0064](0064-policy-bound-initial-negotiated-order.md),
  [ADR-0073](0073-declared-negotiation-policy-slots.md),
  [ADR-0117](0117-local-exact-repair-is-an-opt-in-verified-transaction.md),
  [D-233](../ledgers/decision-ledger.md), [R-184](../ledgers/risk-register.md),
  [SEC-169](../ledgers/security-ledger.md), and
  [B-124](../ledgers/benchmark-ledger.md)

## Context

ADR-0055 deliberately admitted only distinct two-pad requests whose pads shared one absolute world
lattice. That was a sound first negotiated-routing boundary, but B-124 measured it as the blocker
in front of the local-repair evidence gate: the coordinator admitted 0 of 20 committed
SimpleRouteJson boards. Sixteen boards could form a two-request envelope but every route B-088 had
accepted on them belonged to a net with at least three pads; four boards could not form the required
two-request envelope. The exactly-two-pad control population failed for a complementary reason:
its pads did not share one world origin even where they shared a pitch.

The single-net A* backend already has a bounded deterministic multi-pin component-merging contract
under ADR-0019. The missing capability is therefore not another routing algorithm. It is a safe
coordinator contract that can invoke the existing one for the corpus it already routes, without
turning the negotiated resource ledger into a physical-clearance authority it cannot be.

Request-local lattice phases make that distinction load-bearing. Absolute unit-edge and vertex
keys from two different phases need not coincide even when the physical copper crosses. The keys
remain useful for ordering and congestion pressure, but they are not a complete collision proof.
The exact whole-set physical-clearance verifier that already gates publication is the authority.

## Decision

Widen the internal `negotiate_routes` coordinator, and only that coordinator, as follows:

- Each request carries between **2 and 32 pads** on the selected signal layer. The request tuple
  still shares one immutable Board IR revision, signal layer, width, clearance and grid step, but
  each request may retain its own lattice origin. No global origin-congruence conjunct remains.
- A returned candidate must bind the exact request net, exact pad count and the lexicographically
  first and last pad identities. Its independently validated connection evidence must cover the
  same complete pad set. A two-pad request retains its existing one-path shape and all previously
  published two-pad snapshot, policy and candidate identities byte-for-byte.
- The no-fill negotiated seam accepts only candidates whose `fill_binding` is absent. Independent
  semantic replay compares that field as well as topology, cost, identities and connections, so a
  custom router cannot attach unverified fill provenance and re-hash it into an apparently valid
  result.
- Demand is estimated from the complete selected-layer pad set, not from the candidate endpoints:
  the Manhattan span of the pad bounding box is divided by the common grid step with ceiling
  division and capped at 1,000,000 cells. This is O(pads), remains bounded by the 32-pad ceiling and
  preserves the legacy two-pad value exactly.
- The existing absolute unit-edge and lattice-vertex ledger remains a deterministic heuristic.
  Every zero-overflow allocation must still pass the independent exact whole-set physical-clearance
  gate before any candidate is published. A shifted-phase crossing invisible to the ledger must
  therefore retry or refuse; it may never pass on the structural ledger alone.
- Local exact repair stays limited to a target whose request and candidate each have exactly two
  pads. Multi-pin local repair needs its own window, provenance and topology proof and is not
  inferred from this admission change.

The public contract does not widen. No MCP, CLI, durable-job, persistence, schema, route-bundle,
apply or live-editor surface accepts negotiated envelopes in this slice, and no board bytes are
written.

## Consequences

The coordinator can now exercise the same 2–32-pad selected-layer population the underlying A*
router already supports, including requests with equal pitch but different lattice phases. Cheap
validation still precedes routing, one- and 33-pad requests refuse before the backend is called,
and cancellation, expansion, obstacle, physical-comparison and iteration ceilings remain in force.

The price is explicit: structural congestion accounting can under-count a conflict between shifted
phases. This can cost iterations or turn a geometrically feasible set into a bounded refusal. It
cannot authorize overlapping copper because the final physical verifier is mandatory and atomic.
That is an accepted completeness and quality limitation, not a safety relaxation.

Candidate identity policy is intentionally unchanged. The exact legacy two-pad identities are
pinned, and multi-pin candidates are reconstructed from trusted request state under the same
versioned negotiated policy. A future public or persistent surface may require a new external
schema version, but no such surface exists here.

## Evidence and limits

Focused production tests pin the 2- and 32-pad boundaries, refuse 1 and 33 before router work,
exercise deterministic request-local origins, prove complete multi-pin connection evidence, preserve
the existing two-pad digests, and reject forged endpoint, pad-count, order and fill bindings
atomically. Mutation-oriented fixtures make every intermediate pad contribute to demand and make
ceiling division observable. A shifted-lattice crossing whose structural resource sets are disjoint
is still caught by the exact physical gate, and a multi-pin conflict cannot enter the two-pin repair
transaction.

B-124 remains immutable evidence of the old contract. A successor census must bind the same B-088
submitted set, freeze the expected admission population before measuring, run the production
coordinator without repair, prove deterministic replay and uninstrumented parity, and publish only
aggregate redacted results from a clean commit. Until that artifact lands, this ADR claims contract
coverage, not corpus completion or route-quality improvement. Even after it lands, issue #90 remains
open unless local repair actually fires and improves a held-out case under the existing physical
and work gates.

That successor must report two post-admission signals separately. A complete allocation that reaches
the physical-clearance trigger is not yet a usable input to the present repair transaction: the
violating candidate tuple must also contain a two-pad target, because multi-pin targets are excluded
above. Conflating those counts would make the new admission capability look like repair evidence it
is not.

No KiCad DRC, electrical, SI/PI/EMC, thermal, DFM, fabrication or hardware conclusion follows from
this decision.

## Alternatives considered

- **Keep the two-pad, shared-origin conjunct and search for another corpus.** Rejected as the next
  machine slice because B-124 already measured that this conjunct excludes the routed population
  the repository owns, while the single-net backend already supports it.
- **Normalize all requests onto one finer world lattice.** Rejected because it can multiply search
  work, change pinned two-pad routes and identities, and still does not replace physical clearance.
- **Treat absolute resource-key disjointness as physical legality.** Rejected because different
  lattice phases provide a concrete counterexample; the existing exact physical gate is the sound
  authority.
- **Enable local repair for multi-pin candidates in the same slice.** Rejected because the present
  repair window and provenance bind one two-terminal target. Generalizing them is a separate
  topology decision and is not needed to measure whether the coordinator reaches the repair gate.
