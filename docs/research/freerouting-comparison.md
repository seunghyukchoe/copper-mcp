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

## Closure receipts and command environment

DRC-clean boards alone are deliberately insufficient. The harness records both content-addressed
receipts alongside both DRC reports:

- `copper-mcp/freerouting-ses-import-receipt/v1` binds the source hash, the freshly produced,
  nonempty bounded SES hash, the imported result-board hash, and workflow value
  `kicad-specctra-ses-import`.
- `copper-mcp/candidate-runner-receipt/v1` binds the source hash, an output emitted by the
  successful optional CopperMCP runner, the evaluated board hash, and workflow value
  `coppermcp-candidate-runner`.

The receipt is an explicit, self-attested provenance assertion, not a signature or a substitute
for review. Matching hashes show that named bytes agree; they do not prove that KiCad imported the
SES into the supplied board or that an arbitrary command template was CopperMCP's candidate
runner. Therefore this harness deliberately never sets `comparison_closed` or `status=completed`.
When all receipt bindings and DRC reports otherwise match, it returns
`status=unavailable_or_incomplete` with `incomplete_reason=self_attested_unverified`.

A future closure gate must itself execute a constrained KiCad SES-import transaction in a private
workspace and a constrained CopperMCP runner contract, then derive both result hashes from those
transactions. Until those two controls exist, receipts remain useful diagnostic bindings but not
comparison-completion evidence. Missing, malformed, mismatched, absent/invalid SES, or failed
processes likewise remain `unavailable_or_incomplete` with `incomplete_reason=incomplete_evidence`.

All Java/JAR/KiCad/CopperMCP commands receive only `HOME`, `TMPDIR`, `PATH`, `LANG`, and `LC_ALL`;
provider tokens and general inherited environment variables are not passed through. A supplied
executable remains user-authorized code execution: process isolation here bounds lifecycle,
output, and resource effects, but is not sandbox containment for malicious code.

### Harness-owned transaction containment status

The optional `--kicad-python` route is deliberately disabled unless an internal, reviewed platform
provider creates an exact private workspace with a sufficient aggregate quota. The harness then
canonicalizes that root and rejects it unless it is a non-symlink directory owned by the current
user with no group/other permissions; each transaction directory, process `cwd`, `HOME`, and
`TMPDIR` is created under that provider root. Source DRC, when run by this route, uses a private
source copy in the same provider boundary. The CLI does not accept a workspace argument.

No such provider is enabled today. On refusal, the harness fails before **all** child-launch
seams: Java/KiCad version probes, source/result DRC, DSN export, FreeRouting, SES import, and the
optional CopperMCP runner. This protects the meaning of a failed preflight; it does not demonstrate
that KiCad or Java are sandboxed. The macOS `sandbox-exec` experiments remain insufficient because
KiCad export required a broad host-read rule; Linux tmpfs/mount-namespace/cgroup providers are not
implemented. Consequently this repository makes no sandbox, FreeRouting parity, performance, or
comparison-closure claim from the capability seam or its unit tests.

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
   with `--freerouting-board`; create the matching receipt JSON files, then provide `--kicad-cli`
   to DRC both. The resulting receipt bindings are self-attested and cannot close this version of
   the harness.
5. Treat `unavailable_or_incomplete` as a real outcome. `self_attested_unverified` means that
   otherwise-matching user-supplied evidence awaits harness-owned import/runner transactions;
   `incomplete_evidence` means a prerequisite, binding, or authoritative DRC record is absent.
   Neither outcome is a routing failure nor a quality win for either tool.

Example (paths are illustrative):

```sh
PYTHONPATH=src python scripts/benchmark_freerouting_comparison.py \
  --source fixtures/independent-v1.kicad_pcb \
  --fixture-provenance fixtures/independent-v1.provenance.json \
  --dsn work/independent-v1.dsn --java /path/to/java \
  --freerouting-jar /path/to/freerouting-2.2.4.jar --kicad-cli /path/to/kicad-cli \
  --copper-board work/copper.kicad_pcb --freerouting-board work/freerouting-imported.kicad_pcb \
  --copper-receipt work/copper-receipt.json \
  --freerouting-import-receipt work/freerouting-import-receipt.json \
  --seed 23 --timeout-seconds 300 --output benchmarks/results/routing/freerouting/run.json
```

## Current local preflight

On 2026-08-05, the development host resolved `/usr/bin/java` but macOS reported no installed Java
runtime; `kicad-cli` was absent; and no released FreeRouting JAR was supplied.  No download was
performed, no GPL source was copied, no fixture was added, and no actual routing comparison ran.
The harness records this exact state as unavailable evidence rather than claiming comparison
closure.
