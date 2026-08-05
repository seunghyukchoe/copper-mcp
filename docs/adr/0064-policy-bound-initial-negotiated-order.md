# ADR-0064: Bind a closed routing-policy decision to the initial negotiated order

- **Status:** Accepted
- **Date:** 2026-08-05
- **Related:** [ADR-0055](0055-bounded-negotiated-congestion.md),
  [AI routing-policy boundary](../research/ai-routing-policy-boundary.md), B-036, B-060

## Context

ADR-0055 provides a bounded, candidate-only `negotiate_routes` coordinator. It begins every run
in stable `(net_id, seed)` order and, after a conflicted pass, reorders retries by its exact
conflict scores. The closed `routing.policy` seam already admits only a canonical,
input-digest-bound permutation of known net IDs plus selections from coordinator-supplied windows.
It cannot represent board bytes, Board IR, route vertices, route widths, costs, candidates,
validation results, or apply authority.

The useful evidence supports bounded scheduling, not learned copper. PathFinder establishes the
present/history congestion and rip-up pattern used by ADR-0055, but its FPGA resource model is not
PCB clearance or fabrication evidence ([McMurchie and Ebeling, 1995](https://doi.org/10.1109/FPGA.1995.242049)).
The available PCB and IC learning work motivates evaluating constrained routing choices and offline
traces; it does not establish direct learned geometry, KiCad DRC, or manufacturing safety
([Liao et al.](https://arxiv.org/abs/1906.08809),
[Li et al.](https://doi.org/10.1109/ICICM63644.2024.10814469), and
[Goldie et al.](https://arxiv.org/abs/2004.10746)).

This decision integrates the existing seam at the smallest truthful point: the first pass's net
order. Corridors, repair windows, cost weights, later retry ordering, model hosting, MCP exposure,
and board mutation are not part of this slice.

## Decision

### Closed pre-routing policy transaction

`negotiate_routes` gains one optional internal `policy_profile: str | None` selector. It is
resolved only through a private immutable allowlist/registry; callers cannot supply a policy
object, factory, module path, model endpoint, or model output. The initial allowlist has exactly
one entry, `deterministic-reference-v1`, mapped to `DeterministicReferencePolicy`. An absent
profile preserves the existing deterministic coordinator. An unknown, malformed, disabled, or
failed profile is rejected; it never falls back to ordinary routing.

A future captured or isolated policy needs its own registered adapter and profile entry. The
adapter, rather than a caller, owns construction and must satisfy a later isolated-worker
resource/cancellation contract before it can enter the allowlist. This remains an internal
integration seam, not a public plugin admission path.

After the coordinator validates the immutable snapshot, negotiated envelope, and every request
against that snapshot, and before it constructs or calls a router, it performs this one
transaction:

1. Check cancellation. If observed, return the existing atomic `CANCELLED` result without
   constructing/evaluating a policy or calling a router.
2. Derive one immutable `RoutingPolicyInput` from that accepted snapshot, envelope, and the
   coordinator's pre-routing state. Derivation is deterministic, side-effect-free, bounded by the
   policy contract, and versioned with this integration. Its input is only: the envelope board
   revision digest; PolicyBounds(0, 0, 0, 0) as a neutral fixed integer cell window, not board
   geometry; every requested net once, canonically sorted, with bounded integer criticality,
   demand, and pre-routing congestion features; and no corridor or repair candidates in this first
   slice. It must not carry raw Board IR, board text/bytes, pad IDs or locations, net names,
   route/copper data, model prompts, or validator output. The coordinator, not the policy, derives
   every feature and clamps it through the existing closed dataclasses.
3. Resolve the selected profile from the private immutable registry once and evaluate its
   registered adapter once through `evaluate_policy`; then check cancellation again. The
   decision must have the declared stable policy ID, the exact canonical input digest, a
   permutation of every requested net exactly once, and only coordinator-supplied selections.
   Because this slice supplies no windows, valid `corridor_hints` and `repair_windows` are
   empty.
4. Canonicalize and record both `policy_input_digest` and `policy_decision_digest`. Do not
   record raw features, IDs, window data, prompts, model text, exception text, or copper in results
   or traces. These digests are linkable content bindings, not secrecy claims.

Any profile resolution/adapter error, policy exception, malformed/non-policy return, identity
mismatch, digest mismatch, missing/repeated/foreign net, or non-empty/unsupplied selection fails
closed as the existing fixed, redacted `INVALID_REQUEST` result. It publishes no candidates or
connections and makes **zero** router calls. Error diagnostics must not include exception or model
output. This does not add a `POLICY_REJECTED` terminal status: rejection occurs before routing
and reusing the existing invalid-boundary status avoids public enum churn while retaining a fixed
policy-rejected diagnostic for local callers.

The existing `RoutingPolicy` protocol has no deadline or cancellation parameter. This first
integration therefore makes no hard-preemption claim for in-process policy code. It confines work
to one registered bounded-input evaluation, observes cooperative cancellation on both sides of it,
and preserves all existing negotiated iteration, expansion, obstacle-check, physical-check, and
cancellation budgets. A model, external plugin, remote, or subprocess evaluator requires a later
isolated-worker contract with an enforceable resource/time budget; it is not silently admitted
through a profile name.

### Ordering and retry authority

If a policy is present and accepted, its `net_order` replaces only the initial `(net_id, seed)`
request order. It changes neither request contents nor seeds, grid, layer, penalties, candidate
geometry, validation, cancellation, or work budgets. The policy is not re-evaluated during a run.

After every non-terminal pass, the coordinator retains ADR-0055's deterministic retry order:
`(-conflict_score, net_id, seed)` from its own exact congestion ledger. A policy may not reorder
conflicted retries, choose a rip-up set, adjust penalties, or influence ties. Any future exception
needs a separate ADR, evidence against the deterministic baseline, a distinct policy
input/decision schema, and candidate-identity versioning.

When `policy_profile` is absent, the initial and retry behavior, the base
`NegotiatedRoutingResult` serialization/shape, budgets, and candidate identities remain
byte-for-byte compatible with the current coordinator. The deterministic reference profile is
opt-in, not an implicit change of default order.

### Provenance and candidate identity

The current negotiated-envelope digest remains the content address of the accepted immutable
`NegotiatedRoutingRequest`. A no-profile candidate retains the existing
`negotiated-congestion-v2` identity label and digest exactly. For an accepted profile decision,
the candidate uses the explicit `negotiated-congestion-policy-order-v3` identity label and its
re-identification digest is a versioned SHA-256 canonical object containing exactly:

```json
{
  "candidate_identity_policy": "negotiated-congestion-policy-order-v3",
  "negotiated_envelope_digest": "sha256:…",
  "policy_decision_digest": "sha256:…",
  "schema": "copper-mcp.negotiated-policy-binding.v1"
}
```

Every candidate in that run and the policy-enabled result's published evidence use this composite
digest. The policy-enabled run returns a separate `PolicyNegotiatedRoutingResult` extension/
subtype containing the profile ID, negotiated-envelope digest, policy-input digest,
policy-decision digest, and composite identity binding. The base `NegotiatedRoutingResult`
receives no new optional fields and is returned only for the no-profile path. A changed accepted
decision must therefore change candidate identity even when the envelope is equal; historic
no-policy candidates remain replayable and comparable.

The policy remains advisory: the deterministic router creates all copper; the ordinary candidate
and negotiated physical-clearance checks still gate publication; authoritative KiCad DRC and
separate explicit apply authorization remain downstream. Raw model output is never an accepted
decision or candidate: a later model adapter must decode it into the existing closed decision type
before this transaction and fail closed on decoding failure.

## Consequences and acceptance evidence

Implementation is accepted only when targeted tests demonstrate all of the following:

- **Compatibility:** ten or more no-profile replays produce the current base
  `NegotiatedRoutingResult` serialization/field shape, status, router call order, and v2
  candidate IDs byte-for-byte; default initial order remains `(net_id, seed)`.
- **Order scope:** an accepted reference/test policy changes the first router-call permutation to
  its `net_order`; a forced second iteration follows only the deterministic
  `(-conflict_score, net_id, seed)` ordering, and policy evaluation occurred exactly once.
- **Boundary failure:** unknown/malformed profile, failed registered adapter/policy, malformed
  output, changed ID, wrong input digest, foreign/repeated/missing net, and any window selection
  each yield the fixed fail-closed result with zero router calls, candidates, and connections; none
  falls back to no-profile routing.
- **Binding:** repeated equal profile/envelope/input/decision triples reproduce equal input,
  decision, and v3 composite digests; changing either the envelope or decision changes the
  composite candidate binding; an ordinary no-profile candidate retains its v2 identity.
- **Limits:** policy inputs respect the closed type's caps; cancellation before or immediately
  after policy evaluation publishes no proposal; all existing negotiated iteration, expansion,
  obstacle-check, and physical-clearance ceilings remain enforced.
- **Measured evaluation, not a safety substitution:** on license-reviewed held-out fixtures, report
  completion/routed-net fraction, overflow units, total wire length, iterations/rip-ups, router
  expansions/obstacle/physical checks, wall time, deterministic replay, and separate KiCad DRC
  outcomes against the no-policy deterministic baseline. Make no quality claim unless a
  predeclared asymmetric-fixture evaluation shows at least a 10% reduction in a named primary
  metric (router work units, iterations, or rip-ups), with no regression in completion, overflow,
  deterministic validation, or KiCad DRC outcomes. A policy is not physically valid or better
  merely because it returns an ordering.

## Alternatives considered

- **Let a policy control every iteration.** Rejected: it would replace congestion-feedback repair
  with an unmeasured advisory choice and obscure ADR-0055's deterministic retry authority.
- **Apply corridor/repair-window selections now.** Rejected: no coordinator-owned windows or
  search semantics are integrated in this slice. Supplying none makes their non-use explicit.
- **Pass Board IR or model-generated paths through the policy.** Rejected: this violates the
  closed advisory boundary and would let a model influence copper outside deterministic routing and
  validation.
- **Treat policy/model exceptions as a fallback to normal routing.** Rejected: a failure could
  become invisible and cause a misleading candidate provenance claim. Invalid policy input fails
  before any router call.
- **Accept arbitrary policy objects/factories or model endpoints.** Rejected: the protocol cannot
  impose a hard deadline or safe cancellation on arbitrary in-process code, and a caller-selected
  implementation defeats the closed boundary. A future captured policy requires a separately
  bounded isolated-worker adapter registered in the private profile allowlist.
