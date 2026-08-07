# KiCad pad `zone_connect`: what it changes, and which values are already sound

Research date: 2026-08-08. This note establishes, from KiCad's own format definition and from
KiCad 10 source, what a pad's `zone_connect` override does to copper geometry and to electrical
connectivity, and which of its values the CopperMCP Board IR model is already sound for. It
supports [issue #124](https://github.com/seunghyukchoe/copper-mcp/issues/124),
[ADR-0091](../adr/0091-attaching-pad-zone-connect-overrides.md), decision
[D-178](../ledgers/decision-ledger.md) and risk [R-135](../ledgers/risk-register.md).

No external code is copied. Source lines are cited by file, function and line number against the
KiCad source mirror as read on the research date; the constructs are otherwise described in
prose. No content from the surveyed board tree is reproduced — the test fixtures are authored
from the format definition.

## Sources

- KiCad S-expression board file format, footprint and pad sections:
  <https://dev-docs.kicad.org/en/file-formats/sexpr-intro/> and
  <https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/>
- `pcbnew/zones.h` — the `ZONE_CONNECTION` enumeration
- `pcbnew/zone_filler.cpp` — `ZONE_FILLER::knockoutThermalReliefs`, spoke construction, and the
  final clip of the fill to the zone's extents
- `pcbnew/drc/drc_engine.cpp` — `DRC_ENGINE::EvalZoneConnection` and the
  `ZONE_CONNECTION_CONSTRAINT` resolution order
- `pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp` and `…_sexpr.cpp` — how the token is
  read and written

All source citations are against the `master` branch of the KiCad source mirror
(<https://github.com/KiCad/kicad-source-mirror>) as fetched on 2026-08-08.

## Finding 1 — the value domain is an enum, and the format documentation understates it

The format defines the token as: "The optional `zone_connect` token defines how all pads are
connected to filled zone. If not defined, then the zone `connect_pads` setting is used. Valid
connection types are integers values from 0 to 3", and then enumerates only three of them —
0 not connected, 1 thermal reliefs, 2 solid fill.

`pcbnew/zones.h` gives the missing fourth and the sentinel:

| Token | `ZONE_CONNECTION` | Meaning |
|---|---|---|
| *(absent)* | `INHERITED = -1` | Defer to the footprint override, then to the zone's `connect_pads` |
| `0` | `NONE` | The pour is knocked out around the pad; no attachment |
| `1` | `THERMAL` | Thermal-relief spokes across a gap |
| `2` | `FULL` | Solid fill; the pour laps over the pad |
| `3` | `THT_THERMAL` | Thermal relief for plated through-hole pads, solid for the rest |

Two properties of the token matter for a parser and are easy to assume wrongly:

1. **`-1` is never written.** `PCB_IO_KICAD_SEXPR::format` emits `(zone_connect %d)` only when
   the local value differs from `INHERITED` (`…_sexpr.cpp:2139` for a pad, `:1497` for a
   footprint, `:2389` for a per-layer padstack entry). An absent token *is* inheritance, so a
   file carrying `-1` was not written by KiCad and its meaning is not established.
2. **The value is not validated on read.** The parser casts it directly:
   `pad->SetLocalZoneConnection( (ZONE_CONNECTION) parseInt( "zone connection value" ) )`
   (`…_sexpr_parser.cpp:6834`; the footprint form is at `:5854`). Any integer at all survives
   round-tripping through KiCad. A tool that accepts the field must check the domain itself.

## Finding 2 — resolution order, and what `3` collapses to

`DRC_ENGINE::EvalRules` for `ZONE_CONNECTION_CONSTRAINT` returns, in priority order: the **pad's**
local override when it is not `INHERITED` (`drc_engine.cpp:1211`); then a matching custom DRC rule;
then the **footprint's** local override (`:2089`); then the **zone's** `connect_pads` mode.

The pad override comes **first**, not after the rules. It sits in the block `EvalRules` introduces
as "Local overrides take precedence over everything *except* board min clearance" (`:1138`) and
returns before the rule iteration at `:1922` is ever reached; only the footprint fallback at
`:1949` onward re-enters `m_constraintMap`. An earlier draft of this note had the pad override
*after* custom rules. The correction runs in the direction that strengthens the conclusion below —
a custom DRC rule cannot detach a pad that carries `1`, `2` or `3` — but the citation was wrong
and is corrected here rather than left standing because its error was convenient.

`DRC_ENGINE::EvalZoneConnection` (`drc_engine.cpp:972`) then collapses `THT_THERMAL`: it becomes
`THERMAL` when the item is a plated through-hole pad and `FULL` otherwise. So `3` is never a
distinct physical treatment — it is a selector between `1` and `2` by pad type, and **both of
its outcomes attach the pad to the pour**.

## Finding 3 — the filler is the only thing that turns the field into copper

`ZONE_FILLER::knockoutThermalReliefs` (`zone_filler.cpp:1827`) is where the resolved value is
consumed. For a same-net pad it switches (`:1972`):

- `THERMAL` — evaluate the thermal-relief gap, record the pad for spoke construction, and knock
  out the gap annulus. Spokes are built later by `ZONE_FILLER::buildThermalSpokes` (`:3356`) as
  square-ended segments running from the pad centre outward, and each is added to the fill
  (`:2970`) only if its outer end lands inside the zone body or inside another surviving spoke.
- `NONE` — take the larger of the physical-clearance constraint and the zone's own clearance and
  knock the pad, or its hole, out of the fill (`:1988`).
- `FULL` — no knockout at all; the pour laps over the pad.

Pads on a *different* net never reach that switch: they are collected as no-connection pads and
knocked out with clearance (`:1891`, `:1906`), so `FULL` can only ever apply same-net.

Two negatives are as load-bearing as the switch itself, and both were checked by reading for the
absence rather than assumed:

- **The field changes no other geometry.** It does not appear in pad shape construction, in pad
  or hole clearance for tracks and vias, or in the zone outline. `zone_filler.cpp` is the only
  place that turns the resolved constraint into copper. It is *not* the only place that reads it:
  `drc_test_provider_zone_connections.cpp:110` reads it to flag a thermal connection starved of
  spokes, and `board_inspection_tool.cpp:1084` reads it to explain a connection in the UI. Both
  report; neither produces geometry, so the geometric claim stands and the "only consumer" phrasing
  an earlier draft used does not.
- **Poured copper never leaves the zone.** The finished fill is intersected with the zone's own
  maximum extents (`zone_filler.cpp:3139`) after spokes are added and clearance holes subtracted.
  Thermal spokes are therefore clipped like everything else. For **every** value of
  `zone_connect`, the poured copper is a subset of the zone boundary polygon.

## Finding 4 — the obstacle direction cannot break, for any value

CopperMCP models a zone as its boundary polygon (ADR-0013), or — only against proved fill — as
the exact islands (ADR-0039, ADR-0070). Finding 3 makes the first an over-approximation for
every value, unconditionally. The second is KiCad's own recomputed polygon, which has the value
already applied, behind a Board IR containment gate that refuses fill escaping its backing zone
outline, so the replacement can only shrink the obstacle.

There is no value of `zone_connect` for which the current obstacle model under-approximates. It
is not a near miss that happens to hold on the surveyed boards; it follows from the clip.

## Finding 5 — the connectivity direction breaks for exactly one value

CopperMCP derives pad-to-pour attachment from one place only: exact integer contact between a
pad's *under-approximating* core rectangles and a **verified** fill island (ADR-0021), where
verified means a fresh private KiCad refill reproduced the cached geometry. A same-net zone on
any layer that has no verified fill vetoes the already-connected claim entirely, and the layered
adapter refuses a same-net zone outright. Nothing infers attachment from a pad sitting inside a
zone outline on the same net.

So no *live* claim reads `zone_connect`: by the time CopperMCP sees a polygon, KiCad has already
applied the value to it, including the void that `0` produces.

What does read it, indirectly, is the Board IR snapshot. `Zone.pad_connection` — parsed from the
zone's `connect_pads`, one of `thermal`, `thru_hole_only`, `solid`, `none` — is carried into every
snapshot and into every snapshot digest, and it is a statement about how pads attach to that pour.
It is *not* carried into Circuit Scene: `circuit_scene.py::_zone_object` publishes boundary, net,
clearance and minimum thickness only. A
pad's `zone_connect` overrides that statement for one pad. Discarding the override therefore
leaves a published statement that may now be wrong, and the direction it is wrong in depends on
the value:

| Pad value | Effect on the pad | Effect of discarding it |
|---|---|---|
| `1` thermal | attaches | `Zone.pad_connection` still reads "attached"; the *mode* may be wrong in either direction |
| `2` solid | attaches | same |
| `3` THT thermal | attaches (as `1` or `2`) | same |
| `0` none | **detaches** | `Zone.pad_connection` may assert `thermal`/`solid`/`thru_hole_only` over a pad the designer isolated |

The asymmetry is the whole finding, and it is about *attachment*, not about the mode. A lost
**attaching** override never turns `Zone.pad_connection` into a claim of attachment where there is
none: whichever of `thermal`, `thru_hole_only` or `solid` the zone declares, and whichever of `1`,
`2` or `3` the pad overrode it with, both readings answer "attached". The published mode can still
be wrong in **either** direction — a zone declaring `solid` over a pad overridden to `1` overstates
the copper, and a zone declaring `no` over a pad overridden to `2` understates it — so "either
exactly right or too pessimistic", which an earlier draft of this note's summary claimed, is false.
It is an imprecision, not an unsoundness: nothing in CopperMCP reads the field, and the identical
exposure already exists with no pad token present at all, since a zone declaring `thermal` says
nothing about whether Finding 3's spoke-survival rules left any spoke standing.

A lost **detaching** override is different in kind. It leaves the model believing in *more*
connection than the board has, which is the direction the project forbids — and it is the case the
designer cared enough about to override.

## What this note deliberately does not claim

- **It does not claim `0` is unsound today.** No current surface reads `Zone.pad_connection` to
  decide connectivity; the refusal of `0` is a guard on a published statement and on the
  inference a future surface would be tempted to make, not the repair of a live defect.
- **It does not establish how `zone_connect` should be modelled.** Carrying it as a per-pad
  `PadZoneConnection` mirroring `Zone.pad_connection` is the coherent shape and is deferred on
  cost — see ADR-0091's alternatives. Nothing here says what such a field should be named or
  where a consumer should read it.
- **It says nothing about thermal spoke geometry.** `thermal_gap`, `thermal_bridge_width` and
  `thermal_bridge_angle` on a pad remain refused, and the spoke-survival rules in Finding 3 are
  recorded to show that a `THERMAL` pad can end up isolated in practice — not as a model
  CopperMCP implements.
- **It measures no board.** The one surveyed board carrying the field carries `2` on five pads;
  that is a fact about a private tree and is not evidence about the population.
