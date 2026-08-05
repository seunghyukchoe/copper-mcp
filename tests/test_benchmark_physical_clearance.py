from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import benchmark_negotiated_physical_clearance

_EVIDENCE_SOURCE_COMMIT = "5a2c1073de2a97cbb69e3a54d5077db0d4a24ba8"
_BENCHMARK_ARTIFACT = (
    Path(__file__).parents[1]
    / "benchmarks"
    / "results"
    / "routing"
    / "2026-08-05-negotiated-physical-clearance.json"
)


def test_physical_clearance_artifact_is_reproducible_and_provenance_bound() -> None:
    first = benchmark_negotiated_physical_clearance.build_report(
        evidence_source_commit=_EVIDENCE_SOURCE_COMMIT
    )
    second = benchmark_negotiated_physical_clearance.build_report(
        evidence_source_commit=_EVIDENCE_SOURCE_COMMIT
    )
    canonical = dict(first)
    run_id = canonical.pop("run_id")

    assert first == second
    assert first["evidence_source_commit"] == _EVIDENCE_SOURCE_COMMIT
    assert (
        first["implementation_commit"]
        == benchmark_negotiated_physical_clearance.IMPLEMENTATION_COMMIT
    )
    assert (
        first["script_sha256"]
        == hashlib.sha256(
            benchmark_negotiated_physical_clearance.SCRIPT_PATH.read_bytes()
        ).hexdigest()
    )
    assert (
        run_id
        == "sha256:"
        + hashlib.sha256(
            json.dumps(canonical, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert json.loads(_BENCHMARK_ARTIFACT.read_text(encoding="utf-8")) == first


def test_physical_clearance_benchmark_requires_a_lowercase_full_evidence_commit() -> None:
    with pytest.raises(ValueError, match="evidence_source_commit"):
        benchmark_negotiated_physical_clearance.build_report(evidence_source_commit="not-a-commit")
