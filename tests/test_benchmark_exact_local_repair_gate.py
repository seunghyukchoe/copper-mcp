from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import benchmark_exact_local_repair_integration_gate as benchmark

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = (
    ROOT
    / "benchmarks"
    / "results"
    / "routing"
    / "2026-08-05-exact-local-repair-gate-correction-v2.json"
)


def test_predeclared_repair_gate_is_deterministic_and_truthfully_declined() -> None:
    first = benchmark.build_report()
    second = benchmark.build_report()

    assert first == second
    assert first["replay_count"] == 10
    assert first["replay_deterministic"] is True
    assert first["gate"]["result"] == "declined"
    assert first["gate"]["transaction_admitted"] is False
    assert first["gate"]["baseline_gates_hold"] is True
    assert first["metering"]["local_repair_expanded_states"] == 0
    assert first["dataset"]["expected_snapshot_digest"] == first["snapshot_digest"]
    assert first["configuration"]["settings"]["max_grid_nodes"] == 256
    assert first["configuration"]["settings"]["max_expansions"] == 5_000
    assert first["configuration"]["settings"]["max_obstacles"] == 64
    assert first["configuration"]["settings"]["max_obstacle_checks"] == 100_000
    for replay in first["replays"]:
        assert replay["status"] == "completed"
        assert replay["overflow_units"] == 0
        assert replay["candidate_revisions_exact"] is True
        assert replay["physical_gate"]["accepted"] is True


def test_committed_repair_gate_artifact_matches_the_harness() -> None:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert artifact == benchmark.build_report()
    report = dict(artifact)
    run_id = report.pop("run_id")
    assert (
        run_id
        == "sha256:"
        + hashlib.sha256(
            json.dumps(report, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
