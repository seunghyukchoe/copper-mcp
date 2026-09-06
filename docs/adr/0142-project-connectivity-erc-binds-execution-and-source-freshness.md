# ADR-0142: Project connectivity ERC binds execution and source freshness

- Status: Proposed; private execution adapter
- Date: 2026-09-07
- Owners: CopperMCP maintainers
- Related: [ADR-0141](0141-project-erc-prepares-an-explicit-rule-and-library-derivative.md),
  [ADR-0140](0140-project-erc-observations-do-not-invent-intent-identities.md),
  [ADR-0139](0139-captured-project-bytes-precede-electrical-execution.md)

## Decision

Add a private `engineering.project_erc.run_project_erc()` operation over a captured schematic
project, explicit symbol-library bytes and operator-owned Settings. Keep generated Circuit Intent
ERC, optimization/v1, all public MCP tool meanings and approval/application capabilities unchanged.
This operation establishes only the published connectivity profile, not complete engineering judgement.

Use ADR-0141's validated immutable input derivative. Compare every original captured file with
its corresponding workspace file immediately before native execution and before delivering the
result. Reads use the existing descriptor-confined reader with each original length as its bound.
Do not scan unrelated workspace files. Only assign the delivered result after the final comparison
succeeds. These are freshness observations at two boundaries, not an atomic filesystem snapshot,
continuous edit exclusion or permission to apply a result after another revision change.

The initial backend profile is an existing vendor-sealed KiCad 10.0.5 macOS application. The fixed
system verifier checks both the enclosing bundle and CLI, including the expected vendor identity;
the CLI version must match exactly. Bind the CLI bytes and sealed component digests, then repeat
the checks before returning evidence. Arbitrary executable hashes, request-selected verifiers and
alternate authority registries are not accepted. Other platforms require separately reviewed
backend profiles and currently refuse; Python 3.11–3.13 support is unchanged.

Execute fixed argument arrays without a shell through the existing bounded child wrapper. Use a
private temporary input copy, private environment/configuration, fixed empty global library tables
and fixed Fontconfig configuration. Disable bytecode writes into the authenticated application.
Retain shared wall-clock, file/output and context limits across preparation, checks and both runs.
Validate temporary input/configuration state after native work. This is not an OS sandbox against
a compromised native executable or privileged concurrent modification of the operator's host.

## Native loading and observations

KiCad 10.0.5 can recover a child-file load error while returning a project ERC report containing
that sheet's identity. Exact UUID coverage alone therefore does not prove complete loading.
Validate each original schematic as a root using the fixed `sch upgrade --force` command in a
separate disposable copy. Only the designated source inode may be resaved; restore its mode via
the retained descriptor, verify every unrelated input and discard the upgraded output. Final ERC
always sees the original captured schematic bytes, never those upgraded derivatives.

Run exactly two full project ERC checks with JSON output, millimetre units, all severity classes
and the native violation exit code. Validate source/backend identities, exact sheet UUID coverage,
effective severity floors, known finding categories, output size and native exit-code consistency.
Normalize only the unpredictable private-directory spelling before the full-finding digest; the
shared report adapter additionally normalizes its root date and collection order.

Bind the report to capture, execution, profile, executable, native-syntax, authentication and
command digests. Expose aggregate counts and digests rather than raw findings, item coordinates or
source/library contents. Failures converge on a fixed ProjectErcError without underlying causes
or contexts, including malformed/cyclic workspace resolution. Privileged traceback-local inspection
is not covered by that disclosure boundary.

Validate the optional configured CLI as None or Path before discovery. Pass the same deadline into
the shared ERC parser: check JSON preflight/tree validation, semantic traversal, sort-key and final
serialization, hashing and return. Encoding/decoding and built-in sorting remain indivisible
operations with checks around them, not hard preemption. Existing generated-schematic callers
omit the new optional deadline and retain their accepted inputs, summaries and digest meanings.

For admissible observations, hard findings take precedence and produce `fail`. Unequal repeated
observations, library parity findings or a nonconforming ignored-check set produce `inconclusive`.
Missing outputs, malformed reports, excluded findings and ignored active rules instead cause a
sanitized execution/validation refusal: no report is delivered. A `pass` means equivalent
zero-hard-error observations within this connectivity profile; warnings can remain.
Simulation, fabrication, board parity and typography remain explicitly not run, and apply authority
remains none. There is no aggregate engineering sign-off or new public optimization package here.

## Evidence and remaining work

Real controls cover hierarchical/shared sheets, project variables, source preservation, repeated
digests, malformed child loading and a stronger native pin-conflict setting. Command doubles cover
backend/state/report faults and cannot substitute for real-engine evidence. Workspace mutation,
missing/symlink files, final-read expiry, invalid settings and sanitized resolution errors have
focused controls. Full final-source validation and hosted checks remain publication gates.

General symbol/model coverage, ordinary schematic/PCB parity and BOM reconciliation, integration
into a versioned optimization package, calibrated physics/fabrication authorities, held-out quality
and genuine human/editor acceptance remain open. The original five-area target is not reduced to
this connectivity subset.
