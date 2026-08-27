# KiCad footprint fields: which reach copper, and in which direction each fails

Research date: 2026-08-28. This note establishes, from KiCad's own format definition and from the
KiCad `9.0`, `10.0` and `master` source branches, what each `(footprint …)` child that CopperMCP's
Board IR adapter did not previously accept does to copper geometry, electrical clearance,
connectivity, keepout and courtyard — and therefore which of them can be read past without
under-approximating copper. It supports [issue #188](https://github.com/seunghyukchoe/copper-mcp/issues/188),
[ADR-0123](../adr/0123-a-container-refusal-that-names-no-field-is-the-defect.md), decision
[D-228](../ledgers/decision-ledger.md), risk [R-179](../ledgers/risk-register.md) and the
measurements [B-132](../ledgers/benchmark-ledger.md) and B-133.

No external code is copied. Source lines are cited by file, function and line number against the
KiCad source mirror as read on the research date; the constructs are otherwise described in prose.
No content from the surveyed board tree is reproduced — the test fixtures are authored from the
format definition.

## Sources

Read at <https://github.com/KiCad/kicad-source-mirror>, at these exact refs:

| Branch | HEAD | `SEXPR_BOARD_FILE_VERSION` |
|---|---|---|
| `9.0` | `ce707d0a6c8736cbf4689560be4fee3d6a7f7f5e` | `20241229` |
| `10.0` | `7f6b3a933459128142c1eaceb38496eff2730db5` | `20260206` |
| `master` (11.0-dev) | `8974c36ac22dcb0651aad2432b6dfadf1a672c22` | `20260826` |

Line numbers below are against `master` unless a branch is named; every load-bearing path was
cross-read against `9.0`, and no difference changes a conclusion. Format documentation:
<https://dev-docs.kicad.org/en/file-formats/sexpr-intro/> (the board page,
<https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/>, delegates the footprint grammar onward).

**The published grammar is stale relative to 9/10 and this note says so rather than working around
it.** The `sexpr-intro` footprint list is roughly KiCad-6-era: it shows `(tedit …)` as mandatory,
names the ratio token `solder_paste_ratio` rather than `solder_paste_margin_ratio`, shows `(id …)`
inside a group rather than `(uuid …)`, and contains **no** definition of `sheetname` or `sheetfile`
at all. Where the documentation is silent or wrong, the parser is treated as authoritative and the
gap is marked.

## Finding 1 — the direct-child grammar, and how much of it the docs omit

`PCB_IO_KICAD_SEXPR_PARSER::parseFOOTPRINT_unchecked` is the real switch (`parseFOOTPRINT` is a
thin version-check wrapper). It has **47 top-level `case T_…` arms on `9.0` and 54 on `10.0`**
(`9.0:4417-5014`, `10.0:4987-5712`, `master:5446-6408`). `master` adds three more that are 11.0
development only — `T_constraint`, `T_transform`, and the `T_fp_ellipse`/`T_fp_ellipse_arc` pair —
and those are deliberately excluded from CopperMCP's declared vocabulary, so a board written by
KiCad 11 reports as unknown rather than being silently covered.

Two writer facts matter to a board reader, both from `pcb_io_kicad_sexpr.h:218-223`:

- `CTL_FOR_BOARD` includes `CTL_OMIT_FOOTPRINT_VERSION`, so `(version)`, `(generator)` and
  `(generator_version)` **never appear inside a footprint in a `.kicad_pcb`** — only in a
  `.kicad_mod`. They are still accepted by the parser, so a hand-edited or third-party-written
  board can carry one.
- `CTL_FOR_LIBRARY` omits `uuid`, `path` and `at`, which is why a library footprint lacks them.

