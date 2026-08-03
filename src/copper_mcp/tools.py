"""Pure application services shared by the CLI and MCP gateway."""

from __future__ import annotations

from typing import Any

from copper_mcp import __version__
from copper_mcp.board_ir_service import summarize_board_ir
from copper_mcp.circuit_intent_service import (
    CircuitSchematicBuild,
    build_schematic_from_content,
)
from copper_mcp.circuit_scene import CircuitScene
from copper_mcp.circuit_scene import observe_board_scene as observe_scene
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import run_board_drc as run_kicad_board_drc
from copper_mcp.kicad_file import inspect_kicad_board
from copper_mcp.models import candidate_from_dict, rank_candidates
from copper_mcp.route_preview import preview_route as preview_route_candidate


def server_info() -> dict[str, Any]:
    """Describe implemented and planned capabilities without overstating maturity."""

    return {
        "name": "CopperMCP",
        "version": __version__,
        "maturity": "mvp",
        "implemented": [
            "bounded KiCad board inspection",
            "content-addressed board revisions",
            "authoritative read-only KiCad DRC summaries",
            "candidate manifest validation",
            "deterministic candidate ranking",
            "read-only Board IR structural inspection",
            "region-scoped semantic Circuit Scene observation with quarantined board text",
            "opt-in deterministic digest-bound copper-only board rendering",
            "non-mutating two-pin route preview on a documented Board IR subset",
            "bounded Circuit Intent validation and deterministic KiCad schematic rendering",
            "explicit create-only CLI schematic export and ephemeral stdio MCP artifact delivery",
        ],
        "planned": [
            "region-scoped and human-facing board rendering",
            "validated placement preview and immutable placement candidates",
            "KiCad IPC adapter",
            "routing job lifecycle",
            "negotiated-congestion router",
            "immutable route patches",
            "explicit candidate application",
        ],
    }


def render_circuit_schematic(content: Any) -> CircuitSchematicBuild:
    """Validate structured circuit content and return a private schematic build."""

    return build_schematic_from_content(content)


def inspect_board(path: str, settings: Settings | None = None) -> dict[str, Any]:
    """Inspect a KiCad board beneath the configured workspace."""

    active_settings = settings or Settings.from_env()
    return inspect_kicad_board(path, active_settings).to_dict()


def run_board_drc(path: str, settings: Settings | None = None) -> dict[str, Any]:
    """Run authoritative KiCad DRC for a board beneath the configured workspace."""

    active_settings = settings or Settings.from_env()
    return run_kicad_board_drc(path, active_settings).to_dict()


def inspect_board_ir(payload: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """Describe one board's Board IR structure without disclosing its content."""

    active_settings = settings or Settings.from_env()
    return summarize_board_ir(payload, active_settings).to_dict()


def preview_route(payload: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """Preview one deterministic two-pin candidate without writing or applying anything."""

    active_settings = settings or Settings.from_env()
    return preview_route_candidate(payload, active_settings).to_dict()


def observe_board_scene_raw(
    payload: dict[str, Any], settings: Settings | None = None
) -> CircuitScene:
    """Observe one board and return the scene object, including any render bytes.

    The MCP gateway and the CLI both need the bytes, which never belong in the JSON
    response, so the shared service hands back the scene itself and each adapter decides how
    to deliver them.
    """

    active_settings = settings or Settings.from_env()
    return observe_scene(payload, active_settings)


def observe_board_scene(
    payload: dict[str, Any], settings: Settings | None = None
) -> dict[str, Any]:
    """Observe one board as a bounded, region-scoped Circuit Scene without modifying it."""

    return observe_board_scene_raw(payload, settings).to_dict()


def validate_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize an untrusted candidate manifest."""

    candidate = candidate_from_dict(payload)
    return {"valid": True, "candidate": candidate.to_dict()}


def compare_candidates(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Rank candidate manifests by correctness, then routing cost."""

    if not 1 <= len(payloads) <= 100:
        raise ValueError("between 1 and 100 candidates are required")
    ranked = rank_candidates([candidate_from_dict(payload) for payload in payloads])
    return {
        "ranking_policy": [
            "hard_drc_errors",
            "unrouted_connections",
            "vias",
            "wire_length_mm",
            "runtime_seconds",
            "candidate_id",
        ],
        "candidates": [candidate.to_dict() for candidate in ranked],
    }
