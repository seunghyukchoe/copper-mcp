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
  contract. Its semantic guards additionally forbid a disabled control (`repair_settings: null`)
  from claiming `completed_with_repair`, `repair_published`, or any non-zero repair work, and
  require all six status categories (`completed`, `no_path`, `partial`, `invalid_request`,
  `cancelled`, `not_run`) to reconcile to the outcome taxonomy. A later checkout or merge-ref
  movement may load the artifact without replacing its historically recorded source commit. Issue
  #90 therefore remains open for human review and calibration of the benchmark interpretation and
  for any further held-out quality decision.

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
reconciliation is independently checked. The semantic guards require each arm's
`outcome_breakdown["envelope_construction"]` to equal the fixed population's
`boards_unable_to_form_a_two_request_envelope` count (**4**); forbid the disabled control from
claiming `completed_with_repair`, `repair_published`, or any non-zero repair-work field; and
require all six status categories (`completed`, `no_path`, `partial`, `invalid_request`,
`cancelled`, `not_run`) to reconcile to the outcome taxonomy. The loader accepts the record after a
merge-ref or later checkout moves `HEAD` while retaining the historical source binding. The source
guard verifies the actual Git runner blob SHA at the declared source commit and accepts later
historical `HEAD` movement without replacing that binding. The closed `total_ripups` bound is
`70 * (8 - 1) = 490`; 490 is accepted and 491 is mutation-killed. The closed aggregate wire bound is
`70 * 62,500,000,000 = 4,375,000,000,000 nm`; the exact bound is accepted and the `+1` boundary
is mutation-killed. Focused validation passes **187/187** tests and the B-141 contract mutation
harness kills **78/78** mutants with
zero survivors or control failures; the **35/35** capability mutant result above belongs to #238 and
is not counted again here. The measured population is **20 offered/imported, 16 admitted,
4 envelope-refused and 70 submitted**. Control is **0 boards / 0 nets**, while treatment is
**1 board / 2 nets** with one published repair and one `completed_with_repair`; the differential is
**+1 / +2 / +7,432 physical checks / +43,750,000 nm wire**. The exact evidence pins are: source
`b7c71d4d643df155c7bdcee5bac25e7d943b7031`; runner
`sha256:5f5e8b8685bf178ef7064ce2690afb789678c2af9c727d40b18847e0738e23a1`; configuration
`sha256:17966b8f508143cf3f54f797ea9a02d6fd66cbfe0621e830950f050f0f1868a3`; whole-metrics digest
`sha256:f7e38d6744feed63b852e10811f34205bb822a1e2e7ca9759a8cea80a326d4b2`; report raw
`sha256:ff2bcd77814e3818a896eb2813b66def45997487301ec8954cd7614d7affc81c` with run
`sha256:bb73a925b00506e4c5305bd2fe0136f4d501f7351d1b78d8b8552b010cf06fe3`; commitment raw
`sha256:129be265f95519db1bb7a5856ad1323d0b57ed0fc180a9bbe6161957b83696d9` with run
`sha256:3633c0b6a1fa362d30572311968e56539cec455e39f1ddf687547592da79e397`; and mutation spec
`sha256:e1f4a225f963385cba00af45109d0d8ae0a22ef228787c2b7e87707cf8108c85`. Mean arm timings
of 40.574s and 41.039s are descriptive only. The commitment pins exact control and treatment
arm totals plus the full differential, while the whole-metrics digest prevents a self-consistent
re-signing from changing any other metric. This remains contract and completion evidence,
not KiCad DRC, electrical, SI/PI/EMC, thermal, DFM, fabrication, apply, editor, hardware,
general-corpus or human-calibration evidence; #90 remains open.

Closure for caller-selected B-141 report and sidecar reads is bounded: every parent directory is
opened fd-relatively without following links, and the final component is opened nonblocking and
must be an exact regular file. A 64 KiB max+1 probe precedes decode/JSON; FIFO and other special
files refuse with fixed, non-echoing diagnostics, and recursion is mapped to a fail-closed refusal.
This adds no quality, physics or generalisation claim.
The corpus closure additionally uses an fd-relative no-follow walk, a 36-entry/20-board closed
manifest, bounded manifest/license and sample reads, raw-manifest and sample size/digest checks,
special-file and traversal refusal, declared-size preflight, and per-board and aggregate iteration
floors.

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

## Amendment, 2026-09-02 — an artifact's recorded revision must be one the default branch carries