Five arms are **read-only legacy**: `T_autoplace_cost90`, `T_autoplace_cost180` (parsed and
discarded), `T_tedit` (removed at `20220225`; parsed as hex and discarded), `T_thermal_gap` and
`T_thermal_width` (KiCad's own comment: never exposed in the GUI), and `T_solder_paste_ratio`
(marked `// legacy token`).

## Finding 2 — the pad → footprint → board fallback chain

Everything below reduces to this, so it is stated once. A `PAD` resolves mask margin, paste margin,
paste ratio, clearance and zone connection by consulting its own padstack, then its **parent
footprint**, then board design settings:

- `PAD::GetSolderMaskExpansion` (`pad.cpp:1970`) — padstack `:2001`, **footprint `:2005-2006`**,
  board `:2011-2012`
- `PAD::GetSolderPasteMargin` (`pad.cpp:2033`) — padstack `:2086`, **footprint `:2090-2091`**,
  board `:2096-2097`; ratio at `:2103`, **`:2107-2108`**, `:2113-2114`
- `PAD::GetClearanceOverrides` (`pad.cpp:1938`) — the pad's own value at `:1940-1941`, otherwise
  **`parentFootprint->GetClearanceOverrides()`** at `:1943-1944`
- `PAD::GetZoneConnectionOverrides` (`pad.cpp:2139`) — padstack `:2141`, **footprint `:2149-2151`**

**In every case the pad wins and the footprint value governs exactly those pads that omit their
own.** Identical on `9.0` (`pad.cpp:1185-1194`, `:1237`, `:1284`, `:1296`). A footprint-level field
is therefore the pad-level field CopperMCP already reasons about, multiplied across the footprint.

## Finding 3 — the mask and paste margins move no copper, and the proof is positive

`FOOTPRINT::TransformPadsToPolySet` (`footprint.cpp:5018`) is the routine that turns pads into
polygons, and it switches on the target layer at `:5027-5045`. Mask expansion is added **only**
under `case F_Mask: case B_Mask:` (`:5030-5031`), paste margin **only** under
`case F_Paste: case B_Paste:` (`:5035-5036`), and **every copper layer falls to `default: break;`**
(`:5043-5044`) with no adjustment. Independently, `PAD::GetEffectiveShape`
(`pad.cpp:1236-1310`) never mentions either margin.

This is an argument from a positive citation, not from an absence sweep, and it is the same
argument [D-227](../ledgers/decision-ledger.md) already accepted for the board-level
`pad_to_paste_clearance` pair one level up.

Where the three margins *do* go: solder-mask DRC (`drc_test_provider_solder_mask.cpp:208-214`,
`:1034-1051`), plotting (`plot_board_layers.cpp:423-426`), rendering (`pcb_painter.cpp:1798-1803`),
`PAD::ViewBBox` (`pad.cpp:2854-2855`), the pad checker (`pad.cpp:3408-3450`) and the
IPC-2581/ODB++/STEP exporters. A directed sweep finds **none of them** in `zone_filler.cpp`,
`pcbnew/connectivity/`, `pcbnew/router/`, or `FOOTPRINT::BuildCourtyardCaches`.

Two file-format subtleties a reader must not get wrong:

1. **Pre-9.0 files treat a literal `0` as "inherit".** The parser normalises it to "unset" at
   `pcb_io_kicad_sexpr_parser.cpp:5893-5895`, `:5903-5905`, `:5914-5916`, gated on
   `m_requiredVersion <= 20240201` ("Use nullable properties for overrides"). A reader that treats
   `(solder_mask_margin 0)` as a hard zero disagrees with KiCad on every pre-9 board.
2. **A negative mask margin is floored so the opening cannot invert**: `pad.cpp:2021-2027` clamps it
   to `-min(padW,padH)/2`.

**Direction of error for CopperMCP: none, at any sign.** They contribute to mask and paste layers
only; copper geometry and every copper clearance constraint are computed without them.

## Finding 4 — `clearance` is a replacement, not a maximum

`FOOTPRINT::GetLocalClearance` (`footprint.h:496`, `:554`) and `GetClearanceOverrides`
(`footprint.h:568`) are read from:

- **`drc_engine.cpp:1146`/`:1149`/`:1162`/`:1173`** — the "local overrides take precedence over
  everything except board min clearance" block, which opens at `:1138` and returns at `:1203-1205`
- **`board.cpp:1142`** — `BOARD::GetMaxClearanceValue`, the worst-case search radius
- **`pns_kicad_iface.cpp:2361`** — `syncWorld`'s `worstClearance`, plus the router's live queries at
  `:631`, `:720`, `:749`

and it reaches copper geometry through `ZONE_FILLER::buildCopperItemClearances`, whose
`knockoutPadClearance` maxes `PHYSICAL_CLEARANCE_CONSTRAINT` with `CLEARANCE_CONSTRAINT`
(`zone_filler.cpp:2195-2206`) and knocks that gap out of the pour (`:2208-2209`). **The footprint
clearance therefore changes the size of the void the copper pour leaves around every pad of that
footprint.**

**The critical ordering fact.** The override block at `:1138` runs *before* the custom-rule
iteration at `:1922`, and it *returns*. So a footprint `clearance` beats netclass, the board
default **and** custom DRC rules — winner takes all, not a maximum. The only thing it cannot beat is
`m_MinClearance`, which floors it at `:1180-1188`.

Direction of error, by sign, because it is **not uniform**:

| Value | Honoured result | Ignoring it is |
|---|---|---|
| positive | `max(value, board_min)`, *replacing* the rule result | **UNSAFE** — can model less required clearance than reality |
| exactly `0` | falls through: `if( override_val )` at `:1176` is false | **safe, and exact** — reproduces KiCad |
| negative | truthy, then clamped *up* to `m_MinClearance` at `:1180-1182` | safe (over-approximation) |

So "ignoring a clearance field is conservative" is **false** here, and a present non-zero value must
be honoured or refused. The zero case is a genuine, provable inert value — which is what makes a
narrowed acceptance sound in principle, and B-132 is what made it pointless in practice.

**One quirk verified but not fully characterised**, recorded rather than smoothed over: the block is
argument-order asymmetric for negatives. `override_val` starts at 0; A's override is assigned
unconditionally (`:1162`) while B's is taken only `if( overrideB > override_val )` (`:1172`), so a
negative override on the B side falls through where the same value on the A side clamps to the board
minimum. Both outcomes are ≤ the ignore-path result, so the safety verdict holds under either
ordering; how a given DRC test provider assigns `a` and `b` was not traced.

## Finding 5 — `zone_connect`, and the one place the pad reasoning does not transfer

Value domain (`zones.h:46-53`), and what each does to the pour around the footprint's pads:

| Token | `ZONE_CONNECTION` | Effect |
|---|---|---|
| *(absent)* | `INHERITED = -1` | defer to the zone's `connect_pads` |
| `0` | `NONE` | **detaches** — pad knocked out with clearance |
| `1` | `THERMAL` | attaches — gap annulus knocked out, spokes added |
| `2` | `FULL` | attaches — no knockout, the pour laps the pad |
| `3` | `THT_THERMAL` | attaches — `DRC_ENGINE::EvalZoneConnection` (`:972`, collapse at `:982-1000`) resolves it to `1` on a plated through-hole pad and `2` otherwise |

Written only when it differs from `INHERITED` (`pcb_io_kicad_sexpr.cpp:1498`), and **not
range-checked on read**: `SetLocalZoneConnection( (ZONE_CONNECTION) parseInt(…) )` at
`pcb_io_kicad_sexpr_parser.cpp:5931` casts any integer straight through, exactly as the pad form
does at `:6915`. A consuming tool must check the domain itself.

The geometric consumer is `ZONE_FILLER::knockoutThermalReliefs` (`:1835`), which calls
`EvalZoneConnection` at `:1973` and then branches at `:1981+`. **The finished fill is intersected
with the zone's own extents at `zone_filler.cpp:3147`** — re-verified on this commit, since
[the pad note](kicad-pad-zone-connect-v1.md)'s Finding 4 rests on it — so poured copper stays a
subset of the zone boundary for every value, and a boundary-polygon obstacle model
over-approximates unconditionally.

**Direction of error: the obstacle direction cannot break; the connectivity direction breaks only
at `0`.** `1`/`2`/`3` all attach, so discarding one mis-states the *mode* without ever inventing an
attachment. Discarding `0` leaves a model believing every non-overriding pad of that footprint
attaches to a pour that in fact voids around all of them.

### The one thing that does **not** transfer from the pad

[The pad note](kicad-pad-zone-connect-v1.md) establishes that the pad override sits at
`drc_engine.cpp:1211`, **before** custom-rule iteration, so "a custom DRC rule cannot detach a pad
that carries `1`, `2` or `3`". **The footprint-level override sits at `:2089`, *after* rule
iteration at `:1922`/`:1972`.** A custom rule therefore *can* override a footprint-level
`zone_connect`, including detaching pads the footprint declared `2`.

This does not make accepting the attaching modes unsound — a rule can detach any pad whether or not
the footprint writes the token, so the exposure is the existing unread-`.kicad_dru` one — but the
pad note's load-bearing sentence is pad-specific and **must not be restated for footprints**.

Note also that footprint `clearance` and footprint `zone_connect` sit on **opposite sides** of the
rule iteration (`:1146` before, `:2089` after), so no single sentence beginning "footprint local
overrides are…" is true of both.

## Finding 6 — `sheetfile` and `sheetname` differ, and only one of them is inert

Both are first-class tokens from the KiCad 8 dev cycle (commit `636db607c1`, 2023-06-19, practical
cutover version `20230620`); before that they were `(property "Sheetfile" …)` /
`(property "Sheetname" …)`, and the parser still upgrades those spellings at `master:5669-5682`.
Payload for both: exactly one symbol-or-quoted-string, then `NeedRIGHT()` — no trailing token.
Parsed at `:5742-5752`, written at `pcb_io_kicad_sexpr.cpp:1448-1452` when non-empty.

**`sheetfile` — inert.** `FOOTPRINT::GetSheetfile` (`footprint.h:490`) has **six** call sites, none
geometric: the writer, `board_exchange_footprint.cpp:667`, `board_netlist_updater.cpp:751`, and
`multichannel_tool.cpp:572`. It appears in **no** `pcbexpr_functions.cpp` predicate, so there is not
even a custom-rule route.

**`sheetname` — not inert, but its terminus is outside this adapter.**
`FOOTPRINT::GetSheetname` (`footprint.h:487`) has twelve call sites; two matter:

- **`pcbexpr_functions.cpp:1083`**, inside `memberOfSheetFunc`, registered as the DRC-rule predicate
  `memberOfSheet('x')` at `:1733` (and `memberOfSheetOrChildren('x')` at `:1138`/`:1734`)
- **`component_class_manager.cpp:229-245`**, which auto-generates one component class per sheet
  name, queryable from a rule via `hasComponentClass('x')` (`pcbexpr_functions.cpp:1653`,
  registered `:1747`)

Concretely: a rule `(rule x (condition "A.memberOfSheet('/Power')") (constraint clearance (min 0.5mm)))`
is evaluated inside `EvalRules`' rule iteration and produces a `CLEARANCE_CONSTRAINT` that reaches
the copper-clearance provider, `ZONE_FILLER` and the router. **The sheet name is a selector for a
clearance value, never a value itself**, and it reaches no `GetEffectiveShape`, connectivity path or
courtyard cache.

**Direction of error: conditional.** If `.kicad_dru` is in scope, discarding `sheetname` destroys
the selector such a rule needs. CopperMCP does not read `.kicad_dru` at all, so this extends the
already-recorded exposure of that gap rather than opening a new one — the same structure as
[the root-board-properties note](kicad-root-board-properties-v1.md) §6.5.

A dead end worth naming so nobody re-walks it: a `ZONE` carries a placement area whose source may be
a sheet name (`zone_settings.h:109-115`, written at `pcb_io_kicad_sexpr.cpp:3311`). It is consumed
by `multichannel_tool.cpp` to *move* footprints; it is not a keepout and does not alter a rule
area's outline.

## Finding 7 — the footprint-local group is the same construct as a root group

**Confirmed in both directions, and true since KiCad 6** (`20201002 // Add groups in footprints`).
The footprint switch calls `parseGROUP( footprint.get() )` (`9.0:4915`, `10.0:5603`, `master:6284`)
— the *same* `parseGROUP` as the board root's — and resolution branches on the stored parent,
adding the group to the footprint at `9.0:1337-1339` / `10.0:1608-1610`. The writer builds
`sorted_groups` from `aFootprint->Groups()` (`9.0:1294-1296`, `10.0:1461-1463`) and formats it with
the same `format( const PCB_GROUP* )`. There is no separate footprint-group grammar.

Children: an optional quoted name and/or a bare `locked` before the first `(`, then `(uuid …)` (or
the pre-KiCad-8 alias `(id …)`), `(locked yes|no)`, `(members "UUID" …)`, and on 10.0 only
`(lib_id …)`. The writer emits name + uuid + optional locked + sorted members, and **skips the whole
`(group …)` when the member list is empty**.

**Geometry: none, and the exclusion is explicit rather than incidental.**
`DRC_TEST_PROVIDER::Init` builds `s_allBasicItems` while skipping `PCB_FOOTPRINT_T` and
**`PCB_GROUP_T`** (`drc_test_provider.cpp:60-70`), so no DRC provider ever iterates a group as an
item. A directed sweep finds zero `PCB_GROUP` references in `pcbnew/connectivity/`,
`zone_filler.cpp` or `pcbnew/router/`. `PCB_GROUP::GetEffectiveShape` (`pcb_group.cpp:345-354`)
exists but merely unions its members' own shapes and nothing in the copper path calls it.
`BuildCourtyardCaches` (`footprint.cpp:4071-4104`) reads only `PCB_SHAPE` items on the courtyard
layers. The one indirect route is `memberOfGroup('x')` (`pcbexpr_functions.cpp:1004`, registered
`:1731`), the same `.kicad_dru` class as `sheetname`.

### Lock, and the counter-evidence that was deliberately not relied on

`PCB_GROUP::SetLocked` (`pcb_group.cpp:184-194`) propagates the lock to every direct member via
`RunOnChildren`. That looks like a load-time hazard, and it is **not** one:
`PCB_IO_KICAD_SEXPR_PARSER::resolveGroups` runs two passes, calling `SetLocked( true )` at
`:1663-1664` in the first, and attaching members with `AddItem` at `:1697` in the second — so
`SetLocked` executes on an empty group and propagates to nothing.

**CopperMCP's refusal of a locked group does not rest on that**, and deliberately so. It rests on
`BOARD_ITEM::IsLocked()`, which consults `GetParentGroup()` **at query time**, so a locked group
makes every member locked in KiCad's model whatever the load-time pass ordering did. The pass
ordering above is a consequence of one function's structure rather than a documented guarantee; a
refactor that attached members before setting the lock would change it silently. It is recorded here
so a future reader does not mistake it for a licence to read a locked group past.

A second subtlety: the parser accepts `locked` in **two** syntactic positions — a bare token before
the first sub-expression (`:7715-7716`) and a `(locked <bool>)` sub-expression (`:7774-7776`) —
while the writer emits only the latter. A reader written against the writer alone rejects files
KiCad accepts.

## Summary table

| Field | Reaches copper / clearance / connectivity? | Ignoring it can under-approximate? | CopperMCP |
|---|---|---|---|
| `sheetfile` | no, by complete sweep | no | accept, count |
| `sheetname` | only via `.kicad_dru` selector | only if `.kicad_dru` is in scope — it is not read at all | accept, count (R-179) |
| `solder_mask_margin` | no (mask layers only) | no, at any sign | accept, count |
| `solder_paste_margin` | no (paste layers only) | no, at any sign | accept, count |
| `solder_paste_margin_ratio` | no | no | accept, count |
| `solder_paste_ratio` (legacy) | no | no | accept, count |
| `zone_connect` `1`/`2`/`3` | yes, but all attach | no | accept, validated |
| `zone_connect` `0` | **yes — detaches** | **yes** | refuse |
| `clearance` non-zero | **yes — DRC, filler, router** | **yes** | refuse |
| `clearance` zero | no (falls through the override guard) | no | refuse anyway — B-132 measured 0 occurrences, so a narrowed rule buys nothing |
| `group` (footprint-local) | no, and DRC excludes it explicitly | no | accept when unlocked, count |
| a locked `group` | lock propagates at query time | authorises a move KiCad forbids | refuse |
| the other 20 declared heads | not established | not established | refuse, **by name** |

## What this note does not establish

- **No released KiCad binary was exercised.** This is a source read, cross-branch, with no board
  measured and no DRC run. B-132 and B-133 are the measurements; this note is the semantics.
- **The published grammar could not be used for `sheetname`/`sheetfile`** — it does not define them.
  Their semantics here come from the parser, the writer and the call-site sweep.
- **The `zone_connect` documentation sentence beyond its first clause** is carried from
  [the pad note](kicad-pad-zone-connect-v1.md) and was not re-fetched.
- **The `PCB_GROUP` sweep is an enumeration, not a completeness proof.** It was built from
  `PCB_GROUP` / `PCB_GROUP_T` / `GetParentGroup` over `pcbnew/drc/`, `pcbnew/router/`,
  `pcbnew/connectivity/` and `zone_filler.cpp`; a consumer reached by a path none of those names
  appears on would not be in it.
- **DRC argument ordering** for the negative-clearance asymmetry (Finding 4) was not traced.
- **Whether a `.kicad_mod` save strips `sheetname`/`sheetfile`** was not determined; nothing in the
  writer guards them with a CTL flag, but `FootprintSave` was not traced far enough to prove they
  are cleared.
