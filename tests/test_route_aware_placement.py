"""Acceptance evidence for opt-in route-aware placement scoring."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from copper_mcp.board_ir import PointNM
from copper_mcp.placement import route_scoring
from copper_mcp.placement.contracts import finalise_candidate
from copper_mcp.placement.solver import (
    PlacementScoringPolicy,
    PlacementSolverSettings,
    score_placement_candidate,
    solve_placement,
)
from scripts import benchmark_route_aware_placement as benchmark

_AWARE_SETTINGS: dict[str, Any] = {
    "scoring_policy": PlacementScoringPolicy.ROUTE_AWARE_ASTAR,
    "route_probe_settings": benchmark.PROBE_SETTINGS,
}


def _solved(*, aware: bool = True) -> tuple[Any, Any, Any]:
    """Return one solved fixture: ``(snapshot, view, result)``."""

    _source, snapshot, view, intent = benchmark._fixture()
    extra = _AWARE_SETTINGS if aware else {}
    result = solve_placement(
        intent,
        snapshot,
        view,
        settings=PlacementSolverSettings(**benchmark.SEARCH_SETTINGS, **extra),
    )
    return snapshot, view, result


def test_original_audio_fixture_route_aware_selection_meets_predeclared_criterion() -> None:
    metrics = benchmark.run_benchmark(3)
    search = metrics["search_comparison"]

    assert metrics["all_retained_candidates_legal"] is True
    assert metrics["deterministic_replays"] is True
    assert search["route_aware_unrouted_probes"] == 0
    assert search["route_aware_route_wire_length_nm"] < search["baseline_route_wire_length_nm"]
    assert search["route_length_improvement_percent"] >= 10
    assert search["probes_per_candidate"] == 1
    assert metrics["criterion"]["minimum_improvement_percent"] == 10
    assert metrics["criterion"]["passed"] is True


def test_the_two_ranked_policies_run_different_searches_not_one_shared_candidate_set() -> None:
    """F1: the score orders the beam, so the policies do not share a candidate set.

    The recorded improvement is a different-search-trajectory result.  Measured here: at the
    committed ``max_ranked`` the two retained sets are entirely disjoint.  The report must describe
    that, and must carry the separate re-ranking measurement rather than implying it.
    """

    metrics = benchmark.run_benchmark(3)
    search = metrics["search_comparison"]
    rerank = metrics["rerank_comparison"]

    assert search["shared_retained_candidates"] == 0
    assert search["baseline_retained_candidates"] == search["route_aware_retained_candidates"] == 8
    assert "not one shared candidate set re-ranked" in search["comparison_kind"]
    assert search["baseline_scoring_policy"] == "same-net-manhattan-v1"
    assert search["route_aware_scoring_policy"] == "route-aware-astar-v1"
    # The re-ranking measurement is the one that really does hold the candidate set fixed.
    assert rerank["shared_candidate_set_size"] == 16
    assert rerank["same_choice"] is False
    assert rerank["route_aware_route_wire_length_nm"] < rerank["manhattan_route_wire_length_nm"]


def test_the_report_records_the_honest_all_probeable_net_observation() -> None:
    """F4: "zero unrouted probes" was a one-net statement; record the whole-fixture one."""

    observation = benchmark.run_benchmark(3)["multi_probe_observation"]
    baseline = observation["baseline_choice"]
    aware = observation["route_aware_choice"]

    assert baseline["attempted_probes"] == aware["attempted_probes"] == 11
    assert baseline["unrouted_probes"] == aware["unrouted_probes"] == 4
    # Probing every net reverses the one-probe result.  Recording it is the point.
    assert aware["wire_length_nm"] > baseline["wire_length_nm"]


def test_a_failed_predeclared_criterion_is_recorded_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7: ADR-0067 promised a negative result would be recorded; make that true."""

    monkeypatch.setattr(benchmark, "MINIMUM_IMPROVEMENT_PERCENT", 99)

    metrics = benchmark.run_benchmark(3)

    assert metrics["criterion"]["passed"] is False
    assert metrics["criterion"]["minimum_improvement_percent"] == 99
    assert metrics["search_comparison"]["route_length_improvement_percent"] < 99


