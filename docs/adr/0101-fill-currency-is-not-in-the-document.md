# ADR-0101: Fill currency is not in the board file, so keep the model and gate the shrink

- Status: Accepted
- Date: 2026-08-13
- Owners: `@seunghyukchoe`
- Related: [ADR-0013](0013-polygon-zone-obstacles.md), [ADR-0021](0021-zone-fill-authority.md),
  [ADR-0039](0039-fill-aware-routing-obstacles.md),
  [ADR-0040](0040-public-fill-routing-provenance.md),
  [ADR-0070](0070-layered-fill-aware-obstacles.md), issue #63

## Context

Issue #63 asks for fill-aware zone routing obstacles: use verified fill geometry as a tighter
obstacle so nets can route through zone windows the boundary envelope forbids, with unverified fill
falling back to the envelope. That is the one change in this project that makes an obstacle
*smaller*, which is the direction an obstacle may not move without proof, so it was investigated
before it was built.

**It is already built.** The single-layer A* core has replaced a foreign zone's conservative
envelope with exact verified fill polygons since [ADR-0039](0039-fill-aware-routing-obstacles.md),
`preview_route` has advertised the effect since [ADR-0040](0040-public-fill-routing-provenance.md),
and the ordered-layer adapter received the same plumbing in
[ADR-0070](0070-layered-fill-aware-obstacles.md). Issue #63's own comment thread records this;
what it does not record is that the issue's requirement was therefore already met, so #63 is the
third issue this week whose premise the code had outrun. This record exists so the fourth reader
does not repeat the investigation, and because the investigation found one thing the code had not
done.

### 1. Fill currency cannot be proven from the board file alone

KiCad tracks fill staleness in `ZONE::m_needRefill`, set by every geometry setter on the zone
(`pcbnew/zone.h`, `pcbnew/zone.cpp`). It is **never written to the file**: the zone writer emits
`(polygon …)` and `(filled_polygon (layer …) [(island yes)] (pts …))` and nothing else about fill
state — no checksum, no hash, no timestamp, no dirty flag
(`pcbnew/plugins/kicad/pcb_io_kicad_sexpr.cpp`). Worse for a reader, the parser ends every zone it
loads with `zone->SetNeedRefill( false )` under the comment "Clear flags used in zone edition"
(`pcb_io_kicad_sexpr_parser.cpp`). A board loaded from disk therefore always claims to be current,
whether or not it is.

The flag would not be sufficient even if it were persisted: it tracks edits to the *zone*, not to
the copper the pour has to clear around, so moving a track never sets it.

This is exactly the premise [ADR-0021](0021-zone-fill-authority.md) was built on, and it holds
against KiCad 10. Freshness is provable only by recomputation — refill a private disposable copy
and compare canonical island geometry — which is what `run_zone_fill_authority` does.

### 2. KiCad's own DRC does not detect a stale fill; it silently uses it

