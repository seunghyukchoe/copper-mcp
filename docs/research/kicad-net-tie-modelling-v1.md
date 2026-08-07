# Modelling KiCad net ties: the declared short, and what may be claimed about it

Research date: 2026-08-07. This note supports ADR-0092 and decision
[D-179](../ledgers/decision-ledger.md): converting a `net_tie_pad_groups` footprint's shorting
copper as a netless obstacle segment with no connectivity claim. It continues the finding recorded
in [D-162](../ledgers/decision-ledger.md) and
[KiCad aperture pads and net ties](kicad-aperture-pads-and-net-ties-v1.md), which established
*why* the adapter refused and deliberately left *how to model* the construct open. No board
content from the surveyed tree is reproduced here; the fixture in
`tests/fixtures/board-ir-v0.2/net-tie-two-pad.kicad_pcb` is authored from the format definitions
below.

## What KiCad declares

The S-expression format defines `net_tie_pad_groups` as "an optional list of net-tie pad groups",
whose value is "a space-separated list of quoted strings, each containing a comma-separated list
of pad names. Nets attached to pads within a single pad-group are allowed to short." Source:
[KiCad S-expression format, footprint](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#_footprint).

The construct measured on the one real board that carries it (the issue #116 survey) is KiCad's
own library part `NetTie-2_THT_Pad1.0mm`: one group of two pad names written `"1, 2"` — note the
space after the comma, which KiCad's reader tolerates and its writer produces — two through-hole
pads on two different ground nets, and one filled, unstroked, axis-aligned rectangular `fp_poly`
on each of `F.Cu` and `B.Cu` bridging the pads. The `NetTie` family in KiCad's official footprint
library is built the same way: pads in one declared group joined by a filled copper polygon.
Source: [kicad-footprints, NetTie.pretty](https://gitlab.com/kicad/libraries/kicad-footprints/-/tree/master/NetTie.pretty).

## How KiCad itself treats the short

KiCad models the tie in its connectivity and DRC engines, not in its net model. `FOOTPRINT`
exposes `IsNetTie()`, `GetNetTiePadGroups()` and `MapPadNumbersToNetTieGroups()` — a mapping from
pad numbers to group ordinals — plus validation (`CheckNetTiePadGroups()`). Source:
[FOOTPRINT class reference](https://docs.kicad.org/doxygen/classFOOTPRINT.html). DRC consults
that mapping to *suppress* the "items shorting two nets" violation for copper inside a declared
group while still reporting it everywhere else; the boundary of that suppression is precise
enough that its interaction with zones and external tracks has its own issue history. Sources:
[KiCad issue #14008, net tie fails DRC in KiCad 7.0](https://gitlab.com/kicad/code/kicad/-/issues/14008),
[KiCad issue #16511, DRC clearance violation when two zones connect to a net tie](https://gitlab.com/kicad/code/kicad/-/issues/16511).

Two properties of KiCad's own treatment matter for Board IR:

1. **The nets stay distinct.** KiCad never merges the tied nets; the netlist keeps both names,
   and the tie is an *exemption* at the copper-overlap check, scoped to one footprint's declared
   pad group.
2. **The exemption is geometric and group-scoped.** Copper outside the group — a third net's
   track crossing the tie polygon — is still a full DRC violation.

## The modelling question, and the direction-of-error answer

Board IR models nets as disjoint sets, and the shorting polygon belongs to two nets at once, so
no `net_id` assignment states the truth. The copper plays two roles, and the project's
direction-of-error rules (obstacles over-approximate; connectivity under-approximates) resolve
them separately:

- **Obstacle.** For every net — third nets and the tied nets alike — the polygon is real copper.
  A netless full-width `Segment` along the rectangle's long midline over-approximates it: the
  router's ceil-rounded half width covers an odd-nanometre short side, and the stadium's end caps
  extend past the short edges, so the modelled copper is a strict superset of the drawn
  rectangle. Over-approximation can only refuse a route through the tie, never permit one.
- **Connectivity.** A joined-nets claim would have to be test-bound: the polygon demonstrably
  bridges copper of both pad groups, under a transform-aware exact-overlap proof that no current
  Board IR surface performs. Until such a proof exists, the only claim consistent with the
  under-approximation rule is *no claim*: `net_id None`, the exact contract net-0 copper already
  has (ADR-0078). The tied nets therefore report unconnected through the tie — a permanent,
  deliberate under-claim, recorded as risk [R-136](../ledgers/risk-register.md).

The rejected alternative — an explicit connectivity edge between the pad groups — is not wrong in
principle; it is unimplementable *soundly* at this slice's evidence level, because emitting the
edge without the bridge proof asserts a connection the file alone does not establish (a tie
polygon that misses its pads is representable in the format and silently broken on the board).

## Why the identity is revision-derived on purpose

An `fp_poly` is not a KiCad track: projecting its UUID onto a Board IR `Segment` would claim a
native identity for an object of a different kind, the exact category error D-158 closed for
reused UUIDs. The derived identity is also load-bearing for safety: both source-preserving patch
adapters refuse any snapshot that carries a `:derived:` identity (ADR-0026's mechanism), so a
board with a net tie is observable and routable in preview but not patchable — no write-back can
move the pads away from the copper that shorts them.

## What this note refuses to claim

- Nothing here verifies that the tie polygon actually touches its pads; the model makes no
  connectivity claim, so it does not need to.
- Nothing is claimed about multi-group ties, groups of other than two pads, non-rectangular tie
  copper, stroked outlines, or pad-overlap-only ties (groups declared with no polygon at all):
  each is unobserved in the surveyed tree and remains a typed refusal.
- Nothing is claimed about KiCad's DRC accepting the *converted* obstacle model; candidate DRC
  remains authoritative (ADR-0004) and unchanged by this decision.