def test_the_report_binds_its_probe_configuration_into_its_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: a one-probe report and an eleven-probe report must not share an identity."""

    monkeypatch.setattr(benchmark, "_git_commit", lambda: "test-commit")
    configuration = benchmark._configuration()

    assert configuration["estimator_id"] == route_scoring.ROUTE_AWARE_ESTIMATOR_ID
    assert configuration["ranking_max_probes"] == 1
    assert (
        configuration["ranking_probe_settings_digest"]
        != configuration["observation_probe_settings_digest"]
    )


def test_route_aware_evidence_names_its_estimator_and_exact_probe_configuration() -> None:
    """F2: evidence taken under different budgets must not be indistinguishable."""

    _snapshot, _view, result = _solved()
    assert result.scoring_policy is PlacementScoringPolicy.ROUTE_AWARE_ASTAR
    evidence = result.ranked[0].route_evidence
    assert evidence is not None
    assert evidence.estimator_id == route_scoring.ROUTE_AWARE_ESTIMATOR_ID
    assert evidence.settings_digest == benchmark.PROBE_SETTINGS.digest()

    other = replace(benchmark.PROBE_SETTINGS, max_probes=11)
    assert other.digest() != benchmark.PROBE_SETTINGS.digest()
    tighter = replace(
        benchmark.PROBE_SETTINGS,
        astar_settings=replace(benchmark.ASTAR_SETTINGS, max_expansions=9_999),
    )
    assert tighter.digest() != benchmark.PROBE_SETTINGS.digest()


def test_the_default_policy_records_itself_and_carries_no_route_evidence() -> None:
    """F2: the default ranking must be identifiable as the default, not merely evidence-free."""

    _snapshot, _view, result = _solved(aware=False)

    assert result.scoring_policy is PlacementScoringPolicy.SAME_NET_MANHATTAN
    assert all(
        item.scoring_policy is PlacementScoringPolicy.SAME_NET_MANHATTAN for item in result.ranked
    )
    assert all(item.route_evidence is None for item in result.ranked)


def test_an_unprojectable_candidate_is_charged_every_probe_it_would_have_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3: reporting one failed probe is a lexicographic inversion once ``max_probes`` > 1.

    ``wire_length_nm`` is a minimize tier, so a hard-coded zero is the *best* possible value.  A
    candidate that could not be represented at all would therefore outrank one that was genuinely
    probed and tied on unrouted probes.
    """

    snapshot, view, result = _solved()
    candidate = result.ranked[0].candidate
    settings = replace(benchmark.PROBE_SETTINGS, max_probes=11, max_total_probes=128)

    def _refuse(*_args: object) -> object:
        raise ValueError("this candidate is outside the narrow projection")

    monkeypatch.setattr(route_scoring, "_project_pad", _refuse)
    evidence, status = route_scoring.score_route_aware_candidate(
        candidate, snapshot, view, settings=settings, stopped=lambda: None
    )

    assert status is None and evidence is not None
    assert evidence.attempted_probes == evidence.unrouted_probes == 11
    assert evidence.refused_probes == 11
    assert evidence.completed_probes == 0
    # The operation meter is charged for the whole share, so an unrepresentable candidate cannot
    # buy an unbounded number of cheap ranks either.
    assert evidence.operation_probes_after - evidence.operation_probes_before == 11


