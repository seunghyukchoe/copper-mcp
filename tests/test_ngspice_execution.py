import base64
import json
import time

import pytest
from test_optimization_container_runner import FakeProcess, runtime
from test_optimization_container_runner import close_fake_processes as close_fake_processes

from copper_mcp.engineering import _ngspice_execution as execution


def _frame(raw=b"untrusted raw output", stderr=b"", exit_code=0):
    return json.dumps(
        {
            "schema_version": "ngspice-op-runtime/v1",
            "exit_code": exit_code,
            "stdout": base64.b64encode(b"native log").decode(),
            "stderr": base64.b64encode(stderr).decode(),
            "raw": base64.b64encode(raw).decode(),
        },
        separators=(",", ":"),
    ).encode()


def _run(tmp_path, frame, **kwargs):
    calls = []
    processes = [
        FakeProcess(),
        FakeProcess(stdout=(execution.IMAGE + "\n").encode()),
        FakeProcess(stdout=frame),
        FakeProcess(),
    ]

    def factory(command, **options):
        calls.append((command, options))
        return processes.pop(0)

    result = execution.run_ngspice(
        b"PRIVATE_SENTINEL_DECK",
        runtime(tmp_path),
        deadline=time.monotonic() + 5,
        process_factory=factory,
        **kwargs,
    )
    assert not processes
    return result, calls


def test_fixed_program_and_confinement_never_receive_deck_in_arguments(tmp_path):
    result, calls = _run(tmp_path, _frame())
    assert result.raw == b"untrusted raw output"
    command = calls[2][0]
    assert "--network=none" in command and "--read-only" in command
    assert "--entrypoint=/usr/bin/python3" in command and "--pull=never" in command
    assert any(value.startswith("--tmpfs=/tmp:rw,noexec,nosuid,nodev,") for value in command)
    assert any(value.startswith("--tmpfs=/work:rw,noexec,nosuid,nodev,") for value in command)
    assert command[-2:] == ("-c", execution._DRIVER)
    assert "PRIVATE_SENTINEL" not in repr(command)
    assert calls[-1][0][-3:-1] == ("rm", "--force")
    assert repr(result) == "<NgspiceExecution redacted>"
    assert calls[2][1]["env"]["LC_ALL"] == "C"


def test_frame_byte_ceiling_is_separate_from_small_mcp_message_limit(tmp_path):
    payload = b"x" * 150_000
    result, _calls = _run(tmp_path, _frame(payload))
    assert result.raw == payload


@pytest.mark.parametrize(
    "frame",
    (
        _frame(stderr=b"PRIVATE native error"),
        _frame(exit_code=1),
        _frame(exit_code=False),
        _frame(raw=b""),
        b"{}",
        b"[]",
        _frame().replace(b'"exit_code":0', b'"exit_code":0,"exit_code":0'),
        _frame().replace(b'"raw":"', b'"raw":"!'),
        _frame(raw=b"x" * (256 * 1024 + 1)),
    ),
)
def test_malformed_or_failed_native_frames_refuse_without_private_context(tmp_path, frame):
    with pytest.raises(execution.NgspiceExecutionError) as error:
        _run(tmp_path, frame)
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("deadline", (True, 0, float("nan"), float("inf"), 10**1000))
def test_bad_deadline_refuses_before_process_creation(tmp_path, deadline):
    with pytest.raises(execution.NgspiceExecutionError):
        execution.run_ngspice(
            b"deck",
            runtime(tmp_path),
            deadline=deadline,
            process_factory=lambda *a, **k: pytest.fail("must not spawn"),
        )


def test_cancelled_or_faulting_callback_refuses_before_probes(tmp_path):
    def broken():
        raise RuntimeError("PRIVATE callback")

    for cancelled in (lambda: True, broken):
        with pytest.raises(execution.NgspiceExecutionError) as error:
            execution.run_ngspice(
                b"deck",
                runtime(tmp_path),
                deadline=time.monotonic() + 5,
                cancelled=cancelled,
                process_factory=lambda *a, **k: pytest.fail("must not spawn"),
            )
        assert error.value.__context__ is None


def test_temporary_cleanup_failure_prevents_success(tmp_path, monkeypatch):
    original = execution.tempfile.TemporaryDirectory

    class FailingCleanup:
        def __init__(self, *args, **kwargs):
            self.context = original(*args, **kwargs)

        def __enter__(self):
            return self.context.__enter__()

        def __exit__(self, *args):
            self.context.__exit__(*args)
            raise OSError("owned cleanup failure")

    monkeypatch.setattr(execution.tempfile, "TemporaryDirectory", FailingCleanup)
    with pytest.raises(execution.NgspiceExecutionError) as error:
        _run(tmp_path, _frame())
    assert error.value.__context__ is None


@pytest.mark.parametrize("late_stop", ("cancel", "callback_failure", "deadline"))
def test_final_frame_stop_prevents_delivery(tmp_path, monkeypatch, late_stop):
    original = execution._decode_frame
    decoded = False

    def decode(*args):
        nonlocal decoded
        result = original(*args)
        decoded = True
        return result

    def cancelled():
        if not decoded:
            return False
        if late_stop == "callback_failure":
            raise RuntimeError("PRIVATE late callback")
        if late_stop == "deadline":
            monkeypatch.setattr(execution.time, "monotonic", lambda: float("inf"))
            return False
        return True

    monkeypatch.setattr(execution, "_decode_frame", decode)
    with pytest.raises(execution.NgspiceExecutionError) as error:
        _run(tmp_path, _frame(), cancelled=cancelled)
    assert decoded
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert "PRIVATE" not in str(error.value)


def test_cancellation_during_successful_workspace_cleanup_prevents_delivery(tmp_path, monkeypatch):
    original = execution.tempfile.TemporaryDirectory
    cleaned = False

    class CancellingCleanup(original):
        def __exit__(self, *args):
            nonlocal cleaned
            result = super().__exit__(*args)
            cleaned = True
            return result

    monkeypatch.setattr(execution.tempfile, "TemporaryDirectory", CancellingCleanup)
    with pytest.raises(execution.NgspiceExecutionError) as error:
        _run(tmp_path, _frame(), cancelled=lambda: cleaned)
    assert cleaned
    assert error.value.__context__ is None and error.value.__cause__ is None
