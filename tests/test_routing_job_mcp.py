from __future__ import annotations

import asyncio
import hashlib
import sqlite3
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
from copper_mcp.routing_job_service import (
    cancel_routing_job as cancel_routing_job_service,
)
from copper_mcp.routing_job_service import (
    export_routing_candidate as export_routing_candidate_service,
)
from copper_mcp.routing_job_service import (
    get_routing_job as get_routing_job_service,
)
from copper_mcp.routing_job_service import (
    start_routing_job as start_routing_job_service,
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


@pytest.mark.parametrize(
    "raw_request",
    (
        "SECRET_ROUTING_JOB_SCALAR",
        {"private_payload": "SECRET_ROUTING_JOB_NESTED"},
    ),
)
def test_job_mcp_routes_malformed_private_requests_through_service_refusal(
    tmp_path: Path,
    raw_request: object,
) -> None:
    """Nested job values must not reach Pydantic's echoing argument-validation path."""

    settings, _, authorization = _workspace(tmp_path)
    repository = RoutingJobRepository(tmp_path / "jobs.sqlite3")
    try:
        with patch.object(server, "_SETTINGS", settings):
            with patch.object(server, "_routing_repository", return_value=repository):
                with pytest.raises(ToolError) as caught:
                    asyncio.run(
                        mcp.call_tool(
                            "start_routing",
                            {"request": raw_request, "authorization_digest": authorization},
                        )
                    )
        assert str(caught.value) == (
            "Error executing tool start_routing: routing job request was refused"
        )
        assert "SECRET_ROUTING_JOB" not in repr(caught.value)
        assert isinstance(caught.value.__cause__, ToolError)
        assert isinstance(caught.value.__cause__.__cause__, RoutingJobServiceError)
    finally:
        repository.close()


def test_job_start_advertises_closed_outer_and_nested_request_schemas() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    schema = tools["start_routing"].input_schema
    assert schema["additionalProperties"] is False
    request_schema = schema["properties"]["request"]
    assert request_schema["additionalProperties"] is False


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


def test_service_malformed_job_id_commits_expired_request_cleanup(tmp_path: Path) -> None:
    """Malformed service handles reach the repository's purge-first lookup boundary."""

    settings, request, authorization = _workspace(tmp_path)
    path = tmp_path / "malformed-service-job.sqlite3"
    repository = RoutingJobRepository(path, ttl_ms=10)
    try:
        with patch("copper_mcp.routing.job_repository._now_ms", return_value=100):
            started = start_routing_job_service(
                {"request": request, "authorization_digest": authorization},
                settings,
                repository,
            )

        with patch("copper_mcp.routing.job_repository._now_ms", return_value=110):
            with pytest.raises(RoutingJobServiceError, match="routing job is unavailable"):
                get_routing_job_service(
                    {"job_id": "malformed-job-id", "authorization_digest": authorization},
                    repository,
                )

        with sqlite3.connect(path) as connection:
            retained = connection.execute(
                "SELECT request_json FROM routing_job_requests WHERE job_id = ?",
                (started["job_id"],),
            ).fetchone()
        assert retained is None
    finally:
        repository.close()


def test_invalid_cancellation_reason_commits_expired_lifecycle_cleanup(tmp_path: Path) -> None:
    """Optional cancellation text cannot bypass the repository retention boundary."""

    settings, request, authorization = _workspace(tmp_path)
    path = tmp_path / "invalid-cancel-reason-retention.sqlite3"
    repository = RoutingJobRepository(path, ttl_ms=10)
    try:
        with patch("copper_mcp.routing.job_repository._now_ms", return_value=100):
            started = start_routing_job_service(
                {"request": request, "authorization_digest": authorization},
                settings,
                repository,
            )

        expired_job_id = _digest(b"expired-lifecycle")
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO routing_job_requests(job_id, request_digest, authorization_digest, "
                "created_at_ms, updated_at_ms, expires_at_ms, request_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    expired_job_id,
                    _digest(b"expired-request"),
                    authorization,
                    90,
                    90,
                    104,
                    sqlite3.Binary(b'{"private":"expired"}'),
                ),
            )
            connection.execute(
                "INSERT INTO routing_jobs(job_id, status, revision, created_at_ms, updated_at_ms, "
                "expires_at_ms, record_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    expired_job_id,
                    "queued",
                    0,
                    90,
                    90,
                    104,
                    sqlite3.Binary(b'{"private":"expired"}'),
                ),
            )

        with patch("copper_mcp.routing.job_repository._now_ms", return_value=105):
            with pytest.raises(RoutingJobServiceError, match="cancellation reason is malformed"):
                cancel_routing_job_service(
                    {
                        "job_id": started["job_id"],
                        "authorization_digest": authorization,
                        "reason": "",
                    },
                    repository,
                )

        with sqlite3.connect(path) as connection:
            request_row = connection.execute(
                "SELECT job_id FROM routing_job_requests WHERE job_id = ?", (expired_job_id,)
            ).fetchone()
            lifecycle_row = connection.execute(
                "SELECT job_id FROM routing_jobs WHERE job_id = ?", (expired_job_id,)
            ).fetchone()
        assert request_row is None
        assert lifecycle_row is None
    finally:
        repository.close()


