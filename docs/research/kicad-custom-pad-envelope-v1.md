# KiCad custom pads: is there an envelope, and can Board IR carry it?

Research date: 2026-08-13. This note supports
[issue #153](https://github.com/seunghyukchoe/copper-mcp/issues/153), decision
[D-190](../ledgers/decision-ledger.md), risk [R-145](../ledgers/risk-register.md) and
[ADR-0099](../adr/0099-custom-pads-have-an-envelope-and-nowhere-to-put-it.md).

A pad is copper. [ADR-0011](../adr/0011-existing-copper-obstacles.md)'s direction-of-error rule — restated
by [ADR-0013](../adr/0013-polygon-zone-obstacles.md) and
[ADR-0072](../adr/0072-conservative-arc-track-envelopes.md) — says an obstacle may only be
**over**-approximated. So the question this note asks first is
[ADR-0095](../adr/0095-copper-text-has-no-derivable-envelope.md)'s question: **is there a region,
derivable from the board document, that provably contains every bit of copper a custom pad puts
down?**

The answer here is **yes**, and that is the opposite of ADR-0095's answer. The refusal in ADR-0099
is therefore not ADR-0095's refusal, and this note exists mostly to establish the *second*
question, which is the one that decides the outcome: **is there somewhere in Board IR to put that
envelope?** A `Pad` is read in both directions of error from one set of fields, so the answer to
the second question is no.

No board content from the surveyed working tree is reproduced here. The corpus appears only as
counts and typed refusal codes. Every measured figure comes from a synthetic board authored for
this note, reproduced in full in section 7.

## 1 — The instrument

`kicad-cli` 10.0.5, the same oracle [ADR-0075](../adr/0075-courtyard-oracle-parity.md) uses for
courtyard parity and the same one the copper-text note measures against:

```sh
kicad-cli pcb export svg --layers F.Cu --exclude-drawing-sheet --page-size-mode 2 \
    -o out.svg board.kicad_pcb
```

Source citations are against the `10.0.5` tag of KiCad's own tree. A code-symbol citation in this
repository is checked by review and by nothing else; where a claim could be measured instead of
cited, it is measured, and both are given.

## 2 — What the corpus carries

Read-only survey of the 34 `.kicad_pcb` documents under `~/Desktop/13_Audio`, the same live working
tree the [board-groups note](kicad-board-groups-v1.md) surveys. **Eight** carry at least one
`smd custom` pad. Restricted to the 18 boards the conversion benchmark selects (`.history` and
derived stems excluded), **three** refuse for it and nothing earlier.

Across the whole tree the `(primitives …)` blocks contain exactly one primitive head:

| Primitive head | Occurrences in a pad |
|---|---:|
| `gr_poly` | 8 |
| everything else in KiCad's vocabulary | 0 |

Every one is `(width 0) (fill yes)` — a filled polygon with no stroke — and every one comes from
one library footprint. All eight `(options …)` blocks read `(clearance outline) (anchor rect)`.

**That table is the reason this note does not propose a bounded primitive subset, and it cuts the
opposite way from how it first reads.** A subset admitting only `gr_poly` would cover every custom
pad in this corpus. It would also be an accepted subset chosen by what one private tree happens to
contain, and section 5 shows the blocker is not the primitive vocabulary at all — so the subset
would buy nothing and would have to be un-shipped once the real blocker was understood.

## 3 — The primitives are unioned with the anchor, not substituted for it

This is issue #153's load-bearing factual question, and it is settled twice, independently.

**From the source.** `PAD::MergePrimitivesAsPolygon` (`pcbnew/pad.cpp:3275-3315`) seeds the merged
polygon with the anchor shape — a `SHAPE_RECT` of `GetSize(aLayer)` at the origin for
`PAD_SHAPE::RECTANGLE`, a circle of radius `size.x / 2` otherwise (`pad.cpp:3284-3296`) — then
transforms every non-proxy primitive into a second polyset and merges it in with
`aMergedPolygon->BooleanAdd( polyset )` (`pad.cpp:3312`). `BooleanAdd` is a union.

The same thing is built a second way for collision: `PAD::buildEffectiveShape` sets
`effectiveShape = GetAnchorPadShape( aLayer )` when the shape is `CUSTOM` (`pad.cpp:1155-1159`),
adds that shape through the ordinary switch, and then — after the switch, unconditionally for a
custom pad — adds *every* non-proxy primitive on top (`pad.cpp:1278-1292`).

**From the plotter.** A synthetic board (section 7.1) whose custom pad has a `2 × 2 mm` `rect`
anchor at the pad origin and one `gr_poly` primitive spanning `x ∈ [6, 10] mm` — starting 5 mm past
the anchor's edge and sharing no point with it — plots as **two** filled paths, at `x ∈ [0, 2]` and `x ∈ [7, 11]`
in the cropped page frame, in a page 10.9982 mm wide. The anchor is plotted. It is not replaced.

So a converter that read `(anchor rect)` and `size` and stopped would drop real copper. On the
corpus pad it would drop a region longer than the anchor and wider than it in `y`, abutting the
anchor's edge.

One thing the disjoint case is *not*: legal. `PAD::CheckPad` runs `MergePrimitivesAsPolygon` and
reports `DRCE_PADSTACK_INVALID` — "custom pad shape must resolve to a single polygon" — when the
result has more than one outline (`pad.cpp:3119-3127`). KiCad plots it anyway. The experiment is
therefore a probe of the shape model, not a claim that boards like it are well-formed.

## 4 — Every primitive admits an exact integer-nanometre containing box

KiCad's primitive vocabulary inside `(primitives …)` is closed and short. `parsePAD`'s
`T_primitives` arm (`pcb_io_kicad_sexpr_parser.cpp:6323-6358`) accepts exactly `gr_line`, `gr_arc`,
`gr_circle`, `gr_rect`, `gr_poly`, `gr_curve`, `gr_bbox` and `gr_vector`, and calls `Expecting(…)`
on anything else. The last two are marked `SetIsProxyItem()` at parse time, and both shape builders
in section 3 skip proxy items, so neither contributes copper: `gr_bbox` is the pad-number display
box and `gr_vector` is a thermal-spoke template.

Writing `w` for the primitive's `(width …)` in nanometres and `⊕ r` for "inflate the box by `r` on
every side", each remaining head has a containing box derivable from the document alone. KiCad
strokes primitives with round caps and joins, so a stroked shape is exactly the Minkowski sum of
its skeleton with a disc of radius `w / 2`, and inflating the skeleton's box by `⌈w / 2⌉` contains
it.

| Head | Containing box, pad-local | Why it contains |
|---|---|---|
| `gr_line` | `AABB(start, end) ⊕ ⌈w/2⌉` | a segment lies in the box of its endpoints |
| `gr_rect` | `AABB(start, end) ⊕ ⌈w/2⌉` | filled or stroked, the rectangle is that box |
| `gr_poly` | `AABB(pts) ⊕ ⌈w/2⌉` | a filled simple polygon lies in its vertices' hull, and the hull lies in their box |
| `gr_circle` | `centre ± (⌈√((end−centre)²)⌉ + ⌈w/2⌉)` | a disc lies in the square of its radius; the square root rounds **up** |
| `gr_arc` | circumcentre of `(start, mid, end)` `± (⌈R⌉ + ⌈w/2⌉)` | an arc lies on its circle, and the circle lies in that square |
| `gr_curve` | `AABB(P₀…P₃) ⊕ ⌈w/2⌉` | the Bézier convex-hull property, proved below |
| `gr_bbox`, `gr_vector` | empty | proxy items; `pad.cpp:3301-3305` and `pad.cpp:1280-1283` skip them |
| anchor `rect` | `± ⌈size/2⌉` | `pad.cpp:3287-3290` |
| anchor `circle` | `± ⌈size.x/2⌉` | `pad.cpp:3294-3295` |

Two degenerate cases are named rather than glossed. `gr_arc`'s circumcentre is undefined when its
three points are collinear, in which case the arc *is* a segment and `AABB(start, mid, end)` bounds
it — so the row above needs a collinearity branch, not a different theorem. And `gr_circle`'s `end`
is a point on the circumference, so its radius is a square root; rounding it up is what keeps the
box containing, and is the same outward rounding `_roundrect_radius` already reports through
`max_roundrect_rounding_nm`.

Every entry is an integer nanometre box obtained by rounding **outward**, so no float participates
in the predicate and no rounding shrinks a region — the two rules `_pad_extent` and `_pad_bounds`
already keep for the shapes Board IR does model.

**The Bézier bound is a theorem, not an assertion.** KiCad's `gr_curve` is a cubic Bézier over four
control points; the parser routes `T_gr_curve` through `parsePCB_SHAPE`, which stores `SHAPE_T::
BEZIER` with `(pts (xy …) (xy …) (xy …) (xy …))`. Its parameterisation is

```text
B(t) = Σ(i = 0..3) C(3, i) · (1 − t)^(3 − i) · t^i · P_i,   t ∈ [0, 1]
```

For `t ∈ [0, 1]` every coefficient `C(3, i)·(1 − t)^(3 − i)·t^i` is non-negative, and by the
binomial theorem they sum to `((1 − t) + t)³ = 1`. So `B(t)` is a *convex combination* of
`P₀ … P₃` at every `t`, hence `B([0, 1]) ⊆ conv{P₀ … P₃} ⊆ AABB{P₀ … P₃}`. The control polygon is a
provable bound, and no property of the particular control points is used.

Measured, so that the parameterisation claim is not taken on trust: a custom pad carrying the
single primitive `(gr_curve (pts (xy 4 0) (xy 6 -8) (xy 10 8) (xy 12 0)) (width 0.2))` (section
7.2) plots into a page **4.8006 mm** tall. The control points span `y ∈ [−8, 8]`, 16 mm; the curve
plus its 0.2 mm stroke occupies 4.8 mm of it. Contained, and loosely — which is what a bound is
allowed to be for an obstacle, and is exactly ADR-0013's bargain.

**So this is not ADR-0095's case.** Copper text refuses because the plotted glyphs are not a
function of the board document's bytes — they depend on a project file, a host font cache, a
compiled-in glyph table and an unbounded string. A custom pad's every primitive carries exact
millimetre literals in the document, and the containing box above is a closed-form function of
them.

## 5 — Where the envelope cannot go: a `Pad` is read in both directions at once

Board IR's `Pad` describes its copper with `shape`, `size_x_nm`, `size_y_nm`, `center` and
`rotation_udeg` (`board_ir/types.py:423-455`). Three call sites read those same fields, and they do
not agree about which way to err:

| Reader | Direction required | What it computes |
|---|---|---|
| `routing/astar.py::_pad_extent` | **over** — must contain the copper | `⌈size/2⌉` half extents, the forbidden region for a track centreline |
| `routing/layered_board_adapter.py::_pad_bounds` | **over** | the same box, inflated per net-class clearance into a layered obstacle |
| `routing/astar.py::_pad_core_extent` | **under** — must be inside the copper | "half extents of a rectangle strictly inside the pad", the attachment core a search may terminate on |

For `PadShape.RECT` the first and the third collapse onto one rectangle: `_pad_extent` returns
`(size + 1) // 2` and `_pad_core_extent` returns `size // 2`, which differ by at most one nanometre
per axis. So a `Pad` with `shape=RECT` is not a bound in either direction — it is an **assertion
that the pad's copper *is* that axis-aligned rectangle**.

That is what forecloses every way of writing a custom pad as a `Pad`:

- **Size it to the union's bounding box.** The obstacle becomes sound — the box is exactly
  `AABB(anchor ∪ primitives)` from section 4, and it contains all the copper. The attachment core
  becomes *false*: on the corpus pad the union is a step shape whose bounding box includes a region
  above and below the anchor that is not copper, and a route terminating there attaches to nothing.
  `_pad_cores`' own docstring already names this failure, in this repository, about a
  ratio-0.5 roundrect: "That claims copper that is not there, which is the one direction an
  attachment core may never err in."
- **Size it to the anchor.** The core becomes sound — the anchor really is copper, section 3 proves
  it — and the obstacle silently loses every primitive. That is the forbidden direction: a router
  would route through real metal.
- **Any other size.** A single rectangle `R` would have to satisfy `copper ⊆ R` and `R ⊆ copper`,
  so `R = copper`, so the pad's copper would have to be exactly an axis-aligned rectangle at the
  pad's centre. A custom pad *can* be authored that way, but then `custom` was not needed to
  express it, and no such pad exists in this corpus: all eight are the same step shape, an anchor
  abutting a polygon that is both longer than it and wider than it in `y`.
- **Any other `PadShape` member.** `CIRCLE`, `OVAL` and `ROUNDRECT` all derive their core from
  `size` too, and each core is a region the model then asserts is copper. None of them has a
  "core is empty" spelling.

The published legality surface makes the same distinction load-bearing rather than internal.
`placement/legalizer.py::_pad_overlap` returns `violated` when two pads' **cores** overlap. A core
that claims copper that is not there would publish a `violated` verdict KiCad does not share, which
[ADR-0075](../adr/0075-courtyard-oracle-parity.md) and
[ADR-0080](../adr/0080-chamfered-and-circular-courtyards.md) classify as a false claim rather than a
conservative one. Bounds-based verdicts (`_outline_containment`, `_keepout_respect`) are unaffected,
because the union's bounding box *is* the copper's bounding box — the defect is one-sided and it is
on the side that cannot be widened.

## 6 — Two sub-questions that do not change the outcome, answered anyway

**6.1 `(options (clearance outline|convexhull))` changes zone fill, not pad copper, and only
upward.** The token is parsed into `SetCustomShapeInZoneOpt`
(`pcb_io_kicad_sexpr_parser.cpp:6522-6541`) and consumed at exactly one site in KiCad 10.0.5:
`ZONE_FILLER::addKnockout` (`zone_filler.cpp:1684-1712`). Both branches call the pad's own
`TransformShapeToPolygon( poly, aLayer, aGap, … )` with the same `aGap`, so the pad's clearance
region is identical either way; the option decides only whether the *hole punched in the zone* is
that inflated outline or its convex hull. A convex hull contains the set it is built from, so
`convexhull` can only make the knockout **larger** — remove more zone copper — never smaller.

So the answer to issue #153's third question is: **no, discarding the value cannot under-state a
clearance anywhere.** It cannot shrink the pad's clearance region at all, and the only thing it
does change is zone copper, which Board IR models as the zone *outline* — an over-approximation of
any valid fill (ADR-0013), and one a smaller knockout cannot escape. This is recorded because it
had to be checked, not because it rescues anything: the pad refuses on its shape, one level above
where this token is read.

**6.2 ADR-0091 is not weakened, and it gains one construct it did not consider.**
[ADR-0091](../adr/0091-attaching-pad-zone-connect-overrides.md) accepts `zone_connect` `1`, `2` and
`3` as modelled-as-nothing, and states that this is "load-bearing on one property … pad-to-pour
connectivity is derived only from verified fill geometry". A custom pad does not disturb that
property, and it does not narrow KiCad's own connectivity either: the anchor is *not* a reduced
connection region in KiCad 10, because `buildEffectiveShape` adds the primitives on top of it
(section 3) and collision therefore sees the whole union.

What a custom pad adds is `gr_vector` — a per-pad thermal **spoke template**, which is attachment
geometry a non-custom pad has no way to carry. It is a proxy item that contributes no copper, and
discarding it is inert under exactly ADR-0091's argument and no other: attachment comes only from
verified fill. Had Board IR ever inferred pad-to-pour attachment from a same-net pad inside a zone
outline — the future surface ADR-0091 names as the thing that would make it unsound — a discarded
spoke template would be a second way to get it wrong.

One ordering consequence, recorded so it is not mistaken for a widening: with the shape decided
before the field checks, a custom pad carrying `zone_connect 0` now refuses under the shape's name
rather than under ADR-0091's. Both refuse. The set of refused boards does not move.

## 7 — The synthetic boards

### 7.1 Disjoint anchor and primitive

```
(kicad_pcb
	(version 20241229)
	(generator "pcbnew")
	(generator_version "10.0")
	(general (thickness 1.6) (legacy_teardrops no))
	(paper "A4")
	(layers
		(0 "F.Cu" signal)
		(2 "B.Cu" signal)
		(9 "F.Paste" user)
		(11 "F.Mask" user)
		(44 "Edge.Cuts" user)
	)
	(setup (pad_to_mask_clearance 0))
	(footprint "T:T"
		(layer "F.Cu")
		(uuid "00000000-0000-0000-0000-000000000001")
		(at 20 20)
		(pad "1" smd custom
			(at 0 0)
			(size 2 2)
			(layers "F.Cu")
			(options (clearance outline) (anchor rect))
			(primitives
				(gr_poly (pts (xy 6 -1) (xy 10 -1) (xy 10 1) (xy 6 1)) (width 0) (fill yes))
			)
			(uuid "00000000-0000-0000-0000-000000000002")
		)
	)
)
```

Plotted page: `10.9982 mm × 1.9812 mm`, two `<path>` elements, at `x ∈ [0, 2]` and `x ∈ [7, 11]`.
The board coordinates are `x ∈ [19, 21]` for the anchor and `x ∈ [26, 30]` for the primitive, so
both survive the crop and both are copper.

### 7.2 A Bézier primitive

The same board with the `(primitives …)` block replaced by

```
			(primitives
				(gr_curve (pts (xy 4 0) (xy 6 -8) (xy 10 8) (xy 12 0)) (width 0.2))
			)
```

Plotted page: `13.0810 mm × 4.8006 mm`, two `<path>` elements. The control polygon's `y` span is
16 mm and the plotted copper's is 4.8006 mm, so the bound holds with 11.2 mm to spare.

## 8 — What would have to become true

Three conditions, and all three are required. They are listed so ADR-0099 can be revisited on
evidence rather than on appetite.

1. **`Pad` would have to carry its obstacle envelope separately from its attachment core** — two
   regions, or one polygon outline plus a derived core — because section 5 shows one rectangle
   cannot be both. This is a `Pad` schema widening at a published version, which is precisely the
   cost [ADR-0096](../adr/0096-edge-connector-pads-convert-as-smd.md) declined to pay for a much
   smaller distinction: "A consumer holding the published `0.2.0` schema was promised a closed
   three-value domain; it would reject a snapshot CopperMCP calls valid, silently, at a version
   that did not move."
2. **Every reader of pad geometry would have to be re-pointed at whichever of the two it needs**,
   and a reader that is added later and reads the wrong one must fail closed rather than silently
   pick the obstacle box as an attachment core. `_pad_core_extent`'s existing screen against a
   named `_CORE_MODELED_PAD_SHAPES` set is the pattern; it would have to become the rule.
3. **The attachment core would have to be derived, not assumed.** A largest-inscribed axis-aligned
   rectangle inside a union of an anchor and arbitrary primitives is a real computation with a real
   budget, and it has to be exact in integer nanometres to satisfy this project's own geometry
   rule. Assuming the anchor is the core is *sound* — the anchor is copper — but on the corpus pad
   it is well under half the pad's area, so a router would be told it may attach to a minority of the
   copper that is there. That is the safe direction, and it is worth having; it is not worth having
   at the price of conditions 1 and 2 until something needs it.

Condition 3 is the one that is easy to skip and cheap to get wrong in the other direction. Nothing
here is blocked on new KiCad knowledge — section 4 settles the geometry completely — so if this is
revisited, it will be a Board IR contract decision and not a research task.
