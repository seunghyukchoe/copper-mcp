# ADR-0012: Through vias as selected-layer obstacles

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

ADR-0011 made selected-layer pads and orthogonal segments into exact obstacles, but kept ADR-0006's
blanket rejection of any board containing a via, on the grounds that a via occupies every layer.
Measuring the router against this repository's own CopperTone board showed what that cost: its nine
vias disqualified all fourteen nets before a single net was examined. The rejection was not
protecting anything — it was the first thing a real board hit.

"Occupies every layer" is a reason to treat a via as an obstacle, not a reason to refuse the board.
Board IR v0.1 admits through vias only, so every via provably crosses whichever layer is being
routed; there is no span ambiguity to get wrong.

## Decision

A via outside the routed net contributes an obstacle rectangle: the bounding box of its outer
diameter, centred on the via, inflated like every other obstacle by the routed half-width plus the
stricter of the routed and obstacle net-class clearances. Vias count against `max_obstacles`.

A via *on* the routed net is rejected as partial routing, matching how ADR-0011 already treats a
same-net segment. Deciding where an existing via may be joined is a routing problem, not an obstacle
problem.

The diameter bounding box over-approximates a round via in exactly the way ADR-0011's round pads do:
it can refuse a route that the true circle would allow, and can never permit a clearance violation.
Drill diameter is deliberately ignored — copper, not the hole, is what a track must clear.

## Consequences

- Vias stop being a board-level veto. On CopperTone the failure mode moved from "nine vias reject
  everything" to per-net diagnostics that describe each net's actual problem.
- Re-measured on that board, the remaining blockers are now precise: nine of fourteen nets have more
  than two `F.Cu` pads, and all five two-pin nets fail on the two `F.Cu` zones. Zones are the next
  blocker, and they need real polygon obstacles — a bounding box around a pour covering most of a
  board would make every route impossible while claiming to be conservative.
- The board still previews zero of fourteen nets. This ADR removes one of three blockers; it does
  not make the router usable on a routed board, and the roadmap should not be read as though it did.
- Obstacle counts grow again, reinforcing that `max_obstacles` needs revisiting before multi-net.

## Alternatives considered

- Keep rejecting boards with vias until multilayer routing exists: rejected because single-layer
  routing around a via is well defined, and the rejection blocked every real board for no gain.
- Use the drill diameter: rejected because the annular copper ring, not the hole, sets clearance.
- Inscribe a rectangle inside the via circle: rejected for the same reason as ADR-0011's round pads —
  it would permit clearance violations.

## References

- [ADR-0006](0006-bounded-deterministic-astar.md)
- [ADR-0011](0011-existing-copper-obstacles.md)
- [Deterministic A* routing baseline](../architecture/routing-baseline.md)
