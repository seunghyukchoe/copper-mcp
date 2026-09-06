# ADR-0135: Inner placement exhaustion is not completed search work

- Status: Proposed
- Date: 2026-09-06
- Owners: CopperMCP maintainers
- Related: [ADR-0067](0067-route-aware-placement-ranking.md),
  [ADR-0133](0133-native-optimization-execution-and-host-confirmation.md)

## Context

The placement solver used to skip every legalizer result lacking a candidate. That conflated
an evaluated illegal pose with an inner check or wall-clock budget running out. An interrupted
legalizer on the final evaluation could therefore produce outer `work_exhausted/128`, allowing
the benchmark to certify a nondeterministically truncated search as deterministic work.

## Decision

Stop the search immediately on the legalizer's typed `budget_exhausted` diagnostic. Report
`legalizer_exhausted`, distinct from the solver's own `work_exhausted` evaluation ceiling. This
covers both legalizer work and time limits without parsing diagnostic message strings or
claiming which budget expired. Initial input exhaustion is not an input-format refusal.

Cancellation and an expired enclosing deadline retain precedence. Previously legalized entries
may remain in a private solver result, as with the existing deadline result, but the optimization
adapter rejects incomplete outcomes before any derivative serialization. It maps inner or outer
exhaustion to the existing job budget failure and never certifies a partial placement search.

Benchmark replay requires both the exact deterministic evaluation ceiling and an outer
`work_exhausted` result. A nested interruption therefore refuses even on evaluation 128. Check
the baseline result before spending work on the second policy. The benchmark profile allows the
solver's existing maximum 60-second outer guard; this changes the benchmark configuration digest,
not production defaults, accepted candidate geometry or historical artifacts. Repeated complete
observations remain mandatory, with no fake clocks or re-signed historical evidence.

## Validation

Real legalizer budget faults are injected at the first, intermediate and final evaluations,
for both work and time limits. Tests verify the distinct status, exact stopping count and source
immutability. A benchmark test proves that inner exhaustion at the final evaluation cannot
become a successful replay. Normal complete search still retains existing candidate identities.
