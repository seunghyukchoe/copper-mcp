# KiCad courtyard DRC parity and the cached-courtyard inset

Research date: 2026-08-06. Pinned oracle: KiCad 10.0.5
(`/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`, reported version `10.0.5`).
This note supports [ADR-0075](../adr/0075-courtyard-oracle-parity.md) and issues #72 and #74.
No external code is copied; the quoted lines are cited identifiers and one comment sentence.

It covers exactly two questions — what KiCad's courtyard collision test actually compares, and how
a footprint's several courtyard shapes combine into one region — for the orthogonal Board IR v0.2
subset. It is not evidence for arcs, nonzero `courtyard_clearance` rules, placement apply, or DRC
in general.

## 1. What the courtyard DRC provider compares

`DRC_TEST_PROVIDER_COURTYARD_CLEARANCE` does not look at footprint graphics. It asks each footprint
for a *cached* polygon set and collides the two:

- The front and back courtyards are fetched separately, via `fpA->GetCourtyard( F_CrtYd )` and
  `fpA->GetCourtyard( B_CrtYd )`, and compared only like-for-like. Coincident shapes on opposite
  sides are therefore not a collision — front and back are independent physical layers.
- Each pair is tested with `frontA.Collide( &frontB, clearance, &actual, &pos )`, guarded by
  `OutlineCount() > 0` on both sides. `clearance` comes from
  `EvalRules( COURTYARD_CLEARANCE_CONSTRAINT, fpA, fpB, F_Cu )` and is `0` by default.
- The collision runs over the whole `SHAPE_POLY_SET`, holes included. Nothing filters holes out or
  substitutes outlines for the filled region.

Source: [`pcbnew/drc/drc_test_provider_courtyard_clearance.cpp`, KiCad
10.0.5](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/drc/drc_test_provider_courtyard_clearance.cpp)
(collision at lines 194-229; polygon fetch at 175-187).

## 2. The 5,000 nm inset is real, and it is not a tolerance for overlap

The cache is built in `FOOTPRINT::BuildCourtyardCaches`
([`pcbnew/footprint.cpp:3664-3765`, KiCad
10.0.5](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/footprint.cpp)). Two lines decide
the whole question:

```
3701:    int maxError = pcbIUScale.mmToIU( 0.005 );        // max error for polygonization
3712:    m_courtyard_cache->front.Inflate( -maxError, CORNER_STRATEGY::CHAMFER_ACUTE_CORNERS, maxError );
```

`pcbIUScale` is pcbnew's one-nanometre internal unit, so `maxError` is exactly **5,000 nm**, and
`Inflate` with a negative argument contracts. Line 3741 does the same for the back cache.

The reason is stated in the source itself, immediately above line 3712:

> "Touching courtyards, or courtyards -at- the clearance distance are legal."

followed by the note that `maxError` is used "because that is the allowed deviation when
transforming arcs/circles to polygons". The contraction exists to stop *polygonisation error* from
manufacturing a false overlap between courtyards that merely touch. It is not a licence to overlap
by 10 µm. That distinction matters for CopperMCP: for an exactly orthogonal courtyard there are no
arcs, so the deviation being compensated is zero and the contraction is pure slack.

Because **both** footprints' caches are contracted, a collision needs `2 × 5,000 = 10,000 nm` of
nominal penetration before the contracted boundaries meet.

### Reproduced against the real tool

Two 10 mm front courtyard squares, offset by exact nanometre amounts, isolated
config/home/cache/temp directories, `kicad-cli pcb drc --format json --severity-all`:

| Nominal penetration | KiCad 10.0.5 |
|---|---|
| 1 mm apart | clear |
| 1 nm apart | clear |
| exact edge touch (0) | clear |
| 1 nm | clear |
| 9,998 nm | clear |
| 9,999 nm | clear |
| **10,000 nm** | **`courtyards_overlap`** |
| 10,001 nm | `courtyards_overlap` |
| 1 mm | `courtyards_overlap` |

The first collision is at exactly 10,000 nm, **inclusive** — contracted caches that merely touch
are reported as colliding. This independently reproduces the figure salvaged from the closed #48
(branch `codex/placement-courtyard-legality`, commit `2846cb7`), which is why that figure is
carried forward here rather than re-derived from the earlier note alone.

Corner-only overlap was measured separately, because a shared corner is the case where a
one-dimensional threshold would be wrong:

| Corner overlap (x × y) | KiCad 10.0.5 |
|---|---|
| 9,999 × 9,999 nm | clear |
| **10,000 × 10,000 nm** | **`courtyards_overlap`** |
| 10,001 × 10,000 nm | `courtyards_overlap` |
| 10,000 × 10,001 nm | `courtyards_overlap` |
| 9,999 nm × 10 mm | clear |
| 10,000 nm × 10 mm | `courtyards_overlap` |

