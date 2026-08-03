# ADR-0019: Route multi-pin nets by deterministic component merging

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`
- Related: ADR-0016, ADR-0018, [multi-pin routing references](../research/multi-pin-routing-references.md)

## Context

ADR-0016 gave the router connectivity analysis over a net's pads and its existing copper, and a
later slice widened that analysis to nets of any pad count. Recognition therefore already spans
multi-pin nets; only *routing* them was missing, and a net with more than two pads was refused with
`invalid_two_pin_net` however well understood its geometry was.

The remaining problem is topology. Two pads have one path; N pads have a tree, and choosing a good
tree is the rectilinear Steiner minimal tree problem. The survey in
[multi-pin routing references](../research/multi-pin-routing-references.md) records the options and
their licensing: FLUTE is BSD-3 and optimal to degree 9, GeoSteiner is an exact oracle, freerouting
is GPL-3.0 and therefore off limits as a code source for this Apache-2.0 repository.

## Decision

A net with more than two pads is routed by **sequential component merging**, recorded in the
candidate as the ordering policy `component-mst-v1`.

- Connectivity analysis produces the net's initial components — pads plus whatever same-net copper
  already joins them. One component means the net is already connected, which is the existing
  terminal outcome, unchanged.
- Otherwise the components are spanned by a **minimum spanning tree**, edges weighted by the exact
  integer rectilinear gap between component bounding boxes and ordered by
  `(gap, lower index, higher index)`. The order is a pure function of the snapshot.
- Each MST edge is one **leg**, routed by the existing multi-source/multi-target A*: the source
  component's copper supplies the seed nodes and the target component's copper the goal nodes, both
  through the same `attachment_nodes` index-range enumeration used for stubs.
- After a leg is routed, its copper joins the merged component, so later legs may attach anywhere
  along it. Legs are same-net copper and therefore never obstacles to one another.
- Any leg failing refuses the whole call. A partial tree is not a candidate.

**What is claimed**: every pad of the net ends in one component; each leg is optimal for the
obstacles present when it was routed; the result is deterministic and exactly reproducible.
**What is not claimed**: Steiner optimality, or optimality of the tree as a whole — an earlier leg
is never revisited once a later one is routed.

`RoutePatch` becomes a tuple of `RoutePath`s so one candidate can carry a tree. Two-pin proposals
carry exactly one path and record the ordering policy `single-path`.

## Consequences

- Multi-pin nets route. A four-pad star fixture becomes a three-leg tree that KiCad 10.0.5 accepts
  with zero errors, warnings, and unconnected items.
- `ROUTER_VERSION` advances to `astar-grid/0.4.0` and the preview response carries
  `patch.paths[].vertices_nm` instead of `patch.vertices_nm`. This is a breaking response change,
  taken deliberately while the project is pre-1.0 rather than maintaining two candidate shapes
  forever.
- Multi-pin legs seed from **pad cores** rather than pad centres. Requiring every pad centre to sit
  on one lattice is unworkable: on the repository's own CopperTone board the largest grid step that
  puts all pads of a multi-pin net on one lattice is 5 µm for six of the nine such nets, which is a
  62-million-node lattice — 250 times the hard ceiling. Seeding from cores removes the constraint
  for every pad but the anchor. Two-pin nets keep centre seeding, so their geometry is unchanged.
- Budgets are shared across the whole tree rather than per leg, because one candidate should honour
  one ceiling. Merge order and budget consumption are both deterministic, so budget exhaustion
  fails at a reproducible leg with reproducible counts.
- Recording the ordering policy in candidate identity makes a better topology additive: a
  FLUTE-guided or learned ordering becomes a new policy string behind the same contract, with
  replay determinism preserved. That is deferred to its own ADR.
- This slice is validated by fixtures, not by CopperTone. Every net on that board is already
  routed by its designer, so multi-pin routing changes nothing there — see the routing baseline.

## Alternatives considered

- **A separate `RouteTreeCandidate`**: rejected. It would duplicate identity derivation, preview
  shaping, DRC replay and the oracle across two types permanently to avoid one pre-1.0 bump.
- **FLUTE / RSMT topology now**: rejected for this slice. An RSMT computed on pad centres ignores
  obstacles, so it is a guide rather than an answer on an obstacle-laden lattice, and vendoring the
  lookup tables is a dependency decision deserving its own record. The `ordering_policy` field is
  the seam that keeps it additive.
- **Per-leg budgets**: rejected. An N-pad net would be able to do N times the work the caller
  authorised.
- **Emitting a partial tree when a leg fails**: rejected. It would violate the candidate invariant
  that a proposal has no unrouted connections, and a partly connected net is not a proposal.
- **Requiring every pad centre on one lattice**: rejected as measured above.

## References

- [ADR-0016](0016-same-net-attachment.md)
- [ADR-0018](0018-diagonal-attachment-cores.md)
- [Multi-pin routing references](../research/multi-pin-routing-references.md)
- [Deterministic A* routing baseline](../architecture/routing-baseline.md)
