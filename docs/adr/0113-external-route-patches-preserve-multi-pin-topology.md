# ADR-0113: External route patches preserve multi-pin topology

- Status: Accepted
- Date: 2026-08-17
- Owners: `@seunghyukchoe`
- Related: [ADR-0112](0112-external-route-candidates-enter-through-a-disposer.md),
  [B-120](../ledgers/benchmark-ledger.md), [D-210](../ledgers/decision-ledger.md),
  [R-162](../ledgers/risk-register.md), [SEC-151](../ledgers/security-ledger.md),
  [issue #99](https://github.com/seunghyukchoe/copper-mcp/issues/99)

## Context

ADR-0112 deliberately established a two-pad, one-path v1 disposer before corpus calibration. The
first B-088 replay exposed a contract fact that synthetic evidence could not: all 70 routed nets
are multi-pin, every candidate carries 2–20 paths, and no emitted path runs pad-centre to pad-centre.
Consequently exactly 0 of 70 source candidates can be represented by v1 without deleting branches
or inventing geometry. Treating one leg as the candidate would make a parser pass look like a route
verification result.

## Decision

Keep v1 unchanged and add the closed `copper-mcp/external-route-patch/v2` document. It replaces the
single ordered `segments` list with a bounded ordered `paths` list, each containing its own closed
ordered segment list. Revision, endpoint identifiers and optional vias retain their v1 meanings;
identity, net, settings, costs, metrics, ordering and budgets remain coordinator-owned.

The v2 validator checks every expanded lattice edge with the existing Board-IR obstacle predicate.
It then converts candidate tracks to exact under-approximating connectivity cores and joins them to
the coordinator-derived pad components under the same obstacle-check work meter. Acceptance
requires one component containing every pad component and every submitted path. A disconnected
branch is `infeasible`; malformed continuity, endpoints, layers and obstacle collisions retain the
existing typed codes. The exact off-grid fallback still upgrades only a physically proven collision.

The production seam remains core-only and read-only. MCP, CLI, persistence, apply, repair, KiCad
DRC and physical validation remain outside this increment.

## Consequences

B-120 re-expresses the 70 B-088 candidates without storing geometry in the result artifact. All
70 are accepted, while the fixed one-nanometre obstacle incursion, dropped middle segment,
wrong-pad endpoint and undeclared-layer via retain four distinct refusal codes. This closes the
first corpus-calibrated disposer slice, not issue #99's original authoritative KiCad DRC or public
intake work. `physical_validation` remains `not_run`.

The connectivity pass is quadratic in the number of pad and track cores, so it consumes the
existing coordinator obstacle-check ceiling and can refuse `budget_exceeded`. v1 remains supported
for genuine two-pad paths; v2 is not a reinterpretation of an already-published accepted set.
