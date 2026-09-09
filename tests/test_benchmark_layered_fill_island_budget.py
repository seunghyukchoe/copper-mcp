"""B-123 layered fill-island resource calibration evidence."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from typing import Any

from scripts import benchmark_layered_fill_island_budget as benchmark


def _artifact() -> dict[str, Any]:
    value = json.loads(benchmark.OUTPUT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_original_calibration_remains_bound_to_its_original_source() -> None:
    original = json.loads(
        (
            benchmark.ROOT
            / "benchmarks/results/routing/2026-08-17-layered-fill-island-budget-v1.json"
        ).read_bytes()
    )
    recorded = original.pop("run_id")
    assert recorded == "sha256:99cc07e4047e95beb0f82be8c10da8641ed33f0ad36d87376007cb887e57d5a6"
    assert recorded == benchmark._canonical_digest(original)
    assert original["metrics"]["gates"]["fill_domain_ceiling_single_and_split"] is False
    configuration = original["configuration"]
    files = {
        **configuration["implementation_sha256"],
        str(benchmark.SCRIPT): configuration["script_sha256"],
        "scripts/benchmark_layered_fill_obstacles.py": configuration["fixture_script_sha256"],
    }
    git = shutil.which("git")
    assert git is not None
    for name, expected in files.items():
        source = subprocess.run(  # noqa: S603 - fixed commit and digest-pinned artifact paths
            [git, "show", f"ddd1819b68ac50c092b5bf9ee8f79dbb42042092:{name}"],
            cwd=benchmark.ROOT,
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
        assert "sha256:" + hashlib.sha256(source).hexdigest() == expected


def test_artifact_is_self_digested_and_binds_scripts_and_implementation() -> None:
    report = _artifact()
    recorded = report.pop("run_id")
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    assert recorded == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert report["configuration"]["script_sha256"] == (
        "sha256:" + hashlib.sha256((benchmark.ROOT / benchmark.SCRIPT).read_bytes()).hexdigest()
    )
    assert report["configuration"]["fixture_script_sha256"] == (
        "sha256:"
        + hashlib.sha256(
            (benchmark.ROOT / "scripts/benchmark_layered_fill_obstacles.py").read_bytes()
        ).hexdigest()
    )
    assert report["configuration"]["implementation_sha256"] == {
        name: "sha256:" + hashlib.sha256((benchmark.ROOT / name).read_bytes()).hexdigest()
        for name in benchmark.BOUND_IMPLEMENTATION_FILES
    }


def test_artifact_records_current_gates_without_expanding_the_selected_cap() -> None:
    metrics = _artifact()["metrics"]
    cases = {case["name"]: case for case in metrics["cases"]}

    assert metrics["source_per_island_cap"] == 500_000
    assert metrics["selected_per_island_cap"] == 500_000
    assert metrics["gates"] == {
        "widest_recorded_corpus_island": True,
        "shipped_fill_default": True,
        "fill_domain_ceiling_single_and_split": all(
            cases[name]["propose_plus_replay_ns"] <= 20_000_000_000
            and cases[name]["incremental_traced_peak_bytes"] <= 1_500_000_000
            for name in ("fill_domain_ceiling", "equal_total_split")
        ),
    }
    assert cases["widest_recorded_corpus_island"]["propose_status"] == "accepted"
    assert cases["shipped_fill_default"]["replay_status"] == "accepted"
    assert cases["selected_cap_overflow"]["propose_code"] == "invalid_request"
    assert cases["aggregate_overflow"]["propose_code"] == ("obstacle_check_budget_exceeded")


def test_artifact_keeps_performance_and_physics_claims_bounded() -> None:
    report = _artifact()

    assert report["configuration"]["kicad_invoked"] is False
    assert report["configuration"]["network_invoked"] is False
    assert report["claims"] == {
        "route_quality": "not_measured",
        "physical_validation": "not_run",
        "cross_machine_performance": "not_claimed",
    }


def test_process_max_rss_is_normalized_to_bytes() -> None:
    assert benchmark._process_max_rss_bytes(123, platform_name="darwin") == 123
    assert benchmark._process_max_rss_bytes(123, platform_name="linux") == 123 * 1_024


def test_widest_recorded_island_replays_under_the_selected_cap() -> None:
    result = benchmark._worker(
        "widest_recorded_corpus_island",
        43_889,
        1,
        benchmark.SELECTED_CAP,
    )

    assert result["propose_status"] == "accepted"
    assert result["replay_status"] == "accepted"
    assert result["replay_identity_matches"] is True
