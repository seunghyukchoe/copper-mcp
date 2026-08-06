# ADR-0076: Assemble the board outline from Edge.Cuts segments, and never repair it

- Status: Accepted
- Date: 2026-08-06
- Owners: CopperMCP maintainers
- Related: [ADR-0065](0065-orthogonal-courtyard-chains.md); [ADR-0072](0072-conservative-arc-track-envelopes.md);
  [ADR-0017](0017-diagonal-segment-envelopes.md);
  [Edge.Cuts outline assembly research](../research/edge-cuts-outline-assembly-v1.md); issue #111

## Context

The Board IR adapter accepted exactly one root graphic on `Edge.Cuts`: a single unfilled `gr_rect`.
Every other `Edge.Cuts` primitive — including the `gr_line` segments KiCad writes whenever an
outline is drawn with the line tool, which is most outlines and every non-rectangular one — was
refused with `unsupported.construct`. A real four-layer board carries four `gr_line` expressions on
`Edge.Cuts` and no `gr_rect`, so it was refused for having an ordinary board shape.

`gr_rect` is not the general case. It is what KiCad emits for one tool. Nothing in the file format
marks which shapes constitute the outline, in what order, or in which direction; the outline is a
property KiCad derives by chaining shape endpoints, and the shapes are an unordered set drawn in
arbitrary directions (research note, findings 1 and 2).

This is the last of the three stacked blockers found by running the tool against a real board
rather than a fixture. The other two were #104 (copper stack numbering, D-153) and #112 (parse node
budget, still open).

## Decision

**An `Edge.Cuts` outline drawn as `gr_line` segments is accepted when those segments chain, by
exact endpoint coincidence, into exactly one closed simple loop. Nothing about a malformed outline
is ever repaired.**

The direction of error is the crux, and it inverts relative to every obstacle decision this project
has taken. ADR-0017 and ADR-0072 over-approximate an obstacle, because a larger obstacle can only
make the router refuse more. **The board outline is routing *room*, so it may only be
under-approximated**: a modelled outline larger than the drawn one hands the router area the
fabricated board does not have, which is a fabrication-affecting error rather than a conservative
one. Assembled from straight segments joined at exactly coincident endpoints, the ring's vertices
*are* the drawn endpoints and nothing is synthesized, so containment holds with equality — the
strongest available form of "never larger", and the same property the `gr_rect` path already had.

Concretely:

- Endpoints must coincide **exactly**, on the integer nanometre grid the format's six-decimal
  millimetre resolution already guarantees. KiCad chains its own outline with a non-zero
  `aChainingEpsilon` and will close a sub-tolerance gap for you; CopperMCP refuses a near-miss
  instead, because closing a gap adds area no drawn segment encloses. A 10 µm gap — comfortably
  inside KiCad's tolerance — is a typed refusal here.
- Every predicate is exact integer arithmetic. No floats, no tolerance, no rounding rule, because
  none is needed once the vertices are drawn points.
- Malformations each refuse with a typed diagnostic and are never repaired: a zero-length segment,
  a duplicate segment, a vertex of degree other than two (open contour or branching spur), a second
  disjoint loop, a self-intersection, and a ring that exhausts a budget. Each of these has more than
  one plausible "helpful" repair — snap the gap, drop the spur, keep the biggest loop — and every
  one of those repairs invents board. The set of malformations is not invented here either: it is
  KiCad's own list from `ConvertOutlineToPolygon`, plus the branching and duplicate cases that fall
  out of assembling a graph rather than a path.
- Work is bounded. The segment count charges `max_vertices_per_ring` before any adjacency is built,
  and the quadratic ring-simplicity test charges `max_intersection_tests`, so a pathological outline
  exhausts a declared budget with `budget.exceeded` rather than running long.
- Ordering and drawing direction are not inputs to the result. The traversal starts at the
  lexicographically least vertex, and canonicalization already normalizes ring winding and start
  vertex, so a scrambled or re-wound segment list yields the same ring.
- A contour assembled from many segments has no single native KiCad identity, so it takes a
  **derived** identity from the source revision and a fixed locator, exactly as any other object
  without a `uuid`. It deliberately does not adopt the `uuid` of any one segment, which would name a
  segment while claiming to name the contour.

**Arcs, circles, polygons and curves on `Edge.Cuts` stay refused**, with a diagnostic that names the
curve rather than reporting the same message as an unsupported layer. This is a deferral with a
reason, not an oversight: ADR-0072's conservative sagitta bound is an *upper* bound, which is the
wrong direction for an outline, and a chord is inscribed only when the arc bulges away from the
board interior — an arc bulging inward gains back the area it bites out. Deciding the bulge side
against the assembled ring's orientation, and subdividing a large-radius arc without exact integer
points on it, is its own slice with its own proof obligations.

## Consequences

- Ordinary boards convert. A rectangular outline drawn as four segments, and a non-rectangular
  (L-shaped) one, both produce a snapshot; the real four-layer board's own four `Edge.Cuts`
  segments assemble into its exact 159 × 150 mm rectangle. That board still refuses overall, on
  four unrelated constructs (see the ledger row), which is what makes this an additive widening
  rather than a claim that the board is supported.
- **No Board IR field, schema, digest, or diagnostic code changes.** `OutlineContour` and `Ring` are
  unchanged, no new diagnostic code is introduced — the refusals reuse `geometry.invalid`,
  `unsupported.topology`, `unsupported.construct`, `geometry.self_intersection` and
  `budget.exceeded` — and every pinned identity in `tests/test_golden_identities.py` is unaffected.
  A board that converted before converts to byte-identical content.
- The documented Board IR subset changes: `docs/architecture/board-ir.md` no longer says the only
  accepted `Edge.Cuts` primitive is one unfilled rectangle. That is the contract change this record
  exists for.
- Two previously-stale refusal messages are corrected in passing: "root graphic on copper or
  Edge.Cuts is unsupported" now says copper (the Edge.Cuts case has its own message), and "Board IR
  adapter v0.1 accepts rectangular Edge.Cuts only" no longer describes the contract.
- A board whose outline KiCad accepts under its chaining epsilon but whose endpoints do not exactly
  coincide is refused here, and the user has to close the gap in KiCad. Recorded as R-117.

## Alternatives considered

**Adopt KiCad's chaining epsilon.** Rejected. It would accept more real boards, and it would do so
by adding area the board does not enclose — the one direction of error this contour may not take.
The refusal names the malformation, so the user can fix it in the editor, where the fix is real.

**Repair the outline: close small gaps, drop spurs, or keep the largest loop.** Rejected for the
same reason, and for a second one: each malformation admits several repairs that disagree, so
picking one is a guess presented as a result.

**Normalize collinear vertices away.** Rejected. A user who splits an edge into two segments has
drawn two segments; keeping both vertices is exact, and dropping them is a normalization whose only
benefit is a shorter vertex list.

**Include arcs now, by chord replacement, reusing ADR-0072's machinery.** Rejected — see above. The
bound in ADR-0072 is conservative in the obstacle direction, and reusing it here would silently
over-approximate the board.

**Ship an `Edge.Cuts` polygon (`gr_poly`) path as well.** Deferred rather than rejected. It is a
closed shape needing no chaining, so it is a smaller problem than arcs, but it is not what the
motivating board draws and it carries its own fill-and-winding questions.