The threshold is therefore symmetric and applies independently to both axes, with no degenerate
band at the corner: the rule is exactly *the contracted regions share at least one point*.

### The band CopperMCP does not resolve

Below 10,000 nm the two models genuinely disagree: the raw geometry overlaps and KiCad's contracted
cache does not. The salvaged #48 note also measured a **tiny-shape band** — courtyards whose
dimensions are near the 10,000 nm threshold vanish or behave inconsistently under contraction, with
the transition depending on orientation (a 10,050 nm square behaved differently from a
10,050 nm × 1 mm rectangle). That band was *not* re-derived here and is deliberately not modelled;
it is a declared non-claim rather than a fitted constant.

## 3. A footprint's courtyard rings are one even-odd region

This is the answer to issue #74, and it is not a modelling choice — it is what KiCad computes.

`ConvertOutlineToPolygon` builds a contour hierarchy before assembling the polygon set
([`pcbnew/convert_shape_list_to_polygon.cpp`, KiCad
10.0.5](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/convert_shape_list_to_polygon.cpp)):

- `buildContourHierarchy` (lines 373-400) counts, for each contour, how many *other* contours
  contain its first point (`parentCandidate.PointInside( firstPt, 0, true )`).
- `addOutlinesToPolygon` (line 411): `if( parentIndexes.size() % 2 == 0 )` — an **even** number of
  parents makes the contour a top-level outline (`AddOutline`).
- `addHolesToPolygon` (lines 446-456): `if( parentIndexes.size() % 2 == 1 )` — an **odd** number of
  parents makes it a hole, added via `AddHole` to the parent that has exactly one fewer parents.
  The source comment reads: "Odd number of parents; we're a hole in the parent which has one fewer
  parents".

That is strict even-odd nesting by depth. A ring drawn inside another ring on the same courtyard
layer is a **hole**, and the interior of that hole is not courtyard material at all.

Two consequences follow, and both were confirmed against the real tool:

