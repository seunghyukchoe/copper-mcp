# Ordered-layer routing v1

**Snapshot date:** 2026-08-05

## Evidence and design

KiCad's official PCB S-expression format gives each copper layer an ordinal and defines a via by
the canonical layer pair it connects. That pair identifies the span; it does not encode a
direction, and the serializer emits the canonical outer ordering regardless of how the route
reached the via. The current Board IR intentionally represents only full-stack through vias, so
this increment uses the widest truthful subset: 2..8 ordered signal layers and one full-stack
transition cost. Its per-layer state and obstacles are a deterministic finite graph; the
Manhattan-plus-one-transition heuristic is admissible because every permitted move and via has a
positive stated cost.

A finite via cap makes the coordinate alone an unsound dominance key: a cheaper arrival can have
exhausted the budget while a more expensive arrival at the same coordinate can still complete. The
capped search therefore carries the via count in its state, and the recorded differential is an
exact `(x, y, layer, vias_used)` uniform-cost oracle rather than a coordinate-only one.

Hart, Nilsson, and Raphael provide the primary A* minimum-cost-path basis. Lee's foundational maze
routing work is the historical routing counterpart. Neither source proves KiCad clearance,
padstack, serialization, or manufacturing behavior, so the result is strictly candidate-only.

## Sources

- KiCad PCB file format, layers and track-via semantics:
  https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/
- KiCad Python Board API, including layer count, padstack-presence, and via properties:
  https://docs.kicad.org/kicad-python-main/board.html
- Hart, Nilsson, Raphael (1968), *A Formal Basis for the Heuristic Determination of Minimum Cost Paths*:
  https://doi.org/10.1109/TSSC.1968.300136
- Lee (1961), *An Algorithm for Path Connections and Its Applications*, cited by the DAC routing record:
  https://doi.org/10.1145/800260.809014

## Unclosed gates

The proof does not cover blind/buried/microvias, mixed/plane routing layers, per-span costs,
source-preserving generalized KiCad bytes, zone refill, DRC, public MCP contracts, or application.
