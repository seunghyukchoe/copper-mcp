"""Private fixed-image ngspice execution; numerical bytes are never engineering authority."""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from copper_mcp.optimization.confined_container import (
    ContainerProcessOwner,
    ContainerRouterLimits,
    OperatorContainerRuntime,
    ProcessFactory,
    _command_digest,
    _docker_environment,
    _sha256,
    _stop_status,
)

IMAGE = "sha256:79f7f67951d42a16f4ada950f42c7b90c425b1e0dc9ad976fbe1a029c3d782a5"
BACKEND_COMMAND = "ngspice-45.2, Build Sun Sep  7 00:00:00 UTC 2025"
_INPUT_BYTES = 16 * 1024 * 1024
_RAW_BYTES = 256 * 1024
_LOG_BYTES = 64 * 1024

# Fixed code executed by the pinned image's Python3.13.9, never assembled from requests.
# resource.setrlimit sets a hard file ceiling before ngspice starts:
# https://docs.python.org/3.13/library/resource.html#resource.RLIMIT_FSIZE
_DRIVER = r"""
import base64, json, os, resource, subprocess, sys
from pathlib import Path
try:
    if sys.version_info[:3] != (3, 13, 9):
        raise ValueError()
    payload = sys.stdin.buffer.read(16777217)
    if not payload or len(payload) > 16777216:
        raise ValueError()
    root = Path("/work")
    (root / "input.cir").write_bytes(payload)
    (root / "spinit").write_bytes(b"")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # ngspice copies the input into a native temporary file. Bound that file too;
    # accepted raw/log outputs have the separate smaller capture ceilings below.
    resource.setrlimit(resource.RLIMIT_FSIZE, (16777216, 16777216))
    env = {"PATH":"/usr/bin:/bin", "HOME":"/work", "LANG":"C", "LC_ALL":"C",
           "SPICE_SCRIPTS":"/work", "SPICE_ASCIIRAWFILE":"1"}
    with (root / "stdout").open("wb") as out, (root / "stderr").open("wb") as err:
        completed = subprocess.run(
            ["/usr/bin/ngspice", "-n", "-b", "-r", "/work/result.raw", "/work/input.cir"],
            stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=env, cwd=root,
            timeout=60, check=False)
    def capture(name, limit):
        path = root / name
        if not path.is_file() or path.stat().st_size > limit:
            raise ValueError()
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError()
        return base64.b64encode(data).decode("ascii")
    result = {"schema_version":"ngspice-op-runtime/v1", "exit_code":completed.returncode,
              "stdout":capture("stdout", 65536), "stderr":capture("stderr", 65536),
              "raw":capture("result.raw", 262144)}
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")))
except (OSError, ValueError, subprocess.SubprocessError):
    sys.exit(2)
"""


class NgspiceExecutionError(ValueError):
    """A context-free refusal without circuit, path, stdout or stderr contents."""


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise NgspiceExecutionError("ngspice execution deadline expired")


@dataclass(frozen=True, slots=True, repr=False)
class NgspiceExecution:
    image_digest: str
    command_digest: str
    input_digest: str
    raw: bytes
    diagnostics: bytes

    def __repr__(self) -> str:
        return "<NgspiceExecution redacted>"


