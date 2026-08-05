# ADR-0037: Render layered route proposals into disposable KiCad bytes

Status: Accepted

Date: 2026-08-05

## Context

The Board IR-bound two-layer router now emits immutable candidates with physical trace width,
through-via dimensions, per-layer paths, and revision-bound identity. The existing KiCad route
serializer is intentionally single-layer, so treating a layered candidate as renderable would
silently drop vias or collapse layer transitions.

KiCad's board S-expression represents a segment with start/end, width, layer, net, and identity;
a through-via carries its center, diameter, drill, full layer span, net, and identity. The Board IR
adapter canonicalizes a through-via's layer span by copper-stack order. See the [KiCad board file
format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/) and [KiCad Python board API](https://docs.kicad.org/kicad-python-main/board.html).

## Decision

Add a pure `render_kicad_layered_candidate_board` adapter with a required original
`LayeredRouteRequest`. The request is replayed through `LayeredBoardRouter` and must reproduce the
candidate before rendering. The adapter:

- accepts exactly two signal layers and full-stack through-vias;
- appends deterministic, collision-checked UUID-bearing segments and vias to a disposable copy;
- emits via layer names in physical stack order even when the route transition is reversed;
- preserves unknown source expressions and rewrites only disposable writer metadata;
- reparses the result and requires Board IR equality with the source plus the candidate geometry;
- remains candidate-only: it does not write files, invoke KiCad, run DRC/fill, expose MCP, or mint
  apply authority.

## Consequences

Layered candidates now have a source-preserving serialization and Board IR replay gate. This
closes one physical-fidelity prerequisite for routed-through-via work without overstating safety:
authoritative KiCad DRC, a public MCP proposal surface, and any mutating apply transaction remain
separate roadmap gates. Request replay is mandatory because the candidate does not itself carry
the grid and every endpoint-policy input needed to reproduce the search.
