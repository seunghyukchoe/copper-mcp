"""CopperMCP public package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("copper-mcp")
except PackageNotFoundError:  # Source checkout without an editable install.
    __version__ = "0.1.0"

__all__ = ["__version__"]
