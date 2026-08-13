# Fill-vertex budget calibration, and what a budget behind a parse actually defends

**Research date:** 2026-08-13
**Reviewed against:** CopperMCP at `f0f7414`, KiCad board format version 20260206, KiCad 10.0.5
**Covers:** how many cached zone-fill vertices real boards carry and how that scales with source
size; what reading them costs, split into the part `max_fill_vertices` can prevent and the part it
cannot; how many fill vertices an adversary can present before the parse budgets refuse the
document at all; what the re-derived default costs an attacker and what it buys a real board; and
the second population this one budget also meters.
**Refuses to claim:** that these densities generalise beyond the 4 in-repository boards and the
18-board private corpus measured below; that any board here is fresh or stale (this note spawns no
KiCad — that split is B-105's and is quoted, not re-derived); or that raising this budget improves
any route. B-105 measured the route benefit at zero and that number stands.

## 1. The defect

`max_fill_vertices` shipped at **50,000**. ADR-0021 records the calibration basis in one sentence:
"CopperTone's pour is 4,314 vertices across two layers." That is the whole of it — one fixture,
one board, and a default an order of magnitude above it.

Real pours are 12–30× that fixture. Over a private audio-project tree, 18 boards carry a cached
pour and **7 of them exceed 50,000 vertices**:

| Islands | Vertices | Widest island | Vertices/MiB |
|---:|---:|---:|---:|
| 121 | 130,305 | 43,889 | 15,008 |
| 22 | 88,638 | 29,132 | 21,187 |
| 72 | 76,838 | 23,365 | 16,533 |
| 64 | 65,563 | 21,420 | 16,389 |
| 96 | 59,852 | 17,320 | 21,287 |
| 34 | 58,804 | 18,777 | 18,310 |
| 20 | 50,482 | 15,691 | 29,503 |

The last row misses the budget by 482 vertices.

`run_zone_fill_authority` reads the cached fill *before* it can compare it to a refill, so a board
over the budget is refused with `cached zone fill could not be read: ...` **before freshness is
ever considered**. Running the real authority path over the corpus at the shipped default and at
200,000, byte-identical sources, KiCad 10.0.5:

| Outcome | at 50,000 | at 200,000 |
|---|---:|---:|
| `fresh` | 9 | **12** |
| `stale_fill` | 2 | **6** |
| refused — over the vertex budget | **7** | **0** |

Every board that moved moved out of a budget refusal, and it moved to a real answer in both
directions: three to `fresh`, four to `stale_fill`. The budget was the first thing refusing a real
board, and it was hiding the answer the surface exists to give.

The split here counts every corpus board carrying a pour. Issue #165 and B-105 report 12 boards
and a 6/6 split because they count only the boards that also *convert* through Board IR, which is
the population their surface needs; the six extra boards here carry a readable pour without
converting, and reading a pour does not require conversion. Both counts are of the same corpus and
neither contradicts the other.

## 2. Method

Every `.kicad_pcb` reachable from this repository, plus every board under the operator's private
audio project tree excluding `.history/` and derived stems, was read **read-only** through
`read_fill_islands` with the budget raised far past reach, and the fill population counted with the
same accounting the enforcing code uses. Cost was measured twice per board — once at a budget of 3,
which refuses at the first vertex, and once unbounded — because the difference between them is the
only work this budget meters. Timing is `perf_counter_ns` with `tracemalloc` enabled; peak figures
are traced allocation, not RSS.

The corpus is a live working tree that changes under measurement, so every row carries its source's
SHA-256 prefix and the freshness split above was taken back to back against byte-identical sources.
No board was copied into this repository and nothing corpus-derived is committed: the reproducible
artifact at
[`benchmarks/results/board-ir/2026-08-13-fill-vertex-budget-calibration.json`](../../benchmarks/results/board-ir/2026-08-13-fill-vertex-budget-calibration.json)
is the **repository-only** run, and the corpus numbers appear here as explicitly out-of-repo
observations, the same way the private four-layer board appears in
[the parse-budget calibration](parse-budget-calibration-v1.md).

## 3. The cost curve, and where the budget sits on it

`read_fill_islands` calls `parse_sexpr` on the whole document before it counts a single fill
vertex. A budget refusal is therefore **paid for at full parse price**. Measured on the seven
largest corpus boards:

| Source bytes | Vertices | Refuse at budget 3 | Complete read | Marginal | Marginal share |
|---:|---:|---:|---:|---:|---:|
| 9,104,023 | 130,305 | 20.894 s | 24.223 s | 3.329 s | 13.7 % |
| 4,873,297 | 76,838 | 11.171 s | 12.883 s | 1.712 s | 13.3 % |
| 4,386,848 | 88,638 | 10.036 s | 11.964 s | 1.928 s | 16.1 % |
| 4,194,661 | 65,563 | 9.504 s | 11.051 s | 1.547 s | 14.0 % |
| 3,367,512 | 58,804 | 7.677 s | 9.063 s | 1.386 s | 15.3 % |
| 2,948,224 | 59,852 | 6.713 s | 8.039 s | 1.326 s | 16.5 % |
| 2,920,896 | 25,113 | 6.237 s | 7.002 s | 0.766 s | 10.9 % |

**Refusing the largest board costs 20.9 of the 24.2 seconds that reading it completely costs.** The
budget cannot prevent 86 % of its own workload. What it does meter is the materialisation of
already-parsed atoms into points, and that is linear and cheap:

| Quantity | Min | Max |
|---|---:|---:|
| Marginal wall time per admitted vertex | 15.9 µs | 51.8 µs |
| Marginal retained bytes per admitted vertex | 91 B | 160 B |

On the boards large enough for the ratio to be stable the figure is 22–26 µs and 113–145 bytes per
vertex. So the honest arithmetic for an operator is `extra ≈ 25 µs × budget` and
`≈ 130 B × budget`, on top of a parse that is already paid.

This is the finding that decides the design. **`max_fill_vertices` is not the defence against an
unbounded vertex list; `ParseLimits` is** (ADR-0079). A budget that costs 40 ms to exceed is a
different thing from one that costs 40 seconds, and this one is neither: it costs whatever the
parse costs, whichever side of it you land on.

## 4. What an adversary can actually present

Because the parse happens first, the reachable fill-vertex population is bounded by the parse
budgets before `max_fill_vertices` is consulted at all. The shortest legal fill vertex is
`(xy 0 0)`, eight bytes and four charged nodes. Bisecting the densest legal fill document against
the shipped `ParseLimits` defaults:

| Shape | Maximum reachable vertices | Document | Refuses above with |
|---|---:|---:|---|
| one island | 499,999 | 4,000,062 B | `budget.exceeded.children_per_list` |
| 4,096 islands | **741,375** | 6,094,870 B | `budget.exceeded.nodes` |

At the shipped parse defaults **no document can offer more than 741,375 fill vertices.** An
operator who raises `COPPER_MCP_MAX_PARSE_NODES` moves that, but the parser's fixed 16 MiB input
ceiling caps it at 2,097,152 under any configuration.

## 5. The default, and where the number comes from

| Budget | Was | Now | Range | Derivation |
|---|---:|---:|---|---|
| `max_fill_vertices` | 50,000 | **500,000** | 3 – 1,000,000 (unchanged) | 16 MiB × the densest observed pour, 29,503 vertices/MiB ⇒ 472,048, rounded up |

The rule is ADR-0079's, applied at the *densest* observation rather than the median so it is a
worst-case rule: **a board that fits inside the parser's 16 MiB input ceiling should normally fit
inside every scale budget too.** Density is a property of how KiCad writes a pour, not of the
design, and it is tight enough for the byte ceiling to be a usable proxy: 7,195–29,503 vertices per
mebibyte across the 22 pour-carrying boards measured — 18 corpus, 4 in-repository — median 15,732.

The number is robust to whether the private corpus is included at all, which is worth stating
because it is the difference between a calibration and a number picked to clear one tree. The
densest **in-repository** board is CopperTone at 27,239 vertices/MiB, giving 16 × 27,239 = 435,824.
Both derivations round to the same 500,000, and 500,000 is 3.8× the largest pour anyone here has
measured.

**The range is deliberately unchanged.** ADR-0079 ends each range where its budget stops being
reachable, and 1,000,000 is still reachable: §6 reads a 741,375-vertex document at it. It is
also left below the 2,097,152 the byte ceiling would allow, because raising a ceiling no measured
board needs buys nothing. The floor of 3 is what a polygon costs.

## 6. What the raised default costs an adversary

The question a DoS control has to answer is what an untrusted request can buy. Measured against
the densest reachable document — 741,375 vertices in 4,096 islands, 6,094,870 bytes:

| Budget | Outcome | Wall time | Peak traced |
|---:|---|---:|---:|
| 50,000 (was) | refused | 29.325 s | 146.8 MiB |
| **500,000 (now)** | refused | **35.812 s** | **168.4 MiB** |
| 1,000,000 (ceiling) | read | 40.135 s | 226.8 MiB |

**Raising the default buys an attacker 6.5 seconds and 21.6 MiB.** It does not buy them the
29.3 s and 146.8 MiB the old default already conceded, because that is the parse, and the parse
happens either way. Stated as a share: the change is +22 % wall time and +15 % peak against a
floor this budget has never been able to move. An operator who wants the old posture back sets
`COPPER_MCP_MAX_FILL_VERTICES=50000` and gets exactly it; an operator who wants a materially
better one has to lower `COPPER_MCP_MAX_PARSE_NODES`, which is the control that actually binds.

Three separate controls stand between an untrusted board and unbounded work here, and this budget
is the last of them: `max_board_bytes` bounds the file, `ParseLimits` bounds the document, and
`max_fill_vertices` bounds the points built from it.

## 7. One budget, two populations

`settings.max_fill_vertices` is charged at **two** call sites in one proof:

1. the **cached** pour, read from the board in the operator's workspace — adversary-authored bytes;
2. the **recomputed** pour, read from the copy KiCad refilled — this server's own subprocess
   output, already bounded by `kicad_timeout_seconds`, by the child's file-size ceiling, and by
   `ParseLimits` on the re-read.

A *stale* board is by definition one whose cache and refill differ, so their sizes are free to
differ, and they do. The committed `zone-fill-stale` fixture caches **148** vertices in one island
and KiCad recomputes **186** across two — 26 % larger. Every budget in [148, 185] therefore admits
the operator's board and then refuses this server's own recomputation of it. Across the six stale
corpus boards the deltas are smaller and run in both directions: −24, −9, −2, 0, +2, +2 vertices
against totals of 6,184 to 130,305, so at most 0.03 %.

That measurement argues for two things and against a third.

**For:** the refusal must say which document ran out. It did not. `_points` hardcoded the word
"cached", so the refilled call site produced the self-contradicting `refilled zone fill could not
be read: cached zone fill exceeds the configured vertex budget`. The reader is called twice and
cannot know which document it holds, so it no longer guesses; both call sites already name it.

**For:** the number must be sized so the gap cannot bind in practice, which 500,000 does at 3.8×
the largest pour measured.

**Against:** a second knob. Unlike issue #128 — where one budget met two populations three orders
of magnitude apart, and no single number could be right for both — these two populations are the
same board's pour computed twice, and across the corpus they differ by at most 0.03 %. There is no
second distribution here to fit a second number to. Splitting the knob would add configuration
surface and answer a question the measurement does not ask.

## 8. What raising it changes, and what it cannot

`max_vertices` reaches exactly one expression inside `read_fill_islands`: the guard that aborts
`_points`. It can decide *whether* a read happens and never *what* it returns. Two consequences,
both pinned by tests rather than left to be inferred:

- The refusal threshold is **exactly** the board's vertex total. `remaining` decrements by each
  island's consumed points, so the read raises if and only if the board's total exceeds the
  budget — a board-wide total, never a per-island one. `zone-fill-islands` separates the two: 186
  vertices across two islands whose larger is 94, so a budget of 100 admits either island alone
  and must still refuse the board.
- Above that threshold the value does not depend on the budget at all. The same islands and the
  same `fill_digest` come back at the vertex count, at the old default, at the new one, and at the
  ceiling; and end to end, `ZoneFillAuthority.to_dict()` is byte-identical across that same ladder
  while KiCad recomputes the refill on every run.

So raising this budget can only turn a budget refusal into a real answer. It cannot turn one answer
into a different one, and it invents no freshness: `zone-fill-stale` is `stale_fill` at every
budget that admits it. This is deliberately **not** argued from an equality between a run with the
change and a run without it — that would bound what the change perturbs, not what the budget can
reach. The claim is one-directional and about the parameter itself.

The corpus corroborates it where the fixtures cannot. Of the 11 boards that reached an answer at
50,000, **every one returns the same answer at 200,000** — nine `fresh` stayed `fresh`, two
`stale_fill` stayed `stale_fill`, and no board changed sides in either direction. Only the seven
budget refusals moved. That is corroboration of the argument above and not a substitute for it:
it is a differential over one corpus, and what makes the claim general is that the budget's only
use is an abort.

Independently, none of the five surfaces the real-board harness measures reads this budget at all:
`settings.max_fill_vertices` reaches exactly two expressions in the codebase, both inside
`run_zone_fill_authority`, and that harness never sets `include_fill_authority`. A
before → after → before run corroborates it, and is reported with its noise rather than rounded to
"identical":

- 16 of 18 boards are byte-identical across all three runs. One board was edited by the designer
  between the second and third runs — the hazard `R-146` records — so the third run is against a
  corpus state that differs by one board, and only the first pair carries the differential claim.
- **Run totals are identical in all three runs**, and on the 16 stable boards conversion, route
  verdicts, placement verdicts and scene content agree **16/16 in every pairing**.
- KiCad DRC violation counts do **not** agree: 10 of 16 boards match in every pairing, including
  baseline against baseline. The counts saturate near per-rule caps (199/200/201, 499/500/502) and
  which rules fill them varies between runs of identical code, so this is a property of KiCad on
  these boards and is not attributable to the change. It is recorded rather than filtered out,
  because a differential presented as clean when it is not is worth less than one that says where
  it is noisy.

## 9. What this makes reachable, including the refusals

Raising a budget that gated a refusal makes previously unreachable code paths reachable on real
boards, and the honest expectation is that new refusals surface there. One does.

`max_fill_vertices` meters a board's **total** pour. Every consumer of the islands it returns
charges the **widest single island** instead:
`routing.layered_board_adapter._MAX_FILL_VERTICES` refuses any island above 4,096 vertices
outright, and the single-layer core's `_polygon_touches_rect` walks one island's vertices per
pairwise test against `max_obstacle_checks`. Those two populations are not proportional — one
corpus board holds 61 % of its 27,622 vertices in a single ring, another 29 % of 59,852 across 96
islands — so no board total predicts them.

**14 of the 18 corpus boards carry at least one island above 4,096 vertices**, the widest being
43,889; no in-repository board does, which is why the ceiling has never been met. At the old
default seven of those boards could not be read at all, so the per-island ceiling was
unreachable for them; at 500,000 they read, and the ordered-layer adapter will then refuse their evidence
with `verified fill island is not a bounded polygon`. That is a real refusal moving from one place
to another, not a regression, and it is filed rather than fixed here: the per-island ceiling lives
in the routing path, its cost is the obstacle-check budget rather than this one, and B-105 already
measured the single-layer fill shrink as a net loss on this corpus. Sizing it needs its own
calibration against `max_obstacle_checks`, which this note does not do.

## 10. What this does not establish

- Nothing here says a pour is *fresh*. No KiCad process is spawned by the committed runner; the
  fresh/stale split in §1 is B-105's, re-run here against byte-identical sources and quoted.
- No route is unlocked. B-105 measured that at zero — only one converting board has an `F.Cu` zone
  at all — and raising this budget does not change it. The gain is that a resource refusal becomes
  the honest `fresh` or `stale_fill` answer.
- The densities come from 18 boards in one designer's project family plus this repository's four
  pour-carrying boards. A board unlike these is outside the sample.
- Timing and memory were measured on CPython 3.12, macOS/arm64. The per-vertex constants are
  implementation details of that runtime; the linearity is a property of the reader.
