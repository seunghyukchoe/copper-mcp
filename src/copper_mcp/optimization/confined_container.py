"""Shared local-Docker primitives, with no geometry, request dispatch or apply authority.

ContainerRouterLimits retains its compatibility name; its resource bounds are backend-neutral.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Protocol, cast


class ContainerRunStatus(StrEnum):
    SUCCESS = "success"
    DAEMON_UNAVAILABLE = "daemon_unavailable"
    IMAGE_UNAVAILABLE = "image_unavailable"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    EMPTY_OUTPUT = "empty_output"
    PROCESS_IO_FAILED = "process_io_failed"
    EXITED_NONZERO = "exited_nonzero"
    LAUNCH_FAILED = "launch_failed"
    CLEANUP_FAILED = "cleanup_failed"


class ContainerRunnerError(ValueError):
    """Configuration or request input was refused before Docker is invoked."""


_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,240}@sha256:[0-9a-f]{64}$")


_LOCAL_IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")


_DIGEST = re.compile(r"@sha256:([0-9a-f]{64})$")


_MAX_INPUT_BYTES = 16 * 1024 * 1024


_MAX_OUTPUT_BYTES = 16 * 1024 * 1024


_MAX_STDERR_BYTES = 64 * 1024


_MAX_RUNTIME_MS = 3_600_000


_MAX_MEMORY_BYTES = 4 * 1024 * 1024 * 1024


_CLEANUP_GRACE_SECONDS = 2.0


_CLEANUP_ATTEMPTS = 3


_SAFE_PATH = "/usr/bin:/bin"


class _Process(Protocol):
    pid: int
    stdin: BinaryIO | None
    stdout: BinaryIO | None
    stderr: BinaryIO | None
    returncode: int | None

    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def kill(self) -> None: ...


ProcessFactory = Callable[..., _Process]


@dataclass(frozen=True, slots=True)
class ContainerRouterLimits:
    max_runtime_ms: int
    max_input_bytes: int
    max_output_bytes: int
    memory_bytes: int
    cpu_count: int
    pids_limit: int
    work_tmpfs_bytes: int
    max_stderr_bytes: int = _MAX_STDERR_BYTES

    def __post_init__(self) -> None:
        _positive_int("max_runtime_ms", self.max_runtime_ms, _MAX_RUNTIME_MS)
        _positive_int("max_input_bytes", self.max_input_bytes, _MAX_INPUT_BYTES)
        _positive_int("max_output_bytes", self.max_output_bytes, _MAX_OUTPUT_BYTES)
        _positive_int("memory_bytes", self.memory_bytes, _MAX_MEMORY_BYTES)
        _positive_int("cpu_count", self.cpu_count, 64)
        _positive_int("pids_limit", self.pids_limit, 4096)
        _positive_int("work_tmpfs_bytes", self.work_tmpfs_bytes, _MAX_MEMORY_BYTES)
        _positive_int("max_stderr_bytes", self.max_stderr_bytes, _MAX_STDERR_BYTES)
        if self.work_tmpfs_bytes > self.memory_bytes:
            raise ContainerRunnerError("work tmpfs exceeds container memory")


@dataclass(frozen=True, slots=True)
class OperatorContainerRuntime:
    """Operator-only local settings; never derived from a router request."""

    docker_executable: Path
    docker_socket: Path
    config_root: Path

    def __post_init__(self) -> None:
        for name, value in (
            ("docker executable", self.docker_executable),
            ("docker socket", self.docker_socket),
            ("docker config root", self.config_root),
        ):
            if not isinstance(value, Path) or not value.is_absolute():
                raise ContainerRunnerError(f"{name} is invalid")
        if not self.docker_executable.is_file() or not os.access(self.docker_executable, os.X_OK):
            raise ContainerRunnerError("docker executable is invalid")
        if self.docker_socket.exists() and not self.docker_socket.is_socket():
            raise ContainerRunnerError("docker socket is invalid")
        if self.config_root.exists() and not self.config_root.is_dir():
            raise ContainerRunnerError("docker config root is invalid")

    @property
    def docker_host(self) -> str:
        return "unix://" + str(self.docker_socket)


def _docker_environment(config_dir: Path) -> dict[str, str]:
    value = str(config_dir)
    return {
        "PATH": _SAFE_PATH,
        "HOME": value,
        "TMPDIR": value,
        "LANG": "C",
        "LC_ALL": "C",
        "DOCKER_CONFIG": value,
    }


def _command_digest(command: tuple[str, ...]) -> str:
    normalized = tuple(
        "<container>" if index and command[index - 1] == "--name" else item
        for index, item in enumerate(command)
    )
    return _sha256("\0".join(normalized).encode())


def _positive_int(name: str, value: int, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ContainerRunnerError(f"{name} is invalid")


def _image_reference(value: str) -> None:
    if type(value) is not str or not (_IMAGE.fullmatch(value) or _LOCAL_IMAGE.fullmatch(value)):
        raise ContainerRunnerError("router image must be digest pinned")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _image_digest(image: str) -> str:
    if _LOCAL_IMAGE.fullmatch(image):
        return image
    match = _DIGEST.search(image)
    if match is None:
        raise ContainerRunnerError("router image must be digest pinned")
    return "sha256:" + match.group(1)


def _stop_status(cancelled: Callable[[], bool] | None) -> ContainerRunStatus | None:
    if cancelled is None:
        return None
    try:
        return None if cancelled() is False else ContainerRunStatus.CANCELLED
    except Exception:
        return ContainerRunStatus.CANCELLED


def _close_streams(process: _Process) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def _kill_process(process: _Process) -> None:
    # Do not reap before signalling: an exited leader may still own inherited pipes.
    # _exchange leaves that leader unreaped while its pipe work is incomplete.
    if process.returncode is not None:
        return
    try:
        if os.name == "posix" and process.pid > 1:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass


def _reap(process: _Process, deadline: float) -> bool:
    while True:
        if process.poll() is not None:
            try:
                process.wait(timeout=0)
                return True
            except (OSError, subprocess.TimeoutExpired):
                return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.005)


class ContainerProcessOwner:
    """Shared bounded I/O and cleanup; callers own their fixed execution profiles."""

    def __init__(
        self,
        runtime: OperatorContainerRuntime,
        limits: ContainerRouterLimits,
        *,
        process_factory: ProcessFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(runtime) is not OperatorContainerRuntime:
            raise ContainerRunnerError("container runtime is invalid")
        self._runtime = runtime
        self._limits = limits
        self._process_factory = process_factory or subprocess.Popen
        self._clock = clock

    def _preflight(
        self,
        arguments: tuple[str, ...],
        environment: dict[str, str],
        deadline: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> bool:
        try:
            process = self._spawn(arguments, subprocess.DEVNULL, environment)
        except OSError:
            return False
        _output, successful = self._probe(process, deadline, cancelled)
        return successful

    def _image_available(
        self,
        image: str,
        environment: dict[str, str],
        deadline: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> bool:
        try:
            template = "{{.Id}}" if _LOCAL_IMAGE.fullmatch(image) else "{{json .RepoDigests}}"
            process = self._spawn(
                ("image", "inspect", f"--format={template}", image),
                subprocess.DEVNULL,
                environment,
            )
        except OSError:
            return False
        output, successful = self._probe(process, deadline, cancelled)
        if not successful:
            return False
        try:
            observed = output.strip().decode("ascii")
        except UnicodeError:
            return False
        if _LOCAL_IMAGE.fullmatch(image):
            return observed == image
        try:
            repo_digests = json.loads(observed)
        except json.JSONDecodeError:
            return False
        return type(repo_digests) is list and image in repo_digests

    def _probe(
        self,
        process: _Process,
        deadline: float,
        cancelled: Callable[[], bool] | None = None,
        *,
        cleanup_deadline: float | None = None,
    ) -> tuple[bytes, bool]:
        """Always terminate/reap utility clients, including timed-out or cancelled probes."""
        try:
            output, status = self._exchange(process, None, deadline, cancelled)
            successful = status is None and process.returncode == 0
        finally:
            _kill_process(process)
            reaped = _reap(
                process,
                cleanup_deadline
                if cleanup_deadline is not None
                else self._clock() + _CLEANUP_GRACE_SECONDS,
            )
            _close_streams(process)
        return bytes(output), successful and reaped

    def _spawn(
        self, arguments: tuple[str, ...], stdin: int, environment: dict[str, str]
    ) -> _Process:
        command = (
            str(self._runtime.docker_executable),
            "--host",
            self._runtime.docker_host,
            *arguments,
        )
        return cast(
            _Process,
            self._process_factory(
                command,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                bufsize=0,
                start_new_session=os.name == "posix",
            ),
        )

    def _cleanup(self, name: str, process: _Process, environment: dict[str, str]) -> bool:
        deadline = self._clock() + _CLEANUP_GRACE_SECONDS
        removed = False
        try:
            for _attempt in range(_CLEANUP_ATTEMPTS):
                if self._clock() >= deadline:
                    break
                try:
                    remover = self._spawn(("rm", "--force", name), subprocess.DEVNULL, environment)
                except OSError:
                    continue
                _output, removed = self._probe(remover, deadline, cleanup_deadline=deadline)
                if removed:
                    break
        finally:
            _kill_process(process)
            reaped = _reap(process, deadline)
            _close_streams(process)
        return removed and reaped

    def _exchange(
        self,
        process: _Process,
        input_bytes: bytes | None,
        deadline: float,
        cancelled: Callable[[], bool] | None,
    ) -> tuple[bytearray, ContainerRunStatus | None]:
        """Poll owned pipes without buffered locks or background I/O workers.

        Unix pipe readiness and nonblocking descriptors are supported by all project Python
        versions: https://docs.python.org/3.11/library/selectors.html . Popen's bufsize=0
        keeps subsequent local closure independent of a descendant retaining a pipe end.
        """
        output = bytearray()
        if os.name != "posix" or (process.pid > 1 and process.returncode is not None):
            return output, ContainerRunStatus.PROCESS_IO_FAILED
        totals = {"stdout": 0, "stderr": 0}
        offset = 0
        try:
            with selectors.DefaultSelector() as selector:
                for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                    if stream is None:
                        return output, ContainerRunStatus.PROCESS_IO_FAILED
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, name)
                if input_bytes is not None:
                    if process.stdin is None:
                        return output, ContainerRunStatus.PROCESS_IO_FAILED
                    os.set_blocking(process.stdin.fileno(), False)
                    if input_bytes:
                        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                    else:
                        process.stdin.close()
                while True:
                    stopped = _stop_status(cancelled)
                    if stopped is not None:
                        return output, stopped
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return output, ContainerRunStatus.DEADLINE_EXCEEDED
                    # Checking pipe completion first avoids reaping an exited group leader
                    # while descendants still hold a pipe. Its PID remains owned for abort.
                    if not selector.get_map() and process.poll() is not None:
                        return output, None
                    for key, _events in selector.select(min(0.005, remaining)):
                        try:
                            if key.data == "stdin":
                                assert input_bytes is not None and process.stdin is not None
                                written = os.write(key.fd, input_bytes[offset : offset + 8192])
                                if written <= 0:
                                    return output, ContainerRunStatus.PROCESS_IO_FAILED
                                offset += written
                                if offset == len(input_bytes):
                                    selector.unregister(key.fileobj)
                                    process.stdin.close()
                                continue
                            chunk = os.read(key.fd, 8192)
                        except (BlockingIOError, InterruptedError):
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        name = cast(str, key.data)
                        totals[name] += len(chunk)
                        limit = (
                            self._limits.max_output_bytes
                            if name == "stdout"
                            else self._limits.max_stderr_bytes
                        )
                        if totals[name] > limit:
                            return output, ContainerRunStatus.OUTPUT_LIMIT_EXCEEDED
                        if name == "stdout":
                            output.extend(chunk)
        except (OSError, ValueError, KeyError):
            return output, ContainerRunStatus.PROCESS_IO_FAILED
