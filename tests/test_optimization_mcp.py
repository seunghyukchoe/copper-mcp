"""MCP dispatch, host-only confirmation, and non-echoing optimization arguments."""

import asyncio
import hashlib
import json
from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace

import anyio
import pytest
from mcp import ClientSession, types
from mcp.server.elicitation import AcceptedElicitation, DeclinedElicitation
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.runner import modern_on_request
from mcp.shared.direct_dispatcher import create_direct_dispatcher_pair
from mcp.shared.exceptions import MCPError
from test_optimization_coordinator import synthetic_authority as synthetic_authority
from test_optimization_inputs import launch as launch

from copper_mcp.config import Settings
from copper_mcp.mcp_server import CopperMCPServer
from copper_mcp.optimization.contracts import OptimizationError
from copper_mcp.optimization.mcp import HumanDecision, register_optimization_tools


def test_review_deadline_failure_is_a_deliberate_non_echoing_refusal(
    app, launch, synthetic_authority, monkeypatch
):
    from copper_mcp.kicad_cli import KiCadCliError

    server, gateway = app
    allow_direct_elicitation(monkeypatch)
    record = completed_search(app, launch)["record"]
    service, _owner = gateway.service()

    async def accept(*_args, **_kwargs):
        return AcceptedElicitation(data=HumanDecision(decision="approve"))

    def exhausted(*_args, **_kwargs):
        raise KiCadCliError("owned-private-context-canary")

    monkeypatch.setattr(Context, "elicit", accept)
    monkeypatch.setattr(service, "approve_from_host", exhausted)
    with pytest.raises(ToolError) as caught:
        call(
            server,
            "approve_optimization_job",
            {
                "job_id": record["job_id"],
                "expected_record_revision": record["revision"],
                "expected_package_digest": record["package_digest"],
                "expected_judge_digest": record["judge_digest"],
            },
        )
    assert type(caught.value) is ToolError
    assert "canary" not in str(caught.value)


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
    allow_direct_elicitation(monkeypatch)
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


def allow_direct_elicitation(monkeypatch):
    monkeypatch.setattr(
        Context, "session", property(lambda _context: SimpleNamespace(can_send_request=False))
    )


def completed_search(app, launch):
    server, gateway = app
    started = call(server, "start_optimization", launch)
    job_id = started["record"]["job_id"]
    service, _owner = gateway.service()
    service._jobs[job_id].future.result(timeout=15)
    return call(server, "get_optimization_job", {"job_id": job_id})


async def modern_wire_call(
    server,
    name,
    request,
    *,
    input_responses=None,
    request_state=None,
    advertise_elicitation=True,
):
    client_dispatcher, server_dispatcher = create_direct_dispatcher_pair()

    async def should_not_backchannel(_context, _params):
        pytest.fail("2026 resolver elicitation must use InputRequiredResult")

    async def ignore_notification(_context, _method, _params):
        return None

    lowlevel = server._lowlevel_server
    async with lowlevel.lifespan(lowlevel) as lifespan_state:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(
                server_dispatcher.run,
                modern_on_request(lowlevel, lifespan_state),
                ignore_notification,
            )
            session_options = {"dispatcher": client_dispatcher}
            if advertise_elicitation:
                session_options["elicitation_callback"] = should_not_backchannel
            async with ClientSession(**session_options) as session:
                await session.discover()
                try:
                    result = await session.call_tool(
                        name,
                        {"request": request},
                        input_responses=input_responses,
                        request_state=request_state,
                        allow_input_required=True,
                    )
                except MCPError as error:
                    result = error
            server_dispatcher.close()
    return result


