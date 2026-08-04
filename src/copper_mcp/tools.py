"""Pure application services shared by the CLI and MCP gateway."""

from __future__ import annotations

from typing import Any

from copper_mcp import __version__
from copper_mcp.apply.service import apply_candidate as apply_candidate_service
from copper_mcp.board_ir_service import summarize_board_ir
from copper_mcp.circuit_intent_service import (
    CircuitSchematicBuild,
    build_schematic_from_content,
)
from copper_mcp.circuit_scene import CircuitScene
from copper_mcp.circuit_scene import observe_board_scene as observe_scene
from copper_mcp.circuit_scene import observe_live_board_scene as observe_live_scene
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import run_board_drc as run_kicad_board_drc
from copper_mcp.kicad_file import inspect_kicad_board
from copper_mcp.kicad_ipc import inspect_live_board as inspect_live_kicad_board
from copper_mcp.layered_route_preview import preview_layered_route as preview_layered_route_service
from copper_mcp.live_editor_context import (
    LiveEditorContext,
)
from copper_mcp.live_editor_context import (
    inspect_live_editor_context as inspect_live_editor_context_service,
)
from copper_mcp.live_editor_context import (
    inspect_live_editor_context_raw as inspect_live_editor_context_service_raw,
)
from copper_mcp.live_layered_route_preview import (
    preview_live_layered_route as preview_live_layered_route_service,
)
from copper_mcp.models import candidate_from_dict, rank_candidates
from copper_mcp.placement.contracts import PlacementResult
from copper_mcp.placement_preview import preview_live_placement as preview_live_placement_service
from copper_mcp.placement_preview import preview_placement as preview_placement_service
from copper_mcp.route_preview import RoutePreview
from copper_mcp.route_preview import preview_live_route as preview_live_route_candidate
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
            "typed placement intent with deterministic legality preview (no apply, no DRC binding)",
            "operator-gated, token-authorized route-candidate apply with atomic replacement",
            "non-mutating two-pin route preview on a documented Board IR subset",
            "bounded Circuit Intent validation and deterministic KiCad schematic rendering",
            "explicit create-only CLI schematic export and ephemeral stdio MCP artifact delivery",
            "read-only live KiCad IPC board observation (optional kicad-python)",
            "revision-bound live KiCad IPC Circuit Scene observation (read-only)",
            "revision-bound live KiCad IPC route proposal (read-only)",
            "revision-bound live KiCad IPC placement proposal (read-only)",
            "revision-bound live KiCad IPC editor context (read-only)",
            "revision-bound layered route proposal (read-only)",
            "opt-in authoritative DRC evidence for file-backed layered proposals",
            "durable file-backed layered routing jobs with bounded worker execution",
            "authorization-bound candidate geometry export",
            "revision-bound live layered route proposal (read-only)",
        ],
        "planned": [
            "region-scoped and human-facing board rendering",
            "authoritative KiCad DRC binding for placement candidates",
            "explicit placement apply and post-placement observation",
            "live placement/routing action compare-and-swap over KiCad IPC",
            "MCP Tasks negotiated progressive enhancement",
            "multilayer generalization beyond the two-signal subset",
            "negotiated-congestion router",
            "immutable route patches",
            "placement apply and post-placement observation",
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


def inspect_live_board(settings: Settings | None = None) -> dict[str, Any]:
    """Observe one open KiCad PCB through the optional local IPC adapter."""

    return inspect_live_kicad_board(settings).to_dict()


def inspect_live_editor_context_raw(
    payload: dict[str, Any],
    settings: Settings | None = None,
    *,
    client_factory: Any = None,
) -> LiveEditorContext:
    """Inspect active layer and native selection refs without board text or mutation."""

    active_settings = settings or Settings.from_env()
    return inspect_live_editor_context_service_raw(
        payload,
        active_settings,
        client_factory=client_factory,
    )


def inspect_live_editor_context(
    payload: dict[str, Any], settings: Settings | None = None
) -> dict[str, Any]:
    """Return a detached revision-bound live editor context."""

    active_settings = settings or Settings.from_env()
    return inspect_live_editor_context_service(payload, active_settings)


def inspect_board_ir(payload: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """Describe one board's Board IR structure without disclosing its content."""

    active_settings = settings or Settings.from_env()
    return summarize_board_ir(payload, active_settings).to_dict()


def preview_route(
    payload: dict[str, Any],
    settings: Settings | None = None,
    token_authority: Any = None,
) -> dict[str, Any]:
    """Preview one deterministic two-pin candidate without writing or applying anything."""

    active_settings = settings or Settings.from_env()
    return preview_route_candidate(payload, active_settings, token_authority).to_dict()


def preview_live_route_raw(
    payload: dict[str, Any],
    settings: Settings | None = None,
    *,
    client_factory: Any = None,
) -> RoutePreview:
    """Propose one route against the exact active KiCad IPC snapshot without mutation."""

    active_settings = settings or Settings.from_env()
    return preview_live_route_candidate(
        payload,
        active_settings,
        client_factory=client_factory,
    )


def preview_live_route(payload: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """Return a revision-bound live route proposal as a detached dictionary."""

    return preview_live_route_raw(payload, settings).to_dict()


def preview_layered_route(
    payload: dict[str, Any], settings: Settings | None = None
) -> dict[str, Any]:
    """Return a revision-bound two-signal-layer proposal with optional private DRC evidence."""

    active_settings = settings or Settings.from_env()
    return preview_layered_route_service(payload, active_settings)


def preview_live_layered_route_raw(
    payload: dict[str, Any],
    settings: Settings | None = None,
    *,
    client_factory: Any = None,
) -> dict[str, object]:
    """Propose a bounded layered route from one exact active KiCad snapshot."""

    active_settings = settings or Settings.from_env()
    return preview_live_layered_route_service(
        payload,
        active_settings,
        client_factory=client_factory,
    )


def preview_live_layered_route(
    payload: dict[str, Any], settings: Settings | None = None
) -> dict[str, object]:
    """Return a detached read-only live layered route proposal."""

    return preview_live_layered_route_raw(payload, settings)


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


def observe_live_board_scene_raw(
    payload: dict[str, Any],
    settings: Settings | None = None,
    *,
    client_factory: Any = None,
) -> CircuitScene:
    """Observe the active KiCad IPC document and bind its bytes to a Circuit Scene."""

    active_settings = settings or Settings.from_env()
    return observe_live_scene(payload, active_settings, client_factory=client_factory)


def observe_live_board_scene(
    payload: dict[str, Any], settings: Settings | None = None
) -> dict[str, Any]:
    """Return a redaction-safe Circuit Scene bound to one active KiCad IPC snapshot."""

    return observe_live_board_scene_raw(payload, settings).to_dict()


def preview_placement(payload: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """Validate one placement proposal against a workspace board without modifying it."""

    active_settings = settings or Settings.from_env()
    return preview_placement_service(payload, active_settings).to_dict()


def preview_live_placement_raw(
    payload: dict[str, Any],
    settings: Settings | None = None,
    *,
    client_factory: Any = None,
) -> PlacementResult:
    """Propose one placement against the exact active KiCad IPC snapshot."""

    active_settings = settings or Settings.from_env()
    return preview_live_placement_service(
        payload,
        active_settings,
        client_factory=client_factory,
    )


def preview_live_placement(
    payload: dict[str, Any], settings: Settings | None = None
) -> dict[str, Any]:
    """Return a detached revision-bound live placement proposal."""

    return preview_live_placement_raw(payload, settings).to_dict()


def apply_candidate(
    payload: dict[str, Any],
    settings: Settings | None = None,
    token_authority: Any = None,
) -> dict[str, Any]:
    """Apply one authorized route candidate to a workspace board."""

    active_settings = settings or Settings.from_env()
    return apply_candidate_service(payload, active_settings, token_authority).to_dict()


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
