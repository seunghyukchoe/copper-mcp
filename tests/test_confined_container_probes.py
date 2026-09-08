import io
import signal
import time

import pytest
from test_optimization_container_runner import FakeProcess, RunningFakeProcess, limits, runtime
from test_optimization_container_runner import close_fake_processes as close_fake_processes

from copper_mcp.optimization import confined_container
from copper_mcp.optimization.confined_container import ContainerProcessOwner, ContainerRunStatus


def test_failed_preflight_terminates_and_closes_its_client(tmp_path, monkeypatch):
    process = RunningFakeProcess()
    owner = ContainerProcessOwner(
        runtime(tmp_path), limits(), process_factory=lambda *a, **k: process
    )
    monkeypatch.setattr(
        owner, "_exchange", lambda *a: (bytearray(), ContainerRunStatus.DEADLINE_EXCEEDED)
    )
    assert not owner._preflight(("info",), {}, time.monotonic() + 1)
    assert process.returncode == -9
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))


def test_failed_image_probe_terminates_its_client(tmp_path, monkeypatch):
    process = RunningFakeProcess()
    owner = ContainerProcessOwner(
        runtime(tmp_path), limits(), process_factory=lambda *a, **k: process
    )
    monkeypatch.setattr(
        owner, "_exchange", lambda *a: (bytearray(), ContainerRunStatus.DEADLINE_EXCEEDED)
    )
    assert not owner._image_available("sha256:" + "a" * 64, {}, time.monotonic() + 1)
    assert process.returncode == -9


def test_cleanup_terminates_client_before_closing_reader_streams(tmp_path):
    process = RunningFakeProcess()

    class Reader(io.BytesIO):
        def close(self):
            assert process.returncode is not None, "a reader can hold the pipe lock until exit"
            return super().close()

    process.stdout = Reader()
    owner = ContainerProcessOwner(
        runtime(tmp_path), limits(), process_factory=lambda *a, **k: FakeProcess(1)
    )
    assert not owner._cleanup("owned", process, {})
    assert process.returncode == -9 and process.stdout.closed


def test_unconfirmed_reap_still_closes_owned_unbuffered_pipes_and_refuses(tmp_path, monkeypatch):
    process = RunningFakeProcess()
    owner = ContainerProcessOwner(
        runtime(tmp_path), limits(), process_factory=lambda *a, **k: process
    )
    monkeypatch.setattr(
        owner, "_exchange", lambda *a: (bytearray(), ContainerRunStatus.DEADLINE_EXCEEDED)
    )
    monkeypatch.setattr(confined_container, "_kill_process", lambda *a: None)
    monkeypatch.setattr(confined_container, "_reap", lambda *a: False)
    assert not owner._preflight(("info",), {}, time.monotonic() + 1)
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
    assert not owner._cleanup("owned", process, {})
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))


def test_group_termination_does_not_reap_its_leader_first(monkeypatch):
    process = RunningFakeProcess()
    process.pid = 123456789
    calls = []
    monkeypatch.setattr(process, "poll", lambda: pytest.fail("must not reap before group signal"))
    monkeypatch.setattr(confined_container.os, "killpg", lambda *args: calls.append(args))
    confined_container._kill_process(process)
    assert calls == [(process.pid, signal.SIGKILL)]


def test_already_reaped_handle_cannot_signal_a_reused_group(tmp_path, monkeypatch):
    process = FakeProcess(stdout=b"untrusted output")
    process.pid = 123456789
    monkeypatch.setattr(
        confined_container.os, "killpg", lambda *args: pytest.fail("group identity is stale")
    )
    owner = ContainerProcessOwner(runtime(tmp_path), limits())
    output, accepted = owner._probe(process, time.monotonic() + 1)
    assert not accepted and output == b""
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))


def test_remover_exception_still_stops_and_closes_the_original_client(tmp_path, monkeypatch):
    process = RunningFakeProcess()
    owner = ContainerProcessOwner(
        runtime(tmp_path), limits(), process_factory=lambda *a, **k: FakeProcess()
    )

    def interrupted(*args):
        raise RuntimeError("controlled remover interruption")

    monkeypatch.setattr(owner, "_exchange", interrupted)
    with pytest.raises(RuntimeError, match="controlled remover interruption"):
        owner._cleanup("owned", process, {})
    assert process.returncode == -9
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
