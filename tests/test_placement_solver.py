"""Regression coverage for the bounded deterministic placement-solver baseline."""

from __future__ import annotations

from dataclasses import replace
from itertools import chain, repeat
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.placement import build_placement_view, parse_placement_intent
from copper_mcp.placement import solver as solver_module
from copper_mcp.placement.route_scoring import RouteProbeSettings
from copper_mcp.placement.solver import (
    PlacementScoringPolicy,
    PlacementSolverError,
    PlacementSolverSettings,
    solve_placement,
)
from copper_mcp.routing.contracts import AStarSettings

ROOT = Path(__file__).resolve().parents[1]
ROTATION_BOARD = ROOT / "tests/fixtures/board-ir-v0.1/footprint-rotation.kicad_pcb"
LOCKED_BOARD = ROOT / "tests/fixtures/board-ir-v0.2/footprint-pose-courtyard.kicad_pcb"
PADLESS_BOARD = ROOT / "tests/fixtures/board-ir-v0.2/padless-footprint.kicad_pcb"
PADLESS_REF = "footprint:kicad:93000000-0000-0000-0000-000000000011"
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:solver-test", name="Solver test", **CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _board(path: Path) -> tuple[object, object]:
    source = path.read_bytes()
    conversion = parse_kicad_bytes(source, _profile())
    assert conversion.snapshot is not None, conversion.diagnostics
    return conversion.snapshot, build_placement_view(source, conversion.snapshot)


def _intent(view: object, board: Path, *, subjects: list[str] | None = None) -> object:
    footprint_refs = sorted(view.footprints)  # type: ignore[union-attr]
    return parse_placement_intent(
        {
            "board": board.name,
            "constraints": CONSTRAINTS,
            "subjects": footprint_refs if subjects is None else subjects,
            "placement_grid_nm": 1_000_000,
        }
    )


def _settings(**overrides: object) -> PlacementSolverSettings:
    values: dict[str, object] = {
        "max_evaluations": 128,
        "max_rounds": 5,
        "beam_width": 4,
        "max_ranked": 8,
        "step_nm": 1_000_000,
        "deadline_seconds": 10.0,
        "legalizer_max_checks": 100_000,
        "legalizer_deadline_seconds": 2.0,
    }
    values.update(overrides)
    return PlacementSolverSettings(**values)  # type: ignore[arg-type]


def test_solver_is_deterministic_and_reduces_same_net_manhattan_proxy() -> None:
    snapshot, view = _board(ROTATION_BOARD)
    intent = _intent(view, ROTATION_BOARD)

    runs = [solve_placement(intent, snapshot, view, settings=_settings()) for _ in range(3)]
    first = runs[0]
    assert first.initial is not None and first.initial.candidate is not None
    assert first.status in {"completed", "work_exhausted"}
    assert first.ranked
    assert all(item.candidate.evidence.legality.legal for item in first.ranked)
    assert first.initial_score is not None
    assert min(item.score.connectivity_manhattan_nm for item in first.ranked) < (
        first.initial_score.connectivity_manhattan_nm
    )
    signatures = [
        (
            run.status,
            run.evaluations,
            [(item.candidate.candidate_id, item.score) for item in run.ranked],
        )
        for run in runs
    ]
    assert signatures == [signatures[0]] * 3


def test_solver_never_moves_locked_or_padless_footprints() -> None:
    snapshot, view = _board(LOCKED_BOARD)
    locked = next(item.ref_id for item in view.footprints.values() if item.locked)
    result = solve_placement(_intent(view, LOCKED_BOARD, subjects=[locked]), snapshot, view)

    assert result.status == "completed"
    assert len(result.ranked) == 1
    locked_placement = next(
        item for item in result.ranked[0].candidate.placements if item.ref_id == locked
    )
    assert not locked_placement.moved

    padless_snapshot, padless_view = _board(PADLESS_BOARD)
    padless = solve_placement(
        _intent(padless_view, PADLESS_BOARD, subjects=[PADLESS_REF]),
        padless_snapshot,
        padless_view,
    )
    assert padless.status == "input_refused"
    assert padless.initial is not None and padless.initial.candidate is None
    assert not padless.ranked


