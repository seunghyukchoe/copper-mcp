"""Native import/review cannot begin a context scan beyond the inherited work budget."""

import time

import pytest
from test_optimization_inputs import launch as launch
from test_optimization_native_import import imported_launch as imported_launch

from copper_mcp.kicad_cli import KiCadCliError
from copper_mcp.optimization import inputs, service
from copper_mcp.optimization.contracts import OptimizationError
from copper_mcp.optimization.repository import OptimizationJobRepository


@pytest.mark.parametrize("remaining", [2.25, 0.25])
def test_post_import_context_scan_uses_only_remaining_time(imported_launch, monkeypatch, remaining):
    value, settings = imported_launch
    clock = [time.monotonic()]
    deadline = clock[0] + 20
    native_import, capture = inputs.import_native_board, inputs._drc_context
    scans = []

    def imported(*args, **kwargs):
        result = native_import(*args, **kwargs)
        clock[0] = kwargs["deadline"] - remaining
        return result

    def bounded(path, scoped, captured=None):
        scans.append(scoped.max_drc_context_scan_seconds)
        assert scans[-1] <= int(deadline - clock[0])
        return capture(path, scoped, captured)

    monkeypatch.setattr(inputs.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(inputs, "import_native_board", imported)
    monkeypatch.setattr(inputs, "_drc_context", bounded)
    if remaining < 1:
        with pytest.raises(OptimizationError):
            inputs.prepare_optimization(value, settings, deadline=deadline)
        assert len(scans) == 1
    else:
        inputs.prepare_optimization(value, settings, deadline=deadline)
        assert scans == [10, 2]


@pytest.mark.parametrize("remaining", [2.25, 0.25])
def test_review_rescan_cannot_outlive_review_deadline(
    imported_launch, tmp_path, monkeypatch, remaining
):
    value, settings = imported_launch
    owner = "sha256:" + "c" * 64
    repository = OptimizationJobRepository(tmp_path / "review.sqlite3")
    host = service.OptimizationService(settings, repository, allow_host_confirmation=True)
    try:
        record = host.start(value, owner)
        host._jobs[record.job_id].future.result(timeout=120)
        record, _ = host.get(record.job_id, owner)
        assert record.status == "awaiting_approval"
        clock = [time.monotonic()]
        deadline = clock[0] + min(
            settings.max_route_preview_seconds,
            host._jobs[record.job_id].request.limits.max_runtime_ms / 1000,
        )
        native_import, capture = service.import_native_board, service._drc_context
        scans = []

        def imported(*args, **kwargs):
            result = native_import(*args, **kwargs)
            clock[0] = kwargs["deadline"] - remaining
            return result

        def bounded(path, scoped, captured=None):
            scans.append(scoped.max_drc_context_scan_seconds)
            assert scans[-1] <= int(deadline - clock[0])
            if len(scans) == 2:
                raise OptimizationError("owned stop after verifying the final scan allowance")
            return capture(path, scoped, captured)

        monkeypatch.setattr(service.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(service, "import_native_board", imported)
        monkeypatch.setattr(service, "_drc_context", bounded)
        monkeypatch.setattr(
            host.authority,
            "issue_from_human_channel",
            lambda *_a, **_kw: pytest.fail("deadline control must not issue consent"),
        )
        with pytest.raises((OptimizationError, KiCadCliError)):
            host.approve_from_host(
                record.job_id, owner, record.revision, record.package_digest, record.judge_digest
            )
        assert scans == ([10] if remaining < 1 else [10, 2])
        assert repository.get(record.job_id, owner).status == "awaiting_approval"
    finally:
        host.close()
        repository.close()
