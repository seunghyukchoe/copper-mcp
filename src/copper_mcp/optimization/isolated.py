"""Bounded parent supervisor for native code imported fresh from an inventoried source tree."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from pydantic import Field

from copper_mcp.config import Settings
from copper_mcp.optimization.contracts import ClosedModel, OptimizationError
from copper_mcp.optimization.inputs import PreparedOptimization
from copper_mcp.optimization.judge import JudgeReport
from copper_mcp.optimization.lifecycle import TERMINAL, OptimizationJobRecord
from copper_mcp.optimization.package import OptimizationPackage
from copper_mcp.optimization.repository import OptimizationJobRepository


class _Response(ClosedModel):
    record: OptimizationJobRecord
    judges: Annotated[tuple[JudgeReport, ...], Field(max_length=32)]
    source: Annotated[str, Field(min_length=1, max_length=64 * 1024 * 1024)] | None


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def _exchange(payload: bytes, deadline: float, cancelled: Callable[[], bool]) -> bytes:
    if os.name != "posix":
        raise OptimizationError("isolated native execution is unavailable")
    entry = Path(__file__).with_name("isolated_entry.py")
    process = subprocess.Popen(  # noqa: S603 - fixed local interpreter and package-owned entrypoint
        [sys.executable, "-I", str(entry)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={"PATH": os.defpath, "LANG": "C.UTF-8"},
    )
    assert process.stdin is not None and process.stdout is not None
    output = bytearray()
    offset = 0
    selector = selectors.DefaultSelector()
    os.set_blocking(process.stdin.fileno(), False)
    os.set_blocking(process.stdout.fileno(), False)
    selector.register(process.stdin, selectors.EVENT_WRITE)
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while selector.get_map():
            if time.monotonic() >= deadline or cancelled():
                raise OptimizationError("isolated native execution stopped")
            for key, _events in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                if key.fileobj is process.stdin:
                    try:
                        offset += os.write(key.fd, payload[offset : offset + 16_384])
                    except BrokenPipeError:
                        offset = len(payload)
                    if offset == len(payload):
                        selector.unregister(process.stdin)
                        process.stdin.close()
                else:
                    chunk = os.read(key.fd, 65_536)
                    if not chunk:
                        selector.unregister(process.stdout)
                    else:
                        output.extend(chunk)
                        if len(output) > 64 * 1024 * 1024:
                            raise OptimizationError(
                                "isolated native output exceeds its byte budget"
                            )
        if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
            raise OptimizationError("isolated native execution failed")
        return bytes(output)
    finally:
        selector.close()
        _terminate(process)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()


def run_isolated_job(
    repository: OptimizationJobRepository,
    job_id: str,
    prepared: PreparedOptimization,
    owner: str,
    settings: Settings,
    launch: object,
    retain: Callable[[OptimizationPackage, bytes], None],
    observe: Callable[[JudgeReport], None],
) -> OptimizationJobRecord:
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
