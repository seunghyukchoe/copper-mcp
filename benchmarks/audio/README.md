# Audio circuit capability benchmarks

This directory tests what CopperMCP can demonstrate today without importing somebody else's
schematic or overstating PCB readiness. All executable fixtures are original CopperMCP test data or
separately identified open hardware. External audio-project sites are metadata-only references and
are never downloaded by the runner.

## Evidence ladder

| Stage | Question | Current automated evidence |
|---|---|---|
| Source intake | Is provenance and redistribution permission explicit? | Catalog validation |
| Circuit intent | Is a bounded topology and expected connectivity available? | Canonical original RC intent fixture |
| Schematic generation | Can the deterministic core render the supported topology as KiCad bytes? | Byte-stable render plus optional KiCad SVG/netlist round trip |
| ERC | Does the generated schematic pass authoritative electrical checks? | Not implemented |
| Board generation | Can MCP place components and create a new board from circuit intent? | Not implemented |
| Board IR inspection | Can CopperMCP represent the board subset deterministically? | Automated |
| Route preview | Can it propose a bounded immutable route or a typed refusal? | Automated for declared nets |
| Candidate KiCad DRC | Does the exact disposable candidate pass KiCad? | Available generally, not claimed by this microcase |
| Hardware evidence | Is it safe, manufacturable, and measured? | Not claimed |

The first board fixture is a synthetic low-voltage RC low-pass connectivity microcase. `AUDIO_IN`
is deliberately a supported two-pad net; `AUDIO_OUT` and `GND` are multi-pad nets that demonstrate
the router's current typed refusal. A separate independently authored Circuit Intent fixture
describes a two-component RC topology and renders into a new KiCad schematic derivative. The two
fixtures are not asserted to have schematic-to-board parity. Neither is a component-qualified
product, carries a frequency or performance claim, or should be fabricated.

Run the offline, network-free capability check with:

```bash
make benchmark-audio
make check-circuit-intents
```

The command uses the same protocol-independent services as the MCP adapter, runs each declared
operation twice, and emits only structural counts, digests, statuses, and diagnostic codes. The
Circuit Intent check separately validates its strict schema and canonical bytes, then renders twice
in memory and compares the complete artifacts. Neither command fetches reference sites, copies
circuit content, writes a board candidate, runs ERC, or applies copper. Catalog, board, and licence
files are read once under byte and path bounds; the runner uses that exact validation snapshot and
reports only capability claims derived from observed results.

`ne5532-stereo-summing-routing-v1` is a larger CopperMCP-original Apache-2.0 routing topology:
14 synthetic footprints, 35 pads, 11 nets, and no starting copper. Its dedicated
`scripts/benchmark_ne5532_audio_routing.py` runner invokes the public service behind MCP for eight
independent F.Cu previews—four two-pad and four multi-pad—and records deterministic candidate IDs,
path counts, lengths, vias, and bounded search work. It intentionally does not combine candidates
into a whole-board route. Its TI/KiCad research basis and nonclaims are in
[`docs/research/ne5532-audio-routing-fixture.md`](../../docs/research/ne5532-audio-routing-fixture.md).

## Licensing

The synthetic fixture and catalog are Apache-2.0 test data under the repository license. A catalog
entry for an external website records only the site's identity, top-level URL, terms URL, reviewed
date, and reuse restriction. It does not grant CopperMCP any right to reproduce that site's text,
schematics, PCB artwork, downloads, or contributed material.
