# ADR-0118: Keep authoritative signoff closed until a bounded executor exists

- **Status:** Accepted
- **Date:** 2026-08-17
- **Related:** [ADR-0112](0112-external-route-candidates-enter-through-a-disposer.md),
  [ADR-0114](0114-external-candidates-continue-to-private-kicad-drc.md),
  [ADR-0115](0115-external-route-verification-is-a-versioned-read-only-mcp-boundary.md),
  [issue #91](https://github.com/seunghyukchoe/copper-mcp/issues/91)

## Context

Issue #91 needs one evidence vocabulary for SI, PI, thermal and DFM evaluation without making a
surrogate, model, or caller-supplied result an approval authority. The existing candidate and
external-evidence seams provide the useful vocabulary: a result is meaningful only when it is
bound to one immutable candidate and Board revision, and evidence has a content digest that can
be compared with the authoritative output that produced it.

This slice has no coordinator-owned bounded authoritative executor or registered production
backend for those domains. A callable selected by a caller, a surrogate ranking result, or a
backend exception cannot establish authority. Exposing a positive status now would therefore
make a nominal field look like a physical, electrical, thermal, or fabrication approval.

## Decision

Keep the authoritative-signoff seam private and closed. Its result vocabulary may represent a
typed refusal or an explicit non-claim, and may carry only redacted, bounded identity and evidence
metadata. A positive claim, when the future executor exists, must be bound to exactly one
immutable candidate identity and Board revision plus completed evidence from a fixed
coordinator-owned authoritative backend; `evidence_revision` is the content digest of that
authoritative output, not a caller-chosen run label.

Until that executor and backend registry are implemented, production has no path to construct
`SIGNED_OFF`. Surrogate or advisory output can contribute bounded ranking data only and can never
produce SI, PI, thermal or DFM approval. Absent, failed, stale, mismatched or unconfigured
authority produces a fixed refusal or explicit non-claim. The seam is not exported through MCP or
CLI and does not accept arbitrary runners, network processes, board bytes, geometry, prompts,
backend exception text, tokens or mutation claims.

## Consequences

- The current core is honest about what it can prove: candidate/revision-bound evidence structure
  exists, but positive authoritative signoff is intentionally unavailable.
- Consumers can distinguish refusal/non-claim from a future authoritative result without treating
  surrogate ranking or one invocation as approval.
- A future executor must be coordinator-owned, bounded, fixed-profile, content-digest-bound and
  independently tested before the positive status can be admitted.
- The issue remains open; this decision does not claim SI/PI, thermal, DFM, fabrication, hardware,
  or production signoff.

## Alternatives considered

- **Let a caller provide a backend callable:** rejected because fixed ID fields do not prove which
  authority ran and an untrusted callable could mint a passing result.
- **Treat surrogate or advisory output as signoff:** rejected because ranking evidence is not an
  authoritative domain result.
- **Expose a positive result type before execution exists:** rejected because an unreachable or
  caller-forgeable approval field would be misleading.
- **Expose the seam through MCP or CLI now:** rejected because transport exposure would create a
  public promise before bounded execution and evidence gates exist.

## Verification

The private core tests cover deterministic identity, candidate/revision/evidence binding, stale
and mismatched evidence, hostile closed inputs and bounds, cancellation/deadline, backend-failure
redaction, surrogate non-claim behavior, and the regression that private construction cannot mint
`SIGNED_OFF`. No test is presented as authoritative SI/PI/thermal/DFM execution; that capability
is explicitly deferred.
