# Source-to-board parity with `kicad-cli` 10.0.5

Research note for [#66](https://github.com/seunghyukchoe/copper-mcp/issues/66), written before the
implementation it justifies. It answers one question: **can CopperMCP obtain an authoritative
schematic-to-board connectivity verdict from KiCad, without reimplementing KiCad's semantics?**

The answer is yes, and the path is narrower and more dangerous than it looks. Three facts below
(§2, §3, §5) each independently turn a naive implementation into a silent false "pass".

Every claim here was either read out of KiCad's own source or executed against
`/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli` reporting `10.0.5`. Observed output is
labelled **measured**; source claims carry a URL.

**Reproduction, 2026-08-07.** Every **measured** claim in §1–§6 was re-executed independently
against the same binary before the implementation was accepted, outside the test suite, and every
one reproduced: the no-project run, the silently-degraded run, the exit-code-5-without-a-schematic
run, the `--severity-error` emptying, the delivered-schematic tables, and the §6 sum across all
four board fixtures. Every source URL was re-fetched and the quoted text located in it. One claim
did **not** survive that re-check and is corrected in §4 — the report's `schematic_parity` array is
filled from `m_fpWarningsProvider`, not from a "`MARKER_PARITY` provider", which is a marker type
and not a report provider at all. The correction narrows what this note claims rather than what the
implementation does, which already refuses an unreviewed finding type.

## 1. `pcb drc --schematic-parity` exists and needs no project

`kicad-cli pcb drc` in 10.0.5 accepts `--schematic-parity`, documented as
"Test for parity between PCB and schematic"
([CLI reference](https://docs.kicad.org/10.0/en/cli/cli.html)).

The prior slice ([#94](https://github.com/seunghyukchoe/copper-mcp/pull/94), ADR-0071) recorded an
assumption that this "requires a project, not standalone mode". **That assumption is wrong for the
CLI**, and the correction is the finding that unblocks this work.

`PCBNEW_JOBS_HANDLER::JobExportDrc` derives the schematic from the *board filename* by swapping the
extension in place — no project file is consulted, no search path, no project name:

> `schematicPath.SetExt( FILEEXT::KiCadSchematicFileExtension );`

falling back to the legacy `.sch` extension if the `.kicad_sch` does not exist
([`pcbnew_jobs_handler.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/pcbnew_jobs_handler.cpp),
extension constants in
[`wildcards_and_files_ext.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/common/wildcards_and_files_ext.cpp)).

The netlist itself is produced by an eeschema KIFACE entry point, `generateSchematicNetlist`
([`eeschema.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/eeschema/eeschema.cpp)), and the
project load beneath it is guarded:

> `if( wxFileExists( projectPath ) )`

([`eeschema_helpers.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/eeschema/eeschema_helpers.cpp)).
When the `.kicad_pro` is absent KiCad falls through to a default project. The project is optional at
every step.

**Measured.** A directory containing only `parity.kicad_pcb` and `parity.kicad_sch` — no
`.kicad_pro`, no `.kicad_prl`, no library tables — produces a populated `schematic_parity` array.

The GUI is the opposite: the pcbnew manual says of the parity option that it
"has no effect when running the PCB editor in standalone mode"
([pcbnew manual](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html); the sentence is carried in the
upstream source at
[`pcbnew_inspecting.adoc`](https://gitlab.com/kicad/services/kicad-doc/-/raw/master/src/pcbnew/pcbnew_inspecting.adoc)).
Reading the two together: the CLI deliberately routes around the standalone-GUI limitation by
loading the `.kicad_sch` directly through the eeschema KIFACE. That reading is ours, not a quotation.

## 2. A missing netlist degrades silently — the central hazard

When the schematic cannot be fetched, `JobExportDrc` reports at `RPT_SEVERITY_ERROR`, sets
`checkParity = false`, and **continues**. There is no early return. The run proceeds to
`RunTests(...)` with parity disabled, writes a well-formed report, and returns `SUCCESS`.

**Measured**, board with no sibling schematic:

```
stderr: Failed to fetch schematic netlist for parity tests.
        Schematic parity tests require a fully annotated schematic.
exit:   0
report: "schematic_parity": []
```

The `schematic_parity` key is emitted unconditionally — it is listed in the
`NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE` serialization macro for `DRC_REPORT`
([`rc_json_schema.h`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/include/rc_json_schema.h))
and is a `required` property of the report schema
([`drc.v1.json`](https://schemas.kicad.org/drc.v1.json)).

So: **`"schematic_parity": []` means either "parity passed" or "parity never ran", and nothing in
the exit code, the report body, or the schema distinguishes them.** The only signal is unstructured
English on stderr — which the bounded-subprocess design deliberately discards
(`stderr=DEVNULL`, [SEC-119](../ledgers/security-ledger.md)).

Any implementation that reads an empty array as a pass will report a board it never checked as
matching. §5 is how we avoid that without parsing stderr.

## 3. Exit codes cannot discriminate parity

`--exit-code-violations` returns `ERR_RC_VIOLATIONS = 5`
([`exit_codes.h`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/include/cli/exit_codes.h),
commented "Rules check violation count was greater than 0"). But the tail of `JobExportDrc` ORs
three providers into that one code — markers, ratsnest, and footprint warnings — so a parity
failure is indistinguishable from an ordinary clearance violation or an unconnected item.

**Measured.** A board with a deliberate net mismatch exits 5; a board with *no schematic at all*
(parity silently skipped) also exits 5, on unrelated DRC violations alone.

This mirrors the ERC exit-code-5 finding from #94: the exit code reports "findings exist", not
"the check you asked for failed". We therefore do not pass `--exit-code-violations`, and we parse
the report.

## 4. Which findings are parity findings

Two separate mechanisms are easy to conflate here, and only one of them decides the report.

`DRC_REPORT::WriteJsonReport` fills `schematic_parity` from **`m_fpWarningsProvider`** — one of
three providers it drains, alongside `m_markersProvider` into `violations` and `m_ratsnestProvider`
into `unconnected_items`
([`drc_report.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/drc/drc_report.cpp)).
The array's membership is therefore a property of that provider, not of any marker type.

Separately, `PCB_MARKER`'s constructor routes exactly seven DRCE codes to the `MARKER_PARITY`
*marker type*, which is a GUI classification
([`pcb_marker.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/pcb_marker.cpp)):
`DRCE_MISSING_FOOTPRINT`, `DRCE_DUPLICATE_FOOTPRINT`, `DRCE_EXTRA_FOOTPRINT`, `DRCE_NET_CONFLICT`,
`DRCE_SCHEMATIC_PARITY`, `DRCE_SCHEMATIC_FIELDS_PARITY`, `DRCE_FOOTPRINT_FILTERS`. Every other code
falls to the `default:` arm and becomes `MARKER_DRC`.

The two sets coincide in everything measured, and the table below is the seven marker-type codes'
settings keys — but the coincidence is *read off* the two files, not guaranteed by either, so this
note does not claim the report can only ever contain these seven. The implementation refuses an
unreviewed `type` instead of assuming the list is closed, which is why the distinction is safe to
record rather than resolve. The JSON `type` strings come from the settings keys in
[`drc_item.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/drc/drc_item.cpp):

| JSON `type` | Meaning |
|---|---|
| `missing_footprint` | schematic symbol has no board footprint |
| `extra_footprint` | board footprint has no schematic symbol |
| `duplicate_footprints` | two board footprints claim one symbol |
| `net_conflict` | pad net differs from, or is absent in, the schematic |
| `footprint_symbol_mismatch` | value, FPID, DNP, or exclude-from-BOM differs |
| `footprint_symbol_field_mismatch` | a named field differs |
| `footprint_filters_mismatch` | footprint outside the symbol's filters |

Two corrections to assumptions worth recording, both from `pcb_marker.cpp`:

* `footprint_type_mismatch`, `lib_footprint_mismatch`, and `lib_footprint_issues` sound like parity
  checks and are **not** — they route to `violations`.
* Pad/pin *count* mismatches have no dedicated key; a pad with no schematic pin and a schematic pin
  with no pad both emit `net_conflict`.

Item severity is only ever `error` or `warning` (the schema's `Severity` enum). **Measured:** every
parity finding produced by the fixtures below is `warning`. `--severity-error` therefore returns an
empty parity array even for a genuinely mismatched board — a fourth way to get a false pass. We
pass `--severity-all`.

**Measured:** `footprint_filters_mismatch` appears in the report's `ignored_checks` by default, so
only six of the seven codes can occur under compiled-in default severities.

## 5. CopperMCP's own schematics are board-excluded by construction

This is the fact that decides the design, and it is not about KiCad at all.

`render_kicad_schematic` emits `(on_board no)` on every symbol and library symbol, with an empty
`Footprint` property (`src/copper_mcp/adapters/kicad_schematic.py`). That is correct for what
ADR-0015 and ADR-0056 scoped: a *schematic-delivery* artifact for a two-pin passive subset with no
footprint assignments. ADR-0056's parity verifier even asserts the resulting `exclude_from_board`
property is present in the exported netlist.

The consequence is that the board-side netlist KiCad derives from a delivered CopperMCP schematic
is **empty** — `missing_footprint` and `extra_footprint` "both exclude board-only footprints", and
an `on_board no` symbol never enters the netlist at all.

**Measured**, delivered schematic (`on_board no`) against a board that implements it *correctly*:

| board | `schematic_parity` result |
|---|---|
| correct 2-component board | `extra_footprint` ×2 — every footprint reads as unmatched |
| board with a deliberate net error | `extra_footprint` ×2 — the real error is invisible |
| board with no footprints at all | `[]` — reads as clean |

A correct board is reported as entirely extraneous, a wrong board produces the *same* output, and an
empty board looks perfect. The delivered schematic cannot participate in a parity check.

**Measured**, same intent rendered with `(on_board yes)` and the `Footprint` property left empty
(i.e. board-eligibility alone, no footprint assignments added):

| board | `schematic_parity` result |
|---|---|
| correct 2-component board | `footprint_symbol_mismatch` ×2, `footprint_symbol_field_mismatch` ×2 |
| net mismatch on R1 pin 2 | the above, **plus `net_conflict`** |
| footprint deleted | `missing_footprint` ×1, `footprint_symbol_mismatch` ×1, … |
| extra footprint added | the correct-board set, **plus `extra_footprint`** |

Board-eligibility **alone** is sufficient. No footprint assignment has to be invented, and Circuit
Intent v0.1 has no footprint field to invent one from. The residual
`footprint_symbol_mismatch` / `footprint_symbol_field_mismatch` findings are the unavoidable
signature of a footprint-less intent: the symbol's `Footprint` and `Description` fields differ from
whatever the board's footprint carries. They say nothing about connectivity.

## 6. The liveness invariant

§2 leaves us needing a positive proof that KiCad actually loaded the netlist, without reading
stderr. §5 supplies one, for free, from the same report.

Under a board-eligible, footprint-less projection, **every** schematic component must produce
exactly one of two findings:

* it is absent from the board → `missing_footprint`
* it is present on the board → `footprint_symbol_mismatch`, because the symbol's empty `Footprint`
  field cannot equal the board footprint's library identifier

Therefore:

```
count(missing_footprint) + count(footprint_symbol_mismatch) == component_count
```

**Measured** across every fixture in §5: the sum is exactly 2 for a 2-component intent — for the
correct board, the mismatched board, the board missing a footprint, and the board with an extra
footprint alike. When the netlist is not fetched, or `--severity-error` suppresses the findings, or
the projection is accidentally the board-excluded delivered schematic, the sum is **0**.

This is an arithmetic cross-check in the same spirit as #94's ERC exit-code cross-check: the report
must predict its own preconditions, and a disagreement is refused rather than reconciled. It fails
closed — a board footprint with an empty library identifier would break the invariant and be
refused rather than passed.

## 7. What this justifies

An authoritative path exists, so `schematic_board_parity` need not stay a non-claim. It requires:

1. A **board-eligible parity projection** of the Circuit Intent — a second derivative alongside the
   delivered schematic, differing only in board-eligibility, with its own digest. The delivered
   schematic's bytes and digest are untouched, so ADR-0056/ADR-0071 evidence stays valid.
2. Co-naming the projection and the board on the same basename inside the private snapshot, which
   is the only mechanism KiCad offers (§1).
3. `--severity-all`, no `--exit-code-violations`, and report parsing (§3, §4).
4. The §6 liveness invariant as a hard gate before any verdict is reported.
5. Connectivity findings (`net_conflict`, `missing_footprint`, `extra_footprint`,
   `duplicate_footprints`) deciding the verdict; projection-artifact findings
   (`footprint_symbol_mismatch`, `footprint_symbol_field_mismatch`, `footprint_filters_mismatch`)
   disclosed as counts and explicitly not claimed as parity failures.

What it does **not** justify: a claim that the *delivered* schematic file matches the board (it is
board-excluded and cannot), any footprint-level or library-level correctness claim, or
`board_ready`. Those stay non-claims.

## 8. Not established

* Parity was executed only against KiCad 10.0.5. Flags and report schema are unchanged in 9.0 as
  far as the documentation shows, but 9.0 was not run.
* `schemas.kicad.org/drc.v1.json` redirects to KiCad's `master` copy, not a 10.0-pinned artifact.
* `master` carries a `schematicPath.MakeAbsolute()` call in the block quoted in §1 that the 10.0
  fetch did not show. It affects only relative board paths; our snapshot paths are absolute.
* The `"fully annotated schematic"` sentinel in `JobExportDrc` appears to be reachable only on the
  GUI mail path, since `generateSchematicNetlist` always overwrites its output. Unconfirmed; the
  §6 invariant covers this case regardless.

## References

- [KiCad CLI reference](https://docs.kicad.org/10.0/en/cli/cli.html)
- [pcbnew manual](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html)
- [`pcbnew_jobs_handler.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/pcbnew_jobs_handler.cpp)
- [`pcb_marker.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/pcb_marker.cpp)
- [`drc_item.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/drc/drc_item.cpp)
- [`drc_report.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/drc/drc_report.cpp)
- [`rc_json_schema.h`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/include/rc_json_schema.h)
- [`exit_codes.h`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/include/cli/exit_codes.h)
- [`eeschema.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/eeschema/eeschema.cpp)
- [`eeschema_helpers.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/eeschema/eeschema_helpers.cpp)
- [`wildcards_and_files_ext.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/common/wildcards_and_files_ext.cpp)
- [DRC report schema](https://schemas.kicad.org/drc.v1.json)
- [Authoritative schematic ERC research](kicad-schematic-erc-authority-v1.md)