B-141's evidence binds `source_commit` and verifies the runner blob at that revision. A pull
request publishes from a branch commit, and squash-merging discards it: #239's artifact recorded
`b7c71d4d643df155c7bdcee5bac25e7d943b7031`, the squash landed as
`86634180e5a3f0956cf2ede4168710f1fce8fbcb`, and every fresh clone of the default branch then
refused the artifact because the recorded revision named nothing. The refusal was correct. The
recorded value was truthful on the pull-request branch but was not durable or verifiable after the
squash.

The artifact is rebound to the squash commit, which the default branch carries and every clone
fetches. A clean replay on that source reproduced the semantic metrics digest exactly while
freshly observing descriptive mean timings of **39.224s** control and **40.042s** treatment. The
semantic result is unchanged; the published artifact is nevertheless a new measurement record,
not a byte-only re-signing. Two alternatives were weighed and rejected on evidence rather than
taste:

The displaced bytes remain directly auditable as
[`2026-08-30-negotiated-multipin-branch-repair-v1-b7c71d4d.json`](../../benchmarks/results/routing/archive/2026-08-30-negotiated-multipin-branch-repair-v1-b7c71d4d.json)
and its
[`archived commitment`](../../benchmarks/results/routing/archive/2026-08-30-negotiated-multipin-branch-repair-v1-b7c71d4d.commitment.json).
Their raw SHA-256 values remain `ff2bcd77814e3818a896eb2813b66def45997487301ec8954cd7614d7affc81c`
and `129be265f95519db1bb7a5856ad1323d0b57ed0fc180a9bbe6161957b83696d9`.
They retain their original internal canonical path for byte identity and therefore validate only
as historical offline material, never as the current authoritative pair. The corrected canonical
record passes **190/190** focused tests and the unchanged 78-mutant set with zero survivors or
control failures.

- **Bind the tree instead of the commit.** Refuted by measurement: the branch tree
  (`792bd419e043955f0f44978158008aef34495645`) and the squash tree
  (`b2e1edac893a8cdbda5e6a944f52871bfd34a00e`) differ, because the default branch advanced between
  publication and merge. A squash preserves file *content*, not the tree. The blob binding that
  does survive is `runner_sha256`, and it was already bound.
- **Accept content-only provenance as `repository_bound`.** Rejected because the artifact claims it
  was produced at a *named revision*. When no clone can check that claim, certifying it green is
  precisely the failure this contract exists to refuse. `offline` remains the honest answer, and
  `load_artifact` stays fail-closed.

**Under linear history a commit SHA is not a durable identifier for anything a pull request
produces.** `main` sets `required_linear_history=true`: branch protection refuses merge commits, and
both remaining strategies rewrite history — squash collapses the branch to one new commit, rebase
replays every commit under new SHAs. No strategy available to this repository preserves a branch
commit's SHA. An artifact published on a branch therefore cannot bind its own revision and survive
its own merge. The only commit that can be bound durably is one that is *already* on the default
branch when the artifact is written, which is why this rebinding names the squash commit of the pull
request that introduced the artifact rather than any commit of the branch performing the repair.

That makes the present fix a valid unblock but not a general answer: it must be repeated on every
future runner change. The structural answer is to bind provenance to what survives every strategy —
**the runner blob**, whose hash is already bound as `runner_sha256`, verifiable with no history at
all — and to stop requiring a resolvable commit for the strongest guarantee. Two facts constrain that
design and are recorded here so the follow-up does not rediscover them:

- **The tree does not survive, only the blob does.** Measured on this very merge: branch tree
  `792bd419e043955f0f44978158008aef34495645`, squash tree
  `b2e1edac893a8cdbda5e6a944f52871bfd34a00e`. They differ because the default branch advanced
  between publication and merge, so a squash rewrites the root tree even when it preserves every
  byte of the file in question. Binding the tree would fail exactly as binding the commit did.
- **A commit-derived date cannot be fixed at publication time.** Deriving the evidence date from
  `git log --first-parent main` for the first commit carrying the runner blob is durable *after* the
  merge, but at publication the blob is not yet on the default branch, so the value would change at
  merge and break the artifact's self-digest. The follow-up must either drop the date from the
  verified set — recording it as informational under `offline` — or accept a post-merge derivation.

The follow-up is a separate change because it is mutually exclusive with this one: implementing it
edits the runner, which moves `runner_sha256`, which is precisely what would stop
`86634180e5a3f0956cf2ede4168710f1fce8fbcb` from carrying the bound bytes. One pull request cannot
both bind that commit and change the runner.

