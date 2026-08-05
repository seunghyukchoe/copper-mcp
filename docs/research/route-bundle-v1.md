# Route-bundle preview v1

## Question

Can CopperMCP move from independent same-base route candidates to one small, read-only,
compositionally verified plan without creating an implicit apply/export channel?

## Sources and constraints

- [KiCad's current CLI manual](https://docs.kicad.org/10.0/en/cli/cli.pdf) documents
  `kicad-cli pcb drc`, JSON reports, and exit code `5` for reported violations. This is why the
  benchmark's KiCad check runs against a private derivative and reports aggregate findings only.
  It uses CopperMCP's bounded DRC adapter rather than an unbound PATH invocation: the adapter
  resolves and hashes the executable, creates private process state, caps report/stdout/stderr
  before decoding the report, bounds JSON structure, and rejects inconsistent report/exit evidence.
- [The MCP tool schema](https://modelcontextprotocol.io/specification/2025-06-18/schema)
  defines input/output schemas and `readOnlyHint`; the MCP adapter declares both, but the service
  is the actual no-write enforcement point.
- [ADR-0002](../adr/0002-mcp-adapter.md) requires MCP handlers to call a protocol-independent
  application service. [ADR-0055](../adr/0055-bounded-negotiated-congestion.md) supplies the
  existing bounded present/history occupancy coordinator and its PathFinder provenance
  ([McMurchie and Ebeling, 1995](https://doi.org/10.1109/fpga.1995.242049)).
  [ADR-0066](../adr/0066-atomic-route-bundle-preview.md) records the resulting public contract.

## Decision

`preview_route_bundle` accepts two through eight distinct Circuit Scene `net_ref_id` values plus
the source-board and Board-IR digest preconditions. It rebuilds one shared profile/snapshot,
uses the fixed deterministic negotiated coordinator, repeats the entire composition once, and
publishes a content-addressed `RouteBundlePlan` only when both results match, every requested net
has a two-pin candidate, no connection/unrouted result remains, no lattice overflow remains, and
the core's cross-net swept-disc physical-clearance gate accepted the allocation.

The input order is retained in the plan identity and seeds, while candidate storage remains
canonical by net ID. Only the *storage* order is canonicalized: whatever order the references
arrive in, the published candidate list and the digest's candidate-ID list are sorted by net ID, so
a caller cannot alter the shape of the response by reordering the request.

Reference order is deliberately not neutralized, because it is semantically meaningful. Each
reference derives its per-net search seed from its index, so permuting `net_ref_ids` permutes the
seeds and genuinely produces different candidate IDs and a different `bundle_id`. That is a
different request, not a different rendering of the same one. The request boundary therefore caps
the accepted seed so every derived `seed + index` stays inside the supported integer range.

The identity also binds the coordinator's `policy_digest`, which covers the iteration ceiling and
the penalty/budget envelope, so bundles composed from the same references under different
coordinator policy cannot collide.

For public-fixture evidence, an internal serializer verifies the plan identity and repeats the
bounded physical-clearance gate before emitting one disposable, source-preserving combined board.
It has no public MCP tool, no persistence, no apply token, and no file write.

## Evidence

[`2026-08-05-route-bundle-v1.json`](../../benchmarks/results/routing/2026-08-05-route-bundle-v1.json)
records the CopperMCP-original Apache-2.0 committed audio-fixture replay. Independent same-base
candidates share one lattice unit; the complete bundle has zero overflow, three pair checks, and
26 mm total candidate length. The SHA-256-bound resolved KiCad 10.0.5 CLI reported exit `0`, zero
errors, and zero unconnected items on the private combined derivative. The source fixture remained
unchanged.

## Nonclaims

This is limited to two through eight distinct, two-pin, one-layer, common-grid requests with the
existing Board IR subset. It does not support multilayer capacity, vias, zones/fill, arbitrary
rules, electrical verification, fabrication signoff, route application, candidate persistence,
public derivative delivery, or general-board scaling. The DRC result is evidence for the one
private derivative, not authority to apply the plan.
