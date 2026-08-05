# NE5532-class audio routing fixture

**Reviewed:** 2026-08-05
**Fixture:** `benchmarks/audio/fixtures/ne5532-stereo-summing-routing-v1.kicad_pcb`
**Licence:** Apache-2.0; CopperMCP-original

## Research basis

Texas Instruments' public [NE5532x/SA5532x data sheet](https://www.ti.com/lit/ds/symlink/ne5532a.pdf)
identifies a dual amplifier's `OUT1`, `1IN-`, `1IN+`, `VCC-`, `2IN+`, `2IN-`, `OUT2`, and
`VCC+` pin roles. Its supply-layout guidance recommends 0.1-uF bypass capacitors close to the
power pins. The fixture consequently uses the same abstract roles: left/right inputs, left/right
summing nodes, feedback nodes, two output nodes, positive/negative supply rails, and analog ground.
It deliberately contains no component values, operating-point calculation, load, or schematic
claim. TI explicitly places suitability and implementation validation with the customer.

The board is written in KiCad's documented native
[PCB S-expression format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/). It contains
synthetic `CopperMCP:*` footprint names and deterministic UUIDs; it does not import a third-party
board, footprint library, schematic, artwork, BOM, or download.

## What the benchmark proves

`scripts/benchmark_ne5532_audio_routing.py` calls the shared public
`copper_mcp.tools.preview_route` application service—the same route-preview service exposed by the
MCP adapter—against a private copy of the board. The fixed test has 14 footprints, 35 pads, 11
nets, and zero starting segments, vias, or zones. It independently previews eight selected nets:
four two-pad nets and four multi-pad nets. Every request is replayed at least twice and must return
byte-identical service output. For each routed candidate, the runner records candidate identity,
pad/path counts, route length, via count, expanded states, and obstacle checks. It fails on any
provenance hash, source-count, deterministic-result, internal-violation, source-mutation, or
declared-coverage drift.

The nontrivial cases are the three- and four-pad summing/supply nets. They exercise the existing
bounded multi-pin ordering path rather than merely a two-pad line. Each preview starts from the
same unrouted source board; no individual candidate is merged with another candidate.

## Recorded KiCad observation

On 2026-08-05, the local KiCad 10.0.5 CLI JSON DRC was run against a private copy of the exact
fixture source (`sha256:749adc8b4d26b7f7ef878f9cf681521a8efdf446b9f0bf559243918e6e1957a9`).
The zero-copper source reported 14 violations and 24 unconnected items. Eight disposable
derivatives were then serialized independently, one for each preview candidate and each beginning
from that unchanged source snapshot. They retained 14 violations and reported, in declaration
order, 23, 23, 22, 22, 23, 23, 21, and 21 unconnected items. The benchmark did not merge or apply
candidates, and its source-copy preservation check remained true.

## Explicit nonclaims

This is not an electrically validated NE5532 design, an ERC result, a clean KiCad DRC result, a
whole-board or combined-net route-feasibility result, a fabrication-ready board, a hardware
measurement, or a FreeRouting comparison. The recorded DRC observation is non-clean and limited to
independent, disposable single-candidate derivatives; it never substitutes parser acceptance or a
reduced unconnected count for electrical, fabrication, or completed-board evidence.
