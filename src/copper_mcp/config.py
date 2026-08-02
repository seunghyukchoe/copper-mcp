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
        return cls(
            workspace=workspace,
            transport=transport,
            host=host,
            port=port,
            max_board_bytes=max_board_bytes,
        )
