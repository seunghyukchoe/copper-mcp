# ADR-0149: Project terminal joins consume native evidence

- Status: Proposed; pure-join publication under review
- Date: 2026-09-07
- Owners: CopperMCP maintainers
- Related: [ADR-0145](0145-bom-agreement-binds-content-and-native-components.md),
  [ADR-0147](0147-captured-spice-definitions-precede-terminal-binding.md),
  [ADR-0148](0148-native-pin-net-maps-preserve-source-aliases.md)

## Publication scope

Publish the closed terminal document and pure bounded join first; actual artifact capture,
fresh BOM/native pin execution and final source freshness belong to the mandatory following
orchestration slice. This helper does not authenticate supplied receipts or execute KiCad.
Both slices are implemented in the preserved integration worktree and require their own gates.

## Complete join

Use `project-spice-terminal-bindings/v1`, bound to the declaration and project capture. Canonical
model entries select a model ID, definition name and definition-byte SHA-256, then complete
reference/pin assignments. Each pin has one exact model port or explicit NC. Preserve opaque
`electrical-inputs/v1` model_digest semantics; the new definition_digest identifies selected bytes,
while the artifact digest identifies the whole library. Retain numeric port spellings, including
leading zeros, without numeric normalization.

Reparse actual supplied captured-library bytes once per library under the bounded passive/diode
profile. Require one SPICE model per BOM item and exact complete model/reference/pin/port sets;
extra nonvirtual BOM-excluded components cannot disappear. Other model kinds remain opaque and
earn no SPICE coverage. Preserve every native source alias. NC requires both an explicit binding
and a native no-connect observation; singleton or missing nets do not imply NC.

Keep cumulative reference/pin/alias/model-byte ceilings and a finite caller deadline through
parsing, joining and selected-definition hashing. Returned definition/pin records are immutable
and repr-redacted, not author or model-accuracy attestations. Input declarations remain private.

## Mandatory next work and validation

Retain all pure-join tests, numeric-port/deadline corrections, independent safety and separate
Ponytail review. Validate the isolated slice and complete composition before publishing.
The next operation must derive capture/BOM/native evidence internally, hash its report, then
recapture artifacts and reverify source bytes; it must not accept a caller-supplied success receipt.
Neither slice establishes agreement with existing Sim.Library/Sim.Name/Sim.Pins settings, SPICE
export, simulation, physical accuracy, an engineering pass or application authority. Source-setting
checks, confined native export, sealed ngspice execution and calibration remain mandatory later
stages; partial mappings must not inherit native defaults silently.

Source: [KiCad simulation pin assignments](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#_pin_assignment).
