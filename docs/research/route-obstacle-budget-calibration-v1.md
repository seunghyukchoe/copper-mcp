# Route obstacle-budget calibration, and what a region-scoped obstacle model costs

**Research date:** 2026-08-08
**Reviewed against:** CopperMCP at `b2becd8`, KiCad board format version 20260206
**Covers:** how many objects the single-layer A* router actually models per request on real boards,
split by the three structurally different populations that one `max_obstacles` budget was charging;
what those distributions imply for a re-derived default; what confining the search to a routing
region buys and costs; and what the raised ceiling costs in wall time and peak memory on the
largest real board and on deliberately adversarial input.
**Refuses to claim:** that these densities generalise beyond the 11-board, 385-net mixer corpus and
20-board SimpleRouteJson corpus measured below; that a `region_margin_nm` of 10 mm is right for
boards unlike these; or that any routed candidate here is manufacturable. Nothing was DRC-checked,
nothing was applied, and no board was copied into this repository.

## 1. The defect

`AStarSettings.max_obstacles` defaulted to **256**. Measured against a live audio-project tree,
**0 of 385 net previews routed** at default settings on `F.Cu`, at most 40 nets per board in Board
IR canonical order, decided before the run:

| Verdict | Count | Share |
|---|---:|---:|
| `routed` | **0** | 0.0 % |
| `already_connected` | 263 | 68.3 % |
| `not_routed/obstacle_budget_exceeded` | **93** | 24.2 % |
| `not_routed/invalid_two_pin_net` | 26 | 6.8 % |
| `not_routed/unsupported_geometry` | 3 | 0.8 % |

`already_connected` dominating is **correct**, and stays correct after the fix. These are finished
designs; truthfully reporting that every pad of a net already shares one component is the right
answer, not a failure. The defect is the 93.

Issue #128 read those 93 as one wall. They are not. Splitting them by the message each raised shows
one budget refusing **three structurally different populations**:

| Refusal message | Count | What it was counting |
|---|---:|---|
| the same-net connectivity model exceeds the configured obstacle budget | 60 | the routed net's **own** copper, every layer |
| the selected-layer obstacle count exceeds the configured obstacle budget | 32 | **foreign** copper on the selected layer |
| the same-net attachment copper exceeds the configured obstacle budget | 1 | the routed net's own copper on the selected layer |

Sixty-one of the ninety-three were the routed net's own copper being charged to a budget named for
the copper it has to avoid. No single number can be right for both, because the two distributions
are three orders of magnitude apart.

## 2. Method

Every `.kicad_pcb` under the operator's audio project tree, excluding `.history/`, was read
**read-only** and converted through the same fail-closed adapter the public route-preview surface
uses. Eleven of fifteen convert. For each convertible board, the first 40 named nets in Board IR
canonical order were previewed on `F.Cu` with `clearance_nm = 200,000`,
`track_width_nm = 250,000`, seed 7 — 385 previews, the selection fixed before the run and never
adjusted by retrying.

Boards are a live working tree and change under measurement; the before and after runs below were
taken back to back against byte-identical sources, and each row records the board's SHA-256 prefix
so a disagreement is detectable rather than silent. An earlier pair of runs, taken hours apart,
disagreed on three nets for exactly this reason and was discarded rather than reported.

Object counts per net were measured separately from Board IR, using the same population definitions
the enforcing code uses.

## 3. Measured distribution

Over the 381 nets that present at least two selected-layer pads:

| Population | min | p50 | p90 | p99 | max | Budget it charges |
|---|---:|---:|---:|---:|---:|---|
| same-net objects, all layers | 2 | 90 | 293 | 608 | **755** | `max_net_objects` |
| same-net selected-layer attachment tracks | 0 | 72 | 229 | 450 | 663 | `max_net_objects` |
| foreign selected-layer objects, whole board | 57 | 8,554 | 21,773 | 22,230 | **22,244** | `max_obstacles` |

The two same-net populations fit comfortably inside a small budget. The foreign-copper population
does not fit inside **any** budget that is still a ceiling: 22,244 objects for one two-pin route,
on a board carrying 31,389 segments in total.

Scoping the foreign population to a corridor around the routed net's own copper, widened by a
margin and clipped to the board, changes that materially. Nets whose obstacle model fits a given
budget, out of 381:

