"""Real inherited-pipe regressions, guarded outside the potentially stuck operation."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

_HARNESS = r'''
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from copper_mcp.optimization.confined_container import (
    ContainerProcessOwner, ContainerRouterLimits, OperatorContainerRuntime,
)

root = Path(sys.argv[1])
operation, escape = sys.argv[2], sys.argv[3] == "escape"
client_code = r"""
import os, subprocess, sys
from pathlib import Path
descendant = (
    "import os,sys,time\nfrom pathlib import Path\n"
    "parent=int(sys.argv[1])\n"
    "while os.getppid() == parent:\n    time.sleep(0.001)\n"
    "Path(sys.argv[2]).write_text('exited')\ntime.sleep(30)\n"
)
child = subprocess.Popen(
    [sys.executable, "-c", descendant, str(os.getpid()), sys.argv[1] + ".exited"],
    start_new_session=sys.argv[2] == "escape",
)
Path(sys.argv[1]).write_text(str(child.pid))
print("ready", flush=True)
"""
def fixed_program(command, **options):
    program = (
        [sys.executable, "-c", client_code, str(root / "child.pid"), sys.argv[3]]
        if command[-1] == "owned-test-client"
        else [sys.executable, "-c", "pass"]
    )
    # Exercise the production spawn options, including its buffering/session policy.
    return subprocess.Popen(program, **options)

owner = ContainerProcessOwner(
    OperatorContainerRuntime(Path(sys.executable), root / "unused.sock", root / "config"),
    ContainerRouterLimits(1000, 131072, 256, 67108864, 1, 16, 1048576),
    process_factory=fixed_program,
)
client = owner._spawn(("owned-test-client",), subprocess.PIPE, {})
(root / "client.pid").write_text(str(client.pid))
assert client.stdout.readline() == b"ready\n"
# The descendant observes reparenting. Do not reap the leader outside its I/O owner.
while not (root / "child.pid.exited").exists():
    time.sleep(0.001)
signals = []
real_killpg = os.killpg
def observe_signal(group, number):
    real_killpg(group, number)
    signals.append((group, number))
os.killpg = observe_signal
started = time.monotonic()
if operation == "probe":
    _, accepted = owner._probe(client, started + 0.1)
    assert not accepted
else:
    # The inherited stdin also holds a blocked writer, not just the output readers.
    _, status = owner._exchange(client, b"x" * 131072, started + 0.1, None)
    assert status is not None
    removed = owner._cleanup("owned-test-only", client, {})
    # Removal confirms the owned client/container, not a descendant that escaped its group.
    assert removed
elapsed = time.monotonic() - started
closed = all(stream.closed for stream in (client.stdin, client.stdout, client.stderr))
assert closed
assert threading.active_count() == 1
if not escape:
    assert (client.pid, 9) in signals
assert elapsed < 5
print(json.dumps({"bounded": True, "closed": closed}))
'''


def _clean_owned_descendant(root: Path) -> None:
    """Only kill the known test child while its group still matches a recorded owner."""
    owned = {
        int(path.read_text())
        for path in (root / "child.pid", root / "client.pid")
        if path.exists() and path.read_text().isdecimal()
    }
    for process in owned:
        try:
            group = os.getpgid(process)
            if group > 1 and group in owned:
                os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.skipif(os.name != "posix", reason="requires owned POSIX process groups")
@pytest.mark.parametrize("operation", ("probe", "cleanup"))
@pytest.mark.parametrize("escape", ("same-group", "escape"))
def test_exited_leader_cannot_make_pipe_cleanup_unbounded(tmp_path, operation, escape):
    with (tmp_path / "report.json").open("wb") as out, (tmp_path / "error.log").open("wb") as err:
        outer = subprocess.Popen(  # noqa: S603 - fixed interpreter and in-test literal harness
            [sys.executable, "-c", _HARNESS, str(tmp_path), operation, escape],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        try:
            try:
                outer.wait(timeout=8)
            except subprocess.TimeoutExpired:
                pytest.fail("owned descendant kept cleanup blocked past the outer guard")
            assert outer.returncode == 0, (tmp_path / "error.log").read_text()
            assert json.loads((tmp_path / "report.json").read_bytes()) == {
                "bounded": True,
                "closed": True,
            }
        finally:
            _clean_owned_descendant(tmp_path)
            if outer.poll() is None:
                os.killpg(outer.pid, signal.SIGKILL)
            outer.wait(timeout=2)
