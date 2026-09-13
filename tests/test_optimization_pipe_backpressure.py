"""The real supervisor tolerates transient pipe backpressure without replacing its child."""

import errno
import os
import select
import signal
import time
from types import SimpleNamespace

import pytest
from test_optimization_isolated import owned_guard_command

from copper_mcp.optimization import isolated
from copper_mcp.optimization.contracts import OptimizationError


@pytest.mark.skipif(os.name != "posix", reason="requires owned POSIX process groups")
@pytest.mark.parametrize("close_stdout", [True, False], ids=["closed-stdout", "held-stdout"])
def test_successful_exchange_stops_a_descendant_that_closed_stdout(monkeypatch, close_stdout):
    launch = isolated.subprocess.Popen
    sentinel_read, sentinel_write = os.pipe()
    processes = []

    def owned_group(command, **kwargs):
        process = launch(
            owned_guard_command(
                command,
                "import os,sys,time\n"
                "data=sys.stdin.buffer.read()\n"
                "if os.fork():\n"
                " sys.stdout.buffer.write(data);sys.stdout.flush();os._exit(0)\n"
                + ("os.close(1)\n" if close_stdout else "")
                + "while True: time.sleep(1)\n",
            ),
            **{**kwargs, "pass_fds": (*kwargs["pass_fds"], sentinel_write)},
        )
        processes.append(process)
        os.close(sentinel_write)
        return process

    monkeypatch.setattr(isolated.subprocess, "Popen", owned_group)
    gone = False
    try:
        assert (
            isolated._exchange(b"owned response", time.monotonic() + 10, lambda: False)
            == b"owned response"
        )
        readable, _, _ = select.select([sentinel_read], [], [], 1)
        gone = bool(readable) and os.read(sentinel_read, 1) == b""
        assert gone, "successful exchange leaked its closed-stdout descendant"
        assert processes[0].returncode == -signal.SIGKILL
    finally:
        if not gone and processes:
            os.killpg(processes[0].pid, signal.SIGKILL)
            processes[0].wait(timeout=5)
        os.close(sentinel_read)


@pytest.mark.parametrize("state", ["live", "exited", "poll-error"])
def test_denied_group_cleanup_never_becomes_success(monkeypatch, state):
    calls = []

    def denied(*_args):
        calls.append("signal")
        raise PermissionError("owned-private-canary")

    def poll():
        calls.append("poll")
        if state == "poll-error":
            raise OSError("owned-private-canary")
        return 0 if state == "exited" else None

    monkeypatch.setattr(os, "killpg", denied)
    process = SimpleNamespace(pid=123, returncode=None, poll=poll)
    with pytest.raises(OptimizationError, match="cleanup unverified") as caught:
        isolated._terminate(process)
    assert calls == ["signal", "poll"]
    assert caught.value.__context__ is None
    assert "owned-private-canary" not in str(caught.value)


