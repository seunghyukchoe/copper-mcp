# ADR-0128: A private surrogate ranking is bounded and never signs off

- Status: Accepted
- Date: 2026-08-30
- Owners: @seunghyukchoe
- Related: [Issue #91](https://github.com/seunghyukchoe/copper-mcp/issues/91),
  [ADR-0118](0118-authoritative-signoff-stays-closed-until-a-bounded-executor-exists.md),
  [ADR-0119](0119-a-signoff-claim-rests-on-repeated-agreement-from-a-registered-backend.md),
  [ADR-0127](0127-multi-pin-local-repair-replaces-one-proven-responsible-branch.md),
  [D-237](../ledgers/decision-ledger.md), [R-187](../ledgers/risk-register.md),
  [SEC-174](../ledgers/security-ledger.md), and issue #91

## Context

Issue #91 asks where SI, PI, thermal, and DFM surrogates may help candidate selection while
preserving the project invariant that AI proposes and deterministic code disposes. ADR-0118
keeps this seam private and non-claiming until a coordinator-owned authoritative executor and
fixed backend registry exist. ADR-0119 now supplies one such authority for DFM only: repeated
agreement over candidate-bound KiCad DRC evidence. SI, PI, and thermal still have no registered
authority.

The existing sign-off core already has a bounded SurrogateAdvisory and returns
surrogate_only when advisory data is supplied without authoritative evidence. The missing
agent-only slice is a deterministic ranking contract that can order already-produced route
candidates without receiving a model, runner, board bytes, standalone caller-supplied geometry,
or physics result. Each candidate necessarily carries the path geometry that its immutable
identity already commits to; the ranking seam consumes that geometry only for bounded structural
validation and identity verification. A direct import must not become a supported public surface
merely because Python can import a module by path.

## Decision

### Private entry point and input contract

The only implementation is the direct-import-only module
src/copper_mcp/routing/surrogate_ranking.py. It provides:

    rank_surrogate_candidates(
        candidates: tuple[RouteCandidate, ...],
        domain: SignoffDomain,
        *,
        cancelled: CancellationCheck | None = None,
        deadline: DeadlineCheck | Callable[[], object] | None = None,
    ) -> SurrogateRankingAccepted | SurrogateRankingRefused

The public routing package does not re-export this function or either result class. The signature
is closed: there are no model, weights, backend, prompt, runner, or settings injection arguments.
The domain is metadata only and accepts the existing SI, PI, thermal, and DFM enum values; every
domain produces an advisory-only result under this decision.

The candidate container must be an exact immutable tuple containing 1 through 32 exact
RouteCandidate values. There is no separate geometry argument beyond those already-produced
candidates. Before candidate identity hashing, the module counts every route-path vertex and
refuses above an aggregate 16,384-vertex ceiling. It reconstructs nested contract objects,
verifies every candidate's content identity, rejects duplicate candidate identities and requires
one common digest-shaped base revision. It accepts only exact non-negative integer cost/metric
values within the safe integer bound and requires:

    total_cost_nm = length_nm + bend_cost_nm + proximity_cost_nm + via_cost_nm

No candidate geometry or raw feature value is retained in the ranking accumulator.

### Deterministic advisory projection

The source-owned profile is schema copper-mcp/surrogate-ranking/v1, model identity
copper-mcp-deterministic-cost-surrogate, version 1, and feature order total_cost_nm. The fixed
integer projection is:

    score_milli = -min(1_000_000, ceil(total_cost_nm / 1_000))

Thus produced scores are in [-1,000,000, 0]. Entries sort by descending score and then ascending
candidate identity, so equal scores cannot depend on input order. Ranks are one-based and
contiguous. The profile caps the batch at 32 candidates and aggregate vertices at 16,384.

Each accepted result is closed and redacted. Its fields are schema, status, advisory_only,
domain, model_id, model_version, base_revision, comparison_digest, settings_digest, input_digest,
ranking_digest, and entries. Each entry contains only candidate_id/base_revision, rank/score_milli,
and a feature_digest. The comparison digest binds the shared context: base revision, net, ordered
endpoints, layer, width, pad count, ordering policy, fill binding, router version, policy, and all
nine A* settings. Seed, geometry, cost, and metrics remain free to vary because those are ranked.
The separate digest is required because the existing candidate ID does not include every setting.
The settings digest covers the profile and limits; the input digest covers sorted bindings and
comparison digest; each feature digest covers its binding and score; the ranking digest covers the
complete canonical result. These are identity and reproducibility metadata, not authoritative
evidence or an evidence_revision.

The focused golden vector pins candidate
sha256:e83b51d1d86117d31570c26445ec76bdff019084bdf3e42915490be0775e8538,
comparison sha256:dfd3d897543ffb2edde0de78b6bd3b1d21f0ae9037dfbaa4c1359732fce7033b,
settings sha256:cf3c0b030ef039c0a01396143115f516b3b384caeeb9f1b4cb985150715e9c56,
input sha256:b1d5447fcb691a1108665374357b810d0b5b733b29f4056388224e3d1bb76417,
feature sha256:e910df18de2926646d040edcb53ed2ecead6dbb8b7eb17ec9c3b6933a6f58ffe, and
ranking sha256:e4ad60ac2a81cba1325bbc9e132ee7e7b761df7ca1a31d42b0f7ed4a587e04c5.

### Refusals, stops, and authority

The refusal union has exactly these non-echoing codes:

- invalid_input — surrogate ranking input is invalid
- invalid_domain — surrogate ranking domain is unsupported
- invalid_candidate — surrogate ranking candidate is invalid
- duplicate_candidate — surrogate ranking candidates are not unique
- mixed_base_revision — surrogate ranking candidates use multiple base revisions
- incomparable_candidates — surrogate ranking candidates differ in comparison context
- vertex_budget_exceeded — surrogate ranking exceeded its vertex budget
- cancelled — surrogate ranking was cancelled
- deadline_exceeded — surrogate ranking exceeded its deadline

The refusal payload contains only schema, status, code, and its source-owned fixed diagnostic.
Cancellation is checked before any candidate work and has precedence over deadline. Both stops
are checked during reconstruction, identity/feature work, and final publication. Any callback
exception or non-boolean result becomes the corresponding typed refusal, and no partial entries
are published.

This module never runs DRC or any backend, constructs authoritative evidence, returns SIGNED_OFF,
or authorizes a board write. It accepts no board bytes or standalone geometry beyond each
already-produced candidate, exposes no geometry in its result, and accepts no prompts,
credentials, network process, subprocess, persistence, MCP, CLI, apply, or mutation path. DFM
authority remains the coordinator-owned repeated KiCad DRC executor from ADR-0119; SI, PI, and
thermal remain no-authoritative-backend non-claims. A surrogate ranking is reported to the
sign-off core as surrogate_only and cannot upgrade a claim, including on the registered DFM
domain.

## Consequences

- The coordinator has a deterministic, bounded seam for prioritizing immutable candidates while
  preserving exact validation and physical/DRC gates.
- The result is useful for ordering only. It proves no route quality, model accuracy, SI, PI,
  thermal, DFM, manufacturability, fabrication, hardware, or generalisation property.
- Direct import is an implementation/testing detail, not a supported API. No default routing
  result, public schema, MCP/CLI inventory, apply surface, backend registry, or sign-off authority
  changes.
- A future learned or domain-specific surrogate requires its own reviewed contract, calibration,
  privacy analysis, and authoritative validation; it may not replace this fixed non-claiming seam.
- Issue #91 remains open for authoritative SI/PI/thermal adapters, reviewed exposure, and human
  or external physics/DFM gates. B-141 is routing-completion evidence for #90 and is not physics
  evidence for this decision.

## Verification and evidence

tests/test_routing_surrogate_ranking.py contains 62 focused test cases covering deterministic
reordering, golden digests, comparison-context equality, score and tie rules, all four advisory domains, closed payloads,
redaction, immutability, exact candidate and vertex bounds, forged nested values, every refusal
family, cancellation/deadline precedence and atomicity, closed signature, static import
isolation, and absence of a public routing-root export.

The committed mutation specification
docs/mutants/2026-08-30-private-surrogate-ranking.json
contains 23 exact source mutations. The recorded mutation result is 23/23 killed with no
survivors. Its current file digest is
sha256:7b3f0cfed1e6612243306f0c5b69038567afd4d60806a02645127a602226db96. The source under test
is sha256:b88e4670ffe7fa3e44b99fe84d088cb3ffc1371eeb3e0bcc84055aed9afcb146, and the specification
must be re-anchored and re-run if the source changes; a mutation count without the committed
spec, exact command, interpreter, and report is not evidence under ADR-0098.

No test or mutation result here is authoritative SI, PI, thermal, or DFM execution. The private
module is a cooperative same-process boundary, not a hostile Python sandbox; operational trust
and process isolation remain required for hostile callers.

## Alternatives considered

- **Accept a caller-supplied model, runner, or backend:** rejected because the caller cannot
  establish authority or bind a result to a fixed execution.
- **Return raw features, geometry, or model output:** rejected because it leaks proprietary
  design data and creates an unbounded result contract.
- **Use a floating-point or caller-configurable score:** rejected because replay and digest
  identity would depend on environment or unreviewed policy.
- **Treat the score as SI, PI, thermal, DFM, or sign-off evidence:** rejected because ranking is
  not an authoritative domain result; DFM still requires repeated KiCad DRC evidence.
- **Export the hook through MCP or CLI:** rejected because public exposure would promise a
  capability before domain authority and human validation exist.
- **Publish partial rankings after cancellation or deadline:** rejected because downstream code
  could mistake an incomplete order for a complete candidate comparison.
