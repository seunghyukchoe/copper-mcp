# ADR-0008: Candidate-bound authoritative KiCad DRC evidence

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

ADR-0007 established a pure serializer for an exact replayed route candidate, while ADR-0004
established bounded read-only KiCad DRC for an existing board. Calling those boundaries separately
does not prove that a returned DRC summary describes a particular candidate, source revision, and
rule/library context. Writing the derivative into the caller workspace would also create a preview,
export, or apply-like side effect before those capabilities have dedicated authorization contracts.

## Decision

`run_route_candidate_drc(requested_path, candidate, profile, settings)` is an internal application
boundary that returns `RouteCandidateDrcEvidence`. It is not exposed through MCP, CLI, preview,
export, persistence, or apply surfaces.

The service:

1. resolves the original board inside the configured workspace and captures its bounded board,
   project, custom-rule, library-table, and workspace-local library context;
2. parses only the captured board bytes with the supplied typed constraint profile;
3. requires the resulting Board IR snapshot digest to equal the candidate base revision;
4. renders only through ADR-0007's identity-checked, exact-replay, bounded, round-trip serializer;
5. replaces only the board payload in the in-memory context and rechecks per-file, file-count, and
   cumulative context ceilings;
6. passes that context to the same fixed `kicad-cli pcb drc` execution path used for ordinary board
   DRC, without save, refill, define-variable, or caller-controlled flags;
7. recaptures and hashes the complete private board/rule/library input context after KiCad exits,
   bounds and strictly parses the JSON report, requires exit code `0` to accompany no reported
   findings and documented exit code `5` to accompany at least one reported violation or
   unconnected item, and discards all child output; and
8. recaptures the original live context and discards the result if any board, project/rule, or local
   library byte or membership changed.

The frozen evidence record binds:

- the candidate SHA-256 identity;
- its canonical Board IR base revision;
- the original raw KiCad source revision;
- the patched private board revision;
- the complete patched DRC-context revision; and
- a strict nested `DrcSummary` whose board and context revisions equal those patched revisions.

DRC violation-type counts are copied into an immutable sorted mapping and their total must equal the
error, warning, exclusion, and unconnected aggregate counts. Serialization returns a detached plain
dictionary, so neither inconsistent aggregates, an input dictionary, nor a returned dictionary can
alter validated evidence after construction.

## Consequences

- One candidate can now receive authoritative, content-bound KiCad routing-format and connectivity
  evidence without creating a candidate file in the source workspace.
- Ordinary board DRC and candidate DRC share one fixed subprocess/report path, reducing security
  drift between the two operations.
- The complete captured byte dictionary is consumed after the private snapshot is written, avoiding
  overlap with the final live-context recapture.
- Exit code `5` and a strict report containing findings produce valid evidence rather than an
  adapter failure. A warning-only or exclusion-only report may remain a hard-correctness pass while
  still requiring exit code `5`; process/report disagreement fails closed.
- The initial supported integration case remains the committed synthetic two-pad, single-layer
  route. No public service claims general autorouting support.
- A clean DRC result does not establish electrical behavior, SI/PI, EMC, thermal performance, DFM,
  fabrication readiness, or production safety.

## Alternatives considered

- Run ordinary DRC on a caller-written candidate file: rejected because it loses the atomic
  candidate/source/context binding and creates an unreviewed durable-output surface.
- Trust candidate self-reported internal metrics: rejected because internal grid checks are not
  authoritative KiCad evidence.
- Add a new MCP or CLI command in the same change: rejected because transport ingestion,
  authorization, job persistence, preview, and apply need separate contracts and security reviews.
- Return raw KiCad findings: rejected because descriptions, coordinates, UUIDs, and net names can
  disclose proprietary board information.

## References

- [KiCad 10 command-line interface: PCB DRC](https://docs.kicad.org/10.0/en/cli/cli.html#pcb_drc)
- [KiCad DRC JSON schema v1](https://schemas.kicad.org/drc.v1.json)
- [ADR-0004](0004-authoritative-kicad-drc.md)
- [ADR-0007](0007-disposable-kicad-candidate-snapshot.md)
