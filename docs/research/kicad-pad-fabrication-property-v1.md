# KiCad pad fabrication property (`PAD_PROP`) — what it does, and where

**Research date:** 2026-08-13
**Source:** KiCad master, `KiCad/kicad-source-mirror` (GitHub mirror of `gitlab.com/kicad/code/kicad`),
read at the time above. Every line reference below is to that tree.

## What this document covers

The pad-level `(property <token>)` expression: the token set KiCad can write, the shape its writer
emits, what its reader tolerates, and a complete sweep of every place in KiCad that reads the value.
It exists so that ADR-0099 can decide the construct on evidence rather than on the guess that a
"fabrication annotation" is inert.

## What it refuses to claim

- That the sweep below is complete for anything but the tree read on the date above. A new consumer
  can be added upstream at any time; the ADR's acceptance is therefore also a standing risk (R-144).
- That any of this describes CopperMCP behaviour. The mapping from these facts to Board IR is the
  ADR's job, not this document's.
- Anything about the *padstack* form of a pad, `(padstack …)`, which is a different head and is
  refused by name.

## The construct

### The enum

`pcbnew/padstack.h:105-119`:

```cpp
enum class PAD_PROP
{
    NONE,                  ///< no special fabrication property
    BGA,                   ///< Smd pad, used in BGA footprints
    FIDUCIAL_GLBL,         ///< a fiducial (usually a smd) for the full board
    FIDUCIAL_LOCAL,        ///< a fiducial (usually a smd) local to the parent footprint
    TESTPOINT,             ///< a test point pad
    HEATSINK,              ///< a pad used as heat sink, usually in SMD footprints
    CASTELLATED,           ///< a pad with a castellated through hole
    MECHANICAL,            ///< a pad used for mechanical support
    PRESSFIT               ///< a PTH with a hole diameter with tight tolerances for press fit pin
};
```

Nine enumerators, eight of them writable.

### What the writer emits

`PCB_IO_KICAD_SEXPR::format( const PAD* )`, `pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp`:

- `:1886-1901` maps the value to a `const char*` — `pad_prop_bga`, `pad_prop_fiducial_glob`,
  `pad_prop_fiducial_loc`, `pad_prop_testpoint`, `pad_prop_heatsink`, `pad_prop_castellated`,
  `pad_prop_mechanical`, `pad_prop_pressfit`. `PAD_PROP::NONE` leaves the pointer null, under a
  comment reading `// could be "none"`.
- `:2015-2016` emits `(property %s)` only `if( property )`.

So the writable shape is **exactly one bare, unquoted token from a closed set of eight**, and
**`(property none)` is never written**: an absent expression *is* `NONE`. This is the same asymmetry
ADR-0091 recorded for `zone_connect`'s unwritten `INHERITED`.

### What the reader tolerates, which is more

`PCB_IO_KICAD_SEXPR_PARSER::parsePAD`, `pcb_io_kicad_sexpr_parser.cpp:6913-6939`, `T_property` arm:

```cpp
case T_property:
    while( token != T_RIGHT )
    {
        token = NextTok();
        switch( token )
        {
        case T_pad_prop_bga:           pad->SetProperty( PAD_PROP::BGA );            break;
        ...
        case T_none:                   pad->SetProperty( PAD_PROP::NONE );           break;
        case T_RIGHT:                                                                break;
        default:
#if 0   // Currently: skip unknown property
            Expecting( ... );
#endif
            break;
        }
    }
```

Three consequences, all load-bearing for the ADR:

1. `(property)` with no token parses and leaves the value untouched.
2. An unrecognised token is **silently skipped** — the `Expecting(...)` is compiled out.
3. The arm is a **loop**, so `(property a b)` applies both in order and the **last one wins**.
   `(property pad_prop_heatsink pad_prop_castellated)` therefore resolves in KiCad to
   `CASTELLATED`.

(3) is the reason a consumer may not check only the first atom.

## Complete sweep of the consumers

Method, and its known blind spot. Every enumerator literal was swept individually — including
`PAD_PROP::NONE`, because a site testing `== NONE` contains no other enumerator and is invisible to
a sweep of the interesting ones — and the accessor was swept separately as `pad->GetProperty` and
`aPad->GetProperty`, so a site that reads the value without naming an enumerator is caught. The
blind spot that remains is a site reading it through a generic property-system lookup rather than
the accessor; the property manager registration at `pad.cpp:4110` is exactly such a route and is
listed below on its own line.

