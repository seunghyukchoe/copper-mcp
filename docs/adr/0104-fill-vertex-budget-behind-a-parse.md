# ADR-0104: The fill-vertex budget sits behind a parse, and is calibrated as what it is

- Status: Accepted
- Date: 2026-08-13
- Owners: `@seunghyukchoe`
- Related: [Issue #165](https://github.com/seunghyukchoe/copper-mcp/issues/165), ADR-0021,
  ADR-0079, ADR-0089, ADR-0070, ADR-0101,
  [Fill-vertex budget calibration](../research/fill-vertex-budget-calibration-v1.md),
  `D-195`, `R-150`, `SEC-144`, `B-108`

## Context

`max_fill_vertices` shipped at 50,000. [ADR-0021](0021-zone-fill-authority.md) records the whole of
its calibration basis in one sentence: "CopperTone's pour is 4,314 vertices across two layers." One
fixture, one board.

Real pours are 12–30× that. On a private audio-project tree, 18 boards carry a cached pour and
seven of them run 50,482–130,305 vertices, one missing the budget by 482. Because
`run_zone_fill_authority` must read the cache before it can compare it to a refill, those seven were
refused with `cached zone fill could not be read: ...` **before freshness was ever considered**. The
diagnostic was accurate and pointed at the wrong thing: the operator asked whether their pour was
current and was told about a resource ceiling. Running the real authority path at 50,000 and at
200,000 over byte-identical sources moves 9 `fresh` / 2 `stale_fill` / 7 refused to 12 `fresh` /
6 `stale_fill` / 0 refused.

This is the shape of issue #128 — a default calibrated on fixtures making a surface untestable
against reality — and following that template turned up an answer that differs from it in two
places worth recording rather than quietly resolving.

**1. The budget is charged after the cost it looks like it prevents.** `read_fill_islands` calls
`parse_sexpr` on the whole document before it counts a single fill vertex. Refusing the largest
corpus board at a budget of 3 costs **20.9 s of the 24.2 s** a complete read costs: the budget
cannot prevent 86 % of its own workload. What it meters is only the materialisation of
already-parsed atoms into points, measured at 22–26 µs and 113–145 bytes per vertex on boards large
enough for the ratio to be stable. The defence against an unbounded vertex list is `ParseLimits`
(ADR-0079), and it is also what caps the population: at the shipped parse defaults no document can
offer more than **741,375** fill vertices before `budget.exceeded.nodes` refuses it.

**2. One budget does meter two populations, and unlike #128 that is not an argument for two
knobs.** It is charged twice per proof — once on the operator's cached pour, once on the copy KiCad
refilled. A stale board is by definition one whose cache and refill differ, so their sizes are free
to differ: the committed `zone-fill-stale` fixture caches 148 vertices and recomputes 186, so every
budget in [148, 185] admits the operator's board and then refuses this server's own recomputation
of it. But across six stale corpus boards the two differ by at most 0.03 % (−24 to +2 vertices).
Issue #128 split a budget because its populations were three orders of magnitude apart; here they
are the same board's pour computed twice, and there is no second distribution to fit a second
number to.

The measurement did expose a real defect in the same place. `_points` hardcoded the word "cached",
so the refilled call site produced the self-contradicting `refilled zone fill could not be read:
cached zone fill exceeds the configured vertex budget`.

## Decision

**The default becomes 500,000, derived by ADR-0079's rule.** A board that fits inside the parser's
16 MiB input ceiling should normally fit inside every scale budget, applied at the *densest*
observation so it is a worst-case rule: 16 × 29,503 vertices/MiB = 472,048, rounded up. The
derivation does not depend on the private corpus — the densest in-repository board, CopperTone, is
27,239 vertices/MiB and gives 435,824, which rounds to the same number. 500,000 is 3.8× the largest
pour measured anywhere.

**The range stays 3 – 1,000,000.** ADR-0079 ends each range where its budget stops being reachable,
and 1,000,000 is still reachable: a 741,375-vertex document reads at it. It is deliberately left
below the 2,097,152 the fixed 16 MiB byte ceiling would permit, because raising a ceiling no
measured board needs buys nothing. A ceiling that moves with its default is a ceiling that is not
doing anything.

**This is recorded as a DoS-posture change and priced, not presented as tuning.** Against the
densest reachable document — 741,375 vertices in 4,096 islands, 6,094,870 bytes — the change moves
a refusal from 29.325 s / 146.8 MiB to 35.812 s / 168.4 MiB. **It buys an attacker 6.5 seconds and
21.6 MiB**, +22 % wall time and +15 % peak, against a floor this budget has never been able to
move because that floor is the parse. `COPPER_MCP_MAX_FILL_VERTICES=50000` restores the previous
posture exactly; an operator who wants a materially better one must lower
`COPPER_MCP_MAX_PARSE_NODES`, which is the control that actually binds. Recorded in `SEC-144` and
`B-108`.

**The budget stays one operator setting, and the refusal stops naming the wrong document.**
`_points` no longer guesses which of the two documents it holds; both call sites already say it,
so the refusals are now `cached zone fill could not be read: zone fill exceeds the configured
vertex budget` and `refilled zone fill could not be read: ...`.

**Raising it is answer-preserving, and that is proved from the budget rather than from a
differential.** `max_vertices` reaches exactly one expression in the reader — the guard that aborts
`_points` — so it decides *whether* a read happens and never *what* it returns. Two facts follow
and are pinned by tests: the refusal threshold is exactly the board's vertex **total** (a
board-wide sum, never a per-island one — `zone-fill-islands` separates them at 186 across two
islands whose larger is 94), and above that threshold the islands, the `fill_digest`, and
`ZoneFillAuthority.to_dict()` are identical at the vertex count, at the old default, at the new one
and at the ceiling. Deliberately **not** argued from an equality between a run with the change and
a run without it: that would bound what the change perturbs, not what the budget can reach.

