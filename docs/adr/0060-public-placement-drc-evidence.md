# ADR-0060: Expose opt-in placement DRC evidence through the file-backed preview

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

The placement legalizer already returns an immutable candidate bound to a Board IR snapshot, and
ADR-0053 binds the supported source-preserving serializer to a private KiCad DRC context. Keeping
that oracle internal leaves an AI/MCP caller unable to distinguish deterministic placement legality
from the authoritative KiCad report for the same disposable candidate. Exposing raw reports would
disclose board details and would make a warning-only report easy to misread as a clean board.

KiCad 10's `pcb drc` command supports JSON output and uses exit code `5` for reported violations;
`--refill-zones` and `--save-board` are optional flags, so the read-only path can omit them. MCP
structured output and tool annotations provide a typed response surface, but annotations are hints,
not authorization; the existing candidate and operator-token boundaries remain authoritative.

## Decision

Add `include_drc`, defaulting to `false`, to the file-backed `preview_placement` request. When the
candidate is previewable and the flag is enabled, run `run_placement_candidate_drc` against the
same captured source and private context used by the internal gate. Return only
`PlacementCandidateDrcEvidence`, which binds:

- the immutable candidate and its Board IR base revision;
- the captured source revision;
- the patched disposable board revision; and
- the complete DRC context revision (board, project, rule, and local library inputs).

The nested `DrcSummary` remains aggregate and redacted. Its `passed` field is the compatibility
hard gate (zero active errors and unconnected items), while `clean` is stricter and requires no
warnings, exclusions, ignored checks, or violation-type findings. The source board is never
written. Live placement and apply requests advertise and enforce `include_drc: false`; DRC evidence
does not issue or imply apply authority.

KiCad failures, unsupported serialization, deadline expiry, context races, malformed reports, and
foreign or stale bindings fail closed with a fixed `PlacementPreviewError`. The MCP wrapper keeps a
closed request/response schema and never returns raw KiCad output, board bytes, net names, UUIDs, or
coordinates through the evidence object.

## Evidence and limits

The focused public-contract suite covers omitted-flag zero invocation, deterministic three-run
evidence, source inode/mtime/byte preservation, warning-only `passed=true`/`clean=false`, malformed
and foreign evidence, deadline refusal, live opt-in rejection, and MCP schema validation. B-044
  records three real KiCad 10.0.5 disposable replays with candidate/context binding and no workspace
  mutation. The fixture reports five ignored check classes and no errors, warnings, exclusions, or
  unconnected items, so the measured result is `passed_drc_runs=3`, `clean_drc_runs=0`, and a
  deterministic aggregate signature. This is not whole-board placement quality, ERC, fabrication
  readiness, live editor CAS, apply, or FreeRouting evidence.

## References

- [KiCad 10 CLI DRC](https://docs.kicad.org/10.0/en/cli/cli.html#pcb-drc)
- [MCP tools and structured output](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [ADR-0053: private placement candidate DRC](0053-private-placement-candidate-drc.md)
