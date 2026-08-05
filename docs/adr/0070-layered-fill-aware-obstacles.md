# ADR-0070: Shrink a layered zone envelope only against proved fill

- Status: Accepted
- Date: 2026-08-06
- Owners: `@seunghyukchoe`
- Related: [ADR-0021](0021-zone-fill-authority.md), [ADR-0039](0039-fill-aware-routing-obstacles.md),
  [ADR-0040](0040-public-fill-routing-provenance.md),
  [ADR-0036](0036-board-ir-layered-proposal-adapter.md),
  [ADR-0068](0068-bounded-ordered-layer-routing.md)

## Context

[ADR-0021](0021-zone-fill-authority.md) established the only evidence in this project that lets an
obstacle get *smaller*: KiCad refills a private disposable copy and the canonical island geometry
must equal what the cache already claimed, so an accepted record proves the cached islands are what
KiCad recomputes from this exact board revision. [ADR-0039](0039-fill-aware-routing-obstacles.md)
spent that evidence in the single-layer A* core, and [ADR-0040](0040-public-fill-routing-provenance.md)
made the effect visible at the `preview_route` boundary.

The ordered-layer adapter ([ADR-0036](0036-board-ir-layered-proposal-adapter.md),
[ADR-0068](0068-bounded-ordered-layer-routing.md)) was built independently and never received that
plumbing. It derives one axis-aligned envelope from each foreign zone's boundary bounding box, on
both the track and the via obstacle sets. A four-layer board with an inner-layer pour therefore
loses that layer entirely, even where the pour has a fill window. Its own research note already
listed zone refill among the unclosed gates.

## Decision

`LayeredRouteRequest` gains an optional `verified_fill` tuple of the same `VerifiedFill` value the
single-layer core accepts, so one caller-side freshness proof serves both routers and no second
evidence contract is invented. Preparation applies four rules, in order, before search:

1. **Shape.** Malformed evidence — a non-tuple, a non-`VerifiedFill` entry, an untyped net or layer
   identifier, a malformed source digest, a ring under three or over 4,096 vertices, a non-`PointNM`
   vertex — is `invalid_request` at the input boundary. Island count is capped at the adapter's
   obstacle ceiling, so preparation work stays bounded by declared limits.
2. **Revision.** An island whose `source_revision` differs from the snapshot's source revision is
   `stale_revision`. Evidence proved against another board is not evidence.
3. **Zone backing.** An island with no Board IR zone of the same `(net_id, layer_id)` is
   `unsupported_geometry`. An orphaned island cannot retire an envelope that does not exist.
4. **Containment.** An island whose bounding box is not contained in the bounding box of a backing
   zone is `unsupported_geometry`.

Only then does a foreign `(net_id, layer_id)` with at least one verified island stop emitting its
outline envelope, and each of its islands emit a track envelope and a via envelope instead, inflated
by `max(net-class clearance, zone clearance, foreign-net clearance)` plus candidate half-width or
candidate via radius, in exact integer nanometres and charged against the same obstacle budget.

Islands are carried as bounding boxes, not exact polygons: the layered lattice model is rectangular
by construction, and an exact polygon kernel there is a separate change with its own evidence. The
router still never runs KiCad, and the public `preview_layered_route` contract is unchanged — no
layered response yet claims fill-aware provenance, and a caller must reach the typed internal seam
to supply evidence at all.

## Direction of error

Rule 4 is what makes the shrink sound rather than assumed. KiCad clips poured copper to the zone
outline, so an honest island already satisfies it; checking it converts that into a verified
precondition. Given it, `island ⊆ island bbox ⊆ zone bbox` for every island, hence
`⋃ island bboxes ⊆ zone bbox`: the replacement can only ever remove blocked lattice area that the
outline envelope had blocked, never add copper the model fails to cover. The emitted obstacle still
over-approximates real copper twice over — bounding box, then the same clearance inflation and
half-cell lattice quantization the conservative path used. Absent evidence, evidence on an
unsupported layer, and any failure of rules 1–4 all keep the conservative envelope or refuse, so
every error direction remains refusal-side.

## Evidence

- `tests/test_layered_board_adapter.py` proves the corridor result (14,000 nm conservative to
  8,000 nm fill-aware with zero vias), an exact integer centreline-clearance check against the
  island, unchanged content-addressed identity semantics, a metamorphic monotonicity case over four
  nested islands, all three fail-closed gates, the malformed-input boundary, and obstacle-budget
  billing.
- B-085 records ten deterministic replays of both modes plus every refusal as a real invocation.
- The containment gate was mutation-checked: reverting it turns the escaping-island refusal into an
  accepted candidate and fails exactly one test; reverting the revision gate fails exactly one
  other. Both were restored.

## Consequences

The ordered-layer router can use real, freshness-bound pour geometry instead of writing off a whole
layer. The improvement is intentionally narrow: bounded two-pad ordered-layer proposals, rectangular
island envelopes, foreign nets only, and source-revision-bound evidence. It does not establish zone
refill correctness beyond what ADR-0021 already records, exact polygon layered collision, same-net
poured attachment in this seam, whole-board completion, electrical behaviour, DFM, fabrication
readiness, or Freerouting parity.

A follow-up must decide whether `preview_layered_route` should carry the ADR-0040 `routing_effect`
label; until it does, layered fill-aware routing is an internal capability and must not be described
as a public one.

## References

- [Layered fill-aware obstacles v1](../research/layered-fill-aware-obstacles-v1.md)
- [KiCad S-expression format: zones and `filled_polygon`](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html)
- [KiCad `ZONE_FILLER` class reference](https://docs.kicad.org/doxygen/classZONE__FILLER.html)
- [Freerouting copper-pour awareness request](https://github.com/freerouting/freerouting/issues/152)
- [B-085](../ledgers/benchmark-ledger.md)
