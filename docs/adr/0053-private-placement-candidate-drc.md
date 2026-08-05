# ADR-0053: Bind the supported placement subset to private KiCad DRC

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

Placement previews already produce immutable, revision-bound candidates, and the source-preserving
KiCad adapter can replay a narrow footprint subset. A legalizer result is not sufficient evidence
that KiCad will accept the edited board: project rules, library tables, and KiCad's own geometry
checks must be exercised on the exact disposable candidate. KiCad's CLI DRC interface emits a
bounded JSON report and an exit code for violations, making it suitable for a read-only oracle when
the input context is isolated.

## Decision

Add an internal `run_placement_candidate_drc` service and immutable
`PlacementCandidateDrcEvidence` for the existing serializer subset only:

- front-side, orthogonal, pad-owning footprints with native identities;
- matching, unfilled rectangular `F.CrtYd` geometry; and
- exact source Board IR, candidate, patched-board, and DRC-context digest bindings.

The service captures the board and permitted local KiCad rule/library context, renders the
candidate into a disposable private snapshot, invokes the fixed DRC command with JSON output and
no refill or save flags, and re-captures the original context before accepting evidence. Raw
findings, board bytes, paths, net names, UUIDs, and model data are reduced to the existing
aggregate `DrcSummary`. Unsupported footprint syntax fails closed before KiCad runs.

This is an internal evidence gate. The public placement preview, live placement, placement apply,
undo, post-action observation, back-side/non-rectangular geometry, and electrical/fabrication
claims remain separate roadmap items.

## Evidence and limits

Fake-runner tests cover clean and violating reports, stale/tampered candidates, malformed reports,
timeouts, private-tree mutations, original board/rule/library races, and redaction. A real KiCad
10.0.5 fixture confirms zero errors and unconnected items while preserving source bytes, inode, and
mtime. This does not establish whole-board placement validity, general courtyard support,
fabrication readiness, or FreeRouting parity.

## References

- [KiCad CLI DRC](https://docs.kicad.org/10.0/en/cli/cli.html#pcb_drc)
- [KiCad Board API](https://docs.kicad.org/kicad-python-main/board.html)
- [KiCad IPC add-on guidance](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/)
