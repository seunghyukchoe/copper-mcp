# Every reader of Board IR pad geometry, and the direction of error each one needs

Research date: 2026-08-14. This note is plan item **P3.3a** from the
[post-0.8.0 audit](../audit/2026-08-14-post-0.8.0-audit.md) §P3.3a, and it is the *measure-first*
deliverable that gates **P3.3** (give the pad obstacle envelope an identity distinct from the
attachment core). It supports decision [D-203](../ledgers/decision-ledger.md), risk
[R-156](../ledgers/risk-register.md), benchmark [B-112](../ledgers/benchmark-ledger.md), and it
extends the Context-4 table of
[ADR-0100](../adr/0100-custom-pads-have-an-envelope-and-nowhere-to-put-it.md).

No board content from the surveyed working tree is reproduced. The corpus appears only as counts,
typed refusal codes and locators.

## 0 — Why this note exists, and what it changes

[ADR-0100](../adr/0100-custom-pads-have-an-envelope-and-nowhere-to-put-it.md) refuses the `custom`
pad shape on a stated fact about this repository: a `Pad`'s three geometry fields are read in **both
directions of error**, so no single rectangle can be both the obstacle and the attachment core. Its
Context-4 table names **three** readers. [R-145](../ledgers/risk-register.md) records the gap
honestly: *"ADR-0100's refusal rests on a `Pad` invariant no type enforces: that `_pad_extent` /
`_pad_bounds` and `_pad_core_extent` are the only readers of pad geometry."* Nobody had listed them.

They are not the only readers. This survey finds **23 sites** across **14 modules**, of which
**18 are readers proper**. ADR-0100's conclusion survives — it is strengthened, not weakened — but
three of the fifteen readers it did not name have a direction requirement that is **ambiguous or
already mismatched**, and one of those takes *opposite* directions from a single accessor inside a
single function.

## 1 — Method

Two independent sweeps, because either one alone misses readers. A grep for the value you reason
about cannot see a branch testing its siblings, and a grep for a field name cannot see a caller that
only ever touches a derived accessor.

1. **By field access.** `size_x_nm`, `size_y_nm` and `shape` (including `PadShape` membership tests)
   across `src/`. The two size field names are unique in the tree, so this sweep is exhaustive for
   direct reads.
2. **By accessor call.** Every function whose body appears in sweep 1, then every caller of those,
   then every *transitive* consumer of a stored result — `_PlacedPad.bounds` and `_PlacedPad.core`
   (`placement/legalizer.py:145-150`) are dataclass fields, so a reader of pad geometry can be three
   hops from the field it depends on. This is the sweep that found the mismatches in §4; none of
   those three sites mentions `size_x_nm` or `shape` anywhere.

Scripts and benchmarks that *construct* fixture pads are excluded — they are producers, and they
appear in §6 only where they choose a shape.

**The table below is not a hand-tally.** Sweeps 1 and 2 were re-derived mechanically with an `ast`
walk over every module under `src/` — attribute loads of the three field names, definitions of the
nine accessors, and every call site of those accessors — and the result matches the table exactly:
**9 accessor definitions and 15 accessor call sites**, with field reads confined to the eight
modules listed in Groups A–D. `board_ir/codec.py` is the one reader the AST field sweep cannot see,
because it reads `entry["shape"]` as a subscript on a decoded JSON object rather than as an
attribute; it is D2 below, and it is the reason the sweep was checked against a reading of the
module rather than trusted on its own.

## 2 — Group A: readers that must **over**-approximate (the region must contain the copper)

An obstacle may be enlarged and never shrunk ([ADR-0011](../adr/0011-existing-copper-obstacles.md),
restated by [ADR-0013](../adr/0013-polygon-zone-obstacles.md) and
[ADR-0072](../adr/0072-conservative-arc-track-envelopes.md)). Every reader here would take the
**envelope**.

