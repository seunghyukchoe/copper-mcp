# KiCad copper text: is there an envelope derivable from the board document?

Research date: 2026-08-12. This note supports
[issue #141](https://github.com/seunghyukchoe/copper-mcp/issues/141), decision
[D-185](../ledgers/decision-ledger.md), risk [R-140](../ledgers/risk-register.md), security review
[SEC-136](../ledgers/security-ledger.md) and
[ADR-0095](../adr/0095-copper-text-has-no-derivable-envelope.md).

The question is **not** "how does CopperMCP render text". Copper lettering is real copper, so it is
an obstacle, and [ADR-0011](../adr/0011-existing-copper-obstacles.md)'s direction-of-error rule
says an obstacle may only be **over**-approximated. Dropping it is the one forbidden outcome: a
router would happily propose a track through a letter. So the only question that matters is
whether some region **provably containing** every plotted glyph can be derived from what the board
document carries — `at`, `size`, `thickness`, the string, and the `(effects …)` modifiers.
[ADR-0013](../adr/0013-polygon-zone-obstacles.md) is the template: it does not model a zone's fill,
it models a region the fill provably cannot leave.

The answer is no, for four independent reasons, and each one is measured rather than argued.

No board content from the surveyed working tree is reproduced here. The corpus appears only as
counts; every measured figure below comes from a synthetic board authored for this note, and those
boards are reproduced in full in section 6.

## 1 — The instrument

`kicad-cli` 10.0.5 — the same oracle
[ADR-0075](../adr/0075-courtyard-oracle-parity.md) uses for courtyard parity and the same build
the pad-rotation semantics were established against:

```sh
kicad-cli pcb export svg --layers F.Cu --exclude-drawing-sheet --page-size-mode 2 \
    -o out.svg board.kicad_pcb
```

The plotted copper is read off the SVG directly. Stroke text plots as `<path>` centrelines inside a
group carrying `stroke-linecap:round; stroke-linejoin:round`, so the copper each path occupies is
exactly its centreline Minkowski-summed with a disc of the group's `stroke-width`; the **ink box**
below is the union of every path's coordinates inflated by half that width. Outline (TrueType) text
plots as filled `<path>` polygons instead, with no stroke, and is measured from its vertices. KiCad
also emits an invisible `<text>` element carrying its own `textLength`, which is KiCad's advance
width for the run and is quoted where it is useful.

Two sanity checks that the instrument reads what it claims: text at `(size 1.27 1.27)` with no
`(thickness …)` plots at `stroke-width` `0.1588` mm, which is `1.27 / 8` to the emitted precision
and matches `GetPenSizeForNormal`; the same text with `(bold yes)` plots at `0.254` mm, which is
`1.27 / 5` and matches `GetPenSizeForBold`.

## 2 — What the corpus actually carries

Read-only survey of all 33 `.kicad_pcb` documents under `~/Desktop/13_Audio` (the same live
working tree the [board-groups note](kicad-board-groups-v1.md) surveys), counting root-level
graphic children:

| Root graphic head | Occurrences |
|---|---:|
| `gr_line` | 172 |
| `gr_text` | 71 |
| `gr_text_box` | 0 |

Of the 71 root texts, **69 are on `F.SilkS`** and **2 are on `F.Cu`** — the latter being one live
save and its own `.history` copy of the same board, so **one board in the corpus is refused for
this and nothing else**. Across all 71, the `(effects (font …))` children that appear are `size`
(71), `thickness` (69) and `bold` (4). `(face …)` occurs **zero** times in the whole tree, and no
root text string carries `${…}` — though the tree does carry 9,162 `${` occurrences elsewhere,
4,714 of them `${REFERENCE}` in footprint fields and 4,448 `${KICAD10_3DMODEL_DIR}` in model
paths, so the mechanism is ordinary on these boards and it is only its appearance in *root* text
that is currently absent.

That paragraph is why this note exists rather than a patch, and it cuts the opposite way from how
it first reads. It is tempting to take "no `face`, no `${…}` in a root text" as "the hard cases do
not happen" — but text variables are already in daily use on these very boards, four thousand
`${REFERENCE}` fields deep, so nothing about this designer's habits keeps one out of a copper
legend. And the corpus is one project family in any case; it is not the accepted document set.
Board IR's accepted set is defined by what the adapter admits, and anything it admits it must be
correct for.

## 3 — What the other inputs do, so that the gap is stated exactly

Two of the four inputs are *not* where the problem is. That is a weaker statement than "settled",
and the difference matters — see the caveat at the end of this section.

**The pen width has a bound, and its status is exactly the status of every other read of one
build's source.** `EDA_TEXT::GetEffectiveTextPenWidth` ends with
`penWidth = ClampTextPenSize( penWidth, GetTextSize() )`, and
`ClampTextPenSize( aPenSize, aSize, aStrict )` is
`std::min( aPenSize, KiROUND( aSize * (aStrict ? 0.18 : 0.25) ) )` over
`std::min( |size.x|, |size.y| )`. So the plotted stroke width cannot exceed a quarter of the
smaller text dimension, whatever `(thickness …)` claims. Measured at `(size 1.27 1.27)`: declared
`0.5` and declared `2.0` **both** plot at `0.3175` mm, exactly `1.27 / 4`. A declared thickness is
therefore an over-estimate of the plotted pen, and an absent one is covered by the same clamp.

**`at`, the rotation and `justify` place the box, and do so predictably.** Measured on `mmmm` at
`(size 1.27 1.27)` anchored at `(50, 30)`: `justify left` puts the ink at x ∈ [50.33, 56.65],
`justify right` at [43.35, 49.67], `justify top` at y ∈ [30.40, 31.40], `justify bottom` at
[28.91, 29.92]; `(at 50 30 90)` rotates the ink about the anchor and swaps the extents exactly,
6.3274 × 1.0055 mm becoming 1.0055 × 6.3274 mm. Nothing surprising is hiding here.

What is not derivable is the only thing that matters: **which glyphs get plotted, and how far each
one reaches**.

**The caveat, stated rather than left implicit.** "The clamp only lowers" is a fact about KiCad
10.0.5's source, read from that source — which is *the same class of fact* as "no Newstroke glyph
advances more than 1.5083 × size", and §5's condition 4 refuses to rely on that class without a
mechanism that makes a build mismatch refuse. Calling the pen width "settled" while refusing the
glyph extents on those grounds would be claiming more for one than the argument permits for the
other. It costs nothing to be consistent here, because **the pen bound is not load-bearing**: the
construct refuses whatever the pen does, and the bound is recorded only so the reader can see
exactly which inputs are and are not the reason. If the construct were ever accepted, the clamp
would need condition 4 just as much as the glyph table does.

## 4 — Four reasons the plotted copper is not a function of the board document

### 4.1 `${…}` resolves from documents CopperMCP does not read, and from no document at all

`PCB_TEXT::GetShownText` installs a resolver that tries `LAYER`, then the parent footprint's
`ResolveTextVar`, then `BOARD::ResolveTextVar`, and runs it over the string whenever
`HasTextVars()`. `BOARD::ResolveTextVar` reaches the **project's** text variables, which live in
the sibling `.kicad_pro`, not in the board.

Measured, one `gr_text "${MYVAR}"` at `(size 1.27 1.27)`, **byte-identical `.kicad_pcb` in both
rows**:

| Sibling `.kicad_pro` | Plotted string | Ink width |
|---|---|---:|
| absent | `${MYVAR}` | 8.5650 mm |
| `"text_variables": {"MYVAR": "EXPANDED-WWWWWWWWWWWW"}` | `EXPANDED-WWWWWWWWWWWW` | 28.4012 mm |

Same board bytes, **3.3× the copper**. And some variables resolve from no document whatsoever —
measured plotted strings for the same board: `${FILENAME}` → the board's own file name,
`${PROJECTNAME}` → the containing project's stem, `${CURRENT_DATE}` → the date the plot was taken,
`${LAYER}` → `F.Cu`.

This one is fatal for CopperMCP in particular, not merely inconvenient. Board IR binds every
snapshot to `source.revision`, the digest of the board bytes. A board whose copper depends on a
file outside that digest, on its own path, or on the clock cannot be described by anything derived
from the digest — not tightly, and not loosely either, because the **character count itself** is
unknown. There is no `n` to multiply a per-glyph bound by.

### 4.2 `(face "…")` makes the copper a function of the rendering machine

An outline font is resolved through fontconfig, and `FONTCONFIG::FindFont` reports
`"Font '%s' not found; substituting '%s'."` when it is missing — it substitutes rather than
failing. Measured, `mmmm` at `(size 1.27 1.27)`:

| `(face …)` | Ink width |
|---|---:|
| `"Helvetica"` (installed here) | 5.6916 mm |
| `"NoSuchFontXYZ"` (not installed) | 6.5992 mm |

16 % wider on a machine that lacks the font, from identical bytes. The board document does not
carry the outlines, does not carry a digest of the font it means, and cannot make the substitution
refuse. Whatever else is true, no offline reader can bound this.

### 4.3 For the built-in stroke font, KiCad's own box is not a containing box

This is the case the corpus actually carries, and it is the one where a bound looks reachable. It
is not, and it fails one step earlier than expected.

**KiCad's own text bounding box excludes part of the plotted copper.**
`STROKE_FONT::GetTextAsGlyphs` ends with `aBBox->SetEnd( cursor.x - KiROUND( glyphSize.x * INTER_CHAR ), cursor.y - glyphSize.y )`
— the box is exactly `size.y` tall, measured up from the baseline. Descenders and overbars are
outside it by construction. Measured at `(size 1.27 1.27)` with `justify left bottom`, which is
KiCad placing *its own* box's bottom edge at the anchor `y = 30`, so ink past `y = 30` is ink
outside the box KiCad computed:

| String | Ink below the box bottom (`y = 30`) | Ink above the box top (`y = 30 − size.y`) |
|---|---:|---:|
| `mmmm` | 0 | 0 |
| `(g)pqy` | **0.3995 mm** (0.315 × size) | 0 |
| `~{ABC}` | 0 | **0.5957 mm** (0.469 × size) |

So the first candidate envelope — "the text's own bounding box as KiCad computes it" — is not
merely underivable from the file. It is the **wrong box**: reproducing it exactly would
under-approximate the copper by up to about half the text size, which is the forbidden direction.

**The remaining candidate is a per-glyph bound, and the glyph table is not in the board.**
`STROKE_FONT` loads `newstroke_font`, a C array compiled into KiCad
(`#include <newstroke_font.h>`), decoding each coordinate as `( coordinate[0] - 'R' ) *
STROKE_FONT_SCALE` with `STROKE_FONT_SCALE = 1.0 / 21.0`. Nothing about those extents is in the
`.kicad_pcb`, in the file-format specification, or in anything CopperMCP can read offline. Measured
exhaustively over the 94 printable ASCII code points at `(size 1.27 1.27)`:

| Quantity | Sample maximum | As a multiple of size | Attained by |
|---|---:|---:|---|
| advance | 1.9156 mm | 1.5083 | `m` |
| ink above the anchor | 0.9516 mm | 0.7493 | `$` |
| ink below the anchor | 1.1425 mm | 0.8996 | `(` |

Those are **sample** maxima over one repertoire in one build, and they are already known to be
false ceilings one step outside the sample. Measured the same way, at the same size:

| Glyph | Advance | As a multiple of size |
|---|---:|---:|
| `m` (ASCII maximum) | 1.9156 mm | 1.5083 |
| `Ж` | 2.1575 mm | **1.6988** |
| `漢` | 2.0970 mm | **1.6512** |

`Ж` alone is 12.6 % over the ASCII ceiling, and an overbar run reaches 0.884 × size above the
anchor against an ASCII ceiling of 0.7493. `GetTextAsGlyphs` indexes glyphs as
`dd = c - ' '` against `m_glyphBoundingBoxes->size()`, so the repertoire is finite — but its size, and every extent in it,
is a property of the KiCad build doing the plotting. Nothing in the board file, in the format, or
in CopperMCP declares which build that is, so a glyph widened in a later Newstroke turns every
previously emitted envelope into an under-approximation with no detectable signal. That is exactly
the failure mode the direction-of-error rule exists to make impossible.

**The one bound that *is* structural is useless.** A glyph coordinate is a single byte read as
`c - 'R'`, so the widest span the encoding can represent is about `254 / 21 ≈ 12` times the text
size, per glyph. An envelope of 12 × size × character count is a board-wide obstacle for any
ordinary legend: `mmmm` at 1.27 mm would take a 61 mm box against 6.3274 mm of measured ink,
a factor of ten, and a thirty-character legend would take 457 mm — wider than most boards. It
over-approximates, so it is not *unsafe*; it is a whole-board veto wearing an obstacle's
clothes, and it still does not survive section 4.1.

Three further layout behaviours would each need their own bound and none is in the file:
`INTER_CHAR = 0.2` between glyphs, `SUPER_SUB_SIZE_MULTIPLIER = 0.8` with `SUPER_HEIGHT_OFFSET =
0.35` / `SUB_HEIGHT_OFFSET = 0.15` for `^{}` and `_{}`, and `\t` snapping to a four-column stop
(measured at 1.27 mm: `A\tB` plots 5.8436 mm wide against 2.1545 mm for `AB`).

### 4.4 `gr_text_box` carries its own corners, and they bound neither dimension

This is the strongest objection to refusing text, and it deserves its own measurement rather than
being folded in by analogy. A `gr_text_box` is placed by `(start …)` and `(end …)` — two corners,
in the document, in exact nanometres. If the plotted copper stayed inside them there would be a
derivable envelope for at least one of the two heads, and refusing both identically would be
wrong.

It does not stay inside them. Declared box `(start 20 30) (end 60 32)` — 40 × 2 mm — at
`(size 1.27 1.27)`, measuring **the glyph strokes only** with the plotted border excluded (the
`<g class="stroked-text">` groups, so the rectangle KiCad draws at the declared corners cannot be
mistaken for text ink):

| String | Overflows the declared box by |
|---|---|
| `mmmm` | nothing — inside |
| `(g)pqy` | **0.1425 mm below the bottom edge** |
| `~{ABC}` | **0.1227 mm above the top edge** |
| `word` × 30 | **3.8594 mm above and 3.7479 mm below** |
| one unbreakable 52-character word | **11.1037 mm left and 11.0432 mm right** |

Both overflows are unbounded, and both scale linearly with a string length that §4.1 has already
shown is not derivable:

| One unbreakable word | Ink width | Horizontal overflow, each side |
|---|---:|---:|
| 26 characters | 31.0017 mm | inside |
| 52 characters | 62.1469 mm | ≈ 11.07 mm |
| 104 characters | 124.4373 mm | ≈ 42.22 mm |
| 208 characters | 249.0183 mm | ≈ 104.51 mm |

| Wrapping words | Ink height | Vertical overflow, each side |
|---|---:|---:|
| 5 words | 1.4288 mm | inside |
| 14 words | 3.4734 mm | ≈ 0.74 mm |
| 30 words | 9.6073 mm | ≈ 3.80 mm |
| 60 words | 17.7858 mm | ≈ 7.89 mm |

Two mechanisms, and neither is a corner case. **Vertically**, the box wraps: text longer than one
line grows the block downward *and* upward past both declared edges, so the height is a function of
the line count, which is a function of the string. **Horizontally**, a run with no break
opportunity cannot wrap at all, so it is laid out at full advance and centred — a 208-character
word overflows a 40 mm box by more than 104 mm on each side, ten times the box's own width.

So the corners are not an envelope in either axis. They are a *layout hint*, and the copper is
placed relative to them by the same font machinery §4.3 shows is not in the document. That is why
the refusal covers both heads identically, and it is measured rather than assumed.

## 5 — Verdict, and what would have to become true

**No envelope for `gr_text` or `gr_text_box` on a copper layer is provably containing given only
the board document, so the construct stays refused.** Over-refusal is the conservative direction
and costs exactly one board in this corpus.

Four things would have to become true, and section 4 says which reason each one answers. All four
are required; any three leave a hole.

1. **The rendered string must be a function of digested inputs.** CopperMCP would have to read the
   project's `text_variables` into the source set and digest it alongside the board — and refuse
   `${FILENAME}`, `${PROJECTNAME}` and `${CURRENT_DATE}`, which resolve from the path and the
   clock and can never be bound to a digest. (Answers 4.1.)
2. **`(face …)` must refuse, or carry trusted outlines.** A cached outline in the document is not
   enough on its own: a `render_cache` is a cache with no freshness binding to the string or the
   font that produced it, and [ADR-0070](../adr/0070-layered-fill-aware-obstacles.md) refuses
   stale zone fill for precisely that reason. (Answers 4.2.)
3. **The glyph extents must be a read value, not a measured one** — a table CopperMCP vendors and
   pins by digest, or one KiCad publishes as a contract, covering the whole repertoire rather than
   a sample. (Answers 4.3.)
4. **A font-build mismatch must refuse rather than pass.** Whatever declares the glyph table's
   version has to be checkable at conversion time, so that a KiCad whose Newstroke differs from the
   pinned one refuses instead of silently under-approximating. Without this, item 3 is sound only
   for the build it was measured against, which is the same thing as unsound. (Answers 4.3.)
5. **The corners of a `gr_text_box` must bound its copper**, or the head stays refused separately
   from `gr_text`. Today they bound neither axis and both overflows scale with the string, so
   nothing is gained by splitting the two heads. (Answers 4.4.)

### 5.1 — A note on how the citations in this document are checked

Every KiCad symbol named above was verified by fetching `kicad-source-mirror` at tag `10.0.5` and
reading the file, not from memory. That verification is a **manual** step and no gate performs it.
`scripts/check_doc_links.py` answers two questions — does a relative Markdown link resolve, and
does a label naming a record (`ADR-NNNN`, `D-NNN`, …) agree with the record its target identifies —
and a **code-symbol label is structurally outside both**. A Markdown link whose label is
`FOO::bar` and whose target is a `github.com/.../foo.cpp#L1-L2` URL passes whether or not
`FOO::bar` exists, because the target is a network URL the checker deliberately does not fetch and
the label carries no record identifier for it to judge.

This is not hypothetical here. The first version of this note attributed the bounding-box line to
`STROKE_FONT::drawSingleLineText`, a KiCad-6-era name that does not exist at 10.0.5; the line is
real and lives in `STROKE_FONT::GetTextAsGlyphs`. Right target, wrong label — exactly the class
`D-182` records for a Markdown link whose target resolved and whose label named the wrong ADR, and
exactly the class that checker was built for. It cannot reach this one. Adversarial review caught
it; nothing mechanical would have.

The practical consequence for a reader: **treat a code-symbol citation in this repository as
verified by whoever wrote it and by whoever reviewed it, and by nothing else.** Re-read the source
before relying on one. The measurements in this note are independent of the labels — every number
comes from the oracle in §1 and §6, so a wrong symbol name misdirects a reader without changing a
result.

Until then, the honest model of copper lettering is a refusal that says so.

### 5.2 — Step-4 live-IPC follow-up and measured stop

The KiCad 9/10 IPC surface changes what a future **live-session** adapter might be able to prove:
with board context available, KiCad can expand text and expose `GetTextAsShapes`. That is a
plausible evidence path for obtaining the outlines from the same editor session that will later
plot the board. It is not evidence that the current offline adapter may consume. CopperMCP does
not yet bind the IPC session, project text variables, selected font, and font-build identity to a
source revision, nor does it have a tested contract for carrying those inputs into its conservative
envelope. The existing refusal therefore remains unchanged.

In particular, none of ADR-0095's five exit conditions is currently satisfied. Project text
variables are not included in the source revision and path- or clock-derived variables do not
refuse; face fonts do not carry trusted, freshness-bound outlines; built-in glyph extents are not
read and pinned by digest; a font-build mismatch does not refuse; and `gr_text_box` corners still
do not contain the plotted copper. `GetTextAsShapes` is a research lead, not a reason to weaken
any of those conditions.

The required B-114 paired probe was run before implementation: on the six candidates that reached
the pair, remove exactly the `Edge.Cuts` curve gate and the `.Cu` text refusal, while retaining the
other four boards' recorded pass-two refusals. **Zero of the six newly converted, so the
ten-board corpus remained 0 → 0 of 10.** That observation fixes the prediction for a hypothetical
paired implementation at zero conversions. The premise that these two obvious gates would produce
a first end-to-end import is therefore refuted, and implementation stops here; no code before/after
differential is claimed.
Conversion stops at the first refusal, so the newly visible blockers are recorded as findings,
not regressions:

| Board probe | Curves / `.Cu` text masked | First refusal after the mask |
|---|---:|---|
| TinyTapeout | 4 / 4 | non-copper `gr_text.layer` has invalid arity |
| LPDDR4 | 7 / 1 | `setup.stackup` |
| M.2 | 3 / 2 | unsupported `kicad_pcb.setup` semantic field |
| ov9281 | 0 / 1 | root dimensions |
| Amalthea | 15 / 4 | root dimensions |
| Cynthion | 4 / 6 | root dimensions |

The M.2 and Amalthea probes were corrected from initially over-broad masks and rerun with the exact
layer-filtered masks against the B-114 artifacts; their outcomes were unchanged. No transformed
board bytes were committed. The remaining four boards retain B-114's recorded **pass-two**
refusals: copper layer kind, graphic without one explicit layer, root dimensions, and an unsupported
root semantic construct.
This result does not establish anything behind those blockers, nor does it establish live IPC,
DRC, routing, placement, fabrication, or performance capability. It records a successful
refutation of the Step-4 conversion premise and leaves ADR-0095's refusal policy in force.

## 6 — Reproducing every figure above

Each measurement is one synthetic board of this shape, with `BODY` replaced by the row under test,
plotted with the command in section 1 and read as described there:

```
(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (pad_to_mask_clearance 0))
  (net 0 "")
  (gr_line (start 0 0) (end 200 0) (layer "Edge.Cuts") (width 0.05))
  (gr_line (start 200 0) (end 200 160) (layer "Edge.Cuts") (width 0.05))
  (gr_line (start 200 160) (end 0 160) (layer "Edge.Cuts") (width 0.05))
  (gr_line (start 0 160) (end 0 0) (layer "Edge.Cuts") (width 0.05))
  BODY
)
```

The version token is deliberately KiCad's older `20240108` rather than the `20260206` CopperMCP
accepts: `kicad-cli` reads both, and using the older one keeps these boards outside the adapter's
accepted set so that no oracle scratch file can be mistaken for a fixture. A representative body,
the one measured for the ASCII sweep:

```
(gr_text "m" (at 50 30 0) (layer "F.Cu") (effects (font (size 1.27 1.27))))
```

Section 4.1's second row additionally needs a sibling `pv.kicad_pro` containing
`{"meta": {"filename": "pv.kicad_pro", "version": 1}, "text_variables": {"MYVAR":
"EXPANDED-WWWWWWWWWWWW"}}`, with the board saved as `pv.kicad_pcb` beside it.

Section 4.4's bodies are text boxes on the same board, with `STRING` replaced per row:

```
(gr_text_box "STRING" (start 20 30) (end 60 32) (layer "F.Cu")
  (effects (font (size 1.27 1.27))))
```

Section 4.4 is the one measurement where the plotted **border** must be excluded, because KiCad
draws a rectangle at the declared corners and its union with the glyphs would hide exactly the
overflow being measured. The glyph strokes are the `<path>` elements inside the
`<g class="stroked-text">` groups; everything outside those groups is the border and is discarded
before the bounding box is taken. Reading the union instead reports the declared corners back
unchanged for every string that overflows vertically, which is how a wrong method would look
right.

## 7 — Sources

- [`EDA_TEXT::GetEffectiveTextPenWidth`, KiCad 10.0.5](https://github.com/KiCad/kicad-source-mirror/blob/10.0.5/common/eda_text.cpp#L449-L467)
- [`GetPenSizeForNormal`, `GetPenSizeForBold` and `ClampTextPenSize`, KiCad 10.0.5](https://github.com/KiCad/kicad-source-mirror/blob/10.0.5/common/gr_text.cpp#L37-L96)
- [`STROKE_FONT::GetTextAsGlyphs` (L202–291), `loadNewStrokeFont` (L99) and the Newstroke decode, KiCad 10.0.5](https://github.com/KiCad/kicad-source-mirror/blob/10.0.5/common/font/stroke_font.cpp#L45-L291)
- [`PCB_TEXT::GetShownText` and its text-variable resolver, KiCad 10.0.5](https://github.com/KiCad/kicad-source-mirror/blob/10.0.5/pcbnew/pcb_text.cpp#L162-L195)
- [`BOARD::ResolveTextVar`, KiCad 10.0.5](https://github.com/KiCad/kicad-source-mirror/blob/10.0.5/pcbnew/board.cpp#L510)
- [`EDA_TEXT::HasTextVars`, KiCad 10.0.5](https://github.com/KiCad/kicad-source-mirror/blob/10.0.5/include/eda_text.h#L128)
- [`FONTCONFIG::FindFont` substitution report, KiCad 10.0.5](https://github.com/KiCad/kicad-source-mirror/blob/10.0.5/common/font/fontconfig.cpp#L374-L380)
- [`GetTextAsShapes` IPC message, KiCad 10.0.5](https://github.com/KiCad/kicad-source-mirror/blob/10.0.5/api/proto/common/commands/base_commands.proto#L68-L86)
- [`GetTextAsShapes` and project-context `ExpandTextVariables` handlers, KiCad 10.0.5](https://github.com/KiCad/kicad-source-mirror/blob/10.0.5/common/api/api_handler_common.cpp#L197-L293)
- [KiCad board file format: graphic items and text effects](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/)
- [ADR-0011](../adr/0011-existing-copper-obstacles.md), [ADR-0013](../adr/0013-polygon-zone-obstacles.md), [ADR-0070](../adr/0070-layered-fill-aware-obstacles.md), [ADR-0072](../adr/0072-conservative-arc-track-envelopes.md), [ADR-0075](../adr/0075-courtyard-oracle-parity.md), [ADR-0090](../adr/0090-root-level-board-groups.md)
