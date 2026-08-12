# Committed mutation specs

This directory holds the mutant specifications behind every mutation claim made after
[ADR-0098](../adr/0098-reproducible-mutation-evidence.md). A mutation claim whose mutants are not
committed here is not evidence under that ADR; it is prose.

## Why this directory exists

Before ADR-0098, every mutation run in this project — roughly 170 hand-applied mutants across 24
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

Two guards exist because both failure modes happened for real:

- **No mutant is applied until the unmutated killing tests pass.** PR #154's first scratch
  harness reported 11/11 killed while executing zero tests — a mistyped test path made pytest
  exit 4 every time, and exit 4 read as a kill. A red baseline measures nothing.
- **Only pytest exit 1 counts as a kill.** Exit 0 is a survivor; every other exit (2 interrupted,
  3 internal error, 4 usage/collection error, 5 nothing collected) is `invalid_run`, because a
  kill is only evidence if the run that produced it was capable of reporting a survivor.

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
`stale_anchor`, `invalid_syntax`, `control_failed`, `invalid_run`, `not_run`. A mutant the
harness never reached is reported as `not_run`, never omitted — including when the baseline
fails or a hard failure aborts the run: the report is still written, with every mutant in it.

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
5. **The interpreter** — the harness report records `python_version` and `platform`, and the
   claim must carry (or link a report carrying) them. A run on an interpreter outside
   `requires-python` and the CI matrix is not evidence about the shipped code; PR #154 discarded
   a whole gate run for exactly this after `python3 -m venv` silently picked up Python 3.14.

Two rules about killing tests, both enforced by the CI gate:

- **Never name `tests/test_mutation_harness.py` (or its `TestCommittedSpecs` gate) as a killing
  test.** That gate fails for *any* applied mutant of a committed spec, so it would "kill"
  every mutant regardless of behaviour — a universal false-kill oracle that proves nothing.
- **Killing tests must be real, collectable node IDs.** The gate runs
  `pytest --collect-only` over every committed mapping, so a renamed test fails the build
  instead of silently hollowing out a claim.

And one limit to keep in view when reading a report: **`killed` means the named tests fail with
the mutant applied and pass without it — not that the behaviour is covered.** A comment-only
mutant reports `killed` if its killing test reads the source file, and one flaky test is one
false kill (each direction runs once; there is no repetition and no failure-cause attribution).
Choose killing tests that exercise the mutated behaviour, and weigh the mapping, not just the
count.

## Staying anchored

`tests/test_mutation_harness.py::TestCommittedSpecs` re-checks every spec in this directory on
every CI run: each mutant's file must exist and its anchor must match exactly once. When source
evolves past an anchor, the gate fails; re-anchor the mutant **and re-run its spec** rather than
editing the anchor to keep the build green. The kill verdicts themselves are re-executed on
demand, not in CI — see ADR-0098 for why that line is drawn where it is.
