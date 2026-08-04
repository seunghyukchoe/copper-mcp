# ADR-0045: Verify layered candidate topology before serialization

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** CopperMCP maintainers
- **Related:** ADR-0036, ADR-0037, ADR-0038, ADR-0044

## Context

`LayeredRouteCandidate` is immutable and content-addressed, but its identity digest proves only
that the candidate was re-created from its canonical fields. The previous serializer guard used a
global endpoint/via union and could therefore accept a re-stamped candidate whose path began one
nanometre away from a via. KiCad's DRC is authoritative for the supported serialized board, but a
zero-violation report is not fabrication evidence for via-in-pad treatment.

KiCad documents the board-level pad polygon and padstack APIs, and its PCB Editor DRC guidance
calls out via-in-pad treatment, dangling vias, edge clearance, hole clearance, hole-to-hole
clearance, and missing same-net connections:

- [KiCad Board API](https://docs.kicad.org/kicad-python-main/board.html)
- [KiCad PCB file format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/)
- [KiCad PCB Editor DRC](https://docs.kicad.org/master/en/pcbnew/pcbnew.html)

## Decision

Add a pure, bounded `verify_layered_candidate` gate and require it in the disposable layered
serializer after exact request replay and before any bytes are rendered. The verifier:

1. Re-checks the candidate digest, Board IR snapshot digest, endpoint/net bindings, and endpoint
   layer access.
2. Requires exactly two signal layers and a full-stack via for every layer transition.
3. Builds an explicit path/via chain: every via must equal the adjacent path endpoints and layer
   transition; path segments must be orthogonal, non-zero, non-duplicated, and non-crossing.
4. Applies independent path/vertex/via/intersection budgets and preserves redacted diagnostics.
5. Refuses endpoint-via/via-in-pad geometry conservatively because the candidate contract has no
   padstack treatment or IPC-4761 evidence. The router therefore reserves endpoint pad envelopes
   for tracks and blocks via transitions there.
6. States `physical_validation="not_modelled"` explicitly. Callers may request physical
   validation and receive a refusal rather than a false DRC/fabrication claim.

This is a structural gate, not a replacement for KiCad DRC, exact pad polygons, hole/edge
clearance, zone refill, electrical analysis, or fabrication review. Live MCP proposals remain
candidate-only and are not promoted to serializer or apply authority by this ADR.

## Consequences

Positive:

- Re-stamped disconnected, crossing, duplicate, stale, or endpoint-via candidates fail before
  serialization and future export/apply paths can reuse the same gate.
- The blocked-pad fixture now routes F.Cu → B.Cu → F.Cu, avoiding a via at the final SMD pad while
  retaining deterministic two-via connectivity.
- Structural and physical claims are separated in the result contract.

Trade-offs:

- The conservative endpoint policy may reject valid through-hole via-in-pad layouts until a
  padstack-aware contract carries exact polygon, drill, annular, and IPC-4761 treatment evidence.
- Exact clearance, edge, hole, keepout, and filled-zone legality still require a private KiCad DRC
  round trip; real GUI and fabrication tests remain external gates.

## Evidence

- `tests/test_layered_candidate_verifier.py`: five deterministic structural/negative tests,
  including re-stamped disconnected geometry, same-layer self-crossing, endpoint-via refusal,
  stale digest, and explicit physical-validation refusal.
- `tests/test_layered_board_adapter.py` and `tests/test_kicad_layered_route_patch.py`: endpoint-via
  avoidance, deterministic serialization, and Board IR round trip.
- `scripts/benchmark_layered_candidate_verifier.py` and B-027 record the bounded verifier replay.
