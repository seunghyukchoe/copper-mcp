# ADR-0112: External route candidates enter through a bounded disposer

- Status: Accepted
- Date: 2026-08-17
- Owners: `@seunghyukchoe`
- Related: [D-209](../ledgers/decision-ledger.md),
  [R-161](../ledgers/risk-register.md),
  [SEC-150](../ledgers/security-ledger.md),
  [issue #99](https://github.com/seunghyukchoe/copper-mcp/issues/99),
  [ADR-0001](0001-candidate-first.md),
  [ADR-0006](0006-bounded-deterministic-astar.md),
  [ADR-0103](0103-a-candidate-records-the-model-that-produced-it.md)

## Context

The milestone needs to judge route geometry produced outside CopperMCP. The existing
`tools.validate_candidate` cannot do that: it normalizes a manifest and returns `valid` without
binding the geometry to Board IR or checking obstacles. Treating a foreign document as a
`RouteCandidate` would also let its author choose identity, net binding, settings, costs and
metrics that belong to the deterministic core.

The first implementation must establish the production boundary before importing benchmark
formats. B-088 has 70 routed nets that can later exercise it, while the committed SimpleRouteJson
corpus carries problems but no solution traces. Benchmark conversion therefore remains a separate
evidence step rather than becoming a production dependency.

## Decision

Add a read-only production-core disposer, `verify_external_route_candidate`, with no MCP, CLI,
apply, persistence or benchmark exposure in this slice.

The accepted document is the closed `copper-mcp/external-route-candidate/v1` object. It carries a
problem revision, two endpoint pad IDs, ordered single-layer segments, and an optional via list.
Segments carry only layer, width and integer-nanometre endpoints. Unknown keys and foreign
candidate IDs, net IDs, settings, policy, costs, metrics and evidence are refused.

Coordinator-owned Board IR, `RouteRequest`, endpoints and work budgets are canonicalized before
nested traversal. The disposer verifies the snapshot, validates closed objects with constant
expected-key membership after an exact length check, bounds segment and path work, and checks
cancellation and deadlines. It reconstructs the immutable route candidate from trusted state,
recomputes its canonical identity, and delegates legality to `validate_candidate_path`.

The v1 geometry subset is orthogonal and single-layer. A via naming an absent layer is
`undeclared_layer`; a via whose layers exist is `unsupported_geometry`. Discontinuity, endpoint
mismatch, stale revision, obstacle violation and resource exhaustion remain distinct refusals.
Collinear vertices are compressed only when motion remains monotonic; a reversal is retained and
refused rather than deleting an excursion from the geometry that reaches validation.
The validator also exposes one bounded exact-edge wrapper for the special case in which the main
lattice gate reports an orthogonal path off-grid: only a physically proven collision upgrades the
result to `obstacle_violation`; a legal off-grid path remains `unsupported_geometry`, and both
passes share one obstacle-check budget.

The result is redacted. An accepted result may disclose the recomputed candidate ID. A refusal
discloses a fixed typed code and diagnostic. Both may disclose bounded segment, edge-check and
obstacle-check counts and always state `physical_validation: not_run`. Geometry, board content,
names, paths, apply tokens and mutation claims are never returned.

## Consequences

External proposers now have a production-core verification target whose authority is the same
immutable Board IR and candidate-path validator as local routing. Hostile type-exact coordinator
objects fail closed, caller dictionaries cannot buy an unbounded key scan, and production code has
no dependency on the benchmark-only SimpleRouteJson importer or private A* preparation symbols.

The seam is deliberately not yet a user-facing feature. Issue #99 remains open until all 70 B-088
routes are re-expressed under the closed document and every unperturbed route is accepted while the
four predeclared perturbations produce distinct codes. The focused synthetic acceptance and four
refusal tests establish the vertical slice, not corpus coverage, route quality, KiCad DRC,
electrical correctness, fabrication readiness or physical validation.

## Alternatives considered

Extending `tools.validate_candidate` was rejected because its manifest vocabulary already treats
caller-supplied identity and metrics as the document, while the disposer must recompute them.
Importing SimpleRouteJson in production was rejected because benchmark formats are adapters, not
trust boundaries. Relabelling the 1 nm off-grid case as an obstacle failure was rejected; the exact
fallback reports that code only when the Board IR obstacle predicate proves the collision.
