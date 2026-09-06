# ADR-0131: A routed bundle carries opt-in authoritative DRC evidence, and nothing else changes

- Status: Accepted
- Date: 2026-09-04
- Owners: @seunghyukchoe
- Related: Issue #264, ADR-0004, ADR-0008, ADR-0103, ADR-0109, ADR-0114, ADR-0066

## Context

`preview_route_bundle` publishes the only mutually-compatible multi-net result in the
project -- negotiated routing, a complete composition replay, and the exact cross-net
clearance gate -- but it cannot be checked against KiCad at all. Single nets verify
through `preview_route` with `include_drc`, and foreign single nets through the mandatory
DRC of `verify_external_route_candidate`. The agent loop therefore breaks exactly where a
human's does not: batch the nets, then check the board. The README records "no DRC" for
bundles as an explicit non-claim, and this ADR narrows it.

## Decision

An opt-in `include_drc` flag on the bundle request (MCP-only, default off) continues a
routed plan through the same fixed KiCad DRC path single candidates use, on one private
disposable board carrying every plan patch:

- **Compose by replay, not by trust.** The plan is re-verified against the original
  snapshot through the existing bundle serializer: plan staleness, the negotiated
  physical-clearance replay over the composition, per-candidate net/layer resolution,
  object and byte budgets, and a combined round-trip proof that the patched board is
  exactly the source plus every patch. Any deviation refuses before KiCad starts, so
  structural problems never execute a subprocess. Fill needs no handling on this path:
  the negotiated coordinator admits only fill-free candidates, so a composed plan is
  always routed under the conservative model and there is no per-net fill to replay.
- **One splice, one round-trip proof.** All patches' segments are spliced before the root
  close in a single pass with per-plan UUID namespaces, and the patched board must
  reparse to exactly the source content plus every patch -- the same byte-preservation
  proof the single path runs, generalized to N patches.
- **One DRC run, bundle-bound evidence.** The evidence binds `bundle_id`, the shared
  `base_revision`, every `candidate_id`, the source board, the patched board, and the
  patched rule/library context, with the live count labelled `single_invocation`: one run
  is execution evidence, not a reproducible differential (ADR-0109). A KiCad execution
  failure propagates as a refusal answer, exactly as a single-candidate `include_drc`
  failure does -- the caller re-requests without the flag for the plan alone.
- **Response schema 1.0 to 1.1, routed variant only.** The additive optional `drc_evidence`
  field follows the route-preview 1.0-to-1.1 precedent. `not_routed` and `unsupported_board`
  cannot carry evidence and keep their shape. No apply token, no persistence, no CLI, no
  live peer: verification is not authorization, and a DRC-clean bundle is still not an
  appliable one -- per-net apply with re-verification remains the only write path.

## Consequences

An agent can now close its own loop on multi-net work: compose, check the composed board
against KiCad, then decide -- while every refusal stays typed and every count stays
comparable. What does not change matters more: DRC-clean is not electrical, signal-
integrity, manufacturability, or hardware review; one invocation is not a differential;
and the composed board exists only on a private disposable copy that is never published
and never applied from.

What becomes harder: composed splice work scales with total patch edges against the object
and byte budgets, and a KiCad run per bundle costs wall-clock per invocation. Both are
bounded and refused loudly rather than truncated.

Follow-up required: bundle-atomic apply is deliberately not proposed -- sequential per-net
apply with re-verification stays the only write path, and a multi-net write needs its own
adversarial review before it is designed, let alone built.

## Alternatives considered

- **Per-candidate DRC runs sharing one board.** Rejected: N invocations for N candidates
  prove less than one run over the composition (cross-net copper is the point of the plan),
  and N evidence objects invite cherry-picked differentials.
- **Plan plus typed DRC refusal beside it.** Rejected: it splits one verdict into two
  documents with different bindings and teaches callers to act on the plan while ignoring
  the refusal. The single-route surface fails the whole response on DRC failure; the bundle
  does the same.
- **Replaying fill-bound candidates under a fresh refill.** Rejected as unnecessary:
  coordinator admission forbids fill-bound candidates, so no composed plan can carry fill
  and there is nothing to refill or replay.
- **A bundle apply token.** Rejected outright: verification is not authorization, and an
  atomic multi-net write is a new mutation surface requiring adversarial review.
