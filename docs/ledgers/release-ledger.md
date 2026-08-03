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
| 0.2.0 | 2026-08-03 | `0cdb1fac7c16c2ccce72c8d1777c3f68d48d3bb1` | Clean `make check` on this exact commit with Python 3.12.13 and KiCad 10.0.5: Ruff lint and format, version, ledger, audio, and Circuit Intent checkers, strict mypy across 39 source files also clean under mypy 2.3.0, 494 tests plus 29 subtests including all 14 real-KiCad DRC nodes, secret scan, `pip-audit` with no known vulnerabilities, and the isolated sdist and wheel build; hosted PR #34 checks (CI on Python 3.11-3.13, CodeQL, dependency review, dependency and secret audit) green at this head | Superseded |
| 0.2.0 | 2026-08-03 | `ccaa2b07ceade59e04299a5f62dc8af80afcf5fd` | Clean `make check` on this exact commit with Python 3.12.13 and KiCad 10.0.5: Ruff lint and format, version, ledger, audio, and Circuit Intent checkers, strict mypy across 39 source files also clean under mypy 2.3.0, 494 tests plus 29 subtests including all 14 real-KiCad DRC nodes and the reworked state-independent tag-gate regression, secret scan, `pip-audit` with no known vulnerabilities, and the isolated sdist and wheel build | Ready |
| 0.3.0 | 2026-08-04 | `e90ac77abcac53997b50f889474fedacdfe78010` | Clean `make check` on this exact commit with Python 3.12.13, mypy 2.3.0, and KiCad 10.0.5: Ruff lint and format, version, ledger, audio, and Circuit Intent checkers, strict mypy across 40 source files, 611 tests plus 29 subtests and one environment skip including all 23 real-KiCad nodes, secret scan, `pip-audit` with no known vulnerabilities, and the isolated 0.3.0 sdist and wheel build; hosted PR #38 checks green at this head; one earlier full-suite run saw a single transient failure of the MCP artifact-retrieval test that passed in isolation and in two subsequent full runs, recorded here as a known flake to diagnose rather than hidden | Ready |
| 0.4.0 | 2026-08-04 | `2e02394990f146600c01e0418b891a3a9ce78fd6` | Clean `make check` on this exact commit with Python 3.12.13, mypy 2.3.0, and KiCad 10.0.5: Ruff lint and format, version, ledger, audio, and Circuit Intent checkers, strict mypy across 49 source files, 757 tests plus 200 subtests and one environment skip including all real-KiCad nodes, secret scan, `pip-audit` with no known vulnerabilities, and the isolated 0.4.0 sdist and wheel build; hosted PR #44 checks green at this head | Ready |

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
