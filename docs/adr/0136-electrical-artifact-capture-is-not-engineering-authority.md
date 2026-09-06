# ADR-0136: Electrical artifact capture verifies bytes, not engineering authority

- Status: Proposed
- Date: 2026-09-06
- Related: [ADR-0134](0134-freeze-readiness-requirements-before-measuring-progress.md),
  [balanced readiness](../integrations/balanced-readiness.md)

## Decision

Add a private, read-only `engineering.capture` adapter before implementing project-level
engineering executors. Existing `electrical-inputs/v1` declarations and optimization/v1 semantics
remain unchanged. Capturing an artifact does not authenticate its author or its physical model.

The adapter takes the bounded electrical declaration JSON, a closed
`electrical-artifact-paths/v1` JSON document containing an `artifacts` list of
`artifact_id`/`path` objects, the configured workspace, and operator-owned capture limits.
Every declared artifact must have exactly one binding; extra, missing, duplicate or canonical-alias
paths refuse before reading files. Paths must be canonical workspace-relative POSIX paths;
absolute paths, traversal, backslashes, control characters and symlinks are refused.
For portable alias rejection, compare full relative paths after Unicode case folding and NFC
normalization, without rewriting the paths used for reads. This conservatively refuses two
case-distinct files on a case-sensitive filesystem as well; it does not promise exact on-disk
spelling or filesystem-wide alias detection. Reject Unicode control and surrogate characters.

Use the existing descriptor-anchored `read_workspace_file` boundary. Admit BOM `.csv`/`.json`,
schematic `.kicad_sch`, netlist `.xml`/`.net`, and model-library
`.lib`/`.mod`/`.cir`/`.sp`/`.spice`/`.ibs`/`.json` suffixes. This is a read allowlist, not format
validation: no model directive, include, external reference, command or provider is executed.

At most sixteen artifacts are admitted. Default limits are 8 MiB per file, 32 MiB retained total,
and five seconds of cooperative capture time. Operators may tighten each limit down to one byte
or one second, or increase them only up to 32 MiB per file, 128 MiB total and thirty seconds.
Exact integers are required. Each file must be nonempty
and match its declared SHA-256. Charge the first-pass retained bytes cumulatively and constrain
each read to the smaller of the per-file ceiling and the remaining total. After capturing the
entire declared set in the first sweep, re-read each
file with its first-pass length as its ceiling and require identical bytes before returning;
thus at most twice the retained budget plus bounded oversize probes is read. Check the deadline
before and after each read/hash, and before return. An optional caller deadline may shorten but
never extend the configured capture budget. A blocked filesystem syscall is not preempted by
these checks; a supervising process must impose any required hard wall-clock limit.
This is not a filesystem-wide atomic snapshot: later changes still require a fresh context check
before execution or approval. Canonical path checks do not authenticate filesystem ownership or
claim to detect distinct hard links to one inode.
At entry, require the exact CaptureLimits type and reconstruct a validated private copy, so
invalid instances and later caller-side changes cannot alter the admitted budget.

Return one frozen private capture only after every check passes. Raw paths, names and payloads
must not appear in reprs, error messages or the redacted projection. Public-safe metadata contains
only the declaration/capture digests, artifact count, total bytes and fixed non-claim fields:
`binding_scope: declared_artifact_bytes_only`, `project_capture_complete: false`,
`semantic_validation: not_run`, `model_execution: not_run`, and `apply_authority: none`.
The capture digest binds logical artifact IDs, roles, digests and sizes to the declaration;
physical workspace paths remain private and do not become portable content identities.

## Consequences and validation

This supplies real file verification for later immutable project capture, not complete hierarchy
capture, schematic/PCB parity, a valid model library, simulation evidence, an audited readiness
score or an apply capability. Nested schematic/model dependencies remain unresolved until their
own capture and validation adapter is implemented. No MCP tool or readiness pass is added.

Tests must prove exact-bound success, digest mismatch, missing/extra/duplicate bindings, path
alias/traversal/absolute/control-character rejection, linked/special files, per-file/aggregate
limits, malformed and expired deadlines, mid-capture changes, no partial return, input immutability,
deterministic identity and redaction. Reading a malicious model as data must never execute it.