def test_solver_surfaces_stale_digest_mismatches_before_search() -> None:
    snapshot, view = _board(ROTATION_BOARD)
    stale = replace(snapshot, snapshot_digest="sha256:" + "0" * 64)

    result = solve_placement(_intent(view, ROTATION_BOARD), stale, view, settings=_settings())

    assert result.status == "input_refused"
    assert result.evaluations == 1
    assert result.initial is not None and result.initial.diagnostic is not None
    assert result.initial.diagnostic.code == "stale_revision"


def test_solver_honours_cancellation_and_work_ceiling() -> None:
    snapshot, view = _board(ROTATION_BOARD)
    intent = _intent(view, ROTATION_BOARD)

    cancelled = solve_placement(intent, snapshot, view, cancelled=lambda: True)
    assert cancelled.status == "cancelled"
    assert cancelled.evaluations == 0
    assert cancelled.initial is None

    budgeted = solve_placement(intent, snapshot, view, settings=_settings(max_evaluations=1))
    assert budgeted.status == "work_exhausted"
    assert budgeted.evaluations == 1
    assert budgeted.ranked


def test_solver_supports_a_declared_pad_subject_by_canonicalising_its_owner() -> None:
    snapshot, view = _board(ROTATION_BOARD)
    pad_ref = next(iter(view.owner_by_pad))

    result = solve_placement(
        _intent(view, ROTATION_BOARD, subjects=[pad_ref]), snapshot, view, settings=_settings()
    )

    assert result.status in {"completed", "work_exhausted"}
    assert result.initial is not None and result.initial.request is not None
    assert result.initial.request.subject_refs == (view.owner_by_pad[pad_ref],)
    assert result.ranked


def test_route_aware_scoring_caps_probe_work_across_the_full_solver_operation() -> None:
    snapshot, view = _board(ROTATION_BOARD)
    settings = _settings(
        scoring_policy=PlacementScoringPolicy.ROUTE_AWARE_ASTAR,
        route_probe_settings=RouteProbeSettings(
            max_probes=1,
            max_total_probes=1,
            astar_settings=AStarSettings(
                grid_step_nm=1_000_000,
                max_grid_nodes=10_000,
                max_expansions=10_000,
                max_obstacle_checks=100_000,
            ),
        ),
    )

    result = solve_placement(_intent(view, ROTATION_BOARD), snapshot, view, settings=settings)

    assert result.status == "work_exhausted"
    assert result.route_probes_used == 1
    assert result.route_probe_limit == 1
    assert result.ranked and result.ranked[0].route_evidence is not None
    assert result.ranked[0].route_evidence.operation_probes_after == 1


@pytest.mark.parametrize(
    "values",
    (
        (),
        (0,),
        (-7, -1, 2, 9),
        (4, 4, 4, 4),
        (9, -3, 7, -3, 0),
    ),
)
def test_pairwise_axis_distance_matches_the_quadratic_definition(values: tuple[int, ...]) -> None:
    expected = sum(
        abs(left - right) for index, left in enumerate(values) for right in values[index + 1 :]
    )

    assert solver_module._pairwise_axis_distance(iter(values), stopped=lambda: None) == expected


def test_pairwise_axis_distance_keeps_large_nets_subquadratic_and_exact() -> None:
    count = 20_000

    assert solver_module._pairwise_axis_distance(range(count), stopped=lambda: None) == (
        count * (count - 1) * (count + 1) // 6
    )


def test_solver_drops_an_incomplete_score_when_cancelled_after_legalization() -> None:
    snapshot, view = _board(ROTATION_BOARD)
    intent = _intent(view, ROTATION_BOARD)
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    result = solve_placement(intent, snapshot, view, settings=_settings(), cancelled=cancelled)

    assert result.status == "cancelled"
    assert result.evaluations == 1
    assert result.initial is not None and result.initial.candidate is not None
    assert result.initial_score is None
    assert not result.ranked


