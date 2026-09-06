# Balanced readiness: approved execution program

This program continues the [verified local checkpoint](balanced-readiness-checkpoint-2026-09-06.md)
and the [original v0.13 acceptance](v0.13-supervised-optimization.md). It does not replace either
with a foundation-only release. The first reviewed integration is published as
[draft PR #268](https://github.com/seunghyukchoe/copper-mcp/pull/268), initially at `1cf2fea`.
Publication was explicitly authorized; merging and releasing were not.

The initial [hosted run](https://github.com/seunghyukchoe/copper-mcp/actions/runs/34009062108)
passed all CI, security and CodeQL gates. Its CI critical path was 32m12s, so the under-twenty-minute
target remains unmet. The canonical coverage step reported 4,496 passed and ninety environment/
fixture skips in 1,889.45 seconds. PR observations do not replace successful main calibration;
the four reshaped job budgets remain provisional and release-blocking.

## Locked decisions

- Preserve the fifty-requirement catalog and 40/30/20/10 weighting. Every area must reach at least
  ninety points and pass every critical requirement. Routing and Engineering Judgement each
  require all ten critical requirements, effectively one hundred points under this catalog.
- Cover analog/audio, MCU/sensor and low-power non-isolated supply profiles over genuine 2/4/6/8
  layers. Exclude RF design, DDR/PCIe, mains and safety-critical certification.
- Use traceable independent measurements first for physical calibration. Missing or unsuitable
  references require explicit bench/lab work, never simulation-only calibration credit.
- The new engineering-aware live apply operation must block every failed, inconclusive or not-run
  profile-required check. There is no human override for required unknowns.
- Preserve existing optimization/v1 identities and review-only completion. Introduce explicit v2
  requests/packages, distinct parity evidence and application receipts; never silently convert v1.
- Keep Python 3.11–3.13, canonical 3.12 coverage and at most four test workers on the recorded host.
  No local timing substitutes for successful hosted calibration; no provisional timeout is lowered.

## Ordered increments

1. **Auditable baseline.** Publish the reviewed draft, repair hosted failures, then obtain separately
   authorized baseline integration. Add authenticated evidence intake without changing preliminary
   submissions into audited scores. Freeze corpus splits, metrics and development budget profiles.
2. **Real inputs and native prototype.** Capture immutable electrical project files and dependencies;
   verify BOM/model bindings, hierarchy and candidate parity before project-context ERC. Develop a
   separate pinned KiCad test build and native transaction prototype without replacing the installed
   application or advertising stock IPC as atomic.
3. **Ordinary-board workflows.** Complete native multi-pin trees, candidate-bound fill, production
   DSN/SES and SRJ normalization/disposal, and globally budgeted repair. Add staged placement search
   and validated clearance measurements. Compare identity and optimized placement at equal total
   routing budgets; a digest tie-break does not count as improvement.
4. **Calibrated engineering.** Run project DRC/ERC/parity, fabrication-output DFM, reviewed-model
   ngspice SI/PI, Elmer thermal and bounded openEMS pre-compliance cases. Require convergence,
   matched validity ranges and independently approved uncertainty/tolerances. Keep circuit function
   and ratings distinct from ERC; overlapping uncertainty remains inconclusive.
5. **Guarded live batch.** Integrate bounded Orca scheduling/ranking and deterministic fallback/replay.
   Add apply_optimization_package plus read-only get_optimization_application. The KiCad-side guard
   owns document/session identity, atomic preconditions, edit exclusion, staging, commit, verification
   and guarded rollback. One exact human confirmation creates one undo step without saving. Lost
   acknowledgements reconcile the original operation; uncertainty stops further writes.
6. **Equalize readiness.** Evaluate the frozen held-out cohort and real fault cases, review artifacts,
   then direct subsequent work to the lowest audited area. No unsupported domain earns capability
   credit, and no critical failure can be offset by another requirement.

## Implementation checkpoint: artifact capture and census isolation

The private electrical artifact reader is implemented and independently reviewed: 48 focused
tests cover two complete reads, digest matching, remaining-byte limits, portable aliases,
Unicode controls, immutable per-call limits and no execution or authority. Native QA sources
were prepared and inspected; [native prototype findings](../integrations/native-transaction-prototype.md)
identify real build/test seams but do not claim that a guard was implemented or installed.

The B-140 fixture now recomputes in an isolated source-bound child while retaining every
compatibility assertion. Independent review accepted its strict envelope/provenance checks.
The combined source/input fingerprint is
`sha256:989bd1cbe8baa3741c1d915fb67cf652f32613e53d52affbaeb375b0952cff8d`.
On Python 3.12.13, four-worker `make check` passed 4,649 tests, two skips and 779 subtests in
416.35 seconds of pytest / 430.59 seconds overall, with 89% code coverage. The B-140 setup was
95.56 seconds in that run, versus 379.16 in the prior full run; B-141 replay remained included.
Strict mypy passed 137 source modules; lint/metadata, secret/audit and wheel/sdist build passed.
The capture/input/readiness group also passed 84 tests on each of Python 3.11 and 3.13.
These are local observations, not hosted target acceptance or complete electrical project capture.

## Matched feedback measurement and next CI increment

On clean commit `94dafe9b2b9552068eb42b4aa9df9c485d6dcae8`, the same macOS 26.6.2 arm64
host and Python 3.12.13 completed the serial `make test` in **1,475.20 seconds** (4,649 passed,
two skipped, 779 subtests; 89% coverage), then `make test-fast` in **152.89 seconds**
(4,646 passed, two skipped, 779 subtests). The source/input fingerprint above was unchanged.
Both used `HYPOTHESIS_PROFILE=deterministic-ci`, `PYTEST_ADDOPTS='-q --durations=10'`,
default-off apply/live flags and the same configured local router images. No other local heavy
validation ran concurrently. The fast target uses four workers, no coverage and its declared
slow-evidence/external-router/networked-provider exclusions; the serial target retains them.
The measured end-to-end ratio is **9.65x**, satisfying the local 3x feedback target for this host
and this pair of approved target semantics. This is one sequential pair, not a repeated statistical
estimate or equal-workload parallel scaling measurement.

The [second hosted run](https://github.com/seunghyukchoe/copper-mcp/actions/runs/34012739235)
passed on that commit, with **20m10s** from creation to final package completion. Canonical pytest
reported 4,558 passed and ninety skips in 1,176.35 seconds. This improves the first 32m12s run but
still misses the under-twenty-minute target by ten seconds. Neither PR run calibrates main.

Its per-test timings show three separate identical placement benchmark calls costing 111.43,
113.00 and 109.58 seconds. The next test-only increment shares one fresh three-replay measurement
across those read-only assertion groups, decoding an independent nested result for each consumer.
All original assertions remain. Separate negative-criterion, interruption and two-report
determinism executions remain fresh; no persistent report cache, reduced search budget, marker
exclusion or production behavior change is introduced. Hosted improvement must be measured anew;
the sum of saved test durations is not a prediction of critical-path savings.

The test-only increment passed all thirty placement tests on Python 3.11, 3.12 and 3.13,
plus `make lint` and the changed-file formatting check. Independent read-only review found no
lost assertions or fixture/monkeypatch isolation issues. The full-suite results above belong to
the preceding clean baseline, not a claimed full rerun of this test-only increment.

## Acceptance and human gates

Use at least twelve open-licensed held-out boards, all three electrical profiles, at least three
independent project lineages and all four layer counts. Keep tuning data separate and failed runs
in the denominator; do not replace boards or manufacture layer variants after observing outcomes.
Require ninety-percent eligible-net completion, zero hard accepted DRC errors, no post-apply
regression, and strict placement-quality improvement on at least three held-out boards.

Engineering references must include known-good, known-bad, incomplete and out-of-profile cases,
with zero observed safety-critical false passes. Live tests must inject concurrent edits, undo,
partial staging, cancellation, crashes, lost acknowledgements, duplicates and rollback failures.
Exact-source replay, make check, all interpreters, real engines, artifact review and ledgers remain
mandatory. The PR path must be measured below twenty minutes, and local fast feedback at least
three times faster than a matched serial baseline.

The four-to-six-week window is staged delivery, not a promise to finish every area at ninety.
Merge, release, modified-application installation, paid laboratory work, hardware purchases and
genuine human/specialist validation remain explicit gates. AI clicking approval is not evidence
of genuine human consent. No board saving or general engineering certification is introduced.
