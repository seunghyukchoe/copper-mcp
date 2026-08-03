# ADR-0017: Conservative integer envelopes for diagonal foreign copper

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`
- Related: Roadmap M2, ADR-0011, ADR-0013

## Context

ADR-0011 modeled a foreign-net segment as the rectangle its centreline sweeps, and rejected the
whole board when that model did not apply — that is, whenever a segment was diagonal. The reasoning
was that an axis-aligned rectangle cannot represent a diagonal track without either lying or
over-approximating in a way the cost model would not reflect.

The first half of that has aged badly. Diagonal copper is not exotic; it is what a human router
produces whenever a connection is not axis-aligned. The repository's own CopperTone board carries 30
diagonal `F.Cu` segments across 11 nets, and after ADR-0016 and the polygon-keepout change those
segments became the single refusal standing between two of its nets and a routing attempt.

The second half was already answered by ADR-0013: a conservative polygon envelope with exact integer
containment, inclusive intersection, and rational squared-distance geometry is an honest way to
model a shape the router cannot represent exactly, precisely because the over-approximation errs
toward refusal. What was missing was an envelope construction for a track.

## Decision

A foreign-net selected-layer segment whose axis-aligned model does not apply becomes a conservative
polygon envelope obstacle instead of a board-level refusal.

- The envelope is the **Minkowski sum of the centreline with an axis-aligned square** of the
  segment's half width — equivalently, the convex hull of the two squares centred on the endpoints,
  a hexagon for any diagonal.
- It is **provably a superset** of the real track. A track is its centreline swept with a disc of
  its half width; that disc is inscribed in the square; sweeping a larger shape gives a larger
  region. No numerical argument is required.
- Every vertex is an **exact integer with no rounding step at all**, because the square's corners
  are integer offsets from integer endpoints. The alternative constructions all need an irrational
  unit vector along the segment and therefore a rounding rule that has to be argued correct.
- The margin is unchanged from the orthogonal path: the routed half width plus the stricter of the
  routed and obstacle net-class clearances. Offsetting an envelope that already contains the track
  is a superset of offsetting the track itself, so the inflation rule composes without a separate
  proof.
- Orthogonal foreign segments keep their exact swept-rectangle fast path.
- Envelope vertices charge the obstacle-check budget one per inspected vertex, and each envelope
  counts against `max_obstacles`, exactly as zones and keepouts do.

A diagonal segment **on the routed net** continues to fail closed, and the two cases carry distinct
diagnostics.

## Consequences

- Boards with diagonal copper on other nets are routable instead of refused. A committed
  `diagonal-blocker.kicad_pcb` fixture detours around a diagonal `POWER` track with zero KiCad
  10.0.5 errors, warnings, and unconnected items, and is checked to be discriminating: the straight
  route makes KiCad report `tracks_crossing` as an error.
- The envelope over-approximates the perpendicular extent by at most `(sqrt(2) - 1)` half widths,
  about 41%, worst at 45°. On a dense board that can refuse a corridor a tighter model would allow.
  That is the accepted direction of error, and it is the reason this is an envelope and not a claim
  of exactness.
- Orthogonal segments are untouched, so no board the router already accepted changes geometry or
  identity, and `ROUTER_VERSION` does not move.
- The asymmetry with same-net diagonals is now a load-bearing part of the contract rather than an
  oversight: an **obstacle** may be over-approximated, but **attachment copper** must be
  under-approximated, or the router would claim an electrical connection the board does not have.
  A diagonal has no exact integer inner core yet, so it cannot be attachment copper. Supplying one
  is the natural next slice, and it is what still blocks three CopperTone nets.
- Coverage on CopperTone did not move. Measuring why surfaced an unrelated upstream defect: the
  KiCad adapter applies footprint rotation with the wrong sign, mislocating pads on rotated
  footprints. The routing-baseline coverage section records that the two affected nets' results are
  not currently valid evidence about the router.

## Alternatives considered

- **Keep refusing diagonal foreign copper**: rejected. It is the common case on real boards, and
  ADR-0013 already established that a conservative envelope is an honest model for a shape the
  router cannot represent exactly.
- **Oriented bounding box of the stadium** (a tight 4-gon along the segment axis): rejected. It
  needs the irrational unit vector along the centreline, so every vertex requires an outward
  rounding rule whose correctness has to be argued separately. Marginally tighter, materially
  harder to trust.
- **Octagonal envelope** from axis-aligned and diagonal supporting lines: rejected for the same
  reason — the vertices are rational intersections needing outward rounding, and the tightness gain
  over the swept square is small.
- **Axis-aligned bounding box of the whole segment**: rejected as far too loose. For a long 45°
  track it is nearly a filled square and would refuse most of the board.
- **Decompose the diagonal into orthogonal staircase rectangles**: rejected. It multiplies the
  object count against `max_obstacles` in proportion to length, which turns a geometry question into
  a budget question.
- **Treat same-net diagonals as attachment copper too, using the same envelope**: rejected
  outright. The envelope is an over-approximation; using it for connectivity would assert
  connections that do not exist, which is the one error direction this project never accepts.

## References

- [ADR-0011](0011-existing-copper-obstacles.md)
- [ADR-0013](0013-polygon-zone-obstacles.md)
- [ADR-0016](0016-same-net-attachment.md)
- [Deterministic A* routing baseline](../architecture/routing-baseline.md)
