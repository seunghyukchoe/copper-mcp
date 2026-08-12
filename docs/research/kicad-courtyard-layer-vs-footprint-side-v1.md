# KiCad courtyard layer versus footprint side

Research date: 2026-08-12. Supports [ADR-0097](../adr/0097-courtyard-layer-decides-the-side.md)
and issue #151. Tool under test: KiCad 10.0.5 as installed locally (`kicad-cli version` reports
`10.0.5`). Source read at tag `10.0.5` of `gitlab.com/kicad/code/kicad`. No external code is
copied; line references are pointers, not excerpts.

## The question

Board IR refused any board on which a footprint's courtyard shape sat on the courtyard layer
opposite the footprint's copper side — `unsupported.transform`, "courtyard layer does not match
its footprint side". Three readings were open, and they lead to different fixes:

1. the board is defective and the refusal is correct;
2. KiCad permits it and it means something specific;
3. CopperMCP derives the side rather than reading it, or flips the courtyard layer itself.

## Reading 3 is ruled out first, because it is cheapest

`_footprint_side` in `src/copper_mcp/adapters/kicad_board_ir.py` maps the footprint's own
`(layer ...)` atom — `F.Cu` or `B.Cu` — and nothing else; it derives nothing and refuses any
other value. `_transform` applies a translation and a quarter turn and never a mirror. No code
path in the adapter reads, writes, or flips a courtyard shape's layer. The side was already
being read, and the courtyard layer was already being taken verbatim from the file.

The board data agrees. In the survey corpus the *same* stock library footprint appears both on
`F.Cu` at 0° and on `B.Cu` at 180°, and in both poses it carries two `F.CrtYd` and two `B.CrtYd`
rectangles. Its local coordinates and its courtyard layers are mirrored between the two poses,
which is `FOOTPRINT::Flip` doing the flip in KiCad and writing the result into the file
(`pcbnew/footprint.cpp:2932`, per-child `PCB_SHAPE::Flip` at `pcbnew/pcb_shape.cpp:579-584`,
layer swap in `common/layer_id.cpp:191-192`). A footprint that carries both layers before the
flip still carries both after it. So the mismatch is not a flip that CopperMCP failed to follow,
and it is not a flip that CopperMCP applied twice.

## Reading 1 is ruled out by the library and by the tool

Every mismatching footprint in the corpus is one unmodified stock KiCad footprint:
`Connector_Wire:SolderWire-0.5sqmm_1x02_P4.6mm_D0.9mm_OD2.1mm_Relief`, shipped at
`KiCad.app/Contents/SharedSupport/footprints/Connector_Wire.pretty/`. Its own file declares
`(layer "F.Cu")` and draws four courtyard rectangles: the two-part full envelope on `F.CrtYd`
and the strain-relief slot on `B.CrtYd`. Its `descr` names the mechanism — a soldered wire
connection with **feed-through** strain relief — so the slot passes through the board and keeps
out on the far face. This is not a designer error; it is not even the designer's drawing.

KiCad reports nothing about it. There is no check anywhere in `pcbnew` that compares a courtyard
shape's layer against its footprint's copper side. `MALFORMED_COURTYARD`
(`pcbnew/drc/drc_test_provider_courtyard_clearance.cpp:96-112`) fires only when the shapes on one
layer fail to chain into a closed polygon. `MISSING_COURTYARD` (same file, `113-125`) fires only
when **both** layers are empty, so a back-only courtyard satisfies it on a front-side footprint.
Measured directly: a board with one `F.Cu` footprint carrying only a `B.CrtYd` rectangle produces
zero violations at `--severity-all`.

KiCad's own library convention requires the arrangement rather than merely tolerating it. KLC
F5.3 (`gitlab.com/kicad/libraries/klc`, `content/footprint/F5/F5.3.adoc`) requires the enclosing
courtyard on `F.CrtYd` and states that where the component requires a courtyard on the back of
the PCB, a corresponding courtyard must be provided on `B.CrtYd`.

## Reading 2, and what it means precisely

**A courtyard belongs to the layer it is drawn on. The footprint's side is not consulted.**

- `FOOTPRINT::BuildCourtyardCaches` (`pcbnew/footprint.cpp:3664`) walks `GraphicalItems()` and
  files each `PCB_SHAPE` by `item->GetLayer() == B_CrtYd` (`:3683`) or `== F_CrtYd` (`:3690`)
  into two separate lists, converted by two independent `ConvertOutlineToPolygon` calls (`:3704`,
  `:3735`) into the `front` and `back` members of `FOOTPRINT_COURTYARD_CACHE_DATA`
  (`pcbnew/footprint.h:139-145`). `IsFlipped()`, `GetSide()` and the footprint's own `GetLayer()`
  appear nowhere in the function. The struct's own comment says a footprint can have both
  populated.
