# FreeRouting comparison boundary and reproducible harness

Research date: 2026-08-05

## Scope and conclusion boundary

CopperMCP is an Apache-2.0 candidate-first platform; FreeRouting is GPL-3.0.  The comparison
is therefore deliberately process-isolated: this repository builds neither links nor vendors
FreeRouting.  It only launches a user-supplied released JAR and exchanges user-supplied files.
No report can claim a comparison is complete unless both result boards have the same KiCad CLI
DRC evidence, their source hashes remain unchanged, and every version/hash/timeout is retained.

The current reference release is **FreeRouting v2.2.4** (2026-05-13).  Its official README
documents `java -jar freerouting-2.2.4.jar -de MyBoard.dsn -do MyBoard.ses -inc GND,VCC` and
the release page identifies v2.2.4 as latest at research time.  FreeRouting is GPL-3.0 according
to its repository license label.  Sources: [release](https://github.com/freerouting/freerouting/releases),
[README CLI and JRE instructions](https://github.com/freerouting/freerouting#command-line-interface),
and [repository licence label](https://github.com/freerouting/freerouting).

KiCad documents the matching workflow: export the *same* board to DSN, route it externally to
SES, then import the SES into the existing board.  Import adds routing only; footprints, nets,
and outline must match.  This is why the harness requires a KiCad-imported disposable
FreeRouting board rather than pretending to parse or apply SES itself.  See [KiCad PCB Editor:
Importing Specctra session files](https://docs.kicad.org/master/en/pcbnew/pcbnew.html#importing-specctra-session-files)
and [Specctra DSN exporter](https://docs.kicad.org/master/en/pcbnew/pcbnew.html#specctra-dsn).

## Harness

`scripts/benchmark_freerouting_comparison.py` accepts:

- an independently authored, provenance-described KiCad source board and its KiCad-exported
  DSN;
- a Java executable and a release JAR, recorded by SHA-256 and run with the documented DSN/SES
  argv (never through a shell);
- a CopperMCP disposable result board and a separate KiCad copy after the generated SES has
  been imported; and
- the exact `kicad-cli` used to run `pcb drc --format json` on each result.

The optional CopperMCP command-template boundary is a JSON argv array and supports only
`{source}`, `{output}`, and `{seed}`.  It runs against a private temporary source copy.  The
result must be persisted explicitly as `--copper-board` before DRC, which prevents an ephemeral
worker output from being silently substituted for evidence.

The report has a content-addressed `run_id`, source/provenance/DSN/JAR/result hashes, seed,
timeouts, Java/KiCad version probes, fixed process roles/timing/exit status, and whether source
bytes were preserved. It deliberately omits raw argv, paths, and child stdout/stderr. Untrusted
inputs have byte/count/schema ceilings; process output is captured with an 8 KiB ceiling and the
process group is killed on overflow or timeout. The ranking is intentionally lexicographic:

1. completion and zero KiCad-reported unconnected items;
2. zero KiCad hard violations;
3. via count, then routed segment length, then wall runtime.

Board-text via/length values are secondary descriptive data.  KiCad DRC is authoritative;
FreeRouting’s own score and CopperMCP’s internal checks are not substituted for it.

## Reproduction procedure

1. Create an Apache-2.0 or otherwise independently licensed fixture and record `origin`,
   `license_spdx`, and `derivation_statement` in a JSON provenance file.  Do not use private or
   third-party boards without an explicit reusable licence.
2. In KiCad, make a disposable copy and use **File → Export → Specctra DSN**.  Retain the source
   `.kicad_pcb`, project/rules, and DSN.  Do not hand-author a parallel DSN.
3. Run the harness with `--java`, `--freerouting-jar`, `--dsn`, `--source`, and provenance.  It
   emits a private `.ses` hash and FreeRouting process evidence.  Import that SES into another
   disposable copy in KiCad as documented above.
4. Produce CopperMCP’s competing disposable board using its supported candidate/replay path;
   preserve its source relation and supply it with `--copper-board`.  Supply the imported board
   with `--freerouting-board`, then provide `--kicad-cli` to DRC both.
5. Treat `unavailable_or_incomplete` as a real outcome.  It means a prerequisite, an imported
   result, or authoritative DRC evidence is absent; it is neither a routing failure nor a quality
   win for either tool.

Example (paths are illustrative):

```sh
PYTHONPATH=src python scripts/benchmark_freerouting_comparison.py \
  --source fixtures/independent-v1.kicad_pcb \
  --fixture-provenance fixtures/independent-v1.provenance.json \
  --dsn work/independent-v1.dsn --java /path/to/java \
  --freerouting-jar /path/to/freerouting-2.2.4.jar --kicad-cli /path/to/kicad-cli \
  --copper-board work/copper.kicad_pcb --freerouting-board work/freerouting-imported.kicad_pcb \
  --seed 23 --timeout-seconds 300 --output benchmarks/results/routing/freerouting/run.json
```

## Current local preflight

On 2026-08-05, the development host resolved `/usr/bin/java` but macOS reported no installed Java
runtime; `kicad-cli` was absent; and no released FreeRouting JAR was supplied.  No download was
performed, no GPL source was copied, no fixture was added, and no actual routing comparison ran.
The harness records this exact state as unavailable evidence rather than claiming comparison
closure.
