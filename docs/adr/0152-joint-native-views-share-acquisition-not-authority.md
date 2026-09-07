# ADR-0152: Joint native views share acquisition, not authority

- Status: Proposed; independently reviewed and locally fully validated
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0144](0144-native-component-inventory-precedes-bom-reconciliation.md),
  [ADR-0148](0148-native-pin-net-maps-preserve-source-aliases.md),
  [ADR-0149](0149-project-terminal-joins-consume-native-evidence.md)

## Context

Component inventory and pin/net mapping used the same fixed KiCad XML export in separate
authenticated contexts. Covered profiling of the complete nominal-DC development workflow
measured about30 seconds in six whole-bundle signature checks across three contexts; source
preparation was milliseconds. A previous integrated repeat-run failure was not reproduced,
so timing variance is not treated as a proven explanation of that failure.

## Decision

Use a private joint producer for the model-binding workflow. It derives its source census and
prepared input, checks their identities, verifies original source bytes, and opens one existing
authenticated context over the unchanged XML snapshot. It executes exactly two fixed
`sch export netlist --format kicadxml` commands, checking the private snapshot/state and clean
diagnostics after each. Each bounded payload passes through both existing parsers.

Require both semantic observation pairs to agree and cross-check the component-netlist digest.
Do not require raw XML byte equality: the existing parsers deliberately handle volatile Date
metadata. Construct the existing component-inventory and pin-map records with their distinct,
unchanged v1 command/report namespaces and `repetitions=2`. Hash both reports and check final
source freshness. No partial pair or retained raw XML is delivered.

The original public component and pin-map APIs remain unchanged. Public BOM reconciliation still
acquires its own native inventory. The joint path first performs the same cheap BOM schema,
declaration/project identity and item-set admission, then feeds its internally produced inventory
to authoritative reconciliation. Artifact recapture, CSV limits, model joins and final source
checks remain. Caller-created records are not authenticated by the comparison helper.

Authentication is neither cached nor skipped inside an executed context. Context-entry and
exit-time bundle/executable verification remain; the later modified SPICE derivative still
requires a separate authenticated context. No budget is increased. The inexpensive repeated
pure preparation remains to avoid widening the census API for an unmeasured benefit.

## Evidence and remaining gates

Independent review accepted the implementation after restoring pre-native BOM admission.
Real comparison showed both joint reports and digests equal their legacy counterparts.
The final covered real-equivalence and repeated-operation controls passed, with whole calls
of29.368s and26.273s within unchanged60-second limits. These are scoped observations, not a
statistical performance guarantee. An isolated real-equivalence control also passed.

Fresh integrated validation, exact-tip isolated checks, protected hosted validation and main
verification remain required. This acquisition change adds no simulator, calibrated physics,
engineering verdict, approval capability or apply authority. No five-area readiness requirement
is closed solely by sharing the native work.

Source: [KiCad10 CLI netlist export](https://docs.kicad.org/10.0/en/cli/cli.html#schematic-export-netlist),
plus the pinned10.0.5 native behavior and the existing parser/execution contracts above.
