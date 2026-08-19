# ADR-0120: Withheld apply authority has one closed, non-echoing reason

- **Status:** Accepted
- **Date:** 2026-08-24
- **Related:** [ADR-0025](0025-file-level-candidate-apply.md),
  [ADR-0059](0059-separately-authorized-placement-apply.md),
  [ADR-0103](0103-a-candidate-records-the-model-that-produced-it.md),
  [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md),
  [R-149](../ledgers/risk-register.md)

## Context

Route and placement preview responses can deliberately omit an apply token. Before this decision,
the same `null` represented a request that did not ask, disabled operator authority, absence of a
candidate, an unapplicable board, a fill-bound candidate, a no-op placement, a replay refusal, or a
surface that never mints tokens. A caller could not distinguish a retryable configuration from a
permanent capability boundary. One placement replay exception was swallowed and became the same
silent `null`.

The reason is public output. It therefore cannot quote a path, net, coordinate, reference,
exception, object count, or any other board-derived value. The response contract must also prevent
ambiguous documents that contain both a token and a refusal reason, or neither.

## Decision

Every route, layered-route, placement, and corresponding live preview response carries exactly one
of `apply_token` and `apply_token_withheld_reason`. The reason is one of eight fixed literals from a
single leaf module: `unsupported_surface`, `not_requested`, `apply_disabled`, `no_candidate`,
`no_move`, `board_not_appliable`, `fill_bound_candidate`, or `replay_refused`.

The shared decision function defines precedence. A surface that can never mint reports
`unsupported_surface`; otherwise request intent precedes server authority, proposal state, board
appliability, fill binding, and replay acceptance. The implementation may skip expensive checks
once an earlier reason decides the result. Internal result objects and public MCP output models
both enforce the token/reason exclusive-or relation.

Because the closed output models reject documents without the new field, route and layered-route
preview responses move from `1.0` to `1.1`, and placement preview responses move from `0.1.0` to
`0.2.0`. Placement candidates stay `0.1.0`; candidate identity and apply binding do not move. Old
preview documents are not rewritten or relabelled. Callers that need the new reason re-run the
preview under the new contract, as described in the
[migration note](../migrations/preview-apply-token-reasons.md).

Reasons are diagnostic output only. They grant no apply authority, do not make a token reusable,
do not weaken revision or candidate binding, and do not add a live mutation path.

## Consequences

- An agent can decide whether changing request or operator configuration can produce a token.
- A swallowed placement replay refusal becomes a typed, fixed, non-echoing result.
- Existing consumers must move to the declared preview response versions and accept the closed
  vocabulary; old stored responses remain old-version evidence.
- Live and file-backed surfaces that never mint tokens state that permanent boundary explicitly.
- The vocabulary can grow only through another public-contract decision and its tests.

## Alternatives considered

- **Return a free-form message:** rejected because it would drift and could disclose board content.
- **Infer the reason from status and settings:** rejected because replay, fill, and board
  appliability are not fully represented by those fields.
- **Add the field only to token-minting surfaces:** rejected because a permanent unsupported
  surface is one of the distinctions callers need.
- **Keep the internal invariant without validating MCP output:** rejected because the published
  schema must refuse forged or drifted token/reason combinations itself.

## Verification

Focused tests construct all eight reasons across six preview surfaces, prove the vocabulary is
defined once, scan every produced reason against the fixed closed set, pin the new version literal
on every surface, inspect generated JSON Schema for required token/reason keys and the exact
eight-value enum, and reject missing, unknown, or ambiguous combinations at internal and MCP
boundaries. The ADR-0098 mutation spec
`docs/mutants/2026-08-20-withheld-apply-token-reasons.json` kills 15 of 15 mutants, including the
three public-contract exclusive-or gates; its SHA-256 is
`8c860d0f2f0591b6728e6cedb6a74c3641088ab116eba03d93f9433d14eda8e0`.
