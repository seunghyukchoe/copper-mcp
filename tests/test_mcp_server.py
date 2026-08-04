from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ResourceLink

import copper_mcp.mcp_server as _server
from copper_mcp.adapters import net_id_for_name
from copper_mcp.apply.tokens import ApplyTokenAuthority
from copper_mcp.circuit_intent_service import build_schematic_from_content
from copper_mcp.mcp_server import mcp
from copper_mcp.scene_render import SceneRenderStore
from copper_mcp.schematic_artifacts import SchematicArtifactStore

ROOT = Path(__file__).resolve().parents[1]
CIRCUIT_FIXTURE = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-intent-v1.json"
RESOURCE_TEMPLATE = "pcb://artifacts/schematic/{token}/circuit.kicad_sch"
RESOURCE_URI = re.compile(r"^pcb://artifacts/schematic/([A-Za-z0-9_-]{43})/circuit\.kicad_sch$")


def _content() -> dict[str, object]:
    document = json.loads(CIRCUIT_FIXTURE.read_text(encoding="utf-8"))
    content = document["content"]
    assert isinstance(content, dict)
    return content


def _resolve_local_ref(root: dict[str, object], node: object) -> dict[str, object]:
    assert isinstance(node, dict)
    reference = node.get("$ref")
    if reference is None:
        return node
    assert isinstance(reference, str)
    assert reference.startswith("#/")
    resolved: object = root
    for part in reference.removeprefix("#/").split("/"):
        assert isinstance(resolved, dict)
        resolved = resolved[part]
    assert isinstance(resolved, dict)
    return resolved


