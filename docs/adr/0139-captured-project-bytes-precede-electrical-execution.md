# ADR-0139: Captured project bytes precede electrical execution

- Status: Proposed; private input adapter
- Date: 2026-09-06
- Owners: CopperMCP maintainers
- Related: [ADR-0136](0136-electrical-artifact-capture-is-not-engineering-authority.md),
  [ADR-0138](0138-schematic-hierarchy-is-private-bounded-metadata.md)

## Decision

Add `engineering.schematic_project_capture.capture_schematic_project()` as a read-only bridge
between declared workspace files and the existing private hierarchy model. Keep existing
electrical-inputs/v1 declarations, artifact capture, optimization/v1 and approval meanings intact.
This adapter is not a complete electrical input package or an execution/apply capability.

Require a root `.kicad_sch`, its exact same-stem `.kicad_pro`, and every declared reachable child
schematic, with an explicit SHA-256 binding per file. Permit at most 64 schematics plus that
single project file. Validate and copy all bindings before I/O: canonical relative paths,
allowed suffixes, unique exact identities, portable case/NFC aliases and file/directory-prefix
collisions. Preserve caller spelling rather than retargeting aliases. Use the existing
descriptor-anchored workspace reader; linked or special files refuse.

Privately copy and validate CaptureLimits. Defaults and maximum ceilings remain those of
ADR-0136: 8 MiB/file, 32 MiB retained total and five seconds by default; operator limits cannot
exceed 32 MiB/file, 128 MiB total or thirty seconds. A caller deadline can shorten, not extend,
the shared operation. Every first read uses the smaller of the per-file and remaining aggregate
budget. Hash each captured payload and compare its declared digest before proceeding.

Parse the project as bounded UTF-8 JSON, rejecting duplicate keys, invalid/non-finite values and
excessive depth/value counts. Validate only the text-variable map needed for hierarchy resolution:
up to 128 entries, 128-character keys in the declared identifier subset and 4096-character values.
Other project settings are retained as bytes, not certified as valid or safe to execute. Feed
the copied map into ADR-0138's native-compatible single-pass filename resolver. Do not substitute
environment-only KIPRJMOD or invent project-root anchoring for an explicitly declared relative
value.

Add optional cooperative deadline callbacks to the existing JSON preflight and tree guards;
their default behavior and accepted JSON set remain unchanged. Check every 4096 scanned
characters/visited values and at phase boundaries. Project parsing distinguishes deadline expiry
from malformed JSON, and capture checks expiry before producing a generic refusal.
These checks do not preempt a blocking filesystem call, UTF-8 decoding or the native JSON decoder.
A supervising process remains necessary for a hard wall-clock guarantee.

Derive hierarchy only from captured bytes. After the entire set has passed, perform a complete
second sweep including the project file, with each original length as its read ceiling, and
require byte equality. Return a frozen, repr-redacted capture whose digest binds root identity
and every path/digest/size tuple. No partial capture is returned. This is not an atomic
filesystem snapshot: execution/review/application must still enforce their own revision gates.

Fixed boundary errors are raised after internal handlers so underlying read, decoding or
malformed-limit exceptions are not retained as causes/contexts. This is not protection against
privileged inspection of caller inputs or traceback frame locals.

## Evidence and limits

Tests cover declared/nested/shared project capture, variables, malformed JSON, missing/extra/
duplicate bindings, aliases, traversal, symlinks and special files, exact digests, byte/time
limits, copied values, a changed second sweep and fixed error chains. Deterministic clock tests
exercise expiry during JSON scanning and after decode failure without weakening Python's
integer conversion limits.

Library/model dependency closure, BOM reconciliation, schematic/PCB parity, real ERC, physics,
fabrication outputs and live editor mutation remain separate work. No MCP tool, readiness pass,
engineering sign-off, board write or release is added by this adapter.
