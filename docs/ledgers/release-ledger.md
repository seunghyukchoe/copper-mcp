# Release Ledger

Public releases and any recovery actions are recorded below.

| Version | Date | Tag / commit | Artifacts | Validation | Security | Notes |
|---|---|---|---|---|---|---|
| 0.1.0 | 2026-08-03 | [`v0.1.0`](https://github.com/seunghyukchoe/copper-mcp/releases/tag/v0.1.0) / [`a65c548`](https://github.com/seunghyukchoe/copper-mcp/commit/a65c5484fe57b1c67a93dc913fdec47f766e82d7) | Wheel `e563e2f3…6edc` and sdist `7128688e…202` published with GitHub build-provenance attestations | Dry run [#30762423298](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30762423298) and tag run [#30762649321](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30762649321) passed version, 32-test, audit, build, upload, and attestation gates; published wheel installed and reported `0.1.0` in a clean environment | CodeQL, secret scan, dependency review, dependency audit, immutable tag, and both artifact attestations verified against the release workflow, `refs/tags/v0.1.0`, and exact source digest | Published. The automated release-create step lacked repository context; the same attested artifacts were manually recovered after digest/provenance/install verification, and the workflow now passes `--repo` explicitly. |

Corrections require a dated note below the table; never replace released assets silently.
