# ADR-0070: Authoritative KiCad schematic ERC and generated-schematic round trip

- Status: Accepted
- Date: 2026-08-06
- Owners: CopperMCP maintainers
- Related: [Issue #66](https://github.com/seunghyukchoe/copper-mcp/issues/66),
  [ADR-0004](0004-authoritative-kicad-drc.md), [ADR-0015](0015-bounded-circuit-schematic-delivery.md),
  [ADR-0056](0056-kicad-schematic-parity.md),
  [research note](../research/kicad-schematic-erc-authority-v1.md),
  [SEC-119](../ledgers/security-ledger.md)

## Context

CopperMCP renders a deterministic KiCad schematic from a bounded Circuit Intent subset, but nothing
had ever asked KiCad what it thought of the result. ADR-0015 reported `erc`, `kicad_cli_parse`, and
`schematic_board_parity` as `not_run` and deferred authoritative ERC "until its fixed-argument
schematic subprocess and report contract receive a separate security review". ADR-0056 then built a
pure parity verifier that compares a KiCad-exported netlist against the source intent, but left the
CLI invocation that produces that netlist to a caller who did not exist — its own docstring says
"The caller owns the fixed-argument, private-environment CLI invocation that produced the XML."

An [earlier ERC experiment](../research/kicad-schematic-erc-containment.md) shipped nothing. It
failed a security review because it claimed kernel-enforced containment of private user input via
macOS `sandbox-exec`, and that claim did not survive: `RLIMIT_FSIZE` bounds each file rather than
total writes, and KiCad needed a broad runtime read rule that could disclose workspace data through
the report channel. Its recorded conclusion was that any future path needs "a new architecture
decision, pinned-tool evidence, and independent security review".

Two properties of ERC make a naive wrapper actively misleading. Exit code 5 means "findings exist",
not "check failed" — it fires on warnings, with no error-versus-warning discrimination. And ERC
severities are per-project, so a verdict is only meaningful alongside the settings that produced it.

## Decision

CopperMCP treats fixed-argument `kicad-cli sch erc` as the authoritative electrical-rule checker for
schematics it generated itself, and fixed-argument `kicad-cli sch export netlist --format kicadxml`
as the read-back half of a round trip. CopperMCP never decides what an electrical rule violation is;
it transports KiCad's verdict and owns only what is claimed about it.

**The subject is always CopperMCP's own render, never a workspace file.** The service accepts Circuit
Intent, renders it twice and requires byte-identical artifacts, then hands those exact bytes to
KiCad. This is what lets the path skip the library-table discovery and project snapshotting that
ADR-0004's board adapter requires: the subprocess receives no user design data that did not arrive
through the tool argument. There is no `--define-var`, no caller-supplied flag, and no path input.

The schematic is written into a private read-only temporary directory, executed through the same
`RLIMIT_FSIZE` child wrapper and private `HOME`/config environment as board DRC, bounded by the
existing report-byte and timeout budgets, and the tree is re-validated afterwards so an unexpected
side effect or a mutated input is refused rather than reported. Both `sch` commands share one helper,
so the two subprocess surfaces cannot drift apart.

**Report acceptance is cross-checked, not trusted.** Only the reviewed `erc.v1.json` shape is
accepted, with bounded JSON depth, value count, and duplicate-key rejection reused from the DRC path.
The report's own finding count must predict the observed exit code — `5` when any violation is
present, `0` otherwise — and a run whose report and exit code disagree is refused rather than
reconciled. `coordinate_units` must equal `mm` even though the published schema marks it optional,
because a report that omits the units it was asked for is not the report we requested. Only exit
codes `0` and `5` are accepted.

**Two signals, never one.** `passed` means KiCad reported no error-severity violation. `clean` is
true only when the report carries no findings and no ignored checks at all. The bounded passive
fixture is `passed: true, clean: false` on KiCad 10.0.5 — four warnings, four ignored checks — and a
test pins that exact pair, satisfying ADR-0015's requirement that the fixture's reviewed warnings
"must not be relabeled as clean evidence".

Every result is bound to both the Circuit Intent digest and the schematic artifact digest, and the
result type refuses construction when either binding fails. The public response carries digests,
counts, KiCad's violation-type keys, and fixed literals only — no schematic bytes, net or component
names, values, coordinates, UUIDs, or KiCad description text. The exported netlist's digest is
deliberately omitted, because KiCad stamps the export with a wall-clock date and the private snapshot
path, so it would look like a stable identity while behaving like a nonce.

The new `verify_circuit_schematic_erc` MCP tool and `copper-mcp schematic-erc` CLI command expose
this over both transports. Unlike `render_circuit_schematic`, they are not stdio-only, because they
return no bytes and issue no artifact capability.

`kicad_cli_parse` is upgraded from `not_run` to `passed`, because KiCad cannot run ERC on a schematic
it failed to load. `schematic_board_parity`, `electrical_validation`, and `board_ready` remain
single-value non-claim literals.

## Consequences

- Generated schematics now carry a real electrical-rule verdict and a real KiCad round trip, closing
  the two ADR-0015 deferrals that could be closed and giving ADR-0056's verifier its missing caller.
- KiCad becomes an optional runtime dependency for schematic verification as well as board DRC.
  KiCad-dependent tests are skip-typed, and the CLI's absence is a typed refusal, never a verdict.
- The checked snapshot has no `.kicad_pro`, so KiCad evaluates it against compiled-in default
  severities. No user project setting can weaken the verdict — and equally, the verdict is not
  necessarily what the same schematic would report inside the user's own project. That is a
  non-claim, not a gap to be closed by silently adopting a project file.
- The fixture's `lib_symbol_issues` warnings are expected and permanent for a self-contained
  generated file: the schematic names a `CopperMCP` symbol library that no library table declares.
  Suppressing them would require either shipping a library table into the snapshot or overriding
  severities, both of which would weaken an authoritative check to make output prettier.
- New KiCad ERC schema versions fail closed until reviewed, matching the DRC adapter.
- ERC success proves nothing about schematic-to-board parity, signal integrity, simulation,
  manufacturability, or hardware safety. KiCad models board parity as a board-side DRC result with
  no representation in an ERC report at all.
- Source-to-board parity remains genuinely unimplemented. It needs a board to compare against and a
  contract binding a schematic digest to a board digest; that is follow-up work on issue #66.

## Alternatives considered

- **Reimplement ERC checks in Python**: rejected outright. It contradicts the charter's authoritative
  -tools rule, and a second opinion about electrical rules is worse than no opinion.
- **Run ERC on arbitrary workspace schematics**: rejected for this slice. It reintroduces exactly the
  user-data exposure the containment experiment failed to bound, and needs library-table discovery
  and project snapshotting that the generated-render path does not. A workspace-facing ERC surface
  can be added later against the same adapter, with its own review.
- **Retry the `sandbox-exec` containment boundary**: rejected. The prior review found it unsound, the
  helper is deprecated on the host OS, and the correct response is to not make the claim rather than
  to weaken the profile until it passes.
- **Map exit code 5 to failure**: rejected as factually wrong. Exit 5 fires on warnings, so this
  would report the passing fixture as broken.
- **Ignore the exit code and trust the report**: rejected. The cross-check is what detects a
  truncated or substituted report; neither artifact is trustworthy alone.
- **Report a single `erc: passed` boolean**: rejected. It would either hide the fixture's real
  warnings or fail a schematic KiCad considers acceptable. The `passed`/`clean` split is the honest
  encoding, and it matches `DrcSummary`.
- **Include the exported netlist digest in the response**: rejected as a false identity, since the
  export embeds a timestamp and the private path and is not reproducible.
- **Fold ERC into `render_circuit_schematic`**: rejected. Rendering must stay usable without a KiCad
  install, and a render is not the place to spend a subprocess budget the caller did not ask for.

## References

- [KiCad 10 CLI: schematic ERC](https://docs.kicad.org/10.0/en/cli/cli.html)
- [KiCad 9 CLI: schematic ERC](https://docs.kicad.org/9.0/en/cli/cli.html)
- [KiCad ERC report schema](https://gitlab.com/kicad/code/kicad/-/raw/master/resources/schemas/erc.v1.json)
- [KiCad CLI exit codes](https://gitlab.com/kicad/code/kicad/-/raw/master/include/cli/exit_codes.h)
- [KiCad ERC default severities](https://gitlab.com/kicad/code/kicad/-/raw/10.0/eeschema/erc/erc_settings.cpp)
- [Pcbnew: schematic parity DRC](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html)
