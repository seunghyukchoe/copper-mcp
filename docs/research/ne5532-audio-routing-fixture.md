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

## Explicit nonclaims

This is not an electrically validated NE5532 design, an ERC result, a KiCad DRC result, a
combined-net autoroute result, a fabrication-ready board, a hardware measurement, or a FreeRouting
comparison. The local workstation used for this evidence had no `kicad-cli`, so the report records
authoritative DRC as `not_run`; it never substitutes parser acceptance for a KiCad DRC pass.

Future work may run a separately provenance-bound KiCad DRC on a disposable candidate derivative,
then must record that operation's exact binary/version, source and derivative hashes, and the
authority boundary separately.
