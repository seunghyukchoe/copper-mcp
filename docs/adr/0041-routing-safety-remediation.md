# ADR-0041: Close layered and freshness-bound routing safety gaps

- Status: Accepted
- Date: 2026-08-05
- Owners: `@seunghyukchoe`

## Context

The layered Board IR proposal and freshness-bound fill path were correct within their intended
subsets, but review found four ways untrusted input or physical margins could be misrepresented:
malformed obstacle entries could be hidden by a count-budget result, malformed settings could reach
arithmetic, an absent compare-and-swap value could be reported as a stale revision, and foreign
obstacle envelopes omitted the candidate copper radius. Exact fill also carried no zone identifier,
so its governing explicit clearance needed a conservative association.

## Decision

- Validate obstacle values before returning `obstacle_budget_exceeded`; reserve that diagnostic for
  structurally valid entries.
- Validate every layered A* cost and resource field at the adapter boundary using the same finite
  ceilings as the pure planner.
- Emit `stale_revision` only when a caller supplied an expected revision and it differs from the
  requested base revision.
- Inflate foreign track, via, and zone envelopes by the candidate track half-width or via radius,
  in addition to the stricter net/zone clearance.
- For freshness-bound islands, use the strictest `Zone.clearance_nm` among matching net/layer
  zones, because the island record intentionally carries no zone identifier. Keep fill opt-in,
  source-revision-bound, and fail-closed for orphaned or mismatched evidence.

## Evidence and limits

Focused regressions cover malformed requests, physical envelope margins, explicit high-clearance
zones, and orphaned-fill refusal. B-023 records deterministic replay and the negative gate. These
changes do not add KiCad mutation, durable export, DRC authority, electrical validation, or
FreeRouting parity; the layered path remains candidate-only.
