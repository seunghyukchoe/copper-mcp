# ADR-0042: Public, candidate-only layered route preview

**Status:** Accepted
**Date:** 2026-08-05
**Owners:** CopperMCP maintainers

## Context

CopperMCP already has a deterministic two-signal-layer Board IR router, a source-preserving
disposable serializer, and an internal candidate-bound KiCad DRC gate.  The missing MCP seam meant
an AI host could observe a revision-bound scene but could not request a bounded via-capable proposal.
The MCP tools specification treats tools as model-controlled and recommends human-in-the-loop
confirmation, input/output schemas, validation, sanitization, rate limits, and timeouts.  KiCad's
official Board API also distinguishes read-only board inspection from mutation commits, so this
milestone must remain read-only.

## Decision

Add `preview_layered_route` as a separate MCP contract and application service.  It:

1. reads one workspace-confined `.kicad_pcb` through the descriptor-anchored file boundary;
2. requires `expect_board_revision` and `expect_snapshot_digest` compare-and-swap values;
3. accepts two stable pad references and infers the net only after Board IR conversion;
4. supports only the existing two-signal-layer, rectangular-outline, full-stack-via adapter subset;
5. validates bounded integer settings and emits a closed, status-specific structured output union;
6. verifies the candidate's canonical content digest before returning geometry; and
7. performs no KiCad write, refill, DRC, candidate apply, persistence, or durable export.

Stale board and snapshot checks return a typed `not_routed`/`stale_revision` result. Conversion
failures return `unsupported_board` with diagnostic counts; a valid snapshot outside the layered
subset remains a bounded `not_routed` diagnostic. Pad/net names and raw board text are never
returned. The candidate is an inspectable proposal, not evidence that KiCad would accept or apply
the route.

## Alternatives rejected

- Widening the legacy single-layer `RoutePreviewToolResponse`: this would make candidate identity,
  path layer semantics, and apply expectations ambiguous.
- Accepting a raw net name: a pad-reference request is safer for scene-to-route chaining and avoids
  echoing author-controlled names across the MCP boundary.
- Exposing the internal DRC/serializer/apply paths in one call: those are separately reviewed,
  bounded capabilities with different mutation and evidence semantics.

## Evidence and references

- [MCP Tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
  documents tool schemas, annotations, structured output, validation, sanitization, and human
  confirmation guidance.
- [KiCad Python Board API](https://docs.kicad.org/kicad-python-main/board.html) documents the
  open-board model, read APIs, serialization, and explicit commit grouping for mutations.
- [KiCad PCB S-expression format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/) defines
  segment and via layer/geometry fields used by the internal serializer, which is not invoked here.

## Follow-up gates

Durable routing jobs, multilayer generalization, negotiated congestion, serializer export through
MCP, authoritative DRC in the public response, and apply authority remain separate roadmap items.
Benchmark B-024 records deterministic replay, schema validation, stale CAS refusal, and source
immutability for this first public slice.