| Region margin | ≤256 | ≤1,024 | ≤2,048 | ≤4,096 | ≤8,192 | ≤16,384 | ≤32,768 |
|---|---:|---:|---:|---:|---:|---:|---:|
| whole board | 25 | 65 | 65 | 103 | 182 | 342 | 381 |
| 25 mm | 48 | 69 | 104 | 212 | 321 | 375 | 381 |
| 10 mm | 57 | 123 | 188 | **273** | 350 | 378 | 381 |
| 5 mm | 90 | 178 | 243 | 314 | 370 | 380 | 381 |
| 2 mm | 142 | 222 | 275 | 332 | 374 | 380 | 381 |
| 1 mm | 160 | 241 | 292 | 341 | 375 | 380 | 381 |

Read across the 4,096 column: a 10 mm region reaches with 4,096 objects the coverage that the
whole-board model needs about 16,384 for. **Scoping is worth a factor of four in the ceiling**, and
the ceiling is the security control, so that factor is the argument for doing it.

Restricting to the 32 nets that actually reached the obstacle model — small, sparse nets, median 5
same-net objects — makes the case sharper: whole-board p50 647 and max 22,244 against 10 mm-scoped
p50 334, p90 2,180.

## 4. The defaults, and where each number comes from

| Budget | Was | Now | Ceiling | Derivation |
|---|---:|---:|---:|---|
| `max_obstacles` | 256 | **4,096** | 4,096 → **32,768** | 10 mm-scoped coverage of 273/381 nets; §5 shows what the ceiling costs |
| `max_net_objects` | — (shared `max_obstacles`) | **1,024** | 4,096 | next power of two above the observed maximum of 755, and the largest whose pairwise merge stays under a quarter of the default check budget |
| `region_margin_nm` | — (whole board) | **10,000,000** (10 mm) | 1 m | the widest margin still worth a 4× lower ceiling in §3; also wider than any board in the repository's fixtures, so every committed fixture routes with the region equal to the board |
| `max_obstacle_checks` | 2,000,000 | 2,000,000 | 10,000,000 | unchanged; §5 shows it is still the binding work control |

`max_net_objects` deserves its derivation spelled out, because it is the one budget here whose cost
is superlinear. The connectivity model merges components by comparing **every admitted pair**, so
*n* objects cost *n*(*n*−1)/2 charges against `max_obstacle_checks`. At 1,024 that is 523,776
charges — a quarter of the default check budget, so the object budget is what refuses and the
refusal can name it. At the 4,096 ceiling it is 8,386,560, which fits only if an operator has also
raised the check budget toward its own 10,000,000 maximum. The ceiling is placed exactly where the
budget stops being reachable, following ADR-0079.

## 5. What the raised ceiling costs

The question a DoS control has to answer is what an untrusted request can buy. Measured on the
largest real board (`ch`: 31,389 segments, 1,003 vias, 580 pads), worst of 40 nets, peak traced
allocation for the whole `propose()` call:

| Configuration | Worst wall time | Peak allocation |
|---|---:|---:|
| default: 10 mm region, `max_obstacles` 4,096 | 1.87 s | 45.5 MiB |
| whole board, `max_obstacles` 4,096 | 1.97 s | 45.5 MiB |
| whole board, `max_obstacles` 32,768 | 1.95 s | 45.5 MiB |

Peak memory does not move with the budget, because it is dominated by the parsed snapshot rather
than by the obstacle model: 32,768 inflated rectangles are about 4 MiB of tuples. On this board the
worst case is the same-net connectivity merge, not the obstacle model.

Against a synthetic adversarial board — a 200 mm square whose entire routing region is packed with
foreign tracks on a 100 µm pitch — the ceiling behaves as a ceiling:

| Objects offered | at default 4,096 | at ceiling 32,768 |
|---|---|---|
| 32,000 | refuse, 0.84 s, 37.5 MiB | model built, search runs, 1.60 s, 37.5 MiB |
| 60,000 | refuse, 1.52 s, 70.4 MiB | refuse, 1.74 s, 70.4 MiB |
| ~90,000 | Board IR's own default byte budget refuses the snapshot first | same |

Three separate controls therefore stand between an untrusted request and unbounded work, and the
obstacle budget is the least of them: the Board IR parse budgets bound the board (ADR-0079), the
obstacle-check budget bounds the predicates, and `max_obstacles` bounds the model. Raising the
model's ceiling from 4,096 to 32,768 buys an attacker about half a second and no additional memory.
That is the cost, stated so it can be disagreed with.

## 6. Why the scoping is conservative

An obstacle is an over-approximation. Dropping one is the dangerous direction: the router would
propose copper through real copper, and unlike a refusal that is not recoverable. The scoping is
therefore not "obstacles near the route" — which would be a heuristic — but a closed argument:

