"""KiCad IPC action entrypoint for the optional CopperMCP live observer."""

from __future__ import annotations

import json

# The unavailable-message vocabulary. Every path out of this entrypoint prints exactly one of
# these three shapes, and none of them carries a socket path, a board string, a filesystem path,
# or the KICAD_API_TOKEN KiCad puts in this process's environment.
_UNAVAILABLE = "CopperMCP IPC observer unavailable: {reason}"
_NOT_INSTALLED = (
    "CopperMCP is not installed in this KiCad plugin environment. Install it into the Python "
    "interpreter configured under Preferences > Plugins: pip install 'copper-mcp[kicad]'"
)


def main() -> int:
    """Print only redacted observation metadata; never mutate the KiCad document."""

    try:
        from copper_mcp.kicad_ipc import KicadIpcError, inspect_live_board
    except ImportError:
        # Installing the package from the Plugin and Content Manager installs this file, not
        # CopperMCP: KiCad's per-plugin requirements.txt is resolved against PyPI with
        # `--only-binary :all:`, and copper-mcp is deliberately not published there. Without this
        # branch a PCM user's first click is an unhandled traceback whose text is a filesystem
        # path. The exception is not echoed -- only the fixed, actionable sentence is.
        print(_UNAVAILABLE.format(reason=_NOT_INSTALLED))
        return 1

    try:
        observation = inspect_live_board()
    except KicadIpcError as error:
        # Do not echo socket paths, KiCad messages, board text, or tokens into KiCad's
        # warning bar. The class name is stable, actionable, and contains no board data.
        print(_UNAVAILABLE.format(reason=error.__class__.__name__))
        return 1
    print(json.dumps(observation.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
