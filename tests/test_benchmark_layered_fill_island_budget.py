"""B-123 layered fill-island resource calibration evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from scripts import benchmark_layered_fill_island_budget as benchmark


def _artifact() -> dict[str, Any]:
    value = json.loads(benchmark.OUTPUT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


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


def test_artifact_records_the_failed_domain_gate_and_selected_finite_cap() -> None:
    metrics = _artifact()["metrics"]
    cases = {case["name"]: case for case in metrics["cases"]}

    assert metrics["source_per_island_cap"] == 500_000
    assert metrics["selected_per_island_cap"] == 500_000
    assert metrics["gates"] == {
        "widest_recorded_corpus_island": True,
        "shipped_fill_default": True,
        "fill_domain_ceiling_single_and_split": False,
    }
    assert cases["widest_recorded_corpus_island"]["propose_status"] == "accepted"
    assert cases["shipped_fill_default"]["replay_status"] == "accepted"
    assert cases["equal_total_split"]["propose_plus_replay_ns"] > 20_000_000_000
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