| # | Site | What it computes | Consumer, and what a pad whose envelope ≠ core would do to it |
|---|---|---|---|
| A1 | `routing/astar.py:697` `_pad_extent` | `⌈size/2⌉` half extents | `astar.py:1805-1820`, the blocking-pad obstacle a track centreline may not enter. Given the core, the router crosses real metal. **Named by ADR-0100.** |
| A2 | `routing/layered_board_adapter.py:261` `_pad_bounds` | the same box | `:764`, inflated per net-class clearance into a layered obstacle. Same failure. **Named by ADR-0100.** |
| A3 | `routing/layered_board_adapter.py:790` | `_pad_bounds(endpoint_pad)` | reserves the endpoint pad against **via** transitions only. Enlarging reserves more and refuses more, which is conservative. **Not named.** |
| A4 | `routing/layered_candidate_verifier.py:200` `_point_in_pad_envelope` | circumradius envelope, `isqrt(hx²+hy²)+1` | `:503` and `:643` → `UNSUPPORTED_ENDPOINT_VIA`. It detects via-in-pad in order to **refuse** a candidate, so enlarging refuses more candidates. Conservative. **Not named.** |
| A5 | `circuit_scene.py:351` `_pad_half_extents` | exact for quarter turns, circumscribed circle otherwise | `:491`, the region-query bounds. Its own docstring at `:480` states the requirement: *"Bounds here decide which objects a region query returns, so they must over-approximate."* **Not named.** |
| A6 | `placement/geometry.py:633` `pad_half_extents` | as A5 | public API (`__all__:837`). **Not named.** |
| A7 | `placement/geometry.py:650` `pad_bounds` | A6 re-centred on an optional origin | public API (`__all__:835`); the module docstring at `:9` states the direction. Feeds A8, B3, and every site in §4. **Not named.** |
| A8 | `placement/view.py:194` | `pad_bounds(pad)` unioned into a footprint hull | `_PlacedFootprint.hull`, the cheap prefilter in `_pad_overlap:477`. Enlarging the hull only admits more pairs to the exact test. Conservative. **Not named.** |

Eight readers. All eight are unambiguous, and A3–A8 are the six ADR-0100 did not list.

## 3 — Group B: readers that must **under**-approximate (the region must be inside the copper)

| # | Site | What it computes | Consumer, and what a pad whose envelope ≠ core would do to it |
|---|---|---|---|
| B1 | `routing/astar.py:946` `_pad_core_extent` | *"half extents of a rectangle strictly inside the pad"* | screened by `_CORE_MODELED_PAD_SHAPES:673` so an unknown shape fails closed. **Named by ADR-0100.** |
| B2 | `routing/astar.py:978` `_pad_cores` | B1 plus a disc's inscribed square | `astar.py:1236` same-net connectivity and `:1576` component roots — the attachment points a search may terminate on. Given the envelope, a route terminates on copper that is not there. |
| B3 | `placement/geometry.py:658` `pad_core` | as B1, screened by `_CORE_MODELLED_SHAPES:34` | `legalizer.py:337` → `_pad_overlap:492`, which returns **`violated`**. This is the ADR-0075 / ADR-0080 parity surface: a core claiming absent copper publishes a legality verdict KiCad does not share. **Not named** — ADR-0100 names `_pad_overlap` as the consumer but not `pad_core` as the reader. |

Three readers, all unambiguous. `_pad_cores`' own docstring already names the failure mode in this
repository's history: *"That claims copper that is not there, which is the one direction an
attachment core may never err in."*

## 4 — Group C: three readers whose direction requirement is **not** satisfied today

This is the finding that decides the gate. All three reach pad geometry only through
`_PlacedPad.bounds`, three hops from any field name, and none is mentioned in ADR-0100 or in
section 5 of the [custom pad envelope note](kicad-custom-pad-envelope-v1.md).

### C1 — `placement/legalizer.py:501-518` `_outline_containment`

```
if not any(rect_inside_ring(pad.bounds, contour.outer) for contour in contours):
    return "violated"
...
if rect_touches_ring(pad.bounds, hole):
    return "violated"
```

It reads an **over**-approximating box and publishes **`violated`**. That is the wrong direction:
`bbox(copper) ⊆ outline` is strictly stronger than `copper ⊆ outline`, so enlarging the box makes
`rect_inside_ring` more likely to be false and `rect_touches_ring` more likely to be true — both of
which publish `violated`. Proving a pad is *outside* the board requires an **under**-approximation.

The custom pad envelope note asserts the opposite at §5: *"Bounds-based verdicts
(`_outline_containment`, `_keepout_respect`) are unaffected, because the union's bounding box **is**
the copper's bounding box."* That is true of the *bound* and does not carry the *verdict*. The
containment predicate is not monotone in the way the sentence assumes. **This note corrects that
claim.**