async def legacy_wire_call(server, name, request, response, *, advertise_elicitation=True):
    client_send, server_receive = anyio.create_memory_object_stream(0)
    server_send, client_receive = anyio.create_memory_object_stream(0)

    async def respond(_context, _params):
        if callable(response):
            return await response(_context, _params)
        return response

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(
            server._lowlevel_server.run,
            server_receive,
            server_send,
            server._lowlevel_server.create_initialization_options(),
            True,
        )
        session_options = {}
        if advertise_elicitation:
            session_options["elicitation_callback"] = respond
        async with ClientSession(client_receive, client_send, **session_options) as session:
            await session.initialize()
            try:
                result = await session.call_tool(name, {"request": request})
            except MCPError as error:
                result = error
        tasks.cancel_scope.cancel()
    return result


def test_current_wire_requests_geometry_consent_before_tool_body(app, launch, synthetic_authority):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    result = asyncio.run(
        modern_wire_call(
            server,
            "export_optimization_package",
            {
                "job_id": record["job_id"],
                "expected_record_revision": record["revision"],
                "expected_package_digest": record["package_digest"],
                "include_geometry": True,
            },
        )
    )
    assert isinstance(result, types.InputRequiredResult)
    assert result.input_requests and len(result.input_requests) == 1
    assert not gateway._artifacts


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_current_wire_resumes_exact_geometry_consent(app, launch, synthetic_authority, action):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    request = {
        "job_id": record["job_id"],
        "expected_record_revision": record["revision"],
        "expected_package_digest": record["package_digest"],
        "include_geometry": True,
    }
    pending = asyncio.run(modern_wire_call(server, "export_optimization_package", request))
    assert isinstance(pending, types.InputRequiredResult)
    assert pending.input_requests is not None
    key = next(iter(pending.input_requests))
    content = {"decision": "approve"} if action == "accept" else None
    resolved = asyncio.run(
        modern_wire_call(
            server,
            "export_optimization_package",
            request,
            input_responses={key: types.ElicitResult(action=action, content=content)},
            request_state=pending.request_state,
        )
    )
    assert isinstance(resolved, types.CallToolResult)
    if action == "accept":
        assert not resolved.is_error
        assert resolved.structured_content["geometry_disclosure"] == "explicitly_authorized"
        assert len(gateway._artifacts) == 1
    else:
        assert resolved.is_error
        assert not gateway._artifacts


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_legacy_wire_uses_standalone_elicitation(app, launch, synthetic_authority, action):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    response = types.ElicitResult(
        action=action,
        content={"decision": "approve"} if action == "accept" else None,
    )
    result = asyncio.run(
        legacy_wire_call(
            server,
            "export_optimization_package",
            {
                "job_id": record["job_id"],
                "expected_record_revision": record["revision"],
                "expected_package_digest": record["package_digest"],
                "include_geometry": True,
            },
            response,
        )
    )
    if action == "accept":
        assert not result.is_error
        assert result.structured_content["geometry_disclosure"] == "explicitly_authorized"
        assert len(gateway._artifacts) == 1
    else:
        assert result.is_error
        assert not gateway._artifacts


def test_current_wire_missing_elicitation_capability_refuses_before_disclosure(
    app, launch, synthetic_authority
):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    result = asyncio.run(
        modern_wire_call(
            server,
            "export_optimization_package",
            {
                "job_id": record["job_id"],
                "expected_record_revision": record["revision"],
                "expected_package_digest": record["package_digest"],
                "include_geometry": True,
            },
            advertise_elicitation=False,
        )
    )
    assert isinstance(result, MCPError)
    assert not gateway._artifacts


def test_legacy_wire_missing_elicitation_capability_refuses_before_disclosure(
    app, launch, synthetic_authority
):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    result = asyncio.run(
        legacy_wire_call(
            server,
            "export_optimization_package",
            {
                "job_id": record["job_id"],
                "expected_record_revision": record["revision"],
                "expected_package_digest": record["package_digest"],
                "include_geometry": True,
            },
            None,
            advertise_elicitation=False,
        )
    )
    assert isinstance(result, MCPError)
    assert not gateway._artifacts


