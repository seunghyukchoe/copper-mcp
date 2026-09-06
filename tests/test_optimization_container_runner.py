"""Adversarial tests for the unregistered local-Docker router seam."""

from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest

from copper_mcp.optimization.container_runner import (
    ContainerRouterLimits,
    ContainerRouterRunner,
    ContainerRunRequest,
    ContainerRunStatus,
    EngineKind,
    OperatorContainerRuntime,
    OperatorRouterImages,
)

IMAGE = "local/copper-router@sha256:" + "a" * 64
LOCAL_IMAGE = "sha256:" + "b" * 64


class FakeProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.pid = 0
        self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(stdout), io.BytesIO(stderr)
        self.returncode: int | None = returncode

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class RunningFakeProcess(FakeProcess):
    def __init__(self) -> None:
        super().__init__()
        self.returncode = None


def limits() -> ContainerRouterLimits:
    return ContainerRouterLimits(1000, 128 * 1024, 256, 64 * 1024 * 1024, 1, 16, 1024 * 1024)


def runtime(tmp_path: Path) -> OperatorContainerRuntime:
    return OperatorContainerRuntime(
        Path(sys.executable), tmp_path / "missing.sock", tmp_path / "config"
    )


def runner(
    tmp_path: Path, processes: list[FakeProcess]
) -> tuple[ContainerRouterRunner, list[tuple[tuple[str, ...], dict[str, object]]]]:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def factory(command: tuple[str, ...], **kwargs: Any) -> FakeProcess:
        calls.append((command, kwargs))
        return processes.pop(0)

    return (
        ContainerRouterRunner(
            runtime(tmp_path),
            OperatorRouterImages(IMAGE, IMAGE),
            limits(),
            process_factory=cast(Any, factory),
        ),
        calls,
    )


def request(payload: bytes = b"board bytes") -> ContainerRunRequest:
    return ContainerRunRequest(EngineKind.FREEROUTING, payload)


def image_inspect() -> FakeProcess:
    return FakeProcess(stdout=(f'["{IMAGE}"]\n').encode())


def test_success_hardens_env_and_returns_untrusted_bytes(tmp_path: Path) -> None:
    value, calls = runner(
        tmp_path,
        [FakeProcess(), image_inspect(), FakeProcess(stdout=b"router output"), FakeProcess()],
    )
    result = value.run(request())
    command, kwargs = calls[2]
    environment = cast(dict[str, str], kwargs["env"])
    assert result.record.status is ContainerRunStatus.SUCCESS and result.output == b"router output"
    assert command[0] == sys.executable and command[1] == "--host"
    assert {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "DOCKER_CONFIG"} == set(environment)
    assert "--pull=never" in command and "--network=none" in command and "--read-only" in command
    assert "--user=65532:65532" in command and "--log-driver=none" in command
    assert "--memory-swap=67108864" in command and "--cap-drop=ALL" in command
    assert "--tmpfs=/work:rw,noexec,nosuid,mode=0700,uid=65532,gid=65532,size=1048576" in command
    assert not any(arg.startswith(("--mount", "--volume", "--env")) for arg in command)


def test_unavailable_preflight_happens_before_input_disclosure(tmp_path: Path) -> None:
    value, calls = runner(tmp_path, [FakeProcess(returncode=1)])
    result = value.run(request(b"private board"))
    assert result.record.status is ContainerRunStatus.DAEMON_UNAVAILABLE
    assert result.record.input_digest is None and len(calls) == 1


def test_tmpfs_cannot_exceed_memory() -> None:
    with pytest.raises(ValueError, match="tmpfs"):
        ContainerRouterLimits(100, 1, 1, 1024, 1, 1, 1025)


def test_random_names_have_stable_command_digest(tmp_path: Path) -> None:
    first, _ = runner(
        tmp_path, [FakeProcess(), image_inspect(), FakeProcess(stdout=b"x"), FakeProcess()]
    )
    second, _ = runner(
        tmp_path, [FakeProcess(), image_inspect(), FakeProcess(stdout=b"x"), FakeProcess()]
    )
    assert first.run(request()).record.command_digest == second.run(request()).record.command_digest


def test_cleanup_failure_is_typed(tmp_path: Path) -> None:
    value, _ = runner(
        tmp_path,
        [
            FakeProcess(),
            image_inspect(),
            FakeProcess(stdout=b"ok"),
            FakeProcess(returncode=1),
            FakeProcess(returncode=1),
            FakeProcess(returncode=1),
        ],
    )
    result = value.run(request())
    assert result.record.status is ContainerRunStatus.CLEANUP_FAILED and result.output is None


def test_empty_successful_stdout_is_not_a_router_success(tmp_path: Path) -> None:
    value, _ = runner(tmp_path, [FakeProcess(), image_inspect(), FakeProcess(), FakeProcess()])
    result = value.run(request())
    assert result.record.status is ContainerRunStatus.EMPTY_OUTPUT and result.output is None


