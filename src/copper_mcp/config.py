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
        )
