"""Owned project-to-ngspice controls; no calibration or engineering authority."""

import json
import os
from pathlib import Path

import pytest
import test_project_spice_model_binding as binding_fixture
from test_project_spice_model_binding import _case

from copper_mcp.engineering import project_operating_point as operation
from copper_mcp.engineering.inputs import parse_electrical_inputs
from copper_mcp.optimization.confined_container import OperatorContainerRuntime


def _inputs(tmp_path):
    case = list(_case(tmp_path))
    declaration = json.loads(case[2])
    declaration["rails"] = [
        {"rail_id": "out", "nominal_voltage_uv": 500000},
        {"rail_id": "vin", "nominal_voltage_uv": 1000000},
    ]
    declaration["load_cases"] = [
        {"case_id": "load_a", "rail_id": "out", "current_ua": 250, "duration_ms": 1000},
        {"case_id": "load_b", "rail_id": "out", "current_ua": 500, "duration_ms": 1000},
    ]
    declaration["operating_limits"] = {
        "min_temperature_millic": 0,
        "max_temperature_millic": 80000,
        "max_input_voltage_uv": 10000000,
    }
    case[2] = json.dumps(declaration).encode()
    digest = parse_electrical_inputs(case[2]).digest
    for index in (4, 5):
        document = json.loads(case[index])
        document["declaration_digest"] = digest
        case[index] = json.dumps(document).encode()
    ground = {"reference": "C1", "pin_number": "2"}
    output = {"reference": "C1", "pin_number": "1"}
    supply = {"reference": "R1", "pin_number": "1"}
    cases = []
    for identifier in ("load_a", "load_b"):
        cases.append(
            {
                "case_id": identifier,
                "temperature_millic": 25000,
                "ground": ground,
                "rail_nodes": [
                    {"rail_id": "out", "positive": output, "negative": ground},
                    {"rail_id": "vin", "positive": supply, "negative": ground},
                ],
                "energized_rails": ["vin"],
                "current_loads": [{"load_case_id": identifier, "from": output, "to": ground}],
            }
        )
    document = {
        "schema_version": "project-spice-operating-point-cases/v1",
        "declaration_digest": digest,
        "project_capture_digest": case[0].digest,
        "cases": cases,
    }
    return case, json.dumps(document).encode()


def test_malformed_case_refuses_before_native_project_work(tmp_path, monkeypatch):
    monkeypatch.setattr(
        operation,
        "_run_project_spice_export_retained",
        lambda *a, **k: pytest.fail("must not execute native project work"),
    )
    case, _cases = _inputs(tmp_path)
    runtime = OperatorContainerRuntime(
        Path(os.sys.executable), tmp_path / "missing.sock", tmp_path / "config"
    )
    with pytest.raises(operation.ProjectOperatingPointError) as error:
        operation.run_project_operating_points(*case[:6], b"{}", case[6], runtime)
    assert error.value.__context__ is None


@pytest.mark.real_kicad
@pytest.mark.skipif(
    not os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")
    or not os.environ.get("COPPER_MCP_TEST_DOCKER_SOCKET"),
    reason="requires explicit sealed KiCad and pinned local ngspice container runtime",
)
def test_real_project_cases_reuse_export_and_preserve_sources(tmp_path, monkeypatch):
    case, cases = _inputs(tmp_path)
    runtime = OperatorContainerRuntime(
        Path(os.environ["COPPER_MCP_TEST_DOCKER"]),
        Path(os.environ["COPPER_MCP_TEST_DOCKER_SOCKET"]),
        tmp_path / "config",
    )
    calls = []
    producer = operation._run_project_spice_export_retained

    def counted(*args, **kwargs):
        calls.append(True)
        return producer(*args, **kwargs)

    monkeypatch.setattr(operation, "_run_project_spice_export_retained", counted)
    result = operation.run_project_operating_points(*case[:6], cases, case[6], runtime)
    assert calls == [True] and len(result.cases) == 2
    repeated = operation.run_project_operating_points(*case[:6], cases, case[6], runtime)
    assert calls == [True, True]
    assert repeated == result and repeated.digest == result.digest
    for sample, expected in zip(result.cases, (0.75, 0.5), strict=True):
        mapping = dict(sample.node_map)
        values = {name: float(value) for name, _unit, value in sample.observation.vectors}
        assert values[f"v({mapping['audio_out']})"] == pytest.approx(expected, abs=1e-12)
        assert values[f"v({mapping['audio_in']})"] == pytest.approx(1.0, abs=1e-12)
    assert result.document()["engineering_validation"] == "inconclusive"
    assert result.document()["apply_authority"] == "none"
    assert repr(result) == "<ProjectOperatingPoints redacted>"
    assert all((tmp_path / name).read_bytes() == data for name, data in case[-1].items())


@pytest.mark.real_kicad
@pytest.mark.skipif(
    not os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")
    or not os.environ.get("COPPER_MCP_TEST_DOCKER_SOCKET"),
    reason="requires explicit sealed KiCad and pinned local ngspice container runtime",
)
def test_real_asymmetric_model_preserves_declared_terminal_direction(tmp_path, monkeypatch):
    # The owned two-terminal placeholder exercises model/pin assignment, not suitability
    # of a diode model for a real BOM capacitor. Model accuracy remains explicitly not_run.
    monkeypatch.setattr(
        binding_fixture,
        "_MODEL",
        (b".model CLAMP D(IS=1n N=1)\n.subckt RES A B\nR1 A B 1k\n.ends RES\n"),
    )
    case, cases = _inputs(tmp_path)
    runtime = OperatorContainerRuntime(
        Path(os.environ["COPPER_MCP_TEST_DOCKER"]),
        Path(os.environ["COPPER_MCP_TEST_DOCKER_SOCKET"]),
        tmp_path / "config",
    )
    forward = operation.run_project_operating_points(*case[:6], cases, case[6], runtime)
    terminal = json.loads(case[5])
    pins = terminal["models"][0]["references"][0]["pins"]
    pins[0]["binding"]["port"], pins[1]["binding"]["port"] = "K", "A"
    case[5] = json.dumps(terminal).encode()
    reverse = operation.run_project_operating_points(*case[:6], cases, case[6], runtime)

    def output(report):
        sample = report.cases[0]
        name = "v(" + dict(sample.node_map)["audio_out"] + ")"
        return next(
            float(value) for vector, _kind, value in sample.observation.vectors if vector == name
        )

    assert 0 < output(forward) < 0.5
    assert 0.6 < output(reverse) < 0.8
    assert forward.digest != reverse.digest
    assert forward.document()["model_accuracy"] == reverse.document()["model_accuracy"] == "not_run"
    assert all((tmp_path / name).read_bytes() == data for name, data in case[-1].items())
