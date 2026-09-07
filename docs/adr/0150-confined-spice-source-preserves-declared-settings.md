# ADR-0150: Confined SPICE source preserves declared settings

- Status: Proposed; complete source-preparation publication under review
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0149](0149-project-terminal-joins-consume-native-evidence.md)

## First publication: complete output interpretation

Interpret supplied KiCad SPICE output before exposing the later confined source/export operation.
Derive exact D/X rows from a complete model-terminal binding, preserve ordered ports and refuse
normalization collisions. This initial profile supports plain ASCII native names and their pinned
10.0.5 conversions; markup/escaped/non-ASCII net names remain explicitly unsupported.

Consume every line of the fixed title/include/component/end shape. Only exact expected library
includes and component rows are accepted; missing, duplicate, extra, swapped or raw-directive
output refuses. Replace only known temporary include identities with stable internal paths in
the normalized private observation. This parser does not authenticate supplied records or execute
KiCad, ngspice, source edits, geometry or application operations.

Admit bounded byte spans before decoding/splitting, cap line and row-token counts, and share a
finite caller deadline through validation, sorting and streaming identity. Assign returnable
results only after all checks succeed. Keep private observations immutable and repr-redacted.

## Mandatory remaining stages

The second publication adds the standalone internal edit kernel: canonical error/check objects,
literal directive/pin parsing, projected UTF-8 sizing and byte-preserving source splices. These
primitives require the later preparer's admitted inputs; direct parser/CST refusals can retain
their native types, while the preparer supplies the fixed public refusal boundary. The kernel
has no facade/coordinator dependency or execution authority. Public preparation and every original
end-to-end test remain in the complete implementation and are not replaced by kernel smoke tests.

The third publication connects complete project/model/alias admission to that kernel and returns
the immutable source derivative. It retains all original end-to-end preparer tests and the
canonical-object identity control. Original bytes, selected models, paths, budgets and projected
outputs are checked; the preparer neither reads arbitrary files nor executes tools or authenticates
caller-created receipts. The whole native export operation remains the mandatory following stage.

Source aliases must resolve to the declared logical reference in the captured instance census;
raw Reference properties do not substitute for instance overrides. Admit bounded nested record
types, identifiers, text and counts before model selection, sorting or binding hashing. Supplied
records remain untrusted even when they were constructed through Python dataclasses.

Preserve the complete implemented source preparer and native coordinator with all regression and
real controls in the integration worktree. Publish their model/source admission and bounded edits,
then the internally owned export operation. Existing simulation settings must be preserved or
explicitly completed in a separate immutable derivative; partial maps must not inherit defaults.
The operation must derive inputs internally, require two clean authenticated exports, hash the
result and finally recapture artifacts/recheck original source freshness. No stage alone grants
model accuracy, simulator, physics, human approval or apply authority.

Retain all 34 parser controls, including late-error, deadline and allocation regressions. Verify
isolated source and full composition, supported Python versions, independent correctness/security
and separate Ponytail review before protected publication. The five-area target and later confined
ngspice/calibration work remain unchanged.

Sources: [KiCad simulation pin assignments](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#_pin_assignment);
pinned 10.0.5 NETLIST_EXPORTER_SPICE and SPICE_GENERATOR, verified with owned native exports.
