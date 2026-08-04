from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import copper_mcp.mcp_server as server
from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.mcp_server import mcp
from copper_mcp.routing import RoutingJobRepository
from copper_mcp.routing_job_service import (
    RoutingJobServiceError,
    _prepare_layered_job,
    execute_routing_job,
)

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _workspace(tmp_path: Path) -> tuple[Settings, dict[str, object], str]:
    board = tmp_path / FIXTURE.name
    source = FIXTURE.read_bytes()
    board.write_bytes(source)
    constraints = NetClass(
        id="class:request",
        name="Request",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    conversion = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(constraints,), default_net_class_id=constraints.id),
    )
    assert conversion.snapshot is not None
    pads = conversion.snapshot.content.pads
    request: dict[str, object] = {
        "board": board.name,
        "start_pad_id": pads[0].id,
        "end_pad_id": pads[1].id,
        "expect_board_revision": _digest(source),
        "expect_snapshot_digest": conversion.snapshot.snapshot_digest,
        "constraints": {
            "clearance_nm": constraints.clearance_nm,
            "track_width_nm": constraints.track_width_nm,
            "via_diameter_nm": constraints.via_diameter_nm,
            "via_drill_nm": constraints.via_drill_nm,
        },
        "grid_step_nm": 250_000,
        "seed": 0,
        "settings": {
            "move_cost": 1,
            "via_cost": 10,
            "max_expansions": 100_000,
            "max_nodes": 250_000,
            "max_obstacles": 256,
            "max_obstacle_checks": 2_000_000,
        },
    }
    return Settings(workspace=tmp_path), request, _digest(b"caller-context")


def test_job_tools_are_closed_and_context_bound(tmp_path: Path) -> None:
    settings, request, authorization = _workspace(tmp_path)
    repository = RoutingJobRepository(tmp_path / "jobs.sqlite3")
    try:
        with patch.object(server, "_SETTINGS", settings):
            with patch.object(server, "_routing_repository", return_value=repository):
                with patch.object(server, "_schedule_routing_job"):
                    started = asyncio.run(
                        mcp.call_tool(
                            "start_routing",
                            {"request": request, "authorization_digest": authorization},
                        )
                    )
                    assert started.structured_content is not None
                    job_id = started.structured_content["job_id"]
                    assert started.structured_content["status"] == "queued"

                    looked_up = asyncio.run(
                        mcp.call_tool(
                            "get_routing_job",
                            {"job_id": job_id, "authorization_digest": authorization},
                        )
                    )
                    assert looked_up.structured_content["request"]["board"] == request["board"]
                    assert looked_up.structured_content["status"] == "queued"

                    cancelled = asyncio.run(
                        mcp.call_tool(
                            "cancel_routing_job",
                            {
                                "job_id": job_id,
                                "authorization_digest": authorization,
                                "reason": "operator_stop",
                            },
                        )
                    )
                    assert cancelled.structured_content["status"] == "cancelled"

                    with pytest.raises(ToolError):
                        asyncio.run(
                            mcp.call_tool(
                                "get_routing_job",
                                {"job_id": job_id, "authorization_digest": _digest(b"wrong")},
                            )
                        )
    finally:
        repository.close()


def test_job_request_cannot_silently_accept_layered_drc_opt_in(tmp_path: Path) -> None:
    settings, request, authorization = _workspace(tmp_path)
    request["include_drc"] = True
    repository = RoutingJobRepository(tmp_path / "jobs.sqlite3")
    try:
        with patch.object(server, "_SETTINGS", settings):
            with patch.object(server, "_routing_repository", return_value=repository):
                with patch.object(server, "_schedule_routing_job"):
                    with pytest.raises(ToolError):
                        asyncio.run(
                            mcp.call_tool(
                                "start_routing",
                                {"request": request, "authorization_digest": authorization},
                            )
                        )
    finally:
        repository.close()


def test_direct_job_preparation_rejects_layered_drc_opt_in(tmp_path: Path) -> None:
    settings, request, _ = _workspace(tmp_path)
    request["include_drc"] = True

    with pytest.raises(
        RoutingJobServiceError,
        match="cannot request authoritative DRC evidence",
    ):
        _prepare_layered_job(request, settings)


def test_job_worker_persists_result_and_explicit_geometry_export(tmp_path: Path) -> None:
    settings, request, authorization = _workspace(tmp_path)
    repository = RoutingJobRepository(tmp_path / "jobs.sqlite3")
    try:
        with patch.object(server, "_SETTINGS", settings):
            with patch.object(server, "_routing_repository", return_value=repository):
                with patch.object(server, "_schedule_routing_job"):
                    started = asyncio.run(
                        mcp.call_tool(
                            "start_routing",
                            {"request": request, "authorization_digest": authorization},
                        )
                    )
        job_id = started.structured_content["job_id"]
        completed = execute_routing_job(job_id, authorization, settings, repository)
        assert completed.status.value == "completed"
        assert completed.candidate_id is not None

        with patch.object(server, "_routing_repository", return_value=repository):
            exported = asyncio.run(
                mcp.call_tool(
                    "export_routing_candidate",
                    {
                        "job_id": job_id,
                        "candidate_id": completed.candidate_id,
                        "authorization_digest": authorization,
                    },
                )
            )
        assert exported.structured_content["geometry_disclosure"] == "explicitly_authorized"
        assert exported.structured_content["candidate"]["candidate_id"] == completed.candidate_id
    finally:
        repository.close()
