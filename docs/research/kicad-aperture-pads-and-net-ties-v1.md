# KiCad aperture pads, net ties, and the footprint placement flag

Research date: 2026-08-07. This note supports three singleton refusals measured against a working
tree of real KiCad boards and recorded in
[issue #116](https://github.com/seunghyukchoe/copper-mcp/issues/116): decisions
[D-158](../ledgers/decision-ledger.md), [D-159](../ledgers/decision-ledger.md) and
[D-160](../ledgers/decision-ledger.md), and risk [R-119](../ledgers/risk-register.md). No external
code is copied, and no board content from the surveyed tree is reproduced here — the constructs are
described by their format definitions, and the fixtures in `tests/test_kicad_board_ir.py` are
authored from those definitions rather than extracted from a design.

## Why these three, and why they were invisible

Each was the *first* refusal on exactly one board out of twelve, behind the two courtyard causes
that dominate the survey. A singleton is the hardest kind of gap to find and the easiest to
misjudge: with one instance there is no distribution to argue from, so the only sound basis for a
decision is the format's own definition of the construct. That is what this note assembles.

## Finding 1 — a footprint graphic on copper was a net tie, and the refusal named the wrong thing

The construct is a `NetTie-2_THT_Pad1.0mm` footprint carrying two filled `fp_poly` rectangles, one
on `F.Cu` and one on `B.Cu`, joining two through-hole pads on two different ground nets. It also
declares `net_tie_pad_groups`.

KiCad's format defines that token as: "An optional list of net-tie pad groups", whose value is "a
space-separated list of quoted strings, each containing a comma-separated list of pad names. Nets
attached to pads within a single pad-group are allowed to short." Source:
[KiCad S-expression format, footprint](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#_footprint).

That sentence is the whole finding. The polygon is not decoration and not a stray drawing left on
the wrong layer — it *is* the short, and it is deliberate. Two consequences follow:

1. **The refusal is correct and is not a geometry problem.** CopperMCP's conservative-envelope
   technique (ADR-0072) answers "this shape cannot be expressed exactly"; it has nothing to say
   here, because the difficulty is connectivity, not shape. Board IR models nets as disjoint sets,
   and this copper belongs to two at once. Modelling it as an obstacle for both nets would forbid
   the very connection the part exists to make; assigning it to one net would assert a connection
   the board does not have. There is no conservative direction, because the two roles the copper
   plays point opposite ways.
2. **The diagnostic was wrong even though the outcome was right.** The adapter already refuses net
   ties, in `_footprints_and_pads`, with a message that says so. But `_semantic_preflight` runs
   first and refused the *graphic* with "footprint graphic on copper or Edge.Cuts is unsupported" —
   which reads as a stray drawing on a copper layer. A user acting on that message goes looking in
   their board for a mistake that is not there, and the actual reason (a deliberate net tie the
   tool does not model) is never stated.

The single message also conflated two situations whose *direction of error* is opposite. Copper is
an obstacle and over-approximates; a graphic on `Edge.Cuts` contributes to the board outline, which
is routing **room** and may only be under-approximated (ADR-0076). Sharing one sentence between
them invites exactly the wrong fix.

**What is deliberately not claimed.** Nothing here establishes how a net tie *should* be modelled.
Reading `net_tie_pad_groups` as an equivalence over nets, and teaching the router that two nets may
legally touch inside one footprint's copper, is a real design question and is out of scope. This
change only makes the refusal name what it refused.

## Finding 2 — a pad with no copper layer is a stencil aperture, and is not a Board IR pad

The construct is eight `(pad "" smd roundrect … (layers "F.Paste"))` expressions, four each on two
`TO-252-2` transistors, alongside the ordinary copper pad they overlie. The adapter required every
`pad` to resolve to at least one copper layer and refused the board when one did not.

KiCad names this object and defines it by the absence of copper. Its footprint editor documentation
carries a section titled "Pads Not on Copper Layers" which states: "There is second method for
creating pads that do not have any copper layers defined. These pads are commonly referred to as
aperture pads and can be use to create custom apertures not based on the outline of a copper pad
geometry." (The two typos are the source's.) Source:
[kicad-doc, pcbnew_creating_editing_footprints.adoc](https://github.com/KiCad/kicad-doc/blob/master/src/pcbnew/pcbnew_creating_editing_footprints.adoc).
The implementation agrees and goes further: `PAD::ApertureMask()` is a layer set containing only
`F_Paste`, and `PAD::CanHaveNumber()` is false for an aperture pad — KiCad will not let one carry a
pad number at all. Source:
[PAD class reference](https://docs.kicad.org/doxygen/classPAD.html).

The library conventions state the intended construction explicitly: build the copper shape with
pads on copper layers only, *without* paste, then add the stencil openings as separate aperture
pads. Source:
[KiCad Library Convention F6.3, pad requirements for SMD footprints](https://klc.kicad.org/footprint/f6/f6.3.html).
This is the standard way to subdivide the paste over an exposed thermal tab, which is exactly what
the surveyed board does.

So the expectation was the wrong half of the pair. The board is not unusual and KiCad wrote exactly
what it meant; the adapter asserted an invariant the format does not hold.

**Direction of error.** This project's rule is that obstacle copper may only be over-approximated
and attachment copper only under-approximated. An aperture pad is *neither*: it has no copper, so
dropping it removes no obstacle and discards no attachment point, and the copper the paste sits on
is a separate pad converted normally. That argument holds only under conditions, so the adapter
checks each one and refuses rather than dropping when any fails: no declared layer is copper; every
declared layer is a paste or mask layer; the pad kind is `smd` (a drill is a hole through copper
whatever the layer list says); it declares no net; and it carries no pad number, matching KiCad's
own rule. "This pad has no copper layer" is not by itself a licence to drop it.

## Finding 3 — `placed` is autoplacement bookkeeping

The construct is `(placed yes)`, on all 31 footprints of one board, refused as an unsupported
footprint field.

KiCad's format defines it as: "The optional `placed` token defines a flag to indicate that the
footprint has not been placed." Source:
[KiCad S-expression format, footprint](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#_footprint).
It is editor state: `FOOTPRINT` exposes it as a plain status accessor pair, `IsPlaced()` /
`SetIsPlaced()`, alongside `NeedsPlaced()` / `SetNeedsPlaced()`, with no geometric or electrical
consumer. Source:
[FOOTPRINT class reference](https://docs.kicad.org/doxygen/classFOOTPRINT.html).

It carries no geometry, no layer and no constraint, which places it in the same class as `descr`
and `tags` — library documentation strings recently accepted for the same reason — and in a
different class from `locked`, which *is* a constraint and is modelled separately. Accepting it as
metadata therefore ignores nothing CopperMCP would otherwise have honoured, and the regression test
proves that by equality rather than by assertion: the converted footprints and pads are identical
with and without the flag.

**The allowlists stay closed.** This is one named token, not a policy of ignoring unknown fields.
An unrecognised footprint field and an unrecognised root field each remain a typed refusal, pinned
by their own controls. Worth recording plainly: the twelve surveyed boards carry **no** root-level
`kicad_pcb` child outside the existing allowlist, so the root allowlist needed no widening at all —
the field measured at the root in an earlier survey framing was this footprint-level one.

## What this note refuses to claim

- No claim that all aperture pads in the wild are paste-only. The rule is written to the
  conditions above and refuses anything outside them, including a mask-only pad on a layer whose
  meaning has not been established here.
- No claim about a *net-tie-aware* routing model, only about the honesty of the refusal.
- No claim that `placed` is meaningless to KiCad. It is meaningless to *copper geometry*, which is
  the only thing CopperMCP models.
- No frequency claim beyond the twelve boards measured. Each construct appeared on exactly one
  board, and a sample of one is a demonstration that the case exists, not a distribution.
