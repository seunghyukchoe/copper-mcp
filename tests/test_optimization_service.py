"""Private optimization service retention tests."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import cast

import pytest
from test_optimization_inputs import launch as launch

from copper_mcp.config import Settings
from copper_mcp.optimization.judge import JudgeReport
from copper_mcp.optimization.repository import OptimizationJobRepository
from copper_mcp.optimization.service import OptimizationService

OWNER = "sha256:" + "8" * 64


def test_cancellation_releases_done_retention_but_defers_running_accounting(
    tmp_path, launch
) -> None:
    repository = OptimizationJobRepository(tmp_path / "optimization.sqlite3")
    service = OptimizationService(Settings(workspace=tmp_path), repository)
    started = threading.Event()
    release = threading.Event()

    def runner(repository, job_id, _prepared, owner, *_args):
        started.set()
        release.wait(timeout=5)
        return repository.get(job_id, owner)

    service._job_runner = runner
    try:
        record = service.start(launch, OWNER)
        assert started.wait(timeout=5)
        private = service._jobs[record.job_id]
        private.source = b"candidate"
        private.judges = [cast(JudgeReport, object())]
        private.reserved_bytes += len(private.source)
        original_reserved = private.reserved_bytes
        settled = threading.Event()
        private.future.add_done_callback(lambda _future: settled.set())

        cancelled = service.cancel(record.job_id, OWNER, record.revision)
        assert cancelled.status == "cancelled"
        assert private.source is None
        assert private.judges == []
        assert private.reserved_bytes == original_reserved

        release.set()
        private.future.result(timeout=5)
        assert settled.wait(timeout=5)
        assert private.source is None
        assert private.judges == []
        assert private.reserved_bytes == 0

        completed = service.start({**launch, "seed": 1}, OWNER)
        done_private = service._jobs[completed.job_id]
        done_settled = threading.Event()
        done_private.future.add_done_callback(lambda _future: done_settled.set())
        done_private.future.result(timeout=5)
        assert done_settled.wait(timeout=5)
        done_private.source = b"candidate"
        done_private.reserved_bytes = len(done_private.source)
        done_private.judges = [cast(JudgeReport, object())]
        service.cancel(completed.job_id, OWNER, completed.revision)
        assert done_private.source is None
        assert done_private.judges == []
        assert done_private.reserved_bytes == 0
    finally:
        release.set()
        service.close()
        repository.close()


@pytest.mark.parametrize("raises", [False, True])
def test_finished_callback_releases_staging_cells_and_failed_future(tmp_path, launch, raises):
    repository = OptimizationJobRepository(tmp_path / "staged.sqlite3")
    service = OptimizationService(Settings(workspace=tmp_path), repository)
    staged, release, settled = threading.Event(), threading.Event(), threading.Event()

    def runner(repository, job_id, prepared, owner, _settings, _launch, retain, observe):
        retain(SimpleNamespace(request_digest=prepared.request.digest), b"private staged bytes")
        observe(cast(JudgeReport, object()))
        staged.set()
        assert release.wait(timeout=5)
        if raises:
            raise RuntimeError("synthetic private execution failure")
        return repository.get(job_id, owner)

    service._job_runner = runner
    try:
        record = service.start(launch, OWNER)
        assert staged.wait(timeout=5)
        private = service._jobs[record.job_id]
        future = private.future
        assert future is not None
        future.add_done_callback(lambda _future: settled.set())
        callbacks = [
            callback
            for callback in future._done_callbacks
            if "pending_source" in callback.__code__.co_freevars
        ]
        assert len(callbacks) == 1
        callback = callbacks[0]
        cells = dict(zip(callback.__code__.co_freevars, callback.__closure__, strict=True))
        assert cells["pending_source"].cell_contents == b"private staged bytes"
        release.set()
        if raises:
            with pytest.raises(RuntimeError, match="synthetic private execution failure"):
                future.result(timeout=5)
        else:
            future.result(timeout=5)
        assert settled.wait(timeout=5)
        assert cells["pending_source"].cell_contents is None
        assert cells["pending_reports"].cell_contents == []
        assert private.source is None and private.judges == []
        assert private.reserved_bytes == 0
        if raises:
            assert private.future is None
    finally:
        release.set()
        service.close()
        repository.close()
