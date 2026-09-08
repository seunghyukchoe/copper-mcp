from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from test_layered_tree_router import FIXTURE

from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.optimization.inputs import prepare_optimization
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.routing import route_targets


class _Probe:
    def __init__(self) -> None:
        self.usage = ResourceUsage()

    def checkpoint(self):
        return SimpleNamespace(usage=self.usage)

    def reserve(self, usage: ResourceUsage) -> None:
        self.usage = self.usage.plus(usage)

    def cancelled(self) -> bool:
        return False


def test_optimization_routes_cross_layer_multipin_net_through_private_tree(
    tmp_path: Path,
) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / "tree.kicad_pcb"
    board.write_bytes(source)
    profile = KiCadConstraintProfile(
        net_classes=(
            NetClass(
                "class:request",
                "Request",
                clearance_nm=250_000,
                track_width_nm=250_000,
                via_diameter_nm=800_000,
                via_drill_nm=400_000,
            ),
        ),
        default_net_class_id="class:request",
    )
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None and not conversion.diagnostics
    snapshot = conversion.snapshot
    net_id = next(pad.net_id for pad in snapshot.content.pads if pad.net_id is not None)
    launch = {
        "board": board.name,
        "expect_board_revision": "sha256:" + hashlib.sha256(source).hexdigest(),
        "expect_snapshot_digest": snapshot.snapshot_digest,
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
        "target_net_refs": [net_id],
        "routing_settings": {
            "grid_step_nm": 1_000_000,
            "max_grid_nodes": 250_000,
            "max_expansions": 100_000,
            "max_obstacles": 256,
            "max_obstacle_checks": 2_000_000,
        },
    }
    settings = Settings(workspace=tmp_path)
    prepared = prepare_optimization(launch, settings)
    probe = _Probe()
    routed = route_targets(
        prepared,
        prepared.source,
        prepared.snapshot,
        settings,
        probe,  # type: ignore[arg-type]
    )
    assert routed.connected_targets == (net_id,)
    assert len(routed.candidate_ids) == 1
    assert routed.route_probes == 2
    assert routed.vias == 1
    assert source == board.read_bytes()
    reparsed = parse_kicad_bytes(routed.source, profile)
    assert reparsed.snapshot == routed.snapshot
    assert reparsed.snapshot is not None
    assert len(reparsed.snapshot.content.segments) >= 2
