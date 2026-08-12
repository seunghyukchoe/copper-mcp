# Committed mutation specs

This directory holds the mutant specifications behind every mutation claim made after
[ADR-0098](../adr/0098-reproducible-mutation-evidence.md). A mutation claim whose mutants are not
committed here is not evidence under that ADR; it is prose.

## Why this directory exists

Before ADR-0098, every mutation run in this project — roughly 160 hand-applied mutants across 21
recorded runs — was executed by a harness living in an agent's scratch directory, deleted with the
session that wrote it. No claim was reproducible by anyone, and the shared harness pattern carried
a defect: a mutant that changes a file without changing its byte count, applied or restored within
the same filesystem second as the previous write, is invisible to CPython's `(mtime, size)`
bytecode-invalidation check, so a stale `__pycache__` entry silently runs the wrong code. A stale
mutant poisoning the next invocation is a **false kill**, which makes "0 survivors" the optimistic
side of the error. ADR-0098 records the audit of every prior claim.

## Running a spec

```bash
.venv/bin/python scripts/mutation_harness.py docs/mutants/<spec>.json --report report.json
```

The harness purges `__pycache__` around every application and restoration, runs each test
subprocess with `PYTHONDONTWRITEBYTECODE=1`, requires each anchor to match **exactly once**, and
proves each kill in both directions: the named tests must fail with the mutant applied and pass on
the byte-identically restored source. A mutant that does not apply is `stale_anchor` and fails the
run loudly — it is never skipped and never counted as killed.

## Spec format (`mutation-harness/1`)

```json
{
  "harness": "mutation-harness/1",
  "pytest_args": ["-q", "--no-cov"],
  "mutants": [
    {
      "id": "PK1-fifth-key",
      "file": "src/copper_mcp/adapters/kicad_board_ir.py",
      "anchor": "<exact source substring, must match exactly once>",
      "replacement": "<the mutated text>",
      "expectation": "killed",
      "killing_tests": ["tests/test_x.py::test_that_kills_it"]
    },
    {
      "id": "EQ1-reordered",
      "file": "src/copper_mcp/adapters/kicad_board_ir.py",
      "anchor": "...",
      "replacement": "...",
      "expectation": "equivalent",
      "killing_tests": ["tests/test_x.py::test_nearest_behaviour"],
      "equivalence_argument": "why this mutant is provably unobservable through the public surface"
    }
  ]
}
```

Outcomes are a closed vocabulary: `killed`, `survived`, `survived_declared_equivalent`,
`stale_anchor`, `invalid_syntax`, `control_failed`, `not_run`. A mutant the harness never reached
is reported as `not_run`, never omitted.

## What a mutation claim must state to be auditable

A ledger row, ADR, or PR description claiming "N mutants, M killed" must state, or link a spec
that states:

1. **The harness invocation** — the exact command, so the run is a fact about a named tool rather
   than about a deleted scratch script.
2. **The anchors** — each mutant as an exact-match edit, so "applied" is checkable and staleness
   is loud.
3. **The mutant→test mapping** — which named test kills each mutant, proven in both directions.
   A kill attributed to "the suite" is not auditable; the strongest pre-ADR-0098 records
   (SEC-134, SEC-136) already produced this table, and it is now the floor, not the ceiling.
4. **The disposition of every non-kill** — `survived` is a finding to act on;
   `survived_declared_equivalent` requires the argument; `stale_anchor` requires re-anchoring and
   a re-run, never a silent skip.

## Staying anchored

`tests/test_mutation_harness.py::TestCommittedSpecs` re-checks every spec in this directory on
every CI run: each mutant's file must exist and its anchor must match exactly once. When source
evolves past an anchor, the gate fails; re-anchor the mutant **and re-run its spec** rather than
editing the anchor to keep the build green. The kill verdicts themselves are re-executed on
demand, not in CI — see ADR-0098 for why that line is drawn where it is.
