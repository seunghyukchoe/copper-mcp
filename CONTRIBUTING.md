# Contributing to CopperMCP

Thank you for helping build open, reproducible PCB automation. Correctness, provenance, and safety
matter more than impressive-looking output.

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

## Contribution workflow

1. Create or claim an issue with a clear acceptance test.
2. Create a focused branch such as `feat/123-net-ordering` or `fix/456-path-boundary`.
3. Add tests before or alongside implementation.
4. Update user-facing documentation and `CHANGELOG.md`.
5. Append to the relevant ledger when the work changes a decision, risk, benchmark, security
   posture, or release.
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

Maintainers may request an ADR, threat-model update, or benchmark reproduction before merging.

## Domain invariants

- An AI policy may suggest actions but never owns connectivity, DRC, or file mutation.
- Generated geometry remains an immutable candidate until validation and explicit application.
- A candidate is always tied to an exact base-board revision.
- Hard DRC and unrouted connections rank ahead of wire length, via count, and runtime.
- Public fixtures require provenance and a compatible redistribution licence.

## Reporting security issues

Do not create a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md).
