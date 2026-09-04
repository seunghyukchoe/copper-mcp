# OrcaRouter merge readiness

Status: **experimental and merge-blocked until the open gates below are closed**.

This checklist is the branch handoff for the optional OrcaRouter integration. Its safe stopping
point is a reviewable, opt-in advisory provider that can merge without changing CopperMCP's
deterministic routing, validation, DRC, MCP, CLI, or apply authority. It is not a production
readiness claim, an OrcaRouter endorsement, or evidence that a remote model improves routing.

## Gate status

| Gate | Status | Required evidence |
|---|---|---|
| Contract boundary | Pass locally | The direct-import-only adapter sends aliases and bounded scalar features, rebinds output to immutable local options, and remains outside the MCP/CLI surface, closed policy registry, geometry, DRC, and apply paths. |
| Offline security and correctness | Pass locally | The focused contract suite and repository checks pass without network access. The exact committed `make check` remains the merge gate because the new files must be tracked in the review commit. |
| Controlled live smoke | **Open** | A partner-provided test key or approved account must execute synthetic input only. Record model/configuration, status class, latency, request ID if available, and refusal behavior; never record prompts, responses, keys, or board data. |
| Documentation | Pass after branch review | README and this guide must state opt-in, experimental, non-production status; setup and disablement; the redacted boundary; upstream data handling; limitations; and the absence of route/DRC/apply authority. |
| Commercial integrity | **Open** | Obtain written terms for any usage-based compensation and permission to disclose it publicly. Until then, do not publish an exact percentage, attribution link, eligibility claim, or payout expectation. |
| Repository and review | **Open** | Track every intended file, run `make check` from a clean exact commit, pass hosted CI/security checks, resolve review conversations, and obtain direct maintainer approval. |
| Merge posture | **Held** | Merge only the optional experimental code and documentation. Keep `CHANGELOG.md` under `Unreleased`; do not bump the version, tag, release, or call the integration production-ready. |

## Local evidence snapshot

As of 2026-09-04 on `codex/orcarouter-integration`, the focused OrcaRouter suite passes **24
tests**, and the full `make test` run passes **4155 tests with 4 skips and 34 warnings**. Ruff,
mypy, secret/dependency checks, package building, schema, DRC-comparability, CI-budget, audio,
Circuit Intent, documentation-link, ledger, ADR-numbering, and diff-whitespace checks pass.
`make lint` reaches the source-distribution tracking gate and
stops because the five intended integration files are still untracked; therefore an exact clean
committed `make check` has deliberately not been claimed. No `ORCA_KEY` was present in this
checkout, so no live provider request was attempted.

## Controlled live smoke protocol

Run this only after the offline gate is green and an operator approves the provider key and data
handling. Use a synthetic `RoutingPolicyInput` fixture, an explicit model when repeatability is
needed, and the existing non-streaming call path. Do not use a customer or proprietary board.

The smoke record may contain:

- date, branch/commit, adapter policy ID, requested model, and configuration profile;
- HTTP status class, bounded elapsed time, refusal code, and response request ID if exposed; and
- whether the returned aliases passed the local decoder and `evaluate_policy`.

It must not contain the API key, Authorization header, prompt, tool arguments, model response,
board revision, raw net/candidate identities, coordinates, geometry, or filesystem path.

The smoke must demonstrate one successful closed tool-call response and one controlled refusal or
upstream failure where the provider makes that reproducible. A missing test account, unavailable
quota, or unapproved data-handling decision leaves this gate open; it does not justify a simulated
pass.

## Commercial and public-disclosure gate

The repository treats the reported 5% usage arrangement as a private claim requiring written
confirmation. Before publishing the exact number, confirm:

- whether the percentage is based on gross or net eligible usage;
- which models, plans, providers, and payment methods qualify;
- attribution and duration, refunds, chargebacks, and account termination;
- payout currency, timing, minimums, tax documentation, and audit/reconciliation access; and
- permission to name the arrangement and percentage in a public README.

Until those terms are approved, the README and guide may say only that the maintainer may receive
compensation from eligible usage under a separate arrangement. That disclosure must not imply that
CopperMCP requires OrcaRouter, that OrcaRouter improves correctness, or that users should select it
solely to support the maintainer.

## Deferred after the safe stopping point

These are deliberately not merge prerequisites for this experimental slice:

- fallback chains, adaptive model selection, or hidden multi-request billing;
- resolved-model or cost provenance as a new public CopperMCP contract;
- remote production workers or hard-preemption guarantees;
- inbound OrcaRouter Firewall MCP deployment;
- model-generated route geometry or apply authority; and
- routing-quality, DRC, electrical, manufacturing, or production-reliability claims.

Each future item needs its own contract, threat model, evidence, and review. The current external
provider risk remains open after merge, and the absence of a live smoke or commercial agreement is
not silently converted into a positive claim.

## Final handoff condition

When the local and review gates pass, this document should be enough for the maintainer to return
to CopperMCP core work. The remaining follow-ups must be recorded as explicit issues or ledger
entries, not left as implied production work. The opt-in adapter remains removable, non-authoritative,
and disabled whenever `ORCA_KEY` is absent.
