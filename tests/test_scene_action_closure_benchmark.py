from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_scene_action_closure_benchmark_records_the_mcp_oracle(tmp_path: Path) -> None:
    output = tmp_path / "scene-action.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["COPPER_MCP_TRANSPORT"] = "invalid-inherited-value"

    completed = subprocess.run(  # noqa: S603 - fixed repository script and local interpreter
        [
            sys.executable,
            str(ROOT / "scripts" / "benchmark_scene_action_closure.py"),
            "--repetitions",
            "2",
            "--warmups",
            "0",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["benchmark"] == "scene-route-referential-closure-v1"
    assert result["configuration"]["harness_helper"] == {
        "path": "scripts/offline_mcp_harness.py",
        "sha256": hashlib.sha256(
            (ROOT / "scripts/offline_mcp_harness.py").read_bytes()
        ).hexdigest(),
    }
    assert all(result["environment"]["dependencies"].values())
    schema = result["schema_evidence"]
    assert schema["candidate_closed"] is True
    assert schema["input_wrapper_closed"] is True
    assert schema["output_closed"] is True
    assert schema["output_status_variants"] == 5
    assert schema["reference_selector_revision_bound"] is True
    assert schema["selector_variants"] == 2
    assert schema["selector_variants_closed"] is True
    assert schema["all_record_objects_closed"] is True
    assert schema["closed_record_object_count"] >= 12
    assert schema["output_schema_digest"].startswith("sha256:")
    assert result["metrics"]["legacy_scene_ref_actionable"] == 0
    assert result["metrics"]["reference_actionable"] == 3
    assert result["metrics"]["hidden_name_oracle_actionable"] == 3
    assert result["metrics"]["candidate_equivalence"] == 3
    assert result["metrics"]["stale_board_revision_refusals"] == 3
    assert result["metrics"]["stale_snapshot_digest_refusals"] == 3
    assert result["metrics"]["stale_reference_refusals"] == 6
    assert result["metrics"]["persistent_workspace_changes"] == 0
    assert result["metrics"]["deterministic_scene_replays"] == 2
    assert result["metrics"]["deterministic_route_replays"] == 18
    assert result["metrics"]["structured_payload_bytes"]["reference_routes_total"] > 0
    assert all(route["candidate_equivalent"] for route in result["routes"])
