# Harness-owned KiCad Specctra transaction

Research date: 2026-08-05

## Finding

KiCad 10.0.5 exposes the documented Specctra DSN/SES workflow through both the PCB editor and
its bundled `pcbnew` Python binding.  The harness can therefore own this chain without parsing
or applying a session itself:

1. copy the bounded source board into a private temporary directory and hash both byte streams;
2. invoke KiCad-bundled Python with the fixed adapter to call `pcbnew.ExportSpecctraDSN`;
3. validate and hash the generated DSN, then run the GPL-3.0-only FreeRouting JAR out of process;
4. validate and hash the generated SES;
5. invoke the fixed adapter on a fresh private source copy to call `pcbnew.ImportSpecctraSES`,
   save a private KiCad board, and hash it; and
6. run the same bounded KiCad CLI DRC and metric extraction on that private board before the
   temporary directory is removed.

The repository's adapter is intentionally a tiny command dispatcher, not a DSN or SES parser.
It copies no FreeRouting source or GPL code, accepts no command template, and returns no board
contents.  The outer harness keeps fixed argv, a minimal child environment, file/input/output
limits, bounded capture, process-group termination, source-preservation hashing, and redacted
records.

## Official interface evidence

- KiCad 10 PCB Editor documentation says the workflow is **File → Export → Specctra DSN**, an
  external autorouter produces `.ses`, then **File → Import → Specctra Session**. It says session
  import applies routing to an existing board, requires matching footprints/nets/outline, and
  adds routing only: [KiCad PCB Editor, Specctra session import](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#specctra-session-import).
- The same version documents DSN as an export for third-party autorouters with no configurable
  exporter options: [KiCad PCB Editor, Specctra DSN](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#specctra-dsn).
- KiCad's generated API documents `ExportSpecctraDSN` in its `pcbnew` namespace:
  [KiCad Python API reference](https://docs.kicad.org/doxygen-python-7.0/namespacepcbnew.html).
- KiCad's source API describes `DSN::ImportSpecctraSession(BOARD*, filename)` as the helper that
  imports an SES into a board, then clears/rebuilds connectivity:
  [KiCad doxygen source](https://docs.kicad.org/doxygen/specctra__import_8cpp_source.html).
- FreeRouting's official command-line interface documents the DSN input and SES output arguments
  used by the isolated JAR invocation: [FreeRouting README](https://github.com/freerouting/freerouting#command-line-interface).

## Local verification

On this macOS host, the KiCad 10.0.5 bundled interpreter successfully executed the fixed
`export-dsn` adapter under the benchmark's minimal environment against the public two-pad
fixture. The resulting private DSN passed the harness shape and byte checks, with SHA-256
`b6f8e59230419ae7d887d5683194284a0bf83dd23cac4280a66983774fe02362` for that disposable run.
Separately, the fixed `import-ses` adapter imported the retained public-fixture FreeRouting v2.2.2
SES (`eb016ba1a7a4e680c472787dcf49b5f115c969d33603951b9b9fb3e94ba8fc4a`) into a fresh private
source copy and saved a routed board (`0589bc7dfc933edce9df7718bf41626aef6ea68de25548cfca86aa2768c393d0`)
containing one segment. Both disposable source copies retained SHA-256
`f2bb74d00f9195237ecebbf15d55c1e0175c38cc0ca65b3281edf64a3ee45c9c`.
These verify the actual KiCad export and import adapter legs, not routing quality or a closed run.

The host currently has no executable Java runtime on `PATH`; therefore the FreeRouting process
and the downstream SES import were not re-run in this research observation. The harness treats
that state as unavailable/incomplete evidence, never as a pass. An earlier separately recorded
v2.2.2 smoke observation remains historical and self-attested at its manual KiCad-import stage;
this new code does not rewrite that artifact.

## What this closes—and what it does not

With `--kicad-python`, a successful report can now mark the **FreeRouting side**
`harness_bound`: original source digest equals the private copy, the harness produced and hashed
the DSN, the isolated FreeRouting process produced and hashed the SES, the fixed KiCad import
adapter produced and hashed the board, and the harness derived its DRC/metric result from that
board. A caller-supplied DSN, imported board, or import receipt cannot substitute for this chain.

This is intentionally not a comparison closure. The competing CopperMCP command-template result
is still an external, self-attested runner contract; a successful FreeRouting transaction therefore
reports `comparison_closed: false` and
`incomplete_reason: copper_runner_self_attested_unverified` when all other evidence matches.
It also does not establish parity, throughput, broad-board routing, sandbox containment, electrical
behavior, fabrication readiness, or an advantage over FreeRouting. A two-pad fixture is useful only
as a transaction smoke test.