## Consequences

Seven of eighteen corpus boards stop being refused for a resource reason and get the answer the
surface exists to give — three `fresh`, four `stale_fill`. Of the eleven boards that already had an
answer, every one keeps it.

**Half a working designer's zoned boards carry a pour KiCad does not reproduce.** That is now
visible rather than hidden behind a ceiling, and it is the honest finding, not a regression.

**No route is unlocked and none is claimed.** `B-105` measured that at zero — only one converting
board has an `F.Cu` zone at all — and this change does not move it. Nothing here is justified with
a route-quality claim.

**A refusal moves from one place to another, as expected when a gate is raised.** This budget meters
a board's *total* pour; every consumer of the islands charges the *widest island* instead
(`routing.layered_board_adapter._MAX_FILL_VERTICES` refuses above 4,096; the single-layer core
walks a ring per pairwise test against `max_obstacle_checks`). Those populations are not
proportional — one corpus board holds 61 % of its vertices in one ring. **14 of 18 corpus boards
carry an island above 4,096**, the widest 43,889, and at the old default seven of them could not be
read at all. Those will now read and then be refused per-island by the ordered-layer adapter. Sizing
that ceiling is a separate calibration against `max_obstacle_checks` in the routing path, filed
as [issue #167](https://github.com/seunghyukchoe/copper-mcp/issues/167) under `R-150` rather than
ridden in on this change.

**No content address moves.** The budget bounds what is admitted and never what is written;
`tests/test_golden_identities.py` is unchanged and passing. Unlike ADR-0089 there is no version
constant to advance, because nothing this budget touches reaches a published identity.

Every committed corpus measurement of this surface taken before today was taken at 50,000 whether
or not an environment said otherwise, because `Settings` constructed directly — as the runners
deliberately do, so an ambient `COPPER_MCP_ALLOW_APPLY` cannot reach them — takes the dataclass
default. That is why the dataclass default and the environment default are now pinned equal by a
test rather than left as two numbers that happen to agree.

## Alternatives considered

**Raise it to 200,000, the number that clears the corpus.** Rejected, and issue #165 asked for it
to be: a number chosen to clear one tree is not a calibration, and the next denser board is back
where this one started. 500,000 follows from a density and a byte ceiling and would have been the
same number without the corpus.

**Split the budget into cached and refilled settings, as #128 split `max_obstacles`.** Rejected by
the measurement rather than by preference. The two populations differ by at most 0.03 % across six
stale corpus boards; #128's differed by three orders of magnitude. A second knob would add
configuration surface and fit a distribution that does not exist. What the two-population finding
does justify is the diagnostic fix and sizing the number so the gap cannot bind.

**Bound the refilled read by the parse budgets alone, leaving it unmetered here.** Attractive —
that read is this server's own subprocess output, already bounded by `kicad_timeout_seconds`, the
child's file-size ceiling and `ParseLimits` — but rejected as an unpriced widening. It would make
the operator's one visible control silently not apply to half of what the call reads.

**Also raise `_MAX_FILL_VERTICES` in the layered adapter, so the newly reachable boards route.**
Rejected as out of scope and unmeasured. Its cost is the obstacle-check budget rather than this
one, `B-105` measured the single-layer fill shrink as a net loss on this corpus, and the routing
path is under concurrent change. Filed as issue #167 under `R-150` so it stays visible.

**Move the ceiling per-request rather than per-server.** Deferred. Issue #165 raises it because the
budget gates a KiCad subprocess, and it is a real question — but a per-request resource ceiling is
a caller-supplied bound on server work, which is a different security posture from an operator's
environment and needs its own decision. Recorded in `R-150` rather than settled in passing.

**Lower the default instead and let operators raise it.** Rejected for the reason ADR-0079 gave:
it leaves every operator with an ordinary large board discovering an environment variable before
the tool answers their question, and the refusal they hit first is about a ceiling rather than
about their board.

## References

- [ADR-0021](0021-zone-fill-authority.md)
- [ADR-0079](0079-discriminated-configurable-parse-budgets.md)
- [ADR-0089](0089-region-scoped-obstacle-model.md)
- [ADR-0101](0101-fill-currency-is-not-in-the-document.md)
- [Fill-vertex budget calibration](../research/fill-vertex-budget-calibration-v1.md)
- [Parse-budget calibration and its DoS cost](../research/parse-budget-calibration-v1.md)
