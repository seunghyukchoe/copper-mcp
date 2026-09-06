"""Deterministic routing facade; leaf imports do not initialize every routing backend."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from copper_mcp.routing._exports import *  # noqa: F403 - preserve the typed public facade


def __getattr__(name: str) -> Any:
    # Module __getattr__ preserves normal from-imports while keeping isolated policy-worker
    # startup independent of geometry, persistence and orchestration imports.
    # https://docs.python.org/3/reference/datamodel.html#customizing-module-attribute-access
    if name.startswith("_") and name != "__all__":
        raise AttributeError(name)
    exports = import_module("copper_mcp.routing._exports")
    if name != "__all__" and name not in exports.__all__:
        raise AttributeError(name)
    value = getattr(exports, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    exports = import_module("copper_mcp.routing._exports")
    return sorted(set(globals()) | set(exports.__all__))
