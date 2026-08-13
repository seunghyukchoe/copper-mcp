# Migrating a deployment to CopperMCP 0.8.0

CopperMCP 0.8.0 changes four things a working 0.7.0 deployment can depend on without having
declared it: the default value of a denial-of-service control, the sentence more than twenty Board
IR refusals emit, the set of placement verdicts a board with a far-side courtyard produces, and the
contents of two published artifacts — `schemas/board-ir/0.2.0.schema.json` and the Circuit Scene
annotation vocabulary — that changed **without their version strings moving**.

**No content address moves in this release.** `ROUTER_VERSION` stays `astar-grid/0.7.0`,
`BOARD_IR_SCHEMA_VERSION` stays `0.2.0`, `SCENE_VERSION` stays `0.3.0`, and
`tests/test_golden_identities.py` is unchanged and passing. Nothing you have stored stops
verifying. That claim is checked against every version constant in `src/` rather than inferred
from the absence of a release note: the [0.7.0 note](copper-mcp-0.7.0.md) carried exactly this
claim while `ROUTER_VERSION` was in fact moving under it, and it was falsified by a re-read at
release time rather than by any gate. A "nothing moves" sentence is worth only the sweep behind
it.

Read §1 first — it is the only item in this release that is a **decision** rather than a
compatibility fix. [What does not require migration](#what-does-not-require-migration) names this
release's other landed changes and says why each one is additive, rather than leaving you to infer
it from silence.

## 1. `max_fill_vertices` moves from 50,000 to 500,000 — decide, do not just accept

`max_fill_vertices` bounds the cached zone-fill vertices `read_fill_islands` will admit from one
board, summed across every island. Its 50,000 default came from a single fixture — ADR-0021 records
it as "CopperTone's pour is 4,314 vertices across two layers" — and real pours are 12–30× that. Of
eighteen private-corpus boards carrying a cached pour, seven run 50,482–130,305 vertices and one
misses the old budget by 482. Because `run_zone_fill_authority` must read the cache before it can
compare it to a refill, those seven were refused for a *resource* reason before freshness was ever
considered ([ADR-0104](../adr/0104-fill-vertex-budget-behind-a-parse.md),
[D-195](../ledgers/decision-ledger.md), #165).

**This is a change to a denial-of-service posture, so it is priced rather than implied.** Against
the densest reachable document — 741,375 vertices in 4,096 islands, 6,094,870 bytes — the change
moves a refusal from **29.325 s / 146.8 MiB to 35.812 s / 168.4 MiB**: it buys an attacker **6.5
seconds and 21.6 MiB**, +22 % wall time and +15 % peak, on top of a floor this budget has never
been able to move. The floor is the parse. `read_fill_islands` parses the whole document before it
counts a single vertex, so refusing the largest corpus board at a budget of 3 still costs 20.9 s of
the 24.2 s a complete read costs; what this budget meters is only the materialisation of
already-parsed atoms, at 22–26 µs and 113–145 bytes per admitted vertex
([SEC-144](../ledgers/security-ledger.md), [B-108](../ledgers/benchmark-ledger.md)).

The range is **unchanged at 3 – 1,000,000**. It deliberately does not move with the default.

To migrate:

- **If the larger transient allocation is acceptable**, do nothing, and expect §1's two verdict
  consequences below.
- **If it is not**, set `COPPER_MCP_MAX_FILL_VERTICES=50000`. That restores the previous posture
  exactly — the dataclass default and the environment default are now pinned equal by a test, so
  the two cannot drift apart.
- **Do not read this budget as your main defence against a hostile fill list.** That defence is
  `ParseLimits` ([ADR-0079](../adr/0079-discriminated-configurable-parse-budgets.md)), which also
  caps the reachable population at **741,375** vertices at the shipped parse defaults. An operator
  who wants a materially better posture must lower `COPPER_MCP_MAX_PARSE_NODES`, which is the
  control that actually binds.

**Two verdict consequences, neither of them a defect.**

1. **A board that reported a budget refusal may now report `stale_fill`.** On the corpus the split
   moves from 9 `fresh` / 2 `stale_fill` / 7 budget-refused to **12 `fresh` / 6 `stale_fill` / 0
   budget-refused**. Half a working designer's zoned boards carry a pour KiCad does not reproduce;
   that was always true and was hidden behind a ceiling. A caller that treated a fill-budget
   refusal as "this board is too big" will now be told the honest thing, which is that the board's
   cached pour is stale.
2. **A refusal moves rather than disappearing.** This budget meters a board's *total* pour, while
   every consumer of the islands charges the *widest island*:
   `routing.layered_board_adapter._MAX_FILL_VERTICES` refuses any single island above 4,096. The
   two populations are not proportional — one corpus board holds 61 % of its vertices in one ring —
   and **14 of 18 corpus boards carry an island above 4,096**, the widest 43,889. Seven of those
   could not be read at all before. They will now read and then be refused per-island, so expect
   `verified fill island is not a bounded polygon` where a fill-budget refusal used to be. Sizing
   that per-island ceiling is a separate calibration, filed as
   [#167](https://github.com/seunghyukchoe/copper-mcp/issues/167) under
   [R-150](../ledgers/risk-register.md) rather than ridden in on this change.

Raising the budget is **answer-preserving**, and that is proved from the budget rather than from a
with-and-without differential: `max_vertices` reaches exactly one expression in the reader — the
guard that aborts `_points` — so it decides *whether* a read happens and never *what* it returns.
Above the threshold the islands, the `fill_digest` and `ZoneFillAuthority.to_dict()` are identical
from the vertex count to the ceiling.

## 2. Board IR refusal messages move, and three disappear

No diagnostic **code** on the board-reading side was added, removed or repurposed in this release.
Every row in §2b, §2c and §2d keeps `unsupported.construct` and keeps its `object_kind`, with one
exception noted in §2c. What moves is the message — which was never a contract, and which some
callers nevertheless match on, because for several of these constructs the message was the only
thing that distinguished them.

### 2a. Three messages a caller will never see again

| Message removed in 0.8.0 | Code it carried | Why | Where |
|---|---|---|---|
| `edge-connector pads are unsupported` | `unsupported.construct` | a `connect` pad now converts as `SMD` | [ADR-0096](../adr/0096-edge-connector-pads-convert-as-smd.md), #149 |
| `root board properties are unsupported` | `unsupported.construct` | a root `(property "<key>" "<value>")` is now read past and counted | [ADR-0094](../adr/0094-root-board-properties-as-metadata.md), #150 |
| `courtyard layer does not match its footprint side` | `unsupported.transform` | the arrangement it named is not an error; see §3 | [ADR-0097](../adr/0097-courtyard-layer-decides-the-side.md), #154 |

Each of these was a *whole-board* refusal, so a caller matching one of them was detecting a class
of board it could not otherwise read. Those boards now reach the converter. Two of the three do not
yet convert on the surveyed corpus — they stop one construct later — so **do not read a removed
refusal as a conversion win**; read it as the frontier moving.

### 2b. Nineteen pad fields move off the generic sentence

0.7.0 gave seven pad fields their own sentence and left the rest behind
`expression contains an unsupported semantic field`, which names nothing an operator can act on.
0.8.0 names the rest ([ADR-0099](../adr/0099-pad-fabrication-properties-and-named-pad-refusals.md),
[D-189](../ledgers/decision-ledger.md), #152). These nineteen heads now report
`pad field '<name>' is unsupported`:

`back_post_machining`, `backdrill`, `chamfer`, `chamfer_ratio`, `die_delay`, `die_length`,
`front_post_machining`, `keep_end_layers`, `padstack`, `rect_delta`, `sim_electrical_type`,
`solder_mask_margin`, `solder_paste_margin`, `solder_paste_margin_ratio`, `teardrops`, `tenting`,
`tertiary_drill`, `thermal_width`, `zone_layer_connections`.

**Any caller matching the exact string `expression contains an unsupported semantic field` in order
to detect one of these nineteen stops matching** — exactly as [the 0.7.0
note](copper-mcp-0.7.0.md#8-seven-pad-refusals-that-could-never-fire-now-fire-with-different-text)
warned for the first seven. **No board's outcome changes for any of the nineteen**: every one
already refused, and only the sentence moved. The generic message still exists, for a head KiCad
itself cannot write, so a caller matching it is now matching a strictly smaller set.

One precision the CHANGELOG's count does not carry: nineteen is the count against KiCad **master**,
whose `parsePAD` accepts 39 top-level heads. Shipping KiCad 10.0.5 accepts 38 — the difference is
exactly `sim_electrical_type` — so eighteen of the nineteen are reachable from a file a 10.0.5
installation can write.

### 2c. Three constructs that refused generically now refuse by name

| Construct | 0.7.0 message | 0.8.0 message |
|---|---|---|
| Root `gr_text` / `gr_text_box` on a copper layer | `root graphic on copper is unsupported`, `object_kind: graphic` | `copper text has no envelope derivable from the board and is unsupported`, `object_kind: text` |
| A `custom`-shape pad | `pad field 'options' is unsupported` | `a custom pad shape has no single region that both contains its copper and is contained by it, and is unsupported` |
| A `trapezoid`-shape pad | whichever field the closed allowlist reached first | `trapezoid pad shapes are unsupported in Board IR adapter v0.2` |

Under 0.7.0 the pad field loop ran before the shape check, so a pad whose *shape* was the real
problem was reported by whichever token the allowlist reached first — for a custom pad that is
`options`, which KiCad's writer emits only for a custom pad and which cannot be removed without
producing a pad KiCad will not read back. 0.8.0 decides kind and shape first, so the shape's own
sentence wins. `options` and `primitives` remain in the unsupported-field table with their
reachability pinned by a test, because KiCad's parser accepts both on a pad of any shape.

None of the three changes a verdict. Copper text refused before and refuses now, deliberately and not
provisionally ([ADR-0095](../adr/0095-copper-text-has-no-derivable-envelope.md), #141); a custom
pad refused before and refuses now, and the record is explicit that the geometry was never the
problem ([ADR-0100](../adr/0100-custom-pads-have-an-envelope-and-nowhere-to-put-it.md), #153).
Every other graphic head on copper keeps the old sentence and `object_kind: graphic`.

### 2d. Four new sentences on a construct that used to refuse unconditionally

Seven pad fabrication properties now convert — see
[What does not require migration](#what-does-not-require-migration). The eighth,
`pad_prop_castellated`, refuses under its own sentence:

```text
pad fabrication property 'pad_prop_castellated' removes board area the outline still claims and is unsupported
```

Three shape refusals accompany it, for the looser forms KiCad's own reader tolerates:
`pad declares more than one fabrication property`, `pad fabrication property is not a single bare
token`, and `pad fabrication property is unsupported`. **No board that converted under 0.7.0 is
refused by any of these** — a pad `(property …)` refused unconditionally before this release, so
every one of these rows is strictly narrower than what it replaced.

### To migrate

1. **Match the diagnostic code plus `object_kind`, and read the message for a human.** Both are
   stable across every row above — `unsupported.construct` everywhere except the removed courtyard
   refusal, which was `unsupported.transform` and whose code is still in use elsewhere.
2. **If you match message text, re-derive your table from this section.** The three removed
   messages in §2a are the ones that will fail silently — they name no board any more, so a matcher
   keyed to them simply never fires, and a board it was there to catch flows past.
3. **Do not treat the generic message's disappearance for the nineteen as a behaviour change in
   what converts.** It is not.

## 3. A courtyard keeps out on the layer it is drawn on, so a placement verdict can flip

Board IR refused every board on which a footprint carried a courtyard on the opposite courtyard
layer. That refusal described a mismatch KiCad does not recognise:
`FOOTPRINT::BuildCourtyardCaches` files each shape by the shape's own `F.CrtYd`/`B.CrtYd` layer and
never reads the footprint's side, and the courtyard DRC provider compares front against front and
back against back with no side test at all. The stock KiCad library ships parts that depend on it
([ADR-0097](../adr/0097-courtyard-layer-decides-the-side.md),
[D-187](../ledgers/decision-ledger.md), #151).

**This tightens a published verdict rather than loosening one, and that is the migration.**
Placement legality now pairs courtyards **by layer** instead of by footprint side, so two coincident
`B.CrtYd` rectangles collide whether their footprints sit on opposite sides or both on the front.
The previous same-side pairing published that arrangement as `proven_clear`. Real `kicad-cli`
10.0.5 calls it a collision, and so does 0.8.0.

**A `proven_clear` verdict you recorded under 0.7.0 for a board carrying a far-side courtyard may
be `violated` under 0.8.0.** The overlap was always there; the model was too narrow to see it.
Cross-layer contact is still not a collision, and the 10,000 nm collision threshold is the same on
both courtyard layers.

The magnitude is real. On the 2026-08-13 replay of the real-board survey, placement preview
returned **1,283 verdicts: 1,127 previewed and 156 `refused/illegal_placement`**, the 156
concentrated on one board — against B-099's earlier "991 of 991 previewed, 0 refusals" on a smaller
corpus. Those two numbers are **not** a before/after on the same input; the corpus grew between
them. What the widened courtyard model contributes is the 156, and
[B-099's survey close-out replay](../ledgers/benchmark-ledger.md) attributes them to
ADR-0080 and ADR-0097 finding overlaps that were always there rather than to a new defect.

To migrate:

1. **Re-run placement preview for any stored `proven_clear` verdict** on a board whose footprints
   draw courtyards on the layer opposite their side. If none of your boards do, nothing changes:
   measured against `kicad-cli` on 13 boards with courtyard layer and footprint side varied
   independently, the result was 12 exact parity, 1 conceded `inconclusive` in the known
   sub-threshold band, and 0 contradictions.
2. **Do not union `far_side_courtyards_nm` with `courtyards_nm`** in a Circuit Scene, or
   `far_side_courtyards` with `courtyards` in a Board IR snapshot. They keep out on the other side
   of the board, and a consumer that pools them reads a keep-out on a side that has none.
3. **Expect the write-back path to be unchanged and still to refuse.** The source-preserving
   placement serializer refuses any board carrying a far-side courtyard rectangle, so such a
   footprint is previewable and not movable through it — another board class, after 0.7.0's net
   ties, where "converts and previews" does not imply "can be written back".
4. **Know that the 64-shape courtyard ceiling now counts both layers against one total**, in the
   adapter and in the decoder. No snapshot representable before this release can exceed it, because
   no such snapshot could carry a far-side shape at all.

## 4. Two published artifacts changed without their version strings moving

This is the item most likely to be missed, because every record in this release correctly says "no
schema version bumps" — and that is true, and it is not the whole answer.

### 4a. `schemas/board-ir/0.2.0.schema.json` changed in place

`$defs/footprint` declares `"additionalProperties": false`. 0.8.0 adds two optional properties to
it, `far_side_courtyards` and `far_side_courtyard_circles`, under the **same** `0.2.0` filename and
the same `BOARD_IR_SCHEMA_VERSION`.

**A third party validating against a copy of this file downloaded before 0.8.0 will reject a
snapshot of a board carrying a far-side courtyard.** The payload is valid; the validator is stale.

Why this is not a version bump: the canonical encoder omits each key entirely when the footprint
has nothing to put in it, so **every board representable before this release encodes byte for
byte as it did** and no stored snapshot's digest moves. There is no payload that was valid under
the old file and is invalid under the new one — the change is a pure widening. It is called out
here because the file is a downloadable artifact whose name did not change, and a consumer has no
way to notice from the version string alone.

To migrate: **re-download `schemas/board-ir/0.2.0.schema.json`** if you validate Board IR snapshots
against a vendored copy. Nothing else is required, and no re-validation of stored payloads is
needed — they all still pass.

### 4b. Two closed MCP contracts widened

| Contract | Change |
|---|---|
| `SceneAnnotationContract.origin` | gains a fourth value, `board_property`, beside `board_text`, `silkscreen`, `footprint_property` |
| `FootprintGeometryContract` | gains optional `far_side_courtyards_nm` and `far_side_courtyard_circles_nm` |

**A client that pins `SceneAnnotation.origin` to the previous three values must widen it.** No board
could have produced `board_property` before this release, because every board carrying a root
property was refused — so this cannot invalidate a scene you already hold, and it can appear on the
very next scene you request. Both halves of each root property pair are returned under the new
origin, charged against the same annotation ceiling, with the same `trust: untrusted_board_author`
as every other annotation ([ADR-0094](../adr/0094-root-board-properties-as-metadata.md),
[ADR-0022 amendment](../adr/0022-circuit-scene-observation.md), #140).

A scene consumer that ignores the two new geometry keys keeps working, and one that reads them
cannot receive an empty array — they are emitted only when non-empty.

## 5. The zone-fill budget refusal loses the word "cached"

`read_fill_islands` is called twice per freshness proof — once on the board in the workspace, once
on the copy KiCad refilled — and its budget refusal hardcoded the word "cached", so the second call
site produced the self-contradicting `refilled zone fill could not be read: cached zone fill
exceeds the configured vertex budget`. Both call sites already name their document, so the reader
no longer guesses (#165).

| | 0.7.0 | 0.8.0 |
|---|---|---|
| Cached read | `cached zone fill could not be read: cached zone fill exceeds the configured vertex budget` | `cached zone fill could not be read: zone fill exceeds the configured vertex budget` |
| Refilled read | `refilled zone fill could not be read: cached zone fill exceeds the configured vertex budget` | `refilled zone fill could not be read: zone fill exceeds the configured vertex budget` |

To migrate: a caller matching either composed string exactly stops matching. Match the
`… could not be read:` prefix, which is what distinguishes the two documents and is unchanged, or
stop matching this text at all — after §1 this refusal is far harder to reach.

## 6. Two new fill-evidence gates on the single-layer router, for in-process callers only

The single-layer A* core now applies the two gates the ordered-layer adapter already had. A
`verified_fill` island is accepted only if it carries at least three vertices and its bounding box
lies inside the bounding box of a backing zone of the same net and layer. Both refuse under the
pre-existing `unsupported_geometry` code ([ADR-0101](../adr/0101-fill-currency-is-not-in-the-document.md),
[D-192](../ledgers/decision-ledger.md), [R-147](../ledgers/risk-register.md), #63):

```text
verified zone fill island is not a closed ring
verified zone fill escapes its backing Board IR zone outline
```

**These gates are refusal-side, which means some inputs that produced a candidate under 0.7.0 now
refuse.** No route that is refused today becomes possible. The affected caller is one using the
typed in-process seam `AStarRouter.propose(…, verified_fill=…)` with evidence that never went
through `run_zone_fill_authority`; honest KiCad evidence already satisfies both gates, because
KiCad clips poured copper to the zone outline. **On the published `preview_route` path nothing
changes.**

A board routed *without* fill evidence spends exactly the obstacle-check budget it always did —
zone outline bounds are measured only when evidence is present, and a regression test pins that
fixture's count at 684 rather than comparing two calls that would move together.

Separately, and **not** introduced by this release: `include_fill_authority` combined with
`include_drc` or with `include_apply_token` **was not a supported combination in 0.7.0 and is not
one on this release's `main`.** `AStarRouter.replay` drops the fill evidence, so a fill-routed
candidate does not reproduce — 8,000 nm replays as 14,000 nm on B-021's own fixture — `include_drc`
then refuses a legitimate candidate, and `include_apply_token` mints a token whose apply is
guaranteed to fail. Do not describe that combination as supported, and do not build on it.

## What does not require migration

Each entry below is a landed change in this release whose answer is "nothing to do", with the
reason rather than the assertion.

- **Seven pad fabrication properties now convert**
  ([ADR-0099](../adr/0099-pad-fabrication-properties-and-named-pad-refusals.md), #152). `(property
  pad_prop_bga | pad_prop_fiducial_glob | pad_prop_fiducial_loc | pad_prop_heatsink |
  pad_prop_mechanical | pad_prop_pressfit | pad_prop_testpoint)` is accepted as exactly one bare
  positional atom, at most one `property` per pad. This is pure widening: a pad `(property …)`
  refused unconditionally under 0.7.0, so no 0.7.0 deployment can hold a snapshot, candidate or
  digest derived from one, and nothing it holds stops verifying. **Nothing is modelled** — no `Pad`
  field, no schema change, no pinned identity. The discarded token is disclosed as
  `ConversionResult.unmodelled_pad_property_count`. One consequence worth knowing before you meet
  it: a caller reading `Pad` cannot tell that the designer marked a pad a heatsink, a test point or
  a BGA ball, and one generating fabrication output from a snapshot alone would emit the wrong
  aperture attribute. The write path bounds the loss — both patch adapters are source-preserving
  splices, so the token survives in the `.kicad_pcb` byte for byte and KiCad's own outputs still
  see it.
- **An edge-connector `connect` pad converts as `SMD`**
  ([ADR-0096](../adr/0096-edge-connector-pads-convert-as-smd.md), #149). Widening, for the same
  reason: every `connect` pad refused before. `PadKind` gains no member, the published `0.2.0`
  `enum` is untouched, and no pinned identity moves. The same disclosure caveat applies — a caller
  reading `kind == "smd"` cannot tell the designer wrote `connect`, the count is
  `ConversionResult.edge_connector_pad_count`, and the token survives the source-preserving splice
  (pinned by `test_a_placement_splice_leaves_an_edge_connector_pad_token_intact`). One form still
  refuses: a `connect` pad with no copper layer at all, which is unchanged behaviour rather than a
  new restriction. **Correction inherited from that record:** an earlier draft said the distinction
  was "discarded loudly". It is not. From an MCP client the discard is silent — see the next entry.
- **`ConversionResult` gains three fields**, all keyword-defaulted and appended last:
  `edge_connector_pad_count`, `unmodelled_board_property_count`, `unmodelled_pad_property_count`. A
  caller constructing one is unaffected; a caller exhaustively destructuring one should expect
  them. **Know their reach before you plan around them:** all three are in-process values on the
  adapter result and reach **no MCP contract, CLI output or Circuit Scene**, so from an MCP client
  every one of these discards is silent. That is equally true of the pre-existing
  `unmodelled_group_count` and `max_roundrect_rounding_nm` — a property of the measured-field
  pattern rather than something this release introduces
  ([R-141](../ledgers/risk-register.md), [R-139](../ledgers/risk-register.md),
  [R-144](../ledgers/risk-register.md)).
- **A board that converted under 0.7.0.** Every conversion-side change in this release widens what
  is accepted or moves a message. Nothing narrows what converts: the removed refusals in §2a all
  admitted more boards, the seven pad properties and the `connect` pad are acceptances, and every
  new refusal sentence in §2c and §2d replaces a refusal that already fired on the same board. §6
  is the one narrowing anywhere in the release, and it is on the router rather than the converter,
  and only through an in-process seam.
- **Every Board IR diagnostic *code*.** No code was added, removed or repurposed on the
  board-reading side. What moved is message text, the set of boards refused, and one new value —
  `text` — in the free-form `object_kind` field.
- **Route candidate, bundle and export identities.** `ROUTER_VERSION` is unchanged at
  `astar-grid/0.7.0`, and unlike 0.7.0 there is nothing to re-derive. `LAYERED_ROUTER_VERSION`,
  `NEGOTIATED_ROUTER_VERSION`, `SCENE_VERSION`, `BOARD_IR_SCHEMA_VERSION` and every schema-version
  constant in `src/` are byte-identical to their 0.7.0 values.
- **The reproducible mutation-evidence standard**
  ([ADR-0098](../adr/0098-reproducible-mutation-evidence.md), #155). Repository tooling and record
  keeping; no server code changed and no board behaviour moved. It does change how you should
  *read* this project's evidence, which is why it is named here rather than omitted: a defect in
  the uncommitted scratch harnesses behind all 24 hand-applied mutation runs published before this
  release means a stale `__pycache__` entry could register a **false kill**, so "0 survivors" was
  the optimistic side of the error. Every prior claim on `main` is now classified from its own
  record — 4 `safe`, 2 `exposed`, 24 runs `unauditable`, 0 shown false — and qualified in place
  rather than retracted. Cite a pre-ADR-0098 mutation claim as `unauditable` rather than as a
  settled kill count.
- **The excessive-agency evaluation now observes a permit**
  ([ADR-0102](../adr/0102-an-evaluation-must-observe-a-permit.md), #110). **No server code
  changed**; this is an evaluation change only, touching `scripts/`, `tests/`, a fixture catalog
  and a regenerated benchmark artifact. The authorized apply path was never unreachable and never
  excluded — it was unnamed and unguarded. Nothing a deployment does changes.
- **The real-board survey close-out** (#116, #156). Documentation. It supersedes published figures
  rather than editing them where they were written, and two of those supersessions are worth
  carrying: real-board route preview is **0 of 425** today against B-096's 14 of 385, because 320
  previews now report `already_connected` — the designer routed those nets — and B-099's
  "placement preview never binds on this corpus" is false today. Neither is a regression and
  neither requires action.
- **The link-label documentation checker** (#145). Repository CI.
- **Tightening any budget below its shipped default** remains supported, including
  `COPPER_MCP_MAX_FILL_VERTICES` back to `50000`.

No board, snapshot, candidate or persisted artifact is rewritten by this upgrade. Nothing is
migrated in place, nothing is destroyed, and — unlike 0.7.0 — there is nothing you must re-derive.
The two things you must *re-read* are a vendored copy of the Board IR 0.2.0 schema file (§4a) and
any table of refusal message text (§2).

## How to read this release's counts

Three cautions, so that a number in the [changelog](../../CHANGELOG.md) entry for one change is not
read as a claim about another.

- **Conversion counts in this release are point-in-time and are not comparable across entries.**
  The private survey corpus grew from 17 boards to 18 saves during this release's work, and each
  entry states the before/after differential measured at its own baseline. The release-level figure
  is **13 of 18 saves**, holding **17 distinct board contents** — two saves are byte-identical — at
  the last measurement.
- **Route preview is not a whole-board result.** It runs one net at a time on `F.Cu` against the
  unrouted snapshot, so its candidates are **not mutually compatible**. Placement preview runs
  **without rules**, so a clean verdict means legal-as-found and never placement quality.
- **An authoritative KiCad DRC count is not reproducible run to run on identical bytes.** Two
  baseline runs at the same commit over byte-identical files disagreed on nine boards, entirely
  within the `drc` section. Any DRC-derived comparison in this release — including §3's
  courtyard-parity measurement against `kicad-cli` — was taken once per board and has no stated
  tolerance. That does not make any recorded count false when taken; it makes it unfalsifiable
  under repetition, which is
  [issue #170](https://github.com/seunghyukchoe/copper-mcp/issues/170)'s finding.
