# ADR-0098: A mutation claim is evidence only if the repository can re-run it

- Status: Accepted
- Date: 2026-08-13
- Owners: `@seunghyukchoe`
- Related: [D-188](../ledgers/decision-ledger.md), [R-143](../ledgers/risk-register.md),
  [SEC-138](../ledgers/security-ledger.md), [committed mutation specs](../mutants/README.md),
  `scripts/mutation_harness.py`, `tests/test_mutation_harness.py`

## Context

This project treats mutation testing as standard evidence: ledger rows, ADRs and pull-request
descriptions carry claims of the form "N mutants, 0 survivors", and reviewers weigh them the way
they weigh a benchmark artifact. Two facts surfaced on 2026-08-12/13 that this record must absorb.

**First, the harness those runs used has a defect.** Every prior mutation run was executed by a
scratch harness in an agent's working directory. A mutant that changes a `.py` file **without
changing its byte count**, applied or restored **within the same filesystem second** as the
previous write of that file, is invisible to CPython's import system: the default `.pyc`
invalidation check compares only `(mtime, size)`, both unchanged, so a stale `__pycache__` entry
runs the wrong code. Reproduced in both directions by two independent agents; the observed symptom
in one case was a 189-failure run caused by a *stale* mutant poisoning the *next* mutant's
invocation — an incident D-184 now records first-hand, having hit it during ADR-0094's
mutation run. The contamination direction matters: a stale mutant failing a later run produces a
**false kill**, so "0 survivors" is the optimistic side of the error. A mutant that silently fails
to *apply* misreports in the other direction. Runs that invoke the full ~1,100-second suite per
mutant are safe, because the apply and restore writes land in different mtime seconds; runs using
fast targeted invocations are exposed. `tests/test_mutation_harness.py` reconstructs the defect
deterministically (same byte count, `os.utime` to the same nanosecond) and shows the purge
defeats it.

**Second, and larger: no mutation harness was ever committed.** The harnesses lived only in
agents' scratch directories and were deleted with them. Independently of the `.pyc` defect, **no
mutation claim in this repository is reproducible by anyone** — not by an outside reviewer, and
not by this project. The claims are testimony about tool runs whose tool no longer exists.

### The audit: every mutation claim on `main`, classified from what the record says

The published record (ledgers, ADRs, research notes, CHANGELOG at `9ee073e`) carries **25
distinct mutation-testing claims**: 4 whose "mutants" are committed tests or fixtures that run in
CI, and 21 runs of hand-applied source mutants — roughly 160 mutants in total (a lower bound;
several rows say "each" without a count). Classified strictly from what each record itself
states, using the one-value literals `safe` / `exposed` / `unauditable` / `false`:

**Shown safe — 4 claims.** The mutation control is committed and runs on every CI invocation, so
no apply/restore cycle and no scratch harness ever existed:

- B-088: the floor→ceil control is a committed test pair in `tests/test_simple_route_json_import.py`.
- B-090: five committed discriminator tests in `tests/test_excessive_agency_evaluation.py`.
- ADR-0092 / R-136: the no-tie mutation control is a committed fixture pair in
  `tests/test_net_tie_footprints.py`.
- R-123: the 200-mutation incremental-equals-rebuild sequence is an in-test property over a data
  structure, not a source mutation at all.

**Shown exposed — 2 claims.** The record states a fast targeted invocation, the exact condition
under which the `.pyc` mechanism can corrupt a verdict:

- B-089 (seven mutants, "caught by `tests/test_placement.py -k Courtyard`").
- B-093 / ADR-0080 (six mutants, caught by three named test files).

Exposed is not falsified: corruption additionally requires a byte-count-preserving mutant and
same-second timing, and neither record states byte counts, so these are exposed *and*
unauditable, not shown wrong.

