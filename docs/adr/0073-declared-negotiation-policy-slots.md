# ADR-0073: Declare negotiation strategy as three separately digest-bound policy slots

- **Status:** Accepted
- **Date:** 2026-08-06
- **Related:** [Issue #62](https://github.com/seunghyukchoe/copper-mcp/issues/62),
  [ADR-0055](0055-bounded-negotiated-congestion.md),
  [ADR-0064](0064-policy-bound-initial-negotiated-order.md),
  [ADR-0066](0066-atomic-route-bundle-preview.md),
  [Negotiated congestion research](../research/negotiated-congestion-v1.md),
  [Multi-pin routing references](../research/multi-pin-routing-references.md),
  D-148, B-087

## Context

ADR-0055 gave the coordinator one negotiation strategy, fused into the loop: nets are handed to the
router in stable `(net_id, seed)` order and then in conflict-score order; history accumulates the
integer overuse count; every net is ripped up on every pass. None of those three choices is named,
none is separable, and none is bound into anything a reader can compare across two runs. Two
coordinator runs that negotiated in materially different ways are indistinguishable from their
published evidence.

ADR-0064 bound a closed policy decision to the *initial* net order and drew the line explicitly:
"A policy may not reorder conflicted retries, choose a rip-up set, adjust penalties, or influence
ties. Any future exception needs a separate ADR, evidence against the deterministic baseline, a
distinct policy input/decision schema, and candidate-identity versioning." This is that separate
ADR.

The literature survey is [negotiated-congestion-v1](../research/negotiated-congestion-v1.md). Three
findings shape this decision rather than decorate it:

- **The rule normally attributed to PathFinder is VPR's.** McMurchie and Ebeling publish
  `c_n = (b_n + h_n) × p_n` and describe both non-base terms qualitatively only — h_n "is increased
  slightly", p_n is "initialized to one" then "gradually increased". The additive-overuse form is
  VPR's `acc_cost`. A slot literal is a provenance claim, so the default is named
  `accumulated-overuse-v1`, not anything containing "pathfinder".
- **Unbounded history is a published failure mode.** BoxRouter 2.0 reports that a monotonically
  growing history term eventually dominates the present term, so "a presently congested edge becomes
  cheaper to pass through than a previously congested edge", and "the solution quality may get worse
  with more iterations". PathFinder's Theorem 1 is the complement: bounded history (`h_n ≤ d_n`)
  gives a delay bound its authors concede is given up on congested circuits. The coordinator's
  existing `_MAX_HISTORY` and `_MAX_PENALTY_NM` caps stop being arbitrary safety limits and become
  the thing this evidence argues for.
- **Conflicted-only rip-up is PathFinder's own enhancement, not a shortcut.** Section 3.5: "To date
  we have not seen any cases where routing only congested nodes resulted in a lower-quality route.
  In our experience the number of iterations increases, but the total running time decreases." VPR,
  CUGR, Dr. CU, and TritonRoute all ship it. The common "full rip-up trades speed for quality"
  framing is not supported by the original authors.

## Decision

### Three closed slots, three digests, one plan

`copper_mcp.routing.negotiation_plan` declares three immutable slots. Each is a fixed enumeration
member plus bounded integers, each publishes **its own** content digest, and a `NegotiationPlan`
composes exactly those three digests — nothing else — into a plan digest.

| Slot | Literals | Bounded weights |
|---|---|---|
| net order | `conflict-descending-v1` *(default)*, `stable-identifier-v1`, `demand-descending-v1`, `demand-ascending-v1` | none |
| cost update | `accumulated-overuse-v1` *(default)*, `scaled-accumulation-v1`, `saturating-decay-v1` | `accumulation_weight`, `decay_numerator`/`decay_denominator`, `present_growth_numerator`/`present_growth_denominator`, each `0..1024` |
| rip-up | `all-nets-v1` *(default)*, `conflicted-only-v1`, `top-conflict-only-v1` | `max_ripup_nets`, `1..32` |

Each default is chosen because it is what ADR-0055 already does and B-036 already measured — not
because the literature prefers it. Where the literature mildly prefers something else, as it does
for rip-up, the default stays put until this repository has its own evidence.

The present-growth ratio lives in the cost-update slot rather than a fourth slot, because a
negotiation iteration moves the history term and the present factor as one update; separating them
would let two digests describe one behavior.

Three rules govern what a slot may contain:

1. **Closed literals only.** A rule is an enumeration member. A caller cannot supply a callable, a
   formula, a module path, or an expression. A future selector — learned or otherwise — can pick
   among these literals and weights; it cannot author a rule body, because there is nowhere to put
   one. This is what makes "learning influences ordering, batching, and weights only" a structural
   property rather than a convention.
2. **No inert parameter may vary a digest.** A weight a rule does not read is pinned to its neutral
   value by validation. `accumulated-overuse-v1` rejects a non-unit accumulation weight; only
   `saturating-decay-v1` accepts a decay ratio; only `top-conflict-only-v1` accepts a rip-up
   ceiling. Two plans therefore have different digests only when they can behave differently.
3. **Monotone pressure.** A decay ratio above 1 would amplify history rather than fade it, and a
   present-growth ratio below 1 would weaken present pressure between iterations. Both are refused:
   they invert the direction the negotiation argument depends on.

Every rule is exact integer arithmetic. `saturating-decay-v1` computes
`(previous × numerator) // denominator + weight × overuse`, and present growth is
`(previous × numerator) // denominator`. Integer floor division means a zero penalty stays zero and
a small penalty can stall below the growth ratio's resolution; that is preferred to admitting a
float into a cost that ADR-0006 requires to be an exact integer.

### Nothing here touches the path search

A slot decides which nets are handed to the router, in what order, how the coordinator's integer
congestion counters move between passes, and which nets are re-routed. The A* expansion, its cost
function, its obstacle predicate, its budgets, and the geometry it emits are untouched. The router
still receives the same `congestion_penalty` callback ADR-0055 defined, which remains an internal
integer search-ordering term that never changes a candidate's physical `RouteCost`.

Net-order demand is the exact Manhattan pad separation in whole lattice cells, derived by the
coordinator from the verified snapshot — the same bounded pre-routing feature ADR-0064's closed
policy input already carries. A caller never supplies it.

### Opt-in, with the no-plan path preserved byte for byte

`negotiate_routes` gains one optional `plan` argument. Absent a plan, the coordinator keeps its
existing code path, ordering, accounting, result shape, and `negotiated-congestion-v2` candidate
identities exactly. The declared plan whose slots are all defaults is *behaviorally* equivalent to
that path and is exported as `LEGACY_EQUIVALENT_PLAN`, but it is not the coordinator's default: a
run that declared a plan is a different run, and says so.

A plan and a `policy_profile` **cannot be declared together**; supplying both is `INVALID_REQUEST`.
Composing ADR-0064's learned initial order with declared slots needs its own evidence shape and its
own measurement, and refusing is the honest answer until that decision exists.

### Candidate identity and evidence

An accepted plan re-identifies every published candidate under the label
`negotiated-congestion-plan-v4`, bound to a versioned SHA-256 canonical object containing exactly:

```json
{
  "candidate_identity_policy": "negotiated-congestion-plan-v4",
  "negotiated_envelope_digest": "sha256:…",
  "negotiation_plan_digest": "sha256:…",
  "schema": "copper-mcp.negotiation-plan-binding.v1"
}
```

The run returns a `PlanNegotiatedRoutingResult` carrying `NegotiationPlanEvidence`: the envelope
digest, the plan digest, all three slot digests individually, and the composite binding. The three
slot digests are published separately so a reader can see *which* slot moved between two runs.
The evidence re-derives the plan digest from exactly those three slot digests and refuses itself
when they do not compose to it, so a published plan digest can never name a slot combination other
than the one it reports. As with ADR-0064's subtype, the base `NegotiatedRoutingResult` gains no
optional field.

### The clearance gate now attributes what it refused

A partial rip-up rule cannot work against a gate that refuses anonymously. A lattice overflow means
two routes share a vertex or edge, which means zero separation, which always fails
`verify_negotiated_physical_clearance` — so before this slice, every congested iteration was
discarded wholesale and `conflicted-only-v1` would have had nothing to retain, making the slot
vacuous.

`PhysicalClearanceVerificationResult` therefore gains `violating_nets`: the first offending pair,
sorted, and **only** for `CLEARANCE_VIOLATION`. Net IDs are already published in `unrouted_nets`, so
this discloses nothing new, and no coordinate, width, or clearance value leaves the gate. A
`BUDGET_EXHAUSTED` or `INVALID_CANDIDATE` verdict attributes nothing, and under a declared plan a
refusal that blames no pair in particular blames the whole allocation: retention is emptied and the
next pass rips up everything.

Retention is internal negotiation state decided before the publication reset. Publication is
unchanged: a candidate set is published only from a pass the gate accepted, and a retained
candidate is re-added to the ledger and re-checked by the gate on every subsequent pass.

### Rip-up accounting

Under a declared plan, `ripups` counts the nets actually selected for re-routing that held a
candidate or connection. The no-plan path keeps its historic `len(best_candidates)` accounting
byte for byte. The two genuinely differ, and B-087 shows why: on the congested fixture the no-plan
run reports `0` rip-ups across five iterations, because `best_candidates` stays empty while every
intermediate pass is refused by the clearance gate. The plan-mode number is the accurate one; the
legacy number is preserved because changing it would move published bytes.

### Bounded work without the incremental spatial index

Issue #64's incremental spatial index is **not** built here, and this slice does not wait for it.
The consequence is recorded rather than hidden: every retained candidate is re-added to the
congestion ledger from scratch at the start of each pass, so ledger reconstruction is linear in the
retained unit-resource count per iteration rather than incremental, and the router's obstacle
queries still go through the ADR-0051 conservative index unchanged. `conflicted-only-v1` reduces
*router calls*, which is where the work is; it does not reduce ledger reconstruction. Existing
per-run expansion, obstacle-check, physical-check, iteration, and resource ceilings all still bound
the coordinator, so behavior stays bounded without the index — it is a constant-factor cost on
fixtures of the size this repository measures, and an untested one at scale.

A pass whose rip-up rule selects no net cannot make progress, so the coordinator stops instead of
looping to the iteration ceiling.

## Consequences and acceptance evidence

`tests/test_routing_negotiation_plan.py` demonstrates all of the following:

- **Determinism:** a declared plan replayed twice yields identical canonical candidate bytes,
  status, iterations, rip-ups, wire length, and evidence.
- **Compatibility:** `LEGACY_EQUIVALENT_PLAN` reproduces the no-plan run's geometry and wire length
  exactly, under a deliberately different `negotiated-congestion-plan-v4` identity; the no-plan
  path keeps its `negotiated-congestion-v2` identity, and the existing suites — including
  `test_golden_identities.py` — pass unchanged.
- **Slot-digest binding:** each slot's digest is distinct; changing any one slot changes the plan
  digest, the composite binding, and every published candidate identity, while the envelope digest
  is unchanged, so only the plan can be what moved the identity.
- **No inert parameter:** every weight a rule does not read is refused, as are an amplifying decay
  ratio, a weakening present-growth ratio, out-of-range weights, and a non-literal rule.
- **Refusals:** a non-plan object, a plan combined with a policy profile, an exhausted work budget,
  and a backend whose proposal the reference core does not reproduce each fail closed with a fixed
  redacted diagnostic and publish nothing.
- **Attribution:** the gate's violating pair is sorted, exactly two, distinct, and only present for
  a clearance violation; an unattributed refusal retains nothing for the next pass.
- **Metamorphic:** adding a foreign-net obstacle never decreases total wire length under any
  declared plan or under no plan — the honest alternative to a longer route is no route, never a
  shorter one. A companion test proves the planted obstacle is not inert.

**Mutation check.** Five load-bearing guards were mutated one at a time. Four were caught
immediately: the rip-up rule always retrying a net with nothing retained, the plan digest composing
from its own slot digests, candidate identity binding the plan composite rather than the envelope,
and the no-inert-parameter rule. The fifth — an unattributed clearance refusal retaining nothing —
**survived**, revealing that no test produced an unattributed refusal. A test was added, and the
mutant is now caught.

**Measured, and claiming nothing.** B-087 sweeps eleven declared plans across three fixtures. It
records genuine wins (shortest-demand-first completes the congested fixture in one pass instead of
five, with 80% fewer router calls and 4% less wire; a VPR-shaped present-growth ratio converges in
three passes instead of five), genuine losses (`conflicted-only-v1`, `top-conflict-only-v1`,
`saturating-decay-v1`, `stable-identifier-v1`, and `demand-descending-v1` all fail to converge
within eight iterations where the default converges in five; `scaled-accumulation-4` converges but
25% longer), and classifies the whole sweep as **exploratory with no quality claim**: it was not
predeclared, the fixtures are small and synthetic, and there is no held-out corpus. The
literature's own calibration — Qu et al. measured 1.95% relative deviation in rule violations but
0.008% in wirelength across 300 random net orders — says a 29% wirelength swing from ordering alone
is evidence about a small fixture, not about routing.

Nothing here claims KiCad DRC, electrical, multilayer, fabrication, apply, or general-board
validity. Via counts are recorded as structurally zero because the coordinator is single-layer by
contract. No MCP surface, job, or public contract changes.

## Alternatives considered

- **Keep one fused strategy and add a "negotiation mode" enum.** Rejected: a single enum cannot
  express that two runs differ only in rip-up selection, and a reader could not tell which choice
  moved a result. Three digests is the whole point.
- **Give the present-growth ratio its own fourth slot.** Rejected: it is half of one iteration
  update. Two digests describing one behavior is precisely the ambiguity this contract removes.
- **Let a policy return a plan.** Rejected here, deliberately. ADR-0064 requires evidence against
  the deterministic baseline before widening policy authority, and B-087 supplies no such evidence.
  A plan and a policy profile are mutually exclusive until a later decision measures the
  composition.
- **Make `conflicted-only-v1` the default because VPR and PathFinder §3.5 favour it.** Rejected: on
  this repository's only congested fixture it does not converge. A default follows measurement
  here, not practice elsewhere.
- **Let history decay without a cap, or grow the present factor without one.** Rejected on
  published evidence: BoxRouter 2.0's instability result and PathFinder's Theorem 1 both argue for
  bounds, and exact integer costs need them anyway.
- **Wait for the incremental spatial index (#64).** Rejected: the slots are bounded and correct
  without it, and the performance consequence is small enough to state honestly at this fixture
  size. Coupling a contract decision to an unrelated performance change would delay both.
- **Report the clearance gate's violating pair as geometry.** Rejected: the pair of net IDs is
  already public; a coordinate or clearance value is not, and attribution does not need one.
