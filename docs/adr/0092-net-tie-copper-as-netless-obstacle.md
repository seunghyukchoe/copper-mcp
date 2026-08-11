# ADR-0092: Net-tie copper is a netless obstacle, and the tie is never a connectivity claim

- Status: Accepted
- Date: 2026-08-07
- Owners: `@seunghyukchoe`
- Related: [Issue #116](https://github.com/seunghyukchoe/copper-mcp/issues/116), ADR-0011,
  ADR-0026, ADR-0072, ADR-0078, [D-162](../ledgers/decision-ledger.md),
  [KiCad net-tie modelling](../research/kicad-net-tie-modelling-v1.md)

## Context

KiCad's `net_tie_pad_groups` declares that "nets attached to pads within a single pad-group are
allowed to short", and the footprint's filled copper polygon *is* that short — deliberate copper
belonging to two nets at once. Board IR models nets as disjoint, so D-162 recorded the refusal as
correct and explicitly declined to say how the construct *should* be modelled. The one real board
carrying it (a `NetTie-2_THT_Pad1.0mm` joining two ground nets: two filled `fp_poly` rectangles
on `F.Cu` and `B.Cu` plus one two-pad group) remained the survey's first refusal.

The copper asks two questions whose safe directions point apart:

1. **Is it an obstacle?** Yes, for every net. For a third net that is unambiguous; for the two
   tied nets it is still copper their new tracks must clear.
2. **Does it join the two nets?** On the fabricated board, yes. But connectivity claims must
   under-approximate, and this claim cannot be test-bound from the file alone: a group
   declaration with a polygon that misses its pads is representable and silently broken, so
   emitting a joined-nets edge on the declaration's word would assert a connection the evidence
   does not establish.

## Decision

**A net-tie footprint converts. Each filled tie polygon becomes a netless obstacle `Segment` —
`net_id None`, full width along the rectangle's long midline, revision-derived identity — and no
connectivity claim of any kind is made through it.**

- **Obstacle, over-approximated.** The segment's width is the rectangle's short side and its
  endpoints are the long side's extremes. Both routers model an orthogonal segment as its
  endpoint bounding box grown by `(width_nm + 1) // 2` on all four sides (`_segment_extent`,
  `_segment_bounds`), so the modelled copper is a strict superset of the drawn rectangle: on the
  short axis the ceil-rounded half width absorbs an odd side and the floor-rounded midline leaves
  at most one nanometre of slack on one edge, and on the long axis the square caps extend a half
  width past each short edge. `None` never equals a request net, so the copper is an obstacle for
  third nets *and* for the tied nets — over-refusal is the accepted direction, and it extends to
  the tie pads themselves, which the obstacle envelope may cover.
- **Connectivity, under-approximated to silence.** The tied nets remain two disjoint `Net`
  objects and report unconnected through the tie, exactly as netless stitching copper behaves
  under ADR-0078. The alternative — an explicit connectivity edge between the pad groups — is
  deferred, not rejected forever: it becomes admissible only with an exact, transform-aware proof
  that the polygon bridges copper of both groups, which no current surface performs.
- **Write-back stays refused, structurally.** The derived identity is deliberate (an `fp_poly` is
  not a track, so its UUID names no `Segment` — D-158's category rule) and load-bearing: both
  source-preserving patch adapters already refuse any snapshot carrying a `:derived:` identity
  (ADR-0026's mechanism), so no route or placement patch can ever separate the tie copper from
  the pads it shorts. A net-tie board is observable and previewable, never patchable.

### The accepted subset, as a closed field list

This is stated as an explicit list of fields rather than as prose, and that is a correction
rather than a presentation choice. **The first version of this record described the subset in
prose as "exactly what the survey observed", and it was not.** Two constructs the prose did not
cover were being accepted, and neither was found by reading the prose — one was found by
adversarial review and one by mutation:

- A **five-point ring** was accepted where the survey observed a four-point ring.
- A polygon with **no `stroke` field at all** was accepted, because the field was read as
  optional and its absence skipped the only outline-width check. That one ran in the forbidden
  direction: the entire argument for modelling the polygon as its long midline is that the drawn
  copper is the rectangle and nothing more, and only `(width 0)` establishes that. Any non-zero
  default behind an omitted field would have put real copper outside the modelled obstacle —
  an **under**-approximated obstacle, which the direction-of-error rule forbids outright.

A prose description is what let both through, so the contract is now a list that a reader can
check field by field and a test pins directly.

**Footprint** — `net_tie_pad_groups` required, appearing exactly once, holding exactly one group
of exactly two distinct pad names that the footprint actually carries (whitespace around names is
stripped, as KiCad writes `"1, 2"`). At least one tie polygon must convert.

**Tie polygon** — an `fp_poly` whose children are drawn only from
`{fill, layer, locked, pts, stroke, tstamp, uuid}`:

| field | required | accepted value |
|---|---|---|
| `pts` | yes | 4 points, or 5 where the last repeats the first (the closing point is dropped); an axis-aligned rectangle with 4 distinct corners after the footprint's orthogonal transform |
| `fill` | yes | `yes` |
| `stroke` | **yes** | `width` `0`; `type` unconstrained |
| `layer` | yes | one declared copper layer that **every** pad of the tied group occupies; never `Edge.Cuts` |
| `locked` | no | either; ORed with the footprint's own lock |
| `tstamp` / `uuid` | no | any; it names no `Segment`, so it is not read for identity |

Everything else is a distinct typed refusal — fourteen of them pinned. A five-point ring that
does *not* close still refuses, even though its corner set is a rectangle and accepting it would
be sound: the point count is what separates it from anything measured, and an unmeasured accept
is the thing this subset exists to avoid.

Requiring `stroke` costs nothing measurable. Across the survey corpus, all 331 `fp_poly`
expressions in 20 board files carry an explicit stroke, and KiCad 10 writes the net-tie polygons
as `(stroke (width 0) (type solid))`, so the omitted form is unobserved on real boards. This is
D-178's rule applied: accept only what is provably free of copper, refuse the rest, and pin the
refusal so nobody widens it later by accident.

No schema, codec, or digest change: netless segments have existed since ADR-0078, and boards
without net ties produce byte-identical content — the committed golden digests pin that.

## Consequences

- The net-tie refusal is gone as a survey cause, and **no additional board converts** — measured
  before and after, not expected. The one board that carried a net tie, `tier1-rev-a`, had three
  blockers stacked on it; this removes the first, D-178 removed the second (per-pad
  `zone_connect` overrides), and the third, `connect`-kind pads, still refuses. Its refusal
  therefore advances rather than clearing, and the corpus conversion count is unchanged at 11 of
  the 12 boards the #116 survey enumerated. Modelling an edge-connector pad is a separate
  contract decision and is not taken here.
- The connectivity under-claim is permanent until a bridge proof exists: a caller asking whether
  the two tied nets are connected is told less than the board knows. That asymmetry, and the
  fact that a router may consequently propose joining the tied nets with new copper elsewhere (which
  authoritative KiCad DRC would then flag), is recorded as [R-136](../ledgers/risk-register.md).
- The tie obstacle envelope can cover the tie pads entirely, making them unroutable at their own
  nets' clearances. Over-refusal is the accepted direction; the tied nets on real boards reach
  those pads through zones and existing tracks, not through new grid routes.
- Every net-tie board is write-back refused as a whole (derived identities refuse both patch
  adapters), so placement and route apply stay closed on such boards without any new guard.

## Alternatives considered

- **Model the tie as an explicit connectivity edge between the pad groups.** States the board's
  truth and would let both nets report connected through the tie. Rejected *at this evidence
  level*: without an exact overlap proof the edge is an untestable claim in the forbidden
  direction, and with pad-group trust alone a geometrically broken tie would still be reported
  connected. Revisit when an exact polygon-to-pad bridge check exists.
- **Obstacle for both nets via a `Keepout`.** Refuses the same routes but misstates the object:
  keepouts are rule areas, not copper, they prohibit pads and footprints placement-side, and the
  scene would show a rule the source does not carry. Rejected.
- **Assign the copper to one tied net.** Asserts a connection for one net and a foreign obstacle
  for the other on the same physical copper; both halves are guesses. Rejected without ceremony.
- **Keep refusing.** Sound but strictly less honest than modelling the obstacle: the board is
  readable, eleven other constructs on it are modelled, and the copper's obstacle role has an
  unambiguous safe direction. Rejected.

## Verification

`tests/test_net_tie_footprints.py` pins the contract end to end on an authored fixture: the
conversion shape (netless, full-width, derived-identity segments on both layers), the third-net
detour with a no-tie mutation control proving the obstacle is load-bearing, the absent
connectivity claim on the tied nets (`connected is None` where the only physical join is the
tie), the write-back refusal, and ten typed refusals for malformed ties.
`tests/test_kicad_board_ir.py` re-pins the rotated-footprint transform of the tie rectangle and
the write-back refusal on the subset fixture.

## References

- [KiCad S-expression format, footprint](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#_footprint)
- [FOOTPRINT class reference](https://docs.kicad.org/doxygen/classFOOTPRINT.html)
- [KiCad net-tie modelling research](../research/kicad-net-tie-modelling-v1.md)
- [Board IR contract](../architecture/board-ir.md)
