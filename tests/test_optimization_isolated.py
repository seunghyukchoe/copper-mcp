"""Fresh native execution cannot reuse stale/monkeypatched modules from its MCP parent."""

import base64
import errno
import json
import os
import select
import signal
import sys
import time
from pathlib import Path

import pytest
from test_optimization_coordinator import synthetic_authority as synthetic_authority
from test_optimization_inputs import launch as launch

from copper_mcp.config import Settings
from copper_mcp.optimization.inputs import prepare_optimization
from copper_mcp.optimization.isolated import run_isolated_job
from copper_mcp.optimization.isolated_entry import _inventory, _SourceOnlyFinder
from copper_mcp.optimization.repository import OptimizationJobRepository
from copper_mcp.routing import AStarRouter


def owned_guard_command(command, worker_code):
    """Exercise the real guardian with an owned test worker, not a second guardian model."""
    return [
        sys.executable,
        "-I",
        "-c",
        "import runpy,sys; guard=runpy.run_path(sys.argv[1]); "
        "guard['supervise']([sys.executable,'-I','-c',sys.argv[4]],int(sys.argv[2]),float(sys.argv[3]))",
        *command[2:],
        worker_code,
    ]


@pytest.fixture
def exchange_child(monkeypatch):
    from copper_mcp.optimization import isolated

    popen = isolated.subprocess.Popen
    processes = []
    descriptors = {}

    def owned_echo(command, **kwargs):
        process = popen(
            owned_guard_command(
                command, "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
            ),
            **kwargs,
        )
        processes.append(process)
        descriptors.update(write=process.stdin.fileno(), read=process.stdout.fileno())
        return process

    monkeypatch.setattr(isolated.subprocess, "Popen", owned_echo)
    return processes, descriptors


def test_exchange_ready_eagain_preserves_one_child_payload_and_response(
    monkeypatch, exchange_child
):
    from copper_mcp.optimization import isolated

    processes, descriptors = exchange_child
    read, write = isolated.os.read, isolated.os.write
    blocked = set()
    writes = []

    def busy_write(fd, value):
        if fd == descriptors.get("write"):
            if "write" not in blocked:
                blocked.add("write")
                raise BlockingIOError(errno.EAGAIN, "owned transient write")
            writes.append(len(value))
        return write(fd, value)

    def busy_read(fd, count):
        if fd == descriptors.get("read") and "read" not in blocked:
            blocked.add("read")
            raise BlockingIOError(errno.EAGAIN, "owned transient read")
        return read(fd, count)

    monkeypatch.setattr(isolated.os, "write", busy_write)
    monkeypatch.setattr(isolated.os, "read", busy_read)
    payload = b"owned complete payload\x00" * 2000
    assert isolated._exchange(payload, time.monotonic() + 10, lambda: False) == payload
    assert blocked == {"write", "read"}
    assert writes and max(writes) <= select.PIPE_BUF
    assert len(processes) == 1 and processes[0].returncode == -signal.SIGKILL
    assert processes[0].stdin.closed and processes[0].stdout.closed


@pytest.mark.parametrize("direction", ["write", "read"])
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_exchange_eagain_still_terminates_for_cancel_or_deadline(
    monkeypatch, exchange_child, direction, stop
):
    from copper_mcp.optimization import isolated
    from copper_mcp.optimization.contracts import OptimizationError

    processes, descriptors = exchange_child
    operation = getattr(isolated.os, direction)
    blocked = []
    now = time.monotonic()

    def busy(fd, value):
        if fd == descriptors.get(direction):
            blocked.append(fd)
            raise BlockingIOError(errno.EAGAIN, "owned stalled pipe")
        return operation(fd, value)

    monkeypatch.setattr(isolated.os, direction, busy)
    monkeypatch.setattr(
        isolated.time, "monotonic", lambda: now + 10 if blocked and stop == "deadline" else now
    )
    with pytest.raises(OptimizationError, match="stopped"):
        isolated._exchange(b"owned", now + 5, lambda: bool(blocked) and stop == "cancel")
    assert blocked and len(processes) == 1
    assert processes[0].returncode is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed


def test_parent_cached_router_cannot_supply_the_recorded_execution(
    launch, tmp_path: Path, monkeypatch
):
    settings = Settings(
        workspace=tmp_path,
        max_route_preview_seconds=120,
        kicad_cli=tmp_path / "intentionally-unavailable-kicad",
    )
    prepared = prepare_optimization(launch, settings)

    def stale_parent(*_args, **_kwargs):
        raise AssertionError("stale parent router must never execute")

    monkeypatch.setattr(AStarRouter, "propose", stale_parent)
    reports = []
    artifacts = []
    with OptimizationJobRepository(tmp_path / "jobs.sqlite3") as repository:
        owner = "sha256:" + "a" * 64
        record = repository.create(prepared.request, owner)
        result = run_isolated_job(
            repository,
            record.job_id,
            prepared,
            owner,
            settings,
            launch,
            lambda package, source: artifacts.append((package, source)),
            reports.append,
        )
    assert result.failure_code == "required_domain_inconclusive"
    assert reports and reports[0].domains[0].status == "inconclusive"
    assert not artifacts