The defect is latent in *frequency*, not in magnitude. `pad_bounds` represents circles, ovals,
and roundrects by their axis-aligned bounding boxes, so a box corner extends beyond the copper by
up to (√2−1)·r ≈ 0.414× the pad radius for a circle — about 207 µm on a 1 mm-diameter pad, and
`_pad_overlap`'s docstring already reports the class as measured (*"axis-aligned boxes clip on pads
KiCad calls clean"*). `_outline_containment` and `_keepout_respect` can therefore publish `violated`
hundreds of micrometres from copper KiCad calls clear **today**; what P3.3 changes is exposure, not
existence: on the corpus pad the envelope is a step shape's bounding box and the core is an anchor
well under half its area, so re-pointing `pad_bounds` at the envelope widens the false-`violated`
surface from the corner slivers to the whole gap between envelope and core.

### C2 — `placement/legalizer.py:520-536` `_keepout_respect`

```
if rect_touches_ring(pad.bounds, keepout.boundary):
    return "violated"
```

The same shape of error, from the same accessor. Over-approximating a keepout **intrusion** claim
publishes a violation KiCad does not share. It needs the core, or a three-valued answer of the kind
`_pad_overlap` already has.

### C3 — `placement/legalizer.py:603-617` `_resolve_bounds` → `:632-700` `_evaluate_rule`

`_resolve_bounds` returns `pad.bounds` for any rule that names a pad. One accessor, four different
requirements inside one function:

| Rule | Predicate | Direction it needs |
|---|---|---|
| `RegionRule` `keep_in` (`:683`) | `rect_inside_ring(bounds, boundary)` | **under** |
| `RegionRule` `keep_out` (`:685`) | `rect_touches_ring(bounds, boundary)` | **over** |
| `AlignmentRule` (`:653`), `SymmetryRule` (`:659-664`) | `_centre(bounds)` | **exact** — a custom pad's envelope centre is not its pad centre, so an alignment residual computed from the envelope measures the wrong point |
| `EdgeRule` (`:673`) | `bounds[0] - board[0]`, … | **exact** — an offset from a board edge is a position, not a bound |
| `ProximityRule` (`:645`) | `rect_gap(subject, target)` | **over** understates the gap, so it over-reports `satisfied`; arguably the wrong direction for a `violated`/`satisfied` verdict too |

Two of these need opposite directions from the *same call*, and two need a region that is neither.
There is no re-pointing of `_resolve_bounds` that satisfies all five. Splitting it means deciding,
per rule kind, which region a placement rule is *about* — which is a semantic change to the
published placement legality contract, not a mechanical re-point.

## 5 — Group D: four readers for which no region is the right answer

| # | Site | Requirement | The problem |
|---|---|---|---|
| D1 | `board_ir/canonical.py:180-182` `_pad` | **exact** | `shape`, `size_x_nm` and `size_y_nm` enter the canonical payload the snapshot digest is taken over. Any unconditional new key moves **every** board's digest. Byte-identity on the golden fixtures is achievable only with a conditional encoding, which is itself a schema-shaped decision. |
| D2 | `board_ir/codec.py:623-626` and its required-key set `:645-659` | **exact** | the published surface. `schemas/board-ir/0.3.0.schema.json` `$defs/pad` carries `additionalProperties: false` and a 13-key `required` array. A new key is a **widening**; per [ADR-0105](../adr/0105-a-schema-version-moves-with-its-accepted-set.md) the version moves `0.3.0` → `0.4.0`. |
| D3 | `board_ir/types.py:457-475` `Pad.__post_init__` | **ambiguous** | three cross-field invariants bind `size` to something: `drill_x_nm > size_x_nm` raises; `CIRCLE ⇒ size_x == size_y`; `2 × roundrect_radius ≤ min(size)`. Which region? Binding the drill check to the **core** changes acceptance of boards that convert today — the core half extent is `size // 2`, so a pad with odd `size` and `drill == size` passes now and would fail. Binding it to the **envelope** is unsound for a custom pad, whose drill lives in the anchor and whose envelope is much larger. Neither is free. |
| D4 | `circuit_scene.py:608-610` `_pad_object` | **exact / published** | publishes `"size_nm": [size_x_nm, size_y_nm]` and `"shape"` into the scene observation geometry ([ADR-0022](../adr/0022-circuit-scene-observation.md)). Publishing the envelope tells a consumer the copper *is* a rectangle it is not — ADR-0100's own refused claim, exported. Publishing the core under-states real copper on an observation surface. Publishing both widens a **second** published contract, with its own version regime, which P3.3 does not scope. |

