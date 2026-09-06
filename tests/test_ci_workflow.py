"""Whole-workflow compiler checks for CI wiring and artifact reuse."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
LINT_SCRIPTS = frozenset(
    {
        "check_version.py",
        "check_ledgers.py",
        "check_adr_numbers.py",
        "check_doc_links.py",
        "check_sdist_tracked.py",
        "check_schema_sets.py",
        "check_drc_comparability.py",
        "check_ci_budgets.py",
        "check_audio_benchmarks.py",
        "check_circuit_intents.py",
    }
)


def _workflow(path: Path) -> dict[str, object]:
    yaml = pytest.importorskip("yaml")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _job(document: dict[str, object], name: str) -> dict[str, object]:
    jobs = document["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[name]
    assert isinstance(job, dict)
    return job


def _runs(job: dict[str, object]) -> str:
    steps = job["steps"]
    assert isinstance(steps, list)
    return "\n".join(str(step.get("run", "")) for step in steps if isinstance(step, dict))


def test_ci_compiles_into_the_four_acceleration_jobs() -> None:
    document = _workflow(CI)
    assert set(document["jobs"]) == {"quality", "compatibility", "evidence", "package"}


def test_ruff_version_is_identical_for_development_ci_and_commit_hooks() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "ruff==0.16.6" in project["project"]["optional-dependencies"]["dev"]
    assert project["tool"]["ruff"]["required-version"] == "==0.16.6"
    config = _workflow(ROOT / ".pre-commit-config.yaml")
    hooks = [repo for repo in config["repos"] if repo["repo"].endswith("/ruff-pre-commit")]
    assert len(hooks) == 1
    assert hooks[0]["rev"] == "321478e58f4938179c6b86e4ddfa923d1547a49b"


def test_pytest_registers_every_declared_evidence_marker() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = document["tool"]["pytest"]["ini_options"]["markers"]
    assert {entry.split(":", 1)[0] for entry in markers} >= {
        "slow_evidence",
        "real_kicad",
        "external_router",
        "networked_provider",
    }


def test_make_targets_preserve_fast_full_compat_and_evidence_contracts() -> None:
    assert "test-fast:" in MAKEFILE and "-n 4 --dist loadfile --no-cov" in MAKEFILE
    assert "test-full:\n\tPYTHONPATH=src $(PYTHON) -m pytest" in MAKEFILE
    assert "test:\n\tPYTHONPATH=src $(PYTHON) -m pytest" in MAKEFILE
    assert "COVERAGE_ARGS ?= --no-cov" in MAKEFILE
    assert "test-evidence:" in MAKEFILE and "--no-cov" in MAKEFILE


def test_quality_runs_every_make_lint_checker_and_commit_range_gate() -> None:
    document = _workflow(CI)
    quality = _job(document, "quality")
    quality_runs = _runs(quality)
    lint = MAKEFILE.split("lint:", 1)[1].split("\n\n", 1)[0]
    in_lint = {
        line.split("scripts/", 1)[1].split()[0] for line in lint.splitlines() if "scripts/" in line
    }
    assert in_lint == set(LINT_SCRIPTS)
    for script in LINT_SCRIPTS:
        assert f"scripts/{script}" in quality_runs
    assert "scripts/check_commit_message.py --range" in quality_runs
    assert "github.event.pull_request.base.sha" in str(quality)
    assert "github.event.pull_request.head.sha" in str(quality)


@pytest.mark.parametrize("script", sorted(LINT_SCRIPTS))
def test_every_repository_checker_runs_in_ci(script: str) -> None:
    assert f"scripts/{script}" in _runs(_job(_workflow(CI), "quality"))


def test_the_commit_message_checker_runs_over_the_pull_requests_own_commits() -> None:
    quality_job = _job(_workflow(CI), "quality")
    assert 'scripts/check_commit_message.py --range "${BASE_SHA}..${HEAD_SHA}"' in _runs(
        quality_job
    )
    assert "github.event.pull_request.base.sha" in str(quality_job)
    assert "github.event.pull_request.head.sha" in str(quality_job)


def test_the_lint_target_and_the_workflow_check_the_same_scripts() -> None:
    lint = MAKEFILE.split("lint:", 1)[1].split("\n\n", 1)[0]
    in_lint = {
        line.split("scripts/", 1)[1].split()[0] for line in lint.splitlines() if "scripts/" in line
    }
    assert in_lint == set(LINT_SCRIPTS)
    quality = _runs(_job(_workflow(CI), "quality"))
    for script in in_lint:
        assert f"scripts/{script}" in quality


def test_the_full_checkout_the_range_and_the_tag_reads_both_need_is_still_there() -> None:
    document = _workflow(CI)
    quality = _job(document, "quality")
    steps = quality["steps"]
    assert isinstance(steps, list) and isinstance(steps[0], dict)
    assert steps[0]["with"] == {"fetch-depth": 0}
    assert "scripts/check_commit_message.py --range" in _runs(quality)
    assert "scripts/check_schema_sets.py" in _runs(quality)


def test_compatibility_and_evidence_keep_deterministic_hypothesis_and_correct_coverage() -> None:
    document = _workflow(CI)
    compatibility = _job(document, "compatibility")
    evidence = _job(document, "evidence")
    for job in (compatibility, evidence):
        assert job["env"] == {"HYPOTHESIS_PROFILE": "deterministic-ci"}
    assert "matrix.python-version == '3.12'" in _runs(compatibility)
    assert "PYTEST_TIMING_ARGS=--durations=20" in _runs(compatibility)
    assert "$(PYTEST_TIMING_ARGS)" in MAKEFILE
    assert "--no-cov" in _runs(compatibility)
    assert "make test-evidence" in _runs(evidence)
    assert "needs" not in compatibility
    for job in (compatibility, evidence):
        steps = job["steps"]
        assert isinstance(steps, list)
        checkout = steps[0]
        assert isinstance(checkout, dict)
        assert checkout["with"] == {"fetch-depth": 0}


def test_dynamic_evidence_skips_nonsensitive_prs_and_package_accepts_only_that_skip() -> None:
    document = _workflow(CI)
    quality = _job(document, "quality")
    evidence = _job(document, "evidence")
    package = _job(document, "package")
    outputs = quality["outputs"]
    assert isinstance(outputs, dict) and {"evidence-enabled", "evidence-policy"} <= set(outputs)
    assert evidence["if"] == "needs.quality.outputs.evidence-enabled == 'true'"
    assert "needs.evidence.result == 'skipped'" in _runs(package)
    assert "needs.quality.outputs.evidence-enabled == 'false'" in _runs(package)
    assert "needs.quality.outputs.evidence-policy == 'skipped-nonsensitive-pr'" in str(
        _runs(package)
    )
    assert package["if"] == "always()"
    assert package["name"] == "Python 3.12"
    assert "format('Python {0}', matrix.python-version)" in str(
        _job(document, "compatibility")["name"]
    )
    assert package["steps"][0]["name"] == "Enforce all required roles"


def test_quality_is_the_single_builder_and_package_verifies_its_downloaded_manifest() -> None:
    document = _workflow(CI)
    quality_runs = _runs(_job(document, "quality"))
    package_runs = _runs(_job(document, "package"))
    assert quality_runs.count("python -m build") == 1
    assert quality_runs.count("python scripts/build_pcm_package.py") == 1
    assert "dist/*.metadata.json" in quality_runs
    assert "SHA256SUMS" in quality_runs
    assert "sha256sum --check dist/SHA256SUMS" in package_runs
    assert 'python-version: "3.12"' in CI.read_text(encoding="utf-8")
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in (
        CI.read_text(encoding="utf-8")
    )
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in (
        RELEASE.read_text(encoding="utf-8")
    )


def test_release_uses_strict_calibration_before_the_complete_release_gate() -> None:
    release = _workflow(RELEASE)
    assert "python scripts/check_ci_budgets.py --require-calibrated && make check" in _runs(
        _job(release, "verify")
    )


def test_classifier_command_has_no_untrusted_branch_name_shell_interpolation() -> None:
    quality_runs = _runs(_job(_workflow(CI), "quality"))
    assert "scripts/ci_test_plan.py" in quality_runs
    assert "--ref-name" not in quality_runs
