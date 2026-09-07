# ADR-0148: Native pin-net maps preserve source aliases

- Status: Proposed; native-executor publication under review
- Date: 2026-09-07
- Owners: CopperMCP maintainers
- Related: [ADR-0144](0144-native-component-inventory-precedes-bom-reconciliation.md),
  [ADR-0146](0146-placed-pin-census-precedes-model-interface-binding.md),
  [ADR-0147](0147-captured-spice-definitions-precede-terminal-binding.md)

## Publication scope

This final publication stage adds authenticated read-only execution above the unchanged source
admission and XML mapper. It derives its own census, verifies the prepared input identity, performs
two fixed native exports, checks complete observation equality, and validates source freshness
after the final report hash. All 14 executor controls, including four real KiCad cases, are retained.

The returned map binds native observations to captured inputs; it is not a model, circuit-function,
ERC/DRC, physics, approval or application verdict. Component-inventory v1 remains unchanged. This
completes the three mapper publication stages only after its own review and validation gates pass;
complete model-terminal/NC binding and confined calibrated simulation remain subsequent work.

## Complete mapping decision

Add a separate private native pin-to-net map before comparing model terminals with circuit nets.
Preserve component-inventory v1 and its digest. Reuse bounded XML admission/design/component checks;
interpret nets separately. The executor must derive its own census, compare two complete observations
inside the authenticated KiCad context, and check source/state freshness before delivery.

Group nonvirtual source occurrences by reference and pin number while retaining every canonical
sheet/symbol/pin UUID identity. Admit every census field before hashing, including exact flags,
selectors, text and digest bounds. Known QuotedAtom source strings may become plain strings only
after the early length gate; native XML validation remains strict. Check each pin against its exact
source-symbol occurrence and require consistent metadata within an alias group. Do not choose one
occurrence and drop the rest. Count virtual pins explicitly outside the physical-component scope.
Reject duplicate pin numbers within one sheet/symbol occurrence even when their pin UUIDs differ;
legitimate aliases must come from distinct source occurrences. Apply that check before virtual-pin
exclusion and before hashing. Source group/context representations are fixed and redact private fields.

The later XML join must require exactly one native node for every logical pin and refuse missing,
extra, duplicate or cross-net keys before dictionary reduction. Retain net names/codes/classes and
effective pin metadata. Preserve explicit `+no_connect` observations; do not infer NC from missing
nodes, singleton nets or names beginning with `unconnected`. Empty native net records must refuse.

## Evidence and non-authority

The executor includes owned real controls for shared aliases, conflicting nets and explicit NC.
KiCad 10.0.5 deduplicates aliases within a net record, not across nets. These fixed controls verify
their stated observations; they do not establish arbitrary per-node source UUID authority or
physical validity beyond the captured-source and supported native-profile checks.

Records are immutable and repr-redacted; temporary grouping dictionaries remain private working
data, not immutable evidence. Refusals are fixed and context-free. Keep cumulative bounds and the
same caller deadline through admission, grouping and hashing. No request-selected executables,
authority registry, engineering pass, approval capability or board-file mutation is introduced.

## Mandatory validation

Publish source admission/direct controls, complete XML mapping/identity/remaining parser controls,
then the native executor/all real controls. Retain all 103 parser and 14 executor baseline cases,
plus extraction controls. Verify source-only import isolation, direct error/alias re-export identity,
digest preservation, complete extracted integration, supported interpreters and fresh full checks.
Independent correctness/security and separate Ponytail review remain required before publication.
Complete model definition/terminal/NC binding and confined, calibrated simulation still follow; no
stage or safe refusal alone earns the five-area 90% readiness target.
