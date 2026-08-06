"""Validated process configuration.

The project intentionally reads the process environment directly and never loads
``.env`` files on behalf of callers. Secret-file loading belongs to the host or a
dedicated secret manager.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_ALLOWED_TRANSPORTS = frozenset({"stdio", "streamable-http"})


class ConfigurationError(ValueError):
    """Raised when process configuration is unsafe or invalid."""


def _bounded_int(name: str, raw: str, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings shared by the CLI and MCP gateway."""

    workspace: Path
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765
    max_board_bytes: int = 64 * 1024 * 1024
    kicad_cli: Path | None = None
    kicad_timeout_seconds: int = 120
    max_drc_report_bytes: int = 8 * 1024 * 1024
    max_drc_context_bytes: int = 128 * 1024 * 1024
    max_drc_context_files: int = 10_000
    max_drc_context_scan_seconds: int = 10
    max_route_preview_seconds: int = 30
    max_fill_vertices: int = 50_000
    # Provisional: CopperTone's whole board is ~120 objects. Raise after measuring a genuinely
    # dense board rather than guessing upward now.
    max_scene_objects: int = 2_000
    max_scene_vertices: int = 200_000
    max_render_bytes: int = 4 * 1024 * 1024
    max_scene_annotations: int = 5_000
    max_placement_subjects: int = 64
    max_placement_rules: int = 256
    max_placement_checks: int = 2_000_000
    max_placement_seconds: int = 10
    allow_apply: bool = False
    allow_live_ipc: bool = False
    #: Consent to mutate the *running editor's* in-memory document. Deliberately its own flag
    #: rather than the conjunction of the two above: ADR-0069 recorded that the live opt-in
    #: "enables observation only", and ADR-0025's flag is documented as replacing a file on
    #: disk. Reading an already-granted pair as mutation consent would retroactively widen what
    #: past operators agreed to. See ADR-0073.
    allow_live_apply: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        workspace = Path(os.environ.get("COPPER_MCP_WORKSPACE", str(Path.cwd()))).expanduser()
        workspace = workspace.resolve(strict=True)
        if not workspace.is_dir():
            raise ConfigurationError("COPPER_MCP_WORKSPACE must be a directory")

        transport = os.environ.get("COPPER_MCP_TRANSPORT", "stdio")
        if transport not in _ALLOWED_TRANSPORTS:
            allowed = ", ".join(sorted(_ALLOWED_TRANSPORTS))
            raise ConfigurationError(f"COPPER_MCP_TRANSPORT must be one of: {allowed}")

        host = os.environ.get("COPPER_MCP_HOST", "127.0.0.1").strip()
        if not host or any(character.isspace() for character in host):
            raise ConfigurationError("COPPER_MCP_HOST is invalid")

        port = _bounded_int("COPPER_MCP_PORT", os.environ.get("COPPER_MCP_PORT", "8765"), 1, 65535)
        max_board_bytes = _bounded_int(
            "COPPER_MCP_MAX_BOARD_BYTES",
            os.environ.get("COPPER_MCP_MAX_BOARD_BYTES", str(64 * 1024 * 1024)),
            1024,
            1024 * 1024 * 1024,
        )
        raw_kicad_cli = os.environ.get("COPPER_MCP_KICAD_CLI", "").strip()
        kicad_cli = Path(raw_kicad_cli).expanduser() if raw_kicad_cli else None
        kicad_timeout_seconds = _bounded_int(
            "COPPER_MCP_KICAD_TIMEOUT_SECONDS",
            os.environ.get("COPPER_MCP_KICAD_TIMEOUT_SECONDS", "120"),
            1,
            3600,
        )
        max_drc_report_bytes = _bounded_int(
            "COPPER_MCP_MAX_DRC_REPORT_BYTES",
            os.environ.get("COPPER_MCP_MAX_DRC_REPORT_BYTES", str(8 * 1024 * 1024)),
            1024,
            64 * 1024 * 1024,
        )
        max_drc_context_bytes = _bounded_int(
            "COPPER_MCP_MAX_DRC_CONTEXT_BYTES",
            os.environ.get("COPPER_MCP_MAX_DRC_CONTEXT_BYTES", str(128 * 1024 * 1024)),
            1024,
            1024 * 1024 * 1024,
        )
        max_drc_context_files = _bounded_int(
            "COPPER_MCP_MAX_DRC_CONTEXT_FILES",
            os.environ.get("COPPER_MCP_MAX_DRC_CONTEXT_FILES", "10000"),
            1,
            100_000,
        )
        max_drc_context_scan_seconds = _bounded_int(
            "COPPER_MCP_MAX_DRC_CONTEXT_SCAN_SECONDS",
            os.environ.get("COPPER_MCP_MAX_DRC_CONTEXT_SCAN_SECONDS", "10"),
            1,
            300,
        )
        max_route_preview_seconds = _bounded_int(
            "COPPER_MCP_MAX_ROUTE_PREVIEW_SECONDS",
            os.environ.get("COPPER_MCP_MAX_ROUTE_PREVIEW_SECONDS", "30"),
            1,
            600,
        )
        max_fill_vertices = _bounded_int(
            "COPPER_MCP_MAX_FILL_VERTICES",
            os.environ.get("COPPER_MCP_MAX_FILL_VERTICES", "50000"),
            3,
            1_000_000,
        )
        max_scene_objects = _bounded_int(
            "COPPER_MCP_MAX_SCENE_OBJECTS",
            os.environ.get("COPPER_MCP_MAX_SCENE_OBJECTS", "2000"),
            1,
            200_000,
        )
        max_scene_vertices = _bounded_int(
            "COPPER_MCP_MAX_SCENE_VERTICES",
            os.environ.get("COPPER_MCP_MAX_SCENE_VERTICES", "200000"),
            3,
            5_000_000,
        )
        max_render_bytes = _bounded_int(
            "COPPER_MCP_MAX_RENDER_BYTES",
            os.environ.get("COPPER_MCP_MAX_RENDER_BYTES", str(4 * 1024 * 1024)),
            1024,
            64 * 1024 * 1024,
        )
        max_scene_annotations = _bounded_int(
            "COPPER_MCP_MAX_SCENE_ANNOTATIONS",
            os.environ.get("COPPER_MCP_MAX_SCENE_ANNOTATIONS", "5000"),
            1,
            1_000_000,
        )
        max_placement_subjects = _bounded_int(
            "COPPER_MCP_MAX_PLACEMENT_SUBJECTS",
            os.environ.get("COPPER_MCP_MAX_PLACEMENT_SUBJECTS", "64"),
            1,
            4_096,
        )
        max_placement_rules = _bounded_int(
            "COPPER_MCP_MAX_PLACEMENT_RULES",
            os.environ.get("COPPER_MCP_MAX_PLACEMENT_RULES", "256"),
            0,
            16_384,
        )
        max_placement_checks = _bounded_int(
            "COPPER_MCP_MAX_PLACEMENT_CHECKS",
            os.environ.get("COPPER_MCP_MAX_PLACEMENT_CHECKS", "2000000"),
            1,
            100_000_000,
        )
        max_placement_seconds = _bounded_int(
            "COPPER_MCP_MAX_PLACEMENT_SECONDS",
            os.environ.get("COPPER_MCP_MAX_PLACEMENT_SECONDS", "10"),
            1,
            600,
        )
        raw_allow_apply = os.environ.get("COPPER_MCP_ALLOW_APPLY", "0")
        if raw_allow_apply not in {"0", "1"}:
            # Exact membership, no case folding and no truthiness. "false", "no" and "" would
            # all be truthy under bool(), and a flag that enables board mutation must never be
            # switched on by an ambiguous spelling.
            raise ConfigurationError('COPPER_MCP_ALLOW_APPLY must be exactly "0" or "1"')
        raw_allow_live_ipc = os.environ.get("COPPER_MCP_ALLOW_LIVE_IPC", "0")
        if raw_allow_live_ipc not in {"0", "1"}:
            # Same exact-membership rule as the apply flag. Connecting to whatever socket the
            # official binding defaults to is an outbound action against the operator's running
            # editor, so it must never be switched on by an ambiguous spelling either.
            raise ConfigurationError('COPPER_MCP_ALLOW_LIVE_IPC must be exactly "0" or "1"')
        raw_allow_live_apply = os.environ.get("COPPER_MCP_ALLOW_LIVE_APPLY", "0")
        if raw_allow_live_apply not in {"0", "1"}:
            # Same exact-membership rule again, for the same reason: this flag is the only
            # consent that authorizes mutating a document the operator has open in front of
            # them, and no ambiguous spelling may switch it on.
            raise ConfigurationError('COPPER_MCP_ALLOW_LIVE_APPLY must be exactly "0" or "1"')
        return cls(
            workspace=workspace,
            transport=transport,
            host=host,
            port=port,
            max_board_bytes=max_board_bytes,
            kicad_cli=kicad_cli,
            kicad_timeout_seconds=kicad_timeout_seconds,
            max_drc_report_bytes=max_drc_report_bytes,
            max_drc_context_bytes=max_drc_context_bytes,
            max_drc_context_files=max_drc_context_files,
            max_drc_context_scan_seconds=max_drc_context_scan_seconds,
            max_route_preview_seconds=max_route_preview_seconds,
            max_fill_vertices=max_fill_vertices,
            max_scene_objects=max_scene_objects,
            max_scene_vertices=max_scene_vertices,
            max_render_bytes=max_render_bytes,
            max_scene_annotations=max_scene_annotations,
            max_placement_subjects=max_placement_subjects,
            max_placement_rules=max_placement_rules,
            max_placement_checks=max_placement_checks,
            max_placement_seconds=max_placement_seconds,
            allow_apply=raw_allow_apply == "1",
            allow_live_ipc=raw_allow_live_ipc == "1",
            allow_live_apply=raw_allow_live_apply == "1",
        )
