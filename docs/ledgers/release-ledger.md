# Release Ledger

Public releases and any recovery actions are recorded below.

| Version | Date | Tag / commit | Artifacts | Validation | Security | Notes |
|---|---|---|---|---|---|---|
| 0.1.0 | 2026-08-03 | [`v0.1.0`](https://github.com/seunghyukchoe/copper-mcp/releases/tag/v0.1.0) / [`a65c548`](https://github.com/seunghyukchoe/copper-mcp/commit/a65c5484fe57b1c67a93dc913fdec47f766e82d7) | Wheel `e563e2f3…6edc` and sdist `7128688e…202` published with GitHub build-provenance attestations | Dry run [#30762423298](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30762423298) and tag run [#30762649321](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30762649321) passed version, 32-test, audit, build, upload, and attestation gates; published wheel installed and reported `0.1.0` in a clean environment | CodeQL, secret scan, dependency review, dependency audit, immutable tag, and both artifact attestations verified against the release workflow, `refs/tags/v0.1.0`, and exact source digest | Published. The automated release-create step lacked repository context; the same attested artifacts were manually recovered after digest/provenance/install verification, and the workflow now passes `--repo` explicitly. |
| 0.2.0 | 2026-08-03 UTC | [`v0.2.0`](https://github.com/seunghyukchoe/copper-mcp/releases/tag/v0.2.0) / [`a099a661d89dc47dc16dd32f76f808e0956224f4`](https://github.com/seunghyukchoe/copper-mcp/commit/a099a661d89dc47dc16dd32f76f808e0956224f4) | Wheel `copper_mcp-0.2.0-py3-none-any.whl`: 126,493 bytes, SHA-256 `3f2b46577bf6a9bba3ec7d54215ed0ddeddc8b077fb331571a00904684769235`; sdist `copper_mcp-0.2.0.tar.gz`: 3,087,503 bytes, SHA-256 `5a6034c7f9511143ebe2dc659d87693e18936184faa7406af470eb0d19bae040` | Tag-triggered Release run [#30811504543](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30811504543) completed successfully at the exact tag commit; public job/step records show the version check, `make check` release gate, distribution upload, provenance attestation, and GitHub release creation succeeded | Downloaded wheel/sdist hashes and sizes match the public release API; public SLSA v1 provenance verified for both against `refs/tags/v0.2.0`, source digest `a099a661d89dc47dc16dd32f76f808e0956224f4`, and `.github/workflows/release.yml` | Published `2026-08-03T11:57:49Z`; historical reconciliation only, with no clean-install or current-branch ancestry claim |
| 0.3.0 | 2026-08-03 UTC | [`v0.3.0`](https://github.com/seunghyukchoe/copper-mcp/releases/tag/v0.3.0) / [`50eabf73e76f13535d74dfad1a1bd24c46cc594a`](https://github.com/seunghyukchoe/copper-mcp/commit/50eabf73e76f13535d74dfad1a1bd24c46cc594a) | Wheel `copper_mcp-0.3.0-py3-none-any.whl`: 139,882 bytes, SHA-256 `e178c1082288097c2fd6da25bb129df4572d7c4075298de55905674db1c8f2a1`; sdist `copper_mcp-0.3.0.tar.gz`: 3,143,758 bytes, SHA-256 `80d6404f589fd678bb74b5f01082ce44423c07cb830687ced81269c004f972ca`; `coppertone-render.png`: 229,273 bytes, SHA-256 `5386201fb9a95a7ff77ddb302fab79519d9049f1718743e0d1342c6845d1141d` | Tag-triggered Release run [#30826975351](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30826975351) completed successfully at the exact tag commit; public job/step records show the version check, `make check` release gate, distribution upload, provenance attestation, and GitHub release creation succeeded | Downloaded asset hashes and sizes match the public release API; public SLSA v1 provenance verified for the wheel/sdist against `refs/tags/v0.3.0`, source digest `50eabf73e76f13535d74dfad1a1bd24c46cc594a`, and `.github/workflows/release.yml`; the PNG attestation lookup returned HTTP 404 and no PNG provenance is claimed | Published `2026-08-03T15:21:27Z`; historical reconciliation only, with no clean-install or current-branch ancestry claim |
| 0.4.0 | 2026-08-03 UTC | [`v0.4.0`](https://github.com/seunghyukchoe/copper-mcp/releases/tag/v0.4.0) / [`6304d777595499ec60c6a8ca4c608354dd1d9753`](https://github.com/seunghyukchoe/copper-mcp/commit/6304d777595499ec60c6a8ca4c608354dd1d9753) | Wheel `copper_mcp-0.4.0-py3-none-any.whl`: 190,950 bytes, SHA-256 `bb7be666926f709c370fea7e4381b3e02bb7b461f39db8a5dff93dbc1552f1c9`; sdist `copper_mcp-0.4.0.tar.gz`: 3,248,261 bytes, SHA-256 `fe82abeb334947b6cf518a065e08a4e591045f936b9a6bd81e3495937df14d74` | Tag-triggered Release run [#30839482036](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30839482036) completed successfully at the exact tag commit; public job/step records show the version check, `make check` release gate, distribution upload, provenance attestation, and GitHub release creation succeeded | Downloaded wheel/sdist hashes and sizes match the public release API; public SLSA v1 provenance verified for both against `refs/tags/v0.4.0`, source digest `6304d777595499ec60c6a8ca4c608354dd1d9753`, and `.github/workflows/release.yml` | Published `2026-08-03T18:04:34Z`; historical reconciliation only, with no clean-install or current-branch ancestry claim |

> **Published-release history reconciliation — 2026-08-05:** The three rows above restore public
> release history that was absent from this divergent ledger line. GitHub's release, tag-object,
> workflow-run/job, and attestation APIs were checked read-only; every asset was downloaded and its
> byte size and SHA-256 independently reproduced. Wheel and sdist provenance verification enforced
> the repository, signer workflow, exact tag ref, and exact source digest. The v0.3.0 PNG is a
> hash-verified release asset only: its SLSA-provenance lookup returned HTTP 404. No clean-environment
> install verification was found for 0.2.0, 0.3.0, or 0.4.0. GitHub comparisons reported all three
> tag commits `diverged` from current `main`, so these historical facts assert no tag ancestry and
> authorize no tag, release, replacement asset, or publication.

## Release authorization

Rows are append-only. A `Ready` row may be added only after a clean `make check` on the exact source
commit recorded in that row. The release metadata commit containing the row may change only this
ledger and `CHANGELOG.md` relative to that source commit. `Ready` authorizes creating the
corresponding tag from the metadata commit; it is not evidence that a tag, artifact, GitHub release,
or package publication exists.

| Version | Date | Validated source commit | Full gate evidence | Status |
|---|---|---|---|---|
| 0.2.0 | 2026-08-03 | `0cdb1fac7c16c2ccce72c8d1777c3f68d48d3bb1` | Clean `make check` on this exact commit with Python 3.12.13 and KiCad 10.0.5: Ruff lint and format, version, ledger, audio, and Circuit Intent checkers, strict mypy across 39 source files also clean under mypy 2.3.0, 494 tests plus 29 subtests including all 14 real-KiCad DRC nodes, secret scan, `pip-audit` with no known vulnerabilities, and the isolated sdist and wheel build; hosted PR #34 checks (CI on Python 3.11-3.13, CodeQL, dependency review, dependency and secret audit) green at this head | Superseded |
| 0.2.0 | 2026-08-03 | `ccaa2b07ceade59e04299a5f62dc8af80afcf5fd` | Clean `make check` on this exact commit with Python 3.12.13 and KiCad 10.0.5: Ruff lint and format, version, ledger, audio, and Circuit Intent checkers, strict mypy across 39 source files also clean under mypy 2.3.0, 494 tests plus 29 subtests including all 14 real-KiCad DRC nodes and the reworked state-independent tag-gate regression, secret scan, `pip-audit` with no known vulnerabilities, and the isolated sdist and wheel build | Ready |
| 0.3.0 | 2026-08-04 | `e90ac77abcac53997b50f889474fedacdfe78010` | Clean `make check` on this exact commit with Python 3.12.13, mypy 2.3.0, and KiCad 10.0.5: Ruff lint and format, version, ledger, audio, and Circuit Intent checkers, strict mypy across 40 source files, 611 tests plus 29 subtests and one environment skip including all 23 real-KiCad nodes, secret scan, `pip-audit` with no known vulnerabilities, and the isolated 0.3.0 sdist and wheel build; hosted PR #38 checks green at this head; one earlier full-suite run saw a single transient failure of the MCP artifact-retrieval test that passed in isolation and in two subsequent full runs, recorded here as a known flake to diagnose rather than hidden | Ready |
| 0.4.0 | 2026-08-04 | `2e02394990f146600c01e0418b891a3a9ce78fd6` | Clean `make check` on this exact commit with Python 3.12.13, mypy 2.3.0, and KiCad 10.0.5: Ruff lint and format, version, ledger, audio, and Circuit Intent checkers, strict mypy across 49 source files, 757 tests plus 200 subtests and one environment skip including all real-KiCad nodes, secret scan, `pip-audit` with no known vulnerabilities, and the isolated 0.4.0 sdist and wheel build; hosted PR #44 checks green at this head | Ready |
| 0.5.0 | 2026-08-05 | `e679b1c61e676812f5565a422f71f0cf6e042cab` | Preparation: version, ledger, audio-benchmark, and Circuit Intent checks; Ruff; strict mypy across 87 source files; and secret scan passed. Targeted held-out-audio provenance tests (12) and NE5532 benchmark tests (17; the real-KiCad observation excluded) passed. `make check` is not clean: `tests/test_apply_engine.py::RealKiCadTests::test_the_applied_board_opens_and_the_net_becomes_connected` and `tests/test_apply_service.py::RealKiCadTests::test_the_applied_board_opens_and_the_net_becomes_connected` each received KiCad CLI exit `-6`; `pip-audit` and isolated `python -m build` could not resolve PyPI. | Blocked |
| 0.5.0 | 2026-08-05 | `f62ea5abb91e5977637131d54cf56ff652fe83aa` | Hosted PR #56 validated this exact source commit: CI run 31003667936 passed Python 3.11, 3.12, and 3.13 with formatting, Ruff, strict mypy, the full unit suite, metadata and ledger checks, repository secret scan, and isolated wheel/sdist builds with uploaded artifacts; CodeQL run 31003667722 and its analysis check passed; Security run 31003667587 passed the dependency and secret audit plus dependency review. The earlier local macOS KiCad `-6` and PyPI-resolution failures remain preserved in the preceding Blocked row and are superseded only by these exact-head hosted gates. | Ready |
| 0.5.0 | 2026-08-05 | `0dfd8adfe2fb343f79579eed0241834b8864fdce` | Exact-source push [CI run 31008856276](https://github.com/seunghyukchoe/copper-mcp/actions/runs/31008856276) passed Python 3.11, 3.12, and 3.13 with the full test suite, metadata and ledger checks, and repository secret scan; its Python 3.12 job also built and uploaded the wheel and sdist. [CodeQL run 31008856286](https://github.com/seunghyukchoe/copper-mcp/actions/runs/31008856286), [Security run 31008856291](https://github.com/seunghyukchoe/copper-mcp/actions/runs/31008856291), and [OpenSSF Scorecard run 31008856328](https://github.com/seunghyukchoe/copper-mcp/actions/runs/31008856328) passed for the same exact source. | Ready |
| 0.6.0 | 2026-08-06 | `0304d83376a867bb245bd0980b9e1d0a74ad581e` | Preparation on this exact commit with Python 3.12.13, mypy 2.3.0, Ruff 0.16.1, and KiCad 10.0.5: Ruff lint and format clean over 381 files; strict mypy clean across 90 source files; 1,647 tests passed, 1 skipped, 445 subtests passed out of 1,648 collected, including the real-KiCad nodes; version, ledger, ADR-number, doc-link, audio-benchmark, Circuit Intent, and secret checks passed; and the isolated 0.6.0 sdist and wheel build succeeded. `make check` is nonetheless not clean here: `python -m pip_audit .` cannot run in this environment — its isolated build environment aborts in `ensurepip` with `SIGABRT` — so the dependency audit is unperformed locally and is deferred to the hosted Security workflow. | Blocked |
| 0.6.0 | 2026-08-06 | `b80f0d4ff0192e934c8dfad58ed36de9010e86c7` | Exact-source `workflow_dispatch` [CI run 31052112078](https://github.com/seunghyukchoe/copper-mcp/actions/runs/31052112078) passed Python 3.11, 3.12, and 3.13 at this head: formatting across 381 files, Ruff, strict mypy across 90 source files, 1,590 tests passed with 58 environment skips (the real-KiCad nodes, which have no KiCad on a hosted runner and did run locally), version, ledger, ADR-number, doc-link, and secret checks, and the Python 3.12 job's isolated `copper_mcp-0.6.0` wheel and sdist build with uploaded artifacts. Exact-source [Security run 31052113554](https://github.com/seunghyukchoe/copper-mcp/actions/runs/31052113554) passed the dependency and secret audit, which supplies the `python -m pip_audit .` that the preceding `Blocked` row could not run locally. [CodeQL run 31052107958](https://github.com/seunghyukchoe/copper-mcp/actions/runs/31052107958) and its analysis check passed for pull request #93 at this head, as did the pull-request [Security run 31052107981](https://github.com/seunghyukchoe/copper-mcp/actions/runs/31052107981) with dependency review and the pull-request [CI run 31052107772](https://github.com/seunghyukchoe/copper-mcp/actions/runs/31052107772) on all three Python versions. OpenSSF Scorecard has no `workflow_dispatch` trigger and runs only on pushes to `main`, so no Scorecard evidence is claimed for this commit. The preceding `Blocked` row's local `pip-audit` gap is superseded only by these exact-head hosted gates. | Ready |

> **v0.5.0 authorization correction — 2026-08-05:** PR #56's rebase merge orphaned the
> previously recorded validated source `f62ea5abb91e5977637131d54cf56ff652fe83aa` from current
> `main`. Under the append-only latest-row semantics, the new `0dfd8adfe2fb343f79579eed0241834b8864fdce`
> `Ready` row supersedes that authorization without rewriting it; the earlier `Blocked` and `Ready`
> rows remain audit history. No v0.5.0 tag or release exists yet.

> **v0.4.0 authorization reconciliation — 2026-08-05:** The `0.4.0` row above is restored
> verbatim from [`6304d777`](https://github.com/seunghyukchoe/copper-mcp/commit/6304d777595499ec60c6a8ca4c608354dd1d9753), where it existed before the tag and identified
> validated source `2e02394990f146600c01e0418b891a3a9ce78fd6`. This append-only amendment repairs a
> divergent-history omission; it is not a newly issued authorization, a rerun of the original
> validation, proof that the tag commit is an ancestor of current `main`, or authorization for
> v0.5.0.

> **Correction — 2026-08-03:** The first `0.2.0` row's status was changed from `Ready` to
> `Superseded` because the release run for the tag created from its metadata commit failed:
> `tests/test_version.py::test_tag_gate_refuses_unreleased_v0_2_0` asserted that `0.2.0` is
> unreleased, so the authorization itself flipped the test inside the release gate. The unpublished
> `v0.2.0` tag was deleted before any artifact or release existed, the regression was reworked to
> pin the state-independent mismatched-tag refusal, and the second row re-authorizes `0.2.0` on the
> reworked source commit. No released asset was replaced.

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

> **Draft-PR hosted-gate correction — 2026-08-03:** GitHub commit
> [`dae0e90`](https://github.com/seunghyukchoe/copper-mcp/commit/dae0e90d8379a1cb82af08a5836a57cfaf4d565f)
> has tree `e196836314569674d1f1bc363c9aeaf522625f08`, matching the final formatted local
> working tree. Draft PR [#34](https://github.com/seunghyukchoe/copper-mcp/pull/34) then passed
> [CI run 30799036239](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30799036239)
> across Python 3.11, 3.12, and 3.13, including the isolated package build and temporary workflow
> artifact; [Security run
> 30799036250](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30799036250) passed the
> dependency and secret audits; and [CodeQL run
> 30799037008](https://github.com/seunghyukchoe/copper-mcp/actions/runs/30799037008) passed. These
> hosted gates supersede the local network/build blocker as PR evidence, but the local managed
> checkout remains metadata-write-restricted. The temporary CI artifact is not a release artifact,
> and no `Ready` authorization, version tag, GitHub release, or package publication exists.

> **Unrestricted desktop gate closure — 2026-08-03:** On the unrestricted desktop checkout at PR
> [#34](https://github.com/seunghyukchoe/copper-mcp/pull/34) head
> [`169add2`](https://github.com/seunghyukchoe/copper-mcp/commit/169add2db175b7fa2219fc892b8242f31a0bef39)
> (working tree byte-identical to that commit, tree `8075288d6249ab744919fe333e4398ca6d264d6e`),
> every previously outstanding external gate passed locally with Python 3.12.13 and KiCad 10.0.5:
> the full pytest suite reported 389 passed plus 26 subtests including all real-KiCad DRC nodes;
> Ruff, strict mypy across 39 source files, the version, ledger, audio, and Circuit Intent
> checkers, and the secret scan passed; `pip-audit` resolved PyPI and found no known
> vulnerabilities; and `python -m build` produced the `0.2.0` wheel and sdist. The eight hosted PR
> checks (CI 3.11/3.12/3.13, CodeQL ×2, dependency review, dependency and secret audit, label)
> were re-confirmed green at this head. Live end-to-end verification exercised `board-ir`,
> `preview-route --drc` (routed, with digest-bound authoritative KiCad DRC evidence), and
> `render-schematic` (deterministic artifact digest matching the committed fixture digest). This
> closes the readiness row's outstanding gates as evidence only; no `Ready` authorization row, tag,
> or publication is added here.
