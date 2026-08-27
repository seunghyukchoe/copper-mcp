# KiCad `setup` fields: which of them can reach copper, and which cannot

Research date: 2026-08-28. This note supports
[issue #188](https://github.com/seunghyukchoe/copper-mcp/issues/188), decision
[D-227](../ledgers/decision-ledger.md), risk [R-178](../ledgers/risk-register.md), security review
[SEC-164](../ledgers/security-ledger.md) and
[ADR-0122](../adr/0122-the-stackup-is-read-per-field-because-three-of-its-fields-are-not-about-z.md).
It is the domain half of that decision; the measurement halves are
[B-130](../ledgers/benchmark-ledger.md) — the closed field census that named the surface — and
B-131 — the before/after differential.

Sources are KiCad's own user manuals for 9.0 and 10.0, KiCad's file-format developer
documentation, and KiCad's source at branches `9.0` and `10.0`. **Paths and line numbers are from
the `10.0` branch unless a claim is prefixed `[9.0]`**, and they are given so a claim can be
re-checked, not because they are stable.

No board content from any surveyed board is reproduced here. The census this note supports is
aggregate-only and commits no board byte, path, digest or atom value; the fixtures in
`tests/test_kicad_board_ir.py` are authored from the format definitions below.

## 0 — What is actually in a `setup` block, and do not trust the format docs for it

The authority is the writer, `PCB_IO_KICAD_SEXPR::formatSetup()` in
`pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:551-638` (`[9.0]`: `:529-597`). KiCad 10 emits
exactly: `stackup`, `pad_to_mask_clearance`, `solder_mask_min_width`, `pad_to_paste_clearance`,
`pad_to_paste_clearance_ratio`, `allow_soldermask_bridges_in_footprints`, `tenting`, `covering`,
`plugging`, `capping`, `filling`, `zone_defaults`, `aux_axis_origin`, `grid_origin`,
`pcbplotparams`. Nothing else. KiCad 9 emits the same set minus `covering`/`plugging`/`capping`/
`filling`/`zone_defaults`, with a different `tenting` syntax.

**`dev-docs.kicad.org/en/file-formats/sexpr-pcb/` claims to cover "all versions of KiCad from 6.0"
and is materially wrong about this block**, which is why nothing below rests on it alone:

1. It never mentions `allow_soldermask_bridges_in_footprints`, `tenting`, `covering`, `plugging`,
   `capping`, `filling` or `zone_defaults`.
2. It documents `castellated_pads` as current. KiCad 10 removed it — see §2.
3. It says `pad_to_paste_clearance_ratio` "is the percentage (from 0 to 100)" defaulting to 100 %.
   The source treats it as a *fraction*: `pad_margin.x = margin + KiROUND( padSize.x * mratio )`
   (`pcbnew/pad.cpp:1834`), and the default is `0.0`, not `1.0`
   (`DEFAULT_SOLDERPASTE_RATIO`, `include/board_design_settings.h:69`;
   `pcbnew/board_design_settings.cpp:228`).

## 1 — The design rules are not here, and have not been since KiCad 6

`BOARD_DESIGN_SETTINGS` is a `NESTED_SETTINGS` stored in the **project file** `.kicad_pro` under
`board.design_settings.` (`common/project/project_file.cpp:406`), with parameters registered as
`rules.min_clearance`, `rules.min_track_width`, `rules.min_via_diameter`,
`rules.min_copper_edge_clearance` and so on (`pcbnew/board_design_settings.cpp:268-333`). Net
classes — per-class clearance, track width, via size — live at project path `net_settings`
(`common/project/project_file.cpp:170`). Custom rules live in `.kicad_dru`
(`common/wildcards_and_files_ext.cpp:169`).

There was never a `(rules …)` or bare `(clearance …)` token inside `setup`. Pre-6.0 boards carried
*individual* rule tokens there — `trace_clearance`, `clearance_min`, `via_size`, `via_drill`,
`hole_to_hole_min` and about two dozen more — and KiCad 10 still **parses** them for backward
compatibility at `pcb_io_kicad_sexpr_parser.cpp:2544-2729`, each setting
`m_LegacyDesignSettingsLoaded` so the legacy values win over the project JSON and are migrated out
on the next save (`pcbnew/board.cpp:221`). **None of them is emitted by KiCad 7, 8, 9 or 10.**

This matters twice. It is why a `setup` block on a modern board carries no clearance CopperMCP
could be ignoring — the clearance question is answered by the caller-supplied constraint profile,
not by the document. And it is a standing residual for anything that *re-fills* a zone:
`pcbnew/zone_filler.cpp:460-476` builds a `DRC_ENGINE` and calls `InitEngine()` against the
`.kicad_dru` before filling, so zone fill geometry cannot be derived from the `.kicad_pcb` alone.
CopperMCP consumes KiCad's stored `(filled_polygon …)` and therefore inherits KiCad's last fill
rather than reproducing it — which is already this project's recorded position, and this note does
not widen it.

## 2 — `stackup`, and the three fields inside it that are not about Z

`(stackup …)` is the ordered top-to-bottom list of physical layers and substrate
(`pcbnew/board_stackup_manager/board_stackup.cpp:786-787`). It is written only when
`m_HasStackup` is set (`pcb_io_kicad_sexpr.cpp:559-560`); otherwise KiCad synthesises a default on
load (`parser:2899-2906`).

### The copper layer set is derived *from* the layers section, not from here

`parseLayers()` calls `SetCopperLayerCount()` and `SetEnabledLayers()`
(`parser:2391-2397`). The stackup parser then matches each `(layer "NAME")` against the
**already-enabled** layers (`parser:2029-2036`) and maps anything unmatched to a dielectric;
`BuildDefaultStackupList` reads `GetEnabledLayers()` and `GetCopperLayerCount()`
(`board_stackup.cpp:668-669`). The 10.0 manual presents the Physical Stackup page as where copper
layer count is set, but that is a UI affordance that edits the `(layers …)` section.

### The layer sub-tokens

| Token | Meaning | Constrains copper? | Where |
|---|---|---|---|
| `type` | free-form label string, **not** authoritative — the real `BOARD_STACKUP_ITEM_TYPE` is derived from the layer *name* | No | written from `GetTypeName()`, `board_stackup.cpp:806`; derivation at `parser:2041-2050` |
| `thickness` | physical layer thickness, optional trailing `locked` on dielectrics | **Only** through post-machining — §3 | `board_stackup.cpp:820-829`; `GetLayerDistance` `:869-925` |
| `material` | material name string | No | `:831-835` |
| `epsilon_r` | dielectric constant, written only when `material` is present | No — impedance and delay | `:837-838` |
| `loss_tangent` | dielectric loss tangent | No | `:840-844` |
| `color` | mask/silk/dielectric appearance | No — 3D viewer and 3D export only | written only `if( item->IsColorEditable() )`, `:814`; 10.0 manual, "Physical Stackup" |

`copper_finish` is a quoted string from a predefined list — ENIG, ENEPIG, HAL SnPb, HAL lead-free,
hard gold, immersion tin/nickel/silver/gold, HT_OSP, OSP, None, user defined
(`board_stackup_manager/stackup_predefined_prms.cpp:34-50`, whose comment says these are the
"standard names in .gbdjob files"). `dielectric_constraints` is a `yes`/`no` written through
`FormatBool` (`common/io/kicad/kicad_io_utils.cpp:35-38`).

### `edge_plating`, `edge_connector`, `castellated_pads` — and the counter-evidence

These are the three fields in the block that assert something about material at the board edge
rather than about the stack in Z.

**KiCad derives no geometry from any of them.** An exhaustive whole-tree grep for
`m_EdgePlating` and `m_EdgeConnectorConstraints` returns only the Gerber job-file writer
(`pcbnew/exporters/gerber_jobfile_writer.cpp:281,284-289`), the stackup text report
(`board_stackup_reporter.cpp:133,136`), the board-characteristics table drawn on the board
(`board_tables/board_characteristics_table.cpp:122,126`), the GUI panel
(`panel_board_finish.cpp:58,100-101`), the stackup class itself, and the parser
(`parser:1983,1995-2000`). **Zero hits in `pcbnew/drc/`, in any plotter, in `zone_filler.cpp`, in
`3d-viewer/`, or in the STEP exporter.** `[9.0]` is identical apart from
`tools/drawing_stackup_table_tool.cpp`. Both the 9.0 and 10.0 manuals say, in the Board Finish
section, that these settings "only impact the board attributes output as part of Gerber job files
at this time". The header comment at `board_stackup.h:324-331` agrees: the purpose is the job file.

Legal values: `edge_plating` is emitted as `yes` only when true (`board_stackup.cpp:862-863`) and
read as true only for `yes` (`parser:1981-1985`); `edge_connector` is `yes` or `bevelled`, emitted
only when non-`NONE` (`board_stackup.cpp:856-860`, `parser:1993-2003`).

**`castellated_pads` no longer exists in KiCad 10.** `[9.0]` declares `bool m_CastellatedPads`
(`board_stackup.h:333`) and writes `(castellated_pads yes)` (`board_stackup.cpp:820-821`). In 10.0
the member is gone; the parser consumes the token and discards it —
`case T_castellated_pads: // Legacy compatibility. just skip it` (`parser:2005`) — and
`FormatBoardStackup` never emits it (`board_stackup.cpp:850-864`). The removal is commit
`09e1fca7e459cae9fe748a6c866933c91e96756d`, 2025-08-11, *"More about support of press-fit pad
fabrication property"*, which added `BOARD::GetPadWithCastellatedAttrCount()`
(`pcbnew/board.cpp:3999-4008`). Castellation is now a **pad property**,
`(property pad_prop_castellated)`, and unlike the stackup flag it *does* change DRC:
`drc/drc_test_provider_edge_clearance.cpp:210-213,396-397` allows edge collisions inside the holes
of castellated pads. CopperMCP already refuses that pad property
([ADR-0099](../adr/0099-pad-fabrication-properties-and-named-pad-refusals.md)).

## 3 — The one path that makes a `thickness` load-bearing for copper

This is the finding that decides the shape of D-227, and it exists only in KiCad 10.

`PCB_VIA::GetPostMachiningKnockout( PCB_LAYER_ID )` (`pcbnew/pcb_track.cpp:858-945`) and
`PAD::GetPostMachiningKnockout` (`pcbnew/pad.cpp:666-745`) compare a counterbore or countersink
depth against `BOARD_STACKUP::GetLayerDistance( F_Cu/B_Cu, aLayer )` to decide whether the copper
on that layer is knocked out, and for a countersink compute the diameter *at that layer* from the
layer distance and the cone angle. That result flows into `GetEffectiveShape( aLayer )`
(`pcb_track.cpp:2758-2790`, `pad.cpp:985`) — and therefore into every DRC clearance test — and
into `zone_filler.cpp:1865,2020,2190,2313`, `connectivity/connectivity_algo.cpp:858,872` and
`drc/drc_test_provider_connectivity.cpp:154-159`. KiCad's own QA test for the coupling is
`qa/tests/pcbnew/drc/test_drc_backdrill_postmachining.cpp:64-66`, whose comment is "Set up a proper
stackup with known layer thicknesses".

The trigger is a pad or via carrying `(front_post_machining …)` or `(back_post_machining …)`
(writer: `pcb_io_kicad_sexpr.cpp:1758-1784` for pads, `:2678-2697` for vias). Plain `(backdrill …)`
and `(tertiary_drill …)` use explicit layer ranges (`pcb_track.cpp:826-848`) and are **not**
stackup-dependent. `grep -rn PostMachining` over the whole `9.0` tree returns nothing.

**Consequence.** Reading the stack past is sound *only while* post-machined pads and vias are
refused. Both are: `front_post_machining` and `back_post_machining` are in
`_UNSUPPORTED_PAD_FIELDS`, and the via head allowlist refuses them. This is a premise, not a
coincidence, so ADR-0122 makes it explicit and two tests pin it on both object kinds.

## 4 — `grid_origin` and `aux_axis_origin`

`m_gridOrigin` is `///< origin for grid offsets` (`include/board_design_settings.h:796`) and is
read by the editor and grid helper only (`common/eda_draw_frame.cpp:1082,1096`,
`common/tool/grid_helper.cpp:92,379`, `pcbnew/pcb_base_edit_frame.cpp:223`), plus one optional
user-selected STEP export origin (`exporters/step/exporter_step.cpp:1158`). The 10.0 manual: "The
grid origin is the point that the grid aligns to". It is emitted only when non-zero
(`pcb_io_kicad_sexpr.cpp:626-633`). Nothing on the board moves with it.

`m_auxOrigin` is `///< origin for plot exports` (`board_design_settings.h:795`) and every consumer
is an exporter: `exporters/place_file_exporter.cpp:105`, `gerber_placefile_writer.cpp:60`,
`export_d356.cpp:107,190`, `export_gencad.cpp:65`, `dialog_gendrill.cpp:349`, `pcbplot.cpp:351`,
`plot_board_layers.cpp:1226`, `pcb_plotter.cpp:85`, `pcbnew_jobs_handler.cpp:1485,1738`. The 10.0
manual: "The drill/place file origin is a configurable point that can be used for fabrication
outputs."

**The caveat that must be stated rather than assumed.** `aux_axis_origin` moves no board item —
board coordinates are absolute — but it is the offset KiCad applies when *writing* drill, place and
Gerber output. Board IR is in absolute board coordinates and claims nothing about fabrication
output frames, so ignoring it is correct **for that claim**; a consumer that generated fab output
from a snapshot alone would emit it in the wrong frame. That is exactly what
`unmodelled_setup_field_count` discloses.

## 5 — The paste and mask pairs

`pad_to_paste_clearance` and `pad_to_paste_clearance_ratio` back `m_SolderPasteMargin` and
`m_SolderPasteMarginRatio` — "Solder paste margin absolute value" and "ratio value of pad size"
(`include/board_design_settings.h:728-729`). Outside dialogs and QA, the only consumers are the
sexpr writer and parser, the Altium and legacy importers, `PAD::GetSolderPasteMargin()`
(`pcbnew/pad.cpp:1746-1849`), and the DRC engine's *default value* for
`SOLDER_PASTE_ABS_MARGIN_CONSTRAINT` and `SOLDER_PASTE_REL_MARGIN_CONSTRAINT`
(`drc/drc_engine.cpp:1970,1977`).

The single cleanest proof is `FOOTPRINT::TransformPadsToPolySet()`
(`pcbnew/footprint.cpp:4529-4547`): it adds `GetSolderMaskExpansion()` only under
`case F_Mask: case B_Mask:`, adds `GetSolderPasteMargin()` only under
`case F_Paste: case B_Paste:`, and `default:` — every copper layer — breaks with no adjustment at
all. `PAD::GetSolderPasteMargin` is itself paste-scoped:
`PCB_LAYER_ID cuLayer = ( aLayer == B_Paste ) ? B_Cu : F_Cu;` (`pad.cpp:1830`).

The already-accepted mask pair confirms the same shape from the other side.
`m_SolderMaskExpansion` is "Solder mask inflation around the pad or via"
(`board_design_settings.h:722`) and reaches `PAD::GetSolderMaskExpansion` (`pad.cpp:1725`),
`PCB_VIA::GetSolderMaskExpansion` (`pcb_track.cpp:1435,1462`), the painter and the 3D viewer, plus
`SOLDER_MASK_EXPANSION_CONSTRAINT`'s default (`drc_engine.cpp:1963`). `m_SolderMaskMinWidth`
reaches `plot_board_layers.cpp:96,1002` and the mask and silk DRC providers
(`drc_test_provider_solder_mask.cpp:903`, `drc_test_provider_silk_clearance.cpp:78`).

**The nuance worth stating precisely:** all four values *do* feed DRC — but only mask-domain and
paste-domain tests. None alters copper geometry or copper-to-copper clearance. Ignoring them is
sound provided the non-claim covers soldermask and solderpaste geometry *and* the DRC tests over
them, which is what D-227 states. The 10.0 manual's own framing agrees: the section "allows global
adjustment of the clearance … between solder mask / solder paste shapes and the copper shapes of
the parent pads" — copper is the input, mask and paste are the output.

## 6 — `zone_defaults`, which is new and stays refused

`(zone_defaults (property (layer "…") (hatch_position (xy X Y))))` is new in KiCad 10
(writer `pcb_io_kicad_sexpr.cpp:607-615,3099-3116`; parser `parser:2890-2971`). It carries the
default hatch *phase* of a hatched zone fill. `grep hatch_position` over the whole `9.0` tree
returns nothing.

A reader that consumes KiCad's stored `(filled_polygon …)` is unaffected; a reader that re-fills is
not, and the phase is copper geometry. It is therefore **not** added to the accepted set, and it is
named in `_REFUSED_SETUP_HEADS_ON_RECORD` so the absence reads as a decision. B-130's closed
vocabulary buckets any head it does not list as `other`, and `other` measured 0 on all six boards,
so no board of that cohort writes one — an absence that is evidence because the instrument could
have reported a presence.
