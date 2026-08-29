from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts import benchmark_live_layered_route_preview as benchmark

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = (
    ROOT
    / "benchmarks"
    / "results"
    / "routing"
    / "2026-08-05-live-layered-route-preview-review-remediation.json"
)
HOSTILE_CONFIGURED_TOKEN = "HOSTILE_CONFIGURED_TOKEN_MUST_NEVER_CROSS_THE_BENCHMARK"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def test_live_layered_benchmark_executes_the_closed_read_only_contract(tmp_path: Path) -> None:
    output = tmp_path / "live-layered-route-preview.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["KICAD_API_TOKEN"] = HOSTILE_CONFIGURED_TOKEN

    completed = subprocess.run(  # noqa: S603 - fixed repository script and local interpreter
        [
            sys.executable,
            str(ROOT / "scripts" / "benchmark_live_layered_route_preview.py"),
            "--repetitions",
            "2",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    rendered = output.read_text(encoding="utf-8")
    report = json.loads(rendered)
    run_id = report.pop("run_id")
    assert run_id == "sha256:" + hashlib.sha256(_canonical_bytes(report)).hexdigest()
    assert json.loads(completed.stdout) == {**report, "run_id": run_id}

    assert report["schema"] == "copper-mcp/benchmark/live-layered-route-preview/v1"
    assert report["script"] == str(benchmark.SCRIPT_PATH)
    metrics = report["metrics"]
    assert metrics["repetitions"] == 2
    assert metrics["schema_valid_replays"] == 2
    assert metrics["deterministic_candidate_ids"] is True
    assert metrics["candidate_matches_file_oracle"] is True
    assert metrics["candidate_id"].startswith("sha256:")
    assert metrics["via_count"] == 2
    assert metrics["stale_board_refused"] is True
    assert metrics["stale_session_refused"] is True
    assert metrics["stale_snapshot_refused"] is True
    assert metrics["capture_race_refused"] is True
    assert metrics["ipc_clients_closed"] is True
    assert metrics["source_unchanged"] is True
    assert metrics["kicad_invoked"] is False
    assert metrics["drc_performed"] is False
    assert metrics["serialization_performed"] is False
    assert metrics["apply_authority"] is False
    assert metrics["real_gui_session"] is False

    public_output = rendered + completed.stdout + completed.stderr
    for private_marker in (
        HOSTILE_CONFIGURED_TOKEN,
        benchmark.OBSERVED_EDITOR_IDENTITY,
        "CopperMCP_Blocker",
        "AUDIO",
        "POWER",
        "(kicad_pcb",
    ):
        assert private_marker not in public_output


def test_live_layered_stable_metrics_match_the_review_remediation_artifact() -> None:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    expected = artifact["metrics"]

    assert benchmark._run(expected["repetitions"]) == expected
