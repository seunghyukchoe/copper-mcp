# ADR-0077: Roundrect corner radii round up, by geometry role

- Status: Accepted
- Date: 2026-08-06
- Owners: `@seunghyukchoe`
- Related: [Issue #116](https://github.com/seunghyukchoe/copper-mcp/issues/116), ADR-0011,
  ADR-0017, ADR-0072,
  [Roundrect radius precision research](../research/roundrect-radius-precision-v1.md)

## Context

KiCad does not store a roundrect pad's corner radius. It stores a dimensionless `roundrect_rratio`
— a scaling factor applied to the pad's *shorter* side — writes it back with ten significant
digits, and recomputes the radius on every read as `KiROUND(ratio * min(size.x, size.y))`.

The product of an ordinary ratio and an ordinary pad side is therefore routinely a fractional
number of nanometres. The Board IR adapter computed that product in exact integer arithmetic and
refused whenever it left a remainder. That was fail-closed and never produced a wrong answer, but
measured against a working tree of 23 real KiCad boards it was the **first refusal on 5 of them**,
and 592 of the 4,537 roundrect pads in that tree (13.0%) carry a fractional radius. The largest
shortfall to the next whole nanometre is 0.80 nm — against pad sides of tens of thousands of
nanometres and design rules of hundreds of thousands. This is a precision artifact of KiCad's own
ratio encoding, not a shape the model cannot represent.

The hard part is not that a rounding is needed. It is the **direction**. Issue #116 proposed
"the pad is copper, so the radius must round outward (larger pad = larger obstacle = safe)". That
reasoning does not hold: a larger corner radius means *more* rounding and therefore a *smaller*
pad, so "round the copper outward" and "round the radius up" are opposite instructions. The safe
direction cannot be derived from "a pad is copper" at all.

## Decision

**A roundrect corner radius that is not a whole number of nanometres is rounded up, and the
amount is recorded rather than hidden.**

The direction is settled by geometry role, following the asymmetry ADR-0011, ADR-0017 and
ADR-0072 already establish — an obstacle may be over-approximated, attachment copper must be
under-approximated — and the two roles turn out not to conflict, because **only one of them reads
the radius**.

- As an **obstacle**, a pad is over-approximated by its full axis-aligned bounding box.
  `_pad_extent`, `pad_half_extents` and `_pad_half_extents` all derive it from `size_x_nm` and
  `size_y_nm` alone. Corner rounding is discarded — conservatively, since the box contains the
  rounded shape — before the radius could matter. No rounding of the radius can shrink an
  obstacle, because no obstacle consults it.
- As **attachment copper**, the radius is consumed only by the under-approximating inner core:
  the full-width band of half-height `half_y - radius`, in `pad_core` and `_pad_core_extent`. A
  larger radius shrinks that band, keeping it strictly inside the true copper, which is what stops
  the router asserting a connection the board does not have.

Rounding up is therefore safe for the one role that reads the value, and safe under either
candidate reference geometry without having to adjudicate between them: `ceil(x) >= x` covers the
mathematically exact radius, and `ceil(x) >= KiROUND(x)` covers the integer radius KiCad itself
renders.

Three things are deliberately *not* done.

- **The residue is not reported as a diagnostic.** Every caller of `parse_kicad_bytes` treats a
  non-empty `diagnostics` tuple as a refusal, so a warning would refuse the board it exists to
  admit. It is a separate `ConversionResult.max_roundrect_rounding_nm` field, defaulting to zero,
  in the shape of the SimpleRouteJson importer's `max_outward_rounding_nm`: measured, not
  asserted, and readable by a caller that needs bit-exact pad geometry and would rather decline.
- **A radius that rounds up past half the short side is refused, not clamped.** A ratio within
  half a nanometre of 0.5 on an odd-nanometre short side produces a radius the shape cannot hold.
  Rounding down to fit, or clamping to the largest representable radius, would both give the pad
  a core taller than its real copper. It keeps a typed `integer.precision` refusal.
- **A ratio outside `(0, 0.5]` is still refused.** Zero is refused because Board IR cannot express
  a zero-radius roundrect — KiCad calls that pad a rect, and reclassifying it is a shape decision,
  not a precision one. Above 0.5 is refused because KiCad's own setter clamps there, so a larger
  value never came from KiCad.

**A related correction lands with it.** `_pad_cores` gave a pad its inscribed square whenever the
central band collapsed to a bar, on the reasoning that only a round pad degenerates that way. A
roundrect whose radius is exactly half its shorter side is a stadium and collapses identically,
so a 2.0 x 1.0 mm stadium was handed a core reaching 1.0 mm from its centre in y where the copper
stops at 0.5 mm — an attachment core claiming copper that is not there. The disc treatment is now
gated on the pad *being* a disc rather than on the collapse. This was reachable before this ADR,
since KiCad writes that pad for a ratio of 0.5, and rounding a fractional radius up onto the
boundary makes it easier to reach.

## Consequences

Board IR pad geometry is no longer bit-identical to the source in every case: a roundrect radius
may exceed the exact ratio product by less than one nanometre. `docs/architecture/board-ir.md`
records this alongside the unchanged rule that millimetre and degree tokens still convert exactly
with no rounding path — the ratio is a third kind of token and is the only one that rounds.

No content address moves. A pad whose radius was already exact converts to the identical value,
and a pad whose radius was not exact previously produced no snapshot at all, so no previously
published digest can change. The golden identity pins are unchanged.

The safety argument rests on a property of the current code — that no obstacle model consults the
corner radius — rather than on a general truth. Nothing in the type system enforces it. It is
pinned by a test asserting the obstacle envelope is independent of the radius, and carried as
R-118.

The roundrect refusal is eliminated on all 5 boards that hit it. None of the 5 converts as a
result: each has further, independent gaps — rotated courtyards and circle/arc courtyard
primitives, items 1 and 3 of issue #116 — so the board-level conversion count is unchanged at 1
of 23. The gap that is closed is closed completely; it was not the last one on any of those
boards.
