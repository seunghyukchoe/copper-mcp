# ADR-0035: Keep the layered A* search seam internal until board fidelity exists

Date: 2026-08-04

## Context

FreeRouting's maze search can cross layers through drills and assigns a positive cost to those
transitions. CopperMCP's production route contract is intentionally narrower: `RouteRequest`,
`RoutePatch`, and the KiCad serializer currently describe one signal layer and orthogonal
segments. Extending those public contracts before the geometry and serialization rules are
complete would make an abstract path look like a KiCad-valid candidate.

## Decision

Add an internal-only, integer `LayeredAStar` oracle over a two-layer lattice. It supports cardinal
moves, explicit layer transitions, deterministic tie-breaking, positive move/via costs,
per-layer rectangular cell obstacles, cancellation, and separate node/expansion/obstacle budgets.
Its immutable result types are not exported through `routing/__init__.py`, MCP, CLI, Board IR, or
the apply path. The existing single-layer router and candidate IDs remain unchanged.

## Required follow-up before production routing

The oracle is not a board router. A future adapter must bind typed Board IR layer IDs and net/pad
ownership, nanometre origins and grid attachment sets, trace width and clearance, edge geometry,
via diameter/drill and keepout rules, through-via stack legality, source-preserving segment/via
serialization, Board IR round-trip equality, and authoritative KiCad DRC. Negotiated congestion,
rip-up, multi-net scheduling, and live mutation remain separate milestones.

## Evidence

- `tests/test_layered_astar.py` covers via-required completion, via-cost choice, layer-scoped
  obstacles, deterministic replay, stale/invalid/cancellation/resource failures, and blocked
  terminals.
- B-017 records deterministic replay and an independent Dijkstra differential check for this abstract
  lattice only.
- FreeRouting primary sources: [architecture](https://raw.githubusercontent.com/freerouting/freerouting/master/docs/architecture.md),
  [maze search](https://raw.githubusercontent.com/freerouting/freerouting/master/src/main/java/app/freerouting/autoroute/MazeSearchAlgo.java),
  and [batch autorouter](https://raw.githubusercontent.com/freerouting/freerouting/master/src/main/java/app/freerouting/autoroute/BatchAutorouter.java).