def _assert_closed_object(schema: dict[str, object], properties: set[str]) -> None:
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    declared = schema["properties"]
    assert isinstance(declared, dict)
    assert set(declared) == properties
    assert set(schema["required"]) == properties


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        """Give each test its own schematic artifact store.

        The store is a module-level singleton, so without this every test in the process
        shares one LRU, one byte budget and one TTL clock. Nothing in the current suite is
        known to depend on that — the store holds far fewer entries than its 16-entry ceiling
        and the 15-minute TTL cannot expire inside a 30-second run, and a deliberate
        reproduction attempt over more than twenty runs never reproduced the transient
        failure that prompted this. The isolation is therefore defensive rather than a fix
        for a diagnosed cause: it removes an entire class of cross-test coupling cheaply, and
        makes any future failure here attributable to the test that caused it.
        """

        store = SchematicArtifactStore()
        patcher = patch.object(_server, "_SCHEMATIC_ARTIFACTS", store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_declares_expected_read_only_tools(self) -> None:
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual(
            {tool.name for tool in tools},
            {
                "apply_candidate",
                "compare_candidates",
                "inspect_board",
                "inspect_board_ir",
                "inspect_live_board",
                "inspect_live_editor_context",
                "observe_board_scene",
                "observe_live_board_scene",
                "preview_placement",
                "preview_live_placement",
                "preview_live_route",
                "preview_layered_route",
                "preview_live_layered_route",
                "preview_route",
                "render_circuit_schematic",
                "run_board_drc",
                "server_info",
                "start_routing",
                "get_routing_job",
                "cancel_routing_job",
                "export_routing_candidate",
                "validate_candidate",
            },
        )

    def test_live_observer_advertises_closed_read_only_output(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        live = tools["inspect_live_board"]
        self.assertEqual(live.input_schema["type"], "object")
        self.assertEqual(live.input_schema["properties"], {})
        self.assertIsNotNone(live.output_schema)
        assert isinstance(live.output_schema, dict)
        self.assertIs(live.output_schema["additionalProperties"], False)
        self.assertEqual(live.output_schema["properties"]["source"]["const"], "kicad-ipc-live")
        self.assertEqual(live.output_schema["properties"]["read_only"]["const"], True)
        assert live.annotations is not None
        self.assertIs(live.annotations.read_only_hint, True)
        self.assertIs(live.annotations.destructive_hint, False)
        self.assertIs(live.annotations.idempotent_hint, True)
        self.assertIs(live.annotations.open_world_hint, False)

    def test_live_observer_returns_typed_redacted_content(self) -> None:
        payload = {
            "schema_version": "0.1.0",
            "source": "kicad-ipc-live",
            "kicad_version": "10.0.5",
            "api_version": "10.0.5",
            "compatibility": "compatible",
            "board_digest": "sha256:" + "a" * 64,
            "board_bytes": 128,
            "object_counts": {"nets": 2, "pads": 4},
            "socket_kind": "default-local-ipc",
            "read_only": True,
        }
        with patch.object(_server, "inspect_live_board_service", return_value=payload):
            result = asyncio.run(_server.mcp.call_tool("inspect_live_board", {}))
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, payload)

    def test_live_scene_advertises_a_closed_revision_bound_request(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        live_scene = tools["observe_live_board_scene"]
        self.assertEqual(live_scene.input_schema["type"], "object")
        self.assertIs(live_scene.input_schema["additionalProperties"], False)
        request_schema = live_scene.input_schema["properties"]["request"]
        self.assertIs(request_schema["additionalProperties"], False)
        self.assertEqual(request_schema["properties"]["board"]["const"], "live")
        self.assertIn("expect_board_revision", request_schema["properties"])
        self.assertIn("expect_snapshot_digest", request_schema["properties"])
        assert live_scene.annotations is not None
        self.assertIs(live_scene.annotations.read_only_hint, True)
        self.assertIs(live_scene.annotations.destructive_hint, False)
        self.assertIs(live_scene.annotations.idempotent_hint, True)

    def test_live_scene_returns_the_same_structured_scene_contract(self) -> None:
        from copper_mcp.circuit_scene import observe_board_scene

        board = ROOT / "tests" / "fixtures" / "circuit-scene-v0.1" / "scene-region.kicad_pcb"
        settings = replace(_server._SETTINGS, workspace=board.parent.resolve())
        scene = observe_board_scene(
            {
                "board": board.name,
                "constraints": {
                    "clearance_nm": 200_000,
                    "track_width_nm": 250_000,
                    "via_diameter_nm": 600_000,
                    "via_drill_nm": 300_000,
                },
                "region": {
                    "min_x_nm": 0,
                    "min_y_nm": 0,
                    "max_x_nm": 30_000_000,
                    "max_y_nm": 30_000_000,
                },
            },
            settings,
        )
        with patch.object(_server, "observe_live_board_scene_service_raw", return_value=scene):
            with patch.object(_server, "_SETTINGS", settings):
                result = asyncio.run(
                    _server.mcp.call_tool(
                        "observe_live_board_scene",
                        {
                            "request": {
                                "board": "live",
                                "constraints": {
                                    "clearance_nm": 200_000,
                                    "track_width_nm": 250_000,
                                    "via_diameter_nm": 600_000,
                                    "via_drill_nm": 300_000,
                                },
                                "region": {
                                    "min_x_nm": 0,
                                    "min_y_nm": 0,
                                    "max_x_nm": 30_000_000,
                                    "max_y_nm": 30_000_000,
                                },
                            }
                        },
                    )
                )
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["scene_version"], "0.2.0")

    def test_live_route_advertises_a_closed_read_only_revision_bound_request(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        live_route = tools["preview_live_route"]
        self.assertEqual(live_route.input_schema["type"], "object")
        self.assertIs(live_route.input_schema["additionalProperties"], False)
        request_schema = live_route.input_schema["properties"]["request"]
        self.assertIs(request_schema["additionalProperties"], False)
        self.assertEqual(request_schema["properties"]["board"]["const"], "live")
        self.assertIn("net_ref_id", request_schema["properties"])
        self.assertNotIn("net", request_schema["properties"])
        self.assertEqual(
            set(request_schema["required"]),
            {
                "board",
                "layer",
                "constraints",
                "net_ref_id",
                "expect_board_revision",
                "expect_snapshot_digest",
            },
        )
        assert live_route.annotations is not None
        self.assertIs(live_route.annotations.read_only_hint, True)
        self.assertIs(live_route.annotations.destructive_hint, False)
        self.assertIs(live_route.annotations.idempotent_hint, True)
        self.assertIs(live_route.annotations.open_world_hint, False)

    def test_live_placement_advertises_closed_read_only_revision_bound_request(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        live_placement = tools["preview_live_placement"]
        self.assertEqual(live_placement.input_schema["type"], "object")
        self.assertIs(live_placement.input_schema["additionalProperties"], False)
        request_schema = live_placement.input_schema["properties"]["request"]
        self.assertIs(request_schema["additionalProperties"], False)
        self.assertEqual(request_schema["properties"]["board"]["const"], "live")
        self.assertEqual(
            set(request_schema["required"]),
            {
                "board",
                "constraints",
                "subjects",
                "expect_board_revision",
                "expect_snapshot_digest",
            },
        )
        self.assertNotIn("include_apply_token", request_schema["properties"])
        assert live_placement.annotations is not None
        self.assertIs(live_placement.annotations.read_only_hint, True)
        self.assertIs(live_placement.annotations.destructive_hint, False)
        self.assertIs(live_placement.annotations.idempotent_hint, True)
        self.assertIs(live_placement.annotations.open_world_hint, False)

    def test_layered_route_advertises_closed_read_only_revision_bound_request(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        layered = tools["preview_layered_route"]
        self.assertEqual(layered.input_schema["type"], "object")
        self.assertIs(layered.input_schema["additionalProperties"], False)
        request_schema = layered.input_schema["properties"]["request"]
        self.assertIs(request_schema["additionalProperties"], False)
        self.assertEqual(
            set(request_schema["required"]),
            {
                "board",
                "start_pad_id",
                "end_pad_id",
                "constraints",
                "expect_board_revision",
                "expect_snapshot_digest",
            },
        )
        self.assertNotIn("net", request_schema["properties"])
        self.assertIn("start_layer_id", request_schema["properties"])
        self.assertIn("end_layer_id", request_schema["properties"])
        self.assertIn("grid_step_nm", request_schema["properties"])
        self.assertIn("settings", request_schema["properties"])
        self.assertEqual(request_schema["properties"]["include_drc"]["type"], "boolean")
        output = layered.output_schema
        self.assertIsNotNone(output)
        assert isinstance(output, dict)
        variants = [_resolve_local_ref(output, variant) for variant in output["anyOf"]]
        self.assertEqual(len(variants), 4)
        self.assertEqual(
            sorted(variant["properties"]["status"].get("const") for variant in variants),
            ["not_routed", "not_routed", "routed", "unsupported_board"],
        )
        for variant in variants:
            self.assertIs(variant["additionalProperties"], False)
        assert layered.annotations is not None
        self.assertIs(layered.annotations.read_only_hint, True)
        self.assertIs(layered.annotations.destructive_hint, False)
        self.assertIs(layered.annotations.idempotent_hint, True)
        self.assertIs(layered.annotations.open_world_hint, False)

    def test_layered_route_rejects_unknown_wrapper_fields_without_echo(self) -> None:
        secret = "SECRET_LAYERED_ROUTE_WRAPPER"
        with self.assertRaises(ToolError) as caught:
            asyncio.run(
                mcp.call_tool(
                    "preview_layered_route",
                    {"request": {}, secret: 1},
                )
            )
        self.assertNotIn(secret, str(caught.exception))

    def test_live_layered_route_advertises_closed_read_only_revision_bound_request(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        live = tools["preview_live_layered_route"]
        self.assertEqual(live.input_schema["type"], "object")
        self.assertIs(live.input_schema["additionalProperties"], False)
        request_schema = live.input_schema["properties"]["request"]
        self.assertIs(request_schema["additionalProperties"], False)
        self.assertEqual(request_schema["properties"]["board"]["const"], "live")
        self.assertIn("start_pad_id", request_schema["properties"])
        self.assertIn("end_pad_id", request_schema["properties"])
        self.assertNotIn("net", request_schema["properties"])
        self.assertNotIn("net_ref_id", request_schema["properties"])
        self.assertEqual(request_schema["properties"]["include_drc"].get("const"), False)
        self.assertEqual(
            set(request_schema["required"]),
            {
                "board",
                "start_pad_id",
                "end_pad_id",
                "constraints",
                "expect_board_revision",
                "expect_snapshot_digest",
                "expect_session_revision",
            },
        )
        assert live.annotations is not None
        self.assertIs(live.annotations.read_only_hint, True)
        self.assertIs(live.annotations.destructive_hint, False)
        self.assertIs(live.annotations.idempotent_hint, True)
        self.assertIs(live.annotations.open_world_hint, False)

    def test_live_layered_route_rejects_unknown_wrapper_fields_without_echo(self) -> None:
        secret = "SECRET_LIVE_LAYERED_ROUTE_WRAPPER"
        with self.assertRaises(ToolError) as caught:
            asyncio.run(
                mcp.call_tool(
                    "preview_live_layered_route",
                    {"request": {}, secret: 1},
                )
            )
        self.assertNotIn(secret, str(caught.exception))

    def test_layered_route_rejects_unknown_request_fields_without_echo(self) -> None:
        secret = "SECRET_LAYERED_ROUTE_REQUEST"
        with self.assertRaises(ToolError) as caught:
            asyncio.run(
                mcp.call_tool(
                    "preview_layered_route",
                    {"request": {secret: 1}},
                )
            )
        self.assertNotIn(secret, str(caught.exception))

    def test_layered_route_returns_status_specific_structured_candidate(self) -> None:
        digest = "sha256:" + "a" * 64
        request = {
            "board": "two-pad.kicad_pcb",
            "start_pad_id": "pad:kicad:start",
            "end_pad_id": "pad:kicad:end",
            "constraints": {
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            },
            "expect_board_revision": digest,
            "expect_snapshot_digest": digest,
            "include_drc": True,
        }
        response = {
            "schema_version": "1.0",
            "status": "routed",
            "board_path": "two-pad.kicad_pcb",
            "board_revision": digest,
            "snapshot_digest": digest,
            "request": request,
            "conversion_diagnostic_counts": {},
            "drc_evidence": {
                "candidate_id": digest,
                "candidate_base_revision": digest,
                "source_revision": digest,
                "patched_board_revision": "sha256:" + "b" * 64,
                "patched_drc_context_revision": "sha256:" + "c" * 64,
                "summary": {
                    "base_revision": "sha256:" + "b" * 64,
                    "drc_context_revision": "sha256:" + "c" * 64,
                    "kicad_version": "10.0.5",
                    "drc_schema": "https://schemas.kicad.org/drc.v1.json",
                    "coordinate_units": "mm",
                    "error_count": 0,
                    "warning_count": 0,
                    "exclusion_count": 0,
                    "ignored_check_count": 0,
                    "unconnected_count": 0,
                    "violation_type_counts": {},
                    "passed": True,
                    "schema_version": "1.0",
                },
            },
            "candidate": {
                "candidate_id": digest,
                "base_revision": digest,
                "start_pad_id": "pad:kicad:start",
                "end_pad_id": "pad:kicad:end",
                "router_version": "layered-board-a-star/0.1.0",
                "policy": "board-layered-a-star-v1",
                "seed": 0,
                "patch": {
                    "net_id": "net:name:0123456789abcdef0123456789abcdef",
                    "width_nm": 250_000,
                    "via_diameter_nm": 800_000,
                    "via_drill_nm": 400_000,
                    "paths": [
                        {
                            "layer_id": "layer:F.Cu",
                            "vertices_nm": [[0, 0], [1_000, 0]],
                        }
                    ],
                    "vias": [],
                },
                "cost": {
                    "wire_length_nm": 1_000,
                    "via_count": 0,
                    "via_cost_units": 0,
                    "total_search_cost_units": 1,
                },
                "metrics": {
                    "expanded_states": 1,
                    "discovered_states": 2,
                    "peak_frontier_states": 1,
                    "obstacle_checks": 0,
                    "move_steps": 1,
                    "vias": 0,
                    "wire_length_nm": 1_000,
                    "bend_count": 0,
                },
                "settings": {},
            },
            "diagnostic": None,
        }
        with patch.object(_server, "preview_layered_route_service", return_value=response):
            result = asyncio.run(mcp.call_tool("preview_layered_route", {"request": request}))
        self.assertFalse(result.is_error)
        structured = result.structured_content
        assert isinstance(structured, dict)
        self.assertEqual(structured["status"], "routed")
        self.assertEqual(structured["candidate"]["patch"]["paths"][0]["layer_id"], "layer:F.Cu")
        self.assertNotIn("net", structured["request"])
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        output_schema = tools["preview_layered_route"].output_schema
        assert isinstance(output_schema, dict)
        self.assertEqual(list(Draft202012Validator(output_schema).iter_errors(structured)), [])

    def test_render_tool_declares_structured_content_and_security_annotations(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        render = tools["render_circuit_schematic"]

        self.assertEqual(set(render.input_schema["properties"]), {"content"})
        self.assertEqual(render.input_schema["required"], ["content"])
        self.assertEqual(render.input_schema["properties"]["content"]["type"], "object")
        self.assertNotIn("snapshot_digest", repr(render.input_schema))
        self.assertIsNotNone(render.annotations)
        assert render.annotations is not None
        self.assertFalse(render.annotations.read_only_hint)
        self.assertFalse(render.annotations.destructive_hint)
        self.assertFalse(render.annotations.idempotent_hint)
        self.assertFalse(render.annotations.open_world_hint)

    def test_render_tool_advertises_exact_bounded_input_and_output_schemas(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        render = tools["render_circuit_schematic"]
        input_schema = render.input_schema
        _assert_closed_object(input_schema, {"content"})
        input_properties = input_schema["properties"]
        assert isinstance(input_properties, dict)
        content = _resolve_local_ref(input_schema, input_properties["content"])
        content_fields = {
            "circuit_id",
            "project_name",
            "title",
            "components",
            "nets",
            "ports",
        }
        _assert_closed_object(content, content_fields)
        content_properties = content["properties"]
        assert isinstance(content_properties, dict)
        for field, minimum, maximum in (
            ("components", 1, 64),
            ("nets", 1, 128),
            ("ports", None, 32),
        ):
            collection = content_properties[field]
            assert isinstance(collection, dict)
            self.assertEqual(collection["type"], "array")
            self.assertEqual(collection.get("minItems"), minimum)
            self.assertEqual(collection["maxItems"], maximum)

        component_collection = content_properties["components"]
        net_collection = content_properties["nets"]
        port_collection = content_properties["ports"]
        assert isinstance(component_collection, dict)
        assert isinstance(net_collection, dict)
        assert isinstance(port_collection, dict)
        component = _resolve_local_ref(input_schema, component_collection["items"])
        net = _resolve_local_ref(input_schema, net_collection["items"])
        port = _resolve_local_ref(input_schema, port_collection["items"])
        _assert_closed_object(component, {"id", "kind", "reference", "value"})
        _assert_closed_object(net, {"id", "name", "connections"})
        _assert_closed_object(port, {"id", "net_id", "direction"})
        net_properties = net["properties"]
        assert isinstance(net_properties, dict)
        connections = net_properties["connections"]
        assert isinstance(connections, dict)
        self.assertEqual(connections["minItems"], 1)
        self.assertEqual(connections["maxItems"], 128)
        _assert_closed_object(
            _resolve_local_ref(input_schema, connections["items"]),
            {"component_id", "pin"},
        )

        output_schema = render.output_schema
        self.assertIsNotNone(output_schema)
        assert output_schema is not None
        _assert_closed_object(
            output_schema,
            {"schema", "schema_version", "status", "intent", "artifact", "verification"},
        )
        output_properties = output_schema["properties"]
        assert isinstance(output_properties, dict)
        artifact = _resolve_local_ref(output_schema, output_properties["artifact"])
        _assert_closed_object(
            artifact,
            {
                "kind",
                "mime_type",
                "format_version",
                "artifact_digest",
                "intent_digest",
                "size_bytes",
                "resource_uri",
                "retention",
            },
        )
        artifact_properties = artifact["properties"]
        assert isinstance(artifact_properties, dict)
        self.assertEqual(artifact_properties["size_bytes"]["maximum"], 1_000_000)
        retention = _resolve_local_ref(output_schema, artifact_properties["retention"])
        _assert_closed_object(
            retention,
            {"scope", "ttl_seconds", "persistent", "reclamation"},
        )

    def test_schematic_resource_is_a_non_enumerated_typed_template(self) -> None:
        resources = asyncio.run(mcp.list_resources())
        templates = asyncio.run(mcp.list_resource_templates())

        self.assertEqual({str(resource.uri) for resource in resources}, {"pcb://server/manifest"})
        matches = [template for template in templates if template.uri_template == RESOURCE_TEMPLATE]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].mime_type, "application/x-kicad-schematic")

    def test_render_tool_returns_redacted_metadata_and_retrievable_exact_bytes(self) -> None:
        content = _content()
        expected = build_schematic_from_content(content)

        result = asyncio.run(mcp.call_tool("render_circuit_schematic", {"content": content}))

        self.assertFalse(result.is_error)
        document = result.structured_content
        self.assertIsNotNone(document)
        assert document is not None
        expected_document = expected.to_dict()
        artifact = expected_document["artifact"]
        resource_uri = document["artifact"]["resource_uri"]
        match = RESOURCE_URI.fullmatch(resource_uri)
        self.assertIsNotNone(match)
        assert match is not None
        artifact["resource_uri"] = resource_uri
        artifact["retention"] = {
            "scope": "process",
            "ttl_seconds": 900,
            "persistent": False,
            "reclamation": "lazy_on_access_or_process_exit",
        }
        self.assertEqual(document, expected_document)
        self.assertNotIn(expected.artifact.artifact_digest, resource_uri)
        self.assertNotIn(expected.artifact.intent_digest, resource_uri)

        # The capability token is intentionally opaque, random, and not board content.  Remove
        # only that token before scanning metadata so a coincidental substring such as ``1k``
        # cannot make this privacy assertion flaky across CI runs.
        serialized = json.dumps(document, sort_keys=True).replace(
            resource_uri, "pcb://artifacts/schematic/<opaque>/circuit.kicad_sch"
        )
        tool_text = "\n".join(
            block.text for block in result.content if block.type == "text"
        ).replace(resource_uri, "pcb://artifacts/schematic/<opaque>/circuit.kicad_sch")
        for private_value in (
            "AUDIO_IN",
            "AUDIO_OUT",
            "GND",
            "100n",
            "1k",
            "component:c-filter",
            "(kicad_sch",
        ):
            self.assertNotIn(private_value, serialized)
            self.assertNotIn(private_value, tool_text)

        resource = asyncio.run(mcp.read_resource(resource_uri))
        self.assertEqual(len(resource), 1)
        self.assertEqual(resource[0].mime_type, "application/x-kicad-schematic")
        self.assertIsInstance(resource[0].content, bytes)
        self.assertEqual(resource[0].content, expected.artifact.content)
        self.assertEqual(
            f"sha256:{hashlib.sha256(resource[0].content).hexdigest()}",
            document["artifact"]["artifact_digest"],
        )

    def test_render_tool_replays_stable_metadata_but_issues_a_new_capability(self) -> None:
        first = asyncio.run(mcp.call_tool("render_circuit_schematic", {"content": _content()}))
        second = asyncio.run(mcp.call_tool("render_circuit_schematic", {"content": _content()}))
        self.assertFalse(first.is_error)
        self.assertFalse(second.is_error)
        assert first.structured_content is not None
        assert second.structured_content is not None
        first_uri = first.structured_content["artifact"].pop("resource_uri")
        second_uri = second.structured_content["artifact"].pop("resource_uri")

        self.assertNotEqual(first_uri, second_uri)
        self.assertEqual(first.structured_content, second.structured_content)

    def test_unknown_well_formed_capability_fails_without_echoing_it(self) -> None:
        token = "Z" * 43
        uri = RESOURCE_TEMPLATE.format(token=token)

        with self.assertRaises(Exception) as raised:
            asyncio.run(mcp.read_resource(uri))

        self.assertIn("schematic artifact is unavailable", str(raised.exception))
        self.assertNotIn(token, str(raised.exception))

    def test_malformed_mcp_content_fails_without_echoing_private_values(self) -> None:
        content = _content()
        content["title"] = "SECRET_MCP_CIRCUIT\n"

        with self.assertRaises(ToolError) as raised:
            asyncio.run(mcp.call_tool("render_circuit_schematic", {"content": content}))

        message = str(raised.exception)
        self.assertNotIn("SECRET_MCP_CIRCUIT", message)
        self.assertNotIn("AUDIO_IN", message)

    def test_rejected_component_kind_never_reaches_the_exception_chain(self) -> None:
        content = _content()
        components = content["components"]
        assert isinstance(components, list)
        components[0]["kind"] = "SECRET_PRIVATE_KIND"

        with self.assertRaises(ToolError) as raised:
            asyncio.run(mcp.call_tool("render_circuit_schematic", {"content": content}))

        chain: list[BaseException] = []
        pending: list[BaseException | None] = [raised.exception]
        while pending:
            error = pending.pop()
            if error is None or any(error is seen for seen in chain):
                continue
            chain.append(error)
            pending.extend((error.__cause__, error.__context__))
        for error in chain:
            self.assertNotIn("SECRET_PRIVATE_KIND", repr(error))

    def test_scalar_list_and_oversized_mcp_content_never_echoes_private_values(self) -> None:
        oversized = _content()
        oversized["title"] = "SECRET_MCP_OVERSIZED_" + "x" * 300_000
        cases: tuple[object, ...] = (
            "SECRET_MCP_SCALAR",
            ["SECRET_MCP_LIST"],
            oversized,
        )

        for content in cases:
            with self.subTest(kind=type(content).__name__):
                with self.assertRaises(ToolError) as raised:
                    asyncio.run(mcp.call_tool("render_circuit_schematic", {"content": content}))

                message = str(raised.exception)
                self.assertNotIn("SECRET_MCP", message)
                self.assertNotIn("AUDIO_IN", message)

    def test_schematic_tool_and_resource_are_not_registered_for_http(self) -> None:
        script = """
import asyncio
import json
from copper_mcp.mcp_server import mcp

async def main():
    tools = sorted(tool.name for tool in await mcp.list_tools())
    templates = sorted(template.uri_template for template in await mcp.list_resource_templates())
    print(json.dumps({"tools": tools, "templates": templates}))

asyncio.run(main())
"""
        environment = os.environ.copy()
        environment["COPPER_MCP_TRANSPORT"] = "streamable-http"
        environment["COPPER_MCP_WORKSPACE"] = str(ROOT)
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        inventory = json.loads(result.stdout)
        self.assertNotIn("render_circuit_schematic", inventory["tools"])
        self.assertNotIn(RESOURCE_TEMPLATE, inventory["templates"])


class SceneToolSurfaceTests(unittest.TestCase):
    """The MCP surface of observe_board_scene, checked against the wire, not the docstring."""

    def _tool(self) -> object:
        tools = asyncio.run(mcp.list_tools())
        return next(tool for tool in tools if tool.name == "observe_board_scene")

    def test_the_scene_tool_advertises_a_real_output_schema(self) -> None:
        """A tool typed as a bare dict advertises nothing; this one must advertise its shape."""

        scene = self._tool()
        schema = scene.output_schema
        self.assertIsNotNone(schema)
        assert isinstance(schema, dict)
        self.assertEqual(schema["type"], "object")
        self.assertIs(schema["additionalProperties"], False)
        self.assertIn("static", schema["properties"])
        self.assertIn("mutable", schema["properties"])
        self.assertIn("annotations", schema["properties"])
        self.assertIn("truncation", schema["properties"])

    def test_the_scene_tool_is_annotated_read_only(self) -> None:
        scene = self._tool()
        self.assertIsNotNone(scene.annotations)
        assert scene.annotations is not None
        self.assertIs(scene.annotations.read_only_hint, True)
        self.assertIs(scene.annotations.destructive_hint, False)
        self.assertIs(scene.annotations.open_world_hint, False)

    def test_the_scene_tool_returns_structured_content_that_matches_its_schema(self) -> None:
        board = ROOT / "tests" / "fixtures" / "circuit-scene-v0.1" / "scene-region.kicad_pcb"

        # Point the module's settings at the fixture workspace rather than reloading the
        # module. Reloading would rebuild the module-level schematic artifact store, so a
        # capability minted by another test in this process would stop resolving — exactly
        # the cross-test coupling the store isolation below exists to prevent.
        replacement = replace(_server._SETTINGS, workspace=board.parent.resolve())
        with patch.object(_server, "_SETTINGS", replacement):
            result = asyncio.run(
                _server.mcp.call_tool(
                    "observe_board_scene",
                    {
                        "request": {
                            "board": board.name,
                            "constraints": {
                                "clearance_nm": 200_000,
                                "track_width_nm": 250_000,
                                "via_diameter_nm": 600_000,
                                "via_drill_nm": 300_000,
                            },
                            "region": {
                                "min_x_nm": 0,
                                "min_y_nm": 0,
                                "max_x_nm": 30_000_000,
                                "max_y_nm": 30_000_000,
                            },
                        }
                    },
                )
            )

        self.assertFalse(result.is_error)
        structured = result.structured_content
        assert isinstance(structured, dict)
        self.assertEqual(structured["scene_version"], "0.2.0")
        self.assertTrue(structured["static"]["footprints"])
        self.assertEqual(structured["region"]["source"], "explicit")
        self.assertEqual(len(structured["static"]["pads"]), 1)
        self.assertEqual(structured["annotations"], [])


class RouteToolSurfaceTests(unittest.TestCase):
    """Circuit Scene references must remain actionable across the actual MCP boundary."""

    def _tool(self) -> object:
        tools = asyncio.run(mcp.list_tools())
        return next(tool for tool in tools if tool.name == "preview_route")

    def test_route_tool_advertises_exclusive_closed_selectors_and_closed_output(self) -> None:
        route = self._tool()
        self.assertIs(route.input_schema["additionalProperties"], False)
        request_schema = route.input_schema["properties"]["request"]
        variants = request_schema["anyOf"]
        self.assertEqual(len(variants), 2)
        by_name = next(variant for variant in variants if "net" in variant["properties"])
        by_reference = next(
            variant for variant in variants if "net_ref_id" in variant["properties"]
        )
        self.assertIs(by_name["additionalProperties"], False)
        self.assertIs(by_reference["additionalProperties"], False)
        self.assertNotIn("net_ref_id", by_name["properties"])
        self.assertNotIn("net", by_reference["properties"])
        self.assertEqual(
            {
                "board",
                "layer",
                "constraints",
                "net_ref_id",
                "expect_board_revision",
                "expect_snapshot_digest",
            },
            set(by_reference["required"]),
        )
        constraints_schema = by_reference["properties"]["constraints"]
        self.assertIn(
            "strictly smaller",
            constraints_schema["properties"]["via_drill_nm"]["description"],
        )

        output = route.output_schema
        assert isinstance(output, dict)
        definitions = output["$defs"]
        assert isinstance(definitions, dict)
        response_fields = {
            "schema_version",
            "status",
            "board_path",
            "board_revision",
            "snapshot_digest",
            "request",
            "candidate",
            "connection",
            "diagnostic",
            "conversion_diagnostic_counts",
            "drc_evidence",
            "apply_token",
            "fill_authority",
        }
        response_variants = [_resolve_local_ref(output, variant) for variant in output["anyOf"]]
        self.assertEqual(len(response_variants), 5)
        for variant in response_variants:
            _assert_closed_object(variant, response_fields)
        self.assertEqual(
            sorted(variant["properties"]["status"]["const"] for variant in response_variants),
            [
                "already_connected",
                "not_routed",
                "not_routed",
                "routed",
                "unsupported_board",
            ],
        )
        _assert_closed_object(
            definitions["RouteCandidateContract"],
            {
                "candidate_id",
                "base_revision",
                "start_pad_id",
                "end_pad_id",
                "router_version",
                "policy",
                "seed",
                "pad_count",
                "ordering_policy",
                "patch",
                "cost",
                "metrics",
                "settings",
            },
        )
        _assert_closed_object(
            definitions["RoutePatchContract"], {"net_id", "layer_id", "width_nm", "paths"}
        )

    def test_route_tool_is_annotated_read_only(self) -> None:
        route = self._tool()
        self.assertIsNotNone(route.annotations)
        assert route.annotations is not None
        self.assertIs(route.annotations.read_only_hint, True)
        self.assertIs(route.annotations.destructive_hint, False)
        # The route geometry is deterministic, but include_apply_token may mint a fresh
        # destructive capability, so the tool-level annotation must be conservative.
        self.assertIs(route.annotations.idempotent_hint, False)
        self.assertIs(route.annotations.open_world_hint, False)

    def test_route_tool_rejects_invalid_unicode_without_leaking_an_internal_error(self) -> None:
        constraints = {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        }
        for field, value in (
            ("board", "bad\ud800board.kicad_pcb"),
            ("net", "bad\ud800net"),
        ):
            request = {
                "board": "board.kicad_pcb",
                "net": "AUDIO",
                "layer": "F.Cu",
                "constraints": constraints,
            }
            request[field] = value
            with self.subTest(field=field), self.assertRaises(ToolError) as caught:
                asyncio.run(mcp.call_tool("preview_route", {"request": request}))
            message = str(caught.exception)
            self.assertIn("valid Unicode", message)
            self.assertNotIn("UnicodeEncodeError", message)

    def test_observed_audio_net_routes_by_reference_with_hidden_name_equivalence(self) -> None:
        board = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-routing-v1.kicad_pcb"
        constraints = {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        }
        settings = replace(_server._SETTINGS, workspace=board.parent.resolve())
        before = board.read_bytes()
        with patch.object(_server, "_SETTINGS", settings):
            scene_result = asyncio.run(
                mcp.call_tool(
                    "observe_board_scene",
                    {
                        "request": {
                            "board": board.name,
                            "constraints": constraints,
                            "region": {
                                "min_x_nm": 0,
                                "min_y_nm": 0,
                                "max_x_nm": 100_000_000,
                                "max_y_nm": 100_000_000,
                            },
                        }
                    },
                )
            )
            scene = scene_result.structured_content
            assert isinstance(scene, dict)
            target_ref = net_id_for_name("AUDIO_IN")
            observed_refs = {pad["geometry"]["net_id"] for pad in scene["static"]["pads"]}
            self.assertIn(target_ref, observed_refs)
            reference_result = asyncio.run(
                mcp.call_tool(
                    "preview_route",
                    {
                        "request": {
                            "board": board.name,
                            "net_ref_id": target_ref,
                            "expect_board_revision": scene["board_revision"],
                            "expect_snapshot_digest": scene["snapshot_digest"],
                            "layer": "F.Cu",
                            "constraints": constraints,
                            "seed": 23,
                        }
                    },
                )
            )
            oracle_result = asyncio.run(
                mcp.call_tool(
                    "preview_route",
                    {
                        "request": {
                            "board": board.name,
                            "net": "AUDIO_IN",
                            "layer": "F.Cu",
                            "constraints": constraints,
                            "seed": 23,
                        }
                    },
                )
            )
            stale_result = asyncio.run(
                mcp.call_tool(
                    "preview_route",
                    {
                        "request": {
                            "board": board.name,
                            "net_ref_id": target_ref,
                            "expect_board_revision": f"sha256:{'0' * 64}",
                            "expect_snapshot_digest": scene["snapshot_digest"],
                            "layer": "F.Cu",
                            "constraints": constraints,
                            "seed": 23,
                        }
                    },
                )
            )

        self.assertFalse(reference_result.is_error)
        referenced = reference_result.structured_content
        oracle = oracle_result.structured_content
        assert isinstance(referenced, dict)
        assert isinstance(oracle, dict)
        self.assertEqual(referenced["status"], "routed")
        self.assertEqual(referenced["candidate"], oracle["candidate"])
        self.assertEqual(referenced["candidate"]["patch"]["net_id"], target_ref)
        self.assertNotIn("net", referenced["request"])
        self.assertEqual(referenced["request"]["net_ref_id"], target_ref)
        stale = stale_result.structured_content
        assert isinstance(stale, dict)
        self.assertEqual(stale["status"], "not_routed")
        self.assertEqual(stale["diagnostic"]["code"], "stale_revision")
        self.assertIsNone(stale["snapshot_digest"])
        output_schema = self._tool().output_schema
        assert isinstance(output_schema, dict)
        self.assertFalse(list(Draft202012Validator(output_schema).iter_errors(referenced)))
        contradictory = json.loads(json.dumps(referenced))
        contradictory["status"] = "unsupported_board"
        self.assertTrue(list(Draft202012Validator(output_schema).iter_errors(contradictory)))
        self.assertEqual(board.read_bytes(), before)

    def test_route_mcp_validates_connected_and_unsupported_status_variants(self) -> None:
        fixture_root = ROOT / "tests" / "fixtures" / "route-candidate"
        constraints = {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        }
        settings = replace(_server._SETTINGS, workspace=fixture_root.resolve())
        with patch.object(_server, "_SETTINGS", settings):
            connected_result = asyncio.run(
                mcp.call_tool(
                    "preview_route",
                    {
                        "request": {
                            "board": "connected-net.kicad_pcb",
                            "net": "AUDIO",
                            "layer": "F.Cu",
                            "constraints": constraints,
                        }
                    },
                )
            )
        connected = connected_result.structured_content
        assert isinstance(connected, dict)
        self.assertEqual(connected["status"], "already_connected")
        self.assertIsNotNone(connected["connection"])

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            unsupported = (
                (fixture_root / "two-pad.kicad_pcb")
                .read_bytes()
                .replace(b'(layer "Edge.Cuts")', b'(layer "F.SilkS")')
            )
            (workspace / "unsupported.kicad_pcb").write_bytes(unsupported)
            settings = replace(_server._SETTINGS, workspace=workspace.resolve())
            with patch.object(_server, "_SETTINGS", settings):
                unsupported_result = asyncio.run(
                    mcp.call_tool(
                        "preview_route",
                        {
                            "request": {
                                "board": "unsupported.kicad_pcb",
                                "net": "AUDIO",
                                "layer": "F.Cu",
                                "constraints": constraints,
                            }
                        },
                    )
                )
        unsupported_document = unsupported_result.structured_content
        assert isinstance(unsupported_document, dict)
        self.assertEqual(unsupported_document["status"], "unsupported_board")
        self.assertTrue(unsupported_document["conversion_diagnostic_counts"])

    def test_apply_token_calls_are_capability_minting_not_idempotent(self) -> None:
        fixture_root = ROOT / "tests" / "fixtures" / "route-candidate"
        constraints = {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        }
        settings = replace(
            _server._SETTINGS,
            workspace=fixture_root.resolve(),
            allow_apply=True,
        )
        arguments = {
            "request": {
                "board": "two-pad.kicad_pcb",
                "net": "AUDIO",
                "layer": "F.Cu",
                "constraints": constraints,
                "include_apply_token": True,
            }
        }
        with (
            patch.object(_server, "_SETTINGS", settings),
            patch.object(_server, "_APPLY_TOKENS", ApplyTokenAuthority()),
        ):
            first_result = asyncio.run(mcp.call_tool("preview_route", arguments))
            second_result = asyncio.run(mcp.call_tool("preview_route", arguments))

        first = first_result.structured_content
        second = second_result.structured_content
        assert isinstance(first, dict)
        assert isinstance(second, dict)
        self.assertEqual(first["candidate"], second["candidate"])
        self.assertIsNotNone(first["apply_token"])
        self.assertIsNotNone(second["apply_token"])
        self.assertNotEqual(first["apply_token"], second["apply_token"])

    def test_route_relational_constraint_is_documented_and_enforced(self) -> None:
        with self.assertRaises(ToolError) as caught:
            asyncio.run(
                mcp.call_tool(
                    "preview_route",
                    {
                        "request": {
                            "board": "board.kicad_pcb",
                            "net": "AUDIO",
                            "layer": "F.Cu",
                            "constraints": {
                                "clearance_nm": 250_000,
                                "track_width_nm": 250_000,
                                "via_diameter_nm": 400_000,
                                "via_drill_nm": 400_000,
                            },
                        }
                    },
                )
            )
        self.assertIn("constraints are invalid", str(caught.exception))

    def test_unknown_route_wrapper_fields_are_rejected_without_echo(self) -> None:
        secret = "SECRET_ROUTE_WRAPPER"
        with self.assertRaises(ToolError) as caught:
            asyncio.run(mcp.call_tool("preview_route", {"request": {}, secret: 1}))
        self.assertNotIn(secret, str(caught.exception))


REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")


@unittest.skipUnless(REAL_KICAD_CLI.is_file(), "KiCad CLI is not installed")
class RouteRealKiCadOutputTests(unittest.TestCase):
    """Status-specific MCP output must retain authoritative KiCad evidence."""

    def setUp(self) -> None:
        self.root = ROOT / "tests" / "fixtures" / "route-candidate"
        self.constraints = {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        }
        self.settings = replace(
            _server._SETTINGS,
            workspace=self.root.resolve(),
            kicad_cli=REAL_KICAD_CLI,
            max_drc_report_bytes=8 * 1024 * 1024,
        )

    def test_routed_variant_carries_candidate_bound_drc(self) -> None:
        board = self.root / "two-pad.kicad_pcb"
        before = board.read_bytes()
        with patch.object(_server, "_SETTINGS", self.settings):
            result = asyncio.run(
                mcp.call_tool(
                    "preview_route",
                    {
                        "request": {
                            "board": board.name,
                            "net": "AUDIO",
                            "layer": "F.Cu",
                            "constraints": self.constraints,
                            "include_drc": True,
                        }
                    },
                )
            )
        document = result.structured_content
        assert isinstance(document, dict)
        self.assertEqual(document["status"], "routed")
        self.assertEqual(
            document["drc_evidence"]["candidate_id"],
            document["candidate"]["candidate_id"],
        )
        self.assertEqual(board.read_bytes(), before)

    def test_connected_variant_carries_fresh_fill_authority(self) -> None:
        board = self.root / "zone-fill-fresh.kicad_pcb"
        before = board.read_bytes()
        with patch.object(_server, "_SETTINGS", self.settings):
            result = asyncio.run(
                mcp.call_tool(
                    "preview_route",
                    {
                        "request": {
                            "board": board.name,
                            "net": "GND",
                            "layer": "F.Cu",
                            "constraints": self.constraints,
                            "include_fill_authority": True,
                        }
                    },
                )
            )
        document = result.structured_content
        assert isinstance(document, dict)
        self.assertEqual(document["status"], "already_connected")
        self.assertGreater(document["connection"]["fill_polygons"], 0)
        self.assertEqual(document["fill_authority"]["source_revision"], document["board_revision"])
        self.assertEqual(
            document["fill_authority"]["routing_effect"],
            "connectivity_evidence",
        )
        self.assertEqual(board.read_bytes(), before)


class SceneRenderDeliveryTests(unittest.TestCase):
    """How render bytes reach a caller, and where the flag is refused."""

    def setUp(self) -> None:
        store = SceneRenderStore()
        patcher = patch.object(_server, "_SCENE_RENDERS", store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = store

    def _request(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "board": "scene-region.kicad_pcb",
            "constraints": {
                "clearance_nm": 200_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            },
            "region": {
                "min_x_nm": 0,
                "min_y_nm": 0,
                "max_x_nm": 30_000_000,
                "max_y_nm": 30_000_000,
            },
        }
        payload.update(overrides)
        return payload

    def _call(self, settings: object, request: dict[str, object]) -> object:
        with patch.object(_server, "_SETTINGS", settings):
            return asyncio.run(_server.mcp.call_tool("observe_board_scene", {"request": request}))

    def test_the_render_flag_is_refused_off_stdio(self) -> None:
        """The asymmetry: the scene is both-transport, its render is stdio-only.

        Render bytes are delivered through the process-local capability store, which a
        stateless HTTP deployment cannot resolve - the same reason render_circuit_schematic
        is stdio-only. Only the flag is withdrawn, not the whole tool.
        """

        board = ROOT / "tests" / "fixtures" / "circuit-scene-v0.1"
        http_settings = replace(
            _server._SETTINGS, workspace=board.resolve(), transport="streamable-http"
        )
        with self.assertRaises(ToolError) as caught:
            self._call(http_settings, self._request(include_render=True))
        self.assertIn("stdio", str(caught.exception))

    def test_the_scene_itself_still_works_off_stdio(self) -> None:
        """Guard the guard: the refusal above must be about the flag, not the transport."""

        board = ROOT / "tests" / "fixtures" / "circuit-scene-v0.1"
        http_settings = replace(
            _server._SETTINGS, workspace=board.resolve(), transport="streamable-http"
        )
        result = self._call(http_settings, self._request())
        self.assertFalse(result.is_error)
        self.assertIsNone(result.structured_content["render"])

    @unittest.skipUnless(REAL_KICAD_CLI.is_file(), "KiCad CLI is not installed")
    def test_a_render_is_delivered_as_a_model_facing_resource_link(self) -> None:
        board = ROOT / "tests" / "fixtures" / "circuit-scene-v0.1"
        settings = replace(
            _server._SETTINGS,
            workspace=board.resolve(),
            transport="stdio",
            kicad_cli=REAL_KICAD_CLI,
        )
        result = self._call(settings, self._request(include_render=True))

        self.assertFalse(result.is_error)
        links = [item for item in result.content if isinstance(item, ResourceLink)]
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual(link.mime_type, "image/svg+xml")
        self.assertIsNotNone(link.annotations)
        assert link.annotations is not None
        # Model-facing. A human thumbnail would be a separate artifact with audience ["user"].
        self.assertEqual(link.annotations.audience, ["assistant"])
        self.assertRegex(str(link.uri), r"^pcb://artifacts/scene/[A-Za-z0-9_-]{43}/board\.svg$")

        render = result.structured_content["render"]
        self.assertEqual(render["resource_uri"], str(link.uri))
        payload = self.store.read(str(link.uri).split("/")[-2])
        self.assertEqual(len(payload), render["byte_count"])
        self.assertEqual(
            f"sha256:{hashlib.sha256(payload).hexdigest()}", render["normalized_digest"]
        )
        self.assertNotIn(b"CANARY", payload)


if __name__ == "__main__":
    unittest.main()
