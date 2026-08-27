# ADR-0123: A container refusal that names no field is the defect, not the refusal

- Status: Accepted
- Date: 2026-08-28
- Owners: `@seunghyukchoe`
- Related: [Issue #188](https://github.com/seunghyukchoe/copper-mcp/issues/188),
  [D-228](../ledgers/decision-ledger.md), [R-179](../ledgers/risk-register.md),
  [SEC-165](../ledgers/security-ledger.md),
  [B-132](../ledgers/benchmark-ledger.md) and B-133,
  [the footprint-field research note](../research/kicad-footprint-field-semantics-v1.md),
  [ADR-0122](0122-the-stackup-is-read-per-field-because-three-of-its-fields-are-not-about-z.md)
  (the same shape one level up),
  [ADR-0091](0091-attaching-pad-zone-connect-overrides.md) (the `zone_connect` partition this
  reuses),
  [ADR-0090](0090-root-groups-are-editor-organisation.md) (the group validator this reuses),
  [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md) (invoked and found not to
  apply)

## Context

[B-131](../ledgers/benchmark-ledger.md) measured what [D-227](../ledgers/decision-ledger.md)
bought and found the answer was *one gate and no layer*: all six boards whose terminal had been
`setup_semantics` moved to `footprint contains an unsupported semantic field`, **at exactly the
depth they had stopped at before**. It read the shape correctly and said so before this work
began:

> The setup wall was one instance of a structural class, not a construct: a container-level
> allowlist refusal whose diagnostic names the container and no field, which is precisely why
> B-129 could not mask it.

That sentence contains the actual defect, and it is worth stating plainly because it is easy to
mistake for a complaint about coverage. **The problem is not that the adapter refuses these
fields.** Refusing an unmodelled construct is correct and is the whole basis of ADR-0004's
authoritative-DRC backstop. The problem is that the refusal *says nothing about what it refused*.
Three consequences follow, and only the third is obvious:

1. A user cannot act on it. "Some field in footprint 68" does not tell anyone which line to
   delete.
2. A measuring instrument cannot decompose it. B-129 classified this terminal `unmaskable` not
   because the construct resisted masking but because **the diagnostic did not locate anything a
   per-field mask could delete**. The blocker map was therefore coarser than the boards.
3. Least obviously: it hides how much is already fine. B-132 found that on six real boards,
   **1,455 of 1,486** refused occurrences are two schematic provenance strings that reach no
   geometry in KiCad at all. A field-less refusal made a trivial gap and a copper-bearing one
   indistinguishable.

[B-132](../ledgers/benchmark-ledger.md) decomposed the surface, and it came back **closed** —
`other` is 0 at both levels — across 794 footprints:

| Head | Boards (of 6) | Occurrences | Class |
|---|---|---|---|
| `sheetfile` | **6** | 748 | provenance |
| `sheetname` | 5 | 707 | provenance |
| `solder_paste_margin` | 1 | 17 | stencil/mask |
| `solder_paste_margin_ratio` | 1 | 4 | stencil/mask |
| `group` | 1 | 4 | grouping |
| `zone_connect` | 1 | 4 | **copper-interacting** |
| `clearance` | 1 | 1 | **copper-interacting** |
| `solder_mask_margin` | 1 | 1 | stencil/mask |
| 20 further declared heads | 0 | 0 | — |
| anything else (`other`) | **0** | **0** | — |

The census also measured the *values* the two copper-interacting heads carry, as closed predicates
that commit no value: `zone_connect_attaching: 4`, `zone_connect_detaching: 0`, `clearance_zero: 0`.
Those three numbers decide two of the rows below, and they point in opposite directions.

## Decision

**Read the `footprint` block per field, name every refusal, and let the direction of error decide
each field separately.** Six heads are accepted and counted, one is accepted only in the modes
that cannot break the claim Board IR publishes, one is delegated to an existing validator, and
twenty refuse **by name**.

### 1 — The refusal names the field

`_UNSUPPORTED_FOOTPRINT_FIELDS` is the union of the top-level `case T_…` arms of
`PCB_IO_KICAD_SEXPR_PARSER::parseFOOTPRINT_unchecked` on KiCad `9.0` and `10.0`, minus the accepted
heads, minus the layer-routed `fp_*`/`property`/`point` branch, minus `zone`. It runs **before**
the allowlist, exactly as `_UNSUPPORTED_PAD_FIELDS` does one level down (ADR-0091, ADR-0099).

**Adding a head to this table changes a message and never a verdict.** Every one of them already
refused through the allowlist; the table only reaches them earlier and names them. That is why
this half of the decision needs no direction-of-error argument at all, and why no board's
conversion outcome moves with it. A head absent from the table still refuses, unnamed — and no
board byte is ever interpolated into a message, because the interpolated token is a literal from
this tuple selected by lookup.

### 2 — The two provenance strings

`sheetfile` has **six** read sites in KiCad and not one is geometric: the writer, a footprint swap,
netlist sync, and the repeat-layout tool. It appears in no `pcbexpr_functions.cpp` predicate, so
there is not even a custom-rule route. Accepted.

`sheetname` is not inert, and the honest statement is conditional. Twelve read sites, of which two
matter: `memberOfSheetFunc` registers the DRC-rule predicate `memberOfSheet('x')`, and
`COMPONENT_CLASS_MANAGER` auto-generates one component class per sheet name, reachable from a rule
via `hasComponentClass('x')`. A `.kicad_dru` rule can therefore *select* on the sheet name and
raise a clearance above the netclass value. **It is a selector for a value, never a value**, and it
reaches no `GetEffectiveShape`, no connectivity, no courtyard cache and no filler path directly.

Custom DRC rules are already entirely outside this adapter — it does not parse `.kicad_dru` and
never has — so this extends an existing, already-recorded exposure rather than opening a new one.
That is [R-179](../ledgers/risk-register.md), and it is stated rather than left to be inferred.

### 3 — The three mask and paste margins, plus the legacy ratio spelling

`solder_mask_margin`, `solder_paste_margin` and `solder_paste_margin_ratio` are the footprint-wide
defaults for the footprint's pads, resolved **pad → footprint → board design settings**. They move
solder-mask openings and solder-paste apertures and **no copper**, and the proof is a positive
citation rather than an absence sweep: `FOOTPRINT::TransformPadsToPolySet` switches on the target
layer, adding mask expansion only under `case F_Mask: case B_Mask:` and paste only under
`case F_Paste: case B_Paste:`, and **every copper layer falls to `default: break;`** with no
adjustment. `PAD::GetEffectiveShape` never mentions either. Nothing in `zone_filler.cpp`,
`pcbnew/connectivity/`, `pcbnew/router/` or `BuildCourtyardCaches` reads them.

This is precisely the argument D-227 already accepted for the board-level pair, so accepting the
per-footprint twins is consistency and not a new claim. The sign does not change it: a negative
mask margin shrinks an opening (and is floored so the opening cannot invert), a positive one grows
it, and neither adds or removes a nanometre of copper.

`solder_paste_ratio` is carried because KiCad carries it. It is the spelling the **8.0** writer
emitted; 9.0 emits `solder_paste_margin_ratio`; both still parse into one arm, marked
`// legacy token` in KiCad's own source. A reader that knows only the new spelling silently
mis-reads every 8.0-written board — which is the kind of gap this project treats as a defect, not
as out of scope.

### 4 — `zone_connect`, accepted only where it attaches

This is [ADR-0091](0091-attaching-pad-zone-connect-overrides.md)'s partition, one level up, and the
transfer is exact in the part that matters. `PAD::GetZoneConnectionOverrides` consults the pad's
own value and falls back to the parent footprint's, so the footprint default governs every pad that
omits its own.

- The **obstacle** direction cannot break for any value: `ZONE_FILLER` intersects the finished fill
  with the zone's own extents, so poured copper stays a subset of the zone boundary whatever the
  mode is.
- The **connectivity** direction breaks for exactly one value. `1`, `2` and `3` all attach —
  `DRC_ENGINE::EvalZoneConnection` collapses `3` to thermal on a plated through-hole pad and to
  solid on any other — so discarding one can leave the published *mode* wrong in either direction
  while still answering "attached". `0` detaches, and discarding it would leave Board IR publishing
  attachment for every non-overriding pad of a footprint whose designer deliberately isolated them
  all. That is the one direction this project forbids.

So `1`/`2`/`3` are accepted and `0` refuses. KiCad casts the token straight to the enum with **no
range check**, so the domain is checked here rather than assumed.

**One thing does not transfer, and it is recorded rather than smoothed over.** The pad override is
consumed in `DRC_ENGINE::EvalRules` *before* custom-rule iteration, which is what lets ADR-0091 say
a rule cannot detach a pad the file attached. The **footprint** override is consumed *after* rule
iteration, so a custom rule **can** override it — including detaching pads the footprint declared
`2`. This does not make the acceptance unsound, because a rule can detach any pad whether or not
the footprint writes the token, so the exposure is the same unread-`.kicad_dru` one as `sheetname`.
It does mean the pad ADR's load-bearing sentence must not be restated for footprints, and it is not.

### 5 — The footprint-local group is not a new construct

KiCad dispatches a footprint's `(group …)` to the **same** `parseGROUP` as a root one and writes it
from `aFootprint->Groups()` through the same formatter. So it takes the same validator
([ADR-0090](0090-root-groups-are-editor-organisation.md)), the same closed child grammar, the same
positional-atom check, the same **lock refusal**, and the same `unmodelled_group_count`.

One counter rather than two is deliberate: a caller asking "how many groupings did I lose" wants
one number, and splitting the same construct across two would answer neither question. This changes
the meaning of a published count, so it carries a migration note.

**The lock refusal rests on the query-time derivation, not on a pass ordering.**
`BOARD_ITEM::IsLocked()` opens by consulting `GetParentGroup()`, so a locked group makes every
member locked in KiCad's model, transitively, without any member's s-expression saying so — and
lock is a hard authorization gate here, not a hint. Counter-evidence was found and deliberately not
leaned on: `resolveGroups` happens to call `SetLocked` on a group *before* its members are
attached, so the eager propagation reaches nothing at load time. That is a consequence of one
function's pass ordering rather than a documented guarantee, and a refactor that attached members
first would silently change it. The refusal does not depend on it.

### 6 — `clearance` refuses, and the narrowed acceptance is declined on evidence

`FOOTPRINT::GetLocalClearance` reaches `DRC_ENGINE::EvalRules` through `GetClearanceOverrides`,
and from there every copper-clearance test provider, `ZONE_FILLER::buildCopperItemClearances` —
where it **sizes the void the pour leaves around every pad of that footprint** — and the router,
both as a queried constraint and as the seed for `syncWorld`'s worst-case radius.

The direction of error is not the intuitive one, and the intuition is worth naming so it is not
re-derived wrongly later. **A footprint `clearance` is a replacement, not a maximum.** The override
block returns *before* custom-rule iteration, so it beats netclass *and* rules, and can therefore
*lower* the effective clearance to the board minimum. "Ignoring a clearance field is conservative"
is **false** here. A tool cannot tell which side a given value falls on without evaluating the
rules, so any present non-zero value must be honoured or refused.

A narrowed acceptance would have been sound: the override block returns early only
`if( override_val )`, so a footprint `(clearance 0)` falls through to the ordinary rule path and is
genuinely inert — exactly the shape D-227 used for `edge_plating`. **B-132 measured
`clearance_zero: 0`.** Not one of the cohort's `clearance` occurrences is the inert zero, so the
narrowed rule would clear no board while adding accepted input surface. It is declined on that
measurement, and recorded here rather than omitted, so a later slice with different evidence can
revisit it without re-deriving the argument.

### 7 — Payload validation is built in, not added after review

Every accepted leaf gets a closed payload grammar in the same change that accepts it: **no child
expression at all**, exact arity, and a checked token kind, each refusing at the field's own
locator. The `#225` review round established that an accepted container without a payload grammar
is an unread container and a real smuggling surface — a nested child inside an accepted leaf
carried past the very head the allowlist one level up exists to refuse. That lesson is applied
here in advance rather than after a reviewer finds it again.

### 8 — No schema move

Per [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md) the accepted set that governs
the version is the **emitted document's**. No field here enters `BoardIRSnapshot`: every accepted
head is validated and discarded, and the disclosure is a `ConversionResult` counter published
through `BoardIrSummary.unmodelled_counts`, which no file under `schemas/` describes.
`schemas/board-ir/0.4.0.schema.json` is byte-unchanged, `BOARD_IR_SCHEMA_VERSION` stays `0.4.0`,
and `check_schema_sets.py` passes with **no exemption added**. This is the `thermal_bridge_angle`
shape (D-205) and the D-227 shape; #192's custom pads moved 0.3→0.4 because they added *modelled
geometry*, which this does not.

## Consequences

**What a caller gains.** A refusal inside a footprint now names its field, so it is actionable and,
just as importantly, decomposable by an instrument. Twenty heads say what they are. Six are read
and disclosed through `unmodelled_footprint_field_count`; footprint-local groups join the existing
`unmodelled_group_count`.

**What a caller loses, stated as a loss.** A snapshot alone no longer carries a footprint's
schematic origin or its per-footprint stencil and mask defaults. A consumer regenerating fabrication
output from Board IR alone would emit default apertures. That is [R-179](../ledgers/risk-register.md),
and the counter is its typed disclosure rather than its remedy.

**What this did not buy, measured rather than assumed.** [B-133](../ledgers/benchmark-ledger.md)
records it: **0 of 13 boards convert, before and after**; the public gate-stack depth histogram is
**byte-identical**; and the closed blocker histogram does not move either. All ten public boards
refuse *in front of* `footprint`, so this surface is reachable only behind B-129's deliberately
unsound masks — which was stated before the work rather than discovered after it.

**The predicted successor was wrong, and the refutation is recorded.** The prediction named the
pad-level allowlist as the next wall for the boards that cleared this one. It is not: they land on
`footprint graphic on a copper layer is unmodelled copper`, `footprint graphic on Edge.Cuts is
unsupported` and `footprint-local zones are unsupported` — refusals that already named their
construct and were simply queued behind this one.

**A per-board prediction was wrong for an instructive reason.** One board carrying a non-zero
`clearance` was predicted to remain at a named `clearance` refusal. It does not: an earlier
footprint on the same board carries a footprint-local zone, and a board's terminal is its **first**
refusal in document order, not a function of which fields it contains. The `clearance` refusal is
real and reachable — it sits on footprint 178 while the zone sits on footprint 9 — but shadowed.
Reasoning about set membership where the code reasons about document order is the error, and it is
worth naming because a census that reports per-field presence invites exactly it.

## Alternatives considered

**Accept the whole `footprint` block, as a container.** Rejected for the reason #225's review round
established one level up: an accepted container without a per-field grammar is an unread container.
It would also have admitted `clearance` and a detaching `zone_connect`, both of which can
under-report copper or claim connectivity the board lacks.

**Keep the field-less refusal and only widen the allowlist.** Rejected. It would have converted the
same boards while leaving the diagnostic undecomposable, so the next instrument down would face the
identical wall B-129 faced — and the point of this slice is the structural class, not the six
boards.

**Accept `clearance` when zero.** Sound, and declined on measurement rather than on taste; see §6.

**Add a second counter for footprint-local groups.** Rejected: KiCad treats the two as one
construct, and two counters would leave a caller unable to answer the only question the number
exists for.

**Model any of these fields into `BoardIRSnapshot`.** Rejected for now. It is the named exit for
R-179 and it is the one change here that *would* move the schema under ADR-0105. Nothing measured
today needs it.

## References

- [B-132 and B-133](../ledgers/benchmark-ledger.md); the census artifact
  `benchmarks/results/board-ir/2026-08-28-public-footprint-field-census-v1.json` and the
  differential `…-footprint-acceptance-masking-differential-v1.json`
- [The footprint-field research note](../research/kicad-footprint-field-semantics-v1.md), which
  carries every KiCad source citation this record summarises
- KiCad source mirror, branches `9.0`, `10.0` and `master`:
  `pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp`, `…/pcb_io_kicad_sexpr.cpp`,
  `pcbnew/footprint.cpp`, `pcbnew/pad.cpp`, `pcbnew/drc/drc_engine.cpp`, `pcbnew/zone_filler.cpp`,
  `pcbnew/pcbexpr_functions.cpp`, `pcbnew/pcb_group.cpp`, `pcbnew/zones.h`
- [KiCad S-expression format](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/)