@pytest.mark.parametrize("kind", ["directory_link", "file_link", "fifo"])
def test_source_inventory_refuses_untraversed_or_special_entries(tmp_path, kind):
    root = tmp_path / "package"
    root.mkdir()
    (root / "__init__.py").write_text("# package\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.py").write_text("# would be imported\n")
    if kind == "directory_link":
        (root / "submodule").symlink_to(outside, target_is_directory=True)
    elif kind == "file_link":
        (root / "linked.py").symlink_to(outside / "hidden.py")
    else:
        os.mkfifo(root / "not_python")
    with pytest.raises(ValueError, match="source inventory is unavailable"):
        _inventory(root)


@pytest.mark.parametrize("suffix", [".so", ".pyd", ".dll", ".pyc"])
def test_source_inventory_refuses_non_source_importable_files(tmp_path, suffix):
    (tmp_path / "__init__.py").write_text("# source\n")
    (tmp_path / ("hidden" + suffix)).write_bytes(b"not inventoried Python")
    with pytest.raises(ValueError, match="non-source executable"):
        _inventory(tmp_path)


def test_source_only_importer_rejects_sourceless_cache_modules(tmp_path):
    import importlib.machinery
    import py_compile

    cache = tmp_path / "__pycache__"
    cache.mkdir()
    source = tmp_path / "original.py"
    source.write_text("# smoke source\n")
    py_compile.compile(str(source), cfile=str(cache / "hidden.pyc"), doraise=True)
    name = "copper_mcp.__pycache__.hidden"
    spec = importlib.machinery.PathFinder.find_spec(name, [str(cache)])
    assert spec is not None and isinstance(spec.loader, importlib.machinery.SourcelessFileLoader)
    with pytest.raises(ImportError, match="not inventoried"):
        _SourceOnlyFinder(frozenset({str(source)})).find_spec(name, [str(cache)])


def test_child_cannot_exit_leaving_a_well_formed_running_record(launch, tmp_path, monkeypatch):
    from copper_mcp.optimization import isolated

    settings = Settings(workspace=tmp_path, max_route_preview_seconds=120)
    prepared = prepare_optimization(launch, settings)
    owner = "sha256:" + "a" * 64
    with OptimizationJobRepository(tmp_path / "jobs.sqlite3") as repository:
        record = repository.create(prepared.request, owner)
        monkeypatch.setattr(
            isolated,
            "_exchange",
            lambda *_args: json.dumps(
                {
                    "record": record.document(),
                    "judges": [],
                    "source": None,
                }
            ).encode(),
        )
        result = run_isolated_job(
            repository,
            record.job_id,
            prepared,
            owner,
            settings,
            launch,
            lambda *_args: pytest.fail("no candidate expected"),
            lambda *_args: pytest.fail("no judge expected"),
        )
        assert result.failure_code == "interrupted"


@pytest.mark.parametrize(
    "corruption",
    ["missing_source", "missing_field", "not_object", "wrong_bytes", "extra", "bad_judge"],
)
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_invalid_child_delivery_fences_published_metadata_before_callbacks(
    launch, tmp_path, monkeypatch, synthetic_authority, corruption, version
):
    if version == "v2":
        launch = {**launch, "schema_version": "optimization/v2"}
    from copper_mcp.optimization import isolated
    from copper_mcp.optimization.coordinator import coordinate_optimization
    from copper_mcp.optimization.worker import execute_optimization_job

    settings = Settings(workspace=tmp_path, max_route_preview_seconds=120)
    prepared = prepare_optimization(launch, settings)
    owner = "sha256:" + "a" * 64
    observed, retained = [], []
    with OptimizationJobRepository(tmp_path / "jobs.sqlite3") as repository:
        record = repository.create(prepared.request, owner)

        def fake_exchange(_payload, _deadline, _cancelled):
            sources, reports = [], []
            ready = execute_optimization_job(
                repository,
                record.job_id,
                prepared.request,
                owner,
                lambda probe: coordinate_optimization(
                    prepared,
                    settings,
                    probe,
                    retain_private_result=lambda _package, source: sources.append(source),
                    observe_judge=reports.append,
                ),
            )
            assert ready.status == "awaiting_approval"
            result = {
                "record": ready.document(),
                "judges": [report.model_dump(mode="json") for report in reports],
                "source": base64.b64encode(sources[0]).decode("ascii"),
            }
            if corruption == "missing_source":
                result["source"] = None
            elif corruption == "missing_field":
                del result["record"]
            elif corruption == "not_object":
                return b"[]"
            elif corruption == "wrong_bytes":
                result["source"] = base64.b64encode(b"wrong board").decode("ascii")
            elif corruption == "extra":
                result["unexpected"] = "PRIVATE-CANARY"
            else:
                result["judges"] = [{}]
            return json.dumps(result).encode()

        monkeypatch.setattr(isolated, "_exchange", fake_exchange)
        result = run_isolated_job(
            repository,
            record.job_id,
            prepared,
            owner,
            settings,
            launch,
            lambda package, source: retained.append((package, source)),
            observed.append,
        )
        assert result.failure_code == "interrupted"
        assert result.package_digest is None
        assert repository.get(record.job_id, owner) == result
        assert not observed and not retained


