"""Mocked coordinator fault controls, separate from the real project/simulator fixture."""

from types import SimpleNamespace

import pytest

from copper_mcp.config import Settings
from copper_mcp.engineering import project_operating_point as operation
from copper_mcp.engineering._ngspice_execution import NgspiceExecution
from copper_mcp.engineering._operating_point_deck import OperatingPointDeck
from copper_mcp.engineering.spice_operating_point import SpiceOperatingPoint

DIGEST = "sha256:" + "a" * 64


def _setup(tmp_path, monkeypatch):
    state = {"runs": 0, "changed": False, "cancelled": False}
    retained = SimpleNamespace(
        report=SimpleNamespace(_digest=lambda deadline: DIGEST),
        binding=SimpleNamespace(electrical_artifact_capture_digest=DIGEST),
    )
    cases = SimpleNamespace(
        cases=(SimpleNamespace(case_id="owned"),),
        case_document_digest=DIGEST,
        binding_digest=DIGEST,
    )
    monkeypatch.setattr(operation, "_parse", lambda *a: None)
    monkeypatch.setattr(operation, "_run_project_spice_export_retained", lambda *a, **k: retained)
    monkeypatch.setattr(operation, "resolve_operating_point_cases", lambda *a, **k: cases)
    monkeypatch.setattr(
        operation,
        "compile_operating_point_deck",
        lambda *a, **k: OperatingPointDeck(
            b"owned deck", (("v(n0)", "voltage"),), (("owned", "n0"),)
        ),
    )

    def run(*args, **kwargs):
        state["runs"] += 1
        return NgspiceExecution(operation.IMAGE, DIGEST, DIGEST, b"owned raw", b"owned log")

    monkeypatch.setattr(operation, "run_ngspice", run)
    monkeypatch.setattr(operation, "validate_native_diagnostics", lambda *a, **k: None)
    monkeypatch.setattr(
        operation,
        "parse_spice_operating_point",
        lambda *a, **k: SpiceOperatingPoint(
            operation.TITLE, operation.BACKEND_COMMAND, (("v(n0)", "voltage", "1.0"),)
        ),
    )
    monkeypatch.setattr(
        operation,
        "capture_electrical_artifacts",
        lambda *a, **k: SimpleNamespace(digest="changed" if state["changed"] else DIGEST),
    )
    monkeypatch.setattr(operation.execution, "_verify_workspace_source", lambda *a: None)
    runtime = object()  # The mocked execution boundary does not exercise runtime admission.

    def execute(**kwargs):
        return operation.run_project_operating_points(
            SimpleNamespace(_files=()),
            (),
            b"",
            b"",
            b"",
            b"",
            b"",
            Settings(workspace=tmp_path),
            runtime,
            **kwargs,
        )

    return state, execute


def _refuses(execute, **kwargs):
    with pytest.raises(operation.ProjectOperatingPointError) as error:
        execute(**kwargs)
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert "private" not in str(error.value)


def test_replay_disagreement_refuses(tmp_path, monkeypatch):
    state, execute = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        operation,
        "parse_spice_operating_point",
        lambda *a, **k: SpiceOperatingPoint(
            operation.TITLE, operation.BACKEND_COMMAND, (("v(n0)", "voltage", str(state["runs"])),)
        ),
    )
    _refuses(execute)
    assert state["runs"] == 2


def test_artifact_change_during_final_hash_prevents_delivery(tmp_path, monkeypatch):
    state, execute = _setup(tmp_path, monkeypatch)
    digest = operation.ProjectOperatingPoints._digest

    def changed(report, deadline):
        value = digest(report, deadline)
        state["changed"] = True
        return value

    monkeypatch.setattr(operation.ProjectOperatingPoints, "_digest", changed)
    _refuses(execute)
    assert state["runs"] == 2


def test_final_source_verification_failure_is_context_free(tmp_path, monkeypatch):
    _state, execute = _setup(tmp_path, monkeypatch)

    def changed(*args):
        raise OSError("private source changed")

    monkeypatch.setattr(operation.execution, "_verify_workspace_source", changed)
    _refuses(execute)


def test_cancellation_stops_before_another_simulator_run(tmp_path, monkeypatch):
    state, execute = _setup(tmp_path, monkeypatch)
    run = operation.run_ngspice

    def cancelled(*args, **kwargs):
        result = run(*args, **kwargs)
        state["cancelled"] = True
        return result

    monkeypatch.setattr(operation, "run_ngspice", cancelled)
    _refuses(execute, cancelled=lambda: state["cancelled"])
    assert state["runs"] == 1


def test_expiration_during_final_hash_is_not_success(tmp_path, monkeypatch):
    _state, execute = _setup(tmp_path, monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(operation.time, "monotonic", lambda: clock[0])
    digest = operation.ProjectOperatingPoints._digest

    def expired(report, deadline):
        result = digest(report, deadline)
        clock[0] = 100.0
        return result

    monkeypatch.setattr(operation.ProjectOperatingPoints, "_digest", expired)
    _refuses(execute, deadline=10.0)
