"""Roster completeness, typed-result discipline, and self-digest tests for the M2 artifact.

The condition these tests defend is M2's closing condition 2: *a typed ``not_run`` is a result; a
missing row is not*.  So the tests are mostly about what the harness refuses to emit — a roster
that lost a member, a ``not_run`` with no reason, a ``not_run`` with nothing that would change it,
a completion rate over a shortened denominator, and a DRC metric arriving in a protocol that
deliberately has none.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import benchmark_cross_router_comparison as comparison
from scripts import benchmark_simple_route_json_corpus as corpus_runner

ARTIFACT = comparison.DEFAULT_OUTPUT
CORPUS_ARTIFACT = corpus_runner.DEFAULT_OUTPUT

#: Pinned membership. A baseline leaving the roster is exactly the missing row condition 2 forbids,
#: and it would otherwise be invisible: the harness would still emit a well-formed, self-consistent
#: table, one row shorter.
DECLARED_BASELINES = ("freerouting", "pcbworld-evaluation", "tscircuit-autorouting")


def _artifact() -> dict[str, Any]:
    document = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _rows() -> tuple[dict[str, Any], ...]:
    return tuple(baseline.resolve() for baseline in comparison.BASELINE_ROSTER)


def test_the_declared_roster_is_exactly_the_router_baselines_section_three_names() -> None:
    assert sorted(baseline.id for baseline in comparison.BASELINE_ROSTER) == list(
        DECLARED_BASELINES
    )
    for baseline in comparison.BASELINE_ROSTER:
        # Every row must point at the §3 row that determines it, so a reader can check the reason
        # against the research note rather than against this file.
        assert comparison.RESEARCH_NOTE in baseline.determination_row
        assert baseline.reason_kind in {"licence", "environment"}
        assert baseline.preconditions


def test_every_declared_baseline_has_a_row_carrying_a_typed_result() -> None:
    rows = _rows()

    comparison.check_roster(rows)

    assert {row["id"] for row in rows} == set(DECLARED_BASELINES)
    for row in rows:
        assert row["status"] in {"measured", "not_run", "runnable_but_unbridged"}
        assert row["role"] == "baseline"


def test_dropping_a_declared_baseline_from_the_table_is_refused() -> None:
    rows = _rows()

    with pytest.raises(comparison.CrossRouterComparisonError, match="no row"):
        comparison.check_roster(rows[:-1])


def test_substituting_a_baseline_keeps_the_count_and_is_still_refused() -> None:
    rows = _rows()
    swapped = ({**rows[0], "id": "some-other-router"}, *rows[1:])

    with pytest.raises(comparison.CrossRouterComparisonError, match="declared roster"):
        comparison.check_roster(swapped)


def test_a_not_run_row_with_no_reason_is_refused() -> None:
    rows = _rows()
    silent = ({**rows[0], "reason": "   "}, *rows[1:])

    with pytest.raises(comparison.CrossRouterComparisonError, match="records no reason"):
        comparison.check_roster(silent)


def test_a_not_run_row_that_names_nothing_that_would_change_it_is_refused() -> None:
    rows = _rows()
    hopeless = ({**rows[0], "what_would_change_it": []}, *rows[1:])

    with pytest.raises(comparison.CrossRouterComparisonError, match="names nothing"):
        comparison.check_roster(hopeless)


def test_a_reason_that_is_neither_a_licence_nor_an_environment_fact_is_refused() -> None:
    rows = _rows()
    editorial = ({**rows[0], "reason_kind": "judgement"}, *rows[1:])

    with pytest.raises(comparison.CrossRouterComparisonError, match="neither a licence"):
        comparison.check_roster(editorial)


def test_an_unobservable_precondition_is_never_assumed_to_hold() -> None:
    unobservable = comparison.Precondition(
        name="bridge", description="a bridge that does not exist"
    )
    observable = comparison.Precondition(name="git", description="git on PATH", executable="git")

    assert unobservable.satisfied() is False
    assert unobservable.payload()["observable"] is False
    # The control: an observable precondition that genuinely holds reports so, which is what makes
    # the negative above a measurement rather than a constant.
    assert observable.satisfied() is True


def test_a_name_on_path_is_not_evidence_that_the_thing_behind_it_runs(tmp_path: Path) -> None:
    # macOS ships /usr/bin/java as a stub that exists whether or not a JRE does and exits non-zero
    # saying so. A which-hit would have reported it satisfied and flipped FreeRouting's row off
    # not_run on no evidence, which is the audit's own rule: an absence is evidence only if the
    # observation was capable of reporting a presence — and so is a presence. The stub is
    # reproduced as a controlled executable rather than scavenged from the host: a runner with a
    # real JRE inverts the host-scavenged premise (that is how this test failed on hosted CI).
    stub = tmp_path / "java"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    which_only = comparison.Precondition(
        name="java", description="java on PATH", executable=str(stub)
    )
    probed = comparison.Precondition(
        name="java", description="java that runs", executable=str(stub), probe_args=("-version",)
    )

    assert which_only.satisfied() is True
    assert probed.satisfied() is False
    assert probed.payload()["probe"] == ["-version"]
    # A probe that does succeed must still report satisfied, or the check would be a constant.
    runs = tmp_path / "java-that-runs"
    runs.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runs.chmod(0o755)
    assert comparison.Precondition(
        name="java", description="java that runs", executable=str(runs), probe_args=("-version",)
    ).satisfied()


def test_the_licence_bound_baselines_stay_not_run_for_a_licence_reason() -> None:
    rows = {row["id"]: row for row in _rows()}

    for identifier in ("tscircuit-autorouting", "pcbworld-evaluation"):
        row = rows[identifier]
        assert row["status"] == "not_run"
        assert row["reason_kind"] == "licence"
        assert row["results"] is None
    freerouting = rows["freerouting"]
    assert freerouting["status"] == "not_run"
    assert freerouting["reason_kind"] == "environment"
    # #53's park is the operator gate, and the row must say so rather than implying an oversight.
    assert "#53" in freerouting["reason"]
    # Even runnable, this row would not be neutral on this corpus, and the row carries that.
    assert "helped define" in freerouting["comparability_caveat"]


def test_the_protocol_declares_its_metrics_and_carries_no_drc_metric() -> None:
    comparison.check_protocol()

    names = [name for name, _definition in comparison.COMPARISON_METRICS]
    assert "completion_percent" in names
    for name, definition in comparison.COMPARISON_METRICS:
        assert definition.strip()
        for marker in comparison.EXCLUDED_METRIC_MARKERS:
            assert marker not in f"{name} {definition}".lower()


def test_a_drc_metric_arriving_in_the_protocol_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        comparison,
        "COMPARISON_METRICS",
        (*comparison.COMPARISON_METRICS, ("drc_violations", "hard violations per board")),
    )

    with pytest.raises(comparison.CrossRouterComparisonError, match="excluded marker"):
        comparison.check_protocol()


def test_a_metric_with_no_definition_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(comparison, "COMPARISON_METRICS", (("completion_percent", "  "),))

    with pytest.raises(comparison.CrossRouterComparisonError, match="no definition"):
        comparison.check_protocol()


def test_a_completion_rate_over_a_shortened_denominator_is_refused() -> None:
    truthful = {
        "grid_policy": {"name": "fixed", "description": "d"},
        "boards_offered": 1,
        "boards_imported": 1,
        "import_refusals": {},
        "nets_attempted": 3,
        "nets_routed": 1,
        "outcome_breakdown": {"routed": 1, "refused:off_grid": 2, "no_routing_work": 5},
        "routed_wire_length_nm": 10,
        "routed_pad_gap_lower_bound_nm": 5,
        "length_over_pad_gap_lower_bound": 2.0,
        "vias": 0,
        "bends": 0,
    }
    assert comparison._protocol_metrics(truthful)["nets_attempted"] == 3

    shortened = {**truthful, "outcome_breakdown": {"routed": 1, "no_routing_work": 5}}

    with pytest.raises(comparison.CrossRouterComparisonError, match="shortened denominator"):
        comparison._protocol_metrics(shortened)


def test_the_artifact_validates_against_its_own_self_digest() -> None:
    report = _artifact()
    recorded = report.pop("run_id")

    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    assert recorded == "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_the_artifact_states_that_one_measured_row_supports_no_comparison() -> None:
    metrics = _artifact()["metrics"]

    assert metrics["declared_rows"] == 1 + len(DECLARED_BASELINES)
    assert metrics["measured_rows"] == 1
    assert metrics["comparison_supported"] is False
    claims = _artifact()["not_claimed"]
    assert any("supports no comparative conclusion" in claim for claim in claims)
    assert any("No DRC metric is in the protocol" in claim for claim in claims)


def test_the_artifact_accounts_for_every_corpus_the_research_note_determined() -> None:
    metrics = _artifact()["metrics"]
    considered = {entry["id"]: entry for entry in metrics["corpora_considered"]}

    assert set(considered) == {
        "dwiel-tscircuit-benchmark",
        "tscircuit-autorouting",
        "pcbench",
        "pcbworld",
    }
    assert considered["dwiel-tscircuit-benchmark"]["determination"] == "imported"
    assert considered["pcbench"]["determination"] == "not_redistributable"
    assert "ADR-0107" in considered["pcbench"]["note"]
    assert considered["pcbworld"]["determination"] == "not_released"


def test_the_problem_set_is_the_frozen_redistributable_corpus() -> None:
    problem_set = _artifact()["metrics"]["problem_set"]

    assert problem_set["license_spdx"] == "MIT"
    assert problem_set["frozen_and_redistributable"] is True
    assert problem_set["committed_boards"] == 20
    assert problem_set["upstream_commit"] == "be36518b5bf51755dae92c230061ab3cf4e3e063"
    # The committed digests are the pin: a board whose bytes moved would fail the runner's own
    # manifest check before routing, and this asserts the artifact records the same pin.
    manifest = corpus_runner.load_manifest()
    committed = {
        str(entry["name"]).removesuffix(".json"): entry["sha256"]
        for entry in manifest["files"]
        if isinstance(entry, dict) and entry["committed"]
    }
    assert {board["board"]: board["sha256"] for board in problem_set["boards"]} == committed
    # It is a frozen corpus for routing and not an externally authored KiCad family; the artifact
    # must not let the second be read out of the first.
    assert "#110" in problem_set["why_this_satisfies_the_closing_corpus_condition"]


def test_the_subject_row_replays_the_recorded_corpus_run_rather_than_remeasuring() -> None:
    subject = next(row for row in _artifact()["metrics"]["routers"] if row["role"] == "subject")
    recorded = json.loads(CORPUS_ARTIFACT.read_text(encoding="utf-8"))["metrics"]["configurations"]

    for name, results in subject["results"].items():
        assert results["nets_attempted"] == recorded[name]["nets_attempted"]
        assert results["nets_completed"] == recorded[name]["nets_routed"]
        assert results["outcome_breakdown"] == recorded[name]["outcome_breakdown"]
        assert results["routed_wire_length_nm"] == recorded[name]["routed_wire_length_nm"]
        assert results["vias"] == recorded[name]["vias"]
        assert results["bends"] == recorded[name]["bends"]


def test_the_artifact_matches_a_fresh_run_of_the_committed_corpus() -> None:
    # The artifact's baseline rows record the environment that produced them (the macOS java
    # stub among the observations). Parity replays those recorded observations rather than
    # re-probing this machine — probing live here would compare two machines, not two runs of
    # the same code, and a hosted runner with a real JRE flips FreeRouting's row.
    recorded = _artifact()["metrics"]
    observations = {
        row["id"]: {entry["name"]: entry["satisfied"] for entry in row["preconditions"]}
        for row in recorded["routers"]
        if row["role"] == "baseline"
    }

    fresh = comparison.build_report(1, precondition_observations=observations)

    assert fresh["metrics"] == recorded


def test_a_roster_entry_whose_preconditions_all_hold_is_not_silently_reported_not_run() -> None:
    # A baseline whose environment arrives before its driver does must not read as `not_run`: the
    # reason would name a fact that is no longer true. It reports the harness gap instead.
    satisfiable = dataclasses.replace(
        comparison.BASELINE_ROSTER[0],
        preconditions=(
            comparison.Precondition(name="git", description="git on PATH", executable="git"),
        ),
    )

    row = satisfiable.resolve()

    assert row["status"] == "runnable_but_unbridged"
    assert row["unmet_preconditions"] == []
    assert "harness gap" in row["reason"]
