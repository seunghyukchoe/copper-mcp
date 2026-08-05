#!/usr/bin/env python3
"""Print a redacted, read-only live KiCad IPC capability result as canonical JSON."""

from __future__ import annotations

import json

from copper_mcp.kicad_ipc_oracle import probe_live_kicad_ipc


def main() -> int:
    """Run without changing KiCad settings, documents, or process environment."""

    result = probe_live_kicad_ipc()
    print(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
    # An unavailable editor is an expected capability result, including in CI.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
