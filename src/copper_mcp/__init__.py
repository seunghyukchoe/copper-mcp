"""CopperMCP public package."""

from importlib.metadata import PackageNotFoundError, version

_SOURCE_VERSION = "0.2.0"

try:
    __version__ = version("copper-mcp")
except PackageNotFoundError:  # Source checkout without an editable install.
    __version__ = _SOURCE_VERSION

# Editable metadata can lag behind a source checkout until its environment is
# refreshed. The checked source version remains authoritative in that case.
if __version__ != _SOURCE_VERSION:
    __version__ = _SOURCE_VERSION

__all__ = ["__version__"]
