# ADR-0066: Publish a composed route bundle only as one all-or-nothing read-only plan

- **Status:** Accepted
- **Date:** 2026-08-06

## Context

`preview_route` (ADR-0009) returns one two-pin candidate at a time, and ADR-0055 supplies a bounded
present/history negotiated coordinator that already resolves several nets against one shared
lattice. An AI or MCP caller that wants two or more nets today has to call the single-net tool
repeatedly and then decide for itself whether the independent candidates can coexist. That decision
is exactly the one the caller is least able to make: independent same-base candidates routinely
overflow a shared lattice resource, and nothing in the single-net response reports it.

Handing the caller several candidates and letting it compose them would move a correctness decision
out of the deterministic core, which ADR-0001 forbids. Emitting a partial composition would be
worse: a caller that received "three of five nets routed" would have a plan whose remaining two nets
may be unroutable precisely because of the three that were published.

Every comparable public routing surface already carries its own record — ADR-0042, ADR-0050,
ADR-0059, and ADR-0060 — because each one adds a distinct contract, not just an implementation. A
new public MCP tool needs the same treatment: the decision ledger entry alone (D-140) records the
choice but not the contract a client may rely on.

## Decision

Add `preview_route_bundle` as a read-only application service with an MCP adapter, subject to four
binding rules.

**All-or-nothing.** The service accepts two through eight distinct Circuit Scene `net_ref_id`
values on one copper layer with one grid step, plus mandatory `expect_board_revision` and
`expect_snapshot_digest` compare-and-swap preconditions. It publishes a `RouteBundlePlan` only when
the coordinator completes the whole allocation with a candidate for every requested net, no
remaining connection or unrouted net, zero lattice overflow, and acceptance by the existing exact
cross-net swept-disc physical-clearance gate. Any other outcome returns a status and a fixed
diagnostic with no plan. There is no partial plan, and no field through which one could be returned.

**Double negotiation.** The entire composition is negotiated twice against the same immutable
snapshot and envelope, and the two results must be equal before a plan is published. Replaying the
whole allocation, rather than each route independently, is what proves the *negotiated* occupancy
and clearance decision is reproducible; per-route replay would only prove each search is
deterministic, which is already true and is not the property at risk.

Because both runs share one wall-clock budget, a budget that expires between them is classified as
budget exhaustion *before* the equality comparison, and reported with its own diagnostic. Treating
a timeout as a replay mismatch would report a resource outcome as a determinism failure and would
contradict the tool's `idempotent_hint`.

**Digest binding.** `bundle_id` is the SHA-256 of the canonical JSON of exactly the content that
determines the bundle: the schema tag, the Board IR base revision, the ordered `net_ref_ids`, the
sorted candidate IDs, the search settings, the replay and physical-check evidence counts, and the
coordinator's own `policy_digest`. The policy digest binds the iteration ceiling and the
penalty/budget envelope, so two bundles composed from identical references under different
coordinator policy cannot share an identity. Reference *order* is part of the identity because the
index seeds each per-net search; canonicalization applies to candidate *storage* order only.

The request boundary caps the accepted seed so that every derived per-net seed (`seed + index`)
stays inside the supported integer range, and the published JSON Schema advertises the same
ceiling. A schema-valid request must never reach the deterministic core as an out-of-range value.

**Explicit non-claims.** The response carries no DRC field, no apply token, no board bytes, no
derivative URI, and no persistence handle. Combined-board serialization exists only as an internal,
disposable benchmark aid with no public tool and no file write. A bundle is a plan; application
remains the separately authorized operation described by ADR-0025 and ADR-0059.

## Consequences

Callers gain a composition whose coexistence has been decided by the deterministic core rather than
inferred, and one stable identity to refer to it by. The cost is a narrow surface: two through eight
two-pin nets, one layer, one grid step, and the existing Board IR subset. Multilayer capacity, vias,
zones and fill, arbitrary rules, and general-board scaling remain out of scope.

Double negotiation doubles the coordinator cost for every successful bundle. That is accepted
deliberately: the replay is the evidence, and the budget is bounded and reported.

Because the identity binds the policy envelope, any future change to the coordinator's iteration or
penalty ceilings changes every `bundle_id`. That is the intended behaviour — a bundle produced under
different policy is a different bundle — but it means recorded benchmark identities must be
regenerated whenever that envelope moves.

## Alternatives considered

**Return the independent candidates and let the caller compose them.** Rejected: it moves the
coexistence decision out of the deterministic core, and the caller has strictly less information
than the coordinator that produced the candidates.

**Publish a partial bundle with the nets that did route.** Rejected: the published subset is often
the reason the remainder failed, so a partial plan is actively misleading rather than merely
incomplete.

**Replay each route independently instead of the whole allocation.** Rejected: it verifies a
property that is not in doubt and leaves the negotiated occupancy decision unverified.

**Reuse `preview_route` with a list argument.** Rejected: the single-net contract has a candidate
field, an optional DRC flag, and an apply-token path, none of which may exist on a composition. A
separate tool keeps those absences structural rather than conditional.

## References

- [Route-bundle research](../research/route-bundle-v1.md)
- [ADR-0002: MCP is an external adapter](0002-mcp-adapter.md)
- [ADR-0009: Bounded non-mutating route preview](0009-non-mutating-route-preview.md)
- [ADR-0055: Bounded negotiated congestion](0055-bounded-negotiated-congestion.md)
- [D-140](../ledgers/decision-ledger.md), [SEC-115](../ledgers/security-ledger.md),
  [R-111](../ledgers/risk-register.md), [B-079](../ledgers/benchmark-ledger.md)