| Site | What it does | Reaches pad copper, hole, layer span, clearance or connectivity? |
|---|---|---|
| `pad.cpp:2210-2224` | `GetMsgPanelInfo` — the value's display name | No |
| `pad.cpp:3214-3239` | `CheckPad` advisories: `DRCE_PADSTACK` when a fiducial/testpoint/heatsink is NPTH, a castellated pad is not PTH, a BGA is not SMD, a mechanical pad is not PTH, a press-fit pad is not a drilled PTH | No — a DRC *report* |
| `pad.cpp:2853` | `ImportSettingsFrom` copies it | No — editor op |
| `pad.cpp:3683-3692`, `:4110-4112` | `ENUM_MAP` and the property-manager entry `Fabrication Property` | No directly; see "the rule-language route" below |
| `board.cpp:4223-4256` | `GetPadWithPressFitAttrCount`, `GetPadWithCastellatedAttrCount` | No — counters |
| `footprint.cpp:1662-1702` | `GetLikelyAttribute` skips fiducial/heatsink/castellated/mechanical pads when guessing SMD vs THT | No — a footprint type hint |
| `plot_brditems_plotter.cpp:232-264` | Gerber aperture attribute per value | No — plot metadata |
| `gendrill_writer_base.cpp`, `gendrill_gerber_writer.cpp` | drill-file attributes for `CASTELLATED` and `PRESSFIT` | No — drill file |
| `exporter_step.cpp`, `step_pcb_model.cpp` | `CASTELLATED` clips the pad against the board outline in 3D | No — 3D export |
| `board_statistics_report.cpp`, `dialog_board_statistics.cpp` | counts per value | No |
| `dialog_pad_properties.cpp` | the editor combo box | No |
| `drc_test_provider_courtyard_clearance.cpp:324-325` | **`HEATSINK` returns early**, so a holed heatsink pad is exempt from `DRCE_PTH_IN_COURTYARD` / `DRCE_NPTH_IN_COURTYARD` | No — a DRC verdict |
| `drc_test_provider_edge_clearance.cpp:395-396`, `:434-438` | **`CASTELLATED` pads are collected separately and exempted** from the edge-clearance test | No — a DRC verdict, but see below |
| `pns_kicad_iface.cpp:2366-2371` | **`CASTELLATED` adds the pad's hole to the router world as `AddEdgeExclusion`** | **Yes — routable space** |

### The one value that reaches geometry

`PNS_KICAD_IFACE::syncWorld`:

```cpp
if( pad->GetProperty() == PAD_PROP::CASTELLATED )
{
    std::unique_ptr<SHAPE> hole;
    hole.reset( pad->GetEffectiveHoleShape()->Clone() );
    aWorld->AddEdgeExclusion( std::move( hole ) );
}
```

A castellated pad is a plated half-hole on the board edge. KiCad's own router treats its hole as an
edge exclusion — a region routing may not enter — and the edge-clearance provider stops testing the
pad because the pad is *meant* to straddle the boundary. Neither region appears anywhere in
`Edge.Cuts`, so a consumer that derives the board outline from `Edge.Cuts` alone does not have it.

`CASTELLATED` is therefore the only writable value that changes what space is routable.

### The rule-language route

The value is a named term in KiCad's custom-rule language: `pad.cpp:4110` registers
`Fabrication Property` with the property manager, and KiCad's own shipped rule help
(`panel_setup_rules_help_9more_examples.md`) uses it:

```
# Don't use thermal reliefs on heatsink pads
    (constraint zone_connection solid)
    (condition "A.Fabrication_Property == 'Heatsink pad'"))
```

So a `.kicad_dru` can make the fabrication property decide a `zone_connection` — the very field
ADR-0091 reasoned about. This is the constraint any "annotations are inert" reading has to survive,
and it is the same shape as the text-variable problem D-184 hit for root board properties.

## Deliberately not established

- **What a castellated pad's exclusion region actually is**, beyond "the hole shape". Nothing here
  measured it, and ADR-0099 refuses the value rather than approximating it.
- **Whether any of this survives a round trip through a non-KiCad tool.** Not tested.
- **The KiCad version at which `PRESSFIT` was added.** The mirror was read at one commit; the token
  is present there, and that is the whole claim.
