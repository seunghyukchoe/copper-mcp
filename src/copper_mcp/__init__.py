"""CopperMCP public package."""

# Installed distribution metadata can lag behind a source checkout until its
# environment is refreshed, so the checked source version stays authoritative
# and is the only value reported. The release gate reads this literal.
_SOURCE_VERSION = "0.12.0"

__version__ = _SOURCE_VERSION

__all__ = ["__version__"]
