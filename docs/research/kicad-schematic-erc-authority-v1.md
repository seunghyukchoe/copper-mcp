# Authoritative KiCad schematic ERC v1

Date: 2026-08-06

## Question

[ADR-0015](../adr/0015-bounded-circuit-schematic-delivery.md) deferred authoritative ERC "until its
fixed-argument schematic subprocess and report contract receive a separate security review", and
[the earlier containment experiment](kicad-schematic-erc-containment.md) shipped nothing at all. Can
CopperMCP now run `kicad-cli sch erc` as the authoritative electrical-rule checker for a *generated*
schematic, bind its verdict to that schematic's digest, and say exactly what the verdict does and
does not mean — without reimplementing ERC and without repeating the containment claim that was
rejected?

## What the ERC command actually is

`kicad-cli sch erc` is documented identically in KiCad 9 and KiCad 10; the flag set did not change
between them, confirmed both in [the 9.0 CLI manual](https://docs.kicad.org/9.0/en/cli/cli.html) and
[the 10.0 CLI manual](https://docs.kicad.org/10.0/en/cli/cli.html), and in the argument registration
in
[`kicad/cli/command_sch_erc.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/master/kicad/cli/command_sch_erc.cpp).
The relevant flags are `--format json|report`, `--units mm|in|mils`, the combinable
`--severity-error` / `--severity-warning` / `--severity-exclusions` (with `--severity-all` meaning
all three), `--exit-code-violations`, `-o/--output`, and `-D/--define-var`.

CopperMCP uses a fixed vector: `sch erc --format json --units mm --severity-all
--exit-code-violations --output <private> <private>`. `--define-var` is never exposed, because it is
the one flag that would let a caller change what KiCad evaluates.

### Exit code 5 is not an error signal

This is the subtlety that most naive wrappers get wrong.
[`include/cli/exit_codes.h`](https://gitlab.com/kicad/code/kicad/-/raw/master/include/cli/exit_codes.h)
defines `5 = ERR_RC_VIOLATIONS` ("Rules check violation count was greater than 0"), and
[`eeschema/eeschema_jobs_handler.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/master/eeschema/eeschema_jobs_handler.cpp)
returns it on `markersProvider->GetCount() > 0` with **no error-versus-warning discrimination**. The
severity mask decides what is counted, and
[`common/jobs/job_rc.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/master/common/jobs/job_rc.cpp)
defaults that mask to `ERROR | WARNING`.

So exit 5 means "findings exist", not "the schematic failed". A wrapper that maps exit 5 to failure
reports a warning-only schematic as broken; a wrapper that ignores the exit code entirely cannot
detect a truncated report. CopperMCP instead *cross-checks* the two: the report's own finding count
must predict the exit code (`5` if any violation is present, `0` otherwise), and a run whose exit
code and report disagree is refused rather than reconciled. Neither artifact is trusted alone.

The remaining exit codes are `1 ERR_ARGS`, `2 ERR_UNKNOWN`, `3 ERR_INVALID_INPUT_FILE`,
`4 ERR_INVALID_OUTPUT_CONFLICT`, `6 ERR_JOBS_RUN_FAILED`, `7 ERR_UNKNOWN_FILE_FORMAT`. Only `0` and
`5` are accepted.

## The report schema

`https://schemas.kicad.org/erc.v1.json` resolves (via redirect) to
[`resources/schemas/erc.v1.json`](https://gitlab.com/kicad/code/kicad/-/raw/master/resources/schemas/erc.v1.json),
and the [9.0 copy](https://gitlab.com/kicad/code/kicad/-/raw/9.0/resources/schemas/erc.v1.json) has
the same shape, so one parser covers both releases.
[`eeschema/erc/erc_report.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/eeschema/erc/erc_report.cpp)
writes the top-level keys.

The ERC report differs structurally from the DRC report CopperMCP already consumes:

| | `erc.v1.json` | `drc.v1.json` |
|---|---|---|
| Findings | nested, `sheets[] → violations[]` | flat `violations`, `unconnected_items`, `schematic_parity` |
| Sheet identity | `path` + `uuid_path` per sheet | none; a board is flat |
| `Violation.comment` | absent from the schema | present |
| `coordinate_units` | **not** in `required` | in `required` |

Two consequences for the adapter. First, ERC counts must be summed across sheets rather than read
from one array, so the parser walks sheets and rejects a duplicate sheet path. Second, the observed
KiCad 10.0.5 report *does* emit `coordinate_units`, and CopperMCP requires it to equal `mm` even
though the schema makes it optional — a deliberately stricter gate, since a report that omits the
units it was asked for is not the report we requested.

`Violation.excluded` is a boolean with `default: false`, and
[`common/rc_item.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/common/rc_item.cpp) emits it
unconditionally, so the parser treats a missing `excluded` as `false` rather than as malformed. That
same serializer can attach a `comment` to an excluded violation, which the published ERC schema —
`additionalProperties: false`, no `comment` property — would reject; CopperMCP is lenient about
unknown keys inside a violation and strict about the keys it reads, so this upstream inconsistency
cannot break a run.

## What ERC violations exist, and what "default severity" means

The JSON `type` strings are the ERC settings keys enumerated in
[`eeschema/erc/erc_item.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/master/eeschema/erc/erc_item.cpp)
— `pin_not_connected`, `pin_not_driven`, `power_pin_not_driven`, `duplicate_reference`,
`hier_label_mismatch`, `label_dangling`, `bus_definition_conflict`, `net_not_bus_member`,
`unresolved_variable`, `lib_symbol_issues`, `isolated_pin_label`, `similar_labels`,
`endpoint_off_grid`, `four_way_junction`, `single_global_label`, `simulation_model_issue`,
`footprint_filter`, and roughly forty more.

[`eeschema/erc/erc_settings.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/eeschema/erc/erc_settings.cpp)
assigns severities by the rule "everything is an ERROR unless listed otherwise". Notably **warning**
by default: `lib_symbol_issues`, `isolated_pin_label`, `endpoint_off_grid`, `similar_labels`,
`no_connect_dangling`, `missing_input_pin`. Notably **ignored** by default: `single_global_label`,
`four_way_junction`, `simulation_model_issue`, `footprint_filter`.

Those four ignored checks are exactly the `ignored_checks` array in the observed report, which is why
CopperMCP counts them and refuses to call a report with ignored checks "clean" even when it has zero
findings.

### Severities come from the project, and we deliberately have none

ERC severities are per-project, editable under Schematic Setup → Electrical Rules → Violation
Severity and stored in the `.kicad_pro`. The private snapshot CopperMCP checks contains **only** the
generated `.kicad_sch` and no project file, so KiCad evaluates it against its compiled-in default
severity map.

This cuts both ways and both directions matter. It means no user project setting can weaken
CopperMCP's verdict, which is a real determinism property. It equally means the verdict is *not*
necessarily what the same schematic would produce inside the user's own project, which is an
explicit non-claim.

## Why the fixture reports warnings, and why they stay

The bounded passive fixture reports four warnings on KiCad 10.0.5: two `lib_symbol_issues` and two
`isolated_pin_label`. The `lib_symbol_issues` text — "The current configuration does not include the
symbol library 'CopperMCP'" — comes from `TestLibSymbolIssues()` in
[`eeschema/erc/erc.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/eeschema/erc/erc.cpp), raised
when the symbol library has no row in the effective symbol-library table. The generated schematic
embeds its symbols but names a `CopperMCP` library that no table declares, so the warning is the
expected, honest consequence of checking a self-contained generated file in isolation. (That
library-resolution path is fragile in practice: KiCad issue
[#22693](https://gitlab.com/kicad/code/kicad/-/work_items/22693) reports `kicad-cli` resolving
library paths relative to a symlink and producing exactly this warning family.)

ADR-0015 already anticipated this: "The generated passive fixture also produces reviewed KiCad
warnings that must not be relabeled as clean evidence." The adapter therefore reports two separate
signals — `passed` (no error-severity violation) and `clean` (no findings and no ignored checks at
all) — mirroring the split `DrcSummary` already uses. The fixture is `passed: true, clean: false`,
and a test pins that exact pair so a future change cannot quietly promote it.

## ERC is not board parity, and a netlist is not a rule check

The issue asks for source-to-board parity alongside ERC, so the boundary needs to be stated
precisely with sources rather than asserted.

KiCad itself says ERC is incomplete by design: the
[Eeschema manual](https://docs.kicad.org/10.0/en/eeschema/eeschema.html) states that ERC does not
detect all errors and that all detected issues should still be checked by a human.

More decisively, KiCad models schematic-to-board parity as a **board-side** result category, not a
schematic-side one. The [Pcbnew manual](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html) documents
a separate DRC option, "Test for parity between PCB and schematic", notes it has no effect in
standalone mode, and reports its results in their own tab alongside rule violations and unconnected
items. That is the same three-way split visible in `drc.v1.json`'s `violations`,
`unconnected_items`, and `schematic_parity` arrays — and `erc.v1.json` has **no** corresponding
array. There is structurally nowhere in an ERC report for a parity finding to live.

Sync is also explicitly manual:
[Getting Started](https://docs.kicad.org/10.0/en/getting_started_in_kicad/getting_started_in_kicad.html)
states that updating the PCB from the schematic is a decision the designer makes with the Update PCB
from Schematic tool.

Finally, `sch export netlist` performs no rule evaluation at all — it is a separate subcommand whose
only options are the output path and a format. So netlist equivalence and ERC are answering
different questions: the netlist proves *what KiCad read back*, ERC proves *what KiCad thinks of
it*. CopperMCP therefore reports them as two independent stages, and reports schematic-to-board
parity as `not_run` rather than inferring it from either.

## Decision

Ship `kicad-cli sch erc` as the authoritative electrical-rule checker for generated schematics, plus
`kicad-cli sch export netlist --format kicadxml` to feed the existing pure parity verifier from
[ADR-0056](../adr/0056-kicad-schematic-parity.md), whose docstring already reserved this seam: "The
caller owns the fixed-argument, private-environment CLI invocation that produced the XML."

The containment blockers from [the earlier experiment](kicad-schematic-erc-containment.md) are not
re-litigated because the claim that triggered them is not made. That experiment tried to assert
kernel-enforced containment of *private user input* via `sandbox-exec`, and failed on aggregate write
bounds and a broad runtime read rule. This surface asserts no containment property beyond what the
already-shipped board DRC adapter asserts, and it has strictly less exposure: the subprocess input is
CopperMCP's own deterministic render of Circuit Intent the caller just submitted, so there is no
workspace snapshot, no library-table discovery, and no user file the model did not already provide.
See [SEC-119](../ledgers/security-ledger.md) for the review that records this.

## What this does not establish

- Not evidence of schematic-to-board parity, electrical correctness, simulation, manufacturability,
  or board readiness.
- Not a claim that the verdict matches what the user's own project would report, since severities
  are project-scoped and the checked snapshot has no project file.
- Not a containment or privacy claim about the KiCad subprocess.
- Not a claim about ERC on hierarchical sheets, buses, power symbols, or any symbol outside the
  bounded two-pin passive subset. The parser accepts multiple sheets, but nothing generates one.
- Verified against KiCad 10.0.5 only. The schema is unchanged in 9.0, but no 9.x run was executed.

## Unverified

No official KiCad statement was found about whether `kicad-cli sch erc` requires a display; only
`api-server` is documented as headless. Historical issues such as
[#10075](https://gitlab.com/kicad/code/kicad/-/issues/10075) show X requirements in related tooling.
On the macOS host used here it runs with no display configuration, and CI treats the KiCad-dependent
tests as skip-typed rather than assuming availability.
