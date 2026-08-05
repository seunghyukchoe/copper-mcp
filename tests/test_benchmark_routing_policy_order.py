from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from copper_mcp.routing import NegotiatedRoutingResult, NegotiatedRoutingStatus, RouteConnection
from scripts import benchmark_routing_policy_order as benchmark

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "benchmarks" / "results" / "routing" / "2026-08-05-routing-policy-order.json"
PUBLIC_POLICY_PROVENANCE_DOCUMENTS = (
    ROOT / "docs" / "adr" / "0064-policy-bound-initial-negotiated-order.md",
    ROOT / "docs" / "research" / "ai-routing-policy-boundary.md",
)
TEST_EVIDENCE_HARNESS_COMMIT = "f" * 40


def test_policy_order_report_has_complete_candidate_only_schema() -> None:
    report = benchmark.build_report(evidence_harness_commit=TEST_EVIDENCE_HARNESS_COMMIT)

    assert report["schema"] == "copper-mcp/benchmark/routing-policy-order/v1"
    assert report["implementation_commit"] == "cde2f9adc3a6436dbe99a20a12946cc70616f232"
    assert report["typed_harness_base_commit"] == "62570d5bcbe4d812028f77380cef8230241a1785"
    assert report["evidence_harness_commit"] == TEST_EVIDENCE_HARNESS_COMMIT
    assert report["evidence_harness_command"] == (
        "PYTHONPATH=src python3 scripts/benchmark_routing_policy_order.py --replays 10 "
        f"--evidence-harness-commit {TEST_EVIDENCE_HARNESS_COMMIT} "
        "--output benchmarks/results/routing/2026-08-05-routing-policy-order.json"
    )
    assert report["policy_profile"] == "deterministic-reference-v1"
    assert report["policy_id"] == "deterministic-routing-policy-v1"
    assert report["kicad_drc"] == "not_run"
    assert report["apply"] == "not_invoked"
    assert [case["fixture"] for case in report["cases"]] == [
        "crossing-neutral-control",
        "asymmetric-primary",
        "independent-control",
    ]
    for case in report["cases"]:
        baseline = case["baseline"]
        profile = case["profile"]
        assert case["replay_count"] >= 10
        assert case["replay_deterministic"] is True
        assert baseline["evidence"] is None
        assert profile["evidence"]["policy_id"] == report["policy_id"]
        assert baseline["geometry"]["sha256"].startswith("sha256:")
        assert profile["geometry"]["sha256"].startswith("sha256:")
        assert isinstance(baseline["overflow_resources"], list)
        assert isinstance(profile["observed_iteration_orders"], list)


def test_public_policy_provenance_uses_the_artifact_harness_key() -> None:
    for document in PUBLIC_POLICY_PROVENANCE_DOCUMENTS:
        contents = document.read_text(encoding="utf-8")

        assert "`evidence_harness_commit`" in contents
        assert "`evidence_harness_command`" in contents
        assert "materialization commit" in contents
        assert "evidence_source_commit" not in contents


def test_policy_order_harness_resolves_a_symlinked_worktree_path(tmp_path: Path) -> None:
    linked_root = tmp_path / "linked-worktree"
    linked_root.symlink_to(ROOT, target_is_directory=True)
    linked_script = linked_root / "scripts" / "benchmark_routing_policy_order.py"
    module_name = "linked_policy_order_benchmark"
    spec = importlib.util.spec_from_file_location(module_name, linked_script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)

    assert module.ROOT == ROOT
    assert module.SCRIPT_PATH == Path("scripts/benchmark_routing_policy_order.py")


def test_policy_order_report_uses_an_absolute_script_path_when_cwd_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    report = benchmark.build_report(evidence_harness_commit=TEST_EVIDENCE_HARNESS_COMMIT)

    assert report["script"] == "scripts/benchmark_routing_policy_order.py"
    assert report["script_sha256"] == hashlib.sha256(benchmark.SCRIPT_FILE.read_bytes()).hexdigest()


