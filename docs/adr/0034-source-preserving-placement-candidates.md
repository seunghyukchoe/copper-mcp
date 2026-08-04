# ADR-0034: Keep placement candidate rendering source-preserving and subset-bound

**Status:** Accepted

## Context

CopperMCP can now propose footprint poses against a revision-bound Board IR snapshot, but a
placement proposal is not useful for high-fidelity AI/editor work unless it can be projected back
into KiCad syntax without rewriting unrelated board content. KiCad footprints carry more than
their parent pose: pad positions and absolute board-frame pad angles must remain coherent, while
properties, text, graphics, library metadata, and 3D data are outside the Board IR 0.2 contract.

The existing route adapter establishes a safer pattern: parse the original bytes, verify the
candidate against the exact source and snapshot revisions, splice only known spans, parse the
disposable result again, and compare the resulting Board IR with the expected transformation.
Placement candidates also have a deliberate placeable subset: padless mechanical footprints are
represented in Board IR but are not emitted by the pad-based placement legalizer.

## Decision

Add an internal pure adapter, `render_kicad_placement_candidate_board`, with a deliberately narrow
contract:

- accept only a byte-identical source/snapshot pair, a self-identifying placement candidate, and
  the same KiCad constraint profile used for conversion;
- require native identities for all modeled geometry and require the candidate footprint set to
  equal the snapshot's placeable (pad-owning) footprint set, preserving padless footprints as
  untouched source spans;
- support only front-side, orthogonal poses and matching unfilled `F.CrtYd` rectangular geometry;
- splice only changed footprint `(at ...)` expressions and owned pad `(at ...)` angles, preserving
  every other source byte, including writer metadata;
- reject locked moves, side changes, malformed moved flags, unknown/duplicate references, stale or
  tampered revisions, unsupported syntax, and budget overruns;
- parse the disposable result again and require exact Board IR equality with the source plus the
  candidate's pose, pad-angle, pad-center, and courtyard transforms; and
- remain internal and non-mutating until a separate ADR establishes authoritative placement DRC,
  live-session compare-and-swap, single-undo behavior, and post-action re-observation.

## Consequences

AI can obtain a source-faithful disposable placement derivative for the supported footprint subset,
which is the missing prerequisite for placement DRC and later transaction design. The adapter does
not apply a board, invoke KiCad, validate courtyard overlap beyond the Board IR round trip, or claim
FreeRouting-style route completion. Unsupported footprint syntax is refused rather than partially
rewritten.

## Evidence and references

- `tests/test_kicad_placement_patch.py` covers deterministic output, byte preservation, round-trip
  transforms, padless-footprint preservation, stale/tampered/subset/malformed candidates, locks,
  unsupported courtyard layers, native identity requirements, and input budgets.
- KiCad's [Python Board API](https://docs.kicad.org/kicad-python-main/board.html) and
  [IPC add-on API](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/)
  keep editor mutation and commit/undo semantics separate from read-only observation.
- [ADR-0024](0024-placement-intent-and-legalization.md), [ADR-0026](0026-first-class-footprints-in-board-ir.md),
  and [ADR-0025](0025-file-level-candidate-apply.md) define the candidate, footprint, and
  source-splice boundaries this adapter reuses.
