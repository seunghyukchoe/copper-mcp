# ADR-0087: A truncated scene withholds whole kinds instead of emptying them

- Status: Accepted
- Date: 2026-08-08
- Owners: `@seunghyukchoe`
- Related: ADR-0022, ADR-0023, #127, [D-173](../ledgers/decision-ledger.md),
  [R-130](../ledgers/risk-register.md)

## Context

A whole-board `observe_board_scene` on a real board returned `vias: []`, `zones: []` and
`rules: []` for boards holding up to 1,003 vias, 5 zones and a net class. Measured read-only over
the eleven-board mixer corpus at default settings, eight boards hit `max_scene_objects`; on `ch`
the vias, the zones and the only net class were all empty arrays.

The cause is arithmetic, not a bug in any one kind. The scene spent a single object budget in one
fixed emission order — outline, footprints, pads, keepouts, segments, arcs, vias, zones, rules —
and segments outnumber every other kind on a real board by two orders of magnitude. Segments come
fifth, so they consumed whatever the static kinds left and **every kind after them returned
empty**. Raising the ceiling does not fix it: at any ceiling below the board's object count the
same kinds vanish, and the corpus's largest board would need 33,181.

ADR-0022 already made truncation explicit: `ceiling_hit` was non-null and `objects_omitted` was
right. That record was not wrong, it was *elsewhere*. `vias: []` from a truncated scene was
byte-identical to `vias: []` from a board with no vias, so a caller asking "is there a via near
this pad?" was told no — and told it in the collection it was reading, while the correction sat in
a sibling object it had no reason to open.

This is the exact failure mode the project exists to refuse. CopperMCP models "we did not check
this" as an explicit one-value literal — `not_run`, `not_modelled`, `inconclusive`,
`untrusted_board_author` — precisely so that a missing check can never be read as a passing one.
A silently empty collection is optimistic absence wearing a valid response's clothes.

## Decision

**Every array a scene returns is complete for its region and layer filter. A kind that does not
fit is withheld whole, in its own slot, as a value that is not an array.**

Two halves, and neither works alone.

- **Kind-complete allocation.** The ceilings are offered to whole kinds rather than to individual
  objects. Each kind is filtered by region and layers, costed in objects and vertices, and then
  admitted only if all of it fits in what is left. Kinds are offered the budget in ascending
  object count, ties broken by the fixed declaration order — a deterministic order that maximises
  how many kinds are observed completely, and that puts the two kinds a caller needs in order to
  bound a follow-up request, the outline and the rules, ahead of the tens of thousands of segments
  that used to consume everything. A kind that does not fit is skipped rather than ending the
  allocation, so one enormous kind cannot withhold the small ones behind it.
- **A withheld kind is a different JSON type.** In place of the array, the slot carries
  `{"observation": "withheld_by_ceiling", "ceiling_hit": …, "objects_omitted": N}`.
  `observation` is a one-value literal in the sense ADR-0022 established for
  `untrusted_board_author`: there is no spelling of this object that means "observed and empty".

Together these give the invariant the old design lacked: **an empty array means the region holds
none of that kind, and nothing else.** A caller no longer has to read `truncation` to know that,
which matters because the caller who most needed the warning was the one who read the array and
believed it.

The type change is the load-bearing part. A count placed *next to* an empty array is only read by
someone who already suspects truncation. A value of a different type cannot be misread quietly:
`if not vias` is false, `len(vias) == 0` is false, `vias == []` is false, and iterating it yields
strings, so a consumer that walks the collection raises rather than finding nothing. Every naive
read either errors or reports that objects exist. That is the direction of error this project
chooses everywhere else.

`truncation` keeps its shape and becomes a summary: `objects_omitted` is exactly the sum over the
withheld kinds, and `ceiling_hit` names the ceiling of the first kind withheld.

