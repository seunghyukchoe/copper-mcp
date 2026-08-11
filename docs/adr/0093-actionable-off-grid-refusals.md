# ADR-0093: An off-grid refusal carries the pad, the pitch and the exact miss

- Status: Accepted
- Date: 2026-08-12
- Owners: `@seunghyukchoe`
- Related: [Issue #136](https://github.com/seunghyukchoe/copper-mcp/issues/136),
  [issue #116](https://github.com/seunghyukchoe/copper-mcp/issues/116), ADR-0006, ADR-0009,
  ADR-0079, ADR-0089, D-181, B-088, B-096, B-100, R-138, SEC-011, SEC-134,
  [off-grid lattice refusal research](../research/off-grid-lattice-refusal-v1.md)

## Context

Since [ADR-0089](0089-region-scoped-obstacle-model.md) the single-layer route preview produces
`not_routed/off_grid` refusals on real boards — 18 of 385 net previews across three boards of a
private working tree, the first lattice-class refusals ever observed on real hardware. Before that
change every one of these nets refused `obstacle_budget_exceeded` before any lattice question was
asked.

The refusal was sound and said one thing:

> the pad-center delta is not divisible by the requested grid step

That names the rule, not the board. It does not say which pad is off the lattice, what pitch is in
use, or how far the miss is — all three of which the router has computed by the time it refuses,
and all three of which are derived from bytes the caller supplied. This is the same defect
[ADR-0079](0079-discriminated-configurable-parse-budgets.md) named for the parse budgets: a
correct refusal that leaves the caller with nowhere to go.

**The measurement came before the design, and it refuted the hypothesis on record.**
[B-088](../ledgers/benchmark-ledger.md) had localised the routing constraint "to the lattice"; the
[research note](../research/off-grid-lattice-refusal-v1.md) re-previewed all 18 nets at exactly the
finest lattice their own geometry admits, with `max_grid_nodes` at its ceiling, and **routed none**:
13 have a pad centre outside the board outline inset by half the requested track width (125,000 nm at the measured net class) and 5 exceed
the node budget at the pitch they require. The median axis miss is 55,000 nm against a 250,000 nm pitch, so this is not a
near-miss against a slightly coarse default either; and the nanometre residues that collapse ten of
the divisors to 1 or 3 nm are authored by KiCad into the board file — a millimetre literal one
nanometre short of the round value the part was placed at — and converted exactly, so they are
not a units defect on our side.

That result is what settles the shape of this record. There is no lattice to change, so this is a
disclosure decision and nothing more.

## Decision

**`off_grid` carries a typed `OffGridEvidence` object, and every other diagnostic carries `None`.**

| Field | Meaning |
|---|---|
| `pad_id` | the pad whose centre is off the lattice |
| `anchor_pad_id` | the pad the lattice is anchored at |
| `grid_step_nm` | the pitch actually in use |
| `miss_x_nm`, `miss_y_nm` | signed nanometres from the nearest lattice line **to** the pad centre |
| `largest_representable_step_nm` | gcd of the two centre deltas |

Every value is an exact integer computed from the request the caller made against bytes the caller
supplied. The signed miss is chosen over an unsigned distance because it is directly actionable:
moving the pad by `(-miss_x_nm, -miss_y_nm)` lands it on the lattice. Ties at exactly half a step
resolve to the lower line, so the value is a function of the inputs alone.

**The evidence is bound to its own code by a biconditional, checked twice.** `RouteDiagnostic`
refuses evidence on any other code *and* refuses an `off_grid` diagnostic without it; the published
`RouteDiagnosticContract` re-checks the same condition independently, so a payload assembled by
anything other than the service — a rewriting transport, a replayed artifact — can neither smuggle
lattice geometry into a refusal that measured none nor strip it from one that did. The published
field is optional with a `None` default; an *absent* key is the same refusal as an explicit `null`,
because the default resolves first and the biconditional then fires. That is why requiredness adds
nothing, and the alternative below records why it was removed.

**Both evidence contracts refuse to describe an impossible measurement, and the published one is
not the weaker of the two.** A pad that misses on neither axis is on the lattice; a pair whose
largest representable step is a multiple of the requested step is representable at it; a miss
larger than half a step is not a miss to the *nearest* line; an anchor equal to the pad it anchors
is not a measurement. Each is a refusal, not a clamp, in the backend `OffGridEvidence` **and** in
the published `OffGridEvidenceContract`. The first version of this record checked all four in the
dataclass only and claimed the property for "the evidence contract"; adversarial review forged
four individually well-typed, in-range payloads and the published schema accepted every one. A
published schema that asserts less than the runtime lets a schema-only consumer accept what this
project's own code refuses to construct — the same defect a reviewer raised against sibling #137 —
so no guard is left to the runtime alone, and each is pinned by a test that fails without it.

**`largest_representable_step_nm` is a statement about representability, never a prediction.** It
says the largest step at which the pair can be expressed, not that routing succeeds there — B-100
measured that it usually does not. The contract documents this, and the code emits no field
claiming routability, because it cannot prove one.

**Nothing about routing semantics changes.** The lattice, the search, `ROUTER_VERSION`
(`astar-grid/0.7.0`), every pinned identity in `tests/test_golden_identities.py`, and all 385
real-board verdicts are untouched and byte-identical.

## Consequences

**A caller can now distinguish the situations that previously looked alike**, using the number that
separates them: a divisor of 30,000 nm on a short net says a finer lattice would include this pad;
a divisor of 1 nm says no lattice a bounded router can hold ever will, so the pad has to move or
the request has to change. Before this record, distinguishing them required the experiment in
B-100.

**Disclosure widens by two pad identities and one relative displacement, and by nothing else.**
This is inside the settled precedent rather than an extension of it: SEC-011 permits object counts,
ADR-0079 refusals already carry byte-offset locators, and `RouteConnectionContract` already
publishes `start_pad_id` and `end_pad_id` for the same net. An `off_grid` refusal says strictly
less than a **routed** preview of the same request, which publishes absolute path vertices.
It does *not* say strictly less than `already_connected`, which carries pad identities and counts
but no geometry at all — the relative miss and the divisor are not derivable from it — and for a
net that can never be routed the comparison against the routed outcome is in any case vacuous.
The load-bearing argument is therefore the one that holds for every request rather than the
comparison: this is per-request geometry about the net the caller named, computed from bytes the
caller supplied. No object count, no net name, no absolute coordinate, and nothing about board
density enters the payload — the deliberate line ADR-0089 drew when it refused to report observed
obstacle counts. SEC-134 records the review.

**One committed artifact had to be taught to redact.**
`scripts/benchmark_real_board_capability.py` records one refusal message per verdict into a JSON
report that is committed to this public repository while its corpus is private. A message that now
interpolates geometry would have carried a pad's miss distance straight into that file. The runner
truncates an `off_grid` message to `OFF_GRID_MESSAGE_LEAD`, a constant it imports rather than
retypes, so a reworded message cannot silently start leaking. The distinction is durability, not
code: a caller of the service is entitled to these numbers and a committed artifact is not.

**The Dijkstra oracle shares `_prepare` and therefore shares this failure.** Rebuilding its
diagnostic without the evidence is not a lost field but a raised `ValueError`, because the
biconditional is enforced in the constructor. The oracle passes the evidence through, and a test
pins it.

**The outline finding on 13 of the 18 nets is recorded and not acted on.** `off_grid` was standing
in front of a pad-outside-the-outline problem exactly as `obstacle_budget_exceeded` stood in front
of the lattice class. Naming it is this record's contribution; fixing it is not, and R-138 carries
the risk that the new evidence reads as a complete diagnosis when it is one layer of several.

## Alternatives considered

**Make `off_grid` a required key on `RouteDiagnosticContract`.** This is what the first version of
this record did, by declaring `off_grid: OffGridEvidenceContract | None` with no default, and it is
rejected. State the property requiredness would provide: there is none. The anti-strip property —
that a payload carrying `code: "off_grid"` without evidence is refused — comes from the
presence↔code biconditional and not from requiredness, because an absent key resolves to the
default and the biconditional then refuses it. A test pins exactly that, deleting the key rather
than setting it to `null`. What requiredness *does* do is invalidate every diagnostic of every
**other** code that a caller recorded before this change, since a stored `no_path` or
`stale_revision` payload has no such key. That is a compatibility break bought for nothing, and it
was recorded nowhere. The field is therefore `| None = None`, our own serializer emits it on every
response regardless, and the break does not happen. The general rule this instance follows: a
default is the right shape whenever the invariant is *relational* — enforced between fields —
rather than *presence-based*.

**Lower the default `grid_step_nm`.** Rejected by measurement, not by taste. The greatest common
divisor across all eighteen pad pairs is 1 nm, and 1,000 nm even after the KiCad-authored residues
are snapped away; a lattice that fine needs between 1.8 × 10⁷ and 3.3 × 10¹⁴ nodes to span the
pad-to-pad bounding box alone, against a 500,000 ceiling. It would move `ROUTER_VERSION` and every
published content address to convert eighteen `off_grid` refusals into eighteen different ones.

**Snap pad centres onto the lattice.** Rejected on the project's standing conservatism rule.
Connectivity and board outline under-approximate; obstacles over-approximate. Moving a pad centre
by up to half a step to make a route representable invents copper the board does not have, in the
one direction of error that yields a candidate rather than a refusal.

**Report the miss as an unsigned distance, or as a single scalar.** Rejected because it is less
actionable for no gain in disclosure: the sign is what tells a caller which way to move, and the
two axes are what tell them whether one coordinate or both is at fault. The magnitude bound is
identical either way.

**Add a `would_route_at_that_step` field.** Rejected as a claim the code cannot prove at the point
it refuses. The node budget, the region, the outline and the obstacle model are all decided after
the lattice check; B-100 shows the honest answer on real boards is usually "no". A non-claim would
have to be a one-value literal, and a field that only ever says `not_run` is worse than the
absence of the field.

**Put the geometry in the message only.** Rejected: a caller would have to parse prose to act on
it, and the message is bounded at 256 characters and is not a stable contract. The message carries
the numbers for a human reader; the typed object carries them for a program, and the two are
generated from the same measurement.