@pytest.mark.parametrize(
    "late_stage",
    [
        "exchange",
        "response_parse",
        "source_decode",
        "source_hash",
        "observer",
        "final_status_read",
        None,
    ],
)
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_parent_deadline_covers_decoding_and_callback_delivery(
    launch, tmp_path, monkeypatch, synthetic_authority, late_stage, version
):
    if version == "v2":
        launch = {**launch, "schema_version": "optimization/v2"}
    from copper_mcp.optimization import isolated
    from copper_mcp.optimization.coordinator import coordinate_optimization
    from copper_mcp.optimization.worker import execute_optimization_job

    settings = Settings(workspace=tmp_path, max_route_preview_seconds=120)
    prepared = prepare_optimization(launch, settings)
    owner = "sha256:" + "a" * 64
    with OptimizationJobRepository(tmp_path / "jobs.sqlite3") as repository:
        record = repository.create(prepared.request, owner)
        sources, reports = [], []
        ready = execute_optimization_job(
            repository,
            record.job_id,
            prepared.request,
            owner,
            lambda probe: coordinate_optimization(
                prepared,
                settings,
                probe,
                retain_private_result=lambda _package, source: sources.append(source),
                observe_judge=reports.append,
            ),
        )
        assert ready.status == "awaiting_approval"
        response = json.dumps(
            {
                "record": ready.document(),
                "judges": [report.model_dump(mode="json") for report in reports],
                "source": base64.b64encode(sources[0]).decode("ascii"),
            }
        ).encode()
        deadline = prepared.started_at + prepared.request.limits.max_runtime_ms / 1000
        clock = [deadline - 1]
        monkeypatch.setattr(isolated.time, "monotonic", lambda: clock[0])

        def expire(stage):
            if stage == late_stage:
                clock[0] = deadline

        def exchange(*_args):
            expire("exchange")
            return response

        parse = isolated._Response.model_validate_json

        def late_parse(cls, *args, **kwargs):
            result = parse(*args, **kwargs)
            expire("response_parse")
            return result

        decode = isolated.base64.b64decode

        def late_decode(*args, **kwargs):
            result = decode(*args, **kwargs)
            expire("source_decode")
            return result

        sha256 = isolated.hashlib.sha256

        def late_hash(payload=b"", *args, **kwargs):
            result = sha256(payload, *args, **kwargs)
            if payload == sources[0]:
                expire("source_hash")
            return result

        observed, retained = [], []

        get = repository.get

        def late_status_read(*args, **kwargs):
            result = get(*args, **kwargs)
            if retained:
                expire("final_status_read")
            return result

        def observe(report):
            observed.append(report)
            expire("observer")

        monkeypatch.setattr(isolated, "_exchange", exchange)
        monkeypatch.setattr(isolated._Response, "model_validate_json", classmethod(late_parse))
        monkeypatch.setattr(isolated.base64, "b64decode", late_decode)
        monkeypatch.setattr(isolated.hashlib, "sha256", late_hash)
        monkeypatch.setattr(repository, "get", late_status_read)
        result = run_isolated_job(
            repository,
            record.job_id,
            prepared,
            owner,
            settings,
            launch,
            lambda package, source: retained.append((package, source)),
            observe,
        )
        if late_stage is None:
            assert result == ready and observed == reports and retained
        else:
            assert result.failure_code == "budget_exhausted"
            assert result.package_digest is None
            if late_stage == "final_status_read":
                # The callbacks only stage private bytes. Expiry must still prevent a successful
                # return, so the service cannot publish that staging as an approved delivery.
                assert retained and observed == reports
            else:
                assert not retained
                assert len(observed) == (1 if late_stage == "observer" else 0)
