# ADR-0132: Supervised optimization keeps evidence, consent and application separate

- Status: Proposed; internal foundation only
- Date: 2026-09-05
- Owners: CopperMCP maintainers
- Related: [v0.13 staged plan](../plans/v0.13-supervised-optimization.md),
  [routing lifecycle](0043-durable-routing-job-ledger.md),
  [authoritative signoff](0119-a-signoff-claim-rests-on-repeated-agreement-from-a-registered-backend.md),
  [D-250](../ledgers/decision-ledger.md), [R-195](../ledgers/risk-register.md),
  [SEC-177](../ledgers/security-ledger.md)

## Context

Existing immutable routing jobs and legalizer-issued placement candidates need a coordinator,
not a replacement geometry engine. A superficially complete state machine can still approve
routes against the wrong placement, mistake a missing backend for a pass, or persist a private
request. Human approval does not establish electrical correctness or authorize board writes.

## Decision

Introduce a draft `optimization/v1` contract behind an internal-only package. Define the five
start/get/cancel/export/approve command shapes without registering or simulating working MCP
tools. Bind requests to source, Board IR, explicit movable/target scopes, frozen profiles,
allowed backends, exact-integer objectives/limits and mandatory human review. Keep all geometry
and requests ephemeral. Derive job identity from request and authenticated host-owner bindings;
the ID is not itself authorization.

Define pure immutable CAS transitions and cumulative work reservations. Terminate stale,
exhausted, unsupported, failed, interrupted and cancelled executions as non-success. No repair
resets budgets. A selected package binds routing to its resulting placement snapshot and judge
evidence to the final composed board plus rule context. ERC uses the bound electrical input.
Freeze the target denominator in the request rather than infer it from successful outputs.

Represent all seven judge domains explicitly. Require repeat agreement and suppress no missing
check into a pass. Unavailable SI/PI/thermal/EMC authorities remain inconclusive, including after
human review. All required domains must pass before review; any failure blocks it. This envelope
is not evidence authentication and introduces no engineering-signoff status.

Separate human-channel capability issuance from model-callable consumption. The internal
primitive is default-off, bounded, expiring, single-use, owner/revision/package/judge-bound and
process-local. It is a cooperative host primitive, not a human-verification UI or a sandbox
against privileged code. Transport authentication, transactional persistence, workers, verified
executors and separately authorized geometry export must land before public registration.

Keep application outside this coordinator. It neither imports nor calls either apply service,
and it cannot silently apply placement and then routes with stale authority. A release checklist
may reject missing evidence but cannot authenticate observations, authorize a tag or fabricate a
benchmark. Preserve current package version until the staged release actually passes its gates.

## Consequences

This is a first reviewable implementation, not completion of the v0.13 cycle. It provides
executable constraints for later integration and regression tests for unknown-domain honesty,
revision composition, strict JSON types, CAS, budgets and replay. It deliberately cannot prove
router performance, fidelity on 2–8-layer boards, actual backend availability, human-channel
integrity, durable crash recovery, or physical correctness.

The source baseline already tracks the OrcaRouter advisory provider, but local untracked files
are not visible from GitHub. Local WIP reconciliation remains a release condition. No empirical
benchmark or release-ready row is added. Independent review is still required.

## Allocation

ADR-0131 is a live claim in PR #265, so this branch takes 0132. D-248/D-249 and R-194 are live
claims in PRs #265/#266; use D-250/R-195. Main already contains SEC-176, so use SEC-177. Existing
Orca/placement sibling collisions are not resolved by reusing their numbers. Re-read and rebase
the registry before merging; the registry-line conflict is intentional protection.