def test_service_malformed_candidate_id_commits_expired_geometry_cleanup(tmp_path: Path) -> None:
    """Malformed candidate handles reach export retention before the service refuses."""

    settings, request, authorization = _workspace(tmp_path)
    path = tmp_path / "malformed-service-export.sqlite3"
    repository = RoutingJobRepository(path, ttl_ms=10)
    try:
        with patch("copper_mcp.routing.job_repository._now_ms", return_value=100):
            started = start_routing_job_service(
                {"request": request, "authorization_digest": authorization},
                settings,
                repository,
            )
        with (
            patch("copper_mcp.routing.job_repository._now_ms", return_value=102),
            patch("copper_mcp.routing.jobs._store_clock_ms", return_value=102),
            patch("copper_mcp.routing.job_worker._wall_clock_ms", return_value=102),
        ):
            completed = execute_routing_job(
                str(started["job_id"]), authorization, settings, repository
            )
        assert completed.candidate_id is not None

        with patch("copper_mcp.routing.job_repository._now_ms", return_value=112):
            with pytest.raises(
                RoutingJobServiceError,
                match="routing candidate export is unavailable",
            ):
                export_routing_candidate_service(
                    {
                        "job_id": started["job_id"],
                        "candidate_id": "malformed-candidate-id",
                        "authorization_digest": authorization,
                    },
                    repository,
                )

        with sqlite3.connect(path) as connection:
            retained = connection.execute(
                "SELECT candidate_json FROM routing_candidate_exports WHERE candidate_id = ?",
                (completed.candidate_id,),
            ).fetchone()
        assert retained is None
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("tool_name", "handle_fields", "diagnostic"),
    (
        (
            "get_routing_job",
            {"job_id": "malformed-job-id"},
            "routing job is unavailable",
        ),
        (
            "get_routing_job",
            {"authorization_digest": {"malformed": "authorization"}},
            "routing job is unavailable",
        ),
        (
            "cancel_routing_job",
            {"job_id": "malformed-job-id"},
            "routing job cancellation was refused",
        ),
        (
            "cancel_routing_job",
            {"authorization_digest": ["malformed", "authorization"]},
            "routing job cancellation was refused",
        ),
    ),
)
def test_mcp_malformed_job_lookup_handles_commit_expired_request_cleanup(
    tmp_path: Path,
    tool_name: str,
    handle_fields: dict[str, object],
    diagnostic: str,
) -> None:
    """MCP transport validation cannot bypass request-store TTL retention cleanup."""

    settings, request, authorization = _workspace(tmp_path)
    path = tmp_path / f"malformed-mcp-{tool_name}-{len(handle_fields)}.sqlite3"
    repository = RoutingJobRepository(path, ttl_ms=10)
    try:
        with patch("copper_mcp.routing.job_repository._now_ms", return_value=100):
            started = start_routing_job_service(
                {"request": request, "authorization_digest": authorization},
                settings,
                repository,
            )
        payload: dict[str, object] = {
            "job_id": started["job_id"],
            "authorization_digest": authorization,
        }
        payload.update(handle_fields)
        with (
            patch.object(server, "_routing_repository", return_value=repository),
            patch("copper_mcp.routing.job_repository._now_ms", return_value=110),
            pytest.raises(ToolError) as caught,
        ):
            asyncio.run(mcp.call_tool(tool_name, payload))
        assert str(caught.value).endswith(diagnostic)

        with sqlite3.connect(path) as connection:
            retained = connection.execute(
                "SELECT request_json FROM routing_job_requests WHERE job_id = ?",
                (started["job_id"],),
            ).fetchone()
        assert retained is None
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("handle_fields",),
    (
        ({"job_id": "malformed-job-id"},),
        ({"candidate_id": "malformed-candidate-id"},),
        ({"authorization_digest": {"malformed": "authorization"}},),
    ),
)
def test_mcp_malformed_export_handles_commit_expired_geometry_cleanup(
    tmp_path: Path,
    handle_fields: dict[str, object],
) -> None:
    """Malformed MCP export handles reach the geometry store before refusal."""

    settings, request, authorization = _workspace(tmp_path)
    path = tmp_path / f"malformed-mcp-export-{next(iter(handle_fields))}.sqlite3"
    repository = RoutingJobRepository(path, ttl_ms=10)
    try:
        with patch("copper_mcp.routing.job_repository._now_ms", return_value=100):
            started = start_routing_job_service(
                {"request": request, "authorization_digest": authorization},
                settings,
                repository,
            )
        with (
            patch("copper_mcp.routing.job_repository._now_ms", return_value=102),
            patch("copper_mcp.routing.jobs._store_clock_ms", return_value=102),
            patch("copper_mcp.routing.job_worker._wall_clock_ms", return_value=102),
        ):
            completed = execute_routing_job(
                str(started["job_id"]), authorization, settings, repository
            )
        assert completed.candidate_id is not None
        payload: dict[str, object] = {
            "job_id": started["job_id"],
            "candidate_id": completed.candidate_id,
            "authorization_digest": authorization,
        }
        payload.update(handle_fields)
        with (
            patch.object(server, "_routing_repository", return_value=repository),
            patch("copper_mcp.routing.job_repository._now_ms", return_value=112),
            pytest.raises(ToolError) as caught,
        ):
            asyncio.run(mcp.call_tool("export_routing_candidate", payload))
        assert str(caught.value).endswith("routing candidate export is unavailable")

        with sqlite3.connect(path) as connection:
            retained = connection.execute(
                "SELECT candidate_json FROM routing_candidate_exports WHERE candidate_id = ?",
                (completed.candidate_id,),
            ).fetchone()
        assert retained is None
    finally:
        repository.close()