def _decode_frame(payload: bytes, deadline: float) -> tuple[bytes, bytes]:
    _check(deadline)
    if type(payload) is not bytes or not 1 <= len(payload) <= 1024 * 1024:
        raise ValueError

    def object_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        _check(deadline)
        if len(pairs) > 5 or len({key for key, _value in pairs}) != len(pairs):
            raise ValueError
        return dict(pairs)

    def integer(value: str) -> int:
        if len(value) > 8:
            raise ValueError
        return int(value)

    document = json.loads(payload, object_pairs_hook=object_fields, parse_int=integer)
    if type(document) is not dict or set(document) != {
        "schema_version",
        "exit_code",
        "stdout",
        "stderr",
        "raw",
    }:
        raise ValueError
    if (
        document["schema_version"] != "ngspice-op-runtime/v1"
        or type(document["exit_code"]) is not int
    ):
        raise ValueError
    if document["exit_code"] != 0:
        raise ValueError
    values = []
    for key, limit in (("stdout", _LOG_BYTES), ("stderr", _LOG_BYTES), ("raw", _RAW_BYTES)):
        _check(deadline)
        value = document[key]
        if type(value) is not str or len(value) > 4 * ((limit + 2) // 3):
            raise ValueError
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) > limit or base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError
        values.append(decoded)
    stdout, stderr, raw = values
    if stderr or not raw:
        raise ValueError
    # Log interpretation and topology/numerical checks remain coordinator-owned. A clean
    # process exit and finite raw values alone cannot establish convergence or validity.
    _check(deadline)
    return raw, stdout


def _command(name: str) -> tuple[str, ...]:
    return (
        "run",
        "--name",
        name,
        "--pull=never",
        "--platform=linux/amd64",
        "--network=none",
        "--read-only",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--log-driver=none",
        "--memory=536870912",
        "--memory-swap=536870912",
        "--cpus=1",
        "--pids-limit=64",
        "--tmpfs=/work:rw,noexec,nosuid,nodev,mode=0700,uid=65532,gid=65532,size=67108864",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,mode=0700,uid=65532,gid=65532,size=16777216",
        "--workdir=/work",
        "--entrypoint=/usr/bin/python3",
        "--interactive",
        IMAGE,
        "-I",
        "-c",
        _DRIVER,
    )


def run_ngspice(
    payload: bytes,
    runtime: OperatorContainerRuntime,
    *,
    deadline: float,
    cancelled: Callable[[], bool] | None = None,
    process_factory: ProcessFactory | None = None,
) -> NgspiceExecution:
    """Execute only the fixed pinned program. Callers must first admit the generated deck."""
    result = None
    try:
        if type(deadline) not in (int, float):
            raise ValueError
        active = min(float(deadline), time.monotonic() + 60)
        if not math.isfinite(float(deadline)) or type(payload) is not bytes:
            raise ValueError
        if not 1 <= len(payload) <= _INPUT_BYTES:
            raise ValueError
        if cancelled is not None and not callable(cancelled):
            raise ValueError
        _check(active)
        if _stop_status(cancelled) is not None:
            raise ValueError
        limits = ContainerRouterLimits(
            60_000, _INPUT_BYTES, 1024 * 1024, 512 * 1024 * 1024, 1, 64, 64 * 1024 * 1024
        )
        owner = ContainerProcessOwner(runtime, limits, process_factory=process_factory)
        runtime.config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = runtime.config_root.stat()
        if (
            info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or any(runtime.config_root.iterdir())
        ):
            raise ValueError
        with tempfile.TemporaryDirectory(prefix="copper-ngspice-", dir=runtime.config_root) as root:
            env = _docker_environment(Path(root))
            if not owner._preflight(
                ("info",), env, active, cancelled
            ) or not owner._image_available(IMAGE, env, active, cancelled):
                raise ValueError
            _check(active)
            if _stop_status(cancelled) is not None:
                raise ValueError
            command = _command("copper-mcp-ngspice-" + uuid.uuid4().hex)
            process = owner._spawn(command, subprocess.PIPE, env)
            try:
                output, status = owner._exchange(process, payload, active, cancelled)
            finally:
                cleaned = owner._cleanup(command[2], process, env)
            if not cleaned or status is not None or process.returncode != 0:
                raise ValueError
            raw, diagnostics = _decode_frame(bytes(output), active)
            completed = NgspiceExecution(
                IMAGE, _command_digest(command), _sha256(payload), raw, diagnostics
            )
            _check(active)
        if _stop_status(cancelled) is not None:
            raise ValueError
        _check(active)
        result = completed
    except (ValueError, TypeError, OSError, OverflowError, binascii.Error, RuntimeError):
        pass
    if result is None:
        raise NgspiceExecutionError("ngspice could not produce bounded numerical output")
    return result
