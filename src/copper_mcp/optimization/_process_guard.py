"""Keep an owned process-group leader live until its supervisor confirms group cleanup.

Only the guardian owns the status writer. The fixed worker and its descendants cannot report
their own successful exit. No MCP input selects a program, descriptor or guardian deadline.
"""

from __future__ import annotations

import math
import os
import signal
import struct
import sys
import time
from pathlib import Path
from typing import NoReturn

CLEANUP_GRACE_SECONDS = 5


def supervise(command: list[str], status_fd: int, deadline: float) -> NoReturn:
    """Internal fixed-command seam; tests supply owned workers, never an MCP command."""
    if os.getpgrp() != os.getpid() or status_fd < 3 or not math.isfinite(deadline):
        raise ValueError("guardian ownership is invalid")

    def stop_group(*_args: object) -> NoReturn:
        os.killpg(os.getpid(), signal.SIGKILL)
        os._exit(1)  # Unreachable after a successful self-group SIGKILL.

    signal.signal(signal.SIGALRM, stop_group)
    signal.setitimer(
        signal.ITIMER_REAL, max(0.001, deadline + CLEANUP_GRACE_SECONDS - time.monotonic())
    )
    try:
        worker = os.fork()
        if worker == 0:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            os.close(status_fd)
            try:
                os.execv(sys.executable, command)  # noqa: S606 - fixed interpreter, internal worker argv
            except OSError:
                os._exit(127)
        os.close(0)
        os.close(1)
        _pid, status = os.waitpid(worker, 0)
        frame = struct.pack("!i", os.waitstatus_to_exitcode(status))
        if os.write(status_fd, frame) != len(frame):
            stop_group()
        os.close(status_fd)
        while True:
            signal.pause()
    finally:
        # This includes a lost parent/status reader, fork/wait/write errors and the watchdog.
        # Exiting only the guardian would relinquish the PGID while descendants could survive.
        stop_group()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(2)
    supervise(
        [sys.executable, "-I", str(Path(__file__).with_name("isolated_entry.py"))],
        int(sys.argv[1]),
        float(sys.argv[2]),
    )
