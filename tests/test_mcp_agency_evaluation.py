from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import evaluate_mcp_agency_safety as evaluation

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIRECTORY = ROOT / "tests" / "fixtures" / "security" / "mcp-agency-v1"
ARTIFACT = ROOT / "benchmarks" / "results" / "security" / "2026-08-05-mcp-agency-evaluation.json"
CANARY = "CANARY"
TEST_HARNESS_COMMIT = "a" * 40


def test_agency_evaluation_runs_all_predeclared_cases_without_disclosure() -> None:
    report = evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)

    assert report["schema"] == "copper-mcp/security-evaluation/mcp-agency/v1"
    assert report["execution"] == {
        "network": "not_invoked",
        "model": "not_invoked",
        "kicad": "not_invoked",
        "apply": "not_invoked",
        "workspace": "temporary-and-unchanged",
    }
    assert report["counts"] == {
        "attempted": 7,
        "blocked": 7,
        "refused": 4,
        "contained": 3,
        "leaked": 0,
    }
    assert {case["id"] for case in report["cases"]} == {
        "board-text-prompt-injection",
        "model-supplied-external-capabilities",
        "apply-without-capability",
        "stale-revision",
        "resource-exhaustion",
        "data-exfiltration-or-log-leakage",
        "cross-tool-capability-chaining",
    }
    assert CANARY not in json.dumps(report, ensure_ascii=False, sort_keys=True)


def test_agency_evaluation_run_id_is_deterministic() -> None:
    first = evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)
    second = evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)

    assert first == second
    canonical = dict(first)
    run_id = canonical.pop("run_id")
    serialized = json.dumps(
        canonical,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert run_id == "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def test_agency_evaluation_validates_evidence_commit_shape() -> None:
    with pytest.raises(evaluation.EvaluationError, match="40 lowercase"):
        evaluation.build_report(evidence_harness_commit="INVALID")


def test_agency_evaluation_cli_ignores_inherited_copper_configuration(tmp_path: Path) -> None:
    output = tmp_path / "agency-evaluation.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["COPPER_MCP_TRANSPORT"] = "invalid-inherited-value"

    completed = subprocess.run(  # noqa: S603 - fixed local script and interpreter
        [
            sys.executable,
            str(ROOT / "scripts" / "evaluate_mcp_agency_safety.py"),
            "--evidence-harness-commit",
            TEST_HARNESS_COMMIT,
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
    assert completed.stdout == ""
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report == evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)
    assert CANARY not in output.read_text(encoding="utf-8")


def test_committed_agency_artifact_replays_from_its_recorded_harness() -> None:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    harness_commit = artifact["evidence_harness_commit"]
    assert isinstance(harness_commit, str)
    script_sha256 = hashlib.sha256(evaluation.SCRIPT_FILE.read_bytes()).hexdigest()
    assert artifact["script_sha256"] == script_sha256
    assert artifact == evaluation.build_report(evidence_harness_commit=harness_commit)
    assert CANARY not in ARTIFACT.read_text(encoding="utf-8")


def test_hostile_fixtures_hold_only_declared_evaluation_inputs() -> None:
    catalog = json.loads((FIXTURE_DIRECTORY / "threat-cases.json").read_text(encoding="utf-8"))
    assert catalog["canary"] == CANARY
    for name in ("hostile-route-extras.json", "hostile-placement-extras.json"):
        extras = json.loads((FIXTURE_DIRECTORY / name).read_text(encoding="utf-8"))
        assert set(extras) == {
            "callback_url",
            "model_api_token",
            "policy_geometry",
            "workspace_path",
        }
        assert CANARY in json.dumps(extras, ensure_ascii=False)
