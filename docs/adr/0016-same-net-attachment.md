# ADR-0016: Attach to existing same-net copper and complete partial routes

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`
- Related: Roadmap M2 "Attachment to existing same-net copper and bounded partial-route completion"
- Correction: the final Consequences bullet lists the CopperTone blockers behind this change as
  octagonal keepouts, then the `GND` zone envelope, then an off-grid pad delta. Only the first was
  measured. Direct measurement after polygon keepouts landed shows foreign-net diagonal segments
  come next, and the remaining two are in the opposite order. Later still, a mirrored
  footprint-rotation defect in the KiCad adapter was found to have contaminated all of those
  CopperTone measurements; with it fixed, the two nets this ADR made attachable report
  `already_connected`. The current measurement lives in the
  [routing baseline](../architecture/routing-baseline.md); the decision itself is unaffected.
- Follow-up: the already-connected outcome this ADR introduced was later extended to nets of
  any pad count, since the component analysis never depended on there being two pads.
  `RouteConnection` gained a `pad_count` field and its invariant generalised accordingly.
  Routing a multi-pin net remains unsupported, so the scope of the routing decision here is
  unchanged; only the recognition half widened.

## Context

ADR-0011 turned foreign-net copper from a rejection into an obstacle, and explicitly rejected the
symmetric move for the routed net: *"Treat same-net existing segments as connectable copper:
rejected because deciding where a partial route may be joined is a routing problem in its own
right, not an obstacle problem."* That was correct scoping at the time. The consequence was a
blanket veto: any selected-layer segment on the routed net returned `unsupported_geometry` with
"the selected net is already partially routed on the selected layer".

The veto is unusually expensive because partially routed nets are the normal state of a real board.
On the repository's own CopperTone board all five two-pin `F.Cu` nets hit it. It is also a
*masking* refusal: because it fires early in preparation, it hides whatever else about the board
the router cannot yet model, so the measured-coverage numbers in ADR-0012 and ADR-0013 attributed
more to this one rule than it deserved.

Deciding where a partial route may be joined is still a routing problem. What has changed is that
Board IR now carries exact integer pad and segment geometry, so the *connectivity* question —
which pieces of copper are already one electrical object — can be answered exactly rather than
approximated.

## Decision

A selected-layer orthogonal segment on the routed net becomes **attachment copper**: never an
obstacle, and a legal place for the proposed route to begin or end.

- **Two rectangle models, erring in opposite directions.** Obstacle rectangles over-approximate
  copper, so a clearance can never be understated. Connectivity rectangles *under*-approximate it,
  because claiming copper that is not there would assert an electrical connection the board does
  not have. A track's connectivity core drops the round end caps and floors the half width; a pad's
  core is the largest axis-aligned rectangle provably inside its shape — the full rectangle for
  `rect`, inset by the corner radius for `roundrect`, the central band for `oval`, and a centre line
  for `circle`. Every core is a subset of the real copper, so overlapping cores prove overlapping
  copper.
- **Exact integer components.** Connectivity is closed rectangle intersection over the two pad cores
  and every same-net selected-layer segment core, resolved by union-find in which the lowest index
  always wins, so a component's root never depends on discovery order. Exact contact counts as a
  connection.
- **A third terminal outcome.** When both pads land in one component the router returns a typed
  `RouteConnection` rather than a candidate or a diagnostic, surfaced by the public preview as the
  new `already_connected` status. This is not a failure and is deliberately not filed under
  `RouteFailureCode`.
- **Multi-source, multi-target search.** Otherwise the search is seeded from every lattice node the
  source component's segment cores cover, and terminates on any node the target component's cores
  cover. Pads contribute only their centre node. The heuristic becomes the Manhattan distance to the
  target bounding box, which stays admissible and consistent and is exactly the previous heuristic
  when there is a single target.
- **The emitted patch remains new segments only.** Attachment is geometric overlap with existing
  same-net copper, which KiCad accepts for the same net.
- Off-axis or diagonal same-net segments, same-net vias, same-net zones, and endpoint pads whose
  shape is not modeled exactly still fail closed. Attachment copper counts against `max_obstacles`
  alongside every obstacle, and component construction charges the obstacle-check budget per exact
  pair comparison and per emitted seed node.
- The component analysis is skipped entirely when the net carries no same-net selected-layer
  segment, so every board the router accepts today is prepared through the identical path.

## Consequences

- A partially routed net completes from its stub instead of being refused, and the proposal is the
  missing piece rather than a redundant parallel run. A committed `partial-route.kicad_pcb` fixture
  adds 10 mm where the equivalent empty board needs 20 mm, and KiCad 10.0.5 reports zero errors,
  zero warnings, and zero unconnected items for the result.
- Because seeding uses pad centres and the target bounding box degenerates to a single node, a board
  with no same-net copper produces byte-identical geometry, costs, and metrics. Only
  `ROUTER_VERSION`, which advances to `astar-grid/0.3.0`, changes candidate identity.
- The public `status` field gains a fourth value. A consumer that switches exhaustively over three
  statuses must add a branch. `include_drc` on an already-connected net is skipped rather than
  failed, because the fail-closed evidence rule protects a proposal and no copper is being proposed.
- The connectivity model can under-connect: a stub touching only the rounded corner of a pad is not
  seen as attached, and the router then proposes a redundant but DRC-clean route. That is the
  intended direction of error.
- If the cheapest attachment is mid-stub rather than at a stub endpoint, the leftover tail becomes
  copper with an unconnected end and KiCad reports `track_dangling` at **warning** severity. This
  does not flip a hard-correctness pass, but it is a real quality consequence of the current model.
- Removing the veto stops it masking other refusals. Re-measured on CopperTone, coverage is
  unchanged at zero of fourteen previewable `F.Cu` nets: nine are multi-pin, three of the five
  two-pin nets carry diagonal same-net copper, and the remaining two are refused by octagonal
  mounting-hole keepouts, then by a board-wide `GND` zone envelope, then by an off-grid pad-centre
  delta. This ADR makes those blockers visible; it does not remove them.
- Two same-net pads that already touch with no track between them are still routed redundantly,
  because component analysis is gated on the presence of a same-net segment. Overlapping footprints
  are a placement question, not a routing one.

## Alternatives considered

- **Keep the veto until multi-pin routing lands**: rejected because the veto is what hides the rest
  of the contract's limits, so leaving it in place keeps the measured numbers dishonest as well as
  the coverage low.
- **Report "already connected" as a new `RouteFailureCode` under `not_routed`**: rejected. That enum
  is documented as a failure taxonomy, and a correct determination that no copper is needed is not
  a failure. Filing it there would be exactly the mislabeling this project refuses elsewhere.
- **Use the existing bounding-box rectangles for connectivity too**: rejected outright. Over-
  approximating for connectivity claims connections that do not exist, and the already-connected
  outcome emits no candidate, so authoritative DRC cannot falsify it. It is the one error direction
  that is never acceptable.
- **Seed from every lattice node beneath a pad**: rejected because it would change the emitted
  geometry of every board that routes today for no benefit this slice needs. Pad entry policy is a
  separate contract.
- **Restrict attachment to segment endpoints**: rejected because a stub endpoint is frequently off
  lattice, which would make attachment work or fail on an accident of the grid step. The cost is the
  dangling-tail warning above.
- **Zero heuristic (Dijkstra) for the multi-target search**: rejected as unnecessarily slow. The
  target bounding box is admissible, consistent, O(1), and reduces to the previous heuristic.
- **Relax the off-grid and blocked-endpoint pre-checks now that a stub can rescue a pad**: deferred.
  Both are now over-strict, but changing them alters the lattice contract and belongs in its own
  decision.

## References

- [ADR-0006](0006-bounded-deterministic-astar.md)
- [ADR-0011](0011-existing-copper-obstacles.md)
- [ADR-0013](0013-polygon-zone-obstacles.md)
- [Deterministic A* routing baseline](../architecture/routing-baseline.md)
