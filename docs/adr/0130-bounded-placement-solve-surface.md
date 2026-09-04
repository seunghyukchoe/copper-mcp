# ADR-0130: Bounded placement search is a read-only solve surface that never mints apply authority

- Status: Accepted
- Date: 2026-09-04
- Owners: @seunghyukchoe
- Related: Issue #262, ADR-0024, ADR-0110, ADR-0120, B-059, B-078, B-082

## Context

CopperMCP judges placements but does not search them. An AI client must hand-author
every pose through `preview_placement` proposals and read back legality, while the
deterministic local/beam solver (`placement/solver.py`, measured by B-059/B-078/B-082)
is reachable only from tests and benchmarks. The README records "there is no placement
solver" as an explicit non-claim, and the M4 retrospective names "solving for a placement"
as untracked future work rather than done.

The solver core already satisfies every property a public surface would need: it searches
bounded grid-adjacent moves over the caller's own seven-rule intent language (no coordinate
fields exist to widen), it ranks only legalizer-issued candidates, every candidate carries
the same identity, digest bindings, and four three-valued legality checks a preview mints,
and its work is metered by evaluations, rounds, beam width, legalizer checks, route-probe
budgets, and an operation deadline. What is missing is the surface, not the engine.

## Decision

Add a read-only `solve_placement` MCP tool and `solve-placement` CLI command that run the
existing solver over one workspace board and return up to N ranked legalizer-issued
candidates plus solver accounting. The surface reuses the whole placement contract
instead of inventing one:

- **Same intent language.** Subjects, the seven rule kinds, and ref-anchored proposals are
  parsed by `parse_placement_intent` with the same subject/rule ceilings. `include_apply_token`
  and `include_drc` are not fields of this surface: the closed contract drops them, and the
  runtime parser refuses them when smuggled in, exactly as the live surface refuses apply
  authority today.
- **Same bindings.** `expect_board_revision` / `expect_snapshot_digest` are checked before any
  search work, and every returned candidate carries the `candidate_id`, `base_revision`,
  `view_revision`, and `snapshot_digest` a preview would mint for the same pose.
- **Same refusals, no new codes.** A solver `input_refused` returns the legalizer's own
  refusal verbatim (the initial evaluation's diagnostic); a collapsing duplicate proposal
  refuses `infeasible_constraints`, which is what a syntactic contradiction is.
  `work_exhausted` and `deadline_exhausted` refuse `budget_exhausted`, which is an admission
  that the work ran out and is never conflated with infeasibility. `cancelled` is unreachable:
  the service passes no cancellation callback, so no mapping for it exists and none is claimed.
- **No apply authority, by construction.** Every returned candidate carries
  `apply_token: null` with the closed `unsupported_surface` reason: the surface mints no
  capability under any setting, so asking again cannot change the answer. ADR-0120's
  vocabulary is untouched. A caller that wants to apply a solved pose re-previews exactly
  that candidate through `preview_placement` with `include_apply_token: true`, which mints a
  placement-domain token under the ordinary operator flag and single-use rules. The solve
  response never runs DRC and never touches a live editor.
- **Caller work budgets, server time budgets.** The request carries `max_evaluations`,
  `max_rounds`, `beam_width`, `max_ranked`, `step_nm`, and `scoring_policy` under ceilings
  tighter than the core maxima; wall-clock deadlines stay server-side, derived from
  `max_placement_seconds`, because a caller-set CPU deadline is not a reproducible bound and
  the solver's own documentation names `max_evaluations` as the deterministic ceiling. Both
  scoring policies are admitted with server-default route-probe settings: route-aware probes
  are bounded, offline, deterministic Board IR projections, not a model call.

## Consequences

The "no placement solver" non-claim narrows to "no placement solver with apply, DRC, or
live authority": search is advisory, legality-flagged, and ranking-only, exactly like the
surrogate ranking of ADR-0128, which contributes order and never approval. An agent that
receives zero candidates alongside `budget_exhausted` has learned that the work ran out,
not that the placement is impossible; an agent that receives N candidates still owns the
choice, and each choice remains preview-grade until re-previewed and explicitly applied.

What becomes harder: every new MCP tool widens the input surface permanently, and solver
ceilings multiply legalizer work (evaluations × checks). The ceilings above are the bound,
the per-candidate legality records are the evidence, and raising any ceiling is a new
decision with its own measurement rather than a tuning constant.

Follow-up required: SI/PI/thermal-ranked policies stay unregistered non-claims; a live
solve surface is not proposed (live placement is proposal-only by ADR-0032); durable
solve jobs ride the existing routing-job contract if ever needed, not a new one.

## Alternatives considered

- **Mint per-candidate apply tokens.** Rejected: one call would multiply single-use
  capabilities by N, and a token minted for a pose the caller did not individually preview
  breaks the preview-then-apply pairing the two apply surfaces are built on.
- **Return candidate manifests for authorized export instead of full candidates.** Rejected:
  placement candidates are already redacted, revision-bound, and preview-shaped; the export
  machinery exists for route geometry disclosure, which placement does not need.
- **Map exhaustion to a new `solve_exhausted` code.** Rejected: it would say nothing the
  existing `budget_exhausted` does not say, and a second ignorance code invites callers to
  treat one of them as infeasibility.
- **Expose caller deadlines.** Rejected: wall-clock bounds are not reproducible across
  machines, and the deterministic ceiling (`max_evaluations`) already answers the question
  deadlines would be asked.
