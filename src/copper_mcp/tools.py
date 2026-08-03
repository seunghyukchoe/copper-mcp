"""Pure application services shared by the CLI and MCP gateway."""

from __future__ import annotations

from typing import Any

from copper_mcp import __version__
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
        "maturity": "pre-alpha",
        "implemented": [
            "bounded KiCad board inspection",
            "content-addressed board revisions",
            "authoritative read-only KiCad DRC summaries",
            "candidate manifest validation",
            "deterministic candidate ranking",
            "non-mutating two-pin route preview on a documented Board IR subset",
        ],
        "planned": [
            "KiCad IPC adapter",
            "routing job lifecycle",
            "negotiated-congestion router",
            "immutable route patches",
            "explicit candidate application",
        ],
    }


def inspect_board(path: str, settings: Settings | None = None) -> dict[str, Any]:
    """Inspect a KiCad board beneath the configured workspace."""

    active_settings = settings or Settings.from_env()
    return inspect_kicad_board(path, active_settings).to_dict()


def run_board_drc(path: str, settings: Settings | None = None) -> dict[str, Any]:
    """Run authoritative KiCad DRC for a board beneath the configured workspace."""

    active_settings = settings or Settings.from_env()
    return run_kicad_board_drc(path, active_settings).to_dict()


def preview_route(payload: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """Preview one deterministic two-pin candidate without writing or applying anything."""

    active_settings = settings or Settings.from_env()
    return preview_route_candidate(payload, active_settings).to_dict()


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
