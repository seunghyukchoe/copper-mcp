from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "performance_profile_v1.py"
COMMITTED_REPORT = (
    ROOT / "benchmarks" / "results" / "performance" / "2026-08-05-performance-profile-v1.json"
)


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _run_digest(report: dict[str, object]) -> str:
    payload = dict(report)
    payload.pop("run_id")
    return _canonical_digest(payload)


def _report(tmp_path: Path) -> dict[str, object]:
    output = tmp_path / "profile.json"
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    completed = subprocess.run(  # noqa: S603 - fixed script plus pytest-owned output path
        [
            sys.executable,
            str(SCRIPT),
            "--output",
            str(output),
            "--samples",
            "2",
            "--warmups",
            "1",
        ],
        check=True,
        capture_output=True,
        cwd=ROOT,
        env=environment,
        text=True,
        timeout=120,
    )
    assert completed.stdout == output.read_text(encoding="utf-8")
    return json.loads(output.read_text(encoding="utf-8"))


def test_performance_profile_has_fixed_identity_and_three_replayable_scenarios(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)

    assert report["schema"] == "copper-mcp/performance-profile/v1"
    identity = report["identity"]
    assert isinstance(identity, dict)
    assert identity["fixed_seed"] == 23
    assert identity["measurement_configuration"] == {
        "hotspot_limit": 8,
        "samples": 2,
        "warmups": 1,
    }
    assert report["identity_digest"] == _canonical_digest(identity)
    assert report["run_id"] == _run_digest(report)
    scenarios = report["scenarios"]
    assert isinstance(scenarios, dict)
    assert set(scenarios) == {"placement", "routing", "scene"}
    for scenario in scenarios.values():
        assert isinstance(scenario, dict)
        assert len(scenario["timing_perf_counter_ns"]["samples_ns"]) == 2
        hotspots = scenario["hotspots_cumulative"]
        assert 1 <= len(hotspots) <= 8
        assert all(item["cumulative_time_ns"] >= 0 for item in hotspots)


def test_performance_profile_redacts_paths_and_orders_hotspots_by_cumulative_time(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    rendered = json.dumps(report, sort_keys=True)

    assert str(ROOT) not in rendered
    assert str(tmp_path) not in rendered
    scenarios = report["scenarios"]
    assert isinstance(scenarios, dict)
    for scenario in scenarios.values():
        assert isinstance(scenario, dict)
        hotspots = scenario["hotspots_cumulative"]
        assert isinstance(hotspots, list)
        ranking = [
            (-item["cumulative_time_ns"], -item["self_time_ns"], item["function"])
            for item in hotspots
        ]
        assert ranking == sorted(ranking)
        assert all(
            "/" not in item["function"] and "\\" not in item["function"] for item in hotspots
        )


def test_committed_performance_profile_keeps_provenance_outside_deterministic_identity() -> None:
    report = json.loads(COMMITTED_REPORT.read_text(encoding="utf-8"))

    assert report["identity_digest"] == _canonical_digest(report["identity"])
    assert report["run_id"] == _run_digest(report)
    assert report["identity"]["measurement_configuration"] == {
        "hotspot_limit": 8,
        "samples": 5,
        "warmups": 2,
    }
    assert len(report["provenance"]["git_head"]) == 40
    rendered = json.dumps(report, sort_keys=True)
    assert str(ROOT) not in rendered
    for scenario in report["scenarios"].values():
        assert all("/" not in item["function"] for item in scenario["hotspots_cumulative"])
