"""Replay and guard tests for the whole-board negotiated corpus census (B-124).

The census reports a negative — no board on the committed corpus reaches ADR-0117's local-repair
firing precondition — so almost every test here is about whether the instrument was *capable* of
reporting a positive.  A recorder that silently observes nothing, a stage ladder whose top rung is
unreachable, and a cross-check that agrees with everything would all produce exactly the same
artifact, and none of them would be a measurement.

So the file is organised around four questions.

1. Does the census artifact still match a fresh run?  It is fully deterministic — no executable
   probing, no host-dependent branch — so parity is a plain re-run rather than a replay of recorded
   observations.
2. Does the pass-through recorder actually record, and actually change nothing?  Answered on the
   committed two-net KiCad crossing fixture, where the coordinator genuinely reaches its
   physical-clearance gate, rather than on the corpus, where it never does.
3. Does every rung of the blocking-stage ladder, including ``repair_precondition_reached``,
   respond to input?  Answered with constructed gate observations, because the corpus supplies
   none.
4. Do the harness's own refusals fire?  A drifted B-088 baseline, a disagreeing admission
   predicate, and an observer that perturbs the result must each raise rather than be recorded.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.benchmarks.simple_route_json import import_simple_route_json
from copper_mcp.routing import AStarRouter
from copper_mcp.routing import congestion as coordinator
from copper_mcp.routing.congestion import NegotiatedRoutingRequest, negotiate_routes
from copper_mcp.routing.physical_clearance import PhysicalClearanceFailure
from scripts import benchmark_negotiated_congestion as crossing
from scripts import benchmark_negotiated_corpus_census as census
from scripts import benchmark_simple_route_json_corpus as reference

ARTIFACT = census.DEFAULT_OUTPUT


def _artifact() -> dict[str, Any]:
    document = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _observation(
    *, candidates: int, failure: str | None, violating_nets: int
) -> census.GateObservation:
    return census.GateObservation(
        candidates=candidates, failure=failure, violating_nets=violating_nets, pair_checks=1
    )


# --------------------------------------------------------------------------------------------
# 1. The artifact
# --------------------------------------------------------------------------------------------


def test_the_artifact_validates_against_its_own_self_digest() -> None:
    report = _artifact()
    recorded = report.pop("run_id")

    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    assert recorded == "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_the_artifact_matches_a_fresh_run_of_the_committed_corpus() -> None:
    recorded = _artifact()["metrics"]

    fresh = census.build_report(repetitions=1)

    assert fresh["metrics"] == recorded


def test_the_artifact_records_that_repair_was_never_enabled() -> None:
    report = _artifact()

    assert report["metrics"]["repair_settings_enabled"] is False
    assert report["configuration"]["repair_settings"] is None
    assert any("repair is never" in claim for claim in report["not_claimed"])


def test_the_census_reports_the_predeclared_per_board_fields_for_every_board() -> None:
    required = {
        "board",
        "board_revision",
        "document_sha256",
        "submitted_nets",
        "submitted_net_ids",
        "admission_conjuncts",
        "first_unmet_conjunct",
        "envelope_constructed",
        "terminal_status",
        "physical_gate_calls",
        "physical_gate_observations",
        "blocking_stage",
        "repair_precondition_reached",
        "reference_nets_routed",
    }

    for configuration in _artifact()["metrics"]["configurations"].values():
        assert len(configuration["boards"]) == 20
        for board in configuration["boards"]:
            assert required <= set(board), board["board"]
            assert board["blocking_stage"] in census.BLOCKING_STAGES


def test_the_headline_is_a_measured_zero_against_a_named_reference_baseline() -> None:
    metrics = _artifact()["metrics"]
    headline = metrics["headline"]

    assert headline["boards_offered"] == 20
    assert headline["boards_reaching_the_repair_precondition"] == 0
    # The census's second, independently valuable number. Zero completed is an admission refusal,
    # not a routing outcome, and the artifact must carry both halves so it cannot be read as one.
    assert headline["negotiated_nets_completed"] == 0
    assert headline["reference_per_net_nets_routed"] == 70
    assert metrics["reference_baseline"]["nets_attempted"] == 117
    assert any("routing-quality claim" in claim for claim in _artifact()["not_claimed"])


def test_the_two_admission_conjuncts_that_block_this_corpus_are_recorded_separately() -> None:
    configurations = _artifact()["metrics"]["configurations"]
    primary = configurations["b088-routable"]
    control = configurations["two-pad-control"]

    # 16 boards carry two or more reference-routed nets and are refused by the coordinator for the
    # two-pin conjunct; the remaining 4 cannot even form a two-request envelope.
    assert (
        primary["first_unmet_conjunct_breakdown"]["exactly_two_selected_layer_pads_per_net"] == 16
    )
    assert primary["first_unmet_conjunct_breakdown"]["at_least_two_requests"] == 4
    assert primary["boards_admitted_by_the_coordinator"] == 0
    # The control moves the block to the *other* conjunct, which is what makes it a control: the
    # corpus is excluded twice over, not once.
    assert control["first_unmet_conjunct_breakdown"]["one_shared_world_grid"] == 6
    assert control["boards_admitted_by_the_coordinator"] == 0
    # And the two populations do not overlap at all: no net that the per-net reference routed has
    # exactly two selected-layer pads.
    assert primary["submitted_nets_the_reference_routed"] == 70
    assert control["nets_submitted"] == 36
    assert control["submitted_nets_the_reference_routed"] == 0


def test_no_board_reached_the_gate_so_no_gate_observation_was_recorded() -> None:
    for configuration in _artifact()["metrics"]["configurations"].values():
        assert configuration["physical_gate_calls"] == 0
        for board in configuration["boards"]:
            assert board["physical_gate_observations"] == []
            assert board["repair_precondition_reached"] is False


# --------------------------------------------------------------------------------------------
# 2. The recorder is a real instrument
# --------------------------------------------------------------------------------------------


def test_the_recorder_observes_a_real_gate_call_and_changes_no_published_field() -> None:
    # The corpus never reaches the gate, so a corpus-only test would pass on a recorder that does
    # nothing at all. The committed two-net crossing fixture does reach it, and is used here for
    # exactly that reason: it is the positive control the census itself cannot supply.
    snapshot, requests, _source = crossing._load_fixture()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest, requests=requests, max_iterations=8
    )

    control = census._projection(negotiate_routes(snapshot, envelope))
    with census.observed_physical_gate() as observations:
        instrumented = census._projection(negotiate_routes(snapshot, envelope))

    assert observations, "the recorder saw no gate call on a fixture that reaches the gate"
    assert instrumented == control
    assert all(item.candidates == len(requests) for item in observations)
    assert all(item.pair_checks >= 0 for item in observations)


def test_the_recorder_restores_the_coordinator_symbol_even_when_the_body_raises() -> None:
    original = coordinator.verify_negotiated_physical_clearance

    with pytest.raises(RuntimeError, match="deliberate"):
        with census.observed_physical_gate():
            assert coordinator.verify_negotiated_physical_clearance is not original
            raise RuntimeError("deliberate")

    assert coordinator.verify_negotiated_physical_clearance is original


def test_an_observer_that_perturbs_the_published_result_is_refused() -> None:
    # The census's honesty rests on the recorder being inert. This proves the runner would notice
    # if it were not: a recorder that changed a published field must fail the run, not the review.
    _manifest, samples = reference.load_corpus()
    name, payload = next(item for item in samples if item[0].startswith("ts18"))
    problem = import_simple_route_json(Path(name).stem, payload)
    router = AStarRouter()
    submitted = census.PRIMARY.select(census._solo_reference(problem, router))
    calls = {"n": 0}
    honest = census._projection

    def perturbing(result: Any) -> dict[str, Any]:
        calls["n"] += 1
        projected = honest(result)
        return projected if calls["n"] == 1 else {**projected, "iterations": 999}

    census._projection = perturbing  # type: ignore[assignment]
    try:
        with pytest.raises(census.NegotiatedCensusError, match="changed the published"):
            census.census_board(problem, submitted, router)
    finally:
        census._projection = honest  # type: ignore[assignment]
    assert calls["n"] == 2


# --------------------------------------------------------------------------------------------
# 3. Every rung of the ladder responds to input
# --------------------------------------------------------------------------------------------


def test_the_ladder_reports_the_precondition_when_all_three_conjuncts_hold() -> None:
    # The rung the whole census exists to count. No corpus board reaches it, so if this rung were
    # unreachable in code the artifact's zero would be a constant rather than a measurement.
    reached = _observation(
        candidates=4, failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value, violating_nets=2
    )

    assert (
        census._stage_from_observations((reached,), submitted=4, connectable=0)
        == "repair_precondition_reached"
    )


@pytest.mark.parametrize(
    ("observations", "submitted", "expected"),
    [
        ((), 4, "no_physical_gate_call"),
        (
            (_observation(candidates=4, failure=None, violating_nets=0),),
            4,
            "no_clearance_violation",
        ),
        (
            (
                _observation(
                    candidates=4,
                    failure=PhysicalClearanceFailure.BUDGET_EXHAUSTED.value,
                    violating_nets=0,
                ),
            ),
            4,
            "no_clearance_violation",
        ),
        (
            (
                _observation(
                    candidates=3,
                    failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value,
                    violating_nets=2,
                ),
            ),
            4,
            "clearance_violation_on_incomplete_allocation",
        ),
        (
            (
                _observation(
                    candidates=4,
                    failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value,
                    violating_nets=1,
                ),
            ),
            4,
            "clearance_violation_with_one_violating_net",
        ),
    ],
)
def test_each_earlier_rung_is_reported_for_the_input_that_stops_there(
    observations: tuple[census.GateObservation, ...], submitted: int, expected: str
) -> None:
    assert (
        census._stage_from_observations(observations, submitted=submitted, connectable=0)
        == expected
    )


def test_the_latest_rung_any_iteration_reached_is_the_one_recorded() -> None:
    # A run can fail the gate several times. The census must record the furthest a run got, not the
    # first or last thing that happened to it.
    early = _observation(
        candidates=2, failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value, violating_nets=2
    )
    late = _observation(
        candidates=4, failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value, violating_nets=2
    )

    assert (
        census._stage_from_observations((early, late), submitted=4, connectable=0)
        == "repair_precondition_reached"
    )
    assert (
        census._stage_from_observations((late, early), submitted=4, connectable=0)
        == "repair_precondition_reached"
    )


def test_an_already_connected_net_counts_toward_a_complete_allocation() -> None:
    # The recorder sees candidates but not connections, so the census bounds connections with the
    # solo reference's already_connected count. The bound must be able to *complete* an allocation,
    # or the completeness conjunct could never hold on a board carrying pre-existing copper.
    partial = _observation(
        candidates=3, failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value, violating_nets=2
    )

    assert (
        census._stage_from_observations((partial,), submitted=4, connectable=0)
        == "clearance_violation_on_incomplete_allocation"
    )
    assert (
        census._stage_from_observations((partial,), submitted=4, connectable=1)
        == "repair_precondition_reached"
    )


# --------------------------------------------------------------------------------------------
# 4. The harness's own refusals
# --------------------------------------------------------------------------------------------


def test_a_tampered_reference_artifact_is_refused(tmp_path: Path) -> None:
    recorded = json.loads(census.REFERENCE_ARTIFACT.read_text(encoding="utf-8"))
    recorded["metrics"]["configurations"]["fixed"]["nets_routed"] += 1
    tampered = tmp_path / "b088.json"
    tampered.write_text(json.dumps(recorded), encoding="utf-8")

    with pytest.raises(census.NegotiatedCensusError, match="self-digest"):
        census.load_reference_artifact(tampered)


def test_a_submitted_set_that_drifts_from_the_recorded_baseline_is_refused() -> None:
    # The census's comparability with B-088 rests entirely on submitting the same nets B-088
    # routed. If the re-derived set ever stops matching the committed artifact, the run must fail
    # rather than quietly measure a different population under the same name.
    _manifest, samples = reference.load_corpus()
    routed = census._reference_routed_by_board(census.load_reference_artifact())
    drifted = {**routed, "ts18_dual_reg": routed["ts18_dual_reg"] + 1}

    with pytest.raises(census.NegotiatedCensusError, match="does not match the committed"):
        census.run_configuration(samples, census.PRIMARY, drifted)


def test_an_admission_predicate_that_disagrees_with_the_coordinator_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The independent predicate is only a cross-check if a disagreement is fatal. Here it is made
    # to claim every conjunct holds on a board the coordinator refuses.
    _manifest, samples = reference.load_corpus()
    name, payload = next(item for item in samples if item[0].startswith("ts18"))
    problem = import_simple_route_json(Path(name).stem, payload)
    router = AStarRouter()
    submitted = census.PRIMARY.select(census._solo_reference(problem, router))
    monkeypatch.setattr(
        census,
        "_admission",
        lambda *_args, **_kwargs: {name: True for name, _s, _d in census.ADMISSION_CONJUNCTS},
    )

    with pytest.raises(census.NegotiatedCensusError, match="does not match the independently"):
        census.census_board(problem, submitted, router)


def test_a_predicate_that_names_the_wrong_conjunct_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Agreeing that the board was refused is not enough. If the predicate and the coordinator
    # blame different conjuncts, the per-board attribution in the artifact is fiction, and the
    # attribution is the part of this census that says what would change the answer.
    _manifest, samples = reference.load_corpus()
    name, payload = next(item for item in samples if item[0].startswith("ts18"))
    problem = import_simple_route_json(Path(name).stem, payload)
    router = AStarRouter()
    submitted = census.PRIMARY.select(census._solo_reference(problem, router))
    misattributed = {name: True for name, _s, _d in census.ADMISSION_CONJUNCTS}
    misattributed["one_shared_world_grid"] = False
    monkeypatch.setattr(census, "_admission", lambda *_a, **_k: misattributed)

    with pytest.raises(census.NegotiatedCensusError, match="does not match the independently"):
        census.census_board(problem, submitted, router)


def test_the_predicate_and_the_coordinator_agree_on_every_committed_board() -> None:
    # The control for the test above: the cross-check must actually be exercised by the corpus, or
    # its failure mode would be untested and its success meaningless.
    for configuration in _artifact()["metrics"]["configurations"].values():
        refused = [
            board
            for board in configuration["boards"]
            if board["terminal_status"] == "invalid_request"
        ]
        assert refused
        for board in refused:
            expected = census.COORDINATOR_DIAGNOSTICS[board["first_unmet_conjunct"]]
            assert board["negotiated"]["diagnostic"] == expected


def test_a_nondeterministic_configuration_replay_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    honest = census.run_configuration

    def drifting(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        result = honest(*args, **kwargs)
        return result if calls["n"] == 1 else {**result, "nets_submitted": -1}

    monkeypatch.setattr(census, "run_configuration", drifting)

    with pytest.raises(census.NegotiatedCensusError, match="replay diverged"):
        census.run_census(repetitions=2)