def test_current_wire_consumes_consent_once(app, launch, synthetic_authority):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    request = {
        "job_id": record["job_id"],
        "expected_record_revision": record["revision"],
        "expected_package_digest": record["package_digest"],
        "include_geometry": True,
    }
    pending = asyncio.run(modern_wire_call(server, "export_optimization_package", request))
    assert isinstance(pending, types.InputRequiredResult)
    assert pending.input_requests is not None
    key = next(iter(pending.input_requests))
    response = {key: types.ElicitResult(action="accept", content={"decision": "approve"})}
    accepted = asyncio.run(
        modern_wire_call(
            server,
            "export_optimization_package",
            request,
            input_responses=response,
            request_state=pending.request_state,
        )
    )
    assert isinstance(accepted, types.CallToolResult) and not accepted.is_error
    replay = asyncio.run(
        modern_wire_call(
            server,
            "export_optimization_package",
            request,
            input_responses=response,
            request_state=pending.request_state,
        )
    )
    assert isinstance(replay, types.InputRequiredResult)
    assert len(gateway._artifacts) == 1


def test_current_wire_concurrent_replay_discloses_once(app, launch, synthetic_authority):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    request = {
        "job_id": record["job_id"],
        "expected_record_revision": record["revision"],
        "expected_package_digest": record["package_digest"],
        "include_geometry": True,
    }
    pending = asyncio.run(modern_wire_call(server, "export_optimization_package", request))
    assert isinstance(pending, types.InputRequiredResult)
    assert pending.input_requests is not None
    key = next(iter(pending.input_requests))
    response = {key: types.ElicitResult(action="accept", content={"decision": "approve"})}

    async def replay_twice():
        return await asyncio.gather(
            modern_wire_call(
                server,
                "export_optimization_package",
                request,
                input_responses=response,
                request_state=pending.request_state,
            ),
            modern_wire_call(
                server,
                "export_optimization_package",
                request,
                input_responses=response,
                request_state=pending.request_state,
            ),
        )

    results = asyncio.run(replay_twice())
    assert (
        sum(isinstance(result, types.CallToolResult) and not result.is_error for result in results)
        == 1
    )
    assert len(gateway._artifacts) == 1


@pytest.mark.parametrize("disruption", ["expiry", "restart"])
def test_current_wire_expired_or_restarted_challenge_is_not_authority(
    app, launch, synthetic_authority, disruption
):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    request = {
        "job_id": record["job_id"],
        "expected_record_revision": record["revision"],
        "expected_package_digest": record["package_digest"],
        "include_geometry": True,
    }
    pending = asyncio.run(modern_wire_call(server, "export_optimization_package", request))
    assert isinstance(pending, types.InputRequiredResult)
    assert pending.input_requests is not None
    key = next(iter(pending.input_requests))
    if disruption == "restart":
        gateway._consents = type(gateway._consents)()
    else:
        pending_key, pending_value = next(iter(gateway._consents._pending.items()))
        gateway._consents._pending[pending_key] = replace(pending_value, expires_at=0.0)
    retried = asyncio.run(
        modern_wire_call(
            server,
            "export_optimization_package",
            request,
            input_responses={
                key: types.ElicitResult(action="accept", content={"decision": "approve"})
            },
            request_state=pending.request_state,
        )
    )
    assert isinstance(retried, types.InputRequiredResult)
    assert not gateway._artifacts


def test_current_wire_metadata_export_never_requests_consent(app, launch, synthetic_authority):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    result = asyncio.run(
        modern_wire_call(
            server,
            "export_optimization_package",
            {
                "job_id": record["job_id"],
                "expected_record_revision": record["revision"],
                "expected_package_digest": record["package_digest"],
            },
            advertise_elicitation=False,
        )
    )
    assert isinstance(result, types.CallToolResult) and not result.is_error
    assert result.structured_content["package_digest"] == record["package_digest"]
    assert result.structured_content["geometry_disclosure"] == "not_disclosed"
    assert not gateway._artifacts


