# Balanced readiness: requirements and electrical declarations

The balanced program targets ordinary analog/audio, MCU/sensor and low-power non-isolated supply
profiles across 2/4/6/8-layer boards. RF, DDR/PCIe, mains and safety-critical sign-off are excluded.
It extends the [v0.13 plan](../plans/v0.13-supervised-optimization.md); it does not replace that
release's gates with a foundation-only claim.

## Frozen scorecard

`copper_mcp.optimization.readiness.FROZEN_CATALOG` contains fifty versioned requirements, ten per
area. Every area has the same 100-point denominator: capability 40, real validation 30,
integration/recovery 20, release evidence 10. The catalog digest binds profiles, exclusions,
requirements, weights, required evidence origins and critical gates. A changed catalog is a new
denominator, not progress.

```sh
PYTHONPATH=src python scripts/check_readiness.py
PYTHONPATH=src python scripts/check_readiness.py /path/to/submission.json
```

With no file, the command prints the catalog and its digest. Submitted receipts must identify
the same source and catalog, name known unique requirements and use the required evidence origin.
Only a submitted pass contributes points. Missing/inconclusive/not-run/failed evidence contributes
no points, and any missing critical gate blocks review eligibility even above ninety points.
Unit tests and test doubles cannot be substituted for real-engine, physical or hosted evidence.

These are **submitted points, not an audited maturity score**. Artifact digests in caller-authored
JSON do not prove that an engine or human review actually ran. The result therefore always has
`audited_readiness_score: null`, `artifact_authenticity_verified: false`,
`release_authorized: false` and `apply_authority: none`. Exit zero means catalog output or
eligibility for independent artifact review, one means blocked and two means malformed.

## Private electrical input package

`copper_mcp.engineering.inputs.parse_electrical_inputs()` accepts bounded `electrical-inputs/v1`
JSON. It binds board, snapshot and project-context digests to a declared profile, source artifact
references, BOM/model bindings, physically ordered copper/dielectric layers, rails/load cases,
signal edges, operating limits and thermal boundary conditions. Quantities use explicit scaled
integer units; identifiers, references, dimensions and collection sizes are validated.

The raw declaration is private and ephemeral. `redacted_projection()` returns only a package
digest, counts and missing input categories. It includes no model paths, URLs, executable
selection, component identifiers or raw model data. Missing or unsuitable model kinds, incomplete
rail loads, absent schematic/netlist references and missing material/thermal properties remain
explicit. Supplying one valid edge model does not cover a different edge with a wrong model kind.

Input completeness is **declaration coverage only**. It does not authenticate source artifacts,
prove schematic/PCB parity, establish physical parameter validity or run any simulator. This
slice does not add a physics executor, relax the optimization/v1 judge, register new MCP tools,
grant application authority or implement strict live mutation. Actual project capture, backend
execution, model calibration and independent evidence review remain required.

## Verified artifact bytes

The private `engineering.capture.capture_electrical_artifacts()` adapter verifies the actual
bytes behind every declared artifact. It takes declaration JSON and a separate bounded
`electrical-artifact-paths/v1` document, reads only confined allowlisted file types, and completes
a second sweep before returning an immutable private capture. All limits are copied and validated
at entry; the caller cannot extend them during capture. Case-folded/NFC path collisions are
conservatively refused even on case-sensitive filesystems.

Only `capture.redacted_projection` is safe to disclose. It carries digests/counts and explicit
`project_capture_complete: false`, `semantic_validation: not_run`, `model_execution: not_run`
and `apply_authority: none`. A model library remains untrusted data even after its hash matches.
Dependency/hierarchy discovery, format validation, project parity and evidence authentication
remain separate work. See [ADR-0136](../adr/0136-electrical-artifact-capture-is-not-engineering-authority.md).

## Captured candidate connectivity parity

The private `kicad_cli._run_captured_source_to_board_parity()` adapter can check an immutable
candidate board against the existing bounded Circuit Intent projection. It requires the exact
board-byte digest before invoking KiCad and returns the existing evidence bound to that board,
the intent, the delivered schematic and the board-eligible projection. It never rereads the
workspace source board. Invalid inputs and exhausted deadlines refuse; late output is not success.

This reuses the fixed two-file, default-severity check and the component-accounting liveness
invariant in [ADR-0084](../adr/0084-authoritative-source-to-board-parity.md). It does not adopt a
user project or library configuration, broaden Circuit Intent's passive subset, check footprint
correctness, or supply ERC/physics evidence. Real KiCad known-good and known-bad controls use
captured bytes that deliberately disagree with the workspace board to distinguish the two inputs.

The primitive is not exposed in MCP or attached to `optimization/v1` packages. A later versioned
package needs a distinct parity record and its own integration review; parity must not be
relabeled as ERC or silently change existing approval semantics.

## Incomplete placement work

The private placement solver reports `legalizer_exhausted` when an inner work/time limit prevents
evaluation. Optimization refuses that search before serializing derivatives. Replay certification
requires the exact completed work ceiling; it cannot count a timed-out final evaluation as a
completed search. The benchmark's 60-second outer guard is configuration-bound and does not
change production defaults or re-sign historical evidence.

See [ADR-0134](../adr/0134-freeze-readiness-requirements-before-measuring-progress.md) and
[ADR-0135](../adr/0135-inner-placement-exhaustion-is-not-completed-search-work.md).

## Private schematic hierarchy metadata

`engineering.schematic_hierarchy.derive_schematic_hierarchy()` derives deterministic file edges
and UUID instance paths from immutable supplied schematic bytes, including multiple instances of
one child file. It performs no filesystem reads or library lookup. Exact reference bindings,
canonical paths, source/edge/instance/depth ceilings, copied byte/time limits and a separate
single-pass variable expansion with pre-append size checks are mandatory. Native project-variable
resolution is a bounded subset; unresolved substituted values refuse, and process environment
variables do not supply sheet filenames.

This metadata is private, not a project-completeness or ERC report. Filesystem capture, project
settings and library closure, actual ERC/parity, BOM/models and all physical judgement still need
separate evidence. See [ADR-0138](../adr/0138-schematic-hierarchy-is-private-bounded-metadata.md).

## Declared schematic-project bytes

`engineering.schematic_project_capture.capture_schematic_project()` now bridges confined file
reads to the private hierarchy adapter. Supply exact SHA-256 bindings for the root schematic,
same-stem project file and all reachable child schematics. The entire set is validated before
reading; retained bytes, project JSON parsing, hierarchy derivation and the complete second sweep
share copied limits and one cooperative deadline.

The frozen private result contains verified bytes and a bound hierarchy, not a public completeness
or ERC claim. Other project settings, library/model dependencies, BOM and candidate parity still
need their own validation. No board write, approval or new MCP tool is added. See
[ADR-0139](../adr/0139-captured-project-bytes-precede-electrical-execution.md).

## Identity-neutral ERC observations

The private CLI report adapter can now bind a hierarchical report to captured UUID instance paths
and effective rule severity floors without fabricating Circuit Intent IDs. A full-finding digest
normalizes only report date and collection order; descriptions and item details remain bound.
Existing Circuit Intent summaries preserve their public shape and interpretation.

This is report validation, not evidence authentication or complete source-load proof. The separate
project executor, project parity and model authorities must still earn their own acceptance.
See [ADR-0140](../adr/0140-project-erc-observations-do-not-invent-intent-identities.md).
