"""Incomplete private searches cannot become optimization derivatives."""

from types import SimpleNamespace

import pytest
from test_optimization_inputs import launch as launch

from copper_mcp.config import Settings
from copper_mcp.optimization import placement
from copper_mcp.optimization.inputs import prepare_optimization
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.worker import OptimizationExecutionError


@pytest.mark.parametrize(
    "status,expected",
    [
        ("legalizer_exhausted", "budget_exhausted"),
        ("deadline_exhausted", "budget_exhausted"),
        ("cancelled", "backend_failure"),
        ("input_refused", "unsupported_geometry"),
    ],
)
def test_incomplete_search_refuses_before_serialization(
    launch, tmp_path, monkeypatch, status, expected
):
    settings = Settings(workspace=tmp_path, max_route_preview_seconds=120)
    first = prepare_optimization(launch, settings)
    launch["movable_footprint_refs"] = [first.snapshot.content.footprints[0].id]
    prepared = prepare_optimization(launch, settings)
    assert prepared.placement_intent is not None
    monkeypatch.setattr(
        placement,
        "solve_placement",
        lambda *_a, **_kw: SimpleNamespace(
            status=status,
            ranked=(object(),),
        ),
    )

    def forbidden(*_a, **_kw):
        pytest.fail("incomplete placement must never reach derivative serialization")

    monkeypatch.setattr(placement, "render_kicad_placement_candidate_board", forbidden)
    probe = SimpleNamespace(
        checkpoint=lambda: SimpleNamespace(usage=ResourceUsage()),
        reserve=lambda _usage: None,
        remaining_time_ms=lambda: 10_000,
        cancelled=lambda: False,
    )
    with pytest.raises(OptimizationExecutionError) as error:
        placement.search_placements(prepared, settings, probe)
    assert error.value.code == expected
    assert (tmp_path / "board.kicad_pcb").read_bytes() == prepared.source
