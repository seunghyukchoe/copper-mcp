# Release Ledger

No public releases have been published.

| Version | Date | Tag / commit | Artifacts | Validation | Security | Notes |
|---|---|---|---|---|---|---|
| 0.1.0 | Release candidate prepared 2026-08-03 | `v0.1.0` / pending release commit | Wheel and sdist built by the release verifier; tag workflow will rebuild and attest them | `make check` and 32-test suite passed locally and on GitHub; non-publishing release dry run [#30762423298](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30762423298) passed against `b0b9c28`, including version verification, full gate, and artifact upload | Initial, KiCad boundary, and Actions supply-chain reviews recorded; CodeQL, secret scan, dependency review, and dependency audit passed on `main` | Foundation release candidate; not yet published |

When a release completes, replace `Planned` and `TBD` with immutable facts and link the GitHub
release. Corrections require a dated note below the table.