1. The routing region is computed **before** the lattice, from the routed net's pad centres and its
   selected-layer attachment copper, widened by `region_margin_nm` and clipped to the safe board.
2. Every lattice index is derived from that region, so every node the search can expand lies inside
   it, and every edge joins two such nodes.
3. The widest envelope any exact predicate queries with is one lattice step around a point on such
   an edge — that is proximity scoring; edge legality and endpoint containment query smaller ones.
4. An obstacle is dropped only when its own inflated bounds, expanded by one further lattice step,
   do not intersect the region. By (2) and (3) no query can return it.

Step (2) is what makes the rest sound, and it is the step a partial implementation would skip:
scoping the model while leaving the lattice spanning the whole board would route straight through
dropped copper. `tests/test_routing_region_scope.py` attacks exactly that, with a fixture whose only
way around a wall runs through copper the region legitimately dropped; the mutation that unclips the
lattice is caught by it, and the mutation that removes the one-step term in step (4) is caught by a
separate proximity test.

The price is real and is not hidden: a route needing a detour wider than the region is refused. It
refuses under its own code, `no_path_in_region`, rather than claiming `no_path` about a board it
never modelled.

## 7. Result on real boards

Same corpus, same 385 previews, byte-identical sources, defaults:

| Verdict | Before | After |
|---|---:|---:|
| `routed` | 0 | **14** |
| `already_connected` | 263 | 318 |
| `not_routed/invalid_two_pin_net` | 26 | 31 |
| `not_routed/off_grid` | 0 | **8** |
| `not_routed/no_path_in_region` | 0 | 7 |
| `not_routed/obstacle_budget_exceeded` | 93 | **3** |
| `not_routed/unsupported_geometry` | 3 | 3 |
| `not_routed/no_path` | 0 | 1 |

Every net that moved moved out of `obstacle_budget_exceeded`; nothing regressed:

| From | To | Count |
|---|---|---:|
| `obstacle_budget_exceeded` | `already_connected` | 55 |
| `obstacle_budget_exceeded` | `routed` | 14 |
| `obstacle_budget_exceeded` | `off_grid` | 8 |
| `obstacle_budget_exceeded` | `no_path_in_region` | 7 |
| `obstacle_budget_exceeded` | `invalid_two_pin_net` | 5 |
| `obstacle_budget_exceeded` | `no_path` | 1 |

90 of 93 now reach a real verdict. The headline is **not** the 14 routes: it is that 55 of them are
`already_connected`, which was always the true answer for a finished board and was being reported as
a budget failure. Three nets still exceed `max_obstacles` at 4,096 even scoped, which is the budget
working.

**The lattice class becomes visible on real boards for the first time.** Issue #128 recorded that
not one `off_grid`, `no_path`, or `grid_budget_exceeded` refusal appeared in the whole corpus,
because the obstacle model was exhausted before any lattice question was asked. Eight `off_grid`
refusals now appear, on three boards. B-088 measured 59.83 % completion on an external corpus and
localised its constraint to lattice alignment; that hypothesis was untested on real boards and is
now testable. This note does not test it — it makes it measurable, which was the aim. What can be
said is narrow and worth saying anyway: the pad-centre divisibility rule, not the obstacle model, is
now the first thing 8 of 385 real nets meet.

## 8. What this changed elsewhere

Replaying the SimpleRouteJson external corpus (B-088) under the new defaults: **the routed count,
wire length, and bend count are unchanged**, and 9 of the 11 `no_path` refusals are reclassified as
`no_path_in_region`. Ten of twenty candidate digests move, because the recorded obstacle-check
meter moves when the model is scoped. No path geometry changed anywhere in this repository — not in
the corpus, not in the NE5532 fixture, not in the two-pad golden identity. The published candidate
addresses that move are moved by the recorded settings and work meters, which is why
`ROUTER_VERSION` advances to `astar-grid/0.7.0` rather than the change being presented as
compatible.

## 9. What this does not establish

- No routed candidate here was DRC-checked, applied, or verified against KiCad. `routed` means the
  bounded search produced an exact orthogonal path under the modelled obstacles.
- 10 mm is calibrated against one 11-board corpus of comparable mixer boards. A board whose nets
  need longer detours will meet `no_path_in_region` and need a wider margin; the setting exists for
  that, and the refusal names it.
- The three nets still exceeding `max_obstacles` were not investigated further.
- Nothing here measures multi-layer routing, which has its own budgets and is untouched.
