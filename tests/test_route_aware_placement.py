"""Acceptance evidence for opt-in route-aware placement scoring."""

from __future__ import annotations

from dataclasses import replace

import pytest

from copper_mcp.placement import route_scoring
from copper_mcp.placement.contracts import finalise_candidate
from scripts import benchmark_route_aware_placement as benchmark


def test_original_audio_fixture_route_aware_selection_meets_predeclared_criterion() -> None:
    metrics = benchmark.run_benchmark(3)

    assert metrics["all_retained_candidates_legal"] is True
    assert metrics["deterministic_replays"] is True
    assert metrics["route_aware_unrouted_probes"] == 0
    assert metrics["route_aware_route_wire_length_nm"] < metrics["baseline_route_wire_length_nm"]
    assert metrics["route_length_improvement_percent"] >= 10
    assert metrics["criterion"] == {
        "metric": "independent bounded A* routed wire length",
        "minimum_improvement_percent": 10,
        "passed": True,
    }


def test_route_aware_report_is_content_addressed_for_a_fixed_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_git_commit", lambda: "test-commit")

    first = benchmark.build_report(3)
    second = benchmark.build_report(3)

    assert first == second
    assert first["run_id"].startswith("sha256:")


@pytest.mark.parametrize("binding", ("candidate_id", "base_revision", "view_revision"))
def test_route_scoring_refuses_tampered_or_stale_candidates_before_projection(
    monkeypatch: pytest.MonkeyPatch, binding: str
) -> None:
    _source, snapshot, view, intent = benchmark._fixture()
    result = benchmark.solve_placement(
        intent,
        snapshot,
        view,
        settings=benchmark.PlacementSolverSettings(
            **benchmark.SEARCH_SETTINGS,
            scoring_policy=benchmark.PlacementScoringPolicy.ROUTE_AWARE_ASTAR,
            route_probe_settings=benchmark.PROBE_SETTINGS,
        ),
    )
    assert result.ranked
    candidate = result.ranked[0].candidate
    if binding == "candidate_id":
        candidate = replace(candidate, candidate_id="sha256:" + "0" * 64)
    elif binding == "base_revision":
        candidate = finalise_candidate(replace(candidate, base_revision="sha256:" + "0" * 64))
    else:
        candidate = finalise_candidate(replace(candidate, view_revision="sha256:" + "0" * 64))
    monkeypatch.setattr(
        route_scoring,
        "project_legal_candidate_snapshot",
        lambda *_args: pytest.fail("projection must not run for an invalid candidate binding"),
    )

    with pytest.raises(route_scoring.RouteScoringError):
        route_scoring.score_route_aware_candidate(
            candidate,
            snapshot,
            view,
            settings=benchmark.PROBE_SETTINGS,
            stopped=lambda: None,
        )
