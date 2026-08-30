# ADR-0127: Multi-pin local repair replaces one proven responsible branch

- Status: Accepted
- Date: 2026-08-29
- Owners: `@seunghyukchoe`
- Related: [Issue #90](https://github.com/seunghyukchoe/copper-mcp/issues/90),
  [ADR-0055](0055-bounded-negotiated-congestion.md),
  [ADR-0117](0117-local-exact-repair-is-an-opt-in-verified-transaction.md),
  [ADR-0126](0126-negotiated-routing-admits-bounded-multi-pin-nets-on-request-local-lattices.md),
  [D-235](../ledgers/decision-ledger.md), [R-185](../ledgers/risk-register.md),
  [SEC-171](../ledgers/security-ledger.md), [B-140](../ledgers/benchmark-ledger.md), and the
  B-141 differential records [D-236](../ledgers/decision-ledger.md),
  [R-186](../ledgers/risk-register.md), [SEC-173](../ledgers/security-ledger.md),
  [B-141](../ledgers/benchmark-ledger.md)

## Context

B-140 confirmed that the request-local 2–32-pad coordinator reaches a complete-allocation physical
clearance violation on all 16 admitted corpus boards, but every responsible candidate is multi-pin.
ADR-0117 can repair only one complete two-terminal candidate. A multi-pin tree cannot soundly enter
that transaction by pretending one endpoint pair represents the whole candidate: the whole-set
gate names two nets, not the particular path pair that reproduced the violation, and rebuilding a
candidate from one local route could silently delete, reorder or disconnect every other branch.

The missing capability is therefore not a new search algorithm. It is a bounded authority chain
from an exact physical path-pair reproduction, through one capability-bound branch selection, to a
complete immutable candidate reconstruction that is independently validated as a tree and checked
again against every other candidate.

## Decision

### Prove one responsible branch before deriving a window

When a rejected allocation contains an eligible two-pad target, the established ADR-0117 path keeps
absolute precedence and all its provenance and identity bytes remain unchanged. Only when no such
target exists may the opt-in repair transaction consider a 3–32-pad candidate.

The coordinator orders candidate IDs, target paths, conflicting candidates and conflict paths
deterministically. Before viewing geometry it computes the complete compressed-segment pair-work
bound. Responsibility work is capped at 65,536 pair checks, charged to the envelope's physical-work
budget, and the current complete candidate-set recheck is reserved from the same budget. A failed
preflight performs no path-pair verification. Cancellation or any typed verifier refusal publishes
no candidate or repair evidence.

Each candidate/path view is an identity-valid, single-path internal value created solely for the
existing exact physical-clearance predicate; it is never publishable. The first ordered path pair
that independently reproduces `CLEARANCE_VIOLATION` yields a private
`CoordinatorTreeRepairSelection`. That value binds the immutable snapshot and envelope, iteration,
both complete candidate identities, path counts and indices, both path digests, the target branch
endpoints, and the redacted responsibility digest and consumed check count. A module-private
capability and a domain-separated integrity digest are required before selection may become repair
provenance.

### Reconstruct one path and dispose the complete tree

Tree provenance uses the selected target branch's first vertex as its authoritative local origin,
requires every target vertex to be exactly congruent with that lattice, and projects all conflicting
candidates conservatively under one cumulative cell budget before enumerating any blocked cell.
Every unselected target branch is also projected as unavailable same-net copper, expanded by the
trace-width lattice radius, except for the selected branch's two already-authorized endpoints. Its
compressed-segment expansion work is preflighted cumulatively against the same 4,096-cell ceiling
before any blocked-cell enumeration; each actual insertion observes cancellation and charges the
consumed work. This prevents the local solver from retracing an unselected edge or attaching to it
at a new point. The local exact solver receives only the bounded window, endpoints and blocked cells.

Successful local output replaces exactly the selected path. Path count, path order and every
unselected `RoutePath` remain identical; revision, net, layer, width, pad endpoints and count,
settings, seed, router and policy labels, ordering policy and fill binding remain identical. Only
the selected path and its derived candidate identity may change freely. Geometry-derived length,
bends, bend cost and wire length are recomputed; the rejected candidate's proximity/via accounting
and deterministic search metrics remain inherited. Local-repair expansions and validator work are
recorded separately in success evidence and coordinator totals, not rewritten as historical search
metrics; successful tree evidence and totals include every consumed untouched-branch insertion.
Tree attempts reserve their worst-case projection plus validator work against the global obstacle
ceiling before probing. Board-IR projection uses the coordinator cancellation callback, and a failed or cancelled
projection carries its already-consumed obstacle and untouched-expansion work through a closed
internal refusal so later iterations cannot spend work that disappeared from the global account.

A private negotiated-tree validator verifies both complete candidate identities, the exact
preservation and accounting rules above, every expanded selected-layer edge against Board IR, an
acyclic canonical unit-edge graph, no contact with an untouched path or trusted same-net component
outside the original attachment cells, and one connected component containing every selected-layer
pad and every submitted path. Before Board-IR preparation it preflights one exact shared edge ledger
for the original topology, reconstructed topology and reconstructed final edge pass; every evaluated
path, pad and edge contact predicate is charged to the obstacle budget and observes cancellation
before evaluation. After reconstruction and before validation or evidence construction,
the coordinator recomputes the exact complete candidate-set pair-check bound. A grown route that no
longer fits the remaining physical budget refuses there. The coordinator then binds the accepted
candidate into the existing repair composite identity, replaces no other candidate, and reruns the
ordinary whole-set exact physical-clearance verifier. Only that final clean result may publish
success evidence. A topology, accounting, validator, final-clearance, budget or cancellation refusal
discards the reconstructed candidate and all repair evidence atomically.

### Keep the capability internal

This decision widens only the existing opt-in `negotiate_routes(..., repair_settings=...)`
transaction. It adds no MCP, CLI, schema, durable job, persistence, external document, apply, live
editor or board-write surface. Diagnostics and success evidence remain fixed, aggregate and
non-echoing; no path, coordinate, pad, net or board content is added to a result.

## Consequences

- The coordinator can now attempt one exact local replacement for a 3–32-pad tree without
  laundering one branch into a complete candidate.
- Two-pad transactions keep selection precedence and their prior identity bytes. An unused repair
  option still changes no default result or candidate identity.
- Deterministic first-responsible selection and one attempt can decline an allocation another
  branch or a later attempt could repair. Conservative projection can also overblock. Both are
  bounded completeness costs, not safety relaxations.
- A repaired branch may contain more segments than the rejected branch. Its exact reconstructed-set
  work is checked against the actual remaining envelope budget before the validator or final
  verifier runs; a route that no longer fits refuses atomically.
- One branch repair cannot be assumed to resolve every physical conflict. The mandatory final
  whole-set pass is the authority and may discard an otherwise valid reconstructed tree.
- B-140 motivated this capability but did not run it. Issue #90 remains open until the exact B-140
  population is replayed with repair enabled and a predeclared held-out differential demonstrates
  deterministic improvement without weakening any work or physical gate.
- B-141 is that repair-enabled replay and records a positive **completion** differential on the
  exact immutable population: treatment completes one board and two nets where the uninstrumented
  control completes none. The result is evidence that the private transaction was reached and
  published once under its existing gates; it is not, by itself, a routing-quality or
  generalisation result. Per-reason refusal/outcome reconciliation is part of the evidence
  contract, and a later checkout or merge-ref movement may load the artifact without replacing its
  historically recorded source commit. Issue #90 therefore remains open for human review and
  calibration of the benchmark interpretation and for any further held-out quality decision.

## Evidence and limits

Focused tests exercise deterministic first- and last-path responsibility, exact preservation of
every unselected path, one accepted 3-pad target, one accepted 32-pad target with 30 of 31 paths
unchanged, stale and forged bindings, cumulative conflict and untouched expansion-work projection
preflight, many short untouched segments refusing before enumeration, mid-enumeration cancellation
with exact consumed work, untouched-target projection,
complete-tree connectivity and acyclicity, a re-signed new-contact loop, inherited positive
proximity/search accounting, re-signed accounting tampering, original and grown-route final-work
preflights, exact shared topology-edge accounting, charged mid-scan contact cancellation, preserved
work on one-check/max-check/post-projection refusals, cancellation during Board-IR projection,
cancellation at responsibility, validator refusal, absence of a reproducing path pair, and both
final physical violation and cancellation. The established two-pad candidate IDs,
composite digest and provenance digest remain pinned.

The committed mutation spec
[`2026-08-29-negotiated-multipin-branch-repair.json`](../mutants/2026-08-29-negotiated-multipin-branch-repair.json),
SHA-256 `d300bc240fd390a15f47249178e93c1705f7bf81be99d055a105c447e98d4e6b`,
uses the reproducible harness on Python 3.12.13 / macOS-26.5.2-arm64-arm-64bit. Thirty-five mutants
are killed with zero survivors: first-path-only selection, clean-pair attribution, dropped
path-index and responsibility bindings, forged selection capability, deleted or unguarded
unselected paths, skipped untouched-target projection, ignored topology and complete-tree
validation, unbound or reset cost/search accounting, responsibility cancellation, free
responsibility work, omitted original or reconstructed final-work preflights and public accounting,
bypassed final physical refusal, deleted cumulative conflict-projection preflight, undercounted or
free shared tree-edge/contact work, skipped contact/projection cancellation, discarded consumed
projection work on refusal, deleted untouched expansion preflight, weakened shared projection
ceiling, removed untouched-enumeration cancellation, dropped its consumed-work accounting,
deleted global tree obstacle reservation, rejected exact-boundary admission, and dropped
successful untouched-work totals or evidence.

This is contract and synthetic capability evidence, not held-out routing-quality evidence. It is
not KiCad DRC, electrical, SI/PI/EMC, thermal, DFM, fabrication, apply, editor or hardware evidence.

B-141 then exercised the production runner's closed differential contract over the exact B-140
population. The two-arm run used Python 3.12.13 on Darwin/arm64, two repetitions, and an
uninstrumented control with `repair_settings: null` beside treatment using the default bounded
repair settings. The report is self-digested and the companion commitment independently binds the
source commit, runner bytes, configuration, artifact bytes, and exact corpus population. It
contains no board, net, candidate, path or geometry payload. Per-reason refusal/outcome
reconciliation is independently checked, and the new envelope-outcome-to-population semantic guard
requires each arm's `outcome_breakdown["envelope_construction"]` to equal the fixed population's
`boards_unable_to_form_a_two_request_envelope` count (**4**). The loader accepts the record after a
merge-ref or later checkout moves `HEAD` while retaining the historical source binding. Focused
validation passes **99/99** tests and the B-141 contract mutation harness kills **28/28** mutants with zero
survivors or control failures; the **35/35** capability mutant result above belongs to #238 and is
not counted again here. The measured population is **20 offered/imported, 16 admitted,
4 envelope-refused and 70 submitted**. Control is **0 boards / 0 nets**, while treatment is
**1 board / 2 nets** with one published repair and one `completed_with_repair`; the differential is
**+1 / +2 / +7,432 physical checks / +43,750,000 nm wire**. The exact evidence pins are: source
`7d32045f34ee7d8f4561f9ff2fea46fdde490934`; runner
`sha256:a25052ff49f925c057e68fc257a6a86a87f702e5a4290c911b151b24aca0d490`; configuration
`sha256:f6d5da89db014b3e396c5cd31ddc7e81e441abe3d0a12980d29c25066b02eccb`; whole-metrics digest
`sha256:f7e38d6744feed63b852e10811f34205bb822a1e2e7ca9759a8cea80a326d4b2`; artifact
`sha256:0ddc4c998fc8e6175ab85f9f3e9a0d960b8a14f658349b3da05ec85f171dd7fa`; report run
`sha256:6788b0a6e786c32d484d4d71b3d25925c099e504eef590f1475f5e3ac7c9091b`; commitment
`sha256:73c4de2747a97cbb0f5da39cf8a2861b540519ee4240df7f51cd5286c1d233c3` with run
`sha256:6bc8aca8f1bb048624bd7e64e7d7d860b8956abf09ef897d28cf5bc3b3dca2ac`; and mutation spec
`sha256:3834610403d2b47f833a577eaf65e3dbf476b70a0952507b55ea9f84f51e5fb0`. Mean arm timings
of 40.262s and 41.136s are descriptive only. The commitment pins exact control and treatment
arm totals plus the full differential, while the whole-metrics digest prevents a self-consistent
re-signing from changing any other metric. This remains contract and completion evidence,
not KiCad DRC, electrical, SI/PI/EMC, thermal, DFM, fabrication, apply, editor, hardware,
general-corpus or human-calibration evidence; #90 remains open.

## Alternatives considered

- **Repair the complete multi-pin candidate as one local route.** Rejected because one path cannot
  represent an N-pad tree and would erase topology.
- **Choose a branch from the congestion ledger.** Rejected because request-local phases make that
  ledger an incomplete physical-intersection oracle.
- **Trust the whole-set net attribution as branch attribution.** Rejected because it names no path;
  exact path-pair reproduction is cheap enough to bound and is already implemented by the physical
  verifier.
- **Validate only the replacement path.** Rejected because an individually legal path can leave the
  complete candidate disconnected or hide mutation of an untouched branch.
- **Treat connectivity as proof of a tree.** Rejected because a replacement can cross an untouched
  same-net branch twice, remain globally connected and still close a copper loop.
- **Publish before the final whole-set pass.** Rejected because local and Board IR validity do not
  prove pairwise clearance against the rest of the negotiated allocation.
