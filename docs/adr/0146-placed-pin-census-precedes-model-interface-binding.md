# ADR-0146: Placed-pin census precedes model interface binding

- Status: Proposed; project-assembler publication under review
- Date: 2026-09-07
- Owners: CopperMCP maintainers
- Related: [ADR-0144](0144-native-component-inventory-precedes-bom-reconciliation.md),
  [ADR-0145](0145-bom-agreement-binds-content-and-native-components.md)

## Decision

The source-interpreter slice is followed by this complete project assembler and its unit controls.
It adds the private census entry point, immutable records, canonical identity and full hierarchy
occurrence assembly, while reusing the existing capture/hierarchy/flat-library preparation gate.
It performs no native execution and is not a transport-authority replacement. The complete native
differential proof below remains the mandatory final publication increment, not optional future work.

Derive a private occurrence census from the complete captured schematic hierarchy and verified
flat symbol-library closure before binding simulation model ports. A BOM reference or native XML
component UUID list is not enough to identify every placed unit and its pins. Keep existing v1
declarations, native component inventories, approvals and application boundaries unchanged.

Reuse the existing project preparation checks for captured bytes, hierarchy and exact cached/
supplied symbol-body equivalence. The census derivation itself performs no file read or execution.
Bind its immutable records to both captured-source and prepared symbol-context identities. Walk
every hierarchy occurrence, including repeated use of one source file, rather than only unique files.

## Instance and pin semantics

Require explicit current-instance identity using its complete sheet UUID path. Use its effective
reference and unit selection, not possibly stale display fields or the globally displayed unit.
Preserve virtual, excluded, hidden and DNP occurrences; no caller-selected subset can masquerade as
a complete census. Unconnected pins remain present; do not infer electrical net membership or an
explicit no-connect decision from this structural census.

KiCad stores raw pin records across units and filters the active unit at use time. Validate the raw
pin UUID inventory against the applicable library body, then emit pins belonging to the selected
unit or common unit zero. Apply the corresponding body-style/common-style rules. Resolve selected
alternate pin functions only against explicit library definitions. Refuse unsupported or ambiguous
identities instead of choosing an arbitrary pairing or silently omitting an occurrence.
Validated instance indexes are read-only mappings of frozen records. Their backing dictionaries
remain local to parsing and are never exposed, preserving constant-time path lookup without a
mutable nested collection inside the frozen parsed template.

Native source inspection and a controlled RC export probe establish the initial seams, not broad
profile coverage. The probe produced clean XML and SPICE exports with unchanged sources: XML
included library pin numbers/types and net-node membership; SPICE preserved the controlled circuit's
element terminal order. Broader multi-unit, common-pin, alternate and shared-sheet cases require
separate differential controls before claiming their native equivalence.

## Bounds and non-authority

Bound captured bytes, symbol/pin occurrences, library traversal, fields and cumulative work under
one caller deadline. Check deadlines during traversal and canonical identity generation, not only
after collecting the complete result. Errors and disclosed documents contain fixed reasons, digests
and counts, never private references, paths or model data. Frozen records remain self-digesting and
repr-redacted; malformed or late work cannot publish a partial census.

The result is captured structural evidence, not authenticated native connectivity, actual model
definition/terminal agreement, ratings, physics or engineering sign-off. Explicit native/model/
engineering validation remains not run and application authority remains none. The subsequent
model interface path must bind real definition bytes, ordered ports and complete pin associations
to native execution, controlled startup state and calibrated validity constraints as appropriate.
Required unknown engineering domains remain blocking; no readiness requirement is passed by this ADR.

## Validation

Publish this implementation in three mandatory stacked increments: the private captured-source
interpreter with direct controls; the complete project assembler, immutable records and identity with
all existing unit controls; then the complete native differential proof. The split does not redefine
completion as source interpretation alone or make the final native controls optional.

The interpreter never imports the assembler. One shared budget and active deadline span source
interpretation and occurrence assembly; the existing project preparation remains the sole capture/
hierarchy/library verifier. The error class is directly re-exported, not wrapped. Fixtures and direct
source tests must run without importing the later assembler. Module moves must preserve every
existing assertion and canonical digest byte before new publication evidence is collected.

Test explicit instance overrides, shared leaves, multi-unit/common/style selection, hidden/virtual/
excluded/DNP pins and alternate functions. Cover malformed or missing pin/UUID/instance data,
duplicate correspondence, source/library mismatch, cumulative bounds, deadline expiry, immutable
identity and redaction. Keep command doubles, native differential evidence and physics calibration
distinct. Independent correctness and Ponytail reviews, supported interpreters, full source-bound
validation and protected hosted gates remain required before publication.

Sources:

- [KiCad schematic instances and pins](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/).
- [KiCad symbol common syntax](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#_symbols).
- Pinned KiCad 18fb9289: `sch_symbol.cpp:314` (raw body-style pin inventory), `:539` (path lookup),
  `:912` (instance unit selection), `:1681` (selected/common unit filtering), and
  `sch_io_kicad_sexpr.cpp:827` (raw pin UUID serialization).