The scene contract goes to `0.3.0`, because a strict client with a closed schema for
`static.pads: list` stops validating a truncated response. See
[the migration note](../migrations/copper-mcp-0.8.0.md).

## Consequences

- A dense whole-board scene now returns *fewer* objects and *more* truth. On `ch` the old response
  carried 2,000 objects including 1,217 of 31,181 segments; the new one carries 1,792 — every
  footprint, every pad, all 1,003 vias, all 5 zones, the net class and the outline — and says
  `segments: {"observation": "withheld_by_ceiling", "objects_omitted": 31,389}`. The 1,217
  segments that were dropped were an arbitrary prefix that no question about clearance, congestion
  or connectivity could be answered from, and they were labelled as the whole set. Removing them
  removes a falsehood, not information.
- The measured corpus, re-run read-only at default settings after the change: of eleven boards that
  convert, eight truncate, **all of them on `segments` alone**, and no other kind is empty on any
  board that has one. Bounded regions are untouched — 11 of 11 return `objects_omitted: 0` with
  every kind present as a complete array.
- A caller that wants the segments of a dense board must scope a region, which the bounded path
  already supports and which is what ADR-0022 asks of every caller anyway. The whole-board request
  remains the way to bootstrap: it returns the outline the caller needs in order to choose that
  region.
- Which kinds fit now depends on the board's contents, so adding objects to a board can change
  which kinds are withheld. Output stays deterministic for a given board revision and request,
  which is what the replay contract requires; it was never stable across revisions.
- Peak memory is bounded by the object ceiling rather than by the board: kinds are counted before
  they are built, and nothing is constructed for a kind that will be withheld.
- `max_scene_objects` keeps its provisional default of 2,000. This ADR deliberately does not raise
  it, because the defect was never the ceiling's height ([R-130](../ledgers/risk-register.md)).

## Alternatives considered

- **Refuse a whole-board scene that would truncate, and direct the caller to a region.** The most
  conservative option, and the one this record came closest to taking. Rejected on two grounds.
  First, there is no whole-board mode to refuse: ADR-0022 makes a region mandatory and offers no
  shorthand, so the server cannot distinguish "the whole board" from "a large region the caller
  chose" except by predicting the object count — refusing a request whose region the caller
  deliberately picked, on a threshold the caller cannot see. Second and worse, it is
  un-bootstrappable: a caller can only choose a bounded region after it has seen the outline, and
  the outline costs one object. Refusing the whole request denies the cheap kind in order to
  withhold the expensive one. Withholding the expensive kind alone achieves the honesty without
  the deadlock.
- **Per-kind returned/omitted counts beside the arrays.** The suggested direction in #127, and the
  cheapest to build — the allocator already knows what it rejected. Rejected because it leaves
  `vias: []` a bare empty array and relies on every consumer reading the count next to it. That is
  exactly the assumption that failed: `objects_omitted` and `ceiling_hit` already existed, were
  already correct, and were already not enough. Adding a second record in the same shape multiplies
  the number of places a caller must look without changing what happens when it does not look.
- **Proportional fill across kinds.** Guarantees every kind appears, which sounds like the fix and
  is the opposite of it. A proportionally filled scene returns `vias: [12 of 1003]` — an array that
  looks complete, is not, and carries no marker of its own. It converts one lying collection into
  nine. Kind-*complete* filling is the half of this idea that is sound.
- **Raise `max_scene_objects` to fit the largest real board.** Not a fix at any value: the defect
  reappears at the first board above whatever number is chosen, and 33,181 objects is not a
  response size this server should emit by default. The ceiling's job is to bound the response;
  making truncation legible is a separate job and this ADR does that one.
- **A sentinel object inside the array** — `vias: [{"kind": "withheld", …}]`. Non-empty, so the
  falsy test works. Rejected because `len(vias)` then reports 1 for a board with 1,003, which is a
  wrong claim rather than a refusal to claim, and because it puts a non-object in a collection
  whose every other element is a real board object.
