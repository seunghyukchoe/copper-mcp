"""Real MCP dispatch, isolated optimization and KiCad over an owned four-layer board.

No optimizer, isolated worker or native authority is replaced. This uses the SDK's direct
MCP transport, not an asserted stdio or genuine-human-consent test. The owned development
fixture is not held-out board evidence or physical calibration.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import anyio
import pytest
from mcp import types
from test_optimization_mcp import modern_wire_call

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import run_board_drc
from copper_mcp.mcp_server import CopperMCPServer
from copper_mcp.optimization.mcp import register_optimization_tools
from copper_mcp.request_boundary import net_class_constraints

_CONFIGURED_CLI = os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")
_FIXTURE = Path(__file__).parent / "fixtures/route-candidate/layered-tree-ordinary-4layer.kicad_pcb"


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CONFIGURED_CLI, reason="requires explicitly configured real KiCad")
@pytest.mark.parametrize(
    "version,movement",
    [("optimization/v1", False), ("optimization/v2", False), ("optimization/v2", True)],
    ids=["v1-routing", "v2-routing", "v2-placement-and-routing"],
)
def test_mcp_routes_complete_multilayer_tree_and_exports_without_apply(tmp_path, version, movement):
    source = _FIXTURE.read_bytes()
    assert source.count(b'(net "POWER")') == 2
    source = source.replace(b'(net "POWER")', b'(net "BLOCKER_F")', 1).replace(
        b'(net "POWER")', b'(net "BLOCKER_B")', 1
    )
    board = tmp_path / "board.kicad_pcb"
    board.write_bytes(source)
    project = board.with_suffix(".kicad_pro")
    project_source = None
    if version == "optimization/v1":
        # V1 preserves operator-supplied validation semantics. Its positive control must
        # explicitly enable checks that stock KiCad otherwise leaves suppressed.
        project_source = json.dumps(
            {
                "meta": {"version": 1},
                "board": {
                    "design_settings": {
                        "rule_severities": dict.fromkeys(
                            (
                                "missing_courtyard",
                                "track_not_centered_on_via",
                                "tuning_profile_track_geometries",
                                "footprint_filters_mismatch",
                                "footprint_type_mismatch",
                            ),
                            "warning",
                        )
                    }
                },
            }
        ).encode()
        project.write_bytes(project_source)
    before = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
    constraints = {
        "clearance_nm": 250_000,
        "track_width_nm": 250_000,
        "via_diameter_nm": 800_000,
        "via_drill_nm": 400_000,
    }
    net_class = net_class_constraints(constraints)
    converted = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert converted.snapshot is not None and not converted.diagnostics
    snapshot = converted.snapshot
    target = next(net.id for net in snapshot.content.nets if net.name == "TREE")
    assert sum(pad.net_id == target for pad in snapshot.content.pads) == 3
    assert not snapshot.content.segments and not snapshot.content.vias
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=Path(_CONFIGURED_CLI),
        max_route_preview_seconds=120,
        optimization_host_confirmation=False,
    )
    assert run_board_drc(board.name, settings).unconnected_count > 0
    launch = {
        "board": board.name,
        "expect_board_revision": "sha256:" + hashlib.sha256(source).hexdigest(),
        "expect_snapshot_digest": snapshot.snapshot_digest,
        "constraints": constraints,
        "target_net_refs": [target],
        "routing_settings": {
            "grid_step_nm": 1_000_000,
            "max_grid_nodes": 10_000,
            "max_expansions": 50_000,
            "max_obstacles": 64,
            # Proposal and mandatory replay reserve this allowance twice. Leave the
            # unchanged global ceiling room for actual post-route scoring work.
            "max_obstacle_checks": 100_000,
        },
        "limits": {
            "max_runtime_ms": 120_000,
            "max_candidates": 1,
            "max_placement_evaluations": 8,
            "max_route_attempts": 16,
            "max_repair_rounds": 0,
            "max_expansions": 100_000,
            "max_obstacle_checks": 1_000_000,
            "max_external_output_bytes": 2_097_152,
        },
    }
    if version == "optimization/v2":
        launch["schema_version"] = version
    if movement:
        movable = next(
            footprint.id
            for footprint in snapshot.content.footprints
            if footprint.origin.x == 30_000_000 and footprint.origin.y == 25_000_000
        )
        (tmp_path / "placement.json").write_text(
            json.dumps({"proposals": [{"subject": movable, "offset_y_nm": -1_000_000}]})
        )
        launch.update(
            movable_footprint_refs=[movable],
            placement_intent_path="placement.json",
            placement_grid_nm=1_000_000,
        )
        launch["limits"]["max_candidates"] = 2
    server = CopperMCPServer(name="Owned native optimization workflow")
    gateway = register_optimization_tools(server, lambda: settings)

    async def call(name, request):
        result = await modern_wire_call(server, name, request, advertise_elicitation=False)
        assert isinstance(result, types.CallToolResult) and not result.is_error
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    async def exercise():
        with anyio.fail_after(180):
            started = await call("start_optimization", launch)
            job_id = started["record"]["job_id"]
            # Wait for actual parent delivery validation, not merely the child's published
            # SQLite status. This observes the real future; it never substitutes a worker.
            service, _owner = gateway.service()
            await asyncio.to_thread(service._jobs[job_id].future.result, timeout=150)
            status = await call("get_optimization_job", {"job_id": job_id})
            record = status["record"]
            assert record["status"] == "awaiting_approval", (
                record.get("failure_code"),
                [
                    (row["domain"], row["status"], row["reason"])
                    for view in status["judge_reports"]
                    for row in view["report"]["domains"]
                    if row["domain"] in view["report"]["required_domains"]
                ],
            )
            assert status["apply_authority"] == "none"
            exported = await call(
                "export_optimization_package",
                {
                    "job_id": job_id,
                    "expected_record_revision": record["revision"],
                    "expected_package_digest": record["package_digest"],
                    "include_geometry": False,
                },
            )
            assert exported["package_digest"] == record["package_digest"]
            assert exported["geometry_disclosure"] == "not_disclosed"
            assert exported["artifact_uri"] is None
            assert exported["required_status"] == "pass"
            assert exported["aggregate_status"] == "inconclusive"
            package = exported["package"]
            assert package["schema_version"] == version
            assert package["binding"]["board_revision"] == launch["expect_board_revision"]
            assert package["metrics"]["fully_connected_target_nets"] == 1
            assert package["metrics"]["hard_drc_errors"] == 0
            assert package["metrics"]["via_count"] > 0
            if movement:
                comparison = package["comparison"]
                outcomes = comparison["outcomes"]
                assert len(outcomes) == 2
                assert outcomes[0]["role"] == "identity"
                assert all(row["status"] == "reviewable" for row in outcomes)
                assert package["metrics"]["displacement_nm"] > 0
                assert (
                    package["metrics"]["copper_length_nm"]
                    < outcomes[0]["metrics"]["copper_length_nm"]
                )
            assert not gateway._artifacts

    try:
        asyncio.run(exercise())
    finally:
        if gateway._service is not None:
            gateway._service.close()
            gateway._service.repository.close()
    assert before == (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
    if project_source is None:
        assert not project.exists()
    else:
        assert project.read_bytes() == project_source
