# ADR-0140: Project ERC observations do not invent Circuit Intent identities

- Status: Proposed; private report adapter
- Date: 2026-09-07
- Owners: CopperMCP maintainers
- Related: [ADR-0139](0139-captured-project-bytes-precede-electrical-execution.md),
  [ADR-0132](0132-supervised-optimization-keeps-evidence-and-consent-separate.md)

## Decision

Factor the existing private ERC report decoder into identity-neutral observations and the
unchanged legacy Circuit Intent wrapper. Ordinary hierarchical projects must not borrow fake
intent/schematic identifiers to fit a generated-passive-schematic report type.

Keep the existing `_parse_erc_report` signature, genuine caller bindings, result shape and
interpretation. Its default path retains duplicate display-path rejection and legacy UUID
handling. Do not register a new MCP tool, change optimization/v1 or grant execution authority.

The optional project path takes captured expected UUID instance paths. Require canonical
lowercase UUID components, exact set coverage and no duplicate identity. One optional trailing
slash is accepted for native compatibility but cannot create a second identity. Display paths
are not identity: distinct sheets may have the same name.

An optional executor-owned minimum-severity map can require a finding to meet the effective
executed rule floor. Validate and copy a provided nonempty map of at most 10,000 bounded keys and
error/warning/ignore values before reading findings. Active mapped checks cannot appear in
ignored_checks, and their findings cannot be excluded or downgraded. An explicitly disabled
category may be listed as ignored but cannot emit a finding. Reject absent categories under
that map. This compares a report with supplied expectations; it does not authenticate the map,
backend, actual settings or execution. The caller must derive and validate those independently.

Compute a normalized digest of the full validated report, dropping only its root generation
timestamp and normalizing sheet/finding order. Descriptions, items and additional report fields
remain bound; equal counts cannot conceal different findings. Expose only the digest and
aggregate observations, not raw descriptions or item coordinates.

## Validation and limits

Tests cover UUID completeness/duplicates/format, native trailing slashes, duplicate display names,
finding/digest sensitivity, ordering/date invariance, severity floors and unchanged legacy
interpretation. JSON depth/value/non-finite/duplicate-key checks remain shared with the established
CLI boundary.

A digest is not execution authentication. Exact UUID coverage is not proof that every child file
loaded: the pinned KiCad loader can recover child errors while retaining sheet entries. The
separate project executor therefore still needs native source-load validation, immutable context
and library/model checks, backend validation and independent review before publication.
The observation's `passed` flag means only zero reported hard errors; it is not an engineering
judge pass, project-completeness claim, sign-off, approval or application capability.
