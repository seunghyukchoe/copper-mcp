from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import ci_test_plan
from scripts.ci_test_plan import (
    ALL_PYTHONS,
    CANONICAL_PYTHON,
    FAST_MARKER,
    FULL_MARKER,
    changed_files,
    make_plan,
)


def test_normal_pull_request_skips_expensive_evidence_with_an_explicit_policy() -> None:
    plan = make_plan(event_name="pull_request", changed_files=("tests/test_cli.py",))

    assert plan.regular_marker == FAST_MARKER
    assert plan.evidence_enabled is False
    assert plan.evidence_pythons == ()
    assert plan.evidence_policy == "skipped-nonsensitive-pr"


def test_sensitive_paths_and_deletions_fan_out_pull_request_evidence() -> None:
    for changed_file in (
        "src/copper_mcp/board_ir.py",
        "benchmarks/corpora/tscircuit-benchmark/manifest.json",
        ".github/workflows/ci.yml",
        "Makefile",
        "scripts/ci_test_plan.py",
        "scripts/offline_mcp_harness.py",
        "scripts/mutation_harness.py",
        "scripts/evaluate_excessive_agency.py",
        "scripts/evaluate_mcp_agency_safety.py",
        "tests/test_benchmark_routing_policy_order.py",
    ):
        plan = make_plan(event_name="pull_request", changed_files=(changed_file,))
        assert plan.evidence_enabled is True
        assert plan.evidence_pythons == ALL_PYTHONS
        assert plan.evidence_policy == "all-interpreters-sensitive-pr"


def test_unknown_diff_fails_closed_to_all_interpreters() -> None:
    plan = make_plan(event_name="pull_request", changed_files=None)

    assert plan.evidence_enabled is True
    assert plan.evidence_pythons == ALL_PYTHONS


@pytest.mark.parametrize(
    "changed_file",
    (
        "conftest.py",
        "tests/conftest.py",
        "tests/evidence/conftest.py",
        "pytest.ini",
        ".pytest.ini",
        "pytest.toml",
        ".pytest.toml",
        "tox.ini",
        "setup.cfg",
        ".coveragerc",
    ),
)
@pytest.mark.parametrize("event_name", ("pull_request", "push"))
def test_test_infrastructure_cannot_skip_all_interpreter_evidence(
    changed_file: str, event_name: str
) -> None:
    plan = make_plan(event_name=event_name, changed_files=(changed_file,))

    assert plan.evidence_enabled is True
    assert plan.evidence_pythons == ALL_PYTHONS
    assert plan.evidence_policy.startswith("all-interpreters-sensitive")


def test_invalid_or_missing_git_revisions_fail_closed_before_running_git() -> None:
    assert changed_files(base_sha="not-a-sha", head_sha="0" * 40) is None
    assert changed_files(base_sha="", head_sha="0" * 40) is None


def test_git_paths_are_nul_delimited_without_quoting_or_newline_splitting(monkeypatch) -> None:
    path = "tests/한글\nscope/conftest.py"

    def diff(arguments, **kwargs):
        assert "-z" in arguments
        return SimpleNamespace(stdout=f"{path}\0tests/test_cli.py\0")

    monkeypatch.setattr(ci_test_plan.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(ci_test_plan.subprocess, "run", diff)
    files = changed_files(base_sha="0" * 40, head_sha="1" * 40)

    assert files == (path, "tests/test_cli.py")
    assert make_plan(event_name="pull_request", changed_files=files).evidence_pythons == ALL_PYTHONS


def test_main_and_nightly_use_canonical_evidence_unless_the_diff_is_sensitive() -> None:
    for event_name in ("push", "schedule", "workflow_dispatch"):
        normal = make_plan(event_name=event_name, changed_files=("tests/test_cli.py",))
        assert normal.regular_marker == FULL_MARKER
        assert normal.evidence_pythons == (CANONICAL_PYTHON,)

        sensitive = make_plan(event_name=event_name, changed_files=("src/copper_mcp/cli.py",))
        assert sensitive.evidence_pythons == ALL_PYTHONS
