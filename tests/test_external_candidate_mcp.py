from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import copper_mcp.mcp_server as server


def _tool() -> object:
    return next(
        tool
        for tool in asyncio.run(server.mcp.list_tools())
        if tool.name == "verify_external_route_candidate"
    )


def _property_names(schema: object) -> set[str]:
    names: set[str] = set()
    pending = [schema]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            properties = current.get("properties")
            if isinstance(properties, dict):
                names.update(properties)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return names


def test_external_route_tool_advertises_one_closed_versioned_read_only_boundary() -> None:
    tool = _tool()
    assert tool.input_schema["additionalProperties"] is False
    assert set(tool.input_schema["properties"]) == {"request"}
    assert tool.input_schema["required"] == ["request"]

    envelope = tool.input_schema["properties"]["request"]
    assert envelope["additionalProperties"] is False
    assert set(envelope["properties"]) == {
        "schema_version",
        "request",
        "document",
        "start_pad_id",
        "end_pad_id",
    }
    assert set(envelope["required"]) == set(envelope["properties"])
    assert envelope["properties"]["schema_version"]["const"] == "1.0"

    route = envelope["properties"]["request"]
    assert route["additionalProperties"] is False
    assert {
        "board",
        "layer",
        "constraints",
        "net_ref_id",
        "expect_board_revision",
        "expect_snapshot_digest",
    } == set(route["required"])
    assert {
        "net",
        "include_drc",
        "include_fill_authority",
        "include_apply_token",
        "max_path_edges",
    }.isdisjoint(route["properties"])

    document_variants = envelope["properties"]["document"]["anyOf"]
    assert len(document_variants) == 2
    assert {variant["properties"]["schema"]["const"] for variant in document_variants} == {
        "copper-mcp/external-route-candidate/v1",
        "copper-mcp/external-route-patch/v2",
    }
    for variant in document_variants:
        assert variant["additionalProperties"] is False
        assert set(variant["required"]) == set(variant["properties"])

    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True
    assert tool.annotations.open_world_hint is False


def test_external_route_tool_output_schema_has_no_geometry_or_authority_fields() -> None:
    output = _tool().output_schema
    assert isinstance(output, dict)
    definitions = output["$defs"]
    accepted = definitions["AcceptedExternalRouteVerificationContract"]
    refused = definitions["RefusedExternalRouteVerificationContract"]
    assert accepted["additionalProperties"] is False
    assert refused["additionalProperties"] is False
    assert set(accepted["required"]) == set(accepted["properties"])
    assert set(refused["required"]) == set(refused["properties"])
    assert accepted["properties"]["physical_validation"]["const"] == "completed"
    assert refused["properties"]["physical_validation"]["const"] == "not_run"
    assert "candidate_id" not in refused["properties"]
    assert "drc_comparability" not in refused["properties"]
    assert "code" not in accepted["properties"]
    assert "diagnostic" not in accepted["properties"]
    names = _property_names(output)
    assert {
        "x_nm",
        "y_nm",
        "segments",
        "paths",
        "vias",
        "board",
        "board_path",
        "board_bytes",
        "net",
        "net_id",
        "start_pad_id",
        "end_pad_id",
        "apply_token",
        "apply_authority",
        "mutation",
        "repaired_candidate",
    }.isdisjoint(names)
    assert {
        "schema_version",
        "status",
        "physical_validation",
        "segment_count",
        "edge_checks",
        "obstacle_checks",
        "drc_evidence",
    } <= names


def test_external_route_tool_rejects_unknown_wrapper_fields_without_echo() -> None:
    marker = "private-coordinate-991"
    with pytest.raises(ToolError) as captured:
        asyncio.run(
            server.mcp.call_tool(
                "verify_external_route_candidate",
                {"request": {}, marker: {"x_nm": 991}},
            )
        )
    assert str(captured.value) == "external route verification arguments are malformed"
    assert marker not in str(captured.value)


def test_external_route_tool_rejects_hostile_mapping_subclass_before_traversal() -> None:
    class HostileArguments(dict[str, object]):
        def __len__(self) -> int:
            raise AssertionError("must not inspect a hostile mapping subclass")

        def __contains__(self, key: object) -> bool:
            raise AssertionError("must not inspect a hostile mapping subclass")

    with pytest.raises(ToolError) as captured:
        asyncio.run(
            server.mcp.call_tool(
                "verify_external_route_candidate",
                HostileArguments(request={}),
            )
        )

    assert "external route verification arguments are malformed" in str(captured.value)


def test_external_route_wrapper_rejects_before_iterating_hostile_keys() -> None:
    class HostileKeys(dict[str, object]):
        def __iter__(self):  # type: ignore[no-untyped-def]
            raise AssertionError("wrapper keys must not be traversed")

    with pytest.raises(ToolError) as captured:
        asyncio.run(
            server.mcp.call_tool(
                "verify_external_route_candidate",
                HostileKeys(request={}, extra="private"),
            )
        )
    assert str(captured.value) == "external route verification arguments are malformed"


def test_external_route_tool_returns_one_typed_redacted_refusal() -> None:
    response = {
        "schema_version": "1.0",
        "status": "refused",
        "physical_validation": "not_run",
        "code": "stale_revision",
        "diagnostic": "external candidate is stale",
        "segment_count": 0,
        "edge_checks": 0,
        "obstacle_checks": 0,
        "drc_evidence": None,
    }
    with patch.object(server, "verify_external_route_candidate_service", return_value=response):
        result = asyncio.run(
            server.mcp.call_tool("verify_external_route_candidate", {"request": {}})
        )

    assert result.is_error is False
    assert result.structured_content == response


def test_external_route_operational_failure_is_fixed_and_non_echoing() -> None:
    marker = "private-board-marker-223"
    with patch.object(
        server,
        "verify_external_route_candidate_service",
        side_effect=server.ExternalCandidatePublicError(
            "external route verification is unavailable"
        ),
    ):
        with pytest.raises(ToolError) as captured:
            asyncio.run(
                server.mcp.call_tool(
                    "verify_external_route_candidate",
                    {"request": {"board": marker}},
                )
            )

    assert str(captured.value) == (
        "Error executing tool verify_external_route_candidate: "
        "external route verification is unavailable"
    )
    assert marker not in str(captured.value)
