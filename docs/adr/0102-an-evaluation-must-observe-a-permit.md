# ADR-0102: A refusal evaluation must observe a permit, and prove it kept observing one

- Status: Accepted
- Date: 2026-08-13
- Owners: `@seunghyukchoe`
- Related: [D-193](../ledgers/decision-ledger.md), [R-148](../ledgers/risk-register.md),
  [SEC-142](../ledgers/security-ledger.md), [B-106](../ledgers/benchmark-ledger.md),
  [ADR-0098](0098-reproducible-mutation-evidence.md),
  [issue #110](https://github.com/seunghyukchoe/copper-mcp/issues/110),
  `scripts/evaluate_excessive_agency.py`,
  [excessive-agency evaluation v1](../research/excessive-agency-eval-v1.md)

## Context

The excessive-agency suite ([SEC-122](../ledgers/security-ledger.md), B-089) replays predeclared
attacks and requires each to reach a predeclared refusal. Twenty-seven of its twenty-nine scenarios
required a refusal or an honest non-claim. That is a real result and it has a hole in it: **an
evaluation whose every row requires a refusal cannot distinguish a server that refuses correctly
from one that refuses everything, including refusing when it should not.** The suite would have
reported the same green artifact for a server whose apply surface was permanently broken.

The authorized path was not absent. `_scenario_apply_replayed_token` granted consent, minted a
genuine single-use capability through a real preview, applied it, and required real bytes to
change before replaying the spent token. So the permit existed — but only as an unnamed
*precondition* of a refusal scenario, with three consequences:

1. **It was not predeclared.** The catalog declared twenty-nine required outcomes and every one of
   them was a refusal or a non-claim. No row in the artifact said the server had permitted
   anything, so no reader could check that it had.
2. **It could stop happening in silence.** When neither capability probe finds a candidate the
   scenario records `not_run`, which is correct and honest per-row and catastrophic in aggregate:
   with both probes returning nothing the suite still reports **zero failures**. Measured before
   this change, that degraded run recorded 67 passes instead of 77 and an empty `failures` list.
   The only thing that would have caught it was a whole-report count equality pinned in a test —
   a guard that fires for the right reason by accident, cannot say *what* stopped running, and is
   legitimately edited whenever a scenario or family is added. This is the same defect class the
   project has now recorded three times: six of seven property tokens un-counted because every
   test used the same one; a mutation run reporting 11/11 kills while executing zero tests
   (ADR-0098). A check that passes because it never ran is the failure, not the exception.
3. **"Permits what it should" was never paired with "and nothing else".** The apply was guarded by
   a single-board digest, which says nothing about the file beside it or a file the write created.

Issue #110 also asks a question this record must answer plainly, because an evaluation that can
perform a real mutation is a dangerous artifact: if the suite can authorize an apply, what stops a
CI run from writing to something real? The existing containment — copy committed boards into a
temporary directory and point the workspace at it — was sound and **untested**. Nothing in the
suite ever asked the server to leave that directory.

## Decision

**The authorized path becomes a first-class predeclared outcome, and its continued exercise is
asserted by report-level controls that fail the run.**

1. **A new `authorized_apply` scenario family**, with `required_outcome.kind: authorized_write` in
   the digest-bound catalog. `authorized-apply-permits-the-bound-candidate` presents the one
   request the server is supposed to say yes to — operator consent, a single-use capability this
   process minted, the candidate it was minted for, the revision the board is actually at — and
   fails on anything short of a real write, recording the refusal *code* when refused.
   `authorized-apply-changes-only-the-authorized-board` digests every regular file in the
   workspace across that write.
2. **A new `workspace_containment` scenario family** attempts three escapes — an absolute path, a
   `..` traversal, and a symlink whose name passes every syntactic check — each **with consent
   granted and a genuine token**, because a containment check run with consent off is satisfied by
   the consent gate and proves nothing about containment. Each escape aims at a board that really
   exists outside the workspace, because a resolver refusing a missing path establishes nothing
   about one that is merely out of bounds. Each requires `invalid_request` **and** the outside
   board's bytes to be unchanged: a refusal that arrives after a write fails.
3. **Report-level controls**, predeclared in the catalog beside the scenarios and counted in the
   same exit status, assert that the suite ran what it claims to run: the authorized apply was
   exercised somewhere; it was exercised outside the control family; and every declared escape
   route was actually attempted. A control failure is not a weaker result than a scenario failure,
   because a scenario that did not run cannot fail.

**Containment is what the harness copies, not what it promises.** Each family now gets a temporary
enclosure holding two sibling directories: the workspace, and a `beyond/` directory with one real
board in it. Both are temporary, neither is inside the repository, and the worst a broken
confinement guard can do is overwrite a copy the harness made seconds earlier. The suite's only
authorized write lands in the workspace copy; `test_source_boards_are_untouched_by_a_run` continues
to digest the committed boards across a full run.

## Consequences

- The suite is **136 cases: 90 passed, 0 failed, 46 not run**, plus three controls, all holding.
  B-106 records the run; B-089's numbers stand as the earlier measurement.
- **The evaluation can now fail in the way it needs to.** With both probes returning nothing,
  `failures` is still empty and `controls_failed` is 2, and the CLI exits non-zero. That state is
  pinned by a regression test, and two mutants that neuter the controls are killed by it.
- The whole-workspace digest **found something on its first run**: a successful apply creates a
  pre-apply rollback copy under `.copper-mcp-backups/`, which the previous single-board guard could
  not see. The scenario now requires that copy to belong to the board that changed and to carry
  that board's pre-apply bytes — a copy holding post-apply bytes restores nothing, and a copy of a
  different board would put one board's contents into another's history.
- The backup directory name is written into the evaluation as a literal rather than imported from
  the service. If board bytes start landing somewhere else, this evaluation is meant to notice, not
  to follow.
- Cost: the authorized rows depend on a family affording a real candidate. `coppertone-buffer`
  affords none — every net is already routed — so it records `no_apply_capability_available` for
  all three authorized rows. That is a recorded non-claim, and the second control exists precisely
  so the permit is never demonstrated *only* on the fixtures the boundary was built against.

## Alternatives considered

- **Assert the permit inside the replay scenario, as before.** Rejected: it leaves the permit
  unnamed in the artifact and undeclared in the catalog, so a reader cannot check the suite
  observed one, and the degradation above stays invisible.
- **Pin the whole-report counts in a test and call that the control.** That is what exists today
  and it is why this record is needed. It catches the degradation for the wrong reason, names
  nothing, and is edited by any legitimate change to the scenario set — so the next such change can
  absorb the regression without anyone seeing it.
- **Refuse to build an authorizing evaluation at all**, on the grounds that a suite able to mutate
  a board is dangerous. Seriously considered, and rejected on the facts: the suite already
  performed a real authorized apply, so the danger was present and merely unnamed. The honest
  options were to remove the write or to test its confinement, and removing it would have left the
  central claim half-measured. Confinement is now three predeclared escape attempts against a real
  file outside the workspace rather than an assumption.
- **Test containment against a path that does not exist.** Rejected: `resolve(strict=True)` refuses
  a missing path first, so the row would record a containment result it never established — a
  vacuous proof of exactly the kind SEC-122's own findings section already catalogues.
- **Make the containment attempts with consent off.** Rejected for the same reason: the consent
  gate would answer first and the row would pass without confinement ever being consulted.
