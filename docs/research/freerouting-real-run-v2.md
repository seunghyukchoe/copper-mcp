# FreeRouting v2.2.2 real-run evidence

Research date: 2026-08-05

This note records one small, public, reproducible observation through the existing GPL-isolated
comparison boundary. It is **not** an assertion of feature parity, benchmark leadership, or
whole-board autorouting capability.

## Official inputs and host boundary

- FreeRouting asset: [v2.2.2 JAR](https://github.com/freerouting/freerouting/releases/download/v2.2.2/freerouting-2.2.2.jar),
  SHA-256 `f7a716c8f2586eb79d7e6c54c497a6752c3b2401730fdb75c37245d461baa228`.
- Release metadata and the GPL-3.0 project boundary: [official releases](https://github.com/freerouting/freerouting/releases)
  and [official repository](https://github.com/freerouting/freerouting).
- KiCad's documented bridge is export of the exact board to DSN followed by import of the
  generated SES into a disposable KiCad copy: [DSN export](https://docs.kicad.org/master/en/pcbnew/pcbnew.html#specctra-dsn)
  and [Specctra-session import](https://docs.kicad.org/master/en/pcbnew/pcbnew.html#importing-specctra-session-files).

The evaluation used KiCad CLI/UI 10.0.5 and an already installed OpenJDK 26.0.2. No Java runtime
or FreeRouting application was installed globally. The JAR needs
`-Djava.awt.headless=true` for its documented command-line workflow in this macOS environment:
without it, Swing attempts to initialize a rendering pipeline before it handles DSN arguments.
The benchmark harness applies that JVM property without changing FreeRouting's DSN/SES protocol.

## Fixture and observed result

`benchmarks/routing/fixtures/freerouting-common-two-pad-v1.kicad_pcb` is a CopperMCP-authored,
Apache-2.0, two-SMD-pad fixture with one intentionally unrouted `AUDIO` net. KiCad UI exported
the committed DSN; its source/DSN hashes are retained in the result artifact. Before routing,
KiCad DRC found 0 hard violations and 1 unconnected item.

The bounded external process emitted a valid SES (`sha256:eb016ba1a7a4e680c472787dcf49b5f115c969d33603951b9b9fb3e94ba8fc4a`).
That SES was imported through KiCad's GUI into a disposable copy and saved. KiCad GUI DRC reports
then verified that imported board and a CopperMCP workspace-preview plus pure-
`apply_route_candidate` kernel result. The harness bounds, parses, and hashes those reports
because this macOS installation's `kicad-cli pcb drc` aborts before emitting JSON. The latter does
not exercise MCP transport, operator authorization, apply tokens, CAS, backups, or atomic
publication, so it is not evidence about the public apply-service workflow.

The committed machine-readable record is
[`2026-08-05-freerouting-common-two-pad.json`](../../benchmarks/results/routing/2026-08-05-freerouting-common-two-pad.json):

| Result | KiCad hard violations | KiCad unconnected | Vias | Routed length |
| --- | ---: | ---: | ---: | ---: |
| CopperMCP | 0 | 0 | 0 | 20.0 mm |
| FreeRouting v2.2.2 | 0 | 0 | 0 | 20.0 mm |

The record also binds the release asset and each result board to content hashes, reports bounded
process timing, and confirms that source bytes remained unchanged. It records the source's KiCad
GUI DRC report under the same bounded parsing and hashing gate; the observed counts match the
declared baseline of 0 hard violations and 1 intentional unconnected item. A GUI report header
identifies only a board basename, not source bytes, so the source/report relationship is
self-attested and non-causal unless a retained GUI attestation bundles both exact hashes. The
parser accepts only the recorded KiCad 10.0.5 full line sequence, including its documented blank
separators and ignored-check list; extra, missing, duplicate, or reordered lines fail closed. The
DSN/source-export statement remains self-attested: separate source and DSN hashes do not
demonstrate that KiCad exported one from the other. The FreeRouting import and CopperMCP runner
receipts are deliberately self-attested; the harness consequently sets
`comparison_closed: false` and `status: unavailable_or_incomplete` with
`self_attested_unverified`. This evidence demonstrates a live file/process/KiCad bridge on a
trivial board only. A harness-owned SES import and constrained candidate-runner transaction remain
required before any comparison can close.

## Re-run conditions

Use the exact public fixture, release SHA-256, seed `0`, a bounded 120-second limit, KiCad 10.0.5,
and the `freerouting_release_provenance` schema validated by
`scripts/benchmark_freerouting_comparison.py`. The user-supplied Java/JAR processes are lifecycle
and output bounded but not sandboxed; execute only binaries you authorize. The harness strips
provider tokens and does not retain board contents, child argv, or child stdout/stderr in the
artifact.
