import io
import time

from test_optimization_container_runner import FakeProcess, RunningFakeProcess, limits, runtime

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


def test_unconfirmed_reap_never_enters_blocking_stream_closure(tmp_path, monkeypatch):
    process = RunningFakeProcess()
    closed = []
    owner = ContainerProcessOwner(
        runtime(tmp_path), limits(), process_factory=lambda *a, **k: process
    )
    monkeypatch.setattr(
        owner, "_exchange", lambda *a: (bytearray(), ContainerRunStatus.DEADLINE_EXCEEDED)
    )
    monkeypatch.setattr(confined_container, "_kill_process", lambda *a: None)
    monkeypatch.setattr(confined_container, "_reap", lambda *a: False)
    monkeypatch.setattr(confined_container, "_close_streams", lambda *a: closed.append(True))
    assert not owner._preflight(("info",), {}, time.monotonic() + 1)
    assert not owner._cleanup("owned", process, {})
    assert closed == []
