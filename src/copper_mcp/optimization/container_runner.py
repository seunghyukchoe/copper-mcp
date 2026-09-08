"""Fixed external-router requests over shared bounded local-Docker ownership."""

from __future__ import annotations

import math
import os
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from copper_mcp.optimization.confined_container import (
    _LOCAL_IMAGE as _LOCAL_IMAGE,
)
from copper_mcp.optimization.confined_container import (
    ContainerProcessOwner as ContainerProcessOwner,
)
from copper_mcp.optimization.confined_container import (
    ContainerRouterLimits as ContainerRouterLimits,
)
from copper_mcp.optimization.confined_container import (
    ContainerRunnerError as ContainerRunnerError,
)
from copper_mcp.optimization.confined_container import (
    ContainerRunStatus as ContainerRunStatus,
)
from copper_mcp.optimization.confined_container import (
    OperatorContainerRuntime as OperatorContainerRuntime,
)
from copper_mcp.optimization.confined_container import (
    ProcessFactory as ProcessFactory,
)
from copper_mcp.optimization.confined_container import (
    _command_digest as _command_digest,
)
from copper_mcp.optimization.confined_container import (
    _docker_environment as _docker_environment,
)
from copper_mcp.optimization.confined_container import (
    _image_digest as _image_digest,
)
from copper_mcp.optimization.confined_container import (
    _image_reference as _image_reference,
)
from copper_mcp.optimization.confined_container import (
    _sha256 as _sha256,
)
from copper_mcp.optimization.confined_container import (
    _stop_status as _stop_status,
)


class EngineKind(StrEnum):
    FREEROUTING = "freerouting"
    SIMPLE_ROUTE_JSON = "simple_route_json"


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


class ContainerRouterRunner(ContainerProcessOwner):
    """Run fixed router entrypoints using shared bounded process ownership."""

    def __init__(
        self,
        runtime: OperatorContainerRuntime,
        images: OperatorRouterImages,
        limits: ContainerRouterLimits,
        *,
        process_factory: ProcessFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(runtime, limits, process_factory=process_factory, clock=clock)
        self._images = images

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