- `DRC_TEST_PROVIDER_COURTYARD_CLEARANCE::testCourtyardClearances`
  (`pcbnew/drc/drc_test_provider_courtyard_clearance.cpp:137`) fetches
  `GetCourtyard( F_CrtYd )` and `GetCourtyard( B_CrtYd )` for both footprints (`:172-173`,
  `:194-195`) and runs two independent blocks — front at `:219` reporting on `F_CrtYd`, back at
  `:249` reporting on `B_CrtYd`. No pair is ever skipped because the footprints sit on opposite
  copper sides; the only skips are empty courtyards, bounding-box misses, and the error limit.
- KiCad's own QA test names the distinction: `qa/tests/pcbnew/drc/test_drc_courtyard_overlap.cpp`
  case "two footprints, overlap, different sides" (`:282`) leaves **both** footprints on the
  front, gives one an `F.CrtYd` rectangle and the other a `B.CrtYd` rectangle at the same
  position, and expects no collision. "Different sides" there means different *shape* layers.

### Swept in both directions

Enumerating sites that read a shape's own layer would be blind to sites that instead branch on
`IsFlipped()`. Both sweeps were done. Side-agnostic, layer-driven sites are the norm: the cache
build, courtyard-clearance DRC, the interactive variant, `drc_engine.cpp:1658-1666` (a
footprint's effective layer set gains the front mask if its front courtyard is non-empty and the
back mask if its back one is, regardless of side), `drc_test_provider_physical_clearance.cpp`
(footprints are inserted into the R-tree on both courtyard layers unconditionally), plus the
Specctra, ODB++, statistics, autoplacer and painter paths.

Five sites *do* derive a courtyard layer from the footprint's side, and none of them is the
overlap rule:

| Site | What it is for |
| --- | --- |
| `pcbexpr_functions.cpp:392`, `:457` | The DRC **rule language**: `intersectsFrontCourtyard()` means "the courtyard on the footprint's own mounting side", so a flipped footprint's `intersectsFrontCourtyard()` reads its `B.CrtYd` polygon. `intersectsCourtyard()` (`:284`) ORs both and is side-agnostic. |
| `zone_filler.cpp:2453` | A commented workaround for `GetCourtyard()` falling through to the front on inner copper layers; outer layers stay layer-driven. |
| `plot_board_layers.cpp:663` | Sizing the DNP cross-out on `F.Fab`/`B.Fab`, inside a guard that already matched the fab layer to the copper side. |
| `pcb_io_ipc2581.cpp:3088-3091` | Normalising a bottom-side component's package definition back to "top" for IPC-2581 export. |

One footgun worth recording because it is adjacent and easy to trip over:
`FOOTPRINT::GetCachedCourtyard` (`pcbnew/footprint.cpp:3652`) dispatches on `IsBackLayer( aLayer )`
(`include/layer_ids.h:803`), so **every** layer that is not a back layer — including all inner
copper layers and `UNDEFINED_LAYER` — silently returns the *front* courtyard. KiCad documents this
in-tree at `pcbnew/zone_filler.cpp:2451-2452` and `pcbnew/pcb_shape.cpp:370-371`. CopperMCP does
not reproduce the fallthrough: its accessor takes a boolean naming the courtyard layer directly.

## Measured against the tool

Seven two-footprint boards, each pair of 10 mm courtyard squares fully coincident, run through
real `kicad-cli 10.0.5` DRC at `--severity-all`:

| First footprint | Second footprint | `courtyards_overlap` |
| --- | --- | --- |
| `F.Cu` drawing `B.CrtYd` | `B.Cu` drawing `B.CrtYd` | **yes** |
| `F.Cu` drawing `B.CrtYd` | `B.Cu` drawing `F.CrtYd` | no |
| `F.Cu` drawing `B.CrtYd` | `F.Cu` drawing `F.CrtYd` | no |
| `F.Cu` drawing `B.CrtYd` | `F.Cu` drawing `B.CrtYd` | **yes** |
| `F.Cu` drawing `F.CrtYd` | `F.Cu` drawing `F.CrtYd` | **yes** |
| `B.Cu` drawing `F.CrtYd` | `F.Cu` drawing `F.CrtYd` | **yes** |
| `F.Cu` drawing `B.CrtYd`, alone | — | no violation of any type |

The verdict tracks the courtyard layer in all seven and the footprint side in none of them. Rows
one and four differ only in the second footprint's copper side and agree; rows one and two differ
only in the second footprint's courtyard layer and disagree. Row six is the reverse direction of
row one and behaves the same way, so the result is not an artifact of testing only `B.CrtYd`.

The reproducible form of this measurement, including a penetration sweep confirming the same
10,000 nm collision threshold on `B.CrtYd` that ADR-0075 measured on `F.CrtYd`, is
`scripts/benchmark_courtyard_side_oracle_parity.py` (B-101).

## What this note does not claim

Nothing here is a claim about electrical, thermal or manufacturing correctness, about arcs or
curved courtyard chains on either layer, about the `pth_inside_courtyard` rule KiCad also reports
on the fixtures above, or about non-zero configured courtyard clearance. It does not claim that
the KLC source revision read matches the rendering at `klc.kicad.org`, which returns HTTP 403 to
non-browser clients. It does not claim that any board in any corpus is DRC-clean.
