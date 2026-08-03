# ADR-0013: Conservative polygon zone-boundary obstacles

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

ADR-0012 removed vias as a board-level veto and exposed the next blanket rejection: every copper
zone on the selected layer stopped routing before its geometry was examined. Replacing a zone with
its bounding box is not useful. A concave outline may contain a legal corridor, while a full-board
pour's box covers almost the entire routing area.

KiCad gives the zone outline and filled copper different meanings. The outline is the maximum fill
extent; fill is derived state that changes around pads, tracks, other zones, and board rules. Board
IR v0.1 deliberately preserves the simple solid-zone outline and intent but does not make cached
`filled_polygon` data authoritative. Candidate DRC also does not refill or save zones. The router
therefore needs a model that cannot permit a short even when the cached fill is absent or stale.

## Decision

A foreign-net zone on the selected layer is a conservative **zone-boundary envelope obstacle**. The
entire simple polygon interior is treated as potentially occupied copper. This is an
over-approximation of any valid fill clipped to that outline: it may refuse a physically legal
route through an unfilled void, but it must not permit a route through copper that could exist.

The forbidden centreline offset is open and uses:

```text
ceil(route_width / 2)
+ max(route_net_class_clearance,
      zone_net_class_clearance,
      zone.clearance_nm)
```

The larger conflicting clearance governs, matching KiCad's documented zone behavior. A centreline
at exactly that distance is legal; one nanometre closer is not.

The geometry kernel uses Python integers only. It stores the original ring and raw bounds rather
than materializing an offset polygon, then combines:

- an expanded bounding box used only to prune exact checks;
- even-odd containment for a point in a simple polygon;
- inclusive segment intersection; and
- rational squared point-to-segment comparisons, including
  `cross² < distance² × edge_length²` when the perpendicular projection lies on an edge.

Segment-to-polygon distance checks all four endpoint-to-segment relations after ruling out an
intersection. No float, square root, tessellation, external geometry dependency, or under-sized
approximation participates. Concave and diagonal zone boundaries are supported.

One zone counts once against `max_obstacles`. Every vertex inspected while building its bounds,
every expanded-bounds relation, and every polygon-edge relation counts against
`max_obstacle_checks`; cancellation remains observable at least every 64 checks. A* and the
benchmark-only Dijkstra oracle share the same preparation, legality, and proximity predicates.

A same-net selected-layer zone still returns `unsupported_geometry` as partial routing. Connecting
to existing same-net copper requires an attachment and connectivity contract, not an obstacle
exception. A zone on another layer is ignored. Zone priority, thermal, thickness, and island
settings can change copper *inside* the outline but cannot expand it, so they do not weaken this
conservative envelope.

The implementation version advances to `astar-grid/0.2.0`. The
`orthogonal-a-star-v1` policy remains unchanged because the search state, objective, and ordering do
not change for previously accepted inputs.

## Consequences

- Small, concave, and diagonal foreign-net zone outlines no longer cause a blanket unsupported
  result. Exact tests cover deterministic detours, a route through a concave notch that a bounding
  box would reject, complete grid-edge crossings, exact and one-nanometre boundaries, a diagonal
  3:4:5 distance case, all three clearance sources, budgets, cancellation, other-layer handling,
  same-net rejection, and A*/Dijkstra cost agreement. A committed KiCad fixture also verifies that
  an outline-only foreign zone converts and produces the same read-only preview twice without
  touching its workspace.
- This does **not** make cached fill authoritative and does not claim that the outline is physical
  copper. Useful routing through fill voids needs a separate freshness-bound KiCad refill/fill
  digest contract.
- CopperTone remains at zero previewable `F.Cu` nets. Re-measurement found nine multi-pin nets and
  five two-pin nets that already carry same-net `F.Cu` segments. Its foreign GND zone outline also
  spans `(0.5, 0.5)`–`(51.5, 29.5)` mm, so the conservative envelope would remain a full-board
  blocker after partial-route support. No real-board routing improvement is claimed.
- Polygon relation work scales with ring vertices, so charging bounds and edge scans to the existing
  obstacle-check ceiling is part of correctness, not only an optimization.

## Alternatives considered

- Trust KiCad `filled_polygon`: rejected because Board IR does not bind that cache to a verified
  refill revision and candidate DRC intentionally does not refill zones.
- Use the zone bounding box as the obstacle: rejected because it destroys legal corridors in
  concave zones and degenerates to a board-wide veto for common pours.
- Ignore zones and rely on later DRC: rejected because candidate generation must not propose copper
  through known potential copper and DRC is a validation gate, not the geometry engine.
- Treat same-net zones as already connected: rejected because attachment, priority, fill, and
  connectivity semantics are not yet modeled.

## References

- [KiCad 10 PCB Editor: working with zones](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#working_with_zones)
- [KiCad board file format: zones and polygon coordinates](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/)
- [KiCad 10.0.5 zone filler clips final copper to maximum extents](https://github.com/KiCad/kicad-source-mirror/blob/10.0.5/pcbnew/zone_filler.cpp#L2837-L2849)
- [ADR-0005](0005-canonical-board-ir.md)
- [ADR-0006](0006-bounded-deterministic-astar.md)
- [ADR-0012](0012-via-obstacles.md)
- [Deterministic A* routing baseline](../architecture/routing-baseline.md)