## 6 — Group E: carriers and producers (no direction of their own)

These carry or construct pad geometry rather than interpreting it. They are listed because a second
region has to survive each of them, and because E1 is not a `replace`.

| # | Site | Note |
|---|---|---|
| E1 | `placement/legalizer.py:315-329` | rebuilds a `Pad` field-by-field (`spun`) at a new centre and rotation. A second region must be added here explicitly, and must satisfy D3's invariants after the turn. A custom pad's envelope is an AABB in pad-local coordinates, so rotating it needs the same circumscribed-circle treatment A5/A6 already apply. |
| E2 | `placement/route_scoring.py:442` `_project_pad` | `replace(pad, center=…, rotation_udeg=…)` — carries any new field by construction. |
| E3 | `adapters/kicad_placement_patch.py:189-194` | `replace(...)` — as E2. |
| E4 | `adapters/kicad_board_ir.py:2485-2543` | the producer: derives `roundrect_radius_nm` and the size pair. The comment at `:466-477` is where ADR-0100's three-reader claim is restated in code; it is **corrected on this branch** by the addition at `:479-490`, so the source no longer states a set the survey has disproved. |
| E5 | `benchmarks/simple_route_json.py:875-885` | fixture producer; chooses `RECT`/`OVAL` from a benchmark obstacle rectangle. |

## 7 — The count, against what ADR-0100 assumed

| | ADR-0100 Context 4 | This survey |
|---|---:|---:|
| Readers requiring **over** | 2 | 8 |
| Readers requiring **under** | 1 | 3 |
| Readers whose direction is **unsatisfied or ambiguous** | 0 | 3 |
| Readers requiring **exact**, where neither region answers | 0 | 4 |
| Carriers and producers | 0 | 5 |
| **Total sites** | **3** | **23** |

ADR-0100's decision is unaffected and its central argument is strengthened: if one rectangle could
not serve three readers, it certainly cannot serve eighteen. What moves is the **cost of the
revisit**. Condition 2 of the custom pad envelope note's §8 — *"every reader of pad geometry would
have to be re-pointed at whichever of the two it needs"* — was written as if it were bookkeeping over
three call sites. It is not. Three of the readers have no correct re-point, and one of those needs
two opposite directions from a single accessor.

## 8 — What P3.3 would buy, measured

Conversion-only probe of the designer's live tree, read-only, at `4a5fa65`, over the 23 non-`.history`
saves. Neutralisation probes were held in memory in a scratchpad script, never written to the tree
and never applied to a board.

**Today: the custom pad is the live front blocker.** 15 of 23 non-`.history` saves convert. Of the 8
refusals, **6 are the custom pad shape** (5 distinct byte digests), 1 is a net-tie pad group, 1 is a
courtyard topology. Restricted to the benchmark's 18-save selection, this is the recorded **13 of
18**, with **four** custom-pad saves — so the target is real and the `(property …)` blocker
ADR-0100 reported as masked has since been cleared by
[ADR-0099](../adr/0099-pad-fabrication-properties-and-named-pad-refusals.md).

That restriction is arithmetic rather than assertion, and it is worth showing because it is the step
that turns a probe over one tree into the recorded corpus figure. Removing the five saves the
benchmark excludes — three derived stems under `phono-preamp/` and the two superseded saves under
`phono-v2/pcb/`, of which two converted and three refused — gives `23 − 5 = 18` saves and
`15 − 2 = 13` conversions. **18 and 13, matching the recorded figures exactly**, with the five
refusals splitting 4 custom pad + 1 courtyard topology.

**Behind it: a sixth masked blocker, then a seventh.** With the custom pad neutralised in a
throwaway in-memory rewrite — `custom` mapped to `PadShape.RECT`, which is deliberately unsound
geometry used only to see what is behind it — **all six saves refuse immediately on
`pad field 'thermal_bridge_angle' is unsupported`, at the same pad locator.** Neutralising that too:

| Save (digest prefix) | Behind the custom pad | Behind that |
|---|---|---|
| `phono-v2-main` `1b275751` | `thermal_bridge_angle` | converts |
| `phono-v3-main` `9316a32c` | `thermal_bridge_angle` | converts |
| `phono-v2` `549907e9` | `thermal_bridge_angle` | `unsupported.topology: multiple disjoint Edge.Cuts loops` |
| `phono-v3` `b4a95dcd` | `thermal_bridge_angle` | `unsupported.topology: courtyard edges must be non-zero and axis-aligned or 45-degree chamfers` |

So **P3.3, shipped perfectly, converts 0 of 18 additional saves.** P3.3 *plus* a
`thermal_bridge_angle` accept converts **2** (13 → 15 of 18); the remaining two need a further
topology decision each. This is the sixth and seventh instance of one refusal masking the next, and
it is reported rather than counted — the conversion count has not moved and nothing here claims it
has.

## 9 — The gate

The audit's P3.3a gate is: *proceed to P3.3 only if every reader's direction requirement is
unambiguous and no reader needs a semantic change that cannot be proved safe.* **It fails, on four
independent grounds, any one of which is sufficient:**

1. **C3 is unsatisfiable by re-pointing.** `_resolve_bounds` feeds `rect_inside_ring` (needs under)
   and `rect_touches_ring` (needs over) from the same call, plus `_centre` and an edge offset which
   need neither. No choice of region is correct for all five rule kinds.
2. **C1 and C2 are mismatched today and P3.3 widens the mismatch.** Both publish `violated` from an
   over-approximating box. Fixing them is a change to a published legality contract's verdicts on
   boards that already convert; not fixing them ships a false-claim surface proportional to
   envelope-minus-core, which is exactly what ADR-0075 and ADR-0080 forbid.
3. **D4 has no honest answer inside P3.3's scope**, and D3's invariants cannot be re-bound without
   changing which existing boards are accepted.
4. **The measured payoff is zero.** §8. The four target saves refuse one construct later regardless.

**Decision: stop at the survey.** P3.3 is not attempted on this branch. Nothing in the `Pad` type,
the schema, the codec or any reader is changed here.

## 10 — What would have to become true, restated with the real cost

Replacing condition 2 of the custom pad envelope note's §8, which underestimated its own scope:

1. **The three C-group readers need a decision, not a re-point.** `_outline_containment` and
   `_keepout_respect` should become three-valued like `_pad_overlap` — `proven_inside` /
   `violated` / `inconclusive` — which is a placement-contract change worth making on its own merits
   and independently of custom pads. `_resolve_bounds` needs a per-rule-kind region choice.
2. **D4 needs the scene contract to carry both regions**, at its own version, before `Pad` can.
3. **D1 needs a conditional canonical encoding** if existing digests are to hold, and D2 moves
   `board-ir` `0.3.0` → `0.4.0` per ADR-0105 regardless.
4. **The `thermal_bridge_angle` refusal must be resolved first**, or P3.3 cannot be measured at all:
   with it in place, a perfect P3.3 and a broken one produce the same corpus count.

Item 4 is the cheap one and it is the one that should go first. Item 1 is worth doing whether or not
P3.3 is ever attempted, because C1 and C2 are wrong today on boards that convert today.

## 11 — Non-claims

- **Not a claim that the custom pad envelope is underivable.** It is derivable; the
  [custom pad envelope note](kicad-custom-pad-envelope-v1.md) §4 settles that and nothing here
  disturbs it.
- **Not a claim that ADR-0100 is wrong.** Its decision stands and its argument is strengthened. One
  sentence in the supporting research note's §5, about `_outline_containment` and
  `_keepout_respect`, is corrected in §4 above.
- **Not a measurement of C1 or C2's false-violation rate on any real board.** The direction mismatch
  is established by reading the predicates, not by finding a board that trips it. Whether any corpus
  board is currently mis-verdicted is unmeasured.
- **No conversion win is claimed.** The corpus count is 13 of 18 before this branch and 13 of 18
  after it; this branch changes no code.
- **The neutralisation probes in §8 are unsound by construction** and exist only to see what is
  behind a refusal. Nothing from them was committed, written to the corpus, or applied to a board.