def test_non_boolean_cancellation_fails_closed(tmp_path: Path) -> None:
    value, calls = runner(tmp_path, [])
    assert (
        value.run(request(), cancelled=cast(Any, lambda: "yes")).record.status
        is ContainerRunStatus.CANCELLED
    )
    assert not calls


def test_large_unread_stdin_deadlines_without_hang(tmp_path: Path) -> None:
    script = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'])"
    )
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and in-test literal script
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    value, _ = runner(tmp_path, [])
    started = time.monotonic()
    _output, status = value._exchange(cast(Any, process), b"x" * (128 * 1024), started + 0.15, None)
    assert status is ContainerRunStatus.DEADLINE_EXCEEDED and time.monotonic() - started < 1
    os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=1)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_stream_ceiling_is_a_typed_refusal(tmp_path: Path, stream: str) -> None:
    value, _ = runner(tmp_path, [])
    process = (
        FakeProcess(stdout=b"x" * 257) if stream == "stdout" else FakeProcess(stderr=b"x" * 65_537)
    )
    _output, status = value._exchange(cast(Any, process), None, time.monotonic() + 1, None)
    assert status is ContainerRunStatus.OUTPUT_LIMIT_EXCEEDED


def test_nonzero_exit_is_not_success(tmp_path: Path) -> None:
    value, _ = runner(tmp_path, [])
    process = FakeProcess(returncode=17, stdout=b"bytes")
    _output, status = value._exchange(cast(Any, process), None, time.monotonic() + 1, None)
    assert status is None and process.returncode == 17


def test_held_descendant_pipes_prevent_success_until_deadline(tmp_path: Path) -> None:
    script = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'])"
    )
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and in-test literal script
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    value, _ = runner(tmp_path, [])
    _output, status = value._exchange(cast(Any, process), None, time.monotonic() + 0.15, None)
    assert status is ContainerRunStatus.DEADLINE_EXCEEDED
    os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=1)


def test_local_image_id_requires_matching_inspected_identity(tmp_path: Path) -> None:
    runtime_value = runtime(tmp_path)
    runner_value = ContainerRouterRunner(
        runtime_value,
        OperatorRouterImages(LOCAL_IMAGE, LOCAL_IMAGE),
        limits(),
        process_factory=cast(Any, lambda *_args, **_kwargs: FakeProcess()),
    )
    assert runner_value._image_available(LOCAL_IMAGE, {}, time.monotonic() + 1) is False


def test_local_image_id_accepts_matching_inspected_identity(tmp_path: Path) -> None:
    runtime_value = runtime(tmp_path)
    runner_value = ContainerRouterRunner(
        runtime_value,
        OperatorRouterImages(LOCAL_IMAGE, LOCAL_IMAGE),
        limits(),
        process_factory=cast(
            Any, lambda *_args, **_kwargs: FakeProcess(stdout=LOCAL_IMAGE.encode())
        ),
    )
    assert runner_value._image_available(LOCAL_IMAGE, {}, time.monotonic() + 1) is True


@pytest.mark.parametrize("image", ["latest", "repo/image:tag", "repo@sha256:" + "A" * 64])
def test_images_must_be_lowercase_digest_pinned(image: str) -> None:
    with pytest.raises(ValueError, match="digest pinned"):
        OperatorRouterImages(image, IMAGE)


@pytest.mark.external_router
@pytest.mark.parametrize("engine", list(EngineKind))
def test_live_smoke_is_explicitly_not_a_mocked_unit_test(
    tmp_path: Path, engine: EngineKind
) -> None:
    from scripts.smoke_optimization_routers import ROOT, format_observation

    names = (
        "COPPER_MCP_TEST_DOCKER",
        "COPPER_MCP_TEST_DOCKER_SOCKET",
        "COPPER_MCP_TEST_FREEROUTING_IMAGE",
        "COPPER_MCP_TEST_SRJ_IMAGE",
    )
    values = [os.environ.get(name) for name in names]
    if not all(values):
        pytest.skip("requires explicit operator test runtime and two immutable local images")
    docker, socket, freerouting, srj = (str(value) for value in values)
    actual = ContainerRouterRunner(
        OperatorContainerRuntime(Path(docker), Path(socket), tmp_path / "config"),
        OperatorRouterImages(freerouting, srj),
        ContainerRouterLimits(180_000, 1_048_576, 1_048_576, 1024**3, 1, 128, 64 * 1024**2),
    )
    fixture = (
        ROOT / "benchmarks/routing/fixtures/freerouting-common-two-pad-v1.dsn"
        if engine is EngineKind.FREEROUTING
        else ROOT / "hardware/optimization-router/simpleroutejson/smoke-input.json"
    )
    result = actual.run(ContainerRunRequest(engine, fixture.read_bytes()))
    assert result.record.status is ContainerRunStatus.SUCCESS
    assert result.output is not None
    assert format_observation(engine, result.output)