**Shown unauditable — 19 claims.** The record does not establish a reproducible run: ADR-0070
(2 mutants), ADR-0073/B-087 (5, one genuine survivor found and closed), ADR-0078/D-159/R-120
(2), ADR-0081/B-095 (5, one genuine survivor found and closed), B-078 (6, one honest
non-detection recorded), ADR-0087/D-174/R-131 (2), ADR-0091/D-178 (run size unstated; one
genuine survivor found and closed), R-118 (1), R-122 (1), R-127 ("each refusal"), R-132 (3),
D-164 (2), D-180 (8 — these mutate a JSON Schema constraint, not Python, so the `.pyc` mechanism
cannot reach them, but the run is still unreproducible), SEC-119 (1), SEC-133 (26), SEC-134
(25), SEC-136/ADR-0095 (6), SEC-137/ADR-0096/R-141 (23, one provably equivalent), and
D-184/SEC-135 (24, landed with PR #150 while this audit was underway). Two of these — ADR-0070
and SEC-119 — assert a mutant was "caught by exactly one test and by no other", an observation
only a full-suite run can make, which *implies* the safe invocation without stating it; they are
counted as unauditable because an implication a reader must reconstruct is not a record.

D-184/SEC-135 deserve their own sentence, because they are where this ADR comes from. That run
hit the defect live — its first attempt left a stale mutant `.pyc` behind and 189 tests failed
against code no longer on disk — and its recorded figure is from a re-run whose scratch harness
purged `__pycache__` around each invocation and verified each anchor exactly once. The rows are
also the first in this project to state their own limit: "the harness is not committed, so the
record carries the claim and not the evidence." That sentence, generalized, is this ADR. The
purge neutralizes the `.pyc` mechanism for that run *as described*, but the description is of a
tool nobody can re-run, so the claim is still `unauditable` — the classification measures the
record, not the diligence.

**Shown false — 0 claims.** Nothing in this audit shows any prior verdict wrong. Three of the
twenty-one runs reported *genuine survivors* that led to real test additions (B-087, B-095,
ADR-0091), which is evidence the harnesses were at least sometimes measuring reality — and is
exactly the kind of inference this ADR exists to make unnecessary. SEC-134 and SEC-136 already
verified anchors and reported stale mutants as skipped rather than killed; anchor discipline
protects against silent non-application at the *source* level but cannot see a stale `.pyc`, so
even those two runs are unauditable, not safe.

**A live example, not a hypothetical.** While this record was being written, the adversarial
review on open PR #154 reported its branch's state as: full gate not completed by anyone, pytest
interrupted at ~64%, and "Mutants: not_run, none applied, no kill claims made" — with a planned
mutant set written out for whoever finishes. That is the honest form this project's conventions
already require (a non-claim stated as a one-value literal rather than implied), and it is also
exactly the artifact this decision gives a home: under this ADR, that planned set becomes a
committed spec in `docs/mutants/`, and "finished" means the harness ran it to a verdict anyone
can re-run.

## Decision

**1. The harness is repository code.** `scripts/mutation_harness.py` is the only way this project
runs mutation tests from now on. It purges `__pycache__` around every application and every
restoration, runs each test subprocess with `PYTHONDONTWRITEBYTECODE=1`, requires each mutant's
anchor to match **exactly once**, refuses mutants that do not compile, verifies byte-identical
restoration, and proves every kill in both directions — the named tests fail with the mutant
applied and pass on the restored source. A mutant that does not apply is a loud `stale_anchor`
failure of the whole run, never a skip and never a kill.

**2. Mutants are committed, in `docs/mutants/`.** A mutation claim made after this ADR must
commit its spec: the anchors, the replacements, the expectation for each mutant, and the
mutant→killing-test mapping. The strongest pre-ADR records (SEC-134's stale-anchor skips,
SEC-136's anchor verification, ADR-0096's mutant-by-mutant findings) already approached this
form; it is now the floor. What a claim must state to be auditable is written in
[`docs/mutants/README.md`](../mutants/README.md) and
[the development guide](../development.md#mutation-evidence). The first committed spec,
[`2026-08-13-pad-kind-domain.json`](../mutants/2026-08-13-pad-kind-domain.json), re-derives three
of ADR-0096's mutants against today's source and was run to `killed: 3` through the new harness —
the first mutation claim in this repository that anyone can re-run.

**3. Machine-checkable where checking is cheap and meaningful; review-time where it is not.** The
question "should a mutation claim be machine-checkable at all?" splits in two:

- **The claim's *validity conditions* are machine-checked in CI.**
  `tests/test_mutation_harness.py::TestCommittedSpecs` asserts every committed spec still anchors
  exactly once against today's source. Anchor drift — the exact failure SEC-134 caught by hand —
  now fails the build, so a committed claim cannot silently rot into irreproducibility.
- **The claim's *verdicts* are a review-time artifact, re-executed on demand.** CI does not
  re-run the kills. A mutation run is a claim about *chosen* mutants, not about the mutation
  space; its verdicts change only when the anchored code or the killing tests change, and both of
  those changes already trip the anchor gate or the test suite. Re-running every spec on every
  push would multiply gate time for verdicts that cannot have changed while the anchors held, and
  would create pressure to keep specs small — the wrong incentive. The honest statement is that a
  mutation claim is review-time evidence *made reproducible*, the same standard as a benchmark
  artifact: committed inputs, committed tool, dated record, re-runnable by any reader.

**4. Prior claims are qualified, not rewritten.** The 21 hand-applied runs stand as written —
append-only ledgers are corrected by new rows, and dated measurement records are superseded, never
edited. D-188 records the decision, R-143 records the risk with the full classification, and
SEC-138 is the correction row qualifying the security ledger's own mutation evidence (SEC-119,
SEC-133, SEC-134, SEC-136, SEC-137). Any future record citing a pre-ADR-0098 mutation claim
should cite it as `unauditable` (or `exposed` for B-089 and B-093) rather than as a settled kill
count.

## Consequences

- A mutation claim now has the same shape as every other evidence class in this project: a
  committed input, a committed tool, a dated record, and a loud failure mode. The phrase
  "N mutants, 0 survivors" with no spec is no longer admissible in a ledger row or ADR.
- `docs/mutants/` grows one spec per claim. Anchors will go stale as source evolves; the CI gate
  makes that a build failure whose fix is re-anchoring **and re-running the spec**, which is the
  discipline SEC-134 applied manually, now enforced.
- The per-mutant cost of a claim rises (two targeted invocations per mutant, both directions,
  caches purged). That cost is the point: it is the difference between a verdict and a sentence.
- Nothing here re-runs the ~160 historical mutants. Re-deriving them all would be a large,
  low-yield project — the code they anchored has drifted, and the claims they support are already
  qualified. The worked example deliberately re-derives the three most load-bearing ADR-0096
  mutants instead, and future slices should re-derive a prior run's mutants only when they touch
  the code it anchored.
- The `.pyc` mechanism is now documented and regression-tested in-repo, so a future scratch
  harness (which this ADR prohibits, but cannot physically prevent) at least has no excuse.

## Alternatives considered

- **Adopt `mutmut` or `cosmic-ray` instead of a bespoke harness.** Rejected for now: both tools
  generate mutants rather than replaying *chosen* ones, and this project's mutation claims are
  precisely about hand-chosen, argument-bearing mutants tied to named tests (the ADR-0096 model).
  A generated-mutation-score claim is a different evidence class; if the project ever wants one,
  it should arrive as its own decision with its own ledger row, not as a side effect here.
- **Re-run all historical mutants through the new harness.** Rejected as this slice's scope —
  most anchors are stale against drifted source, and the honest classification (`unauditable`)
  already says everything the record can support. The worked example proves the path exists.
- **Run every committed spec's kills in CI.** Rejected; §Decision 3 records the reasoning — the
  anchor gate catches the only condition under which a verdict could silently change, and
  re-executing unchanged verdicts buys gate minutes with no information.
- **Qualify prior claims by editing the rows that made them.** Prohibited by the append-only
  rule, and rightly: a reader who cited SEC-134 yesterday must be able to resolve that citation
  today. New rows (D-188, R-143, SEC-138) carry the qualification.
- **Treat anchor verification as sufficient and skip the cache purge.** Rejected on the
  mechanism: an anchor check reads the *source*, and the defect is that the interpreter does not.
  SEC-134 had anchor discipline and is still unauditable.
