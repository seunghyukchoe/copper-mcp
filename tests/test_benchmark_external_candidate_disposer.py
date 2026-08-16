"""B-088 external disposer corpus evidence and self-digest tests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from scripts import benchmark_external_candidate_disposer as benchmark

ARTIFACT = benchmark.DEFAULT_OUTPUT


def _artifact() -> dict[str, Any]:
    document = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key in value} | set().union(*(_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value))
    return set()


def test_artifact_validates_against_its_own_digest() -> None:
    report = _artifact()
    recorded = report.pop("run_id")
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    assert recorded == "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_artifact_meets_both_halves_of_the_predeclared_gate() -> None:
    metrics = _artifact()["metrics"]

    assert metrics["unperturbed"]["offered"] == 70
    assert metrics["unperturbed"]["accepted"] == 70
    assert len(metrics["unperturbed"]["cases"]) == 70
    assert {item["result"]["status"] for item in metrics["unperturbed"]["cases"]} == {"accepted"}
    assert {item["class"]: item["result"]["code"] for item in metrics["perturbations"]} == {
        "one_nm_obstacle_incursion": "obstacle_violation",
        "dropped_middle_segment": "discontinuous_path",
        "wrong_pad_endpoint": "endpoint_mismatch",
        "undeclared_layer_via": "undeclared_layer",
    }
    assert metrics["physical_validation"] == "not_run"


def test_artifact_records_digests_and_results_without_geometry() -> None:
    report = _artifact()
    keys = _keys(report)
    bound_files = {
        name: "sha256:" + hashlib.sha256((benchmark.ROOT / name).read_bytes()).hexdigest()
        for name in benchmark.BOUND_FILES
    }

    assert report["configuration"]["source_benchmark_run_id"].startswith("sha256:")
    assert report["configuration"]["bound_file_sha256"] == bound_files
    assert "input_set_digest" in keys
    assert "result_set_digest" in keys
    assert not ({"x_nm", "y_nm", "segments", "paths", "vias", "apply_token"} & keys)


def test_artifact_matches_a_fresh_corpus_replay() -> None:
    assert benchmark.run_benchmark() == _artifact()["metrics"]
