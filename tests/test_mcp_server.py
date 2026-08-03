from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

from copper_mcp.circuit_intent_service import build_schematic_from_content
from copper_mcp.mcp_server import mcp

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
    def test_declares_expected_read_only_tools(self) -> None:
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual(
            {tool.name for tool in tools},
            {
                "compare_candidates",
                "inspect_board",
                "inspect_board_ir",
                "preview_route",
                "render_circuit_schematic",
                "run_board_drc",
                "server_info",
                "validate_candidate",
            },
        )

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

        serialized = json.dumps(document, sort_keys=True)
        tool_text = "\n".join(block.text for block in result.content if block.type == "text")
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


if __name__ == "__main__":
    unittest.main()
