# ADR-0138: Schematic hierarchy is private, bounded metadata

- Status: Proposed; internal primitive only
- Date: 2026-09-06
- Owners: CopperMCP maintainers
- Related: [ADR-0136](0136-electrical-artifact-capture-is-not-engineering-authority.md),
  [balanced readiness](../integrations/balanced-readiness.md)

## Context

Ordinary project ERC needs every schematic instance, including several instances of one child
file. The existing Circuit Intent path is deliberately a different, bounded source model.
Neither a file hash nor a successfully parsed hierarchy establishes electrical correctness.
Discovering dependencies must not let KiCad follow arbitrary user-selected paths first.

## Decision

Add private `engineering.schematic_hierarchy.derive_schematic_hierarchy()`. It accepts already
captured immutable schematic bytes and returns frozen source digests, file relationships and
expanded UUID instance paths. It performs no I/O, library lookup, engine execution, MCP
registration, board mutation or public completeness projection. Existing v1 meanings remain
unchanged. This is the first separately reviewed part of project capture, not its completion.

Require canonical workspace-relative POSIX source paths and exact-spelling reference targets.
Reject absolute paths, traversal outside the declared workspace, backslashes, ambiguous portable
case/NFC aliases, missing/unreachable sources, file cycles and duplicate UUIDs within a source.
Shared files may appear under distinct sheet UUID paths. UUIDs are not rewritten to repair input.
KiCad's supported quoted field tails are checked; unsupported field effects refuse rather than
silently changing reference interpretation. This is not a full schematic syntax validator.

Copy and validate operator-owned `CaptureLimits`; enforce their per-file/aggregate byte bounds
and one shared cooperative deadline across hashing, parsing, expansion and return. Admit at most
64 source files, 511 file edges, 512 expanded instances and 16 UUID levels. Check edge and pending
instance budgets before materializing oversized results. Paths and references are at most
4096 characters. The source format range is 20211123 through 20260306; accepting a header in this
range does not claim support for all constructs of that version.

Resolve child paths relative to their containing schematic, following the pinned KiCad 10.0.5
source. Support a copied map of at most 128 project variables, keys of at most 128 characters
using the declared ASCII identifier subset and values of at most 4096 characters. `PROJECTNAME`
is the root schematic stem. Do not consult process environment variables: `KIPRJMOD` in a sheet
filename is supported only when explicitly declared in the project map. Unknown, cyclic, clock
and version-control-dependent references refuse. Limit recursion to eight variable levels,
expanded output to 4096 characters and cumulative expansion work to 65,536 units per reference;
each recursive invocation charges its input characters plus one, including empty replacements.
This independent work ceiling prevents short or empty expansion results hiding exponential work.
Check the same deadline inside expansion. These conservative limits are a published subset, not
a promise to interpret every native project-variable spelling or nesting pattern.

Raw source paths, sheet names and content stay private and repr-redacted. Errors use fixed
messages. This does not defend against privileged same-process inspection of traceback locals.
Cooperative deadlines cannot preempt a blocked native operation; callers that require a hard
wall-clock boundary still need a supervising process.

## Evidence and consequences

Tests cover nested/shared sheets, exact target spelling, UUID uniqueness, real supported field
syntax, cycles, unreachable files, source/edge/instance/depth bounds, deadline expiry, variable
recursion and empty-result amplification, deterministic output and immutability. Synthetic
hierarchy tests earn no real-engine, physical-calibration or held-out quality credit.

Source references: [schematic format](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/),
[native file resolution](https://github.com/KiCad/kicad-source-mirror/blob/18fb9289ff0efdca53c0352ed81a0973f0a6b58c/eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr.cpp),
[project variable resolver](https://github.com/KiCad/kicad-source-mirror/blob/18fb9289ff0efdca53c0352ed81a0973f0a6b58c/common/project.cpp),
and [native field parser](https://github.com/KiCad/kicad-source-mirror/blob/18fb9289ff0efdca53c0352ed81a0973f0a6b58c/eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp).

Actual filesystem capture, JSON project settings, explicit library closure, real project ERC,
schematic/PCB parity, BOM/model reconciliation and engineering authorities remain later slices.
No readiness receipt, physics pass, approval token, apply capability or release is produced.
