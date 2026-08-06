# KiCad roundrect corner radius and the fractional-nanometre residue

Research date: 2026-08-06.  This note supports
[ADR-0076](../adr/0076-roundrect-corner-radius-rounding.md) and the roundrect row of
[issue #116](https://github.com/seunghyukchoe/copper-mcp/issues/116).  No external code is
copied.  Source references are pinned to KiCad `master` at commit
`bd56dda8fd857d2ac39004e74f2eb382f13675c2`; the padstack indirection described below arrived in
KiCad 9, and the arithmetic — though not the call site — is the same in KiCad 6 through 8.

## Official source findings

**The stored value is a ratio, not a length.** KiCad's S-expression documentation defines the
pad token as: "The optional `roundrect_rratio` token defines the scaling factor of the pad to
corner radius", adding that the factor is a number between 0 and 1.  The grammar slot is
`[(roundrect_rratio RATIO)]`.  Source:
[KiCad S-expression format, footprint pad](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#_footprint_pad).

**The documentation is not sufficient, and disagrees with the implementation on the range.**  The
dev-docs say 0 to 1 and do not say *which* dimension is scaled.  Both facts come from the source
instead.  `pcbnew/pad.h` states the semantics directly — "Set the ratio between the smaller X or
Y size and the rounded corner radius" — and adds that it cannot exceed 0.5, with 0.25 as the
IPC-7351C normalized value.  Source:
[pad.h](https://gitlab.com/kicad/code/kicad/-/blob/bd56dda8fd857d2ac39004e74f2eb382f13675c2/pcbnew/pad.h#L797-L803).
The 0.5 cap is enforced, as `std::clamp( aRadiusScale, 0.0, 0.5 )`, in `PAD::SetRoundRectRadiusRatio`,
which is the setter the board parser itself calls.  Source:
[pad.cpp](https://gitlab.com/kicad/code/kicad/-/blob/bd56dda8fd857d2ac39004e74f2eb382f13675c2/pcbnew/pad.cpp#L1155-L1169).
This is why the adapter's accepted range is `(0, 0.5]` and not the documented `[0, 1]`: it
matches what KiCad will actually hold, not what the format prose permits.

**The radius is recomputed on every read, and rounded to an integer nanometre by KiCad itself.**

```
int PADSTACK::RoundRectRadius( PCB_LAYER_ID aLayer ) const
{
    const VECTOR2I& size = Size( aLayer );
    return KiROUND( std::min( size.x, size.y ) * RoundRectRadiusRatio( aLayer ) );
}
```

Source:
[padstack.cpp](https://gitlab.com/kicad/code/kicad/-/blob/bd56dda8fd857d2ac39004e74f2eb382f13675c2/pcbnew/padstack.cpp#L932-L936).
`KiROUND` is round-half-away-from-zero
([util.h](https://gitlab.com/kicad/code/kicad/-/blob/bd56dda8fd857d2ac39004e74f2eb382f13675c2/libs/kimath/include/math/util.h#L98)).
Pcbnew's internal unit is one nanometre — `constexpr double PCB_IU_PER_MM = 1e6;  ///< Pcbnew IU
is 1 nanometer.` — so `size` is already an exact integer nanometre count.  Source:
[base_units.h](https://gitlab.com/kicad/code/kicad/-/blob/bd56dda8fd857d2ac39004e74f2eb382f13675c2/include/base_units.h#L68).

**The ratio is written back with ten significant digits.**  The writer prints
`(roundrect_rratio %s)` through `FormatDouble2Str`, which formats with `{:.10g}` for any value
above 1e-4 — every practically used ratio — and `{:.16f}` with trailing zeros stripped below it.
Sources:
[pcb_io_kicad_sexpr.cpp](https://gitlab.com/kicad/code/kicad/-/blob/bd56dda8fd857d2ac39004e74f2eb382f13675c2/pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp#L2053-L2054),
[string_utils.cpp](https://gitlab.com/kicad/code/kicad/-/blob/bd56dda8fd857d2ac39004e74f2eb382f13675c2/common/string_utils.cpp#L1442-L1467).
The reader parses it with a plain `parseDouble` and no extra precision handling.

**Consequence, and the whole reason this note exists.**  A ten-significant-digit ratio multiplied
by an arbitrary integer nanometre pad side is, in general, *not* an integer number of nanometres.
A ratio of `0.203125` against a 650,000 nm side is exactly 132,031.25 nm.  KiCad resolves this by
applying `KiROUND` at every read; a file therefore does not carry the corner radius at all, it
carries a lossy encoding of it from which the radius is re-derived.  A radius the user set exactly
in the UI can come back a nanometre away from where it started.

**The boundary values are meaningful, not degenerate.**  At a ratio of 0 KiCad treats the pad as a
plain rectangle — "rounded rect pads with radius ratio = 0 are in fact rect pads"
([padstack.cpp](https://gitlab.com/kicad/code/kicad/-/blob/bd56dda8fd857d2ac39004e74f2eb382f13675c2/pcbnew/padstack.cpp#L124-L126)).
At a ratio of 0.5 the radius equals half the shorter side, the two short edges vanish entirely,
and the pad becomes a stadium — a square pad becomes a circle.

## What this repository already did

The adapter computed the radius in exact integer rational arithmetic and refused whenever the
product left a remainder, with `integer.precision` / "roundrect radius is not an exact
nanometre".  That was fail-closed and it never produced a wrong answer.  It was also, measured
against a working KiCad tree of 23 boards, the first refusal on 5 of them.

Measured over the same 23 boards: **4,537 roundrect pads, of which 592 (13.0%) have a fractional
exact radius.  The largest single shortfall to the next whole nanometre is 0.80 nm.**  Nine of
the 23 board files contain at least one such pad.  A pad side is tens of thousands of nanometres
and a design rule is hundreds of thousands, so this is a sub-nanometre encoding artifact of
KiCad's own ratio serialisation, not a shape the model cannot express.

## Direction of error, which is the actual difficulty

The instinct — stated in issue #116 itself — is "a pad is copper, so over-approximate it, so
round the radius outward."  **That instinct is wrong, and its two halves are in direct
opposition.** A larger corner radius means *more* corner rounding and therefore a *smaller* pad.
"Round the copper outward" and "round the radius up" are opposite instructions, so the question
cannot be settled by naming the safe direction for copper; it has to be settled per role.

A pad plays two roles and the repository already treats them asymmetrically (ADR-0011, ADR-0017,
ADR-0072): as an **obstacle** it must be over-approximated, and as **attachment copper** it must
be under-approximated, because claiming copper that is not there would let the router assert a
connection the board does not have.

The resolution is that only one of the two roles reads the radius at all.

- **As an obstacle**, a pad is over-approximated by its full axis-aligned bounding box.
  `_pad_extent` in the router, `pad_half_extents` in placement, and `_pad_half_extents` in the
  scene all derive it from `size_x_nm` and `size_y_nm` alone.  The corner rounding is discarded —
  conservatively, since the bounding box contains the rounded shape — before the radius could
  matter.  No rounding of the radius can shrink an obstacle, because no obstacle consults it.
- **As attachment copper**, the radius is consumed only by the under-approximating inner core:
  the full-width band of half-height `half_y - radius` (`pad_core`, `_pad_core_extent`).  A
  larger radius shrinks that band.

So **rounding up is safe**, and it is safe for the only role that reads the value.  The two roles
do not have to be handled separately, and that is a measured property of this codebase rather
than a general truth — it is exactly what a future obstacle model consulting the radius would
invalidate, which is why it is pinned by a test and carried as a residual risk.

The argument is also robust to which geometry is treated as authoritative.  There are two
candidate references — the mathematically exact `ratio x min(size)`, and the integer radius KiCad
itself renders, `KiROUND(ratio x min(size))` — and `ceil(x) >= x` and `ceil(x) >= KiROUND(x)`
both hold.  The modelled core is no taller than the true core under either reading, so the safety
argument never has to adjudicate between them.

**Where rounding up cannot be applied, the board is refused rather than clamped.**  If the ratio
is within half a nanometre of 0.5 on an odd-nanometre short side, the rounded-up radius exceeds
half that side and the shape cannot hold it.  Rounding down to fit, or clamping to the largest
representable radius, would both hand the pad a core taller than its real copper — the one
direction an attachment core may never err in — so that case keeps a typed refusal.  It was not
observed on any of the 23 boards.

**Arithmetic.**  The ratio's decimal token is converted to an exact integer numerator and
denominator, and multiplied by a pad side that `mm_to_nm` has already produced exactly, so the
comparison that decides the rounding is an integer remainder with no tolerance and no binary
float.  The SimpleRouteJson importer reaches the same guarantee with `Decimal` because its inputs
are arbitrary JSON number tokens; here both operands are already exact integers, so integer
rationals give the identical value with no precision context to configure.

## Measurable acceptance criterion

Predeclared, over the same 23-board tree, with `max_nodes` raised to work around
[#112](https://github.com/seunghyukchoe/copper-mcp/issues/112):

| Measurement | Before | Required after |
| --- | --- | --- |
| Boards whose first refusal is `roundrect radius is not an exact nanometre` | 5 | 0 |
| Fractional-radius roundrect pads converted | 0 | 592 |
| Largest single radius round-up | n/a | 1 nm |
| Golden identity pins moved | n/a | 0 |
| Modelled attachment-core corners outside the true pad | n/a | 0 |

## What this note does not claim

- It claims nothing about **chamfered** roundrect pads.  `chamfer_ratio` and `chamfer` are
  separate tokens, are still refused by the unknown-child check, and have their own geometry.
- It does not claim that a ratio of 0 should convert.  KiCad calls such a pad a rect, and Board
  IR cannot express a zero-radius roundrect; converting it to `PadShape.RECT` would be defensible
  on KiCad's own wording, but that is a shape decision rather than a precision one and is
  deliberately left refused.
- It does not claim the 23-board tree is representative of KiCad boards generally.  It is one
  designer's working audio project tree, and the 13.0% fractional-radius figure is a measurement
  of that corpus and not a population estimate.
- It claims nothing about **per-layer padstacks**, where KiCad 9 allows a different ratio per
  copper layer.  The adapter accepts one `roundrect_rratio` per pad and refuses the rest.
- It does not claim the radius round-trips.  KiCad's own write path quantises the ratio to ten
  significant digits, so a radius is not recoverable from the file to better than the residue
  measured here — by KiCad either.
- It claims nothing about board format versions other than `20260206`, the single version the
  adapter accepts, nor about KiCad 11.
