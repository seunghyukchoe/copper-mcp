"""MCP dispatch, host-only confirmation, and non-echoing optimization arguments."""

import asyncio
from concurrent.futures import Future
from dataclasses import replace

import pytest
from mcp.server.elicitation import AcceptedElicitation, DeclinedElicitation
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from test_optimization_coordinator import synthetic_authority as synthetic_authority
from test_optimization_inputs import launch as launch

from copper_mcp.config import Settings
from copper_mcp.mcp_server import CopperMCPServer
from copper_mcp.optimization.contracts import OptimizationError
from copper_mcp.optimization.mcp import HumanDecision, register_optimization_tools


@pytest.mark.parametrize("delivery", ["pending", "missing", "corrupt", "failed"])
def test_published_metadata_is_not_reviewable_until_parent_validates_delivery(
    app, launch, synthetic_authority, monkeypatch, delivery
):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    service, _owner = gateway.service()
    private = service._jobs[record["job_id"]]
    if delivery == "pending":
        private.future = Future()
    elif delivery == "missing":
        private.source = None
    elif delivery == "corrupt":
        private.source = b"not the judged board"
    else:
        private.future = Future()
        private.future.set_exception(ValueError("private failure"))

    async def forbidden_prompt(*_args, **_kwargs):
        raise AssertionError("unvalidated delivery must not prompt for approval or disclosure")

    monkeypatch.setattr(Context, "elicit", forbidden_prompt)
    common = {
        "job_id": record["job_id"],
        "expected_record_revision": record["revision"],
        "expected_package_digest": record["package_digest"],
    }
    with pytest.raises(ToolError):
        call(
            server,
            "approve_optimization_job",
            {
                **common,
                "expected_judge_digest": record["judge_digest"],
            },
        )
    with pytest.raises(ToolError):
        call(server, "export_optimization_package", {**common, "include_geometry": True})


@pytest.mark.parametrize("operation", ["approve_optimization_job", "export_optimization_package"])
def test_delivery_is_rechecked_after_host_confirmation(
    app, launch, synthetic_authority, monkeypatch, operation
):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    service, _owner = gateway.service()

    async def accept_after_expiry(_ctx, _message, _schema):
        service._jobs[record["job_id"]].source = None
        return AcceptedElicitation(data=HumanDecision(decision="approve"))

    monkeypatch.setattr(Context, "elicit", accept_after_expiry)
    request = {
        "job_id": record["job_id"],
        "expected_record_revision": record["revision"],
        "expected_package_digest": record["package_digest"],
    }
    if operation == "approve_optimization_job":
        request["expected_judge_digest"] = record["judge_digest"]
    else:
        request["include_geometry"] = True
    with pytest.raises(ToolError):
        call(server, operation, request)
    assert not gateway._artifacts
    assert service.repository.get(record["job_id"], _owner).status == "awaiting_approval"


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        workspace=tmp_path, max_route_preview_seconds=120, optimization_host_confirmation=True
    )
    server = CopperMCPServer(name="Optimization test host")
    gateway = register_optimization_tools(server, lambda: settings)
    yield server, gateway
    if gateway._service is not None:
        gateway._service.close()
        gateway._service.repository.close()


def call(server, name, request):
    return asyncio.run(server.call_tool(name, {"request": request})).structured_content


def completed_search(app, launch):
    server, gateway = app
    started = call(server, "start_optimization", launch)
    job_id = started["record"]["job_id"]
    service, _owner = gateway.service()
    service._jobs[job_id].future.result(timeout=15)
    return call(server, "get_optimization_job", {"job_id": job_id})


def test_failed_private_publication_never_exposes_partial_judge_reports(
    app, launch, synthetic_authority
):
    _server, gateway = app
    service, _owner = gateway.service()
    original = service._job_runner
    observed_during_callback = []

    def reject_delivery(repository, job_id, prepared, owner, settings, payload, _retain, observe):
        def stage(report):
            observe(report)
            observed_during_callback.append(service.get(job_id, owner)[1])

        def refuse(_package, _source):
            raise OptimizationError("private retention capacity is exhausted")

        return original(repository, job_id, prepared, owner, settings, payload, refuse, stage)

    service._job_runner = reject_delivery
    result = completed_search(app, launch)
    assert result["record"]["status"] == "failed"
    assert result["judge_reports"] == []
    assert observed_during_callback and all(not reports for reports in observed_during_callback)
    assert service._jobs[result["record"]["job_id"]].source is None


