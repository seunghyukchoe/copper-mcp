# ADR-0087: Scope the obstacle model to a routing region, and split the budget that was counting three things

- Status: Accepted
- Date: 2026-08-08
- Owners: `@seunghyukchoe`
- Related: ADR-0051, ADR-0078, ADR-0079, D-173, B-088, B-098, R-130, SEC-130, issue #128, issue #112

## Context

`AStarSettings.max_obstacles` defaulted to 256. Measured read-only against a live audio-project
tree, **0 of 385 net previews routed** at default settings, with 93 refusing
`obstacle_budget_exceeded` on boards carrying up to 31,389 segments (issue #128). The budget was not
close, and it was not close by two orders of magnitude.

Splitting the 93 by the message each raised — the detail the shared code hid — shows the budget was
charging three unrelated populations, measured in
[the calibration note](../research/route-obstacle-budget-calibration-v1.md):

| Population | p50 | max | Refusals it caused |
|---|---:|---:|---:|
| the routed net's own copper, every layer | 90 | 755 | 60 |
| the routed net's own selected-layer attachment tracks | 72 | 663 | 1 |
| foreign selected-layer copper, whole board | 8,554 | 22,244 | 32 |

Sixty-one of ninety-three were the net's **own** copper — the model that decides whether the net is
already connected — being charged to a budget named for the copper it must avoid. On a finished
board the true answer for those nets is `already_connected`, and it was being reported as a work
failure. That is the same defect ADR-0079 named for the parse budgets: one code for ten ceilings,
so the response could not say which one ran out or what to do about it.

The third population is different in kind. 22,244 objects for one two-pin route does not fit inside
any budget that is still a ceiling, and raising the ceiling to fit the largest board seen so far
would make the budget track corpus size rather than bound work — the next board is always bigger.
But 22,244 whole-board objects is not 22,244 *relevant* objects: a route between two pads interacts
only with copper near the corridor between them. Everything else was loaded, counted, and never
consulted.

The obstacle model over-approximates on purpose. Dropping an obstacle is the one direction of error
that yields a candidate routed through real copper, which — unlike a refusal — is not recoverable.
Any scoping therefore has to be provably conservative, not merely plausible.

## Decision

**One budget becomes three, each metering one population and refusing under its own code.**

| Setting | Default | Ceiling | Meters |
|---|---:|---:|---|
| `max_obstacles` | 4,096 | 32,768 | the region-scoped foreign selected-layer obstacle model |
| `max_net_objects` | 1,024 | 4,096 | the routed net's own copper, connectivity and attachment, every layer |
| `max_obstacle_checks` | 2,000,000 | 10,000,000 | exact geometric predicates evaluated by one request |

Their codes are `obstacle_budget_exceeded`, `net_object_budget_exceeded`, and
`obstacle_check_budget_exceeded`. The first two previously shared one code; the third previously
shared it too, so a caller could not tell whether to raise a count or a work meter. **Each message
names the budget and its configured value**, and deliberately not the observed count, which would
disclose board density to a caller that may not have the board.

**The obstacle model is scoped to a routing region, and the search is confined to the same region.**
The region is the axis-aligned envelope of the routed net's pad centres and its selected-layer
attachment copper, widened by a new `region_margin_nm` setting (default 10 mm, range 1 nm – 1 m) and
clipped to the safe board. Every lattice index is derived from the region, so the search cannot
leave it.

Conservatism is a closed argument rather than a heuristic:

1. every node the search can expand lies inside the region, and every edge joins two such nodes;
2. the widest envelope any exact predicate queries with is one lattice step around a point on such
   an edge — proximity scoring; edge legality and endpoint containment query smaller ones;
3. an obstacle is dropped only when its own inflated bounds, expanded by one further lattice step,
   do not intersect the region.

Step 1 is what makes the rest sound. Scoping the model while leaving the lattice spanning the board
would route straight through dropped copper, and that is the mutation
`tests/test_routing_region_scope.py` exists to catch.

**An exhausted search inside a scoped region refuses `no_path_in_region`, not `no_path`.** `no_path`
is a proof about the modelled board. When the region is a proper subset, the honest claim is about
the region, and the caller's recourse — a wider `region_margin_nm` — is different.

**Clipping to the safe board is deliberate**, not an implementation detail. A board smaller than
twice the margin yields a region equal to the board, so the concept disappears and every small
fixture behaves exactly as before.

**`ROUTER_VERSION` advances to `astar-grid/0.7.0`.** The layered router keeps its own budgets and is
untouched.

## Consequences

**Measured on the same 385 real-board previews, byte-identical sources:** `routed` 0 → 14,
`already_connected` 263 → 318, `obstacle_budget_exceeded` 93 → 3. Every net that moved moved out of
`obstacle_budget_exceeded`, and nothing regressed. The important number is not the 14 routes: it is
the 55 that are now correctly reported as already connected. Eight nets now refuse `off_grid`, the
first lattice-class refusal ever observed on a real board — B-088's hypothesis becomes testable,
and this change deliberately does not test it.

**Candidate identity moves, and stored artifacts stop verifying.** No path geometry changed anywhere
— not the two-pad golden fixture, not the NE5532 fixture, not one of the twenty SimpleRouteJson
boards, whose routed count, wire length and bend count all replay unchanged. The addresses move
because the recorded settings and the obstacle-check meter move. A caller holding a `candidate_id`
computed before this change must re-preview; the ID no longer reproduces, and the version bump is
what says so rather than leaving it to be discovered. `tests/test_golden_identities.py` records the
moved pins with that reason attached.

**A route needing a detour wider than the region is now refused where it previously would have been
searched for.** On the external corpus this cost nothing measurable — the routed count is identical,
and 9 of 11 `no_path` refusals became `no_path_in_region` — but the possibility is real and is why
the margin is a setting rather than a constant.

**The ceiling costs about half a second.** On the largest real board the worst net takes 1.87 s and
45.5 MiB at the new default, and 1.95 s and 45.5 MiB with the model unscoped at the 32,768 ceiling:
peak memory is dominated by the parsed snapshot, not the obstacle list. On adversarial input packed
to 60,000 objects inside the region, both the default and the ceiling refuse, in 1.5 s and 1.7 s.
Board IR's own parse budgets refuse the snapshot entirely past roughly 90,000.

**Follow-up:** the three real-board nets that still exceed 4,096 scoped objects were not
investigated. Neither was the eight-net `off_grid` population, which is now the first thing those
nets meet and is the natural next slice.

## Alternatives considered

**Raise `max_obstacles` and nothing else.** Rejected on measurement: no value that is still a
ceiling covers 22,244 objects for one route, and a budget re-derived from the largest board seen
tracks corpus size rather than work. It would also have left 61 of the 93 refusals in place, since
those were never about obstacles.

**Scope the obstacle model but leave the lattice spanning the board.** Rejected as unsound. It is
the cheaper half of this change and it produces exactly the failure the obstacle model exists to
prevent: copper proposed through copper the request never modelled. It is now a named mutation with
a test that fails on it.

**Let the search leave the region and refuse when it does.** Rejected as strictly worse than
confinement: it refuses in every case confinement would refuse, plus the cases where a legal detour
exists inside the region but A* happened to expand a node outside it first.

**Keep one budget and only add a distinct message.** Rejected for ADR-0079's reason: messages are
dropped before many callers see them, and the whole point is that the caller can act. Three
populations with three costs need three codes.

**Do nothing and document the limit** — the interim issue #128 offered. Rejected because the limit
was not a limit anyone chose: 256 was never derived from anything, and documenting it would have
made a measurement error permanent.
