# KiCad arc tracks as routing obstacles

Research date: 2026-08-06.  This note supports [ADR-0070](../adr/0070-conservative-arc-track-envelopes.md).
No external code is copied.

## Official source findings

KiCad's board S-expression format defines a copper arc as a first-class track object with three
control points and no angle field:

```
(arc (start X Y) (mid X Y) (end X Y) (width W) (layer L) [(locked)] (net N) (tstamp UUID))
```

The specification describes `mid` as the point of the arc between `start` and `end` — a point on
the curve rather than a centre or a control handle — and states that source files are written in
millimetres against a one-nanometre internal resolution.  Source:
[KiCad S-expression board format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/index.html).

Two facts follow, and both are load-bearing below.  First, the circle is determined entirely by
three integer points, so its centre is *rational* and its radius is the square root of a rational
— exactly the situation in which a rounding rule has to be argued rather than assumed.  Second,
the format never records which way the arc sweeps, so the arc that is meant is precisely the one
passing through `mid`; that is what distinguishes it from its companion arc on the same circle.

KiCad's PCB Editor documentation describes arc tracks as ordinary routed copper participating in
connectivity and clearance like straight tracks, not as annotation.  Source:
[KiCad 10 PCB Editor](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html).

## What this repository already did

The Board IR adapter has parsed `(arc …)` into a first-class `Arc` since v0.1
(`adapters/kicad_board_ir.py`), and `board_ir/types.py` already validates that the three control
points are distinct and non-collinear.  The arc reached the IR faithfully and then stopped: the
single-layer router refused **any** selected-layer arc outright
(`RouteFailureCode.UNSUPPORTED_GEOMETRY`), so a board carrying one could not be routed at all.
The refusal was correct in direction — it never claimed a wrong answer — but it is the case this
note is about converting into a faithful observation.

Two existing pieces of prior art frame the choice:

- [ADR-0017](../adr/0017-diagonal-segment-envelopes.md) established that a shape the router cannot
  represent exactly becomes a **conservative polygon envelope**, built as the Minkowski sum of the
  centreline with an axis-aligned square of the half width.  The disc is inscribed in the square,
  so containment needs no numerical argument, and every vertex is an exact integer because a
  square's corners are integer offsets from integer endpoints.
- `circuit_scene.py::_arc_bounds` already bounds an arc conservatively for *scene observation*,
  folding in each cardinal point of the circle the sweep crosses and using a ceiling square root so
  the bound stays outside the true circle.  It handles arcs of any span, but yields an
  axis-aligned bounding box — far too loose for routing, where ADR-0017 explicitly rejected the
  bounding box of a long diagonal as "nearly a filled square".

## Bounded design chosen

A **minor** arc — one spanning at most half a turn — becomes a conservative polygon envelope; a
major arc stays a typed refusal; and an arc on the *routed* net stays a typed refusal regardless
of span.

**Why the chord, and why it is exact.**  Every point of a minor arc projects onto its own chord
segment, and lies within the sagitta of it.  So

    arc  ⊆  chord ⊕ disc(sagitta)
    arc track = arc ⊕ disc(half width)  ⊆  chord ⊕ disc(half width + sagitta)
                                        ⊆  chord ⊕ square(half width + sagitta)

which is exactly ADR-0017's construction with a larger radius.  The envelope therefore reuses that
proof and that integer-vertex property unchanged; only the radius is new.

**The half-turn test is a single integer dot product.**  By the inscribed-angle theorem the angle
subtended at `mid` by the chord is half the arc *not* containing `mid`, so that angle is at least a
right angle exactly when the arc through `mid` is the minor one:

    (start − mid) · (end − mid) ≤ 0

No division, no square root, no tolerance.  A semicircle gives exactly zero and is admitted, which
is the inclusive side — the containment argument still holds with equality at half a turn.  The
degenerate full-circle case (`start == end`) is refused by the same test with no special casing,
because the dot product is then a positive squared length.

