# ADR-0021: Trust poured copper only against a fresh KiCad refill

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`
- Related: ADR-0005, ADR-0013, ADR-0020

## Context

A `filled_polygon` node records where KiCad poured copper at some past moment. Nothing in the file
says whether it still matches the board around it: move a track, and the cache describes copper that
no longer exists. ADR-0013 therefore made a zone a conservative *boundary* obstacle and treated its
fill as untrustworthy, and Board IR discards `filled_polygon` entirely rather than carrying geometry
it cannot vouch for.

That was right, and it was also the last thing standing between the repository's own CopperTone
board and a fully recognised netlist: `GND` has twelve pads joined by a ground pour, and no amount
of via-awareness helps when the copper doing the joining is a pour.

## Decision

Cached fill may be used as connectivity evidence **only when a fresh KiCad refill reproduces it**.

- **Compare, do not substitute.** KiCad refills a private disposable copy and the recomputed fill is
  compared against the cache. Matching means the two are the same geometry, so there is no question
  which one a claim describes. Mismatch is a typed `stale_fill` refusal; neither version is silently
  preferred.
- **The comparison is over canonical geometry, not bytes.** KiCad rewrites and reorders a board
  wholesale on save, so a byte diff of the file says nothing about whether the fill changed. Islands
  are sorted and digested by layer, net and exact integer vertices.
- **An island is the unit, not a zone.** Verified empirically against a board authored to force two
  disjoint regions: KiCad 10.0.5 emits one `filled_polygon` node per connected region rather than
  one node per zone stitched by keyhole seams. Copper touching *different* islands is not connected,
  and a committed fixture pins that.
- **Freshness is a type invariant.** `ZoneFillAuthority` refuses construction when its two digests
  differ, so a stale record cannot exist to be misread.
- **The workspace board is never refilled.** `--refill-zones --save-board` is passed only to the
  disposable copy; every other DRC path in this repository omits it, three existing assertions keep
  it that way, and the source is recaptured and compared afterwards.
- **Opt-in only**, via `include_fill_authority`. It spawns KiCad, so it must never happen implicitly
  on a call that otherwise reads nothing.
- **Fill stays out of Board IR.** The router accepts verified fill as a parameter and never fetches
  it, so snapshots and their digests are unchanged and KiCad execution stays out of the search.
- Scope is **connectivity only**. Using exact fill as a tighter routing *obstacle* would change
  routed geometry on every zoned board and needs its own measurement.

## Consequences

- CopperTone reaches **14 of 14** recognised nets: `GND` is joined by two fill islands and six vias.
  Without the flag it still refuses, which is the honest default rather than a regression.
- The claim costs a KiCad subprocess bounded by the same deadline, private state, and byte ceilings
  as every other KiCad call, plus a `max_fill_vertices` ceiling defaulting to 50,000. CopperTone's
  pour is 4,314 vertices across two layers.
- A board whose author has not refilled since editing gets `stale_fill` rather than a wrong answer.
  That is a real workflow cost and the diagnostic says what to do about it.
- Evidence is version-bound: fill depends on the KiCad that computed it, so `kicad_version` is
  recorded and the evidence is not portable across versions.
- Contact testing uses the polygon itself, because once freshness-bound it is KiCad's own authority
  on where that copper is. Pad and track cores remain under-approximating; only the pour is exact.

## Prior art

This is a **verifying trace** in the sense of Mokhov, Mitchell and Peyton Jones, *Build Systems à la
Carte* (Journal of Functional Programming 30, e11, 2020): a cached derived output is valid exactly
when the digests of its inputs still match, and is otherwise rebuilt. The pour is the derived
output, the board around it is the input, and matching digests are the **early cutoff** — fill
unchanged, so the evidence stands and no work is wasted. Bazel's content-addressed action cache and
self-adjusting computation in the Adapton and Salsa line are the same family.

One difference is worth naming, because it is where the analogy stops. A build system responds to a
stale trace by rebuilding and then *using* the rebuilt output. Here the artifact is the user's board
file, which is not ours to rebuild: a stale cache produces a refusal, not a substitution. The
recomputed pour exists only to answer whether the cache is current, and is discarded either way.

## Alternatives considered

- **Always recompute and use the refilled geometry, skipping comparison**: rejected. It answers
  "would this net be connected if you refilled?" when the user asked "is my board connected?" Those
  differ exactly when the cache is stale, which is the only case that matters. The file is what gets
  fabricated.
- **Trust the cache without refilling**: rejected outright — it is the status quo ADR-0013 refused,
  and it cannot distinguish a current pour from an abandoned one.
- **Store fill in Board IR**: rejected. It would put unverified derived geometry into a snapshot
  digest and change the identity of every zoned board, contradicting ADR-0005.
- **Fold this into `include_drc`**: rejected. That flag binds a *candidate* to DRC and is skipped on
  the already-connected path; overloading it would resurrect DRC where there is no candidate.
- **Digest-only freshness without loading geometry**: rejected as a false economy — usable geometry
  has to be loaded anyway to answer the connectivity question.

## References

- [ADR-0005](0005-canonical-board-ir.md)
- [ADR-0013](0013-polygon-zone-obstacles.md)
- [ADR-0020](0020-via-aware-connectivity.md)
- [Deterministic A* routing baseline](../architecture/routing-baseline.md)
- Mokhov, Mitchell & Peyton Jones, "Build Systems à la Carte", *Journal of Functional Programming*
  30, e11 (2020) — verifying traces and early cutoff
