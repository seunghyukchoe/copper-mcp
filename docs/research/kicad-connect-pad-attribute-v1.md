# KiCad's `connect` pad attribute: what `PAD_ATTRIB::CONN` provably is

Research date: 2026-08-12. This note establishes, from KiCad's own source, exactly what the pad
header token `connect` means and — the question that decides the modelling — **which of KiCad's
own subsystems treat it differently from `smd`**. It supports
[issue #138](https://github.com/seunghyukchoe/copper-mcp/issues/138),
[ADR-0096](../adr/0096-edge-connector-pads-convert-as-smd.md), decision
[D-186](../ledgers/decision-ledger.md) and risk [R-141](../ledgers/risk-register.md).

No external code is copied. Source lines are cited by file, function and line number against the
KiCad source mirror as read on the research date; the constructs are otherwise described in prose.
No content from the surveyed board tree is reproduced — the test fixtures are authored from the
format definition.

## Sources

All citations are against the KiCad source mirror
(<https://github.com/KiCad/kicad-source-mirror>), together with the S-expression board format
documentation at <https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/>. First read at commit
`42cc8ba` and re-verified line-for-line at commit `72eb6aa` on the same day, when the second
sweep below was added; every cited line number is identical at both, and the occurrence count is
unchanged.

### Method, and the blind spot the first version of this note had

The first sweep enumerated every occurrence of `PAD_ATTRIB::CONN` and read all of them. Import
plug-ins for foreign formats (Altium, Eagle, CADSTAR, EasyEDA, Fabmaster, P-CAD, gEDA, legacy,
ODB++, IPC-2581, SolidWorks, DipTrace, Autotrax, Sprint-Layout, PADS, Allegro) are excluded — they
only decide which KiCad attribute a *foreign* pad becomes, and say nothing about what the
attribute means once it exists. What remains is **35 occurrences across 17 files**.

**That sweep is structurally incomplete, and adversarial review of PR #149 proved it.** A site
that tests `== PAD_ATTRIB::SMD` *alone* contains no `CONN` literal, so it is invisible to a
`CONN` grep — and a `CONN` pad silently takes the other branch. `FOOTPRINT::HasThroughHolePads`
(Finding 2, difference 4) is exactly that shape, and the first version of this note missed it and
then claimed exhaustiveness anyway. **The lesson is the method, not the site: a grep for the
value you are reasoning about cannot see the branches that test its siblings.**

The sweep is therefore run twice, and the second half is what completes it for direct attribute
reads:

- `CONN` and `SMD` can only diverge at a site that either names `CONN` explicitly, or tests `SMD`
  alone. A test on `PTH` or `NPTH` puts `SMD` and `CONN` in the *same* branch, so it cannot
  separate them — that was checked rather than assumed, by sweeping those two literals as well
  and finding no site where the two part company.
- So the union of the `CONN` sweep and the `SMD` sweep is complete **for direct comparisons of
  the pad attribute inside `pcbnew/`**.

What that union still does not cover, stated so no later reader mistakes it for exhaustiveness:
behaviour reached through the property system by *user-authored* DRC rule expressions (which can
name `Edge connector` — Finding 2, difference 5), external plugins reading `PT_EDGE_CONNECTOR`
over the IPC API, and anything outside the swept directories. The list below is therefore a
**lower bound** of at least ten divergences, not a complete enumeration.

## Finding 1 — the token, and the closed vocabulary it belongs to

`PAD_ATTRIB` is declared in `pcbnew/padstack.h:96-105` with exactly four members, and the
declaration's own comments define `CONN` as: like SMD, absent from the solder-paste layer, carrying
a distinct attribute in Gerber X files, and intended for edge-card connectors.

The file token and the enum are in bijection, and the vocabulary is closed at four:

| Header token | `PAD_ATTRIB` | Written by | Read by |
|---|---|---|---|
| `thru_hole` | `PTH` | `pcb_io_kicad_sexpr.cpp:1877` | `…_sexpr_parser.cpp:6413` |
| `smd` | `SMD` | `pcb_io_kicad_sexpr.cpp:1878` | `…_sexpr_parser.cpp:6422` |
| `connect` | `CONN` | `pcb_io_kicad_sexpr.cpp:1879` | `…_sexpr_parser.cpp:6432` |
| `np_thru_hole` | `NPTH` | `pcb_io_kicad_sexpr.cpp:1880` | `…_sexpr_parser.cpp:6438` |

`parsePAD` switches on those four tokens and calls `Expecting( "thru_hole, smd, connect, or
np_thru_hole" )` on anything else (`…_sexpr_parser.cpp:6442-6443`); the writer throws on any
attribute outside the four (`pcb_io_kicad_sexpr.cpp:1882-1883`). There is no fifth token, no
alias, and no legacy spelling still accepted by the current parser. **A tool that models all four
has modelled the whole attribute.**

The parser also forces the drill to zero on `connect`, in the same case body and for the same
stated reason it does on `smd` (`…_sexpr_parser.cpp:6433-6437`), and the drill-subexpression
handler treats the two identically when deciding whether to keep a parsed drill size at all
(`…_sexpr_parser.cpp:6589-6592`). **A `connect` pad has no hole, established at the parser and not
merely by convention.**

## Finding 2 — where `CONN` and `SMD` share a branch, and where they part

This is the finding the modelling turns on, and it has two halves. The first is a *universal*
claim and is the load-bearing one; the second is a *lower bound* and is not.

**The universal half.** Every subsystem that decides what copper a pad is, where it sits, what
it spans, whether it has a hole, or what it connects to puts `CONN` and `SMD` in one shared case
body. These are the sites, by subsystem:

| Subsystem | Site | What the shared branch does |
|---|---|---|
| Connectivity | `connectivity/connectivity_items.cpp:164-176` | `SMD`, `CONN` and `NPTH` share one case that pins the connectivity item to the front of the pad's copper stack — a single-layer connection point |
| Router (P&S) | `router/pns_kicad_iface.cpp:1631-1648` | `CONN` and `SMD` share one case producing a solid on the pad's single copper layer |
| Layer set | `pad.cpp:1626-1641` | `SetAttribute` trims both to at most one copper layer |
| Hole | `pad.cpp:2886-2891` | `ImportSettingsFrom` zeroes the drill for both |
| Hole (dialog) | `dialogs/dialog_pad_properties.cpp:2143-2152` | one case for both, zeroing the drill; the comment states outright that they are the same type of pad, differing only in the default non-technical layer set |
| Principal layer | `pad.cpp:632-637` | both resolve their principal layer to `m_layer` rather than to the front of a stack |
| Disabled-layer DRC | `drc/drc_test_provider_misc.cpp:304-311` | both are checked as single-layer items rather than as items piercing every layer |
| Length/delay | `length_delay_calculation.cpp:720-726` | both take their span layer from the front of the copper stack |
| Visibility | `collectors.cpp:178-186` | both are treated as *not* through-hole for the parent-footprint visibility rule |
| Netlist/test export | `exporters/export_d356.cpp:128-130` | both set the IPC-D-356A `smd` flag |
| Teardrops | `dialogs/dialog_global_edit_teardrops.cpp:403-405` | the "SMD pads" checkbox selects `SMD` and `CONN` together |
| UI text | `pad.cpp:2185-2192`, `pad.cpp:2555-2605` | both describe themselves by layer mask rather than as "PTH pad" |

Serialization, the IPC API enum mapping to `PT_EDGE_CONNECTOR` (`api/api_pcb_enums.cpp:67`,
`:85`), the pad-type display strings (`pad.cpp:2540`, `dialogs/dialog_fp_edit_pad_table.cpp:214`),
the dialogs that let a user *set* the attribute (`dialog_pad_properties.cpp:93`, `:775`;
`dialog_fp_edit_pad_table.cpp:438`, `:645`), and the "push pad settings" type filter
(`tools/pad_tool.cpp:222-227`) all name `CONN` separately without changing what copper a pad is.

**Across both sweeps, no site anywhere gives a `CONN` pad different copper, a different shape, a
different size, a different position, a different layer span, a different hole, or a different
electrical connection from an `SMD` pad.** That is the claim the modelling rests on, and the
second sweep is what lets it be stated: a divergence must name `CONN` or test `SMD` alone, and
both literals have now been read.

**The lower-bound half.** At least **ten** sites do treat a `CONN` pad differently. None is
copper geometry or connectivity, and they sort into four classes:

*Fabrication and export output — three:*

1. **Solder paste.** `pad.cpp:3252-3257`: a `CONN` pad carrying `F.Paste` or `B.Paste` raises
   `DRCE_PADSTACK` advising an SMD pad instead — and then falls through into the `SMD` case, which
   is where the no-hole and no-inner-layer checks live. So the paste rule is the *only* thing the
   `CONN` case adds before the two become one.
2. **Gerber aperture attribute.** `plot_brditems_plotter.cpp:206-227`: on an outer copper layer a
   `CONN` pad plots with `GBR_APERTURE_ATTRIB_CONNECTORPAD` where an `SMD` pad plots with
   `GBR_APERTURE_ATTRIB_SMDPAD_CUDEF`. Fabrication metadata; identical geometry.
3. **Pick-and-place exclusion.** `FOOTPRINT::HasThroughHolePads` (`footprint.cpp:4451-4460`)
   returns true when *any* pad is `!= PAD_ATTRIB::SMD`, so a `CONN` pad makes its whole footprint
   count as through-hole. Its sole caller, `exporters/place_file_exporter.cpp:145`, drops that
   footprint from the position file under "exclude all TH". **This is the site the first sweep
   missed** — it contains no `CONN` literal.

*Rule and DRC surface — two:*

4. **Edge.Cuts clearance DRC exemption.** `drc/drc_test_provider_edge_clearance.cpp:431-439`: a
   `CONN` pad is skipped by the board-edge clearance test entirely, grouped with
   `PAD_PROP::CASTELLATED`. An edge-connector finger is *meant* to run to the board edge, and
   KiCad declines to flag it.
5. **A distinct value in the property system.** `PAD_DESC` (`pad.cpp:3665-3671`) maps `CONN` to
   `Edge connector` where `SMD` maps to `SMD`, so a *user-authored* KiCad DRC rule expression can
   select on it. This is the one divergence reachable by something a board author writes rather
   than something KiCad decides.

*Reporting and UI only — four:*

6. Footprint pad tallies: `footprint.cpp:1687-1700` increments `smd_count` only for `SMD` and
   `tht_count` only for `PTH`, so a `CONN` pad is counted as neither.
7. Board statistics: `board_statistics_report.cpp:112-114` gives `CONN` its own "Connector:" row.
8. The clearance inspector's layer pick: `tools/board_inspection_tool.cpp:818-833` special-cases
   `SMD` alone, so a `CONN` pad falls to the generic branch.
9. The footprint editor's pad-area readout: `pad.cpp:2195-2205`, shown for `SMD` only.

*Unreachable from CopperMCP — one:*

10. `pad.cpp:3229` raises a padstack error for a `PAD_PROP::BGA` pad that is not `SMD`. CopperMCP
    refuses any pad carrying a `PAD_PROP` at all, so this cannot be reached through it.

## Finding 3 — plating is not a pad attribute at all

The issue's hypothesis was that gold-plated fingers might carry a plating semantic. They do not,
in KiCad's model. `PAD` exposes `HasHole` and `HasDrilledHole` (`pad.h:113-121`) and nothing named
for plating; the only plated/unplated distinction KiCad draws is `PTH` versus `NPTH`, which is
about the *hole*, and a `CONN` pad has no hole. Surface finish is a board stackup property, not a
pad property, and is not part of the pad s-expression. There is therefore no plating distinction
for a Board IR `Pad` to lose.

The fabrication *property* enum `PAD_PROP` (`padstack.h:113-124`) is a separate, orthogonal field —
`NONE`, `BGA`, `FIDUCIAL_GLBL`, `FIDUCIAL_LOCAL`, `TESTPOINT`, `HEATSINK`, `CASTELLATED`,
`MECHANICAL`, `PRESSFIT` — and CopperMCP refuses a pad carrying one, unchanged by this note.

## Finding 3a — an aperture pad is defined by layers, not by attribute

`PAD::IsAperturePad()` (`pad.h:562-565`) is exactly "the pad's layer set intersects no copper
layer". It does not consult the attribute at all, and `pad_tool.cpp:222-227` special-cases a
`CONN` pad when matching apertures — so a copper-less `connect` pad is **representable** in
KiCad's model, not a contradiction.

CopperMCP nevertheless keeps refusing one. Two reasons, and the first is the honest one: before
this work *every* `connect` pad refused, so refusing the copper-less form is unchanged behaviour
rather than a new restriction, and no board in the surveyed corpus carries one. The second is that
the paste-bearing form is the combination KiCad's own padstack test reports as an error
(Finding 2, difference 1), so admitting it would mean reading past a construct KiCad flags.
Widening the aperture skip to `connect` later would be sound by the same "no copper at all"
argument the `smd` aperture rests on; it is deferred, not rejected on principle.

## Finding 4 — what this means for the direction-of-error rules

CopperMCP requires obstacles to over-approximate and connectivity and the board outline to
under-approximate. Taking every divergence class in turn, against a model that converts a
`connect` pad exactly as an `smd` pad:

- **Paste.** Board IR models copper geometry and carries no paste layer, no mask layer, and no
  fabrication output. Nothing over- or under-approximates, because nothing is claimed.
- **Gerber aperture attribute, pick-and-place exclusion, statistics rows, UI readouts.** Same:
  CopperMCP emits no Gerber, no position file, no board statistics and no pad dialog. It is worth
  saying why the pick-and-place case does not become an exception even though it changes real
  fabrication output: the output is produced by *KiCad*, from the `.kicad_pcb` file, and the
  `connect` token is still in that file — both CopperMCP patch adapters are source-preserving
  splices that rewrite only pose and route geometry. Nothing CopperMCP writes can turn a finger
  into an SMD pad, so KiCad's position file, Gerbers and DRC all still see `connect`.
- **The property-system value.** A user-authored DRC rule naming `Edge connector` is evaluated by
  KiCad against the file, for the same reason. CopperMCP evaluates no rule expressions.
- **Edge clearance exemption.** CopperMCP derives no edge clearance of its own. Authoritative DRC
  is delegated to KiCad (ADR-0004), which applies its own exemption, so the exemption is honoured
  by the only surface that consults it. In the *routing* direction the exemption cannot be
  exploited: the router works inside the board outline inset by half the track width, and a path
  endpoint outside that rectangle is rejected outright (`routing/astar.py`, `_inside_closed`
  against `safe_board`). A finger extending past the outline therefore fails to route — an
  over-refusal, which is the safe direction — rather than authorising copper outside the board.

The obstacle direction is unconditionally safe: a `connect` pad is real copper on one layer with a
shape, a size and a position, and the SMD envelope covers exactly that copper. The connectivity
direction is safe because KiCad's own connectivity engine builds the identical single-layer
connection item for both attributes (Finding 2, first row), so claiming attachment at a `connect`
pad claims exactly what KiCad claims — no more.

## What this note does not establish

- It says nothing about `PAD_PROP::CASTELLATED`, the *other* item the edge-clearance test exempts.
  Castellated pads remain refused, and nothing here argues they should not be.
- It says nothing about per-layer padstacks, backdrills, or any other pad construct outside the
  attribute token.
- It does not establish that a `connect` pad with a paste layer is meaningful. KiCad's own padstack
  test calls that combination an error, and CopperMCP refuses it rather than guessing.
- It measures nothing, and **no benchmark-ledger entry accompanies it.** The corpus effect of the
  decision it supports is stated in prose in decision-ledger row D-186, because the runner's
  output derives from a private corpus and is deliberately not committed. An earlier version of
  this sentence said the effect was "recorded in the benchmark ledger", which was never true; that
  is corrected here and in D-186.
