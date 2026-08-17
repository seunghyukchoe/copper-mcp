# ADR-0116: Layered fill islands have a measured source boundary

- Status: Accepted
- Date: 2026-08-17
- Owners: `seunghyukchoe`
- Related: Issue #167, B-123, ADR-0104, ADR-0108, D-213, R-165, SEC-154

## Context

The ordered-layer adapter historically refused any verified-fill island above 4,096 vertices.
B-108 measured that 14 of 18 real-board pours cross that limit and that the widest observed island
has 43,889 vertices. The number therefore rejects ordinary evidence, but simply deleting it is not
safe. ADR-0108's later aggregate preflight limits a request to 10,000,000 vertices, yet successful
proposal and replay do more than one vertex predicate: shape validation walks every point,
`_points_bounds` scans the ring four times, and candidate identity materializes every point into
canonical JSON. Replay hashes the supplied fill before delegating to proposal, which hashes it
again.

The route search is a different population. Each admitted island is reduced once to one bounding
box and then to at most one track and one via rectangle. Layered A* charges rectangle relations to
`max_obstacle_checks`; it never revisits polygon vertices during `_blocked` or `_via_blocked`.
Consequently the source-validation/hash boundary and the search boundary need separate meters.

B-123 priced the source boundary in isolated subprocesses on the committed synthetic layered-fill
fixture. The predeclared 1,000,000-vertex gate required both one island and an equal-total ten-island
shape to complete proposal plus replay within 20 seconds. The split case took 20.24 seconds and
failed. The fallback 500,000 case took 10.15 seconds with 110,431,900 incremental traced bytes and
passed its 12-second/512,000,000-byte gate. The widest recorded corpus island took 1.50 seconds with
9,713,819 incremental traced bytes and passed its 5-second/256,000,000-byte gate. These timings are
one-host calibration evidence, not cross-machine guarantees.

## Decision

Raise the fixed per-island source-admissibility ceiling from 4,096 to **500,000 vertices**. Keep the
independent 10,000,000 aggregate preflight, the 4,096-island/obstacle domain ceiling, and the
per-request rectangle search meters unchanged.

The exact boundaries remain distinct:

- 500,000 vertices in one structurally valid island are admitted;
- 500,001 vertices refuse as `invalid_request` before bounding-box or identity work;
- aggregate work above 10,000,000 vertices refuses as
  `obstacle_check_budget_exceeded` before vertex traversal;
- derived fill rectangles continue to consume `max_obstacles`, and route relations continue to
  consume `max_obstacle_checks`.

The 500,000 ceiling is server-owned and not caller-configurable. It matches the shipped
`max_fill_vertices` default that produced this evidence without claiming that an operator-raised
one-million-vertex parse allowance must also be routable. Candidate identity, canonical bytes,
public schemas, diagnostics, routing geometry, and apply authority do not change.

## Consequences

Every island size observed by B-108, including 43,889 vertices, now reaches the existing safe
bounding-box model. The largest newly accepted ring can still be expensive, but the expense is
measured, finite, and bounded before both proposal and replay. A caller with a well-formed island
above 500,000 still receives the existing non-echoing malformed-input diagnostic; this is a source
admissibility boundary rather than evidence that the board is geometrically invalid.

B-105's route-quality result remains unchanged: this decision is justified by refusal reachability
and resource calibration, not by a claim that fill evidence unlocks routes. No private board bytes,
KiCad process, network, DRC, electrical, fabrication, or hardware evidence enters B-123.

## Alternatives considered

**Delete the per-island ceiling and rely on the 10,000,000 aggregate preflight.** Rejected because
that preflight counts lengths only and does not price repeated bounds scans or canonical JSON
allocation. It would make multi-gigabyte transient work plausible.

**Raise the ceiling to 1,000,000 because the parse setting permits it.** Rejected by the
predeclared split-island time gate. A parser allowance and a route-candidate identity allowance are
different costs and need not share a maximum.

**Keep 4,096.** Rejected because the public measurements already show it refusing 14 of 18 pours;
the 43,889 control passes far below the selected resource limits.

**Make the ceiling a request setting.** Rejected. Validation and hashing occur outside the layered
A* search work meter, so a client-selected search budget cannot authorize this preparation work.