def test_solver_drops_already_scored_rankings_when_cancelled_during_successor_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, view = _board(ROTATION_BOARD)
    intent = _intent(view, ROTATION_BOARD)
    cancellation_requested = False
    score_calls = 0
    actual_score = solver_module._score

    def cancelled() -> bool:
        return cancellation_requested

    def cancel_during_successor_score(*args: object, **kwargs: object) -> object:
        nonlocal cancellation_requested, score_calls
        score_calls += 1
        if score_calls == 2:
            cancellation_requested = True
        return actual_score(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(solver_module, "_score", cancel_during_successor_score)
    result = solve_placement(intent, snapshot, view, settings=_settings(), cancelled=cancelled)

    assert result.status == "cancelled"
    assert result.evaluations == 2
    assert result.initial is not None and result.initial.candidate is not None
    assert result.initial_score is not None
    assert not result.ranked


def test_scoring_refuses_a_partial_result_after_deadline_exhaustion() -> None:
    snapshot, view = _board(ROTATION_BOARD)
    initial = solver_module.evaluate_placement(
        _intent(view, ROTATION_BOARD), snapshot, view, deadline_seconds=1.0
    )
    assert initial.candidate is not None

    score, evidence, status = solver_module._score(
        initial.candidate,
        snapshot,
        view,
        settings=_settings(),
        stopped=lambda: "deadline_exhausted",
    )

    assert status == "deadline_exhausted"
    assert score is None
    assert evidence is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_evaluations", True),
        ("max_evaluations", 1_000_001),
        ("max_rounds", 10_001),
        ("beam_width", 1_025),
        ("max_ranked", 1_025),
        ("step_nm", 1_000_000_001),
        ("legalizer_max_checks", 2_000_001),
        ("deadline_seconds", float("nan")),
        ("deadline_seconds", float("inf")),
        ("legalizer_deadline_seconds", True),
        ("legalizer_deadline_seconds", 60.1),
    ],
)
def test_solver_settings_reject_bool_nonfinite_and_unbounded_values(
    field: str, value: object
) -> None:
    with pytest.raises(PlacementSolverError):
        PlacementSolverSettings(**{field: value})  # type: ignore[arg-type]


def test_solver_caps_each_legalizer_call_to_the_remaining_operation_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, view = _board(ROTATION_BOARD)
    intent = _intent(view, ROTATION_BOARD)
    clock = chain((0.0, 0.1, 0.2, 0.3, 0.4), repeat(0.4))
    deadlines: list[float] = []
    actual_evaluate = solver_module.evaluate_placement
    legalized_initial = actual_evaluate(intent, snapshot, view, deadline_seconds=1.0)
    assert legalized_initial.candidate is not None

    def capture_deadline(*args: object, **kwargs: object) -> object:
        deadline = kwargs["deadline_seconds"]
        assert isinstance(deadline, float)
        deadlines.append(deadline)
        return legalized_initial

    monkeypatch.setattr(solver_module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(solver_module, "evaluate_placement", capture_deadline)
    result = solve_placement(
        intent,
        snapshot,
        view,
        settings=_settings(max_evaluations=2, deadline_seconds=1.0, legalizer_deadline_seconds=5.0),
    )

    assert result.status == "work_exhausted"
    assert deadlines == [pytest.approx(0.8), pytest.approx(0.6)]


def test_solver_refuses_before_a_legalizer_call_after_operation_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, view = _board(ROTATION_BOARD)
    intent = _intent(view, ROTATION_BOARD)
    clock = iter((0.0, 2.0))

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("expired solver must not enter the legalizer")

    monkeypatch.setattr(solver_module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(solver_module, "evaluate_placement", unexpected_call)
    result = solve_placement(intent, snapshot, view, settings=_settings(deadline_seconds=1.0))

    assert result.status == "deadline_exhausted"
    assert result.evaluations == 0
    assert result.initial is None


def test_solver_cancellation_callback_failure_is_fail_closed() -> None:
    snapshot, view = _board(ROTATION_BOARD)
    intent = _intent(view, ROTATION_BOARD)

    def broken_cancel() -> bool:
        raise RuntimeError("callback transport disappeared")

    result = solve_placement(intent, snapshot, view, cancelled=broken_cancel)

    assert result.status == "cancelled"
    assert result.evaluations == 0
