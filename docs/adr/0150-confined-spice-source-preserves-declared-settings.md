# ADR-0150: Confined SPICE source preserves declared settings

- Status: Proposed; native-export parser publication under review
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