Until then the residue is a process constraint mechanized rather than documented as a habit:
`test_published_artifact_records_a_default_branch_ancestor` checks the recorded revision against
the exact pull-request base injected by hosted CI and refuses before merge when it binds only a
feature-branch commit. Outside that explicitly configured environment the repository-only
assertion skips rather than trusting a possibly stale local `main` or inheriting an `origin` naming
requirement. A future runner change must therefore land before a later evidence-only publication
can bind its default-branch commit.

## Amendment, 2026-09-02 (second) — verified provenance is content, not revision

This is the structural successor the amendment above asks for, and it supersedes that amendment's
*binding* while leaving its record intact. The two relate as follows, and neither edits the other:
the first amendment (and `D-241`) rebound B-141 to squash commit
`86634180e5a3f0956cf2ede4168710f1fce8fbcb` as a one-time unblock, correctly, under a contract that
required a recorded revision to resolve. This amendment (`D-245`) removes that requirement, so the
field the first amendment repaired is no longer load-bearing: `source_commit` is informational at
every guarantee level and no validation path resolves it. The rebinding was not wasted — it kept
`main` green while this change was built — but it was a repair of one instance of a class, and the
class recurs on its own terms.

**The recurrence is measurable, not predicted.** A `--depth=1` clone of `main` at
`f8b4daa7b8d8f4567d445574754a02aa2a609518` runs `main`'s own code against `main`'s own artifact and
refuses:

```
recorded source_commit : 86634180e5a3f0956cf2ede4168710f1fce8fbcb
resolves here          : None
load_artifact          : REFUSED -- the B-141 recorded source commit is not present in this
                         repository, so its provenance could not be consulted
```

The rebinding is therefore durable only while a consumer's clone is deep enough to reach the squash
commit, and it must be repeated on every runner change. Depth, fetch policy and fork boundaries are
properties of the *consumer's* checkout, and an evidence contract that varies with them is not an
evidence contract.

### The verified provenance set

`_bound_input_paths()` is the single source of truth, and it is now the only producer of published
file digests: `_configuration()` derives all six by digesting exactly those paths, so a digest
cannot be published without also being verified. `_assert_bound_inputs_cover_published_digests()`
closes the other direction against the closed configuration key set, so a future field that binds a
new input cannot be published unverified.

| Field | Bound to | Guarantee level | Mechanism |
|---|---|---|---|
| `configuration.runner_sha256` | this runner | `repository_bound` | re-digest the live file |
| `configuration.b140_runner_sha256` | the B-140 runner | `repository_bound` | re-digest the live file |
| `configuration.b140_artifact_sha256` | the B-140 artifact | `repository_bound` | re-digest the live file |
| `configuration.reference_runner_sha256` | the B-088 runner | `repository_bound` | re-digest the live file |
| `configuration.reference_adapter_sha256` | the B-088 adapter | `repository_bound` | re-digest the live file |
| `configuration.reference_artifact_sha256` | the B-088 artifact | `repository_bound` | re-digest the live file |
| `population_binding.corpus_manifest_sha256` | the corpus manifest bytes | `repository_bound` | recomputed while loading the exact corpus |
| `configuration.configuration_sha256` | the configuration object | `offline` | nested self-digest |
| `run_id` | the whole document body | `offline` | self-digest |
| every metric, arm pin and differential | the companion commitment | `companion_bound` | exact measurement and arm pins |
| `date_utc` | nothing in the repository | informational; pinned *as a document field* at `companion_bound` by `artifact_run_id` | opt-in `verify_evidence_date_against_history` relates it to history |
| `source_commit` | nothing | informational at every level; never resolved | published in `not_claimed` |

`GUARANTEE_LEVELS` is deliberately unchanged. Content binding either holds or refuses, so no new
distinction exists that the four existing levels cannot express, and `load_artifact` still reaches
`companion_bound` and still checks the level it reached.

**The one honest movement, disclosed rather than buried.** A re-signed `date_utc` or `source_commit`
used to be refused at `repository_bound`, by Git. It is now refused at `companion_bound`, by
`artifact_run_id`, which digests the whole body. No guarantee is lost at `load_artifact`, which
always reaches `companion_bound`; what is lost is that a caller stopping at `repository_bound`
accepts a document naming any revision it likes. That cost is accepted because the check it replaces
could not survive a squash merge — it refused valid artifacts on `main` while remaining bypassable
by anyone able to re-sign both files anyway — and because the same re-signing still cannot change a
single bound byte.

