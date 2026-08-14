# ADR-0108: The single-layer fill seam refuses malformed evidence under its own numbers

- Status: Accepted
- Date: 2026-08-14
- Owners: `@seunghyukchoe`
- Related: [D-200](../ledgers/decision-ledger.md),
  [issue #166](https://github.com/seunghyukchoe/copper-mcp/issues/166),
  [ADR-0101](0101-fill-currency-is-not-in-the-document.md),
  [ADR-0098](0098-reproducible-mutation-evidence.md),
  [`docs/mutants/2026-08-14-verified-fill-shape-gate.json`](../mutants/2026-08-14-verified-fill-shape-gate.json),
  [issue #167](https://github.com/seunghyukchoe/copper-mcp/issues/167),
  the post-0.8.0 audit §1.3 and plan item P7.2

## Context

[Issue #166](https://github.com/seunghyukchoe/copper-mcp/issues/166) reports that
`AStarRouter.propose(..., verified_fill=...)` performs no shape validation of the evidence it is
handed, where `layered_board_adapter._invalid_verified_fill` refuses all of it at the ordered-layer
adapter's input boundary. ADR-0101 closed the *containment* half of the same asymmetry and left
this half open rather than widening its own blast radius, recording the residue as `R-147`.

The issue asks for a decision, not a patch: **close the gap, or record that the seams are
deliberately asymmetric with the reason.** The post-0.8.0 audit ranks it below the other three open
correctness issues, verifies that containment is present on *both* paths, and states that what is
missing is type and size validation whose failure mode is a loud Python exception rather than wrong
copper in a candidate. The audit's own lean is toward a small close, and the issue's stated trap is
that agreement between two routers may not be argued from a with/without differential: such a
differential bounds what the evidence *adds*, never what a gate fails to reject.

So the decision was taken from the code, by feeding the seam each malformed input the adapter names
and reading what came back. Ten inputs, on a converting fixture with a foreign pour, against `main`
at `4a5fa65`:

| Input | Today's single-layer behaviour |
|---|---|
| A `list` instead of a tuple of islands | **routed** — accepted with no refusal at all |
| `points` a `list` instead of a tuple | **routed** — accepted with no refusal at all |
| A non-`VerifiedFill` entry | **`AttributeError`** out of `_prepare`, uncaught |
| A non-`PointNM` vertex | **`AttributeError`** out of `_polygon_bounds`, uncaught |
| An untyped net identity | `unsupported_geometry` — caught by the zone-backing gate |
| An untyped layer identity | `unsupported_geometry` — caught by the zone-backing gate |
| A malformed source digest | `stale_fill` — caught by the revision equality |
| A ring under three vertices | `unsupported_geometry` — `_prepare`'s own ring floor |
| 40,000 islands | `obstacle_budget_exceeded` — after the preparation work, named for the model |
| A 1,100,000-vertex ring | `obstacle_check_budget_exceeded` — after the work, named for the model |

Four of the ten are unguarded in fact: two are **silently accepted** and two **raise**. Two more
refuse only after the preparation work they were supposed to bound, under a code that names the
obstacle model rather than the input a caller could fix. Of the last four, one — the ring floor —
is `_prepare`'s own deliberate check; the other three refuse for a *semantic* reason that happens
to catch a shape mistake, which is luck rather than construction: an untyped `net_id` is caught
only because no zone happens to be keyed by it.

## Decision

**Close the gap**, at `propose`'s and `replay`'s own input boundary, with
`astar.invalid_verified_fill` mirroring the adapter's vocabulary word for word and every refusal
mapping to `RouteFailureCode.UNSUPPORTED_GEOMETRY`, as issue #166 suggests. Seven of the adapter's
eight clauses are carried. Three parts of the decision are about what is *not* carried, and each is
the load-bearing half:

**1. The three-vertex floor stays where it is.** `_prepare` already refuses a ring under three
vertices under its own message and its own test. Restating the floor at the boundary would make
that check unreachable, and unreachable code cannot be shown to work. The boundary gate therefore
bounds a ring from **above only**, and the two checks are disjoint and both live —
`VF15`/`VF16` are the mutants that hold that apart in both directions.

**2. The vertex-range clause is recorded as subsumed rather than implemented.** It is the eighth
class in the issue's list, and on this path it is provably unreachable: `PointNM.__post_init__`
enforces `JSON_SAFE_INTEGER`, which **is** `(1 << 53) - 1`, the same bound the adapter's
`_MAX_SAFE_INT` applies — so a vertex that passes the type check above cannot fail a range check
below it. The same argument makes the adapter's own copy of that clause dead, which is recorded
here and not repaired: the ordered-layer adapter is not this change's subject and a dead check that
refuses nothing is harmless where a wrong ceiling is not.

**3. The two ceilings are this path's own numbers and deliberately not the adapter's.**

* Islands: `32_768`, the domain ceiling of `AStarSettings.max_obstacles`. Every island becomes a
  candidate obstacle, so evidence above that ceiling could not be modelled even if it were read.
* Vertices per island: `1_000_000`, the domain ceiling of `Settings.max_fill_vertices`. That budget
  bounds a whole *document's* pour, so it is a true upper bound on any single island a configured
  reader can produce, and it refuses nothing any shipped configuration admits. It is not inert: the
  caller this gate exists for is an in-process one that **synthesises** islands rather than reading
  them, which is exactly issue #166's stated exposure, and such a caller reaches it.

Harmonising with the adapter's `_MAX_FILL_VERTICES = 4_096` was considered and refused. `B-108`
measured **14 of 18 corpus boards carrying an island above 4,096, widest 43,889**; issue #167 files
that ceiling as an over-refusal and the audit parks it in M5 behind a paired calibration that plan
item P7.1 says no quality argument may substitute for. Importing it here would newly refuse, on a
path that accepts those boards today, boards whose only fault is a large pour — closing a hygiene
gap by copying a known defect. `VF13` and `VF14` are the mutants that make a later silent
harmonisation fail rather than land.

## Consequences

**What improves.** Every malformed-evidence class now returns a typed `unsupported_geometry`
naming the input, on `propose` and on `replay` alike — `replay` gates *before* `fill_binding_for`,
which reads every field of every island and would otherwise raise on exactly the input the gate
exists to refuse. Two silent accepts and two uncaught exceptions become refusals, and two late
budget refusals become early named ones. The two routers now describe malformed evidence in one
vocabulary, and a test pins the two copies of `_typed`/`_digest` to the same answers so they cannot
drift apart into two.

**What becomes harder.** There are now two implementations of the identity and digest predicates.
Consolidating them is blocked by import direction — `layered_board_adapter` imports `astar` — and
the agreement test is the mitigation rather than the fix.

**What is not claimed.** No behaviour change on any well-formed input: the three semantic gates
(revision, zone backing, containment) are unmoved and a test feeds each one the input it must
reject to prove the shape gate did not swallow them. **No with/without differential is offered
anywhere in this change**, per the issue's own trap: every gate here is proved by handing it the
input it must refuse. And no claim that the exposure was reachable from the public surface — every
public route preview builds its islands from `run_zone_fill_authority` and mypy covers the typed
path, which is exactly why ADR-0101 declined this and why it is landing now as hygiene rather than
as a correctness fix.

**Evidence.** 19 committed mutants,
[`docs/mutants/2026-08-14-verified-fill-shape-gate.json`](../mutants/2026-08-14-verified-fill-shape-gate.json),
one per clause plus the two call sites, the typed code, both ceilings, both identity predicates,
the floor in both directions, and the containment gate — **19 mutants, 19 killed, 0 survivors,
0 `not_run`**, run through `scripts/mutation_harness.py` per ADR-0098; Python 3.12.13 on
macOS-26.5.2-arm64, `baseline_returncode: 0`, spec
`sha256:9795eaaf030b89db91baf1562b01a2517a013ddea01c64fa619f0aa8deb980b9`. Read the mapping and
not the count: **VF15 and VF16 are the pair that matters.** They hold the three-vertex floor and
the boundary ceiling apart in opposite directions — VF16 deletes `_prepare`'s floor, VF15 adds the
floor to the boundary — so between them no version of this change can leave one of the two checks
unreachable while the suite stays green.

## Alternatives considered

**Record the asymmetry and close the issue.** This was a live option and the issue offers it. It is
refused on the measurement: "no shape validation" understates the finding in one direction and
overstates it in another. Two inputs are *accepted and routed*, which no reading of the issue
predicted, and two raise where every other refusal at this seam is typed. An asymmetry ADR would
have had to say "a malformed island raises `AttributeError` here and returns `INVALID_REQUEST`
there, deliberately", and no reason for that survives being written down.

**Mirror the adapter exactly, ceilings included.** Rejected above: it would import issue #167's
over-refusal onto a second path, against P7.1's explicit prohibition on justifying that ceiling by
anything but its calibration.

**Refactor the adapter to share one implementation.** Rejected as blast radius. `astar` cannot
import `layered_board_adapter` (the dependency runs the other way), so sharing means a third module
and a change to a file this lane does not own, with sibling work in flight. The agreement test buys
the same protection at a fraction of the risk, and says so.

**Refuse with a new failure code.** Rejected: the issue names `unsupported_geometry` and ADR-0101's
two gates already use it, so a second vocabulary for the same class of mistake would be the drift
this ADR is closing.
