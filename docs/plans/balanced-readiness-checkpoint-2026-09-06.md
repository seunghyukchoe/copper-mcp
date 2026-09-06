# Balanced-readiness integration checkpoint — 2026-09-06

Latest result: the reviewed local increment is verified on all three supported interpreters.
See **Verified local closure** below for exact scope and results. The five-area ninety-percent
target, hosted calibration and release acceptance remain open.

This continues, without rewriting, the [September 5 checkpoint](v0.13-integration-handoff-2026-09-05.md)
and the [original staged plan](v0.13-supervised-optimization.md). The five-area program remains
incomplete; no audited readiness percentage or release authorization is established.

## Exact working state

Use `/Users/seunghyukchoe/Desktop/12_PCB-worktrees/v013-integration`, branch
`codex/v013-integration`, base commit `1ed83e42d957fbf91772ae627e2d6d003c250ca2`.
Implementation is local and staged in increments, not committed or published. The original
`/Users/seunghyukchoe/Desktop/12_PCB` dirty checkout remains untouched. Version remains 0.12.0.
No user board was applied or saved; no main merge, push, release or installed KiCad replacement
was performed.

The canonical runtime is `.venv/bin/python` (3.12.13). Compatibility environments are
`.venv/compat-311/bin/python` (3.11.15) and `.venv/compat-313/bin/python` (3.13.15), both installed
against this worktree. They are ignored development environments, not package contents.

## Completed verification and corrections

- A serial, coverage-enabled `make check PYTHON=.venv/bin/python` completed successfully:
  **4,530 passed, four skipped, 1,755.75 seconds in pytest, 89% coverage**. Strict mypy checked
  136 source modules; lint/metadata, secret scan, dependency audit and wheel/sdist build passed.
  This result predates the import-environment and candidate-parity changes below.
- The fresh B-141 source-bound recomputation passed inside that full run (165.05 seconds).
  Its import loader compiles the digest-checked source bytes directly rather than accepting
  timestamp-valid cached bytecode. Thirteen targeted tests and independent scoped review passed.
- The two real router smokes unexpectedly skipped in the full run despite explicit configuration.
  Three imported benchmark modules had permanently deleted every `COPPER_MCP_*` environment
  setting during test collection. A subprocess regression reproduced the loss. Imports now use
  temporary safe settings and restore the caller environment on success or failure, without
  replacing an already initialized server. Every actual harness call still uses explicit settings.
- After this correction, **65 tests passed with no skips**, including both pinned real external
  routers, all nine fresh/preloaded/failing-import cases, and the three affected harness suites.
  Historical security reports remain byte-for-byte unchanged; current outcome recomputation and
  current script-digest checks are separate from historical provenance.
- The four intended pytest markers now include `real_kicad`. Safety-evaluation harness and CI
  classifier changes fan evidence replay out to all supported interpreters. The focused CI
  contract/classifier group passed **27 tests**.
- The earlier fast run passed 4,524 tests with two skips in 195.68 seconds (196.14 seconds wall).
  That observation is not a matched, final-source serial-versus-fast experiment and does not by
  itself close the three-times acceleration target. Hosted timing remains unmeasured.

These overlapping test groups are not additive. Earlier failed or interrupted runs remain
non-successful evidence; successful focused checks do not certify untested integration changes.

## Local runtime and native work

The explicitly authorized `copper-mcp` Colima profile was restarted after the host reboot with
the default Docker context left unchanged. Its socket is
`/Users/seunghyukchoe/.colima/copper-mcp/docker.sock`; both immutable images documented in the
[runtime guide](../integrations/local-router-runtime.md) remain present. Smokes use public
synthetic inputs, no network or host mounts, and establish execution/format availability only.

An unmodified sparse checkout of official KiCad 10.0.5 source is prepared under ignored
`build/kicad-native-source`, pinned to commit `18fb9289ff0efdca53c0352ed81a0973f0a6b58c`.
The installed application was neither replaced nor modified. Source inspection confirms that
stock begin/push/drop commit operations do not establish the requested atomic revision guard.
An isolated native transaction implementation, build and fault-injection evidence remain open.

## Remaining acceptance boundaries

The frozen [readiness catalog](../integrations/balanced-readiness.md) supplies a requirements
denominator, not an authenticated evidence intake or an audited score. Electrical-input
declarations do not execute physics. General project schematic capture/parity, fabrication-output
DFM, calibrated SI/PI/thermal/EMC authorities and their reference cases remain outstanding.

Routing still needs production external conversion/disposal, fresh zone fill, cross-layer
multi-pin composition and bounded repair integration. Placement needs measured clearance and
held-out improvement under equal routing budgets. OrcaRouter is incorporated as an advisory
adapter, but production scheduling and held-out quality measurement are not established.

Strict live mutation, genuine host confirmation and recovery tests, the licensed held-out corpus,
successful hosted calibration, independent integrated review and release ledgers remain gates.
Do not treat synthetic confirmations, one-undo IPC support, declarations, or a high test count as
substitutes. No unattended saving or general engineering certification is authorized.

## Validation queue before closure (historical)

Finish the candidate-byte parity increment and scoped review, then freeze source while running
the compatibility suites and current-source evidence replay. Stage only owned implementation
files; do not use cleanup/reset commands on either checkout. Record final-source results here
before claiming that the updated integration is green.

## Review and compatibility follow-up

