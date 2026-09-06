# ADR-0134: Freeze readiness requirements before measuring progress

- Status: Proposed
- Date: 2026-09-06
- Related: [ADR-0132](0132-supervised-optimization-keeps-evidence-and-consent-separate.md),
  [ADR-0133](0133-native-optimization-execution-and-host-confirmation.md)

## Decision

The balanced-readiness program covers ordinary analog/audio, MCU/sensor digital, and low-power
non-isolated supply profiles over 2/4/6/8 copper layers. RF, DDR/PCIe, mains and safety-critical
sign-off remain outside this profile set. Missing models or unsupported boards are not silent
successes, nor may they be excluded after a failed held-out attempt.

Freeze fifty requirement IDs: ten for each of Core/MCP Safety, Routing, Placement, Engineering
Judgement and AI/LIVE Autonomy. Each area has 40 capability points, 30 real-validation points,
20 integration/recovery points and 10 release-evidence points. The requirement catalog binds
the scope, weights, evidence-origin requirements, acceptance statements and critical gates into
one canonical digest. A changed catalog is a changed denominator, not progress.

Submitted receipts must name exact requirement, catalog and evaluated-source digests. They are
unique, bounded, closed and origin-matched; unit tests/test doubles cannot substitute for physical,
real-engine, hosted or independent-review evidence. Only a submitted pass contributes points.
Missing, inconclusive, not-run and failed evidence contributes no points. Every missing critical
gate blocks the result even if submitted points exceed ninety in every area.

The evaluator deliberately reports **submitted** points, not an audited maturity percentage.
It cannot authenticate a real invocation or human review from a digest in caller-authored JSON.
Even a perfect submission returns only eligibility for independent artifact review; audited
readiness remains null and release/apply authority remains absent. The next evidence-intake lane
must verify the actual referenced artifacts and their causal source/profile/executor bindings.
Do not add a bypass boolean that turns this preliminary check into a certificate.

## Interface and compatibility

`readiness-catalog/v1` and `readiness-submission/v1` are new internal/development contracts.
`scripts/check_readiness.py` prints the frozen catalog with no argument or checks a bounded
submission file. Exit zero means catalog export or eligibility for review, never release approval;
one means blocked and two means malformed. It does not modify a board, contact a provider, change
existing optimization/v1 semantics, or register new MCP tools.

## Validation

Tests pin all five denominators, scope/source bindings, unique receipts, evidence origins,
unknown-domain non-credit, the missing-native-guard critical gate, and absence of authority even
for a complete synthetic submission. These are contract tests, not observations of ninety-percent
product readiness. Physical validation and strict KiCad-side mutation remain separate gates.
