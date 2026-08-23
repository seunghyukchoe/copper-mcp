# ADR-0119: A sign-off claim rests on repeated agreement from a registered backend

- **Status:** Accepted
- **Date:** 2026-08-23
- **Owners:** `@seunghyukchoe`
- **Related:** [D-223](../ledgers/decision-ledger.md), [R-174](../ledgers/risk-register.md),
  [SEC-161](../ledgers/security-ledger.md),
  [ADR-0004](0004-authoritative-kicad-drc.md),
  [ADR-0052](0052-in-toto-candidate-drc-statement.md),
  [ADR-0109](0109-a-drc-count-carries-the-comparability-it-was-taken-with.md),
  [ADR-0118](0118-authoritative-signoff-stays-closed-until-a-bounded-executor-exists.md),
  [issue #91](https://github.com/seunghyukchoe/copper-mcp/issues/91)

## Context

[ADR-0118](0118-authoritative-signoff-stays-closed-until-a-bounded-executor-exists.md) left
`SIGNED_OFF` unreachable and named exactly what would lift the deferral: a **coordinator-owned,
bounded, fixed-profile, content-digest-bound** executor, independently tested. It also named the
three shapes it had refused and would keep refusing — a caller-supplied runner, a surrogate
ranking, and a positive field with no execution behind it.

Nothing about that has changed. What has changed is that one of the four domains now has an
authority in this repository that can answer its question, and it has had one since ADR-0004.
KiCad's DRC *is* this project's design-rule authority, `run_route_candidate_drc` already replays
one immutable candidate against it, and [ADR-0052](0052-in-toto-candidate-drc-statement.md)
already projects the result as a redacted, deterministic in-toto Statement bound to the candidate
ID, the candidate's base revision, the source revision, and the patched board and DRC-context
revisions. A DFM question about a candidate is the question DRC answers; SI, PI and thermal have
no such adapter and no path to one in this slice.

There is a second fact that had to be settled before a claim could be built on a DRC result, and
it is this project's own measurement rather than a general caution.
[ADR-0109](0109-a-drc-count-carries-the-comparability-it-was-taken-with.md) records that `B-107`
ran the corpus runner twice **at the same commit over byte-identical boards** and nine boards'
records still differed — every one of them only in the `drc` section. `error_count` moved 936 to
941; a whole violation type appeared in one run and not the other. `B-108` corroborated the
mechanism: the counts saturate near per-rule caps and which rules fill them varies. So a KiCad
DRC count is not a function of the bytes it was taken over, and one invocation is an
**observation**, not a measurement.

ADR-0109 drew the consequence for what a benchmark artifact may *publish*. A sign-off claim is
the same question asked at higher stakes: it is the strongest thing this system can say about a
candidate, and it would be saying it on the back of exactly the number that was measured to move.

## Decision

**`SIGNED_OFF` becomes reachable for `dfm` only, and only through three closed gates.** None is
configurable through supported request/public-API intake or a caller-selected authority.

**1. A fixed backend registry.** `_REGISTERED_BACKENDS` is a module constant mapping one
`(backend ID, backend version)` pair to the domains it may speak for. It registers
`copper-mcp-authoritative-v1` / `1` for `dfm` and nothing else. There is deliberately **no
registration function**: a `register()` call would be precisely the seam ADR-0118 refused, and a
mutable registry is a caller-supplied backend with an extra step. Adding a domain is a source
change reviewed together with the adapter that earns it. `si`, `pi` and `thermal` remain
unregistered and can produce only a non-claim.

**2. A module-private evidence capability.** `AuthoritativeEvidence` refuses to construct without
a module-private sentinel, closing the supported public/intake/construction paths. This is a
cooperative internal-misuse guard, not an in-process security sandbox: a privileged Python caller
able to import or monkeypatch private symbols can bypass it. Serialized evidence remains outside
intake, and the supported untrusted-input/API boundary remains closed; process isolation and
operational trust are required for hostile same-process callers.

**3. Repeated agreement, carried on the evidence.** ADR-0109's literal moves from the benchmark
projection onto the evidence envelope itself, unchanged in meaning:
`single_invocation` / `repeated_agreement` / `repeated_disagreement`, with the repetition count
beside it. **A claim requires `repeated_agreement` over N ≥ 2 invocations.** Disagreement is a
**refusal** (`uncomparable_evidence`), never a quietly weaker claim, because a caller who is told
"signed off" cannot see how much was behind it.

The literal is derived over the **whole in-toto Statement's canonical bytes** rather than over the
DRC section alone. That is the strictest faithful form of ADR-0109's rule, not a second opinion
about it: ADR-0052 put no timestamp, no path and no run label in that Statement, so there is
nothing in those bytes that ADR-0109 would have had to strip, and comparing them covers every
count ADR-0109 compares *plus* the four revisions it does not — so a patched board or DRC-context
revision that moved between runs cannot be laundered into agreement by summaries that happened to
match. `comparability_of` itself is deliberately **not** imported: it lives in the benchmark
package, and a repository invariant already forbids production code from importing that package
at all. A test pins the two vocabularies identical, and a second test pins the absence of any
incomparable field in the Statement, so neither the literals nor the soundness of the byte
comparison can drift without a failure.

**The executor owns its invocation.** `execute_dfm_signoff` names its runner as an import, decides
how many times to run it (2–8, defaulting to 2), and replays the *same* immutable candidate
against the *same* board bytes at one commit — which is how ADR-0109's "byte-identical inputs"
precondition, a thing that module can assert but not check, is satisfied here by construction
rather than by a caller's promise. It checks cancellation and a deadline **before every**
invocation, so a stop ends the sequence instead of being reported after it.

**Two further conditions on the verdict itself.**

- `evidence_revision` is the content address of the exact in-toto Statement bytes — the artefact
  the claim is *about*, not a second encoding of it and not a caller-chosen run label.
- **A run that skipped checks cannot sign off, even when it passed.** Non-zero
  `ignored_check_count` or `exclusion_count` produces `suppressed_evidence`. A run that did not
  evaluate a check cannot report that the check would have passed, and the caller cannot tell
  from the outside which checks were dropped.

**What is unchanged, and deliberately so.** The seam is still not exported through MCP or CLI.
`backend` remains a rejection slot that is never inspected for a callable and never invoked — the
way to keep refusing caller-supplied runners is to keep refusing the argument. A surrogate still
contributes bounded ranking data and never an approval, on a registered domain exactly as on an
unregistered one. Backend failure is a redacted refusal carrying no exception text. And a
sign-off is **not** an authorization to write copper: `apply_candidate` and
`apply_placement_candidate` remain separately gated and are untouched by anything here.

## Consequences

- `dfm` sign-off now means one specific, checkable thing: *KiCad DRC was run N ≥ 2 times over this
  exact candidate on this exact revision, agreed with itself, skipped nothing, and found no
  errors and no unconnected items.* That is worth having and it is narrower than "manufacturable";
  `R-174` carries the gap between the two.
- A claim costs a full DRC run per repetition. That is the price of the property, and the default
  is the cheapest count that can disagree.
- SI, PI and thermal are now visibly distinguishable from "unimplemented": a registered domain
  with no evidence answers `no_authoritative_evidence`, an unregistered one answers
  `no_authoritative_backend`. Issue #91 stays open on the three that have no authority.
- The comparability literal has its first non-benchmark consumer. It was always a statement about
  what a count can support, and a claim is the strongest use of one in this system.

## Alternatives considered

- **Claim on a single invocation.** Rejected on this project's own measurement: `B-107` found
  byte-identical inputs producing different counts at one commit. A single-invocation claim would
  be a promise the number cannot keep.
- **Publish a weaker claim on disagreement.** Rejected. A consumer of `SIGNED_OFF` cannot see the
  comparability that produced it unless refusing is the alternative, and a status whose strength
  varies silently is worse than one that sometimes says no.
- **A numeric tolerance on the counts.** Rejected for ADR-0109's reason, unchanged: a tolerance is
  a constant fitted to the oracle, and it would need refitting whenever KiCad's caps moved — the
  property that made the counts unusable to begin with.
- **A runtime `register_backend()` call.** Rejected. It is a caller-supplied backend with
  ceremony, and it would be reachable from a test, a plugin, or eventually a request.
- **Register `si`/`pi`/`thermal` against the same backend.** Rejected. DRC does not answer those
  questions, and pointing the executor at them would be a naming change dressed as a capability.
- **Let a passing run with exclusions sign off.** Rejected. An exclusion is a suppressed
  violation; a claim resting on one would be reporting the operator's own suppression back to
  them as an approval.
- **Expose the seam through MCP now.** Rejected, unchanged from ADR-0118: transport exposure would
  make a public promise before there is more than one domain behind it.

## Verification

`tests/test_routing_authoritative_signoff.py` covers the registry (only `dfm`; the read hands back
a frozen set), the capability (evidence cannot be constructed without it; only two modules name
it), the claim path and exactly what a claim carries and never carries, candidate/revision/digest
binding, single-invocation and disagreement refusals, the comparability/repetition-count
consistency rule, suppression beating a pass, serialized evidence refused as intake, the
registered-domain-without-evidence non-claim, surrogate non-claim on a registered domain, a
deadline that trips while evidence is being read, and the private-capability regressions that a
claim cannot be minted for an unregistered domain or from one invocation.

`tests/test_authoritative_signoff_executor.py` covers agreement derivation over the Statement
bytes — including the case where matching summaries hide a moved revision — the equivalence of
this seam's literals with ADR-0109's and the absence of any incomparable field in the Statement,
suppression, stop checks gating the loop, repetition bounds, the non-candidate refusal, a
malformed advisory graded before a DRC run is spent on it, a well-formed one recorded without
becoming a claim, and backend failure as a redacted refusal with a pinned payload. **No fake
backend is installed anywhere in either file**, by design: a test that monkeypatched a passing DRC
runner would be exercising the one path this design exists to make impossible. The end-to-end test
runs the real `kicad-cli` twice and is skipped where it is not installed; it asserts the *shape*
of whichever verdict comes back rather than demanding a pass, so it stays a test of the executor
rather than a claim about the fixture.

No test is presented as authoritative SI, PI or thermal execution. Those domains remain deferred.