@pytest.mark.skipif(os.name != "posix", reason="requires owned POSIX process groups")
def test_cancelled_guardian_does_not_leave_a_stdout_holding_descendant(monkeypatch):
    launch, read, clock = isolated.subprocess.Popen, os.read, time.monotonic
    sentinel_read, sentinel_write = os.pipe()
    processes = []
    ready = False
    writer_closed = False
    descendant_gone = False

    def owned_descendant(command, **kwargs):
        nonlocal writer_closed
        assert kwargs["start_new_session"] is True
        process = launch(
            owned_guard_command(
                command,
                "import os,sys,time\n"
                "if os.fork():\n"
                " while True: time.sleep(1)\n"
                "os.write(1,b'ready')\n"
                "while True: time.sleep(1)\n",
            ),
            **{**kwargs, "pass_fds": (*kwargs["pass_fds"], sentinel_write)},
        )
        processes.append(process)
        os.close(sentinel_write)
        writer_closed = True
        return process

    def observed_read(fd, count):
        nonlocal ready
        result = read(fd, count)
        if (
            processes
            and not processes[0].stdout.closed
            and fd == processes[0].stdout.fileno()
            and result
        ):
            ready = True
        return result

    def outer_guard(*_args):
        raise TimeoutError("owned descendant control exceeded its outer guard")

    previous_handler = signal.signal(signal.SIGALRM, outer_guard)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 15)
    monkeypatch.setattr(isolated.subprocess, "Popen", owned_descendant)
    monkeypatch.setattr(os, "read", observed_read)
    deadline = clock() + 30
    monkeypatch.setattr(time, "monotonic", lambda: deadline + 1 if ready else clock())
    try:
        with pytest.raises(OptimizationError, match="stopped"):
            isolated._exchange(b"owned", deadline, lambda: False)
        assert ready and len(processes) == 1
        # Only the controlled grandchild holds this writer after the leader exits.
        readable, _, _ = select.select([sentinel_read], [], [], 2)
        descendant_gone = bool(readable) and read(sentinel_read, 1) == b""
        assert descendant_gone, "the owned descendant survived group termination"
        assert processes[0].returncode == -signal.SIGKILL
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)
        if not descendant_gone and processes:
            # A remaining sentinel writer belongs only to this test's fixed child group.
            try:
                os.killpg(processes[0].pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            processes[0].wait(timeout=5)
        if not writer_closed:
            os.close(sentinel_write)
        os.close(sentinel_read)


@pytest.fixture
def echo_child(monkeypatch):
    """Replace only the child application with an owned echo; keep real pipes/supervision."""
    launch = isolated.subprocess.Popen
    processes = []

    def echo(command, **kwargs):
        assert command[1] == "-I" and command[2].endswith("/_process_guard.py")
        process = launch(
            owned_guard_command(
                command, "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
            ),
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(isolated.subprocess, "Popen", echo)
    return processes


@pytest.mark.skipif(os.name != "posix", reason="supervisor requires POSIX pipes")
@pytest.mark.parametrize("direction", ["read", "write"])
def test_temporary_backpressure_keeps_one_child_and_complete_payload(
    monkeypatch, echo_child, direction
):
    original = getattr(os, direction)
    blocked = False

    def temporarily_unavailable(fd, *args):
        nonlocal blocked
        if echo_child:
            stream = echo_child[0].stdout if direction == "read" else echo_child[0].stdin
            if stream is not None and not stream.closed and fd == stream.fileno() and not blocked:
                blocked = True
                raise BlockingIOError(errno.EAGAIN, "owned transient backpressure")
        return original(fd, *args)

    monkeypatch.setattr(os, direction, temporarily_unavailable)
    payload = b"owned request\n" * 8192
    assert isolated._exchange(payload, time.monotonic() + 30, lambda: False) == payload
    assert blocked and len(echo_child) == 1
    assert echo_child[0].returncode == -signal.SIGKILL


@pytest.mark.skipif(os.name != "posix", reason="supervisor requires POSIX pipes")
@pytest.mark.parametrize("stop", ["deadline", "cancelled"])
def test_backpressure_does_not_disable_stopping(monkeypatch, echo_child, stop):
    original = os.write
    blocked = False

    def unavailable(fd, *args):
        nonlocal blocked
        if echo_child and echo_child[0].stdin is not None:
            stream = echo_child[0].stdin
            if not stream.closed and fd == stream.fileno():
                blocked = True
                raise BlockingIOError(errno.EAGAIN, "owned persistent backpressure")
        return original(fd, *args)

    monkeypatch.setattr(os, "write", unavailable)
    clock = time.monotonic
    deadline = clock() + 30
    if stop == "deadline":
        monkeypatch.setattr(time, "monotonic", lambda: deadline + 1 if blocked else clock())
    with pytest.raises(OptimizationError, match="stopped"):
        isolated._exchange(b"owned request", deadline, lambda: stop == "cancelled" and blocked)
    assert blocked and len(echo_child) == 1
    assert echo_child[0].returncode is not None
