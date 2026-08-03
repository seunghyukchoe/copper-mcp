# ADR-0018: Chained integer squares as the core of diagonal attachment copper

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`
- Related: ADR-0016, ADR-0017, Roadmap M2

## Context

ADR-0016 established the rule that decides which copper the router may join: an **obstacle** may be
over-approximated, because a larger obstacle only refuses more routes, but **attachment copper** must
be *under*-approximated, because a core that is larger than the real copper would let the router
assert an electrical connection the board does not have. It supplied exact inner cores for
orthogonal tracks and for every pad shape, and left diagonal tracks fail-closed on the routed net —
the one shape with no obvious axis-aligned inner rectangle.

ADR-0017 then gave diagonal *foreign* copper a conservative outer envelope, which is the easy half of
the same problem. That left a visible asymmetry, and it was the first blocker on three of the five
two-pin nets of the repository's own CopperTone board.

The component model is axis-aligned, so what is needed is not a single rectangle but a set of them
that is provably inside a rotated stadium and provably behaves as one connected piece.

## Decision

A diagonal same-net selected-layer track contributes a **chain of axis-aligned squares** to
component analysis and to multi-target seeding.

Let `radius` be the floored half width, so real copper contains every point within `radius` of the
centreline. Centres are placed at `start + (delta * i) // steps` for `i` in `0..steps`, and each
carries a square of half side `s`.

- **Subset.** Flooring each axis moves a centre less than one nanometre per axis off the exact
  centreline point it approximates, so a centre lies within `sqrt(2) < 2` of the segment; the
  constant `_CORE_CENTRE_TOLERANCE_NM = 2` absorbs that. A square of half side `s` reaches at most
  `s * sqrt(2)` beyond its centre, and distance to a set obeys the triangle inequality, so every
  point of every square is within `2 + s * sqrt(2)` of the segment. Choosing `s` to satisfy
  `2 * s^2 <= (radius - 2)^2` makes that at most `radius`. Integer-only: `s = isqrt((radius - 2)^2 // 2)`.
- **Overlap.** `steps` is chosen so consecutive centres differ by at most `2 * s` on each axis,
  which is exactly when two closed squares of half side `s` still touch. The chain is therefore one
  connected component by construction, not by inspection.
- **Endpoints.** `i = 0` and `i = steps` land exactly on the track's endpoints, so a diagonal stub
  reaches the pads it is soldered to and can be attached at its far end.
- **Determinism.** Endpoints are canonically ordered before generation, so a track recorded in
  either direction yields the identical chain; squares are emitted in ascending `i`.
- **Bounded.** Each square charges the shared obstacle-check budget as it is generated, so a track
  too long or too thin for the budget fails closed through the existing ceiling rather than
  allocating without limit. A track whose floored half width does not exceed the tolerance cannot be
  modelled at all and is refused with a distinct diagnostic.

Same-net vias and same-net zones remain fail-closed. Foreign diagonal copper keeps ADR-0017's
hexagonal envelope, unchanged.

## Consequences

- Diagonal copper is no longer a refusal anywhere in the contract: over-approximated as an obstacle,
  under-approximated as attachment. The two constructions are deliberate mirror images, and the pair
  is now the worked example of the project's direction-of-error rule.
- On CopperTone every one of the five two-pin `F.Cu` nets is now handled, all five reporting
  `already_connected`; `kicad-cli pcb drc` corroborates by reporting zero unconnected items. The
  board's entire two-pin surface is resolved, and only multi-pin routing remains for it.
- The chain is coarser than the copper it models: squares have half side about `0.7 * radius`, so a
  stub can be attached only near sampled points, and a chain square may cover no lattice node at
  all on a coarse grid. The failure direction is under-connection — a redundant but legal route —
  never a claimed connection that does not exist.
- Component analysis is pairwise over rectangles, so one diagonal now contributes many rectangles
  rather than one. The cost is quadratic in chain length and is charged against the same
  obstacle-check budget; long diagonals on a tight budget will refuse rather than run.
- Boards with diagonal same-net copper were previously refused outright, so no board the router
  already accepted changes geometry or identity, and `ROUTER_VERSION` does not move.

## Alternatives considered

- **Sample the integer lattice points that lie exactly on the centreline** (`gcd`-spaced): rejected.
  For a primitive direction vector there are only two such points — the endpoints — so the chain
  would be disconnected for most real tracks. Exactness of the centres is worthless without a bound
  on their spacing.
- **Step one nanometre at a time along the dominant axis**: rejected. It keeps the deviation below
  one nanometre but needs millions of squares for a ten-millimetre track, which the obstacle budget
  correctly refuses.
- **Rotate the whole component model to the track's axis**: rejected. It would require rational or
  irrational coordinates throughout a model whose exactness is its main safety property.
- **Use ADR-0017's outer envelope for attachment too**: rejected outright. It is an
  over-approximation; using it for connectivity would assert connections that do not exist, which is
  the specific error this ADR exists to prevent.
- **Keep diagonals fail-closed and require the user to reroute them orthogonally**: rejected.
  Diagonal copper is what human routers produce, and refusing it made the router useless on exactly
  the boards it was meant to help.

## References

- [ADR-0016](0016-same-net-attachment.md)
- [ADR-0017](0017-diagonal-segment-envelopes.md)
- [Deterministic A* routing baseline](../architecture/routing-baseline.md)
