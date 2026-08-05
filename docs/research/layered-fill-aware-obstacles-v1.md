# Layered fill-aware routing obstacles v1

**Snapshot date:** 2026-08-06

This note supports [ADR-0070](../adr/0070-layered-fill-aware-obstacles.md). It records how
production routers treat poured copper during routing, why the ordered-layer proposal seam kept a
zone-outline envelope after [ADR-0039](../adr/0039-fill-aware-routing-obstacles.md) had already
tightened the single-layer core, and what has to be *proved* before an obstacle is allowed to
shrink. No external code is copied.

## What production routers actually do with a pour

**Freerouting ignores pours for search purposes.** The upstream feature request
[freerouting#152](https://github.com/freerouting/freerouting/issues/152) records that a copper pour
is imported as a `ConductionArea` with a shape fixed at import time and that the maze search, the
rip-up scheduler, and the optimizer all operate on pad-to-pad connections instead. The reporter's
observation is that "the autorouter is unaware of these pours"; the practical advice in the same
thread is to exclude the poured net from routing and finish it by hand. So the strongest open
whole-board baseline does not offer a fill-aware obstacle model to copy — it offers a documented
absence, plus the reason the absence is tolerable there (the pour's net is simply removed from the
routing problem).

**KiCad's interactive router works from the same derived geometry we do.** KiCad's PCB Editor
documentation defines a zone as an outline polygon plus optional hole polygons, and states that the
filled copper is computed from that outline; the interactive router walks around obstacles it
cannot move and shoves the ones it can
([KiCad PCB Editor](https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html),
[interactive router source doc](https://github.com/KiCad/kicad-doc/blob/master/src/pcbnew/pcbnew_interactive_router.adoc)).
The filled geometry itself is produced by `ZONE_FILLER`
([class reference](https://docs.kicad.org/doxygen/classZONE__FILLER.html)) and persisted as
`(filled_polygon (layer …) (pts …))` inside the zone
([S-expression format](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html)). Two
consequences matter here. First, the exact copper a router should avoid is the *filled polygon set*,
not the outline — an outline with a large keepout window is mostly not copper. Second, that polygon
set is **derived and cached**: it is whatever the last fill produced, and the file format offers no
guarantee that it still matches the outline, the clearances, or the surrounding copper. KiCad's own
router avoids the problem by living inside a process that can refill on demand; a file-backed,
pure-function router cannot.

**The academic literature treats obstacle approximation as a one-way street.** Multi-layer maze and
line-search routers uniformly approximate awkward obstacle shapes *outward* — non-polygonal
obstacles are replaced by enclosing quadrilaterals so every collision test stays uniform
([3D LineExplore, *Scientific Reports*](https://www.nature.com/articles/s41598-026-36925-0)), and
the recorded failure mode in obstacle-avoiding Steiner work is precisely the opposite direction:
restricting attention to obstacles inside a bounding box "may result in invalid solutions"
([multi-layer OARSMT survey](https://link.springer.com/article/10.1007/s44443-025-00227-8)). The
literature's discipline is therefore the same as this project's: a router may make an obstacle
bigger for free, and may only make it smaller against proof.

## Why the layered seam still used the envelope

[ADR-0021](../adr/0021-zone-fill-authority.md) established the refill authority: KiCad refills a
private disposable copy and the canonical island geometry must equal the geometry captured from the
cache, so an accepted `fill_authority` record is a proof that the cached islands are what KiCad
recomputes from *this* board revision. [ADR-0039](../adr/0039-fill-aware-routing-obstacles.md) spent
that proof in the single-layer A* core: a foreign zone with at least one verified island stops
contributing its outline envelope and contributes exact polygon islands instead.

The ordered-layer adapter ([ADR-0036](../adr/0036-board-ir-layered-proposal-adapter.md),
[ADR-0068](../adr/0068-bounded-ordered-layer-routing.md)) was built independently and never received
that plumbing. Its own research note lists "zone refill" among the unclosed gates
([ordered-layer routing v1](./ordered-layer-routing-v1.md)). Concretely, it derives one axis-aligned
envelope from each foreign zone's *boundary bounding box* and inflates it by the governing
clearance, on both the track and the via obstacle sets. A four-layer board with a ground pour on an
inner layer therefore loses that layer entirely, even when the pour has a routing window the fill
already respects — which is the failure the issue describes.

## Design chosen, and its direction of error

The adapter gains an optional `verified_fill` tuple carrying the same `VerifiedFill` value the
single-layer core already accepts, so one caller-side freshness proof serves both routers and no
second evidence contract is invented. Preparation then applies four rules, in order:

1. **Revision binding.** An island whose `source_revision` differs from the snapshot's source
   revision is refused (`stale_revision`). Evidence proved against another board is not evidence.
2. **Zone backing.** An island with no Board IR zone of the same `(net_id, layer_id)` is refused
   (`unsupported_geometry`). An orphaned island cannot replace an envelope that does not exist, and
   accepting it would let a caller delete copper by naming it.
3. **Containment.** An island whose bounding box is not contained in the bounding box of a backing
   zone is refused (`unsupported_geometry`). KiCad clips fill to the outline, so this is a property
   the evidence should already have; checking it converts the soundness argument below from an
   assumption about KiCad into a verified precondition of the shrink.
4. **Replacement.** Only then does a foreign `(net_id, layer_id)` with at least one verified island
   stop emitting its outline envelope, and each of its islands emit a track envelope and a via
   envelope instead.

Islands are polygons; the layered lattice model is rectangular by construction, so each island is
carried as its bounding box rather than as an exact polygon. That is deliberate and it is the
conservative choice: `island ⊆ island bbox`, so the emitted obstacle still over-approximates the
real copper. The shrink is bounded by rule 3, which gives `island bbox ⊆ zone bbox` for every
island, hence `⋃ island bboxes ⊆ zone bbox` — the replacement can only ever remove blocked area,
never add reachable area that the conservative model had also blocked for some other reason. The
clearance inflation is unchanged: `max(net-class clearance, zone clearance, foreign-net clearance)`,
plus candidate half-width for tracks and candidate via radius for vias, all in exact integer
nanometres. Unverified zones, zones on unsupported layers, and any request that trips rules 1–3
keep the envelope or refuse outright, so every failure direction remains refusal-side.

This is narrower than the single-layer core on purpose. The single-layer core keeps exact polygon
obstacles because its obstacle model is polygonal; the layered core would have to grow a polygon
collision kernel and a matching obstacle-check budget to do the same, which is a separate change
with its own evidence. Bounding boxes capture the routing windows this issue is about — an inner
plane with a fill void — without touching the search kernel.

## Measurable acceptance criterion

Predeclared, on an independently generated four-pad-free synthetic two-layer fixture with a foreign
inner-layer pour whose verified fill leaves a lower corridor:

| Measurement | Before | Required after |
| --- | --- | --- |
| Conservative layered route (no fill evidence) | via detour to the far layer | unchanged |
| Fill-aware layered route | not evaluable | strictly lower total cost, zero vias |
| Island proved on another revision | not evaluable | `stale_revision` |
| Island with no backing zone | not evaluable | `unsupported_geometry` |
| Island wider than its backing zone | not evaluable | `unsupported_geometry` |
| KiCad invoked | no | no |

The replay artifact is B-086. It is evidence for this exact bounded adapter subset, not for
arbitrary pours, hole-carrying zones, exact polygon layered collision, whole-board completion,
electrical behaviour, DFM, fabrication readiness, or Freerouting parity.

## Sources

- Freerouting copper-pour awareness feature request:
  https://github.com/freerouting/freerouting/issues/152
- Freerouting project documentation and repository:
  https://freerouting.org/ and https://github.com/freerouting/freerouting
- KiCad PCB Editor manual, zones and interactive routing:
  https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html
- KiCad interactive-router documentation source:
  https://github.com/KiCad/kicad-doc/blob/master/src/pcbnew/pcbnew_interactive_router.adoc
- KiCad `ZONE_FILLER` class reference:
  https://docs.kicad.org/doxygen/classZONE__FILLER.html
- KiCad S-expression file format, zone `filled_polygon`:
  https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html
- 3D LineExplore, multi-layer PCB geometric routing (obstacle quadrilateral approximation):
  https://www.nature.com/articles/s41598-026-36925-0
- Multi-layer obstacle-avoiding rectilinear Steiner minimal tree survey (bounding-box validity):
  https://link.springer.com/article/10.1007/s44443-025-00227-8

## Unclosed gates

Exact polygon collision on the layered lattice, zone holes modelled as anything other than filled
area, blind/buried/microvia spans, same-net poured attachment in the layered seam, a public
`preview_layered_route` fill-authority contract with a `routing_effect` label of the kind
[ADR-0040](../adr/0040-public-fill-routing-provenance.md) defined for the single-layer preview,
durable layered export, and any KiCad-side proof beyond what ADR-0021 already records.
