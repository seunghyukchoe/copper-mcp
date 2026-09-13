"""Real guardian exit/descriptor ownership and bounded failure delivery controls."""

import errno
import os
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_optimization_isolated import owned_guard_command

from copper_mcp.optimization import isolated
from copper_mcp.optimization.contracts import OptimizationError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires owned POSIX processes")


@pytest.mark.parametrize(
    "end",
    [
        "raise SystemExit(7)",
        "os.kill(os.getpid(),signal.SIGTERM)",
        "os.execv('/nonexistent-owned-worker',[])",
    ],
)
def test_worker_failure_is_not_relabelled_as_success(monkeypatch, end):
    popen = subprocess.Popen
    processes = []

    def launch(command, **kwargs):
        process = popen(
            owned_guard_command(command, "import os,signal,sys;sys.stdin.buffer.read();" + end),
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(isolated.subprocess, "Popen", launch)
    with pytest.raises(OptimizationError, match="failed"):
        isolated._exchange(b"owned", time.monotonic() + 10, lambda: False)
    assert processes[0].returncode == -signal.SIGKILL


def test_worker_does_not_inherit_the_completion_writer(monkeypatch):
    popen = subprocess.Popen

    def launch(command, **kwargs):
        code = f"""import errno,os,sys
sys.stdin.buffer.read()
try: os.fstat({command[3]})
except OSError as error: assert error.errno==errno.EBADF
else: raise SystemExit(79)
sys.stdout.buffer.write(b'writer-is-private')
"""
        return popen(owned_guard_command(command, code), **kwargs)

    monkeypatch.setattr(isolated.subprocess, "Popen", launch)
    assert (
        isolated._exchange(b"owned", time.monotonic() + 10, lambda: False) == b"writer-is-private"
    )


@pytest.mark.parametrize("frame", [b"", b"\0\0", b"\0" * 8, b"garbage", struct.pack("!i", 999999)])
def test_malformed_completion_frame_never_delivers(monkeypatch, frame):
    popen = subprocess.Popen
    processes = []

    def launch(command, **kwargs):
        code = f"""import os,signal,sys,time
sys.stdin.buffer.read()
os.close(1)
os.write({command[3]}, {frame!r})
os.close({command[3]})
while True: signal.pause()
"""
        process = popen([sys.executable, "-I", "-c", code], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(isolated.subprocess, "Popen", launch)
    with pytest.raises(OptimizationError):
        isolated._exchange(b"owned", time.monotonic() + 10, lambda: False)
    assert processes[0].returncode == -signal.SIGKILL


def test_missing_guardian_group_is_not_a_success(monkeypatch):
    def gone(*_args):
        raise ProcessLookupError(errno.ESRCH, "owned")

    monkeypatch.setattr(os, "killpg", gone)
    process = SimpleNamespace(pid=123, returncode=None, poll=lambda: 0)
    with pytest.raises(OptimizationError, match="cleanup unverified"):
        isolated._terminate(process, require_group=True)


def test_guardian_early_death_withholds_delivery_and_stops_worker(monkeypatch):
    popen = subprocess.Popen
    reader, writer = os.pipe()
    processes = []

    def launch(command, **kwargs):
        process = popen(
            owned_guard_command(
                command,
                "import os,signal,sys,time;sys.stdin.buffer.read();"
                "os.kill(os.getppid(),signal.SIGKILL);time.sleep(30)",
            ),
            **{**kwargs, "pass_fds": (*kwargs["pass_fds"], writer)},
        )
        processes.append(process)
        os.close(writer)
        return process

    monkeypatch.setattr(isolated.subprocess, "Popen", launch)
    gone = False
    try:
        with pytest.raises(OptimizationError, match="status is malformed"):
            isolated._exchange(b"owned", time.monotonic() + 10, lambda: False)
        readable, _, _ = isolated.select.select([reader], [], [], 2)
        gone = bool(readable) and os.read(reader, 1) == b""
        assert gone and processes[0].returncode == -signal.SIGKILL
    finally:
        if not gone and processes:
            os.killpg(processes[0].pid, signal.SIGKILL)
            processes[0].wait(timeout=5)
        os.close(reader)


@pytest.mark.parametrize("loss", ["deadline", "parent-reader"])
def test_guardian_cannot_park_forever_without_parent_cleanup(loss):
    reader, writer = os.pipe()
    guard = Path(isolated.__file__).with_name("_process_guard.py")
    deadline = time.monotonic() + (0.2 if loss == "deadline" else 10)
    command = [sys.executable, "-I", str(guard), str(writer), str(deadline)]
    worker = "import time;time.sleep(30)" if loss == "deadline" else "pass"
    process = subprocess.Popen(  # noqa: S603 - fixed owned guardian and worker
        owned_guard_command(command, worker),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        pass_fds=(writer,),
    )
    os.close(writer)
    if loss == "parent-reader":
        os.close(reader)
    try:
        assert process.wait(timeout=8) == -signal.SIGKILL
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        if loss == "deadline":
            os.close(reader)