def test_policy_order_geometry_serializes_real_connection_evidence() -> None:
    revision = "sha256:" + "a" * 64
    connection = RouteConnection(
        base_revision=revision,
        start_pad_id="pad:alpha:start",
        end_pad_id="pad:alpha:end",
        attachment_segments=1,
        component_objects=3,
        obstacle_checks=7,
    )
    result = NegotiatedRoutingResult(
        status=NegotiatedRoutingStatus.COMPLETED,
        board_revision=revision,
        connections=(connection,),
    )

    geometry = benchmark._geometry(result)

    assert geometry["value"]["connections"] == [
        {
            "attachment_segments": 1,
            "component_objects": 3,
            "end_pad_id": "pad:alpha:end",
            "fill_polygons": 0,
            "obstacle_checks": 7,
            "pad_count": 2,
            "start_pad_id": "pad:alpha:start",
            "vias": 0,
        }
    ]


def test_policy_order_report_run_id_and_replays_are_deterministic() -> None:
    first = benchmark.build_report(evidence_harness_commit=TEST_EVIDENCE_HARNESS_COMMIT)
    second = benchmark.build_report(evidence_harness_commit=TEST_EVIDENCE_HARNESS_COMMIT)
    canonical = dict(first)
    run_id = canonical.pop("run_id")

    assert first == second
    assert (
        run_id
        == "sha256:"
        + hashlib.sha256(
            json.dumps(canonical, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_policy_order_claim_is_truthfully_classified_from_measurements() -> None:
    report = benchmark.build_report(evidence_harness_commit=TEST_EVIDENCE_HARNESS_COMMIT)
    claim = report["claim"]
    primary = next(case for case in report["cases"] if case["fixture"] == "asymmetric-primary")
    control = next(case for case in report["cases"] if case["fixture"] == "independent-control")

    assert claim["classification"] == "order-effect/no quality claim"
    assert claim["quality_claim"] is False
    assert primary["profile"]["total_wire_length_nm"] > primary["baseline"]["total_wire_length_nm"]
    assert control["profile"]["total_wire_length_nm"] > control["baseline"]["total_wire_length_nm"]


def test_policy_order_benchmark_enforces_a_bounded_replay_count() -> None:
    with pytest.raises(ValueError, match="replays"):
        benchmark.build_report(
            replays=benchmark.REPLAY_MINIMUM - 1,
            evidence_harness_commit=TEST_EVIDENCE_HARNESS_COMMIT,
        )
    with pytest.raises(ValueError, match="replays"):
        benchmark.build_report(
            replays=benchmark.REPLAY_MAXIMUM + 1,
            evidence_harness_commit=TEST_EVIDENCE_HARNESS_COMMIT,
        )

    report = benchmark.build_report(
        replays=benchmark.REPLAY_MINIMUM, evidence_harness_commit=TEST_EVIDENCE_HARNESS_COMMIT
    )
    for case in report["cases"]:
        for run_name in ("baseline", "profile"):
            run = case[run_name]
            assert run["iterations"] <= 4
            assert run["router_call_count"] <= 4 * 2


def test_committed_policy_order_artifact_matches_the_replay_harness() -> None:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    harness_commit = artifact["evidence_harness_commit"]
    assert isinstance(harness_commit, str)
    benchmark._validate_evidence_harness_commit(harness_commit)
    git_executable = shutil.which("git")
    assert git_executable is not None
    source_at_harness_commit = subprocess.run(  # noqa: S603 - the commit is validated above.
        [
            git_executable,
            "-C",
            str(ROOT),
            "show",
            f"{harness_commit}:scripts/benchmark_routing_policy_order.py",
        ],
        check=True,
        capture_output=True,
    ).stdout

    assert hashlib.sha256(source_at_harness_commit).hexdigest() == artifact["script_sha256"]
    current_script_sha256 = hashlib.sha256(benchmark.SCRIPT_FILE.read_bytes()).hexdigest()
    assert artifact["script_sha256"] == current_script_sha256
    assert artifact["evidence_harness_command"] == benchmark._evidence_harness_command(
        replays=artifact["replay_minimum"], harness_commit=harness_commit
    )
    assert artifact == benchmark.build_report(evidence_harness_commit=harness_commit)