def test_five_closed_tools_have_no_model_approval_capability(app):
    server, _gateway = app
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(tools) == {
        "start_optimization",
        "get_optimization_job",
        "cancel_optimization_job",
        "export_optimization_package",
        "approve_optimization_job",
    }
    for tool in tools.values():
        assert tool.input_schema["additionalProperties"] is False
        assert set(tool.input_schema["properties"]) == {"request"}
        assert tool.input_schema["properties"]["request"]["additionalProperties"] is False
    assert "human_confirmation_capability" not in str(
        tools["approve_optimization_job"].input_schema
    )


def test_host_decline_then_accept_preserves_unknown_domains_and_board(
    app, launch, synthetic_authority, monkeypatch, tmp_path
):
    server, _gateway = app
    source = (tmp_path / "board.kicad_pcb").read_bytes()
    observed = completed_search(app, launch)
    record = observed["record"]
    assert record["status"] == "awaiting_approval"
    request = {
        "job_id": record["job_id"],
        "expected_record_revision": record["revision"],
        "expected_package_digest": record["package_digest"],
        "expected_judge_digest": record["judge_digest"],
    }
    prompts = []

    async def decline(_context, message, schema):
        prompts.append(message)
        assert schema is HumanDecision
        return DeclinedElicitation()

    monkeypatch.setattr(Context, "elicit", decline)
    declined = call(server, "approve_optimization_job", request)
    assert declined["record"]["status"] == "awaiting_approval"

    async def accept(_context, message, schema):
        prompts.append(message)
        return AcceptedElicitation(data=HumanDecision(decision="approve"))

    monkeypatch.setattr(Context, "elicit", accept)
    approved = call(server, "approve_optimization_job", request)
    assert approved["record"]["status"] == "completed"
    exported = call(
        server,
        "export_optimization_package",
        {
            "job_id": record["job_id"],
            "expected_package_digest": record["package_digest"],
            "expected_record_revision": approved["record"]["revision"],
        },
    )
    assert exported["aggregate_status"] == "inconclusive"
    assert exported["required_status"] == "pass"
    assert exported["apply_authority"] == "none"
    assert exported["geometry_disclosure"] == "not_disclosed"
    assert all(record["package_digest"] in prompt for prompt in prompts)
    assert (tmp_path / "board.kicad_pcb").read_bytes() == source
    with pytest.raises(ToolError):
        call(server, "approve_optimization_job", request)


@pytest.mark.parametrize(
    "name",
    [
        "get_optimization_job",
        "cancel_optimization_job",
        "export_optimization_package",
        "approve_optimization_job",
    ],
)
def test_bad_handles_never_echo_private_arguments(app, name):
    with pytest.raises(ToolError) as error:
        call(app[0], name, {"job_id": {"PRIVATE-CANARY": "sensitive"}})
    assert "PRIVATE-CANARY" not in str(error.value)


def test_model_boolean_cannot_replace_host_confirmation(app):
    with pytest.raises(ToolError) as error:
        call(app[0], "approve_optimization_job", {"approved": True, "secret": "PRIVATE-CANARY"})
    assert "PRIVATE-CANARY" not in str(error.value)


def test_network_transport_cannot_claim_local_operator_owner(app):
    server, gateway = app
    settings = gateway._settings()
    gateway._settings = lambda: replace(settings, transport="streamable-http")
    with pytest.raises(ToolError):
        call(server, "get_optimization_job", {"job_id": "sha256:" + "0" * 64})


def test_candidate_resource_requires_disclosure_and_is_revoked_on_cancel(
    app, launch, synthetic_authority, monkeypatch
):
    server, gateway = app
    status = completed_search(app, launch)
    record = status["record"]
    prompts = []

    async def consent(_ctx, message, schema):
        prompts.append(message)
        return AcceptedElicitation(data=HumanDecision(decision="approve"))

    monkeypatch.setattr(Context, "elicit", consent)
    exported = call(
        server,
        "export_optimization_package",
        {
            "job_id": record["job_id"],
            "expected_record_revision": record["revision"],
            "expected_package_digest": record["package_digest"],
            "include_geometry": True,
        },
    )
    assert len(prompts) == 1 and "original design content" in prompts[0]
    assert exported["geometry_disclosure"] == "explicitly_authorized"
    assert exported["artifact_ttl_seconds"] == 300
    resource = asyncio.run(server.read_resource(exported["artifact_uri"]))
    assert resource[0].content == synthetic_authority[0]
    assert resource[0].mime_type == "application/octet-stream"
    call(
        server,
        "cancel_optimization_job",
        {"job_id": record["job_id"], "expected_record_revision": record["revision"]},
    )
    token = exported["artifact_uri"].split("/")[-2]
    with pytest.raises(ResourceError):
        gateway.read_artifact(token)
    assert token not in gateway._artifacts
