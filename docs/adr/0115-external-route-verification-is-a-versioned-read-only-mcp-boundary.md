# ADR-0115: External route verification is a versioned read-only MCP boundary

- Status: Accepted
- Date: 2026-08-17
- Owners: `@seunghyukchoe`
- Related: [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md),
  [ADR-0112](0112-external-route-candidates-enter-through-a-disposer.md),
  [ADR-0113](0113-external-route-patches-preserve-multi-pin-topology.md),
  [ADR-0114](0114-external-candidates-continue-to-private-kicad-drc.md),
  [D-212](../ledgers/decision-ledger.md), [R-164](../ledgers/risk-register.md),
  [SEC-153](../ledgers/security-ledger.md),
  [issue #99](https://github.com/seunghyukchoe/copper-mcp/issues/99)

## Context

ADR-0112 and ADR-0113 established a bounded production disposer for closed v1 two-pad and v2
multi-path documents. B-120 then exercised all 70 routed B-088 cases and four predeclared
perturbations through that core. ADR-0114 continued an accepted disposition into authoritative
KiCad DRC on a private source-preserving board. None of those records defined a public transport:
the remaining machine-executable #99 gate was a deliberately versioned request/result boundary
with transport-level resource and disclosure tests.

The existing `validate_candidate` tool cannot fill that role. It validates caller-authored summary
manifests without reading a board, and therefore has neither geometry authority nor the
revision-bound coordinator state needed to reconstruct candidate identity. Widening it would also
mix a permissive compatibility surface with a closed hostile-input boundary.

## Decision

Add one structured MCP tool, `verify_external_route_candidate`. The MCP wrapper is exactly
`{"request": <envelope>}`. The inner envelope is a closed object with `schema_version: "1.0"`, a
reference-only route selector, one existing closed `copper-mcp/external-route-candidate/v1` or
`copper-mcp/external-route-patch/v2` document, and coordinator endpoint pad IDs. The selector takes
`net_ref_id`; it has no net-name alternative and requires both `expect_board_revision` and
`expect_snapshot_digest`. It may also carry the same bounded `seed` and routing `settings` policy
as a reference route request. Candidate identity, net binding and all metrics remain server-derived;
the obstacle ceiling is taken from those validated coordinator settings and the edge ceiling is
their grid-node ceiling capped at 4,096. The foreign document cannot supply policy, cost, identity,
evidence, settings or budgets, and the envelope has no standalone work-ceiling fields.

The public call always requests authoritative KiCad DRC. There is no `include_drc` switch and no
structural-only public success. The coordinator first performs the existing closed parsing,
revision checks, bounded Board IR reconstruction and structural disposal. A refusal executes no
KiCad process and returns the closed redacted `refused` variant with `physical_validation:
not_run`, a typed code, a fixed diagnostic and bounded aggregate counts. An acceptance continues
through the ADR-0114 private serializer and returns the closed redacted `accepted` variant with the
recomputed `candidate_id`, `physical_validation: completed`, candidate-bound aggregate DRC evidence
and `drc_comparability: single_invocation`.

`completed` means that the authoritative command completed and its bound evidence was published.
It does not mean that the DRC summary is `passed` or `clean`; clients must read both fields in the
summary. A single invocation is not reproducible differential evidence.

The tool is read-only, non-destructive and closed-world over both stdio and streamable HTTP. It has
no CLI, persistence, repair or apply peer. It never returns candidate geometry, paths, segments,
vias, coordinates, board bytes, board or net names, workspace paths, apply tokens, authorization
capabilities or mutation claims. It does not use live KiCad IPC and never modifies the source board.

## Consequences

The public contract is independently versioned from the v1/v2 foreign document schemas. Any
accepted-set change to the envelope or result requires a new public version under ADR-0105. Runtime
acceptance remains deliberately broad at the MCP framework layer while the application boundary
parses the advertised closed shape, preventing framework validation errors from echoing hostile
coordinates or board-private values. The outer wrapper is separately closed with a fixed error.

The machine capability in issue #99 may be closed only after the transport tests prove schema
closure, annotations, stdio/HTTP inventory, fixed non-echoing errors, server-derived budgets, the
accepted/refused union and output leak exclusions, and the full local validation gate is green.
B-120 remains the corpus evidence for the disposer; no B-121 is created by publishing a transport.

No result is electrical, SI, PI, EMC, thermal, DFM, fabrication or hardware validation. Human
benchmark calibration and physical-board testing remain outside this boundary.

## Alternatives considered

Reusing `validate_candidate` was rejected because it trusts caller-owned manifest fields and has no
board-backed geometry decision. Allowing a structural-only public success was rejected because it
would publish an accepted route without completing #99's authoritative gate. Accepting standalone
work ceilings or any budget inside the foreign document was rejected; the server derives them from
the bounded coordinator settings. Returning the reconstructed patch was rejected because
verification does not imply disclosure authority, and exposing apply or repair was rejected because
read-only verification is not mutation consent.
