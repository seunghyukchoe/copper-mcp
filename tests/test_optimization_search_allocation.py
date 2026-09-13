"""V2 route-search reservations leave bounded work for every planned fallback."""

from __future__ import annotations

import hashlib
from pathlib import Path

from test_layered_tree_detour import _source_contact_case

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.config import Settings
from copper_mcp.optimization import routing
from copper_mcp.optimization.evaluation_v2 import SlotProbe
from copper_mcp.optimization.inputs import PreparedOptimization, prepare_optimization
from copper_mcp.optimization.lifecycle import ResourceUsage, create_job
from copper_mcp.optimization.package import SlotBudget
from copper_mcp.optimization.routing import route_targets
from copper_mcp.request_boundary import net_class_constraints
from copper_mcp.routing.contracts import RouteDiagnostic, RouteFailureCode, RouteResult

FIXTURE = Path(__file__).parent / "fixtures/route-candidate/layered-tree-ordinary-4layer.kicad_pcb"
CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}


class _RootProbe:
    def __init__(self, prepared: PreparedOptimization) -> None:
        self.record = create_job(prepared.request, owner_binding="sha256:" + "a" * 64)

    def remaining_time_ms(self) -> int:
        return 120_000

    def cancelled(self) -> bool:
        return False

    def checkpoint(self):
        return self.record

    def reserve(self, charge: ResourceUsage):
        self.record = type(self.record).model_validate(
            {**self.record.model_dump(), "usage": self.record.usage.plus(charge).model_dump()}
        )
        return self.record


def _prepared(tmp_path: Path, source: bytes) -> tuple[PreparedOptimization, Settings]:
    board = tmp_path / "board.kicad_pcb"
    board.write_bytes(source)
    net_class = net_class_constraints(CONSTRAINTS)
    parsed = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert parsed.snapshot is not None and not parsed.diagnostics
    snapshot = parsed.snapshot
    targets = sorted(
        net.id
        for net in snapshot.content.nets
        if sum(pad.net_id == net.id for pad in snapshot.content.pads) >= 2
    )
    settings = Settings(workspace=tmp_path)
    prepared = prepare_optimization(
        {
            "schema_version": "optimization/v2",
            "board": board.name,
            "expect_board_revision": "sha256:" + hashlib.sha256(source).hexdigest(),
            "expect_snapshot_digest": snapshot.snapshot_digest,
            "constraints": CONSTRAINTS,
            "target_net_refs": targets,
            "routing_settings": {
                "grid_step_nm": 1_000_000,
                "max_grid_nodes": 250_000,
                "max_expansions": 100_000,
                "max_obstacles": 256,
                "max_obstacle_checks": 2_000_000,
            },
        },
        settings,
    )
    return prepared, settings


def _slot(prepared: PreparedOptimization) -> SlotProbe:
    return SlotProbe(
        _RootProbe(prepared),  # type: ignore[arg-type]
        SlotBudget(
            expansions=600_000,
            route_attempts=32,
            repair_rounds=0,
            routing_checks=1_000_000,
            validation_checks=1_000_000,
            wall_ms=120_000,
        ),
    )


def test_first_net_cannot_reserve_away_later_net_search(tmp_path: Path) -> None:
    prepared, settings = _prepared(tmp_path, FIXTURE.read_bytes())
    assert [net.name for net in prepared.snapshot.content.nets] == ["POWER", "TREE"]
    slot = _slot(prepared)

    routed = route_targets(prepared, prepared.source, prepared.snapshot, settings, slot)

    assert routed.connected_targets == prepared.target_net_refs
    assert slot.usage.route_attempts == 6
    assert slot.routing_checks <= slot.budget.routing_checks


def test_connected_net_releases_its_skipped_search_units(tmp_path: Path, monkeypatch) -> None:
    source = (
        FIXTURE.read_bytes().rstrip()[:-1]
        + b"""\n
      (via (at 20 17.5) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu")
        (net "POWER") (uuid "83000000-0000-0000-0000-000000000001"))\n)\n"""
    )
    prepared, settings = _prepared(tmp_path, source)
    actual_tree = routing.LayeredTreeRouter.propose
    tree_checks: list[int] = []

    def observe_tree(router, snapshot, request, **kwargs):
        tree_checks.append(request.settings.max_obstacle_checks)
        return actual_tree(router, snapshot, request, **kwargs)

    monkeypatch.setattr(routing.LayeredTreeRouter, "propose", observe_tree)
    slot = _slot(prepared)

    routed = route_targets(prepared, prepared.source, prepared.snapshot, settings, slot)

    assert routed.connected_targets == prepared.target_net_refs
    assert tree_checks and tree_checks[0] > 400_000
    assert slot.usage.route_attempts == 4


def test_failed_common_layer_leaves_its_layered_fallback_budget(
    tmp_path: Path, monkeypatch
) -> None:
    prepared, settings = _prepared(tmp_path, _source_contact_case(("F", "F", "F"), (8, 12, 16)))
    attempted_checks: list[int] = []

    def fail_common_layer(_router, _snapshot, request, **_kwargs):
        attempted_checks.append(request.settings.max_obstacle_checks)
        return RouteResult(
            diagnostic=RouteDiagnostic(RouteFailureCode.NO_PATH, "forced common-layer miss")
        )

    monkeypatch.setattr("copper_mcp.optimization.routing.AStarRouter.propose", fail_common_layer)
    slot = _slot(prepared)

    routed = route_targets(prepared, prepared.source, prepared.snapshot, settings, slot)

    assert attempted_checks and routed.connected_targets == prepared.target_net_refs
    assert slot.usage.route_attempts == 8
    assert slot.routing_checks <= slot.budget.routing_checks