**The sagitta bound avoids the rounding rule instead of arguing about it.**  The sagitta is
`r − h`, a difference of two square roots of rationals, so evaluating it needs a rounding rule
whose correctness has to be proved separately — the objection ADR-0017 raised against the oriented
bounding box.  Instead the implementation returns the smallest integer `k` satisfying a
*sufficient* integer condition:

    k · D · ⌊√L²⌋ + K  ≥  ⌊√(P·L²)⌋ + 1  ≥  √(P·L²)

Every substitution (flooring the chord length, ceiling the product root) can only make the
required `k` larger, so the result is an upper bound **by construction** rather than by numerical
accident.  All symbols are integers derived from the integer control points.

**Why a major arc is refused rather than enveloped.**  Past half a turn the arc leaves the chord's
own span — it wraps around beyond both endpoints — so the projection argument fails and the chord
model would silently under-cover the real copper.  That is the one error direction this project
never accepts, so it is a typed refusal.

**Why a selected-net arc is refused.**  An obstacle may be over-approximated, but attachment
copper must be *under*-approximated, or the router would assert a connection the board does not
have.  An arc has no exact integer inner core yet, so it cannot be attachment copper.  This is the
same asymmetry ADR-0017 recorded for diagonal segments before ADR-0018 supplied their inner cores.
The refusal covers every copper layer, matching the existing same-net zone gate, because
connectivity is a multilayer question.

The layered proposal adapter keeps its stricter blanket arc refusal.  Its obstacle model is
rectangles only — it refuses even a diagonal foreign segment — so the blanket refusal is what
makes it safe, and relaxing it without first giving it an arc obstacle would let it route through
arcs silently.

## Measurable acceptance criterion

A committed `arc-blocker.kicad_pcb` fixture carries a `POWER` semicircle
`start (18, 11) → mid (14, 15) → end (18, 19)` bowing across the straight `AUDIO` corridor at
`y = 15 mm`.  The semicircle is deliberate: it is the inclusive boundary of the supported span and
the largest sagitta any admitted arc can have.  The predeclared result is:

| Measurement | Before | Required after |
| --- | --- | --- |
| `AUDIO` route on the fixture | `unsupported_geometry` refusal | routed, `bend_count > 0` |
| `POWER` route on the same fixture | `unsupported_geometry` refusal | unchanged typed refusal |
| Sagitta bound vs. exact 4,000,000 nm sagitta | not evaluable | ≥ exact, within a few nm |
| KiCad 10.0.5 DRC of the source fixture | n/a | 0 violations |
| KiCad 10.0.5 DRC of a straight `AUDIO` track | n/a | `shorting_items` error |

The last row is the discriminating check: a fixture whose direct path is already legal would make
a clean DRC on the detour prove nothing.

Measured on KiCad 10.0.5: the source fixture reports 0 violations and 1 unconnected item (the
unrouted `AUDIO` net); inserting the straight `AUDIO` track reports exactly one `shorting_items`
error against `POWER`.  The sagitta bound for the fixture's semicircle is 4,000,001 nm against an
exact 4,000,000 nm — one nanometre of conservatism.  Across 204 randomised minor arcs with radii
from 0.1 mm to 30 mm, there were zero containment failures and the tightest observed
worst-case-distance to bound ratio was 0.999999994, so the bound is not merely safe but close.

This is evidence for arcs spanning at most half a turn, on the single-layer A* obstacle model, on
this fixture family.  It is not evidence about major arcs, arcs as attachment copper, the layered
proposal adapter, arcs in zone fill, graphical `gr_arc` outlines, or manufacturability.

## What this note does not claim

It does not claim the envelope is tight.  For a near-semicircular arc it over-approximates
substantially, because the square sweep extends the sagitta along the chord as well as across it —
on the committed fixture the envelope is roughly four times the area of the arc's own bounding
box.  That is the accepted direction of error, and it is the reason this is an envelope and not a
claim of exactness.  Splitting each arc at its own `mid` point and enveloping the two halves
separately would cut that materially, at the cost of two obstacle objects per arc instead of one,
and is the natural next slice.
