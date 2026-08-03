"""Model Context Protocol gateway.

All handlers delegate to pure application services. MCP is an adapter rather
than the internal architecture, so routing remains usable through other hosts.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, InputRequiredResult, Tool, ToolAnnotations

from copper_mcp import __version__
from copper_mcp.circuit_intent_service import KICAD_SCHEMATIC_MIME_TYPE
from copper_mcp.config import Settings
from copper_mcp.mcp_contracts import (
    CircuitIntentToolContent,
    CircuitSchematicToolResponse,
)
from copper_mcp.schematic_artifacts import (
    SCHEMATIC_ARTIFACT_TTL_SECONDS,
    SCHEMATIC_ARTIFACT_URI_TEMPLATE,
    SchematicArtifactStore,
    SchematicArtifactUnavailableError,
)
from copper_mcp.tools import compare_candidates as compare_candidates_service
from copper_mcp.tools import inspect_board as inspect_board_service
from copper_mcp.tools import inspect_board_ir as inspect_board_ir_service
from copper_mcp.tools import preview_route as preview_route_service
from copper_mcp.tools import render_circuit_schematic as render_circuit_schematic_service
from copper_mcp.tools import run_board_drc as run_board_drc_service
from copper_mcp.tools import server_info as server_info_service
from copper_mcp.tools import validate_candidate as validate_candidate_service

_SETTINGS = Settings.from_env()
_SCHEMATIC_ARTIFACTS = SchematicArtifactStore()


class CopperMCPServer(MCPServer[None]):
    """MCP server with a private-value-safe schematic argument boundary."""

    async def list_tools(self) -> list[Tool]:
        """Advertise the schematic wrapper as a closed argument object."""

        listed = await super().list_tools()
        result: list[Tool] = []
        for tool in listed:
            if tool.name != "render_circuit_schematic":
                result.append(tool)
                continue
            schema = dict(tool.input_schema)
            schema["additionalProperties"] = False
            result.append(tool.model_copy(update={"input_schema": schema}))
        return result

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[None, Any] | None = None,
    ) -> CallToolResult | InputRequiredResult:
        """Reject unknown schematic wrapper fields before echoing validation can run."""

        if name == "render_circuit_schematic" and set(arguments) != {"content"}:
            raise ToolError("schematic tool arguments are malformed")
        return await super().call_tool(name, arguments, context)


mcp: CopperMCPServer = CopperMCPServer(
    name="CopperMCP",
    version=__version__,
    instructions=(
        "Local-first PCB automation. Board inputs are untrusted. Inspection is read-only, "
        "Circuit Intent is validated before deterministic schematic rendering, and generated "
        "candidates must be validated before any future apply operation."
    ),
)


@mcp.tool()
def server_info() -> dict[str, Any]:
    """Return server version, maturity, and implemented capabilities."""

    return server_info_service()


@mcp.tool()
def inspect_board(path: str) -> dict[str, Any]:
    """Inspect a .kicad_pcb file inside the configured workspace without modifying it."""

    return inspect_board_service(path, _SETTINGS)


@mcp.tool()
def run_board_drc(path: str) -> dict[str, Any]:
    """Run fixed-argument KiCad DRC and return a privacy-preserving summary."""

    return run_board_drc_service(path, _SETTINGS)


@mcp.tool()
def inspect_board_ir(request: dict[str, Any]) -> dict[str, Any]:
    """Report whether a board converts to the supported Board IR and describe its structure."""

    return inspect_board_ir_service(request, _SETTINGS)


@mcp.tool()
def preview_route(request: dict[str, Any]) -> dict[str, Any]:
    """Preview one deterministic two-pin route candidate without modifying any file."""

    return preview_route_service(request, _SETTINGS)


@mcp.tool()
def validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize an immutable route-candidate manifest."""

    return validate_candidate_service(candidate)


@mcp.tool()
def compare_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Rank candidates with hard DRC and connectivity correctness first."""

    return compare_candidates_service(candidates)


def render_circuit_schematic(
    content: CircuitIntentToolContent,
) -> CircuitSchematicToolResponse:
    """Render validated Circuit Intent content into one private ephemeral KiCad resource.

    ``content`` must contain exactly ``circuit_id``, ``project_name``, ``title``,
    ``components``, ``nets``, and ``ports`` under Circuit Intent 0.1.0. Components
    are two-pin resistors or non-polarized capacitors; nets carry explicit
    component-pin connections and ports identify external nets.
    """

    if _SETTINGS.transport != "stdio":
        raise ValueError("schematic artifact delivery is available only over stdio")
    build = render_circuit_schematic_service(content)
    resource_uri = _SCHEMATIC_ARTIFACTS.put(build.artifact)
    response = build.to_dict()
    response["artifact"]["resource_uri"] = resource_uri
    response["artifact"]["retention"] = {
        "scope": "process",
        "ttl_seconds": SCHEMATIC_ARTIFACT_TTL_SECONDS,
        "persistent": False,
        "reclamation": "lazy_on_access_or_process_exit",
    }
    return CircuitSchematicToolResponse.model_validate(response)


@mcp.resource("pcb://server/manifest")
def server_manifest() -> dict[str, Any]:
    """Expose stable server metadata as an MCP resource."""

    return server_info_service()


def schematic_artifact(token: str) -> bytes:
    """Read one live opaque schematic capability without enumerating stored artifacts."""

    try:
        return _SCHEMATIC_ARTIFACTS.read(token)
    except SchematicArtifactUnavailableError as error:
        raise MCPError(-32002, "schematic artifact is unavailable") from error


if _SETTINGS.transport == "stdio":
    mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        structured_output=True,
    )(render_circuit_schematic)
    mcp.resource(
        SCHEMATIC_ARTIFACT_URI_TEMPLATE,
        name="Circuit Intent KiCad schematic",
        description="Private process-local schematic bytes created by render_circuit_schematic.",
        mime_type=KICAD_SCHEMATIC_MIME_TYPE,
    )(schematic_artifact)


def main() -> None:
    """Run the configured MCP transport."""

    if _SETTINGS.transport == "stdio":
        mcp.run()
        return
    mcp.run(
        "streamable-http",
        host=_SETTINGS.host,
        port=_SETTINGS.port,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
