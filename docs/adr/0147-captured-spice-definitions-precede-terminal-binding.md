# ADR-0147: Captured SPICE definitions precede terminal binding

- Status: Proposed; bounded reader independently reviewed, publication pending
- Date: 2026-09-07
- Owners: CopperMCP maintainers
- Related: [ADR-0145](0145-bom-agreement-binds-content-and-native-components.md),
  [ADR-0146](0146-placed-pin-census-precedes-model-interface-binding.md)

## Decision

Interpret actual captured model-library bytes before associating model terminals with the complete
placed-pin census. Keep `electrical-inputs/v1` and its `model_digest` meaning unchanged. A library
reader is not native validation, calibration, a complete model binding, or an engineering authority.
The complete path still requires checked BOM associations, explicit per-pin terminal or NC choices,
fixed native export, sealed simulator execution, freshness checks and physical validity evidence.

Begin with a deliberately bounded `spice-passive-diode/v1` language: ASCII flat libraries containing
diode `.model` definitions and non-nested `.subckt` definitions composed of constant R/C/L elements,
diodes and calls to other definitions in the same library. Preserve subcircuit terminal order and
derive the fixed diode terminal order from the native device convention. Validate every definition,
not only the selected one. Reject case-insensitive name collisions, duplicate terminals/elements,
unresolved or wrong-kind dependencies, call-arity mismatch and recursive subcircuit expansion.
Bound total expanded element work as well as source size so a small acyclic library cannot describe
an unbounded expansion. Native acceptance of more syntax does not broaden this published profile.

No external includes, library sections, nested definitions, expressions, parameters, behavioral or
code models, arbitrary directives, startup commands, file paths or execution are accepted. Support
ordinary full-line comments and plus continuations within fixed byte/line/token/work ceilings.
Reject other comment/continuation forms explicitly in this first profile; do not silently strip
syntax that could have a different meaning under another compatibility mode. A named `.ends`
must match its opener under this stricter profile, although ngspice itself does not check that name.

Keep the original content and ordered definition records immutable and repr-redacted. Parse and
validate dependencies under one finite caller deadline, including result construction. Fixed errors
must not disclose model names, node names or source snippets. No process-global interpreter settings
are changed and no new parser dependency or generic authority registry is introduced.

## Validation and next boundary

Use owned synthetic valid libraries plus malformed, ambiguous, stale-content, bounds and deadline
controls. Preserve all supported interpreters. Compare actual native export and asymmetric diode
behavior in subsequent controls; a resistor terminal swap is not a bad-physics reference. Before
running arbitrary captured libraries, bind a confined executor's complete startup and dependencies:
ngspice's user-startup suppression does not by itself seal its standard startup file.

Definition reading, complete project pin/model binding and real confined native execution are
mandatory parts of this engineering increment, not substitutes for one another. None earns
calibrated SI/PI, ratings, thermal, EMC, human approval or application capability credit by itself.

Sources: [ngspice 46 manual](https://ngspice.sourceforge.io/docs/ngspice-46-manual.pdf), sections
2.4–2.6 and the diode device description;
[KiCad simulation models and pin assignments](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#simulator).