1. A footprint whose courtyard sits entirely inside another footprint's donut hole does **not**
   collide. Measured on a purpose-built board (outer 5–35 mm wall, 13–27 mm × 10–20 mm hole, second
   footprint's courtyard fully inside the hole): **0 violations**.
2. The same second footprint moved onto the annulus **does** collide: `courtyards_overlap`.

The committed fixture `tests/fixtures/board-ir-v0.2/courtyard-donut.kicad_pcb` pins case 1 and real
KiCad reports zero violations and zero unconnected items on it.

Note the interaction with §2: contraction of a region *with a hole* shrinks the outer boundary
inward **and grows the hole**, because both move into the solid. A part sitting in a donut hole
therefore gains clearance under contraction rather than losing it, so the two corrections point the
same way and cannot mask one another.

Contours that *properly intersect* rather than nest take a different path in KiCad
(`aHasMalformedOverlap`, lines 466-491, using `BooleanSubtract`/`BooleanAdd`). That path is not
modelled and is a declared non-claim.

## 4. IPC-7351 courtyard conventions

IPC-7351B defines the courtyard as *the smallest rectangular area that provides a minimum
electrical and mechanical clearance (courtyard excess) around the combined component body and land
pattern boundaries*. The **courtyard excess** is the margin between the rectangle circumscribing the
land pattern and body and the courtyard's outer boundary, and the density-level suffixes are
verbatim from the IPC-7351B naming convention: `M` — Most Material Condition (Density Level A),
`N` — Nominal (Level B), `L` — Least (Level C).

**The excess values themselves are not settled here, and are not relied on.** Published figures
conflict: secondary sources commonly cite Least 0.10–0.12 mm / Nominal 0.25 mm / Most 0.50 mm, while
Tom Hausherr, the IPC-7351 author, states 0.10 / 0.20 / 0.40 mm on the PCB Libraries forum. The
normative table is paywalled and was not read. Only the order of magnitude is load-bearing below,
and every candidate figure agrees on it.

Two points matter for this work:

- The courtyard excess is **tenths of a millimetre** — four to five orders of magnitude larger than
  KiCad's 5,000 nm cache inset. The inset is invisible at the scale real footprints are drawn, which
  is why the disagreement band went unnoticed, and why resolving it silently in either direction
  would be wrong. A footprint author working to IPC never intends a 9,999 nm courtyard
  interference, so neither "clear" nor "collision" is safe to assert on their behalf.
- IPC's courtyard is described as a **single rectangle**; the standard never contemplates an annular
  courtyard. That is an inference from the definition's wording, not a verified prohibition — but it
  does mean the donut is a CAD construct rather than an IPC one.

### Donut courtyards are real, and they ship in the official KiCad library

This is not a hypothetical topology. The current KiCad footprint library
([gitlab.com/kicad/libraries/kicad-footprints](https://gitlab.com/kicad/libraries/kicad-footprints))
contains **31 footprints with two nested closed shapes on `F.CrtYd`** — 30 in `RF_Shielding.pretty`
(Laird `97-200x` and `BMI-S-1xx/2xx`, Würth `36103xxx` and `36503xxx`) and one in `Module.pretty`.
29 of the 31 were already present in the 2020 library snapshot, so this is long-standing practice.

Verified directly from
`RF_Shielding.pretty/Wuerth_36103205_20x20mm.kicad_mod` ("WE-SHC Shielding Cabinet SMD 20x20mm"):

| Shape | Layer | Extent |
|---|---|---|
| `fp_rect (start -11 -11) (end 11 11)` | `F.CrtYd` | 22 × 22 mm — outer wall |
| `fp_rect (start -9.5 -9.5) (end 9.5 9.5)` | `F.CrtYd` | 19 × 19 mm — inner hole |
| `fp_rect (start -10 -10) (end 10 10)` | `F.Fab` | 20 × 20 mm — can body |

The inner ring sits 0.5 mm *inside* the body outline: it is the shield can's inner wall, and the
19 × 19 mm interior is deliberately excluded from the courtyard so that other components can sit
**under** the can. Treating those two rings as solids makes CopperMCP refuse every part placed under
every RF shield in the library — which is precisely the defect issue #74 reports.

KiCad supports this at three independent levels: the file format, the polygon engine
(`ConvertOutlineToPolygon` is documented as building "a polygon set with holes … one or more
top-level closed outlines with zero or more holes in each"), and the library checker — KLC rule F5.3
tests courtyard closure by vertex-degree parity and never counts outlines, so disjoint *and* nested
loops both pass. The KLC test corpus ships a deliberate pass fixture named
`Pass__F5.3__disjoint_courtyard.kicad_mod`.

The committed fixture `tests/fixtures/board-ir-v0.2/courtyard-donut.kicad_pcb` is original — modelled
on this arrangement, not copied from the library.

Sources: [IPC-7351B footprint naming
convention](https://www.cskl.de/fileadmin/csk/dokumente/produkte/pcbl/IPC-7351B_Footprint_Naming_Convention.pdf),
[Eurocircuits on courtyards](https://www.eurocircuits.com/blog/good-courtyards-make-good-neighbours/),
[PCB Libraries forum — courtyard
excess](https://www.pcblibraries.com/forum/placement-courtyard-excess_topic3372.html),
[KiCad footprint library](https://gitlab.com/kicad/libraries/kicad-footprints),
[`ConvertOutlineToPolygon` reference](https://docs.kicad.org/doxygen/convert__shape__list__to__polygon_8h.html),
[KiCad 10.0 PCB Editor](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#design-rule-checking).

## 5. What CopperMCP does with all of this

The predicate is `orthogonal_courtyard_region_overlap` in
`src/copper_mcp/placement/geometry.py`. It pools every ring of one footprint into a single even-odd
scanline region (§3), then looks for a witness rectangle at least 10,000 nm on **both** axes inside
the shared area (§2). The result is three-valued:

| Outcome | Claim |
|---|---|
| `proven_clear` | The raw regions share no positive area. Contraction only shrinks a region, so this is a proof for any ring shape, not just the certified subset. |
| `violated` | The shared area contains a rectangle at least 10,000 nm on both sides, so the contracted regions provably meet. Exact parity with KiCad. |
| `inconclusive` | The regions overlap but no such witness exists — the sub-threshold band, or a shared shape this scan does not certify. Neither claim is made. |

The witness search is sound but not complete: it assembles a rectangle within a single horizontal
strip, so a shared rectangle straddling several strips is not found. That can only turn a `violated`
into an `inconclusive`, never the reverse.

### Measured agreement

`scripts/benchmark_courtyard_oracle_parity.py` runs 15 cases against real `kicad-cli` 10.0.5 — 11
penetration offsets, the donut fixture, both inset-boundary fixtures, and the CopperTone buffer
board — and records **10/15 exact parity, 5/15 conceded as `inconclusive`, 0 contradictions, 0
false-positive violations, 0 false-negative clears**. The five conceded cases are exactly the
sub-threshold band of §2. The script refuses to emit an artifact if any contradiction appears.

## 6. What this note does not claim

- That the tiny-shape band (courtyards near or below 10,000 nm in a dimension) is modelled. It is
  reported `inconclusive`.
- That arcs, curves, circles, or non-orthogonal courtyard geometry are supported; Board IR rejects
  them before a placement view exists.
- That same-footprint rings which touch or properly intersect are handled. Only disjoint and
  strictly nested rings are certified.
- That a nonzero or negative custom `courtyard_clearance` rule is loaded. The model assumes KiCad's
  zero default.
- That `missing_courtyard`, `malformed_courtyard`, placement apply, or any other DRC rule is
  covered.
