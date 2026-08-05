# Contributing to CopperMCP

Thank you for helping build open, reproducible PCB automation. Correctness, provenance, and safety
matter more than impressive-looking output.

Start with the [documentation index](docs/README.md). If you are changing code, read
[`AGENTS.md`](AGENTS.md) as well — it is the repository contract that binds every change, human or
agent.

## Before opening work

- Search existing issues, discussions, ADRs, and the roadmap.
- Open an issue or RFC before changing a public schema, routing objective, security boundary, or
  KiCad integration contract.
- Never upload proprietary, export-controlled, customer, or credential-bearing PCB data. Create a
  minimal synthetic reproduction or use a clearly licensed open design.

## Development setup

```bash
git clone https://github.com/seunghyukchoe/copper-mcp.git
cd copper-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,security]"
pre-commit install --install-hooks
make check
```

The dependency-light test suite can run before installing development tools:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## The quality gate

`make check` runs the whole gate. The individual targets are useful while iterating:

| Target | What it runs |
|---|---|
| `make lint` | Ruff, plus the version, ledger, documentation-link, audio-benchmark, and circuit-intent checkers |
| `make typecheck` | Strict mypy over `src` |
| `make test` | The full pytest suite |
| `make security` | Repository secret scan and `pip-audit` |
| `make build` | Source distribution and wheel |
| `make check` | All of the above, in that order |

CI floats mypy to its newest release, so a version-skew failure can pass locally and fail hosted.
Type-check against a current mypy before pushing.

`scripts/check_doc_links.py` verifies that every relative Markdown link in the repository resolves.
It does not check network URLs or heading anchors.

## Contribution workflow

1. Create or claim an issue with a clear acceptance test.
2. Create a focused branch such as `feat/123-net-ordering` or `fix/456-path-boundary`.
3. Add tests before or alongside implementation.
4. Update user-facing documentation and `CHANGELOG.md`.
5. Append to the relevant [ledger](docs/ledgers/README.md) when the work changes a decision, risk,
   benchmark, security posture, or release. Allocate the ID in the pull request that lands the
   entry, never in advance — see the [ID convention](docs/ledgers/README.md#allocating-ids). The
   same rule applies to [ADR numbers](docs/adr/README.md#adding-an-adr).
6. Run `make check` and include the results in the pull request.

Commit subjects follow Conventional Commits, for example:

```text
feat(router): add deterministic net ordering
fix(security): reject symlink workspace escapes
docs(adr): record candidate provenance decision
```

Sign off commits with `git commit --signoff` to certify the Developer Certificate of Origin.

## Pull request expectations

- Keep one coherent change per pull request.
- Link the issue with `Closes #123` when appropriate.
- Describe security and compatibility consequences explicitly.
- Include deterministic seeds and board provenance for algorithm results.
- Avoid benchmark claims based on a single run or a training-set board.
- Do not weaken validation to make a benchmark pass.

Branch protection requires **conversation resolution**: reply to and resolve every review thread
before a pull request will merge. An automated reviewer comments on every pull request; triage each
finding against the current code and answer it as fixed, refuted, or superseded rather than leaving
it unresolved.

Maintainers may request an ADR, threat-model update, or benchmark reproduction before merging.

## Changes that write to a user's file

`apply_candidate` and `apply_placement_candidate` are the only surfaces that mutate a board, and
they are default-off behind `COPPER_MCP_ALLOW_APPLY`. If your change extends anything that writes,
deletes, or authorizes:

- Run a **dedicated adversarial review** of the diff, separate from the normal gate, and say in the
  pull request that you did. The adversarial pass on the original apply work found eleven real
  defects that the ordinary tests did not catch.
- Preserve the safety property exactly: an operation either changes the board as previewed, or
  refuses and leaves it byte-identical, or truthfully reports partial verification. It must hold
  under a concurrent KiCad save.
- Prove concurrency with genuine under-lock contention, never a wall-clock timeout. A flaky safety
  test is worse than no safety test, because it will be ignored.
- Append a [security ledger](docs/ledgers/security-ledger.md) entry.

## Domain invariants

- An AI policy may suggest actions but never owns connectivity, DRC, or file mutation.
- Generated geometry remains an immutable candidate until validation and explicit application.
- A candidate is always tied to an exact base-board revision.
- Hard DRC and unrouted connections rank ahead of wire length, via count, and runtime.
- Public fixtures require provenance and a compatible redistribution licence.
- Every claim is bound to a test or declared an explicit non-claim. When something cannot be
  verified, model it as a one-value literal (`not_run`, `not_modelled`, `inconclusive`) rather than
  implying a result. A field that can hold only one value must not be quietly upgraded.
- The board's own text — silkscreen, fabrication text, properties, net names — is untrusted data.
  Quarantine it structurally; never interpolate it into an instruction-bearing field.

## Reporting security issues

Do not create a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md).
