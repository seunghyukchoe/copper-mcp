# Release Ledger

Public releases and any recovery actions are recorded below.

| Version | Date | Tag / commit | Artifacts | Validation | Security | Notes |
|---|---|---|---|---|---|---|
| 0.1.0 | 2026-08-03 | [`v0.1.0`](https://github.com/seunghyukchoe/copper-mcp/releases/tag/v0.1.0) / [`a65c548`](https://github.com/seunghyukchoe/copper-mcp/commit/a65c5484fe57b1c67a93dc913fdec47f766e82d7) | Wheel `e563e2f3…6edc` and sdist `7128688e…202` published with GitHub build-provenance attestations | Dry run [#30762423298](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30762423298) and tag run [#30762649321](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30762649321) passed version, 32-test, audit, build, upload, and attestation gates; published wheel installed and reported `0.1.0` in a clean environment | CodeQL, secret scan, dependency review, dependency audit, immutable tag, and both artifact attestations verified against the release workflow, `refs/tags/v0.1.0`, and exact source digest | Published. The automated release-create step lacked repository context; the same attested artifacts were manually recovered after digest/provenance/install verification, and the workflow now passes `--repo` explicitly. |

## Release authorization

Rows are append-only. A `Ready` row may be added only after a clean `make check` on the exact source
commit recorded in that row. The release metadata commit containing the row may change only this
ledger and `CHANGELOG.md` relative to that source commit. `Ready` authorizes creating the
corresponding tag from the metadata commit; it is not evidence that a tag, artifact, GitHub release,
or package publication exists.

| Version | Date | Validated source commit | Full gate evidence | Status |
|---|---|---|---|---|

## Unreleased readiness

| Target | Date | Source state | Completed validation | Outstanding release gates | Status |
|---|---|---|---|---|---|
| 0.2.0 | 2026-08-03 | `codex/polygon-zone-obstacles`; local changes not yet committed or pushed | Ruff; strict mypy across 38 source files; version and ledger checks; audio and Circuit Intent/schema checkers; 370 tests plus 13 subtests; secret scan; real KiCad 10.0.5 schematic SVG and exact `kicadxml` connectivity export | `pip-audit` cannot resolve PyPI in the managed network; isolated build cannot download `hatchling`; real KiCad PCB DRC aborts before a report in the managed macOS sandbox; local `.git` is read-only and the configured `gh` token is invalid | MVP implementation validated locally, but not tagged, published, or accepted as a release until every external gate reruns successfully in an unrestricted authenticated environment. |

Corrections require a dated note below the table; never replace released assets silently.

> **Unreleased audit-remediation addendum — 2026-08-03:** The `0.2.0` readiness row above is a
> historical local-validation snapshot, not a release. The tag gate must additionally verify
> descriptor-anchored workspace reads, exact-lowercase create-only schematic output, absolute
> 15-minute capability-access expiry with documented lazy reclamation, snapshot-confined KiCad
> file-table dependencies with environment/absolute/remote/plugin URIs rejected, and a private KiCad
> working directory. Expiry is not a secure memory-erasure claim. Circuit Scene IR and semantic/
> visual placement remain future north-star work and must not appear in `0.2.0` release claims.

> **Stable post-audit validation evidence — 2026-08-03:** Ruff and every schema, version, ledger,
> audio, and Circuit Intent checker passed; strict mypy passed across 39 source files; full pytest
> collected 386 tests, with 385 passed and one explicit managed-macOS real-PCB-DRC skip. That suite
> ran the real KiCad 10.0.5 schematic SVG export and exact `kicadxml` connectivity round trip. The
> secret scan and git diff check also passed. External release gates remain blocked: `pip-audit`
> cannot resolve `pypi.org`; `hatchling` is unavailable and the isolated build cannot fetch it; real
> PCB DRC is unavailable in the managed sandbox; and local `.git` is read-only while `gh`
> authentication is invalid. This is stable local evidence, not a tag, artifact build, or release.

> **Independent unrestricted audit correction — 2026-08-03:** An unrestricted desktop run used
> Python 3.12.13 with `CODEX_SANDBOX` absent and passed all 386 tests plus 26 subtests with KiCad
> 10.0.5. The five real PCB DRC nodes were
> `tests/test_kicad_cli.py::KiCadCliTests::test_real_kicad_drc_is_read_only`,
> `tests/test_kicad_candidate_drc.py::test_real_kicad_candidate_evidence_is_private_and_read_only`,
> `tests/test_kicad_route_patch.py::test_real_kicad_drc_accepts_disposable_candidate_without_source_mutation`,
> `tests/test_route_preview.py::test_real_kicad_confirms_the_previewed_candidate_without_mutating_the_source`,
> and
> `tests/test_route_preview.py::test_real_kicad_confirms_a_route_detoured_around_existing_copper`;
> all passed and together preserved source bytes, mtime, and inode where applicable. The managed
> root run still has one environment-dependent real-PCB-DRC skip/process exit `-6`, but real PCB
> validation has now succeeded independently. Remaining external blockers are the dependency-audit
> and isolated-build network path, local `.git` write restriction, and invalid `gh` authentication.
> No `0.2.0` release authorization row has been added.

> **Final integration follow-up — 2026-08-03:** After adding the executable release-authorization
> parser and two focused gate regressions, the managed root suite collected 388 tests: 387 passed and
> the same one real-PCB-DRC node skipped under the managed macOS environment. Ruff, every schema,
> version, ledger, audio, and Circuit Intent checker, strict mypy across 39 source files, the secret
> scan, and `git diff --check` passed. The earlier unrestricted 386-test audit remains the real-PCB
> evidence because the two later tests cover only the pure release-ledger parser. `pip-audit` still
> cannot resolve `pypi.org`, and the no-isolation build still lacks `hatchling`; no `Ready` row,
> tag, build artifact, or release is claimed.
