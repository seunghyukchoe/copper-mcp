# ADR-0160: Custom-rule loading needs a native witness

- Status: Proposed; focused native controls passed, integrated review pending
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0004](0004-authoritative-kicad-drc.md),
  [ADR-0109](0109-a-drc-count-carries-the-comparability-it-was-taken-with.md)

## Context

KiCad 10.0.5 can report ordinary DRC results after custom-rule initialization fails. Its loader
catches an initialization exception; the rule engine has already discarded the custom set and
rebuilt implicit defaults. A schema-valid report and a hash of the intended rule file do not prove
that the intended rules compiled. An owned fixture reproduced this with a dimensional constraint
missing its unit suffix. Explicit `0.5mm` restored the expected clearance violation; adding a
project file alone did not. Prior results remain historical, not retroactively corrected evidence.

## Decision

Keep the fixed CLI as the authority and preserve the public summary schema. When custom rules
are present, first use a separate private derivative to distinguish loading from fallback: preserve the
complete original rules, add one uniquely targeted assertion and one private board item, and
require an unexcluded error assertion finding for that item's exact UUID. Counts, descriptions,
exit status alone and default ignored-check counts cannot satisfy this witness.

The appended assertion declares error severity explicitly. Original project bytes and variables,
rule ordering and source content remain intact apart from the controlled board/rule additions; probe
insertion must not repair malformed original input. The final DRC summary is generated from the
untouched original capture, with its original severities, and contains none of the probe's findings.
It still summarizes one original-board invocation, not a sum or average of the two reports.
No synthetic project file or project severity rewrite is needed for the measured KiCad 10.0.5
profile. Literal empty/comment-only rule files receive a probe-only version statement; nonempty
files, including variable-expanded definitions and BOM-prefixed input, are never repaired.

Retain captured input privately on disk and release raw context dictionaries before native
execution. Preserve confinement, original-context identity, file/byte/output ceilings, cleanup,
fixed arguments and zero diagnostic disclosure. Share one absolute deadline across the probe
and final execution. No-rule contexts retain the single-pass path. Missing, malformed, excluded
or unrelated witnesses, private-tree drift and expired work refuse rather than use defaults.

This restores the existing authoritative-rule intent of D-005 and SEC-003; it does not create a
new engineering authority or weaken any gate. A handwritten subset parser and blanket rejection
of legitimate custom rules are not substitutes for native compilation.

## Evidence and remaining gates

The owned native experiment found the UUID-qualified assertion for a version-only file and a
valid clearance rule, and found none for missing units, an unknown constraint or an invalid
condition property. Native source confirms that custom rules are parsed into a temporary vector
before publication and cleared on failure. JSON serializes affected-item UUIDs but not rule names.

Fourteen focused native controls passed against KiCad 10.0.5 after the implementation; this is
not full integration or release acceptance. Full integration, independent review and protected
hosted/main validation remain required.

The witness is not a certificate that every expression compiled. KiCad ignores an assertion
compiler return in the inspected path. Native controls found an inactive invalid assertion
unreported even while the witness fired; tested active invalid assertions produced ordinary
assertion failures. Static rule-expression certification remains an open part of the full program.
This increment closes the measured whole-set fallback path, not that distinct compiler limitation.
No board saving, live mutation, physics, fabrication approval or readiness completion is granted.
