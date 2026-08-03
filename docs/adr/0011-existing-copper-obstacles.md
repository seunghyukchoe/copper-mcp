# ADR-0011: Model existing copper as exact rectangular obstacles

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

ADR-0006 rejected any board carrying selected-layer copper or a pad outside the routed net, with the
reasoning that approximating their clearance would create a false correctness claim. That was the
right call while there was no way to check the result. It also meant the router only worked on empty
synthetic fixtures: every real board has copper on it, so ADR-0009's public preview could not be
pointed at anything a user actually cares about.

Two things changed since. ADR-0005's Board IR carries exact integer pad, segment, and net-class
geometry, and ADR-0008 can hand a replayed candidate to authoritative KiCad DRC. There is now both a
model precise enough to reason with and an oracle able to falsify it.

## Decision

Selected-layer copper outside the routed net becomes an exact axis-aligned rectangular obstacle
rather than a rejection.

- **Pads** not on the routed net contribute a rectangle centred on the pad. Sizes swap on a quarter
  turn; rotations that are not a multiple of 90° are rejected.
- **Segments** not on the routed net contribute the rectangle their centreline sweeps, grown by the
  ceiling of half the segment width. Diagonal segments are rejected.
- Each obstacle is inflated by the ceiling of half the routed track width plus the **stricter** of
  the routed net's class clearance and the obstacle net's class clearance, so a board mixing net
  classes cannot be routed to the looser rule.
- Every obstacle — keepout, pad, and segment alike — counts against `max_obstacles`.

The following still fail closed, because a rectangle cannot represent them without either lying or
silently over-approximating in a way the cost model would not reflect: arcs and zones on the
selected layer, vias (which occupy every layer), off-axis pad rotations, diagonal segments, and a
selected net that is already partially routed.

Non-rectangular pad shapes — circle, oval, roundrect — use their bounding box. That is a deliberate
over-approximation: it can refuse a route that a rounder shape would physically allow, but it can
never permit a clearance violation.

## Consequences

- Preview now works on boards with existing copper, which is the difference between a demo and a
  tool. The synthetic two-pad fixture is no longer the only board that routes.
- The benchmark Dijkstra oracle shares the obstacle model, so optimality comparison still holds;
  tests assert A* and Dijkstra agree on cost for pad, segment, and mixed-obstacle boards.
- A committed `blocked-pad.kicad_pcb` fixture places a 2 mm × 8 mm foreign-net pad between the
  endpoints. The router detours around it, and a KiCad 10.0.5 integration test asserts the resulting
  board reports zero DRC errors and zero unconnected items. The obstacle model is checked against
  the authoritative tool, not only against itself.
- Obstacle count now scales with board content rather than with declared keepouts, so
  `max_obstacles` becomes a load-bearing budget on real boards and its default will need revisiting
  once multi-net routing lands.
- Bounding-box handling of round shapes means the router can report `no_path` on a board where a
  human would find a route. That is the intended direction of error and is documented as such.
- This does not make the router a general autorouter. One net, two pads, one layer, no vias, and no
  rip-up remain the contract.

## Alternatives considered

- Keep rejecting existing copper until exact non-rectangular geometry exists: rejected because it
  indefinitely blocks every real board for a purity that the bounding box already satisfies in the
  safe direction.
- Under-approximate round pads with an inscribed rectangle: rejected outright — it would permit
  clearance violations, which is the one error direction that is never acceptable.
- Use only the routed net's clearance for every obstacle: rejected because a board mixing net
  classes would be routed to the looser rule and KiCad would disagree.
- Treat same-net existing segments as connectable copper: rejected because deciding where a partial
  route may be joined is a routing problem in its own right, not an obstacle problem.

## References

- [ADR-0005](0005-canonical-board-ir.md)
- [ADR-0006](0006-bounded-deterministic-astar.md)
- [ADR-0008](0008-candidate-bound-kicad-drc.md)
- [Deterministic A* routing baseline](../architecture/routing-baseline.md)
