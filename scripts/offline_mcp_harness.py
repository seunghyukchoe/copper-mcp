"""Own an offline server instance without configuring the application's shared server.

This development helper loads only the repository's fixed MCP implementation. It neither starts
a transport nor changes the canonical ``copper_mcp.mcp_server`` module. Harness calls still patch
their private instance's settings explicitly; these instances are not production services.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
_SERVER_NAME = "_copper_mcp_offline_harness_server"
_SERVER_SOURCE = ROOT / "src" / "copper_mcp" / "mcp_server.py"


def load_offline_mcp_server() -> ModuleType:
    """Initialize safe defaults once, preserving caller environment and canonical imports."""

    existing = sys.modules.get(_SERVER_NAME)
    if existing is not None:
        if getattr(existing, "__file__", None) != str(_SERVER_SOURCE):
            raise RuntimeError("offline harness server module name is occupied")
        return existing
    spec = importlib.util.spec_from_file_location(_SERVER_NAME, _SERVER_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("offline harness server source is unavailable")
    server = importlib.util.module_from_spec(spec)
    # Registration needs a real module for type introspection, not a copied runpy namespace.
    sys.modules[_SERVER_NAME] = server
    try:
        with patch.dict(
            os.environ,
            {
                **{
                    name: value
                    for name, value in os.environ.items()
                    if not name.startswith("COPPER_MCP_")
                },
                "COPPER_MCP_WORKSPACE": str(ROOT),
            },
            clear=True,
        ):
            spec.loader.exec_module(server)
    except BaseException:
        # An interrupted/failed initialization must not be reused as a complete server.
        sys.modules.pop(_SERVER_NAME, None)
        raise
    return server