### The evidence date is a tri-state report, not a verdict

`verify_evidence_date_against_history(document, *, ref="HEAD")` returns one of `agrees`,
`disagrees`, `undeterminable`, and **returns rather than raises on all three**. It derives the
default branch's answer from `git log --first-parent` over the runner path, bounded to 200 commits,
and takes the *oldest* first-parent commit whose runner blob equals the bound `runner_sha256` — the
commit that introduced the content, not a later one that reinstated it.

`disagrees` is the ordinary outcome whenever review takes more than a day, and `undeterminable`
covers a branch not yet merged, a history too shallow, and no Git at all. Reporting either as a
verdict would repeat exactly the absent-versus-disagreeing conflation that turned `main` red in
#250. Nothing in `validate_report` or `load_artifact` calls it, and it is never written into the
artifact, so it is not part of the self-digest — it cannot be, which is the reason it lives outside
the contract rather than inside it.

### `source_commit` is demoted, and the report says so

It is not resolved by any validation path and its absence is not an error. It is still *derived* at
publication, because the evidence date is read from it and a hand-written date can name a day the
run did not happen on. Publication happens inside the repository that owns the revision and may
demand it; validation happens anywhere and may not. The demotion is published in `not_claimed` as a
seventh entry, so a consumer reading the JSON alone learns it without reading this ADR.

### What this removes

`test_published_artifact_records_a_default_branch_ancestor`, the `COPPER_MCP_DEFAULT_BRANCH_REF`
injection in the CI workflow, and the workflow test pinning it are all removed. They mechanize the
rule this amendment retires: under the new design every republication necessarily records a branch
commit the pull-request base does not carry, so the guard would refuse this very change. The
archived `b7c71d4d` bytes are untouched and remain byte-identical; because `not_claimed` gained an
entry and claim lists are validated exactly, the archived document no longer validates against the
current contract, which is the correct reading of an append-only archive.

### Evidence

The republication is a re-binding, not a re-measurement: the whole-metrics digest reproduced
`sha256:f7e38d6744feed63b852e10811f34205bb822a1e2e7ca9759a8cea80a326d4b2` exactly, so every
published count, breakdown and differential is unchanged. Descriptive mean wall times were
**47.348s** control and **51.310s** treatment on a contended machine and carry no claim. The new
pins are source `0202329da16ecae0fbb61e7ed7a0215cfa599585` (informational), runner
`sha256:33bd81c80bd6c2ad8f970c5477fd236b4c05759c99b50145a66152451672f3bb`, configuration
`sha256:60ad50ee812875411fd88413182954c56f677ebe0e3300cc5295902d92e8400d`, report run
`sha256:42cd4f172b227e9a26a945f779ad548718eaed3c9f42debe016198b15e123beb`, report raw
`sha256:7e05d1df34b39c726e944b39e6671dc67d920f53a3732ae6898c5653a3a32e69`, commitment run
`sha256:d431b607b89ca81582b2da6686a739b9196097a3e752e12fdaf11cba93059757`, and commitment raw
`sha256:bcf33431581494650c1bbc4c17cc4eb340af327ba564e6c5aa9cd5e385c8ba21`.

The load-bearing proof is the simulated squash: a `--depth=50` clone of `main` carrying this
branch's file content but none of its commits — which is exactly what a squash merge produces —
resolves the recorded revision to `None`, re-digests all six bound inputs as matching, and
**accepts at `companion_bound`**, while reporting the evidence date as `undeterminable` rather than
as a disagreement. The same clone with the branch fetched accepts at `companion_bound` with the
date `agrees`. The control above is the same condition on `main` today, which refuses.

Focused evidence validation is **200/200 tests**, and the committed mutation spec
`sha256:22cbf4a4787b1a67afc9add8ca1c21914a5bec2dfef2551101d1933f1573bf51` kills **79/79** mutants
with zero survivors, zero stale anchors, zero control failures, zero invalid runs and zero
`not_run` — distinct from #238's **35/35** capability mutants, which are not added to this count.
The set moved from 78 to 79: seven anchors went stale against the edited source, five mutants whose
code this amendment deletes were retired rather than silently dropped (they are named in `D-245`),
two were re-anchored onto successor code whose names remain exactly true, and six new mutants pin
the new guards. One survived the first run, and it was a real coverage gap rather than a mapping
typo: nothing exercised the whole-configuration comparison, so `seed`, the router version, the
envelope budgets and every declared ceiling — configuration facts no file digest can witness —
were unpinned against a re-signed document. A new test closes it.
