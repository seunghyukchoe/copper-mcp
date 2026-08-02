"""Internal POSIX process wrapper for enforcing KiCad output limits before execution."""

from __future__ import annotations

import os
import sys


def main(arguments: list[str] | None = None) -> int:
    """Set a per-process file-size ceiling, then replace this process with KiCad."""

    args = list(sys.argv[1:] if arguments is None else arguments)
    if len(args) < 2:
        return 64
    try:
        import resource

        max_bytes = int(args[0])
        if max_bytes <= 0:
            return 64
        executable = args[1]
        command = args[1:]
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))
        os.execv(executable, command)  # noqa: S606 - parent supplies a validated fixed executable
    except (ImportError, OSError, ValueError):
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
