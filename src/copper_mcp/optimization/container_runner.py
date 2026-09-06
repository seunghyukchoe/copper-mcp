"""Bounded local-Docker execution seam for fixed external router images.

This module has no candidate, board, MCP, or parser dependency. A success returns only
untrusted bounded bytes for a coordinator-owned disposer.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Protocol, cast


class EngineKind(StrEnum):
    FREEROUTING = "freerouting"
    SIMPLE_ROUTE_JSON = "simple_route_json"


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


@dataclass(frozen=True, slots=True)
class OperatorRouterImages:
    freerouting: str
    simple_route_json: str

    def __post_init__(self) -> None:
        _image_reference(self.freerouting)
        _image_reference(self.simple_route_json)

    def for_engine(self, engine: EngineKind) -> str:
        if type(engine) is not EngineKind:
            raise ContainerRunnerError("engine kind is invalid")
        return self.freerouting if engine is EngineKind.FREEROUTING else self.simple_route_json

    def identity_kind(self, engine: EngineKind) -> str:
        return (
            "local_image_id" if _LOCAL_IMAGE.fullmatch(self.for_engine(engine)) else "repo_digest"
        )


@dataclass(frozen=True, slots=True)
class ContainerRunRequest:
    engine: EngineKind
    input_bytes: bytes

    def __post_init__(self) -> None:
        if type(self.engine) is not EngineKind or type(self.input_bytes) is not bytes:
            raise ContainerRunnerError("container request is invalid")


@dataclass(frozen=True, slots=True)
class ContainerRunRecord:
    """Retention-safe metadata only; image references, argv, payloads, and paths are absent."""

    status: ContainerRunStatus
    engine: EngineKind
    image_digest: str
    image_identity_kind: str
    command_digest: str | None
    input_digest: str | None
    output_digest: str | None
    input_bytes: int
    output_bytes: int
    exit_code: int | None


@dataclass(frozen=True, slots=True)
class ContainerRunResult:
    record: ContainerRunRecord
    output: bytes | None


class ContainerRouterRunner:
    """Run fixed entrypoints with local Docker, bounded streams, and confirmed cleanup."""

    def __init__(
        self,
        runtime: OperatorContainerRuntime,
        images: OperatorRouterImages,
        limits: ContainerRouterLimits,
        *,
        process_factory: ProcessFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(runtime) is not OperatorContainerRuntime:
            raise ContainerRunnerError("container runtime is invalid")
        self._runtime = runtime
        self._images = images
        self._limits = limits
        self._process_factory = process_factory or subprocess.Popen
        self._clock = clock

    def run(
        self,
        request: ContainerRunRequest,
        *,
        cancelled: Callable[[], bool] | None = None,
        deadline: float | None = None,
    ) -> ContainerRunResult:
        if type(request) is not ContainerRunRequest:
            raise ContainerRunnerError("container request is invalid")
        if len(request.input_bytes) > self._limits.max_input_bytes:
            raise ContainerRunnerError("container input exceeds its byte limit")
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(float(deadline))
        ):
            raise ContainerRunnerError("deadline is invalid")
        if cancelled is not None and not callable(cancelled):
            raise ContainerRunnerError("cancellation hook is invalid")
        image = self._images.for_engine(request.engine)
        stopped = _stop_status(cancelled)
        if stopped is not None:
            return self._result(stopped, request.engine, image)
        work_deadline = min(
            float(deadline) if deadline is not None else float("inf"),
            self._clock() + self._limits.max_runtime_ms / 1000,
        )
        if self._clock() >= work_deadline:
            return self._result(ContainerRunStatus.DEADLINE_EXCEEDED, request.engine, image)
        self._runtime.config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        config_stat = self._runtime.config_root.stat()
        if config_stat.st_uid != os.getuid() or stat.S_IMODE(config_stat.st_mode) & 0o077:
            raise ContainerRunnerError("docker config root is not private")
        if any(self._runtime.config_root.iterdir()):
            raise ContainerRunnerError("docker config root must be empty")
        with tempfile.TemporaryDirectory(
            prefix="copper-mcp-docker-", dir=self._runtime.config_root
        ) as root:
            environment = _docker_environment(Path(root))
            if not self._preflight(("info",), environment, work_deadline):
                return self._result(ContainerRunStatus.DAEMON_UNAVAILABLE, request.engine, image)
            if not self._image_available(image, environment, work_deadline):
                return self._result(ContainerRunStatus.IMAGE_UNAVAILABLE, request.engine, image)
            stopped = _stop_status(cancelled)
            if stopped is not None:
                return self._result(stopped, request.engine, image)
            if self._clock() >= work_deadline:
                return self._result(ContainerRunStatus.DEADLINE_EXCEEDED, request.engine, image)
            name = "copper-mcp-router-" + uuid.uuid4().hex
            command = self._docker_command(name, image)
            input_digest = _sha256(request.input_bytes)
            try:
                process = self._spawn(command, subprocess.PIPE, environment)
            except OSError:
                return self._result(
                    ContainerRunStatus.LAUNCH_FAILED, request.engine, image, command, input_digest
                )
            output, status = self._exchange(process, request.input_bytes, work_deadline, cancelled)
            cleanup_ok = self._cleanup(name, process, environment)
            if not cleanup_ok:
                status = ContainerRunStatus.CLEANUP_FAILED
            elif status is None:
                status = (
                    ContainerRunStatus.EMPTY_OUTPUT
                    if process.returncode == 0 and not output
                    else ContainerRunStatus.SUCCESS
                    if process.returncode == 0
                    else ContainerRunStatus.EXITED_NONZERO
                )
            returned = bytes(output) if status is ContainerRunStatus.SUCCESS else None
            return self._result(
                status,
                request.engine,
                image,
                command,
                input_digest,
                returned,
                process.returncode,
                len(request.input_bytes),
            )

    def _preflight(
        self, arguments: tuple[str, ...], environment: dict[str, str], deadline: float
    ) -> bool:
        try:
            process = self._spawn(arguments, subprocess.DEVNULL, environment)
        except OSError:
            return False
        _output, status = self._exchange(process, None, deadline, None)
        return status is None and process.returncode == 0 and _reap(process, deadline)

    def _image_available(self, image: str, environment: dict[str, str], deadline: float) -> bool:
        try:
            template = "{{.Id}}" if _LOCAL_IMAGE.fullmatch(image) else "{{json .RepoDigests}}"
            process = self._spawn(
                ("image", "inspect", f"--format={template}", image),
                subprocess.DEVNULL,
                environment,
            )
        except OSError:
            return False
        output, status = self._exchange(process, None, deadline, None)
        if status is not None or process.returncode != 0 or not _reap(process, deadline):
            return False
        observed = bytes(output).strip().decode("ascii", errors="ignore")
        if _LOCAL_IMAGE.fullmatch(image):
            return observed == image
        try:
            repo_digests = json.loads(observed)
        except json.JSONDecodeError:
            return False
        return type(repo_digests) is list and image in repo_digests

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
                start_new_session=os.name == "posix",
            ),
        )

    def _docker_command(self, name: str, image: str) -> tuple[str, ...]:
        return (
            "run",
            "--name",
            name,
            "--pull=never",
            "--network=none",
            "--read-only",
            "--user=65532:65532",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--log-driver=none",
            f"--memory={self._limits.memory_bytes}",
            f"--memory-swap={self._limits.memory_bytes}",
            f"--cpus={self._limits.cpu_count}",
            f"--pids-limit={self._limits.pids_limit}",
            "--tmpfs=/work:rw,noexec,nosuid,mode=0700,uid=65532,gid=65532,"
            f"size={self._limits.work_tmpfs_bytes}",
            "--interactive",
            image,
        )

    def _cleanup(self, name: str, process: _Process, environment: dict[str, str]) -> bool:
        deadline = self._clock() + _CLEANUP_GRACE_SECONDS
        removed = False
        for _attempt in range(_CLEANUP_ATTEMPTS):
            if self._clock() >= deadline:
                break
            try:
                remover = self._spawn(("rm", "--force", name), subprocess.DEVNULL, environment)
            except OSError:
                continue
            _output, status = self._exchange(remover, None, deadline, None)
            removed = status is None and remover.returncode == 0 and _reap(remover, deadline)
            if removed:
                break
        _close_streams(process)
        _kill_process(process)
        return removed and _reap(process, deadline)

    def _exchange(
        self,
        process: _Process,
        input_bytes: bytes | None,
        deadline: float,
        cancelled: Callable[[], bool] | None,
    ) -> tuple[bytearray, ContainerRunStatus | None]:
        output = bytearray()
        stdout_done, stderr_done, stdin_done = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        failure, overflow = threading.Event(), threading.Event()

        def read(stream: BinaryIO | None, limit: int, retain: bool, done: threading.Event) -> None:
            try:
                total = 0
                if stream is None:
                    failure.set()
                    return
                while chunk := stream.read(8192):
                    total += len(chunk)
                    if total > limit:
                        overflow.set()
                        return
                    if retain:
                        output.extend(chunk)
            except OSError:
                failure.set()
            finally:
                done.set()

        def write(stream: BinaryIO | None, payload: bytes | None) -> None:
            try:
                if payload is None:
                    return
                if stream is None:
                    failure.set()
                    return
                stream.write(payload)
                stream.close()
            except (BrokenPipeError, OSError):
                failure.set()
            finally:
                stdin_done.set()

        threads = (
            threading.Thread(
                target=read,
                args=(process.stdout, self._limits.max_output_bytes, True, stdout_done),
                daemon=True,
            ),
            threading.Thread(
                target=read,
                args=(process.stderr, self._limits.max_stderr_bytes, False, stderr_done),
                daemon=True,
            ),
            threading.Thread(target=write, args=(process.stdin, input_bytes), daemon=True),
        )
        for thread in threads:
            thread.start()
        while True:
            stopped = _stop_status(cancelled)
            if stopped is not None:
                return output, stopped
            if self._clock() >= deadline:
                return output, ContainerRunStatus.DEADLINE_EXCEEDED
            if overflow.is_set():
                return output, ContainerRunStatus.OUTPUT_LIMIT_EXCEEDED
            if failure.is_set():
                return output, ContainerRunStatus.PROCESS_IO_FAILED
            if (
                process.poll() is not None
                and stdout_done.is_set()
                and stderr_done.is_set()
                and stdin_done.is_set()
            ):
                return output, None
            time.sleep(0.005)

    def _result(
        self,
        status: ContainerRunStatus,
        engine: EngineKind,
        image: str,
        command: tuple[str, ...] | None = None,
        input_digest: str | None = None,
        output: bytes | None = None,
        exit_code: int | None = None,
        input_bytes: int = 0,
    ) -> ContainerRunResult:
        record = ContainerRunRecord(
            status,
            engine,
            _image_digest(image),
            "local_image_id" if _LOCAL_IMAGE.fullmatch(image) else "repo_digest",
            _command_digest(command) if command else None,
            input_digest,
            _sha256(output) if output is not None else None,
            input_bytes if input_digest else 0,
            len(output) if output else 0,
            exit_code,
        )
        return ContainerRunResult(record, output)


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
    if process.poll() is not None:
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