def test_current_wire_approval_accepts_review_without_apply(
    app, launch, synthetic_authority, tmp_path
):
    server, _gateway = app
    source = (tmp_path / "board.kicad_pcb").read_bytes()
    record = completed_search(app, launch)["record"]
    request = {
        "job_id": record["job_id"],
        "expected_record_revision": record["revision"],
        "expected_package_digest": record["package_digest"],
        "expected_judge_digest": record["judge_digest"],
    }
    pending = asyncio.run(modern_wire_call(server, "approve_optimization_job", request))
    assert isinstance(pending, types.InputRequiredResult)
    assert pending.input_requests is not None
    key = next(iter(pending.input_requests))
    approved = asyncio.run(
        modern_wire_call(
            server,
            "approve_optimization_job",
            request,
            input_responses={
                key: types.ElicitResult(action="accept", content={"decision": "approve"})
            },
            request_state=pending.request_state,
        )
    )
    assert isinstance(approved, types.CallToolResult) and not approved.is_error
    assert approved.structured_content["record"]["status"] == "completed"
    assert (tmp_path / "board.kicad_pcb").read_bytes() == source


def test_legacy_response_to_challenge_a_cannot_consume_replacement_b(
    app, launch, synthetic_authority
):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    request = {
        "job_id": record["job_id"],
        "expected_record_revision": record["revision"],
        "expected_package_digest": record["package_digest"],
        "include_geometry": True,
    }

    async def exercise_race():
        entered = anyio.Event()
        release = anyio.Event()
        results = []

        async def delayed_accept(_context, _params):
            entered.set()
            await release.wait()
            return types.ElicitResult(action="accept", content={"decision": "approve"})

        async def invoke():
            results.append(
                await legacy_wire_call(
                    server,
                    "export_optimization_package",
                    request,
                    delayed_accept,
                )
            )

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(invoke)
            await entered.wait()
            pending_key, pending_a = next(iter(gateway._consents._pending.items()))
            gateway._consents._pending[pending_key] = replace(pending_a, expires_at=0.0)
            challenge_b = gateway._consents.issue_challenge(pending_a.binding)
            release.set()
        return results[0], pending_a.binding, challenge_b

    result, replacement, challenge_b = asyncio.run(exercise_race())
    assert isinstance(result, types.CallToolResult) and result.is_error
    retained = next(iter(gateway._consents._pending.values()))
    assert retained.binding == replacement
    assert retained.challenge == challenge_b
    assert not gateway._artifacts


def test_current_wire_tampered_state_is_refused(app, launch, synthetic_authority):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    request = {
        "job_id": record["job_id"],
        "expected_record_revision": record["revision"],
        "expected_package_digest": record["package_digest"],
        "include_geometry": True,
    }
    pending = asyncio.run(modern_wire_call(server, "export_optimization_package", request))
    assert isinstance(pending, types.InputRequiredResult)
    assert pending.input_requests is not None and pending.request_state is not None
    key = next(iter(pending.input_requests))
    suffix = "A" if pending.request_state[-1] != "A" else "B"
    result = asyncio.run(
        modern_wire_call(
            server,
            "export_optimization_package",
            request,
            input_responses={
                key: types.ElicitResult(action="accept", content={"decision": "approve"})
            },
            request_state=pending.request_state[:-1] + suffix,
        )
    )
    assert isinstance(result, MCPError)
    assert not gateway._artifacts


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
        request_schema = tool.input_schema["properties"]["request"]
        if tool.name == "start_optimization":
            assert len(request_schema["oneOf"]) == 3
            assert all(
                branch["additionalProperties"] is False for branch in request_schema["oneOf"]
            )
            assert "schema_version" not in request_schema["oneOf"][0]["properties"]
            v2 = request_schema["oneOf"][1:]
            assert all(
                branch["properties"]["schema_version"]["const"] == "optimization/v2"
                for branch in v2
            )
            modes = {branch["properties"]["input_mode"]["const"]: branch for branch in v2}
            assert set(modes) == {"observed-snapshot", "native-full-board"}
            assert "expect_snapshot_digest" in modes["observed-snapshot"]["required"]
            native = modes["native-full-board"]
            assert "expect_board_revision" in native["required"]
            assert native["properties"]["target_net_refs"]["type"] == "null"
            for branch in request_schema["oneOf"]:
                assert (
                    not {"apply_token", "human_confirmation_capability", "kicad_cli"}
                    & branch["properties"].keys()
                )
        else:
            assert request_schema["additionalProperties"] is False
    assert "human_confirmation_capability" not in str(
        tools["approve_optimization_job"].input_schema
    )