The DRC subsystem contains no reference to refilling or staleness at all. The refill is a caller's
responsibility performed *before* the engine runs: the GUI dialog reads a "Refill all zones before
performing DRC" checkbox and passes it to `DRC_TOOL::RunTests` (`dialogs/dialog_drc.cpp`), and
`kicad-cli pcb drc` refills only when `--refill-zones` is passed
([KiCad 10 CLI reference](https://docs.kicad.org/10.0/en/cli/cli.html)), which the jobs handler
gates on `drcJob->m_refillZones`. Without it, `drcEngine->RunTests(…)` runs against whatever
polygons the file carried, with no warning and no violation code — there is no `DRCE_` for an
out-of-date fill.

So a stale fill is not a loud failure anywhere in KiCad's own file-driven tooling. It is a silent
wrong answer, which is why this project refuses rather than guesses.

### 3. The existing gates cover the obstacle shrink — on one router more thoroughly than the other

[ADR-0070](0070-layered-fill-aware-obstacles.md) applies four gates in the ordered-layer adapter:
shape, revision, zone backing, and **containment** — an island whose bounding box escapes its
backing zone's bounding box is refused. The single-layer core applies only two: revision and zone
backing. It performs the same envelope retirement, at `astar.py`, dropping a foreign selected-layer
zone from the blocking set when *any* island shares its `(net_id, layer_id)`, with no relation
whatsoever between the island's extent and the zone's.

On the public `preview_route` path this is harmless, and the reason is worth stating because it is
the actual load-bearing precondition and it is not containment: `read_fill_islands` walks every
zone and every `filled_polygon` in the board with no net or layer filter, so the island set handed
to the router is board-complete, and `route_preview.py` narrows it no further. The obstacle the
router emits therefore covers KiCad's own recomputed copper. One exception, stated so the word
"complete" stays honest: a zone node carrying no `(net …)` child at all is skipped. Board IR's own
zone parser requires the token and KiCad writes `(net 0)` for an unassigned pour, so the skip is
reachable only on input Board IR would itself refuse — but it is a skip, not a refusal, and a
future parser relaxation would silently widen it. Containment is a consistency
cross-check on that evidence, not the proof of it — an island contained in its zone but omitting
its siblings would satisfy containment and still under-cover — and ADR-0070's phrasing slightly
overstates what rule 4 buys. What it does catch is the realistic mistake at the typed in-process
seam, where `AStarRouter.propose(…, verified_fill=…)` accepts islands from a caller that never went
through `run_zone_fill_authority`.

### 4. The measured benefit is zero routes unlocked, and the measurement says why

B-105 records it. On the corpus, 12 of 18 saves convert and **all 12 carry zones**, yet at the
shipped `max_fill_vertices` default of 50,000 the freshness proof succeeds on only **3**: seven
boards' cached fill is 50,482–130,305 vertices and is refused for exceeding the budget before
freshness is even considered, and two are `stale_fill`. Raise the budget to 200,000 — in domain, the
env ceiling is 1,000,000 — and no board is budget-refused; the split becomes **6 fresh, 6
`stale_fill`**. Half the zoned boards in a working designer's tree carry a pour that does not match
what KiCad recomputes. That is not a defect; it is section 1 and section 2 happening in the wild,
and it is the strongest argument in this record for refusing rather than believing.

Two further facts decide the benefit question:

- **Only 1 of the 12 converting boards has an `F.Cu` zone at all.** Every other board's pours are on
  `B.Cu`, `In1.Cu` and `In2.Cu`, and `preview_route` gates fill authority on a zone existing on the
  *selected* layer. The single-layer shrink therefore cannot engage on eleven of twelve boards no
  matter how good the evidence is. The pours are on the layers the **ordered-layer** router reaches,
  which is where the deferred public layered contract would matter.
- **Where it can engage, it makes outcomes worse, not better.** On the one `F.Cu`-poured board, the
  fill-aware path changed four verdicts and every one went `no_path` →
  `obstacle_check_budget_exceeded`: thirteen exact pour polygons totalling tens of thousands of
  vertices exhaust `max_obstacle_checks` where one boundary polygon did not. Across the three boards
  measurable at the raised budget, 120 nets produced 8 changes: **2 improvements, both from the
  *connectivity* role** (`invalid_two_pin_net` → `already_connected`), **6 regressions to a budget
  refusal, and 0 routes unlocked.**

So the shrink's measured benefit on real boards is zero, its measured cost is real, and both facts
argue the same way: keep it, keep it opt-in, and do not widen it.

### 5. Fill evidence does not survive the seam after the router

Found while sweeping the other direction — not "where is fill used", but "what consumes a routed
candidate, and does it know". `AStarRouter.replay` reconstructs a `RouteRequest` from the
candidate's own fields and calls `propose(snapshot, request)` **with no `verified_fill`**, because a
`RouteCandidate` carries no fill evidence to reconstruct it from. Every downstream verifier of a
routed candidate goes through it: `_replay_candidate` in `adapters/kicad_route_patch.py`, reached by
`preview_route(include_drc=True)`, and the same function in `apply/engine.py`, reached by applying a
candidate.

Demonstrated, not inferred, on B-021's own fixture: the fill-aware candidate is 8,000 nm, its replay
is 14,000 nm, and `replay.candidate == candidate` is false. `include_drc`, `include_apply_token` and
`include_fill_authority` are independent booleans on the file-backed path with no guard between
them, so `preview_route(include_fill_authority=True, include_drc=True)` refuses a legitimate
candidate with "candidate does not match a deterministic router replay", and
`include_apply_token=True` mints a token whose apply fails the same way.

**This is a real defect in the shipped path, and it is not fixed here.** Closing it means deciding
how a candidate carries its fill provenance across the replay boundary, and `RouteCandidate` is
content-addressed with digests pinned in `tests/test_golden_identities.py` — a contract change with
its own ADR, not a rider on this one. It is filed as its own issue and recorded in R-147. It is
latent rather than observed on the corpus for exactly the reason section 4 gives: the shrink never
engages there. That is luck, not a mitigation.

## Decision

**Keep the model exactly as it is. Do not widen the shrink; complete its gate.**

1. No new fill-aware capability is added, and no obstacle is made smaller than it is today. Issue
   #63's requirement is met by ADR-0039, ADR-0040 and ADR-0070, and the issue should close as
   delivered rather than acquire a change to justify it.
2. The single-layer core gains the two gates the ordered-layer adapter already has, applied in
   order after the revision and zone-backing gates and before any envelope is retired:
   - an island of fewer than three vertices is `unsupported_geometry`, restating the fill parser's
     own floor at a seam the parser does not guard;
   - an island whose bounding box is not contained in the bounding box of a backing zone of the
     same `(net_id, layer_id)` is `unsupported_geometry`, with the same message the layered
     adapter uses.
3. Zone outline bounds are measured **only when evidence is present**. Measuring them
   unconditionally would charge obstacle checks on every zoned board and move the
   `obstacle_budget_exceeded` boundary for callers who never asked for fill-aware routing.

Both new gates are refusal-side. Neither permits any route that is refused today.

## Direction of error

The shrink's soundness rests on ADR-0021 — the cached islands are what KiCad recomputes from this
exact board revision and context — plus completeness of the island set, which only
`run_zone_fill_authority` establishes. Nothing here changes that argument or extends it. The gates
added narrow the set of *evidence* the router will act on, not the obstacles it derives from
evidence it accepts. So the change is exactly this, and the first clause is worth stating without
softening: **some inputs that produced a candidate today will now refuse** — specifically, any
caller supplying an island with a degenerate ring or one that escapes its backing zone. Every input
whose evidence passes both gates is unaffected, obstacle for obstacle and obstacle check for
obstacle check; B-105 confirms that on the three real boards where evidence exists. Refusing more
is the safe direction here because the inputs newly refused are exactly those whose evidence cannot
be related to the copper it was about to retire.

## Evidence

- `tests/test_routing_astar.py::test_verified_fill_escaping_its_zone_outline_is_refused`,
  `::test_a_degenerate_verified_fill_island_is_refused`, and
  `::test_the_containment_gate_leaves_an_unevidenced_route_untouched`, which pins the unevidenced
  fixture's obstacle-check count at 684 rather than merely comparing two calls that would move
  together.
- Mutation evidence under [ADR-0098](0098-reproducible-mutation-evidence.md):
  `docs/mutants/2026-08-13-single-layer-fill-containment.json`, run as
  `.venv/bin/python scripts/mutation_harness.py docs/mutants/2026-08-13-single-layer-fill-containment.json --report report.json`
  on CPython 3.12.13 / macOS arm64. Five mutants, five killed, no survivor and no `not_run`:
  containment deleted, containment inverted, containment made strict (killed by the exact-fit
  island, which proves the containment is closed), the ring floor deleted, and the bounds
  measurement made unconditional.
- B-105 for the corpus measurement and the before/after/before digest bracket. It also records the
  gates' cost on real boards: measured with and without on the three boards where fill evidence
  exists, the verdict counts are identical. That differential bounds a refusal-side gate's **cost**,
  which is the one thing a with/without pair can establish; it is deliberately not offered as the
  argument that the gate is correct. The three tests above are that argument, and the five mutants
  are what make them load-bearing.

## Consequences

Issue #63 closes as already delivered, and the reasoning survives in a place a reader reaches.
The two routers now gate fill evidence alike, so the ordered-layer adapter is no longer the only
one that refuses evidence it cannot relate to a zone. Nothing about the public contract, Board IR,
snapshot digests, candidate identity, apply tokens, or DRC authority changes.

The deferred items ADR-0070 named are untouched and remain open: a public
`preview_layered_route` fill-authority contract with an ADR-0040-style `routing_effect`, exact
polygon collision on the layered lattice, and same-net poured attachment in the layered seam. The
single-layer seam also still performs no *shape* validation of `verified_fill` — no type, ring
ceiling, or vertex-type check — where the layered adapter does; that asymmetry is recorded in
R-147 rather than closed here, because it defends against a caller that is already inside the
process and mypy covers the typed path.

Two further things this record deliberately leaves open, both filed as their own issues so they do
not disappear with #63:

- **The replay gap in section 5.** Until it closes, `include_fill_authority` together with
  `include_drc` or `include_apply_token` is **not a supported combination** and must not be
  described as one.
- **`max_fill_vertices` is miscalibrated.** ADR-0021 set it from CopperTone's 4,314-vertex pour;
  real four-layer boards in the corpus run 50,482–130,305, so the ceiling refuses seven of twelve
  boards *before* freshness is considered and hides the honest `stale_fill` answer behind a
  resource refusal. Raising it needs a B-094-style calibration with an adversarial bound, not a
  number chosen to clear one corpus — and it must not be justified with a route-quality claim,
  because section 4 measured that as zero.

## Alternatives considered

- **Build a fill-aware obstacle model for #63**: rejected. It exists. Building a second one would
  have been the fourth false-premise change this week.
- **Add containment to the single-layer core using exact polygon-in-polygon containment**:
  rejected. The bounding-box test is the *weaker* gate and therefore the one that cannot produce a
  false refusal on honest KiCad geometry; an exact test needs its own obstacle-check budget and
  buys nothing the completeness argument does not already supply.
- **Trust the fill without recomputation, since KiCad's own CLI DRC does**: rejected on the
  evidence in section 2. That KiCad silently consumes a stale fill is the reason to refuse, not a
  precedent to follow.
- **Drop the fill-aware path because it buys nothing measurable on this corpus**: rejected. The
  corpus is one designer's tree at one moment (R-146); zero measured effect there is a fact about
  the corpus, not a demonstration that the capability is useless, and the capability is already
  gated so it costs nothing when unused.

## References

- [ADR-0021: Zone fill authority](0021-zone-fill-authority.md)
- [ADR-0039: Freshness-bound fill islands as routing obstacles](0039-fill-aware-routing-obstacles.md)
- [ADR-0070: Shrink a layered zone envelope only against proved fill](0070-layered-fill-aware-obstacles.md)
- [KiCad 10 command-line interface reference](https://docs.kicad.org/10.0/en/cli/cli.html)
- [KiCad S-expression board format](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html)
- [KiCad `ZONE_FILLER` class reference](https://docs.kicad.org/doxygen/classZONE__FILLER.html)
- [B-105](../ledgers/benchmark-ledger.md)
