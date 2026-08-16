# ADR-0114: Accepted external candidates continue to private KiCad DRC

- Status: Accepted
- Date: 2026-08-17
- Owners: `@seunghyukchoe`
- Related: [ADR-0004](0004-authoritative-kicad-drc.md),
  [ADR-0109](0109-a-drc-count-carries-the-comparability-it-was-taken-with.md),
  [ADR-0112](0112-external-route-candidates-enter-through-a-disposer.md),
  [ADR-0113](0113-external-route-patches-preserve-multi-pin-topology.md),
  [D-211](../ledgers/decision-ledger.md), [R-163](../ledgers/risk-register.md),
  [SEC-152](../ledgers/security-ledger.md),
  [issue #99](https://github.com/seunghyukchoe/copper-mcp/issues/99)

## Context

ADR-0113 established corpus-calibrated structural acceptance but deliberately stopped at
`physical_validation: not_run`. The existing candidate-bound KiCad DRC path could not simply be
called with the reconstructed candidate: its serialization boundary requires an exact replay by a
registered CopperMCP router version, while a foreign candidate is intentionally identified as an
external disposer result. Treating that refusal as incompatibility would leave #99's authoritative
gate absent; registering the external policy as a router would falsely claim CopperMCP reproduced
geometry it only verified.

## Decision

Add a production-only, file-backed coordinator,
`verify_external_route_candidate_drc`. It accepts a typed `RoutePreviewRequest`, a foreign closed
v1/v2 document, runtime settings, coordinator-owned endpoint identities and work ceilings. The
typed request must explicitly set `include_drc`; the coordinator reads one confined board, derives
the source revision, constraint profile, Board IR snapshot and `RouteRequest`, enforces optional
source/snapshot preconditions, and invokes the existing bounded disposer. A structural refusal
returns without discovering or executing KiCad.

An acceptance creates a private disposition capability containing the immutable reconstructed
candidate. The ordinary serializer continues to require reference-router replay. A separate
private serializer is selectable only through the exact accepted disposition type; it omits only
the inapplicable router replay and retains source/profile-to-snapshot equality, candidate identity,
native-geometry identity, object/input budgets, deterministic segment identities and exact Board
IR round-trip equality. The existing DRC coordinator then re-reads the source, rejects a stale
candidate, snapshots the full DRC context, runs KiCad on a private patched board and discards the
result if the source or context changes during execution.

The result remains redacted. It returns the structural disposition plus the existing hash-bound
`RouteCandidateDrcEvidence`; no candidate geometry, board bytes, paths, workspace path, apply token
or mutation claim is serialized. `physical_validation: completed` means the authoritative command
completed and does not mean its summary passed. The live count is labelled
`drc_comparability: single_invocation`; a refusal has no DRC section and therefore no comparability
literal.

## Consequences

Closed v1 two-pad and v2 four-pad tree documents now pass from untrusted bytes through Board IR
acceptance, source-preserving private serialization and real KiCad 10.0.5 DRC without changing the
workspace file or entries. Candidate, source, patched-board and DRC-context revisions remain bound.
The five committed mutants covering opt-in, physical completion, evidence binding, serializer
selection and disposition type checks are all killed.

This is still not a public intake. Nothing is exported through MCP, CLI, persistence or apply, and
no new mutation authority exists. Issue #99 remains open for a deliberately versioned public
request/result contract and its transport-level resource and disclosure tests. DRC is not SI, PI,
EMC, thermal, DFM, fabrication or hardware validation, and a single invocation is not a
reproducible differential.

## Alternatives considered

Registering the external policy in the reference-router replay table was rejected because it would
turn verification into a false generation claim. Skipping serialization checks wholesale was
rejected because the Board IR round trip and source preservation are the evidence that KiCad saw
only the accepted patch. Exposing the seam through the existing permissive candidate validator was
rejected because it accepts caller-owned identity and metrics and has no geometry authority.
