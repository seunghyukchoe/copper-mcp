# ADR-0095: Copper text has no envelope derivable from the board, and refuses under its own name

- Status: Accepted
- Date: 2026-08-12
- Owners: `@seunghyukchoe`
- Related: [Issue #141](https://github.com/seunghyukchoe/copper-mcp/issues/141),
  [Issue #116](https://github.com/seunghyukchoe/copper-mcp/issues/116), ADR-0011, ADR-0013,
  ADR-0070, ADR-0072, ADR-0090, ADR-0092,
  [KiCad copper text envelope research](../research/kicad-copper-text-envelope-v1.md)

## Context

One real KiCad 10 board refuses for a root `(gr_text …)` on `F.Cu` and for nothing else. It is the
last uncategorised conversion gap in the issue #116 corpus, and unlike the other two open gaps it
is not about metadata. Copper lettering is real copper.

That makes the direction-of-error rule decide the shape of the answer before any code is written.
An obstacle may only be **over**-approximated (ADR-0011, ADR-0013, ADR-0072). Ignoring the text is
therefore the one forbidden outcome: a router would propose a track straight through a letter and
carry it downstream into apply. Refusing is safe. Modelling it is safe **if and only if** the region
modelled provably contains every glyph KiCad plots.

So the question this record answers is narrow and it is not "how do we render text". It is:
**is there a region, derivable from the board document, that provably contains the plotted
copper?** ADR-0013 is the precedent for the shape of a yes — it does not model a zone's fill, it
models a region the fill cannot leave — and this record is what happens when the same question gets
a no.

It gets a no three times over, for reasons that are independent, and all three are measured against
`kicad-cli` 10.0.5 in the [research note](../research/kicad-copper-text-envelope-v1.md).

**1. The rendered string is not a function of the board document.** `PCB_TEXT::GetShownText`
resolves `${…}` through `BOARD::ResolveTextVar`, which reaches the **project** file's
`text_variables` — a second document CopperMCP neither reads nor digests. Measured: one
byte-identical `.kicad_pcb` carrying `gr_text "${MYVAR}"` plots 8.5650 mm of ink with no sibling
`.kicad_pro` and 28.4012 mm with one, **3.3× the copper from the same bytes**. Worse, `${FILENAME}`
resolves from the board's own path, `${PROJECTNAME}` from the enclosing project, and
`${CURRENT_DATE}` from the clock — quantities that are in no document at all. Board IR binds every
snapshot to `source.revision`, the digest of the board bytes; a quantity outside that digest cannot
be bounded by anything derived from it. Not tightly, and not loosely either, because the character
count itself is unknown.

**2. With `(face "…")`, the copper is a function of the rendering machine.** An outline font is
resolved through fontconfig, which *substitutes* when the face is missing rather than failing:
`"Font '%s' not found; substituting '%s'."` Measured: `mmmm` at 1.27 mm plots 5.6916 mm wide with
an installed `Helvetica` and 6.5992 mm — 16 % wider — with a face this machine does not have. The
document carries neither the outlines nor a digest of the font it means.

**3. For the built-in stroke font, KiCad's own bounding box is not a containing box.**
`STROKE_FONT::drawSingleLineText` sets the box end to `cursor.y - glyphSize.y`, so the box is
exactly `size.y` tall from the baseline and descenders and overbars fall outside it *by
construction*. Measured at 1.27 mm with `justify left bottom`, which puts KiCad's *own* box
bottom at the anchor, `(g)pqy` plots **0.3995 mm below it** and `~{ABC}` **0.5957 mm above the
box top** — up to 0.47 × the text size outside. That disposes of the most obvious candidate:
even a perfect reproduction of KiCad's own box would under-approximate, which is the forbidden
direction.

The only remaining candidate is a per-glyph bound, and Newstroke's extents live in a C array
compiled into KiCad (`#include <newstroke_font.h>`, decoded as `(coordinate[0] - 'R') / 21`), not
in the board and not in the format. Measuring them is possible; *bounding* them is not. The
exhaustive ASCII maxima at 1.27 mm — advance 1.5083 × size, 0.7493 × size above the anchor,
0.8996 × size below — are sample maxima over one repertoire in one build, and they are already
known to be false ceilings one step outside the sample: `Ж` advances 1.6988 × size and `漢`
1.6512 × size, the first 12.6 % over the ASCII ceiling, and an overbar run reaches 0.884 × size
above the anchor against an ASCII ceiling of 0.7493. Nothing in the board, the format, or
CopperMCP declares which font build will plot the gerbers, so a glyph widened in a later
Newstroke would silently turn every emitted envelope into an under-approximation. The one bound
that *is* structural — a coordinate byte read as `c - 'R'` spans at most ~12 × size — turns
`mmmm` at 1.27 mm into a 61 mm box against 6.3274 mm of measured
ink, and still does not survive reason 1.

Two of the four inputs *are* settled, and saying so is what makes the gap precise rather than
vague. The plotted pen width is bounded from the file whatever the file claims:
`GetEffectiveTextPenWidth` ends in `ClampTextPenSize`, which caps it at a quarter of
`min(|size.x|, |size.y|)` — measured, a declared thickness of 0.5 mm and one of 2.0 mm both plot at
0.3175 mm on a 1.27 mm text. And `at`, rotation and `justify` place the box predictably. The gap is
entirely in *which glyphs, and how far each reaches*.

## Decision

**1. A root `gr_text` or `gr_text_box` on a copper layer stays refused, and the refusal is not
provisional.** It is not "unmodelled pending effort"; it is the correct answer to a question whose
inputs are not in the document. Over-refusal is the conservative direction and it costs exactly one
board in the surveyed corpus.

**2. The refusal names the construct, from a closed table.** `_UNMODELLED_COPPER_GRAPHIC_HEADS`
maps `gr_text` and `gr_text_box` to `copper text has no envelope derivable from the board and is
unsupported`, with `object_kind: text`; every other graphic head on copper keeps `root graphic on
copper is unsupported` with `object_kind: graphic`. The sentence emitted is a **value from that
table**, selected by an equality test against the source token and never built from it, exactly as
ADR-0090 rule 4 requires — the board's own text is untrusted data and both a head and a text string
are board bytes. A head absent from the table is refused without being named.

The split matters because the two refusals are not the same fact. A drawn `gr_line` on copper
carries its own geometry, so ADR-0013's envelope is at least *available* to it and the refusal is a
statement about effort. A `gr_text` carries a *reference* to geometry KiCad owns, and the refusal is
a statement about information. An operator reading one sentence for both cannot tell which of those
they are looking at, and only one of them is answerable by drawing the shape differently. This is
D-162's move — "splitting one message into three" — applied to the root path.

**3. Text on a non-copper layer keeps converting, and the accepted subset is a closed field table,
not a sentence.** ADR-0092's prose subset admitted two things it did not mean; this one is a table.
A root text expression is read past **only** when every row holds:

| Field | Requirement |
|---|---|
| head | exactly `gr_text` or `gr_text_box` |
| `(layer …)` | present, exactly one value |
| that layer value | **not** `Edge.Cuts`, **not** `*.Cu`, **not** `F&B.Cu`, and **not** any name ending in `.Cu` |
| every other field — `at`, `effects`, `font`, `size`, `thickness`, `face`, `justify`, `knockout`, `uuid`, the string itself | unread, unconstrained, and contributing nothing |

The last row is the load-bearing one and it is a claim, so it is stated as one: the **layer**
decides, and nothing else can. A layer outside that copper test carries no copper in any KiCad
stack, so the text is not an obstacle; it holds no net, so it cannot enter connectivity; it is not
`Edge.Cuts`, so it is not routing room. Given that, no other field can change any quantity Board IR
models — which is why the table has no other rows, and why widening it is a decision rather than an
edit. The claim is exercised rather than only asserted:
`test_the_hard_text_cases_are_still_ignored_on_a_documentation_layer` puts each of the four
constructs that make a copper envelope underivable — a project text variable, a path-derived one,
overbar and sub/superscript markup, and a non-ASCII code point — on `F.SilkS` and requires the
board to convert. If some other field had crept into the decision, one of them would refuse and
name itself.

The copper test is exact rather than merely conservative on every board that converts, and that is
a property of `_layers`, not of this table: a board whose copper layers are not exactly `F.Cu`,
`In{n}.Cu` and `B.Cu` at KiCad's own IDs is refused wholesale with `object_kind: layer`, so no
converted board can carry copper under a name this test misses. The test is over-inclusive in the
other direction — it catches `In1.Cu` on a two-layer board and the `*.Cu` wildcard — which refuses
more, not less.

**4. No count, and no `render_cache`.** ADR-0090 accepted-and-counted a root group because it was
*accepted*; this construct is refused, so there is nothing to count. KiCad's `render_cache` is not
an escape either: it is a cache with no freshness binding to the string or the font that produced
it, and ADR-0070 refuses stale zone fill for exactly that reason.

## Consequences

**The corpus count does not move, and that is the honest result.** 11 of 17 boards convert before
this change and 11 of 17 after, measured back to back on byte-identical sources with
`scripts/benchmark_real_board_capability.py`. The one board this construct blocks stays blocked;
what changes is that its refusal now says which construct and why. A change that moved the count
here would have had to invent the envelope section 4 of the research note shows cannot be derived.

**No content address moves, and no schema changes.** A board carrying copper text produced no
snapshot before this change and produces none after. `object_kind: text` is a new value in an
existing free-form diagnostic field — the same widening ADR-0090 made with `group` — and no Board
IR field, JSON schema, or golden digest is touched.

**A refusal that names a construct is the point where board bytes get closest to an
instruction-bearing field, and copper lettering is the worst case.** The unmodelled thing *is* the
string, so interpolating it is maximally tempting and would be maximally harmful. The sentence
comes from a closed table and `test_the_copper_text_refusal_never_echoes_the_string_it_refuses`
puts a bearer-token canary in the string and asserts it does not come back. SEC-136 records the
review.

**The refusal is mutation-checked in the direction that matters.** Six single-edit mutants were
applied and all six are killed: dropping `gr_text` from the table, widening the table to a drawn
head, collapsing `object_kind` back to `graphic`, **skipping the refusal so copper text is silently
ignored**, dropping the copper-layer guard so silkscreen starts refusing, and interpolating the
refused text atom into the sentence. The fourth is the one the direction-of-error rule cares about,
and the fifth is why the accepted subset is pinned by a test rather than described in prose. Each
mutant's anchor was verified to match exactly once, so a mutant that had silently stopped applying
would have been reported rather than counted as killed.

**No differential is offered as a safety argument, deliberately.** It would be easy to convert the
corpus board with the `gr_text` deleted, observe that everything but `source.revision` is equal,
and call that evidence. It is not. ADR-0090 records why: an equality between two outputs of the
same reader bounds what an adapter *adds*, never what it *fails to read*, and it holds by
construction whether the dropped construct is inert or catastrophic. Here the construct is copper,
so the differential would have been maximally reassuring and exactly wrong. The argument in this
record is entirely about what the document does and does not determine.

**What would have to become true is written down, so this can be revisited on evidence rather than
on appetite.** Section 5 of the research note lists four conditions, and all four are required:
the project's `text_variables` digested into the source set with path- and clock-derived variables
refused; `(face …)` refused or carrying trusted, freshness-bound outlines; the glyph extents a read
value pinned by digest rather than a measured constant; and a font-build mismatch that refuses
rather than passing. Item four is the one that is easy to skip and fatal to skip: without it, item
three is sound only for the build it was measured against.

## Alternatives considered

**Model the text as its own KiCad-computed bounding box.** Rejected on measurement, not on
principle. The box KiCad computes is `size.y` tall from the baseline and the plotted copper leaves
it by up to 0.47 × the text size at 1.27 mm. It is not a containing box, so deriving it exactly
would produce an under-approximation — the one forbidden direction — with the extra hazard of
looking rigorous.

**A deliberately generous envelope from `at`, `size`, `thickness` and the string length.**
Rejected, and this is the alternative that had to be taken seriously, because over-approximation is
the *safe* direction and a loose box is admissible where a tight one is not derivable. It fails on
information, not on looseness. `${…}` makes the string length itself underivable (reason 1), so
there is no `n` to multiply by; `(face …)` makes the per-glyph factor a property of the plotting
machine (reason 2); and for the stroke font the factor is a measurement of one font build with no
mechanism to detect a change (reason 3). A constant fitted to KiCad 10.0.5 and applied to KiCad 11
is not an over-approximation, it is an unverified guess that happens to be large. The project's
standing has been that an envelope is *derived and then oracle-checked* (ADR-0072, ADR-0075); an
oracle-*fitted* constant inverts that.

**Restrict acceptance to a closed subset — stroke font, literal ASCII, no markup, angle in
quarter-turns — and bound the glyphs exhaustively over that repertoire.** Rejected, and it is the
strongest alternative: it would have converted the corpus board and taken the count to 12 of 17,
because that board's text is a literal ASCII dimension string with a default thickness. Exhaustive
measurement over a closed repertoire really is stronger than sampling. It is still a measurement of
one build, and the failure it admits is silent: nothing in the board or in CopperMCP observes which
Newstroke plots the gerbers, so a widened glyph turns a shipped envelope into an
under-approximation with no signal anywhere. One board is not worth a soundness argument whose
refutation is invisible. Condition 4 above is precisely what this alternative is missing, and it is
what would make it acceptable.

**Accept only where it cannot matter — copper text already inside a keepout or a knockout.**
Rejected as surface without payoff. It is a conditional acceptance whose condition needs its own
containment proof between two regions, one of which is the region that cannot be derived. The
corpus carries no such case.

**Ask KiCad for the box through `kicad-cli` or the IPC plugin.** Rejected for Board IR, though it
is what the *research* here is built on. Conversion is offline, deterministic and pure: it maps
bytes to a snapshot with no subprocess, no host font cache and no clock. Binding the accepted
document set to an external renderer would make the same bytes convertible on one machine and not
another — the property reason 2 identifies as the defect. An oracle is the right instrument for
establishing a fact about KiCad and the wrong dependency for a reader that must be reproducible.

**Refuse `gr_text` on non-copper layers too, for symmetry.** Rejected without hesitation.
Silkscreen lettering is ink; it is not an obstacle, holds no net, and cannot affect any claim. 69
of the 71 root texts in the surveyed corpus are on `F.SilkS`, so refusing them would cost most of
the corpus to buy nothing. The layer decides, and pinning that boundary with a test is why mutant
five is killed.
