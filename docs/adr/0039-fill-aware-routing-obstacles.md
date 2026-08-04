# ADR-0039: Freshness-bound fill islands as routing obstacles

- Status: Accepted
- Date: 2026-08-05
- Owners: `@seunghyukchoe`

## Context

The conservative router treats a selected-layer foreign zone outline as solid copper. That is
sound when KiCad's cached `filled_polygon` data may be stale, but it can reject a legal route
through a real pour void. ADR-0021 already defined an opt-in refill authority: KiCad refills a
private disposable copy and the canonical island geometry must match the captured cache. The A*
core previously used that evidence only to recognize same-net connectivity; it did not improve
obstacle geometry.

KiCad's board file format emits derived fill islands, while the KiCad CLI provides a bounded DRC
and refill path. The evidence must therefore remain out of Board IR and be passed explicitly to the
router, so a snapshot digest never silently changes when a fill cache changes.

## Decision

Extend the deterministic single-layer A* preparation path:

1. Every supplied `VerifiedFill` must match the Board IR source revision and a parsed zone with the
   same net/layer. A stale or orphaned island fails closed before search.
2. For a foreign net on the selected layer, a zone with at least one matching verified island is
   removed from the conservative outline-obstacle list. Each verified island becomes an exact
   polygon obstacle, inflated by the stricter routed/zone net-class clearance and charged against
   the same object and obstacle-check budgets.
3. Same-net verified islands retain their existing connectivity role; they are never turned into
   obstacles for the routed net. Unverified zones remain conservative solid envelopes.
4. The router remains pure and never runs KiCad. The public `preview_route` response does not yet
   claim fill-aware provenance for routed candidates; a separate response-contract change must
   make the opt-in mode visible before it is promoted as a public capability.

## Evidence

B-021 uses an independently generated rectangular zone with a verified upper fill island and a
1,000 nm-grid two-pin route through the lower void. Ten deterministic replays reduce the route from
14,000 nm under the conservative envelope to 8,000 nm with exact fill obstacles. A matching-zone
refusal and stale-source regression keep the boundary fail closed.

## Consequences

- The search can use real, freshness-bound copper geometry without trusting a cache by default.
- The improvement is intentionally narrow: single-layer two-pin/multi-pin A*, polygon islands, and
  source-revision-bound evidence only. It does not establish zone refill correctness beyond KiCad's
  report, whole-board completion, negotiated congestion, electrical performance, DFM, or
  fabrication readiness.
- A future public response must report that fill-aware mode was used and bind any optional DRC
  evidence to the same source/context revisions.

## References

- [KiCad board file format: zones and filled polygons](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/)
- [KiCad command-line interface: PCB DRC and refill](https://docs.kicad.org/10.0/en/cli/cli.html#pcb_drc)
- [ADR-0021: Zone fill authority](0021-zone-fill-authority.md)
- [B-021](../ledgers/benchmark-ledger.md)

## Follow-up

ADR-0040 promotes the freshness record to routed `preview_route` responses when explicitly
requested, with a typed `routing_effect`; this does not change the pure-router or fail-closed
boundaries decided here.
