# Risk Register

| ID | Risk | Likelihood | Impact | Mitigation | Status / owner |
|---|---|---:|---:|---|---|
| R-001 | Malformed or oversized PCB input exhausts the parser. | Medium | High | Bounded reads, typed validation, fuzzing, no file execution. | Open / security |
| R-002 | Model output bypasses constraints or triggers excessive agency. | High | High | Allowlisted policy actions, deterministic geometry/DRC, separate apply authorization. | Open / architecture |
| R-003 | A candidate is applied to a board changed after routing began. | Medium | High | Content hashes, live revision recheck, atomic undoable commit. | Open / KiCad integration |
| R-004 | Dependency or GitHub Action compromise affects contributors/releases. | Medium | High | Minimal dependencies, Dependabot, audit, CodeQL, immutable action pins, scoped permissions, a non-publishing release dry run, and tag-only attestations/releases. | Mitigated / release |
| R-005 | Benchmark leakage or selected examples exaggerate quality. | High | Medium | Project-family splits, multiple seeds, publish failures, immutable benchmark ledger. | Open / research |
| R-006 | DRC-clean output is treated as electrically or manufacturably safe. | Medium | High | Explicit limitations, physics/DFM gates, professional review requirement. | Open / documentation |
| R-007 | KiCad subprocess or report-schema drift yields unsafe or stale evidence. | Medium | High | Fixed arguments, timeout, bounded private report, fail-closed schema parsing, board revision recheck. | Mitigated / KiCad integration |
