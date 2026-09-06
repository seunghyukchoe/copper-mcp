# ADR-0137: Isolate census recomputation without dropping compatibility checks

- Status: Proposed
- Date: 2026-09-06
- Related: [development guide](../development.md),
  [execution program](../plans/balanced-readiness-execution.md)

## Observation

Hosted draft PR #268 run `34009062108` passed, but its required path took 32 minutes 12 seconds.
The Python 3.12 compatibility test step took 1,889.45 seconds; the non-coverage interpreter jobs
each finished in approximately nine minutes. Local timing repeatedly showed B-140's complete
census fixture taking roughly 350–380 seconds with coverage versus roughly 90 without coverage.
This is evidence of an instrumentation-sensitive recomputation, not proof of the complete hosted
bottleneck decomposition. Add per-test duration reporting before making broader scheduling claims.

## Decision

Keep every B-140 compatibility assertion and its once-per-module current recomputation on every
supported interpreter. Do not mark these tests out of PRs, substitute a historical artifact for
fresh measurement, or alter the historical census runner and report bytes.

Run the full census fixture through a fixed `--census` mode of the existing isolated source-binding
entrypoint. Reuse the before/after source inventory and digest-checking source loader. This mode
imports only the fixed census runner after bootstrap, computes one report, verifies the source
inventory again and emits a bounded self-digested `current-census-replay/v1` envelope. The parent
checks closed fields, receipt digest, interpreter, repetition count, source identity and report
type before existing assertions consume it. Unknown argument forms fail closed.

The subprocess uses the selected interpreter with `-I`, a minimal PATH/locale environment, no
coverage or pytest environment inheritance, the existing 3,600-second replay ceiling, and at
most one MiB of encoded output. Its status is `measured`, not engineering pass or release approval.
The existing no-argument B-141 receipt, two repetitions and provenance interpretation are unchanged.

Coverage still runs on canonical product/contract tests and focused recomputation/refusal cases.
The full corpus census now supplies execution evidence without multiplying tracing overhead by
every explored node. A changed coverage percentage must be reported honestly; this is not a claim
that every line executed by the child is recorded by the parent's coverage tracer.

## Validation

Require real fresh B-140 comparison, unchanged legacy B-141 behavior, stale-bytecode/source-drift
refusal, malformed/oversized/tampered envelope refusal and fixed child environment. Re-measure
the local fixture and hosted path. Keep the failed timing target in the record, retain provisional
job timeouts, and do not fill main-calibration entries from a pull-request run.
