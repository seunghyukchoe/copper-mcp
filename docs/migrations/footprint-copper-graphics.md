# Stray footprint copper becomes an obstacle, and a converted board can stop being appliable

Board IR now converts boards whose footprints draw filled copper polygons of their own — logos,
antennas, artwork — instead of refusing them. No schema version moves: `BOARD_IR_SCHEMA_VERSION`
stays `0.4.0` and `schemas/board-ir/0.4.0.schema.json` is byte-unchanged, because the accepted
geometry enters the snapshot as a `Segment`, which the schema already carries (ADR-0105, D-230,
ADR-0125).

## The change that can surprise you: a converting board may not be appliable

This is the one to read before the rest.

A footprint's `fp_poly` is a *graphic*, not a track, so its `uuid` names no `Segment`. The obstacle
Board IR derives from it therefore carries a **revision-derived identity**, and both
source-preserving patch paths — `kicad_route_patch` and `kicad_placement_patch` — refuse any
snapshot containing one.

**What that means in practice.** A board with stray footprint copper will now:

- convert, where it previously returned `footprint graphic on a copper layer is unmodelled copper`;
- expose its copper to the router as an obstacle;
- and **fail at apply time**, on the whole snapshot rather than on one object.

That is not a regression: the board did not convert at all before, so nothing that used to be
appliable stopped being appliable. But a caller whose flow is "convert, then apply" will now reach
the second step on boards that never got there, and will see the patch path refuse. Net-tie copper
has had exactly this contract since ADR-0092; this change makes it reachable on a much wider class
of board.

The refusal is deliberate and is a guarantee rather than a gap. Board IR modelled this copper by a
bounding envelope, so it does not know the shape well enough to write it back, and a patch path
that proceeded anyway could silently delete artwork it cannot represent (ADR-0026).

## One number is added

`footprint_copper_graphic_envelope_count` joins `inspect_board_ir`'s `unmodelled_counts` map,
taking it from nine entries to ten. A caller reading that map as a fixed-size structure must widen
it.

Unlike every other entry, this one does **not** report an erasure. The geometry *is* modelled; what
the number discloses is that the model is **looser** than the copper it stands for. Each counted
polygon became one `Segment` whose modelled extent is the polygon's board-coordinate vertex
bounding box inflated by the stroke half width — a proven superset of the drawn copper, never a
subset.

**What a caller should do with a non-zero value.** Read it as: *this many obstacles are larger than
the real copper.* The consequence is one-directional. An over-approximated obstacle can never let
the router propose a track through copper that is really there, but it can make a routable board
report unroutable, because a concave shape's open channels are closed by its bounding box. If a
route fails on a board with a non-zero count, that count is the first thing to check. R-181 records
the residual and B-136 the measured magnitude: on the two boards surveyed, every envelope covered
under 5% of the board's `Edge.Cuts` bounding box, and so did each board's whole envelope union.

The count is a cardinality of source expressions, zero for a refused conversion, and never
contributed to by net-tie copper, which takes its own pre-existing path.

## Vertex budgets now include copper-graphic vertices

A polygon reduced to an envelope loses its vertices before Board IR validation counts serialized
rings, so a caller's `max_total_vertices` would not otherwise have covered them. They are now
charged against that budget, exactly as reduced custom-pad primitive vertices already were. A
caller running close to a tight vertex ceiling on a board with large copper artwork may see
`total vertex budget exceeded` where the board previously refused for a different reason.

## Five refusal messages changed, and one is new

A footprint graphic on a copper layer that is *not* an acceptable filled polygon no longer answers
with one shared sentence. Each now names its own primitive:

| Construct | New message |
|---|---|
| `fp_line` | `footprint copper line is unmodelled copper` |
| `fp_arc` | `footprint copper arc is unmodelled copper` |
| `fp_rect` | `footprint copper rectangle is unmodelled copper` |
| `fp_circle` | `footprint copper circle is unmodelled copper` |
| `fp_curve` | `footprint copper curve is unmodelled copper` |
| `point` | `footprint copper point is unmodelled copper` |
| `fp_text`, `fp_text_box`, `property` | `footprint copper text has no envelope derivable from the board and is unsupported` |

`footprint graphic on a copper layer is unmodelled copper` survives only as the fallback for a head
outside that table — a KiCad 11 `fp_ellipse`, for instance.

The text sentence is deliberately **not** the root one
(`copper text has no envelope derivable from the board and is unsupported`). A diagnostic reader
must be able to tell a footprint's copper text from the board's, and a shared sentence would also
re-bucket instruments that match on message together with locator.

A polygon that is not acceptable refuses by the field that made it so:
`footprint copper polygon must name one declared copper layer` (for `*.Cu`, `F&B.Cu` or an
undeclared layer), `… must be filled`, `… must declare its outline stroke`,
`… must carry three distinct vertices`, and `… encloses no area to bound`.

**What a caller must do.** Any client matching on the old sentence to detect this class of refusal
should match on the locator suffix `.graphic` under a `kicad_pcb.footprint[N]` prefix instead, which
is unchanged and covers every message above.

## What did not change

No apply authority, token, CLI, DRC, routing-policy or board-write behaviour. No board's *first*
refusal moves on the public benchmark cohort. Direct public conversion stays 0/10 and the frozen own
corpus stays 15/18, with the new counter at 0 on all fifteen (B-137).