def test_an_unprojectable_candidate_never_outranks_a_probed_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3: the same defect, stated as the ranking outcome it produces."""

    snapshot, view, result = _solved()
    candidate = result.ranked[0].candidate
    settings = PlacementSolverSettings(
        **benchmark.SEARCH_SETTINGS,
        scoring_policy=PlacementScoringPolicy.ROUTE_AWARE_ASTAR,
        route_probe_settings=replace(benchmark.PROBE_SETTINGS, max_probes=11, max_total_probes=128),
    )
    probed, _evidence, probed_status = score_placement_candidate(
        candidate, snapshot, view, settings=settings
    )

    def _refuse(*_args: object) -> object:
        raise ValueError("this candidate is outside the narrow projection")

    monkeypatch.setattr(route_scoring, "_project_pad", _refuse)
    unprojectable, _unprojectable_evidence, unprojectable_status = score_placement_candidate(
        candidate, snapshot, view, settings=settings
    )

    assert probed_status is None and unprojectable_status is None
    assert probed is not None and unprojectable is not None
    assert probed.unrouted_probes < unprojectable.unrouted_probes
    assert unprojectable > probed


def test_probe_refusals_are_counted_apart_from_a_completed_no_path_search() -> None:
    """F5: an A* budget or grid refusal is not evidence that a placement cannot be routed.

    Measured on this fixture at the committed 1,000,000 nm probe grid, all four non-completing
    probes are ``off_grid`` refusals: the pad-centre delta is not divisible by the grid step.
    Collapsing them into a bare "unrouted" count would report a router limitation as a routability
    verdict.
    """

    snapshot, view, result = _solved()
    candidate = result.ranked[0].candidate

    evidence, status = route_scoring.score_route_aware_candidate(
        candidate,
        snapshot,
        view,
        settings=benchmark.MULTI_PROBE_SETTINGS,
        stopped=lambda: None,
    )

    assert status is None and evidence is not None
    assert evidence.unrouted_probes == 4
    assert evidence.refused_probes == 4
    assert evidence.refused_probes <= evidence.unrouted_probes


def test_structural_integrity_failures_escape_the_failed_probe_handler() -> None:
    """F9: an integrity violation must not be downgraded to "one probe happened to fail"."""

    snapshot, view, result = _solved()
    candidate = result.ranked[0].candidate
    duplicated = finalise_candidate(
        replace(
            candidate,
            placements=tuple(
                sorted(
                    (*candidate.placements, candidate.placements[0]),
                    key=lambda placement: placement.ref_id,
                )
            ),
        )
    )

    with pytest.raises(route_scoring.RouteScoringError, match="not unique"):
        route_scoring.score_route_aware_candidate(
            duplicated,
            snapshot,
            view,
            settings=benchmark.PROBE_SETTINGS,
            stopped=lambda: None,
        )


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
        "_project_footprint",
        lambda *_args: pytest.fail("placements must not be used for an invalid candidate binding"),
    )

    with pytest.raises(route_scoring.RouteScoringError):
        route_scoring.score_route_aware_candidate(
            candidate,
            snapshot,
            view,
            settings=benchmark.PROBE_SETTINGS,
            stopped=lambda: None,
        )


@pytest.mark.parametrize(
    "binding",
    ("tampered_candidate_id", "stale_base_revision", "stale_view_revision"),
)
def test_projection_helper_refuses_invalid_bindings_before_using_placements(
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
    if binding == "tampered_candidate_id":
        candidate = replace(candidate, candidate_id="sha256:" + "0" * 64)
    elif binding == "stale_base_revision":
        candidate = finalise_candidate(replace(candidate, base_revision="sha256:" + "0" * 64))
    else:
        candidate = finalise_candidate(replace(candidate, view_revision="sha256:" + "0" * 64))
    monkeypatch.setattr(
        route_scoring,
        "_project_footprint",
        lambda *_args: pytest.fail("placements must not be used before binding validation"),
    )

    with pytest.raises(route_scoring.RouteScoringError):
        route_scoring.project_legal_candidate_snapshot(candidate, snapshot, view)


def test_projection_reproduces_the_source_poses_exactly_for_an_unmoved_candidate() -> None:
    """F10: an identity placement must be a geometric identity, pad for pad."""

    _source, snapshot, view, intent = benchmark._fixture()
    initial = solve_placement(
        intent,
        snapshot,
        view,
        settings=PlacementSolverSettings(**{**benchmark.SEARCH_SETTINGS, "max_rounds": 0}),
    )
    candidate = initial.ranked[0].candidate
    assert not any(placement.moved for placement in candidate.placements)

    projected = route_scoring.project_legal_candidate_snapshot(candidate, snapshot, view)

    assert projected.snapshot_digest == snapshot.snapshot_digest
    for source, target in zip(snapshot.content.pads, projected.content.pads, strict=True):
        assert (source.id, source.center, source.rotation_udeg) == (
            target.id,
            target.center,
            target.rotation_udeg,
        )


def test_projection_translates_a_moved_footprint_rigidly() -> None:
    """F10: a translated footprint must carry its pads by exactly the origin delta."""

    _source, snapshot, view, intent = benchmark._fixture()
    result = solve_placement(
        intent, snapshot, view, settings=PlacementSolverSettings(**benchmark.SEARCH_SETTINGS)
    )
    moved = next(
        item
        for item in result.ranked
        if any(placement.moved for placement in item.candidate.placements)
    )
    candidate = moved.candidate
    projected = route_scoring.project_legal_candidate_snapshot(candidate, snapshot, view)
    sources = {pad.id: pad for pad in snapshot.content.pads}
    targets = {pad.id: pad for pad in projected.content.pads}
    origins = {footprint.id: footprint.origin for footprint in snapshot.content.footprints}
    checked = 0

    for placement in candidate.placements:
        # This search only translates; orientation is carried through unchanged.
        assert placement.orientation_udeg == view.footprints[placement.ref_id].orientation_udeg
        delta_x = placement.origin_x_nm - origins[placement.ref_id].x
        delta_y = placement.origin_y_nm - origins[placement.ref_id].y
        for pad_id, owner in view.owner_by_pad.items():
            if owner != placement.ref_id:
                continue
            assert targets[pad_id].center == PointNM(
                sources[pad_id].center.x + delta_x, sources[pad_id].center.y + delta_y
            )
            assert targets[pad_id].rotation_udeg == sources[pad_id].rotation_udeg
            checked += 1
    assert checked == len(snapshot.content.pads)


def test_projection_rotation_is_rigid_about_the_footprint_origin() -> None:
    """F10: a quarter turn must preserve every pad's distance from its footprint origin."""

    _source, snapshot, view, intent = benchmark._fixture()
    result = solve_placement(
        intent, snapshot, view, settings=PlacementSolverSettings(**benchmark.SEARCH_SETTINGS)
    )
    candidate = result.ranked[0].candidate
    placement = candidate.placements[0]
    footprint = next(item for item in snapshot.content.footprints if item.id == placement.ref_id)
    turned = replace(
        placement,
        origin_x_nm=footprint.origin.x,
        origin_y_nm=footprint.origin.y,
        orientation_udeg=(footprint.rotation_udeg + 90_000_000) % 360_000_000,
    )
    pads = [
        pad for pad in snapshot.content.pads if view.owner_by_pad.get(pad.id) == placement.ref_id
    ]
    assert pads

    for pad in pads:
        rotated = route_scoring._project_pad(pad, footprint, turned)
        before = (
            pad.center.x - footprint.origin.x,
            pad.center.y - footprint.origin.y,
        )
        after = (
            rotated.center.x - footprint.origin.x,
            rotated.center.y - footprint.origin.y,
        )
        assert before[0] ** 2 + before[1] ** 2 == after[0] ** 2 + after[1] ** 2
        assert rotated.rotation_udeg == (pad.rotation_udeg + 90_000_000) % 360_000_000


