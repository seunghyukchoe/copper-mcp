# Audio circuit benchmark intake

**Review date:** 2026-08-03

CopperMCP can use public audio-project catalogs to choose useful capability categories, but a public
web page is not automatically an open dataset. This review separates private reference use from
content that may be committed, tested in CI, redistributed, or used to support a public benchmark
claim. It is a conservative engineering policy, not legal advice.

## Source review

| Source | Reviewed evidence | Repository classification | Permitted CopperMCP use |
|---|---|---|---|
| Elliott Sound Products | [Project catalog](https://sound-au.com/p-cat.htm) and [site disclaimer](https://sound-au.com/disclaimer.htm) | `reference-only`; personal/non-commercial source rights; permission required for reproduction, republication, commercial use, and project/article deep linking | Human review of broad challenge categories and provenance terms only |
| diyAudioProjects.com | [Top-level site](https://diyaudioprojects.com/) and [Terms of Service](https://www.diyaudioprojects.com/tos.htm) | `reference-only`; one personal/non-commercial offline copy; permission required for reproduction or distribution | Human review of broad challenge categories and provenance terms only |

No schematic, diagram, PCB artwork, BOM, component selection, construction text, screenshot,
download, or reconstructive description from either site is included in CopperMCP. Individual
contributors may hold additional rights, so a site's ability to display submitted content is not a
licence for this repository. An explicit written permission or a separately verified compatible
licence would require a new provenance review.

The offline runner never opens these URLs. It records source-level metadata so contributors can
understand why a reference is not a fixture, and it rejects any catalog claim that these sources are
redistributable. Terms must be reviewed again before relying on them later.

## Safe benchmark strategy

1. Use the reference catalogs only to identify general challenge families such as low-voltage
   small-signal paths, active filters, headphone loads, power stages, power supplies, and test
   equipment.
2. Write a fresh CopperMCP circuit-intent case from fundamentals, or accept a board carrying an
   explicit compatible open licence and complete provenance.
3. Keep high-voltage, mains-powered, high-current, and thermal-risk designs out of default tests.
4. Bind every committed artifact and its exact licence file to SHA-256 digests,
   repository-relative paths, an SPDX licence identity, safety class, derivation statement, and
   precise claim/non-claim list.
5. Run local structural and routing evidence without network access or source mutation. Treat KiCad
   DRC, ERC, simulation, DFM, prototype safety, and measured audio performance as separate gates.

## Current capability result

[`benchmarks/audio/catalog.json`](../../benchmarks/audio/catalog.json) contains two executable cases:

- an Apache-2.0, independently authored low-voltage RC connectivity microcase; and
- the existing CERN-OHL-S-2.0 CopperTone board preview.

The RC board microcase confirms that the MCP-shared services deterministically convert a three-net,
eight-pad KiCad board, route the deliberately isolated two-pad `AUDIO_IN` net, and return
`invalid_two_pin_net` for the two declared multi-pad nets. CopperTone confirms deterministic Board
IR conversion only. A local KiCad 10.0.5 smoke test also parses the RC board and plots its `F.Cu` and
`Edge.Cuts` Gerbers without changing the source; it does not run or claim clean DRC.

An independently authored Circuit Intent fixture separately encodes a two-component, three-net RC
topology under the `copper.circuit-intent` `0.1.0` schema. The deterministic core validates its
complete pin assignment and content digest, embeds original symbols, and renders a new in-memory
KiCad `20250114` schematic. A KiCad 10.0.5 integration check exports SVG and `kicadxml` and verifies
the exact component-pin net membership. This is format/connectivity evidence, not ERC or parity with
the board microcase. The two external catalogs are not executed.

The validator performs one bounded catalog read and one repository-confined bounded read for each
artifact and licence. The runner carries those exact validated bytes into its private workspace,
derives capability claims from the observed inspection, candidate, and typed-refusal results, and
rejects any declared claim that does not match the evidence.

This evidence does **not** show that MCP can derive a circuit from a web page, expose Circuit Intent
over MCP, run ERC, choose or verify values and parts, establish schematic-to-board parity, place a
board, autoroute a whole design, validate electrical or audio performance, or produce a fabrication-
ready result. Those are future acceptance stages, not implicit consequences of structural rendering
or a route preview.

Run the checked, network-free capability report with:

```bash
make benchmark-audio
```

The report is an ephemeral functional check. It is not a performance result and is not added to the
append-only benchmark ledger until a clean-commit evidence format and repeatable run protocol are
defined.
