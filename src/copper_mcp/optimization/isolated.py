"""Bounded parent supervisor for native code imported fresh from an inventoried source tree."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import select
import selectors
import signal
import struct
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from pydantic import Field

from copper_mcp.config import Settings
from copper_mcp.optimization._process_guard import CLEANUP_GRACE_SECONDS
from copper_mcp.optimization.contracts import ClosedModel, OptimizationError
from copper_mcp.optimization.inputs import PreparedOptimization
from copper_mcp.optimization.judge import AnyJudgeReport
from copper_mcp.optimization.lifecycle import TERMINAL, AnyOptimizationJobRecord
from copper_mcp.optimization.package import AnyOptimizationPackage
from copper_mcp.optimization.repository import OptimizationJobRepository


class _Response(ClosedModel):
    record: AnyOptimizationJobRecord
    judges: Annotated[tuple[AnyJudgeReport, ...], Field(max_length=32)]
    source: Annotated[str, Field(min_length=1, max_length=64 * 1024 * 1024)] | None


def _terminate(process: subprocess.Popen[bytes], *, require_group: bool = False) -> None:
    if process.returncode is not None:
        if require_group:
            raise OptimizationError("isolated guardian ownership was lost")
        return
    denied = False
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        denied = require_group
    except PermissionError:
        denied = True
    if denied:
        # Reap a finished leader only after attempting the owned group. Its exit never
        # proves that descendants are gone, so denied cleanup must still refuse delivery.
        try:
            process.poll()
        except OSError:
            pass
        raise OptimizationError("isolated native delivery stopped: group cleanup unverified")
    process.wait(timeout=CLEANUP_GRACE_SECONDS)


def _close_pipes(process: subprocess.Popen[bytes]) -> None:
    try:
        if process.stdin is not None:
            process.stdin.close()
    finally:
        if process.stdout is not None:
            process.stdout.close()


def _exchange(payload: bytes, deadline: float, cancelled: Callable[[], bool]) -> bytes:
    if os.name != "posix":
        raise OptimizationError("isolated native execution is unavailable")
    entry = Path(__file__).with_name("_process_guard.py")
    with ExitStack() as cleanup:
        status_read, status_write = os.pipe()
        status_reader = cleanup.enter_context(os.fdopen(status_read, "rb", buffering=0))
        status_writer = cleanup.enter_context(os.fdopen(status_write, "wb", buffering=0))
        process = subprocess.Popen(  # noqa: S603 - fixed local interpreter and guardian
            [sys.executable, "-I", str(entry), str(status_write), str(deadline)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(status_write,),
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
        cleanup.callback(_close_pipes, process)
        cleanup.callback(_terminate, process)
        status_writer.close()
        assert process.stdin is not None and process.stdout is not None
        output = bytearray()
        status = bytearray()
        exited = False
        offset = 0
        selector = cleanup.enter_context(selectors.DefaultSelector())
        for stream in (process.stdin, process.stdout, status_reader):
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE)
        selector.register(process.stdout, selectors.EVENT_READ)
        selector.register(status_reader, selectors.EVENT_READ)
        while selector.get_map():
            if time.monotonic() >= deadline or cancelled():
                raise OptimizationError("isolated native execution stopped")
            for key, _events in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                if key.fileobj is process.stdin:
                    try:
                        # Readiness guarantees only PIPE_BUF bytes, not a full request chunk.
                        # https://docs.python.org/3.12/library/select.html#select.PIPE_BUF
                        offset += os.write(key.fd, payload[offset : offset + select.PIPE_BUF])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        raise OptimizationError("isolated native input was not delivered") from None
                    if offset == len(payload):
                        selector.unregister(process.stdin)
                        process.stdin.close()
                elif key.fileobj is status_reader:
                    try:
                        chunk = os.read(key.fd, 5)
                    except BlockingIOError:
                        continue
                    status.extend(chunk)
                    if len(status) > 4 or (not chunk and len(status) != 4):
                        raise OptimizationError("isolated worker status is malformed")
                    if not chunk:
                        selector.unregister(status_reader)
                    if len(status) == 4 and not exited:
                        if struct.unpack("!i", status)[0] != 0:
                            raise OptimizationError("isolated native execution failed")
                        # The guardian still owns the PGID. Kill before reaping it, even when
                        # a descendant holds stdout; drain the closed group's buffered output next.
                        _terminate(process, require_group=True)
                        if process.returncode != -signal.SIGKILL:
                            raise OptimizationError("isolated guardian did not remain owned")
                        exited = True
                else:
                    try:
                        chunk = os.read(key.fd, 65_536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(process.stdout)
                    else:
                        output.extend(chunk)
                        if len(output) > 64 * 1024 * 1024:
                            raise OptimizationError(
                                "isolated native output exceeds its byte budget"
                            )
        if not exited or offset != len(payload):
            raise OptimizationError("isolated native execution did not finish")
        return bytes(output)


def run_isolated_job(
    repository: OptimizationJobRepository,
    job_id: str,
    prepared: PreparedOptimization,
    owner: str,
    settings: Settings,
    launch: object,
    retain: Callable[[AnyOptimizationPackage, bytes], None],
    observe: Callable[[AnyJudgeReport], None],
) -> AnyOptimizationJobRecord:
    repository_path = repository.path
    if repository_path is None:
        raise OptimizationError("isolated native execution requires a file-backed repository")
    values = asdict(settings)
    values["workspace"] = str(settings.workspace)
    values["kicad_cli"] = None if settings.kicad_cli is None else str(settings.kicad_cli)
    deadline = prepared.started_at + prepared.request.limits.max_runtime_ms / 1000
    payload = json.dumps(
        {
            "settings": values,
            "repository": str(repository_path),
            "job_id": job_id,
            "owner": owner,
            "request": prepared.request.model_dump(mode="json"),
            "launch": launch,
            "started_at": prepared.started_at,
            "deadline_ms": int(deadline * 1000),
            "import_output_bytes": prepared.import_output_bytes,
        },
        allow_nan=False,
        ensure_ascii=True,
    ).encode("ascii")
    if len(payload) > 262_144:
        raise OptimizationError("isolated native input exceeds its byte budget")

    def cancelled() -> bool:
        return repository.get(job_id, owner).status == "cancelled"

    def checkpoint() -> None:
        # The status query can block behind SQLite work. Bound it on both sides so even the
        # final delivery checkpoint cannot return a late success after a slow query.
        if time.monotonic() >= deadline or cancelled() or time.monotonic() >= deadline:
            raise OptimizationError("isolated native delivery stopped")

    try:
        checkpoint()
        response = _exchange(payload, deadline, cancelled)
        checkpoint()
        result = _Response.model_validate_json(response)
        checkpoint()
        record = result.record
        if record.schema_version != prepared.request.schema_version:
            raise OptimizationError("isolated native version binding is inconsistent")
        if record != repository.get(job_id, owner):
            raise OptimizationError("isolated native record binding is inconsistent")
        if record.status not in TERMINAL and record.status != "awaiting_approval":
            raise OptimizationError("isolated native execution did not finish")
        if (record.status == "awaiting_approval") != (result.source is not None):
            raise OptimizationError("isolated candidate publication is invalid")
        source = None
        package = None
        if result.source is not None:
            source = base64.b64decode(result.source, validate=True)
            checkpoint()
            package = repository.get_package(job_id, owner)
            checkpoint()
            if (
                not source
                or len(source) > settings.max_board_bytes
                or "sha256:" + hashlib.sha256(source).hexdigest()
                != package.binding.candidate_board_revision
            ):
                raise OptimizationError("isolated candidate byte binding is inconsistent")
        checkpoint()
        # Nothing reaches the parent callbacks until the entire response is validated.
        if prepared.request.schema_version == "optimization/v2":
            if any(
                report.schema_version != "optimization/v2"
                or report.board_revision != prepared.request.board_revision
                or report.settings_digest != prepared.request.judge_profile_digest
                or report.required_domains != prepared.request.required_domains
                or report.electrical_inputs_digest != prepared.request.electrical_inputs_digest
                for report in result.judges
            ):
                raise OptimizationError("isolated judge binding is inconsistent")
            if package is not None and package.judge not in result.judges:
                raise OptimizationError("isolated selected judge is missing")
        for report in result.judges:
            checkpoint()
            observe(report)
        if package is not None and source is not None:
            checkpoint()
            retain(package, source)
        checkpoint()
        return record
    except (OptimizationError, ValueError, OSError, subprocess.SubprocessError):
        current = repository.get(job_id, owner)
        if current.status not in TERMINAL:
            return repository.cancel(
                job_id,
                prepared.request,
                owner,
                expected_revision=current.revision,
                failure_code="budget_exhausted" if time.monotonic() >= deadline else "interrupted",
            )
        return current
