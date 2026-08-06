"""Bind the committed B-089 artifact to the harness that produced it.

Unlike B-087's artifact, this one carries wall-clock medians, which no host reproduces. The
reproduction test therefore strips every field whose name ends in the suffix the artifact itself
declares, and requires everything else to match exactly. A benchmark that could not state which of
its numbers are portable would not be evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts import benchmark_incremental_spatial_index as benchmark

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "benchmarks" / "results" / "routing" / "2026-08-06-incremental-spatial-index.json"
TEST_EVIDENCE_HARNESS_COMMIT = "f" * 40


def _artifact() -> dict[str, Any]:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def _without_host_specific(value: Any, suffix: str) -> Any:
    """Recursively drop every mapping key ending in ``suffix``, plus the harness identity."""

    if isinstance(value, dict):
        return {
            key: _without_host_specific(item, suffix)
            for key, item in value.items()
            if not key.endswith(suffix)
            and key
            not in {
                "run_id",
                "evidence_harness_commit",
                "evidence_harness_command",
                "speedup_percent",
            }
        }
    if isinstance(value, list):
        return [_without_host_specific(item, suffix) for item in value]
    return value


def test_the_committed_artifact_is_bound_to_its_own_script_and_run_identity() -> None:
    report = _artifact()
    script = ROOT / str(report["script"])

    assert report["schema"] == "copper-mcp/benchmark/incremental-spatial-index/v1"
    assert report["script_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()
    recomputed = {key: value for key, value in report.items() if key != "run_id"}
    assert report["run_id"] == benchmark._digest(recomputed)
    assert report["host_specific_field_suffix"] == "_ns"


def test_the_harness_reproduces_every_portable_measurement() -> None:
    committed = _artifact()
    fresh = benchmark.build_report(evidence_harness_commit=TEST_EVIDENCE_HARNESS_COMMIT)
    suffix = str(committed["host_specific_field_suffix"])

    assert _without_host_specific(fresh, suffix) == _without_host_specific(committed, suffix)


def test_the_artifact_records_the_invariants_the_design_rests_on() -> None:
    invariants = _artifact()["invariants"]

    assert invariants["every_retention_state_identical"] is True
    assert invariants["every_index_answer_identical"] is True
    assert invariants["every_index_query_conservative"] is True
    assert invariants["every_coordinator_replay_deterministic"] is True


def test_the_artifact_records_the_regression_that_produced_the_bare_clear_branch() -> None:
    """A ledger row that claims a regression was measured must be backed by the artifact.

    Retaining nothing is a full rip-up. The shipped implementation takes a bare clear there, so
    both sides do the same work and neither operation count moves. If a future change made that
    branch subtract net by net again, this row would stop being zero.
    """

    report = _artifact()
    zero_retention = [
        point
        for case in report["ledger_cases"]
        for point in case["retention"]
        if point["retained_nets"] == 0
    ]

    assert zero_retention, "no fixture swept the zero-retention point"
    for point in zero_retention:
        assert point["after_resource_operations"] == 0
        assert point["before_resource_operations"] == 0
        assert point["after_strategy"] == "recount-survivors"
        assert point["states_identical"] is True


def test_the_artifact_records_a_win_wherever_anything_is_retained() -> None:
    report = _artifact()
    retained_points = [
        point
        for case in report["ledger_cases"]
        for point in case["retention"]
        if point["retained_nets"] > 0
    ]

    assert retained_points
    for point in retained_points:
        # The exact, host-independent claim: the new reconstruction never performs more unit
        # operations than the one it replaces.
        assert point["after_resource_operations"] <= point["before_resource_operations"]
        assert point["states_identical"] is True


def test_the_artifact_records_the_corpus_and_the_synthetic_sweep_separately() -> None:
    fixtures = {case["fixture"] for case in _artifact()["ledger_cases"]}

    assert "synthetic/congested-channel" in fixtures
    assert any(name.startswith("synthetic/parallel-") for name in fixtures)
    assert any(name.startswith("corpus/") for name in fixtures)


def test_the_artifact_records_the_coordinator_outcome_including_the_non_convergence() -> None:
    runs = {run["plan"]: run for run in _artifact()["coordinator_runs"]}

    baseline = runs["no-plan-baseline"]
    assert baseline["status"] == "completed"
    assert (baseline["iterations"], baseline["router_calls"]) == (5, 30)

    # B-087 recorded that this rule does not converge here. It still does not; the window rule is
    # not being credited with fixing something that was never broken.
    assert runs["rip-up/conflicted-only"]["status"] == "no_path"

    windowed = runs["rip-up/conflict-window-1"]
    assert windowed["status"] == "completed"
    assert windowed["iterations"] == baseline["iterations"]
    assert windowed["total_wire_length_nm"] == baseline["total_wire_length_nm"]
    assert windowed["router_calls"] < baseline["router_calls"]

    # A wide enough window is full rip-up by another name, and the artifact must show that rather
    # than implying the rule is monotonically better.
    assert runs["rip-up/conflict-window-16"]["router_calls"] == baseline["router_calls"]

    for run in runs.values():
        assert run["replay_deterministic"] is True


def test_the_artifact_records_a_bounded_claim_and_its_non_claims() -> None:
    report = _artifact()

    assert report["claim"]["quality_claim"] is False
    assert report["kicad_drc"] == "not_run"
    assert report["apply"] == "not_invoked"
    assert (
        "the corpus boards are real but small; no scaling result is claimed from them"
        in report["non_claims"]
    )
    assert (
        "wall-clock medians are host-specific and are not portable numbers" in report["non_claims"]
    )
