"""Final connectivity spends its declared verification allocation, never borrowed search work."""

from types import SimpleNamespace

import pytest
from test_optimization_coordinator import run
from test_optimization_coordinator import synthetic_authority as synthetic_authority
from test_optimization_identity_v2 import connected_launch as connected_launch

from copper_mcp.optimization import routing
from copper_mcp.optimization.evaluation_v2 import SlotProbe
from copper_mcp.optimization.package import SlotBudget
from copper_mcp.optimization.worker import OptimizationExecutionError
from copper_mcp.routing.contracts import AStarSettings


@pytest.mark.parametrize("validation", [False, True])
def test_connectivity_checks_use_the_active_pool(monkeypatch, validation):
    slot = SlotProbe(
        SimpleNamespace(),
        SlotBudget(
            expansions=100,
            route_attempts=10,
            repair_rounds=0,
            routing_checks=100,
            validation_checks=1000,
            wall_ms=10000,
            external_output_bytes=1024,
        ),
    )
    slot.routing_checks = 100
    slot.validation = validation
    charges = []
    monkeypatch.setattr(
        slot, "reserve", lambda charge: charges.append((slot.validation, charge.obstacle_checks))
    )
    work = routing._ConnectivityWork(AStarSettings(max_obstacle_checks=64), slot)
    if validation:
        work.obstacle_check()
        assert charges == [(True, 64)]
    else:
        with pytest.raises(OptimizationExecutionError):
            work.obstacle_check()
        assert not charges


def test_final_connectivity_uses_verification_phase(
    connected_launch, tmp_path, synthetic_authority, monkeypatch
):
    actual = routing._already_connected
    phases = []

    def observed(prepared, snapshot, net, fill, probe):
        phases.append(probe.validation)
        return actual(prepared, snapshot, net, fill, probe)

    monkeypatch.setattr(routing, "_already_connected", observed)
    record, _, package = run(connected_launch, tmp_path)
    assert record.status == "awaiting_approval", record.failure_code
    # Repair admission and routing use search checks; final connectivity uses verification.
    assert package is not None and phases == [False, False, True]
