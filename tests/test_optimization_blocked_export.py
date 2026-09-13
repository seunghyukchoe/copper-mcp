"""Failed v2 jobs export terminal evidence, never an approvable/disclosable candidate."""

import asyncio
import json

import pytest
from mcp import types
from mcp.server.mcpserver.context import Context
from test_optimization_inputs import launch as launch
from test_optimization_mcp import modern_wire_call
from test_optimization_workflow_v2 import _synthetic_cli

from copper_mcp.config import Settings
from copper_mcp.mcp_server import CopperMCPServer
from copper_mcp.optimization.lifecycle import BlockedEvaluationV2
from copper_mcp.optimization.mcp import register_optimization_tools


@pytest.mark.parametrize("failure", ["backend", "required-domain"])
def test_failed_mcp_job_exports_immutable_blocked_report_after_restart(
    launch, tmp_path, monkeypatch, failure
):
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=_synthetic_cli(tmp_path),
        max_route_preview_seconds=120,
        optimization_host_confirmation=True,
    )
    server = CopperMCPServer(name="Owned blocked optimization workflow")
    gateway = register_optimization_tools(server, lambda: settings)
    request = {**launch, "schema_version": "optimization/v2"}
    if failure == "backend":
        request["allowed_backends"] = ["freerouting-dsn-ses-v1"]
    else:
        request["required_domains"] = ["SI"]
    original = (tmp_path / launch["board"]).read_bytes()

    async def forbidden_prompt(*_args, **_kwargs):
        pytest.fail("blocked evaluation must not request human consent")

    monkeypatch.setattr(Context, "elicit", forbidden_prompt)

    async def call(name, payload, *, error=False):
        result = await modern_wire_call(server, name, payload, advertise_elicitation=True)
        assert isinstance(result, types.CallToolResult)
        assert result.is_error is error
        return result.structured_content

    async def exercise():
        started = await call("start_optimization", request)
        job = started["record"]["job_id"]
        service, owner = gateway.service()
        await asyncio.to_thread(service._jobs[job].future.result, timeout=150)
        status = await call("get_optimization_job", {"job_id": job})
        assert status["record"]["status"] == (
            "backend_failure" if failure == "backend" else "failed"
        )
        digest = status["blocked_evaluation_digest"]
        assert digest is not None and status["record"]["package_digest"] is None
        export = {
            "job_id": job,
            "expected_record_revision": status["record"]["revision"],
            "expected_package_digest": digest,
        }
        first = await call("export_optimization_package", export)
        package = BlockedEvaluationV2.model_validate_json(json.dumps(first["package"]))
        assert package.digest == digest == first["package_digest"]
        assert first["status"] == package.status == "blocked"
        assert package.evidence_scope == "terminal-metadata-only"
        assert package.record == service.repository.get(job, owner)
        assert package.apply_authority == package.approval_authority == "none"
        assert package.geometry_disclosure == "not_disclosed"
        expected = "backend_failure" if failure == "backend" else "required_domain_inconclusive"
        assert expected in package.blocking_codes
        assert "artifact_uri" not in first and not gateway._artifacts
        await call("export_optimization_package", {**export, "include_geometry": True}, error=True)
        await call(
            "approve_optimization_job",
            {**export, "expected_judge_digest": "sha256:" + "a" * 64},
            error=True,
        )
        await call(
            "export_optimization_package",
            {**export, "expected_package_digest": "sha256:" + "b" * 64},
            error=True,
        )
        await call(
            "export_optimization_package", {**export, "expected_record_revision": 0}, error=True
        )
        with pytest.raises(ValueError):
            service.export_blocked(
                job, "sha256:" + "c" * 64, export["expected_record_revision"], digest
            )
        # Drop every private candidate/job handle and reopen the same persisted owner-bound store.
        service.close()
        service.repository.close()
        gateway._service = None
        reopened = await call("get_optimization_job", {"job_id": job})
        assert reopened["blocked_evaluation_digest"] == digest
        assert await call("export_optimization_package", export) == first
        with pytest.raises(ValueError):
            BlockedEvaluationV2.model_validate_json(
                json.dumps({**first["package"], "blocking_codes": ["invalid_candidate"]})
            )

    try:
        asyncio.run(exercise())
    finally:
        if gateway._service is not None:
            gateway._service.close()
            gateway._service.repository.close()
    assert (tmp_path / launch["board"]).read_bytes() == original


def test_v1_failed_job_does_not_gain_blocked_export(launch, tmp_path):
    settings = Settings(workspace=tmp_path, max_route_preview_seconds=120)
    server = CopperMCPServer(name="V1 refusal remains unchanged")
    gateway = register_optimization_tools(server, lambda: settings)
    value = {**launch, "allowed_backends": ["freerouting-dsn-ses-v1"]}

    async def exercise():
        result = await modern_wire_call(server, "start_optimization", value)
        job = result.structured_content["record"]["job_id"]
        service, _ = gateway.service()
        await asyncio.to_thread(service._jobs[job].future.result, timeout=150)
        result = await modern_wire_call(server, "get_optimization_job", {"job_id": job})
        assert "blocked_evaluation_digest" not in result.structured_content
        refused = await modern_wire_call(
            server,
            "export_optimization_package",
            {
                "job_id": job,
                "expected_record_revision": result.structured_content["record"]["revision"],
                "expected_package_digest": "sha256:" + "a" * 64,
            },
        )
        assert isinstance(refused, types.CallToolResult) and refused.is_error

    try:
        asyncio.run(exercise())
    finally:
        if gateway._service is not None:
            gateway._service.close()
            gateway._service.repository.close()
