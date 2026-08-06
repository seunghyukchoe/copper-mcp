# ADR-0076: Authoritative source-to-board parity via a board-eligible intent projection

- Status: Accepted
- Date: 2026-08-06
- Owners: CopperMCP maintainers
- Related: [Issue #66](https://github.com/seunghyukchoe/copper-mcp/issues/66),
  [ADR-0004](0004-authoritative-kicad-drc.md), [ADR-0015](0015-bounded-circuit-schematic-delivery.md),
  [ADR-0056](0056-kicad-schematic-parity.md), [ADR-0071](0071-authoritative-schematic-erc.md),
  [research note](../research/source-to-board-parity-v1.md),
  [SEC-121](../ledgers/security-ledger.md), [D-154](../ledgers/decision-ledger.md),
  [R-117](../ledgers/risk-register.md)

## Context

ADR-0071 closed the schematic half of issue #66 and left `schematic_board_parity` an explicit
non-claim, recording that a board-side verdict "requires a project, not standalone mode, which is a
design constraint worth resolving before implementation".

**That constraint does not exist.** `PCBNEW_JOBS_HANDLER::JobExportDrc` derives the schematic from
the board filename by swapping the extension in place, and the project load beneath it is guarded by
`wxFileExists`. A directory holding only `parity.kicad_pcb` and `parity.kicad_sch` — no
`.kicad_pro`, no library tables — produces a populated `schematic_parity` array. The GUI's parity
checkbox is the thing that "has no effect ... in standalone mode"; the CLI routes around that by
loading the schematic through the eeschema KIFACE directly. The
[research note](../research/source-to-board-parity-v1.md) establishes this at source level.

Removing that blocker exposes three sharper ones, each of which turns a naive wrapper into a silent
false "pass":

1. **A missing netlist degrades silently.** When the schematic cannot be fetched, KiCad reports at
   `RPT_SEVERITY_ERROR`, sets `checkParity = false`, and continues — no early return. The run exits
   `0` and writes `"schematic_parity": []`. The key is emitted unconditionally and is `required` by
   the report schema, so an empty array means "passed" **or** "never ran", with nothing in the exit
   code, report body, or schema to tell them apart. The only signal is English on stderr, which our
   containment discards by design.
2. **Exit codes cannot discriminate.** `--exit-code-violations` ORs markers, ratsnest, and footprint
   warnings into one code `5`. A board with no schematic at all still exits `5` on unrelated
   violations. This is ADR-0071's exit-code-5 lesson repeating: the code reports "findings exist".
3. **Parity findings are all `warning` severity**, so `--severity-error` returns an empty parity
   array for a genuinely mismatched board.

The decisive constraint is not KiCad's, though. It is ours: `render_kicad_schematic` emits
`(on_board no)` on every symbol, which is correct for the schematic-*delivery* artifact ADR-0015 and
ADR-0056 scoped, and which makes the board-side netlist empty. Measured against a board that
implements the intent correctly, the delivered schematic yields `extra_footprint` for every
footprint; against a deliberately wrong board it yields exactly the same thing; against an empty
board it yields `[]`. The delivered schematic cannot participate in a parity check at all.

## Decision

CopperMCP treats fixed-argument `kicad-cli pcb drc --schematic-parity` as the authoritative
source-to-board connectivity oracle, and derives a **board-eligible parity projection** of the
Circuit Intent to give it something to check.

**The projection is a second derivative of the same immutable intent, not a substitute for the
delivered schematic.** `render_kicad_schematic` gains a `board_eligible` parameter defaulting to
`False`; the projection is the same renderer with `(on_board yes)`. Connectivity, references,
values, and topology are byte-for-byte identical in origin — only board-eligibility differs. The
delivered artifact's bytes and digest are untouched, so every ADR-0056 and ADR-0071 digest, golden
identity, and round-trip claim remains valid. Evidence carries **both** digests, and the claim is
scoped accordingly: parity is asserted between *the board* and *the Circuit Intent's connectivity*,
never between the board and the delivered schematic file, which is board-excluded and could not
support such a claim.

No footprint assignment is invented. Circuit Intent v0.1 has no footprint field, and measurement
shows board-eligibility alone is sufficient for `net_conflict`, `missing_footprint`,
`extra_footprint`, and `duplicate_footprints` to fire correctly.

**A liveness invariant gates every verdict.** Under a board-eligible, footprint-less projection each
schematic component must produce exactly one of two findings: `missing_footprint` if it is absent
from the board, or `footprint_symbol_mismatch` if it is present, because the symbol's empty
`Footprint` field cannot equal the board footprint's library identifier. Therefore

```
count(missing_footprint) + count(footprint_symbol_mismatch) == component_count
```

must hold, and it is measured to hold for the correct board, the mismatched board, the board with a
deleted footprint, and the board with an added footprint alike. It is `0` when the netlist was not
fetched, when severities suppressed the findings, or when the projection was accidentally the
board-excluded artifact. A disagreement is a typed refusal, never a reconciliation — the same
discipline ADR-0071 applied to the ERC exit code. It fails closed.

**Findings are split by what they can support.** `net_conflict`, `missing_footprint`,
`extra_footprint`, and `duplicate_footprints` decide the verdict.
`footprint_symbol_mismatch`, `footprint_symbol_field_mismatch`, and `footprint_filters_mismatch` are
the unavoidable signature of a footprint-less intent — the symbol's `Footprint` and `Description`
fields differ from whatever the board carries — and are disclosed as counts while explicitly not
being claimed as parity failures.

**Containment mirrors ADR-0004 and ADR-0071 exactly.** The board arrives as a workspace path, is
read through the bounded workspace reader, and is copied with the projection into a private
read-only snapshot under a fixed basename. The argument vector is fixed: no `--define-var`, no
caller-supplied flags, no `--exit-code-violations`, no `--save-board` or `--refill-zones`. The child
runs under the `RLIMIT_FSIZE` wrapper with private `HOME`/`TMPDIR`/config, `stdout`/`stderr`
discarded, and the snapshot tree is revalidated afterwards so a side effect or mutated input is
refused rather than reported. Reviewed as [SEC-121](../ledgers/security-ledger.md).

**Redaction is unchanged.** Parity finding descriptions embed net names verbatim — measured:
`"Pad net (GND) doesn't match net given by schematic (AUDIO_OUT)"` — and affected items carry UUIDs
and coordinates. Only per-type counts, digests, the board revision, and fixed literals cross the
boundary. No description, name, reference, UUID, or coordinate is ever returned.

Evidence binds to the intent digest, the delivered schematic digest, the projection digest, and the
board revision together. A result that is not bound to all four is evidence about nothing.

## Consequences

`schematic_board_parity` becomes a real, test-bound claim for the bounded passive subset, and issue
#66's remaining leg closes. A genuinely mismatched board is detected — a control fixture proves it —
and four distinct silent-false-pass modes are refused rather than reported.

The cost is a second rendered artifact per verification and a second concept for callers to hold:
the schematic CopperMCP *delivers* and the projection it *checks against* are not the same bytes.
Evidence reports both digests rather than hiding the distinction.

This does not claim that the delivered schematic file matches the board, that footprints are
correct, that library identities or footprint assignments are meaningful, or that the board is
manufacturable. `electrical_validation` and `board_ready` remain non-claims. Hierarchical sheets,
buses, power symbols, and anything beyond the two-pin passive subset remain out of scope, as does
KiCad 9 verification — only 10.0.5 was executed.

## References

- [Source-to-board parity research](../research/source-to-board-parity-v1.md)
- [KiCad CLI reference](https://docs.kicad.org/10.0/en/cli/cli.html)
- [`pcbnew_jobs_handler.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/pcbnew_jobs_handler.cpp)
- [`pcb_marker.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/pcb_marker.cpp)
- [`drc_item.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/drc/drc_item.cpp)
- [DRC report schema](https://schemas.kicad.org/drc.v1.json)
