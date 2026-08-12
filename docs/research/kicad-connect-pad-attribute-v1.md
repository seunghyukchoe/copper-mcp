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

All citations are against commit `42cc8ba` of the KiCad source mirror
(<https://github.com/KiCad/kicad-source-mirror>), fetched 2026-08-12, together with the
S-expression board format documentation at <https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/>.

The method is exhaustive rather than illustrative: every occurrence of `PAD_ATTRIB::CONN` in the
tree was enumerated and read. Import plug-ins for foreign formats (Altium, Eagle, CADSTAR,
EasyEDA, Fabmaster, P-CAD, gEDA, legacy, ODB++, IPC-2581, SolidWorks) are excluded — they only
decide which KiCad attribute a *foreign* pad becomes, and say nothing about what the attribute
means once it exists. What remains is **35 occurrences across 17 files**, and all of them are
accounted for below.

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

## Finding 2 — every place KiCad distinguishes `CONN` from `SMD`

This is the finding the modelling turns on. The 35 non-importer occurrences fall into three
groups: sites that put `CONN` and `SMD` in one shared case body or one shared boolean; sites that
name `CONN` separately but produce no geometry (serialization, the IPC API enum mapping, a
statistics row, two UI filters, six display strings); and exactly **three** sites where a `CONN`
pad is genuinely treated differently. The shared-body sites, by subsystem:

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

The no-geometry sites are the file writer and parser (Finding 1), the IPC API enum mapping to
`PT_EDGE_CONNECTOR` (`api/api_pcb_enums.cpp:67`, `:85`), a board-statistics row
(`board_statistics_report.cpp:114`), the pad-type display strings (`pad.cpp:2540`, `pad.cpp:3671`,
`dialogs/dialog_fp_edit_pad_table.cpp:214`), the pad-properties and pad-table dialogs that let a
user *set* the attribute (`dialog_pad_properties.cpp:93`, `:775`;
`dialog_fp_edit_pad_table.cpp:438`, `:645`), and the "push pad settings" type filter
(`tools/pad_tool.cpp:222-227`), which special-cases `CONN` only to keep aperture and non-aperture
pads apart. None of them changes what copper a pad is.

**There is no site anywhere that gives a `CONN` pad different copper, a different shape, a
different size, a different position, a different layer span, a different hole, or a different
electrical connection from an `SMD` pad.** The three genuine differences are:

1. **Solder paste.** `pad.cpp:3252-3257`: a `CONN` pad carrying `F.Paste` or `B.Paste` raises
   `DRCE_PADSTACK` advising an SMD pad instead — and then falls through into the `SMD` case, which
   is where the no-hole and no-inner-layer checks live. So the paste rule is the *only* thing the
   `CONN` case adds before the two become one.
2. **Gerber aperture attribute.** `plot_brditems_plotter.cpp:206-227`: on an outer copper layer a
   `CONN` pad plots with `GBR_APERTURE_ATTRIB_CONNECTORPAD` where an `SMD` pad plots with
   `GBR_APERTURE_ATTRIB_SMDPAD_CUDEF`. Fabrication metadata; identical geometry.
3. **Edge.Cuts clearance DRC exemption.** `drc/drc_test_provider_edge_clearance.cpp:431-439`: a
   `CONN` pad is skipped by the board-edge clearance test entirely, grouped with
   `PAD_PROP::CASTELLATED`. This is the one difference that is not about fabrication output: an
   edge-connector finger is *meant* to run to the board edge, and KiCad declines to flag it.

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
under-approximate. Taking the three differences in turn, against a model that converts a `connect`
pad exactly as an `smd` pad:

- **Paste.** Board IR models copper geometry and carries no paste layer, no mask layer, and no
  fabrication output. Nothing over- or under-approximates, because nothing is claimed.
- **Gerber aperture attribute.** Same: CopperMCP emits no Gerber.
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
- It measures nothing. The corpus effect of the decision it supports is recorded in the benchmark
  ledger, not here.