The first environment-only correction passed its focused tests but independent review found
that it still cached test settings in the canonical application server. The offline harnesses
now own a separate private module. Twelve subprocess cases verify fresh/preloaded/failed imports
and a later HTTP CLI startup with its own settings; no real transport starts in those tests.
All three current reports include the helper's path/hash in their identities, while historical
artifact bytes and independently compared outcome fields remain unchanged. The final focused
harness group passed 49 tests in 26.54 seconds.

Captured-byte parity was implemented and exercised against real KiCad. Review found an early
deadline check and per-phase refresh gap; these are corrected with phase-specific fault tests.
The combined parity suite passed 80 tests and 23 subtests. This is a private adapter, not a
new `optimization/v1` field or a project/physics judgement claim.

The first non-evidence compatibility run on Python 3.11.15 passed 4,563 tests, two skips and
779 subtests in 169.08 seconds (170.64 seconds wall), with real router smokes configured.
The corresponding Python 3.13.15 run recorded 4,562 passed, one failure and two skips in
168.34 seconds: an earlier CLI test left an environment variable pointing at a deleted temporary
workspace. Relevant CLI test classes now restore their environments, and committed-killing-test
collection uses an explicit safe environment, tested against deliberately invalid parent settings.
The correction and parity checks passed 154 tests and 56 subtests on each compatibility interpreter.
These focused results do not replace the required broad rerun.

The subsequent canonical four-worker coverage run completed pytest in 520.61 seconds but failed
two mutation-harness tests (4,565 passed, two failed, two skipped; 779 subtests passed). Inherited
`PYTEST_ADDOPTS` enabled nested xdist and changed collection-error exit codes, including one false
mutation-kill classification. The nested runner now clears ambient options, preserves explicit
spec arguments, and forces serial execution. Missing tests and import-breaking mutants retain
their non-evidence classifications under a parallel outer run.

An integrated optimization-runtime review found no P0/P1 issues and four P2 reliability gaps.
Their fixes require explicit fresh approval observations; cover parent response parsing, decoding,
hashing and callbacks with deadline checks; cap heartbeat renewal to remaining runtime; and revoke
cancelled private data immediately, releasing done-work accounting without waiting for its TTL.
The existing `completed` review result is preserved and does not mean applied. See SEC-186.
Final scoped re-review and a new complete validation run are still required after these changes.

## Verified local closure

Final executable/input inventory:
`sha256:324658cefd328e078581b064297c66a75437414db3e553193629bf83701369c7`.
The before/after inventory remained identical throughout the final validation. Source, tests and
implementation were staged, not committed or published. Subsequent changes to this checkpoint
and the ledger record observations only; they do not alter that executable/input inventory.

- Python 3.12.13: full `make check`, four workers with coverage, **4,586 passed, two skipped,
  779 subtests passed**, 530.33 seconds in pytest and 543.78 seconds overall. Coverage was 89%
  (a code-coverage statistic, not a readiness percentage). Lint, metadata/schema/ledger checks,
  strict mypy across 136 source modules, secret scanning, dependency audit and wheel/sdist build
  passed. The fresh B-141 source-bound replay was included and passed in 180.71 seconds.
- Python 3.11.15: final non-evidence compatibility sweep **4,585 passed, two skipped, 779
  subtests passed**, 288.15 seconds in pytest. The final isolated-delivery regression plus fresh
  B-141 replay passed separately: **24 tests**, 174.94 seconds, including 172.00 seconds for replay.
- Python 3.13.15: the same final compatibility selection **4,585 passed, two skipped, 779
  subtests passed**, 282.75 seconds in pytest. Isolated delivery plus fresh replay: **24 tests**,
  166.84 seconds, including 163.76 seconds for replay.

The two final compatibility sweeps ran concurrently with two workers each, never more than four
test workers total. Their timings are not directly comparable to the earlier four-worker-per-leg
observations. No matched serial-versus-fast speedup or hosted PR-duration claim is made.

The skips were an inapplicable two-pin endpoint-swap scenario and a KiCad authority-path test
whose discovery used PATH. The latter was then executed separately on all three interpreters
with the installed KiCad location added to that subprocess's PATH: **one pass per interpreter,
no skips**. That existing test validates the authority path's permitted verdict shape, not that
the board earned a successful engineering verdict. Other real KiCad parity/DRC cases and both
configured real external-router smokes ran in the broad selections.

The final delivery guard also checks the clock after its database status query; a regression
first reproduced late successful delivery when the query itself crossed the deadline. The fix
and positive control passed independent review. Reviewed repository, lease, delivery, cancellation,
callback-retention and mutation-runner corrections have no remaining P1/P2 findings in that scope.
This is an integrated runtime/remediation review, not specialist physics or whole-release approval.

Local wheel, sdist and PCM artifacts were rebuilt and the sdist tracked-file allowlist passed.
They remain development artifacts under `dist/`; a generated download URL is not publication.

## Next development and external boundary

The next baseline gate is publishing this reviewed integration branch as a **draft pull request**
for hosted validation and calibration. That requires explicit user authorization; no push,
pull request, merge, tag or release has been performed. Do not lower CI timeouts based on local
timings or mark the original v0.13 acceptance complete.

After baseline integration, prioritize real project-context electrical capture and a distinct
versioned parity/judge package, production external-router conversion/disposal, candidate-bound
fresh fill and multi-pin repair, measured placement quality, and the native KiCad transaction
prototype. The licensed held-out corpus and calibrated physics/reference cases remain essential.
Installation of a modified KiCad test application, actual human-host consent, hardware/specialist
validation and public release remain explicit human/environment gates. Stock live apply still
refuses mutation; no unattended save or aggregate engineering sign-off has been introduced.