def test_probe_selection_takes_stable_supported_single_layer_nets_in_order() -> None:
    """F10: probe choice must be deterministic, bounded, and restricted to supported nets."""

    _source, snapshot, view, intent = benchmark._fixture()
    result = solve_placement(
        intent, snapshot, view, settings=PlacementSolverSettings(**benchmark.SEARCH_SETTINGS)
    )
    projected = route_scoring.project_legal_candidate_snapshot(
        result.ranked[0].candidate, snapshot, view
    )
    pads_by_net: dict[str, list[object]] = {}
    for pad in projected.content.pads:
        if pad.net_id is not None:
            pads_by_net.setdefault(pad.net_id, []).append(pad)

    everything = route_scoring._probes(projected, 32)

    assert len(everything) == 11
    assert everything == tuple(sorted(everything))
    assert len({net_id for net_id, _layer in everything}) == len(everything)
    for net_id, layer_id in everything:
        pads = pads_by_net[net_id]
        assert 2 <= len(pads) <= 9
        assert all(layer_id in pad.layer_ids for pad in pads)
    # The cap truncates a stable prefix rather than reshuffling the selection.
    assert route_scoring._probes(projected, 3) == everything[:3]
    assert route_scoring._probes(projected, 1) == everything[:1]


def test_the_legality_verdict_is_invariant_across_scoring_policies() -> None:
    """F10: scoring ranks candidates; it must never touch what the legalizer decided."""

    _source, snapshot, view, intent = benchmark._fixture()
    baseline = solve_placement(
        intent, snapshot, view, settings=PlacementSolverSettings(**benchmark.SEARCH_SETTINGS)
    )
    aware = solve_placement(
        intent,
        snapshot,
        view,
        settings=PlacementSolverSettings(**benchmark.SEARCH_SETTINGS, **_AWARE_SETTINGS),
    )
    assert baseline.ranked and aware.ranked
    assert all(item.candidate.evidence.legality.legal for item in baseline.ranked)
    assert all(item.candidate.evidence.legality.legal for item in aware.ranked)

    candidate = baseline.ranked[0].candidate
    before = candidate.evidence.legality
    plain_score, plain_evidence, _plain = score_placement_candidate(
        candidate,
        snapshot,
        view,
        settings=PlacementSolverSettings(**benchmark.SEARCH_SETTINGS),
    )
    aware_score, aware_evidence, _aware = score_placement_candidate(
        candidate,
        snapshot,
        view,
        settings=PlacementSolverSettings(**benchmark.SEARCH_SETTINGS, **_AWARE_SETTINGS),
    )

    assert candidate.evidence.legality == before
    assert plain_evidence is None and aware_evidence is not None
    assert plain_score is not None and aware_score is not None
    # The two policies may order candidates differently, but they read the same legality and the
    # same intent-rule violations off the very same legalizer evidence.
    assert plain_score.violated_rules == aware_score.violated_rules
    assert plain_score.connectivity_manhattan_nm == aware_score.connectivity_manhattan_nm
    assert plain_score.moved_footprints == aware_score.moved_footprints
