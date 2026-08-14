# ADR-0100: A custom pad has a derivable envelope and nowhere in Board IR to put it

- Status: Accepted
- Date: 2026-08-13
- Owners: `@seunghyukchoe`
- Related: [Issue #153](https://github.com/seunghyukchoe/copper-mcp/issues/153),
  [Issue #152](https://github.com/seunghyukchoe/copper-mcp/issues/152), ADR-0011, ADR-0013,
  ADR-0072, ADR-0075, ADR-0080, ADR-0091, ADR-0093, ADR-0095, ADR-0096,
  [KiCad custom pad envelope research](../research/kicad-custom-pad-envelope-v1.md)

## Context

Three of the survey corpus's phono saves refuse with `unsupported.construct` —
`pad field 'options' is unsupported` — at a pad. The refusal is reachable only once
[ADR-0094](0094-root-board-properties-as-metadata.md) and
[ADR-0097](0097-courtyard-layer-decides-the-side.md) have landed; on `main` before them, each of
those refusals hid it in turn. It is the fourth instance in two days of a blocker masking the next
one.

**1. The refusal names a field that is not the gap.** `(options …)` is emitted by KiCad's writer
under `GetShape() == PAD_SHAPE::CUSTOM` and under no other condition
(`pcb_io_kicad_sexpr.cpp:2050-2062`). It is a *mandatory sub-field of the shape*, not an optional
extra somebody added. Naming it sends the reader after a token they cannot remove without producing
a pad KiCad will not read back. The construct that cannot be modelled is `custom`, and the field
loop merely reached one of its parts first. That is the same defect ADR-0093 fixed for off-grid
geometry and #152 is fixing for the pad refusals: a message that names a token rather than the
thing behind it.

**2. A pad is copper, so the only interesting question is whether it can be over-approximated.**
ADR-0011's direction-of-error rule, restated by ADR-0013 and ADR-0072, allows an obstacle to be
enlarged and never shrunk. So the question ADR-0095 asked about copper text gets asked again here:
**is there a region, derivable from the board document, that provably contains every bit of copper
a custom pad puts down?**

**3. The answer is yes, and it is worth being exact about how comprehensively yes.** A KiCad custom
pad is an anchor rect-or-circle of `size` **unioned** with a list of drawn primitives — established
from `PAD::MergePrimitivesAsPolygon` (`pad.cpp:3275-3315`), which seeds the polygon with the anchor
and then `BooleanAdd`s the primitives; from `PAD::buildEffectiveShape` (`pad.cpp:1278-1292`), which
adds every non-proxy primitive on top of the anchor shape; and from KiCad 10.0.5's own plotter,
which plots **both** shapes for a pad whose single primitive starts 5 mm past its anchor's edge. The
primitives do not replace the anchor, and a converter that read the anchor alone would drop real
copper.

Given that, the primitive vocabulary is closed at eight heads
(`pcb_io_kicad_sexpr_parser.cpp:6323-6358`), two of which are proxy items that contribute no
copper, and **every one of the remaining six admits an exact integer-nanometre containing box
derivable from the document**. `gr_curve` is the one that looks hard and is not: a cubic Bézier is
a convex combination of its four control points at every parameter value — the Bernstein
coefficients are non-negative on `[0, 1]` and sum to `1` by the binomial theorem — so the curve
cannot leave their bounding box. Measured, a curve whose control points span 16 mm in `y` plots
4.8006 mm of copper.

**So this is emphatically not ADR-0095's case, and it must not be filed as one.** Copper text
refuses because the plotted glyphs are not a function of the board document's bytes. A custom pad's
geometry is entirely in the document, in exact millimetre literals, and section 4 of the research
note gives the closed-form box for every head. ADR-0013 is the governing precedent for what to do
with a shape that cannot be modelled exactly but can be bounded, and by the geometry alone this is
an ADR-0013 case.

**4. It still refuses, because Board IR's `Pad` is read in both directions of error from one set of
fields.** This is what decides the record, and it is a fact about this repository rather than about
KiCad.

| Reader | Direction required | What it computes from `shape`, `size_x_nm`, `size_y_nm` |
|---|---|---|
| `routing/astar.py::_pad_extent` | **over** — must contain the copper | `⌈size/2⌉`, the region a track centreline may not enter |
| `routing/layered_board_adapter.py::_pad_bounds` | **over** | the same box, inflated into a layered obstacle |
| `routing/astar.py::_pad_core_extent` | **under** — must be inside the copper | "half extents of a rectangle strictly inside the pad", the attachment core a search may terminate on |

> **Amendment, 2026-08-14.** This table names the three clearest readers, and it was written as if
> it were the complete set — [R-145](../ledgers/risk-register.md) records that nobody had checked.
> The P3.3a survey ([pad geometry reader survey](../research/pad-geometry-reader-survey-v1.md),
> `B-112`, `D-203`) enumerates **23 sites across 14 modules**: 8 needing **over**, 3 needing
> **under**, 4 needing an **exact** region neither answers, 5 carriers, and **3 whose direction
> requirement is unsatisfied on `main` today**. **This record's decision is unaffected and its
> argument is strengthened** — one rectangle cannot serve 18 readers if it cannot serve 3. What
> moves is the cost of the revisit in Consequences below: condition 2 is not bookkeeping over three
> call sites, and one of the three unsatisfied readers needs two opposite directions from a single
> accessor, so it admits no re-point at all.

For `PadShape.RECT` the over- and under-approximating readers collapse onto one rectangle —
`(size + 1) // 2` against `size // 2`, at most a nanometre apart per axis. A `Pad` with
`shape=RECT` therefore does not *bound* its copper in either direction; it **asserts that the
copper is that axis-aligned rectangle**.

That forecloses every spelling. Size the pad to the union's bounding box and the obstacle is sound
while the attachment core claims copper that is not there — which is not a hypothetical failure
mode but one this repository has already had, and `_pad_cores`' own docstring already names: "That
claims copper that is not there, which is the one direction an attachment core may never err in."
Size it to the anchor and the core is sound while the obstacle silently loses every primitive,
which is the forbidden direction. Any other single rectangle `R` must satisfy `copper ⊆ R` and
`R ⊆ copper`, hence `R = copper`, hence the copper must already be an axis-aligned rectangle — in
which case `custom` was not needed to express it. No pad in the corpus is one: all eight are a step
shape, an anchor abutting a polygon that is wider than it.

The distinction is not internal. `placement/legalizer.py::_pad_overlap` publishes `violated` when
two pads' **cores** overlap, so a core claiming absent copper would publish a legality verdict KiCad
does not share — which ADR-0075 and ADR-0080 classify as a **false claim, not a safe one**.

## Decision

**1. A `custom` pad shape refuses, by name, and the name says why rather than what.** The message is
`a custom pad shape has no single region that both contains its copper and is contained by it, and
is unsupported`. It is a value selected from a closed table by an equality test against the source
token, never built from it, so the refusal names the construct without echoing one byte of the
board — the rule `_UNMODELLED_ROOT_HEADS` and `_UNMODELLED_COPPER_GRAPHIC_HEADS` already follow. The
indexed locator still says which pad.

**2. The kind and shape are decided before the field checks, and that ordering is the fix for the
reported symptom.** With the field loop first, every custom pad on every real board was told that
`options` is unsupported. It is now told that its shape is. No board's outcome changes: the same
set of boards refuses, with the same typed code, the same `object_kind` and the same locator. Only
the sentence moves. This answers issue #153's first question the way #152 answered it for the pad
refusals — the refusal names the construct, not the token the allowlist happened to reach.

**3. The refusal table is a closed two-entry table, and its whole domain is asserted rather than
sampled.**

| Source token | Refusal message | Why it is here |
|---|---|---|
| `custom` | `a custom pad shape has no single region that both contains its copper and is contained by it, and is unsupported` | unmodell**able** through today's `Pad`: see Context 4 |
| `trapezoid` | `trapezoid pad shapes are unsupported in Board IR adapter v0.2` | unmodell**ed**: a convex quadrilateral derivable from `size` and `(rect_delta …)`, with both directions of error available |

The domain is exactly KiCad's six writer tokens (`pcb_io_kicad_sexpr.cpp:1643-1649`) minus Board
IR's four `PadShape` members, and
`test_the_unmodelled_pad_shape_table_is_kicads_tokens_minus_board_irs` asserts that partition. This
is the check ADR-0091 needed for `zone_connect` and ADR-0096 applied to the pad-kind table: a
behavioural test can only probe tokens someone thought to write down, and a seventh key quietly
added here would change no board that exists.

The two messages differ deliberately. A reader must be able to tell "nobody has modelled this yet"
from "this cannot be carried by the type", because the two have completely different fixes, and
being unable to tell them apart is the complaint issue #153 opens with. This is also why the table
has two entries and not one: ADR-0096 deleted a one-entry refusal table with nothing left to name,
and a table that can never miss its default is dead code. This one can.

**4. `options` and `primitives` stay in `_UNSUPPORTED_PAD_FIELDS`, and their reachability is
pinned.** KiCad's writer emits `(options …)` only for a custom pad, so it is tempting to read those
two entries as newly unreachable and delete them. They are not: KiCad's *parser* accepts
`T_options` and `T_primitives` on a pad of any shape
(`pcb_io_kicad_sexpr_parser.cpp:6315-6323`), so a hand-edited or third-party file can put either on
a `roundrect` pad, where the shape refusal does not fire and the field loop is the only thing
between that file and a snapshot.
`test_pad_field_refusals_are_still_reachable_on_a_modelled_shape` asks that question before the
entries can be deleted, rather than after — which is the order ADR-0091 found had been got wrong
behind the pad allowlist.

**5. No accepted subset is defined, and no bounded primitive subset is shipped.** Every custom pad
in the corpus is a single filled `gr_poly`, and a subset admitting only that head would convert all
three phono boards' pads. It is deliberately not offered. The blocker is not the primitive
vocabulary — section 4 of the research note bounds all six heads — so such a subset would be an
accepted subset chosen by what one private tree happens to contain, and it would have to be
withdrawn as soon as the real blocker was understood. A prose subset would be worse still; the only
closed table in this record is the refusal table above.

**6. No differential is offered as a safety argument.** It would be easy, and it would be worthless
twice over: nothing about this change converts a board, so a with/without comparison is trivially
equal, and the equality would hold by construction for *any* refusal — including a wrong one. A
custom pad is copper, which makes the argument especially seductive and especially inapplicable.
The safety claim in this record rests on Context 4 and on nothing else.

## Consequences

**The corpus count does not move, and that is the expected result.** Conversion-only measurement
against the private corpus, before and after and before again with per-board content digests: **12
of 18** each time, with every converting board's digest unchanged. Three boards refuse for the
custom pad before and after; only their message changes. No `PadShape` member is added, no
published schema changes, and no pinned digest in `tests/test_golden_identities.py` moves — the
change touches refusal messages and the order two checks run in, and neither reaches a snapshot.

**A fifth masked blocker is now visible, and it is reported rather than counted.** With the custom
pad neutralised in a throwaway in-memory rewrite — deliberately unsound geometry, used only to see
what is behind it, never committed and never written to the corpus — all three phono boards still
refuse, on `(property …)` **on a pad**: KiCad's `PAD_PROP` (`bga`, `fiducial_glbl`,
`fiducial_local`, `testpoint`, `heatsink`, `castellated`, `mechanical`), 10 occurrences across the
three boards. It refuses today through `_reject_unknown_children` with `expression contains an
unsupported semantic field`, which names nothing — the same defect class this record fixes one
level up. It is not a free accept: `castellated` in particular describes a plated half-hole at the
board edge and is a real copper claim. A follow-up issue is filed. **No conversion win is claimed
by this record.**

**Two sub-questions were checked and neither changes the outcome.** `(options (clearance
outline|convexhull))` is consumed at exactly one site in KiCad 10.0.5 —
`ZONE_FILLER::addKnockout` (`zone_filler.cpp:1684-1712`) — where both branches inflate the pad by
the same `aGap` and the option decides only whether the *zone's* knockout is that outline or its
convex hull. A convex hull contains the set it is built from, so the option can only enlarge the
knockout, and Board IR models a zone as its outline (ADR-0013), which contains every valid fill
regardless. Discarding the value therefore cannot under-state a clearance anywhere. And ADR-0091 is
not weakened: a custom pad does not narrow KiCad's own connectivity — `buildEffectiveShape` gives
collision the whole union, not the anchor — and the one attachment-relevant construct a custom pad
adds, the `gr_vector` thermal-spoke template, is inert under exactly ADR-0091's standing argument
that pad-to-pour attachment comes only from verified fill. One ordering consequence is recorded so
it is not mistaken for a widening: a custom pad carrying `zone_connect 0` now refuses under the
shape's name rather than ADR-0091's. Both refuse.

**The refusal is mutation-checked in the direction that matters.** Six mutants under
`docs/mutants/2026-08-13-custom-pad-shape-refusal.json`, run through the committed harness of
ADR-0098 — which purges `__pycache__` around every application, requires each anchor to match
exactly once, refuses to apply a mutant until the unmutated killing tests pass, and counts only
pytest exit 1 as a kill. All six were killed on Python 3.12.13:

| Mutant | The regression it models | Killed by |
|---|---|---|
| `CP1-custom-entry-deleted` | the `custom` entry quietly dropped, so the shape refuses unnamed again | `test_the_unmodelled_pad_shape_table_is_kicads_tokens_minus_board_irs` |
| `CP2-custom-repointed-at-the-weaker-message` | `custom` given the trapezoid sentence, erasing the unmodelled/unmodellable distinction | `test_a_custom_pad_shape_is_refused_by_name_and_not_by_its_mandatory_sub_field` |
| `CP3-shape-decided-after-the-field-loop-again` | the ordering reverted, so `options` is named again | `test_a_custom_pad_shape_is_refused_by_name_and_not_by_its_mandatory_sub_field` |
| `CP4-named-lookup-skipped` | the named lookup bypassed while the table stays present and correct | `test_a_custom_pad_refuses_the_same_way_whatever_primitives_it_carries` |
| `CP5-custom-admitted-to-padshape` | `PadShape` widened to admit `custom`, the accept this record refuses | `test_the_unmodelled_pad_shape_table_is_kicads_tokens_minus_board_irs` |
| `CP6-options-and-primitives-dropped-from-the-field-loop` | the two entries deleted as newly unreachable, which they are not | `test_pad_field_refusals_are_still_reachable_on_a_modelled_shape` |

CP1 and CP5 are the two a behavioural test cannot see on its own, and they are why the table's
domain is asserted rather than sampled: both leave every board that exists converting exactly as it
does now.

ADR-0098's standing gate earned its keep on this change and it is worth recording rather than
smoothing away. Moving the kind check into `_pad_kind` re-indented the source
`2026-08-13-pad-kind-domain.json`'s `PK3` mutant was anchored on, so that committed spec stopped
matching — `test_committed_mutant_specs_stay_anchored_to_current_source` failed in the full run and
named the mutant. It was re-anchored to the method and the spec re-run to `killed: 3`. Under the
hand-run practice ADR-0098 replaced, that claim would simply have gone quietly stale.

**What would have to become true is written down, so this can be revisited on evidence rather than
on appetite.** Section 8 of the research note lists three conditions and all three are required:
`Pad` would have to carry its obstacle envelope separately from its attachment core, which is a
schema widening at a published version and precisely the cost ADR-0096 declined to pay for a much
smaller distinction; every reader of pad geometry would have to be re-pointed at whichever of the
two it needs, with a later reader failing closed rather than silently taking the obstacle box as a
core; and the attachment core would have to be *derived* — a largest inscribed axis-aligned
rectangle in a union of an anchor and arbitrary primitives, exact in integer nanometres and inside
a budget. The third is the one that is easy to skip. Nothing here waits on new KiCad knowledge: the
geometry is settled, and a revisit would be a Board IR contract decision.

## Alternatives considered

- **Convert the custom pad as `PadShape.RECT` sized to the union's bounding box.** Rejected, and it
  is the option that looks correct. The box really is a sound obstacle — it is the copper's exact
  bounding box, derivable in integer nanometres. But `_pad_core_extent` reads the same `size` in
  the opposite direction and would hand a router an attachment core covering copper that is not
  there, and `_pad_overlap` would publish that as a `violated` legality verdict KiCad does not
  share. This repository has already shipped and fixed exactly this defect once, at much smaller
  magnitude, for a roundrect of ratio 0.5.

- **Convert it as the anchor rect, and count the discarded primitives.** Rejected. The anchor is
  genuinely copper (Context 3), so as attachment it is sound, and a count would be honest about the
  loss in the way `edge_connector_pad_count` is. But the pad is *also* an obstacle, and the anchor
  under-approximates it: on the corpus pad the anchor is well under half the pad's area, and the
  rest is real metal a router would be free to cross. A count does not make an unsound obstacle
  sound; it only
  makes it documented.

- **Split the pad: an anchor `Pad` for attachment plus the primitives' envelope as netless copper
  (the ADR-0092 treatment).** Rejected. Both directions do come out sound, which is why it was
  considered rather than dismissed. It fails on reach: the netless copper would have to be visible
  to every consumer that today derives an obstacle from `Pad` alone, and `placement/legalizer.py`'s
  pad-versus-pad and keepout rules read footprint pads, not board copper — so the placement
  legality surface would keep the anchor-only, under-approximating view while the router got the
  full one. Two obstacle models that disagree is worse than one refusal, and closing the gap is
  condition 2 of the revisit list under a different name.

- **Accept a bounded primitive subset — `gr_poly` with `(width 0) (fill yes)` — the ADR-0080
  treatment.** Rejected. ADR-0080 bounded a *courtyard*, which is only ever an obstacle, so a
  conservative envelope was the whole answer. A pad is an obstacle and attachment copper at once,
  and no restriction on which primitives appear changes that. The subset would convert every custom
  pad in one private corpus and would be justified by nothing else.

- **Refuse, but keep naming `options`.** Rejected. It is a true sentence about the wrong object.
  The reader's only available action — remove `(options …)` — produces a pad KiCad's own parser
  rejects, so the message actively misdirects. This is the failure mode ADR-0093 named for off-grid
  geometry and #152 is naming for the pad refusals.

- **Add a `PadShape.CUSTOM` member carrying the bounding box.** Rejected on ADR-0096's reasoning,
  which applies here with more force rather than less. `PadShape` reaches every snapshot digest and
  the published `0.2.0` schema promises a closed four-value domain; widening it in place would make
  a consumer reject a snapshot CopperMCP calls valid, silently, at a version that did not move. And
  unlike `PadKind`, `PadShape` *is* read — by `_pad_core_extent`'s `_CORE_MODELED_PAD_SHAPES`
  screen, which would return `None` for the new member and leave the pad with no attachment core at
  all. That failing closed is correct behaviour and it is also the point: the member would buy an
  obstacle and cost the pad its connectivity, which is the split alternative above with a schema
  bump attached.

- **Model the pad from KiCad's own `TransformShapeToPolygon` over IPC.** Rejected on ADR-0004 and
  ADR-0074 grounds without needing the geometry argument: the Board IR adapter is a pure,
  offline, source-preserving reader, and making conversion depend on a live KiCad would change what
  Board IR *is* to solve a representation problem inside it.

## References

- [KiCad 10.0.5 `PAD::MergePrimitivesAsPolygon`](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/pad.cpp#L3275-3315)
- [KiCad 10.0.5 `PAD::buildEffectiveShape`](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/pad.cpp#L1142-1295)
- [KiCad 10.0.5 pad primitive and option parsing](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp#L6315-6358)
- [KiCad 10.0.5 `ZONE_FILLER::addKnockout`](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/zone_filler.cpp#L1684-1712)
- [KiCad board file format: pads](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/)
- [KiCad custom pad envelope research](../research/kicad-custom-pad-envelope-v1.md)
- [ADR-0013](0013-polygon-zone-obstacles.md)
- [ADR-0091](0091-attaching-pad-zone-connect-overrides.md)
- [ADR-0095](0095-copper-text-has-no-derivable-envelope.md)
- [ADR-0096](0096-edge-connector-pads-convert-as-smd.md)
