"""Regression coverage for the bounded deterministic placement-solver baseline."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.placement import build_placement_view, parse_placement_intent
from copper_mcp.placement.solver import PlacementSolverSettings, solve_placement

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
