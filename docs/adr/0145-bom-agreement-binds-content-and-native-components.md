# ADR-0145: BOM agreement binds content and native components

- Status: Proposed; private implementation under review
- Date: 2026-09-07
- Owners: CopperMCP maintainers
- Related: [ADR-0136](0136-electrical-artifact-capture-is-not-engineering-authority.md),
  [ADR-0144](0144-native-component-inventory-precedes-bom-reconciliation.md)

## Decision

Read actual captured BOM content and compare it with a freshly executed native component inventory.
Do not treat declaration counts, filename matches or model-library hashes as this comparison.
Keep electrical-inputs/v1 fields and meanings unchanged. A separate bounded
`bom-component-bindings/v1` document associates the declaration digest and project-capture digest
with explicit item-to-reference groups; it is not an approval or executable selection surface.

The first publication is a data-only reader for the fixed KiCad CSV profile. Required headers are
Refs, Value, Footprint, Qty and DNP; ordinary additional columns are retained, not silently certified.
Use standard CSV quoting, UTF-8, bounded fields/rows and context-free errors. Exact known reference
tokens take precedence over range expansion; ascending same-prefix numeric ranges are bounded before
allocation. Numeric conversions must not depend on Python's configurable decimal integer-string
limit or change global interpreter/Decimal settings. Arithmetic remains integral and exact.
The reader also accepts lower remaining row/reference budgets. Reconciliation passes its shared
remaining ceilings across artifacts, so another file cannot reset either budget.

The stdlib CSV engine's field limit must not inherit unrelated process-global settings. Initialize
a fresh private multi-phase `_csv` module per call from the already imported standard-library spec;
never replace `sys.modules`, reload the shared module or temporarily modify its limit/registry.
Require a distinct module and exception identity before configuring the private state. Set the
complete CSV dialect explicitly and retain the fixed 128-Ki-character parsing ceiling, with the
existing 4,096-byte field validation remaining authoritative. This preserves prior default-runtime
refusal classification as well as valid input acceptance. Initialization and parsing share the
caller deadline. Unsupported initialization produces a fixed, context-free unavailable-backend
refusal. Isolation is verified on CPython 3.11, 3.12 and 3.13; no model/plugin path is selected.
## Reconciliation boundary

The subsequent private operation executes the existing native inventory internally and captures
declared artifact bytes through the confined artifact reader. Every declared item must identify
exactly one row in its declared BOM artifact, with matching reference group and quantity. Every BOM
row must be bound once. Compare the complete reference set against all native components not excluded
from BOM, including DNP entries, then compare exact value, footprint and DNP metadata per reference.
No caller-selected partial component scope, guessed electrical equivalence or unit conversion.

Additional declared schematic artifacts must belong to the captured project. Existing v1 board,
snapshot and project-context digests are not reinterpreted as schematic-capture identities. Explicit
new binding digests identify the two input families without asserting complete electrical inputs.

Native inventory, artifact reads, parsing, comparison, hashing and final source/artifact freshness
checks share a bounded deadline. Carry it into the complete native receipt's component digest and
the final report's association conversion and streaming JSON/hash work, preserving canonical bytes.
Checks are cooperative over bounded primitives, not hard real-time interruption guarantees.
Changed bytes, malformed inputs and unavailable execution refuse;
well-formed disagreements return fixed mismatch counts and take precedence over missing scope.
Empty BOM-eligible component scope without disagreement is inconclusive, never a successful coverage result.
Matching empty declaration and reference-binding sets are valid inputs to this comparison; the
binding document must still contain its explicit `items` field. Nonempty native scope or contradictory
BOM rows still fails, and unequal declaration/binding item sets still refuse before execution.
Final checks follow report hashing and precede successful delivery; they are not atomic
filesystem or editor transaction guarantees.

For internal cross-operation checks, retain the exact native inventory alongside the report only
after those same final checks. The existing public function still returns the unchanged v1 report.
Its native_inventory_digest identifies the full project-component-inventory receipt, not the
underlying component-netlist. Consumers must verify the report-to-receipt link and then compare
the normalized component-netlist identity with other native observations. No extra native run,
new serialized report field or reinterpretation of either v1 digest is introduced.

## Authority and disclosure

Private, immutable associations can connect declared model IDs to checked BOM rows, but they do not
validate model definitions, device pins, model-digest meaning, ratings, calibration or physics.
Extra BOM columns remain outside the selected comparison scope. A report can state only agreement
of reference, value, footprint, quantity and DNP metadata, not engineering sign-off.

Disclose only digests, counts and fixed reasons. Raw references, values, model identifiers and custom
column data remain private and repr-redacted. Model/ratings/engineering validation stays not run and
application authority remains none. No MCP operation, v1 approval change, board saving or release.

## Validation

Use native KiCad synthetic controls, complete and mismatching BOMs, malformed CSV, range/quantity
bounds, restrictive Python integer limits, context/encoding failures, stale files, late hashing,
and missing/extra/duplicate bindings. Preserve all supported interpreters. Independent review,
source-bound full validation and protected hosted gates remain required before each publication.

Sources: [KiCad 10 BOM export](https://docs.kicad.org/10.0/en/cli/cli.html),
[exact Decimal construction](https://docs.python.org/3.12/library/decimal.html#decimal.Decimal).
