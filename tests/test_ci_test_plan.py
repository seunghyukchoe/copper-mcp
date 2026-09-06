from __future__ import annotations

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


def test_invalid_or_missing_git_revisions_fail_closed_before_running_git() -> None:
    assert changed_files(base_sha="not-a-sha", head_sha="0" * 40) is None
    assert changed_files(base_sha="", head_sha="0" * 40) is None


def test_main_and_nightly_use_canonical_evidence_unless_the_diff_is_sensitive() -> None:
    for event_name in ("push", "schedule", "workflow_dispatch"):
        normal = make_plan(event_name=event_name, changed_files=("tests/test_cli.py",))
        assert normal.regular_marker == FULL_MARKER
        assert normal.evidence_pythons == (CANONICAL_PYTHON,)

        sensitive = make_plan(event_name=event_name, changed_files=("src/copper_mcp/cli.py",))
        assert sensitive.evidence_pythons == ALL_PYTHONS