def _v1_projection(schema):
    """Select only the advertised v1 branches; preserve every remaining schema field."""

    def walk(value):
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        output = {key: walk(item) for key, item in value.items() if key != "$defs"}
        for union in ("anyOf", "oneOf"):
            if union not in output:
                continue
            branches = [
                branch
                for branch in output[union]
                if not branch.get("$ref", "").endswith("V2")
                and branch.get("properties", {}).get("schema_version", {}).get("const")
                != "optimization/v2"
            ]
            if len(branches) == 1:
                # Pydantic adds a title to the new union wrapper; the original branch
                # retains its own title (or its untitled reference) verbatim.
                output = {
                    **branches[0],
                    **{key: item for key, item in output.items() if key not in {union, "title"}},
                }
            else:
                output[union] = branches
        return output

    output = walk(schema)
    definitions = schema.get("$defs", {})
    # A typed root union moves the unchanged v1 model behind a root reference.
    # Resolve only that reference; the original byte-for-byte golden stays fixed.
    ref = output.get("$ref", "")
    if ref.startswith("#/$defs/"):
        output = {
            **walk(definitions[ref[8:]]),
            **{key: value for key, value in output.items() if key != "$ref"},
        }
    needed = {}
    pending = [output]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/$defs/") and ref[8:] not in needed:
                name = ref[8:]
                needed[name] = walk(definitions[name])
                pending.append(needed[name])
            pending.extend(value.values())
    if needed:
        output["$defs"] = needed
    return output


def test_five_tool_schema_digest_matches_clean_6244_and_8a4_baselines(app):
    server, _gateway = app
    tools = sorted(asyncio.run(server.list_tools()), key=lambda tool: tool.name)
    normalized = json.dumps(
        [
            {
                "name": tool.name,
                "input_schema": _v1_projection(tool.input_schema),
                "output_schema": _v1_projection(tool.output_schema),
            }
            for tool in tools
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    assert len(normalized) == 60_042
    assert "sha256:" + hashlib.sha256(normalized).hexdigest() == (
        "sha256:b6f0bc2d53d688bf4922ef17ca6c88cb9f8dbcc3eddf8b1bc4e474d3fb86627f"
    )


def test_host_decline_then_accept_preserves_unknown_domains_and_board(
    app, launch, synthetic_authority, monkeypatch, tmp_path
):
    server, _gateway = app
    allow_direct_elicitation(monkeypatch)
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


@pytest.mark.parametrize(
    ("name", "fixed_message"),
    [
        ("export_optimization_package", "optimization package is unavailable"),
        ("approve_optimization_job", "optimization approval was refused"),
    ],
)
def test_resolver_refusals_are_deliberate_tool_errors(
    app, launch, synthetic_authority, name, fixed_message
):
    server, gateway = app
    record = completed_search(app, launch)["record"]
    service, _owner = gateway.service()
    service.allow_host_confirmation = False
    request = {
        "job_id": record["job_id"],
        "expected_record_revision": record["revision"],
        "expected_package_digest": record["package_digest"],
    }
    if name == "export_optimization_package":
        request["include_geometry"] = True
    else:
        request["expected_judge_digest"] = record["judge_digest"]

    with pytest.raises(ToolError) as error:
        asyncio.run(server.call_tool(name, {"request": request}))
    assert type(error.value) is ToolError
    assert str(error.value) == f"Error executing tool {name}: {fixed_message}"


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
    allow_direct_elicitation(monkeypatch)
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
