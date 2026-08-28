# ADR-0125: Stray footprint copper is bounded by a box because no fill rule is written down

- Status: Accepted
- Date: 2026-08-28
- Owners: `@seunghyukchoe`
- Related: [Issue #188](https://github.com/seunghyukchoe/copper-mcp/issues/188),
  [D-230](../ledgers/decision-ledger.md), [R-181](../ledgers/risk-register.md),
  [SEC-167](../ledgers/security-ledger.md),
  [B-136](../ledgers/benchmark-ledger.md) and B-137,
  [ADR-0092](0092-net-tie-copper-as-netless-obstacle.md) (the special case this generalizes),
  [ADR-0078](0078-netless-copper-as-obstacle.md) (the `net_id None` contract),
  [ADR-0072](0072-conservative-arc-track-envelopes.md) (the obstacle direction),
  ADR-0026 (why a derived identity refuses write-back),
  [ADR-0095](0095-copper-text-has-no-derivable-envelope.md) (the sibling that stays refused),
  [ADR-0075](0075-courtyard-oracle-parity.md) and
  [ADR-0080](0080-chamfered-and-circular-courtyards.md) (the parity surfaces this must not
  disturb),
  [ADR-0123](0123-a-container-refusal-that-names-no-field-is-the-defect.md) (the naming rule
  applied one level down),
  [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md) (invoked and found not to
  apply)

## Context

[B-133](../ledgers/benchmark-ledger.md) left six boards on three refusals that already name their
construct, and recorded **two** of them stopping at `footprint graphic on a copper layer is
unmodelled copper`. That refusal's own docstring said what it was waiting for:

> a conservative envelope would be admissible here (ADR-0072's direction), but no real board
> surveyed carries one, so inventing the envelope would be modelling a case that has not been
> observed.

[B-136](../ledgers/benchmark-ledger.md) is that observation. Across 102 footprints the two boards
write 34,208 layer-bearing graphics, of which **56** sit on a declared copper layer, and the 56
are one kind: a filled `fp_poly` with an explicit non-zero stroke, in a closed five-child grammar,
none of it net-tie copper. Five other graphic kinds are present in quantity — 32,532 `fp_line`,
1,064 `property`, 243 `fp_rect`, 184 `fp_text`, 50 `fp_circle`, 23 `fp_arc` — and **not one** of
them is on copper.

So the case is observed. What B-136 also found is that it is not the case anyone would have
guessed:

- **`all_distinct` is 0 of 56.** Every one of these polygons repeats a vertex — 30 only the
  closing one, 26 an interior one. A Board IR `Ring` rejects a repeated vertex outright, so *no
  exact region model is expressible* for a single one of them.
- **26 of 56 are self-intersecting.** A self-intersecting outline does not determine a filled
  region on its own: even-odd and non-zero winding fill different sets, and the document never
  says which. KiCad resolves it in `SHAPE_POLY_SET`; the `.kicad_pcb` file does not carry the
  answer, and an adapter that picked one would be asserting something the source does not say.

That second finding is the decision. It rules out the tighter models *on soundness grounds* rather
than on effort: a convex hull, a triangulation and a scanline decomposition are all models of "the
filled region", and there is no filled region until a fill rule is chosen.

The cost of the coarse model was measured rather than assumed. Every one of the 56 envelopes
covers under **5%** of its board's `Edge.Cuts` bounding box, and each board's whole envelope union
— computed exactly by coordinate compression, not summed — is also under 5%. The 4 carrier
footprints are all padless: this is artwork, not part geometry.

## Decision

**A filled `fp_poly` on one declared copper layer, inside a footprint that declares no net tie,
converts to one netless `Segment` whose modelled extent is the polygon's board-coordinate vertex
bounding box inflated by the stroke half width.** Everything else on that surface refuses, by a
sentence that names its own primitive kind.

The three questions the copper asks are answered separately, as ADR-0078 and ADR-0092 answer them
for their cases:

- **Obstacle: over-approximate.** The emitted segment's modelled extent is a proven superset of
  the drawn copper. `_footprint_copper_obstacle_segments` carries the four-step containment proof;
  its load-bearing step is that the filled region lies in the convex hull of the vertices *under
  any fill rule*, because the outline is a closed polyline through those vertices and the region a
  closed curve encloses lies inside any convex set containing the curve. The stroke adds at most
  half its width, because KiCad centres a stroke on its path — `STROKE_PARAMS::GetWidth()` is the
  full width and `PCB_SHAPE::TransformShapeToPolygon` inflates a polygon shape by
  `SHAPE_POLY_SET::Inflate( GetWidth() / 2 )` before adding it to the layer. The half width is
  rounded **up**, `(width + 1) // 2`, so an odd width cannot leave a nanometre of copper outside.
- **Connectivity: no claim.** `net_id` is `None`, the contract net-0 copper already has under
  ADR-0078. Nothing is claimed to connect through this copper.
- **Identity: derived.** An `fp_poly` is not a KiCad track, so its `uuid` names no `Segment`. The
  identity is revision-derived, and both source-preserving patch paths already refuse a snapshot
  containing one, so a board carrying stray copper converts and routes but **cannot be written
  back** (ADR-0026). That is the same contract net-tie copper has.

**The emitted segment is always axis-aligned**, whatever the footprint's rotation, because the box
is taken in board coordinates after `_transform` and a non-quarter-turn rotation refuses earlier.
This is load-bearing, not incidental: `layered_board_adapter._segment_bounds` returns `None` for a
diagonal foreign segment and the layered router answers `diagonal foreign segments are not
modeled`, so an envelope that could come out diagonal would have traded one refusal for another.

**The remainder refuses by name**, from a closed table keyed on the head and never built from the
source token: `fp_line`, `fp_arc`, `fp_rect`, `fp_circle`, `fp_curve` and `point` each name their
primitive, and `fp_text`, `fp_text_box` and a footprint `property` get ADR-0095's sentence in a
footprint-scoped spelling. That is ADR-0123's rule applied one structural level down. It is worth
applying because the kinds ask for different fixes: a stroked `fp_line` is geometrically a
`Segment` and a stroked minor arc is an `Arc`, so those two are waiting on an *observation*; a
filled `fp_circle` is a disc that no Board IR obstacle type represents, so it is waiting on a
*type*; copper text is waiting on the five exit conditions ADR-0095 records, none of which is met.

An unfilled polygon, a polygon on `*.Cu` or `F&B.Cu`, a polygon with a curved `pts` side, a
polygon without an explicit stroke and a polygon with fewer than three distinct vertices all
refuse, each by its own sentence. The curved side is the one that matters for soundness: a curved
side bulges outside the hull of the listed vertices, so a vertex-derived envelope would not
contain it.

**No Board IR schema field is added**, so ADR-0105 is invoked and found not to apply: the accepted
set grows, but it grows into `Segment`, which the schema already carries. One measured count is
added — `footprint_copper_graphic_envelope_count` — and it discloses an *approximation* rather
than an erasure, the footing `max_roundrect_rounding_nm` is already on.

## Consequences

**What improves.** Copper that was refused is now modelled, in the only direction an obstacle may
err. Two boards clear a gate. Nine constructs that shared one field-less sentence now
refuse through seven that name what they refused.

**What becomes harder, and it is not small.** A board with stray copper is **not write-back
capable**: the derived identity refuses both patch paths. A caller can convert it, inspect it and
route against it, and cannot apply the result. That is the honest consequence of modelling copper
whose identity is not a track's, and it is recorded rather than discovered.

**What a caller must read.** A non-zero
`footprint_copper_graphic_envelope_count` means that many obstacles are *looser* than the copper
they stand for. An over-approximated obstacle cannot permit an illegal route, but it can make a
routable board report unroutable, and the count is how a caller finds out that this is possible.
R-181 records the residual.

**What is not disturbed.** The placement legalizer's four verdicts — `pad_overlap`,
`outline_containment`, `keepout_respect`, `courtyard_overlap` — read `pads`, `outline` and
`keepouts`, and never `segments` or `arcs`. So an over-approximated obstacle cannot turn a
`proven_clear` into a `violated`, which is the ADR-0075/ADR-0080 rule this change had to satisfy.
That is also the reason a `Segment` is admissible here where a synthetic `Keepout` would not be:
`keepout_respect` *does* read keepouts, and a keepout the board never declared would publish a
placement violation KiCad does not share.

**What the corpus does not evidence.** No board in any measured corpus reaches the conversion:
both B-136 boards clear this gate and then refuse at a root zone field, so the acceptance *gate*
is exercised by real boards while the *envelope geometry* is evidenced by the containment proof
and by a property test over generated polygons. That gap is stated rather than papered over.

## Alternatives considered

**Keep refusing.** The refusal was correct while the case was unobserved and is not correct now.
It also fails the direction test in the other direction: a whole-document refusal answers "is
there an obstacle here" with silence.

**Model the polygon exactly, as a `Ring`.** Impossible for **56 of 56**: `Ring` rejects a repeated
vertex and every one of these polygons has one. Even without that, 26 of 56 are self-intersecting
and their filled region is not determined by the document.

**Convex hull, triangulation, or a scanline decomposition into many segments.** All three are
tighter, all three are models of "the filled region", and there is no filled region until a fill
rule is chosen. They would also multiply one source expression into an unbounded number of
obstacles. The bounding box is correct under *every* fill rule, which is a stronger property than
a tighter model that had to guess one, and B-136 measured its cost at under 5% of either board.

**Emit a synthetic `Keepout` with the polygon as its boundary.** Rejected twice over: `Ring` cannot
represent these polygons at all, and `keepout_respect` reads keepouts, so a keepout the board never
declared would publish a placement violation KiCad does not share — exactly the ADR-0075 trap.

**Reuse the polygon's own `uuid` so the board stays write-back capable.** Rejected: an `fp_poly` is
a graphic and not a track, so a `Segment` carrying its UUID would let a patch emit a `(segment …)`
colliding with the graphic's identity. ADR-0092 made the same call for the same reason.

**Accept an unfilled polygon too, since the same envelope contains it.** True and rejected: B-136
measured `fill_no` and `fill_absent` at 0 of 56, and this adapter's standing rule is that a model
for an unobserved form is a model of nothing. The refusal names the field, so the day a board
writes one the diagnostic says so.
