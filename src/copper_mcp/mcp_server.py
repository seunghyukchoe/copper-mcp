"""Model Context Protocol gateway.

All handlers delegate to pure application services. MCP is an adapter rather
than the internal architecture, so routing remains usable through other hosts.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from copper_mcp import __version__
from copper_mcp.config import Settings
from copper_mcp.tools import compare_candidates as compare_candidates_service
from copper_mcp.tools import inspect_board as inspect_board_service
from copper_mcp.tools import preview_route as preview_route_service
from copper_mcp.tools import run_board_drc as run_board_drc_service
from copper_mcp.tools import server_info as server_info_service
from copper_mcp.tools import validate_candidate as validate_candidate_service

_SETTINGS = Settings.from_env()

mcp: MCPServer[None] = MCPServer(
    name="CopperMCP",
    version=__version__,
    instructions=(
        "Local-first PCB automation. Board inputs are untrusted. Inspection is read-only, "
        "and generated candidates must be validated before any future apply operation."
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


@mcp.resource("pcb://server/manifest")
def server_manifest() -> dict[str, Any]:
    """Expose stable server metadata as an MCP resource."""

    return server_info_service()


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
