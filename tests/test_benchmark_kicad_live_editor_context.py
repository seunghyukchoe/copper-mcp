from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.live_editor_context import _OPTIONAL_FIELDS, _REQUIRED_FIELDS
from scripts import benchmark_kicad_live_editor_context as benchmark

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "benchmarks" / "results" / "mcp" / "2026-08-04-live-editor-context.json"
VOLATILE_METRICS = frozenset({"median_capture_latency_ns"})


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def test_editor_context_benchmark_executes_and_self_digests(tmp_path: Path) -> None:
    output = tmp_path / "live-editor-context.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")

    completed = subprocess.run(  # noqa: S603 - fixed repository script and local interpreter
        [
            sys.executable,
            str(ROOT / "scripts" / "benchmark_kicad_live_editor_context.py"),
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
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    run_id = report.pop("run_id")
    assert run_id == "sha256:" + hashlib.sha256(_canonical_bytes(report)).hexdigest()
    assert report["benchmark"] == benchmark.BENCHMARK_NAME
    assert report["metrics"]["deterministic_replays"] == 2
    assert report["metrics"]["mutating_ipc_calls"] == 0
    assert report["metrics"]["raw_editor_content_returned"] is False
    assert benchmark.SOURCE not in output.read_text(encoding="utf-8")


def test_editor_context_benchmark_sends_only_current_contract_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supported = frozenset((*_REQUIRED_FIELDS, *_OPTIONAL_FIELDS))
    seen: list[frozenset[str]] = []
    inspect = benchmark.inspect_live_editor_context_raw

    def recording_inspect(
        request: Any,
        settings: Any,
        **kwargs: Any,
    ) -> Any:
        seen.append(frozenset(request))
        return inspect(request, settings, **kwargs)

    monkeypatch.setattr(benchmark, "inspect_live_editor_context_raw", recording_inspect)
    benchmark._run(2)

    assert seen
    assert all(fields <= supported for fields in seen)
    assert all("expect_snapshot_digest" not in fields for fields in seen)


def test_editor_context_stable_metrics_match_the_historical_artifact() -> None:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    report = benchmark._run(artifact["configuration"]["repetitions"])

    assert report["benchmark"] == artifact["benchmark"]
    assert report["configuration"]["source_sha256"] == artifact["configuration"]["source_sha256"]
    expected = {
        key: value for key, value in artifact["metrics"].items() if key not in VOLATILE_METRICS
    }
    actual = {key: value for key, value in report["metrics"].items() if key not in VOLATILE_METRICS}
    assert actual == expected
