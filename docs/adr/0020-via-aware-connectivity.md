# ADR-0020: Treat same-net through vias as connectivity joints

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`
- Related: ADR-0012, ADR-0016, ADR-0019

## Context

ADR-0012 made a foreign-net via an obstacle instead of a board-level veto, but a via on the
*routed* net stayed a blanket refusal: the router could neither route around it nor reason about it.
That was right while the only question was "can this router build a path", because a via is a layer
change and the search is single-layer.

It became wrong once the router learned to *recognise* existing connections. A via is copper, and a
through via is copper on every layer at its position. A net whose pads are already joined through
one needs no route at all, and refusing to look reports a problem the board does not have. On the
repository's own CopperTone board this was the last gap: three nets stayed refused purely because
they carried vias.

## Decision

A same-net through via is a **connectivity joint**, and connectivity analysis becomes multilayer.

- **Routing is unchanged.** It remains a single-layer contract, and a net that still needs new
  copper while carrying a via keeps its existing refusal. Only the already-connected claim is
  extended.
- **The via's core is the annulus, never the hole.** The drill is not copper, so a square inscribed
  in the outer circle would claim the one region that certainly is not there. The ring is covered by
  four axis-aligned rectangles, one per side. With outer radius `R` floored from the diameter and
  hole radius `r` taken as the *ceiling* of half the drill — so the hole is overstated and never
  encroached — the right rectangle spans `x` in `[r, a]`, `y` in `[-b, b]`, with `b` half the ring
  width and `a = isqrt(R^2 - b^2)`, which makes `a^2 + b^2 <= R^2` in exact integers. The other
  three are quarter-turn rotations.
- **A via's own rectangles are unioned atomically.** The four are mutually disjoint for a real via,
  and that is expected rather than a defect: the annulus is one piece of physical copper joined by a
  plated barrel. Deriving the ring's self-connectivity from rectangle overlap would be modelling the
  model rather than the board.
- **Objects connect only when they share a layer.** Pads carry their layer set, segments their one
  layer, and a through via every copper layer — which is precisely what makes it a joint. Board IR
  admits through vias only and validates that they span the complete copper stack, so blind, buried
  and microvias remain fail-closed at the adapter and never reach this model.
- **Zones still veto the claim.** An unfilled or stale zone cannot prove connectivity, and its fill
  is not trusted; a net carrying a same-net zone is never claimed connected however its vias fall.
- `RouteConnection` gains a `vias` count, and its component invariant becomes
  `component_objects == attachment_segments + pad_count + vias`. A non-zero count is what tells a
  caller the evidence is multilayer even though the request names one layer.

## Consequences

- On CopperTone, `VCC` and `L_OUT` move from refused to `already_connected`, taking the board from
  11 of 14 recognised nets to **13 of 14**. `GND` stays refused, honestly: it carries a same-net
  zone, so nothing here can prove its connectivity.
- The claim is corroborated the same way as before, by board-level KiCad DRC reporting zero
  unconnected items. A committed `via-joint.kicad_pcb` fixture runs a net front stub → via → back
  stub → via → front stub, and the check is discriminating: removing either the via or the
  back-layer stub in a scratch copy makes KiCad report an unconnected item.
- Connectivity is now a multilayer property while routing is a single-layer one. That asymmetry is
  deliberate and worth stating plainly rather than papering over: recognising a connection someone
  else built is a strictly easier problem than building one.
- Copper that touches only the drill hole is not touching the via. A regression test pins that,
  because it is the one place where an over-approximating core would silently invent a connection.

## Alternatives considered

- **Inscribed square of the outer circle**: rejected outright. It covers the drill hole, so it would
  claim contact with copper that ends in the middle of an unplated void.
- **A single rectangle across the ring's width on one axis**: rejected. It models the via as
  directional, so whether a connection is seen would depend on which side the copper approaches
  from.
- **Deriving the annulus's self-connectivity from rectangle overlap**: rejected. The four
  rectangles are disjoint for real geometry, so this would report a via as four separate objects and
  break the very connection the via provides.
- **Extending routing through vias in the same slice**: rejected as a different problem. Multilayer
  path search needs a layer-aware lattice, via insertion cost, and a via-placement contract; none of
  that is required to recognise a connection that already exists.
- **Trusting a zone's cached fill to prove connectivity**: rejected, consistent with ADR-0013.

## References

- [ADR-0012](0012-via-obstacles.md)
- [ADR-0016](0016-same-net-attachment.md)
- [ADR-0019](0019-multi-pin-component-merging.md)
- [Deterministic A* routing baseline](../architecture/routing-baseline.md)
