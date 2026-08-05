"""KiCad IPC action entrypoint for the optional CopperMCP live observer."""

from __future__ import annotations

import json

from copper_mcp.kicad_ipc import KicadIpcError, inspect_live_board


def main() -> int:
    """Print only redacted observation metadata; never mutate the KiCad document."""

    try:
        observation = inspect_live_board()
    except KicadIpcError as error:
        # Do not echo socket paths, KiCad messages, board text, or tokens into KiCad's
        # warning bar. The class name is stable, actionable, and contains no board data.
        print(f"CopperMCP IPC observer unavailable: {error.__class__.__name__}")
        return 1
    print(json.dumps(observation.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
