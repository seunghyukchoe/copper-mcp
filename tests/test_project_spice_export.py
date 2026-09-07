"""Whole-operation controls for read-only native SPICE export, not simulation."""

import hashlib
import json
import os

import pytest
from test_project_spice_model_binding import _case
from test_project_spice_source import _add_fields, _inputs

from copper_mcp.engineering import project_spice_export as export
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    capture_schematic_project,
)
from copper_mcp.engineering.spice_export import SpiceExportObservation, expected_spice_rows


def _mock_case(tmp_path, monkeypatch):
    case, binding, _artifacts = _inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(export, "run_project_spice_model_binding", lambda *a, **k: binding)

    def execute(prepared, supplied, _settings, deadline):
        assert supplied is binding
        observation = SpiceExportObservation(
            expected_spice_rows(binding, deadline=deadline),
            tuple(path for _artifact, path in prepared.model_paths),
        )
        return export.ProjectSpiceExport(
            prepared.binding_digest,
            prepared.prepared.execution_digest,
            *("sha256:" + "f" * 64 for _ in range(4)),
            "10.0.5",
            observation,
        )

    monkeypatch.setattr(export, "_execute", execute)
    return case


@pytest.mark.parametrize("target", ("source", "model"))
def test_change_during_final_hash_prevents_delivery(tmp_path, monkeypatch, target):
    case = _mock_case(tmp_path, monkeypatch)
    original = export.ProjectSpiceExport._digest

    def changed(report, deadline):
        result = original(report, deadline)
        name = case[0].root_path if target == "source" else "inputs/models.lib"
        (tmp_path / name).write_bytes(b"owned changed bytes")
        return result

    monkeypatch.setattr(export.ProjectSpiceExport, "_digest", changed)
    with pytest.raises(export.ProjectSpiceExportError) as error:
        export.run_project_spice_export(*case[:7])
    assert error.value.__context__ is None
    assert "owned" not in str(error.value)


def test_all_stages_receive_one_shared_deadline(tmp_path, monkeypatch):
    case = _mock_case(tmp_path, monkeypatch)
    deadlines = []

    def observe(function, *, positional=False):
        def called(*args, **kwargs):
            deadlines.append(args[-1] if positional else kwargs["deadline"])
            return function(*args, **kwargs)

        return called

    for name in (
        "run_project_spice_model_binding",
        "capture_electrical_artifacts",
        "prepare_project_spice_source",
    ):
        monkeypatch.setattr(export, name, observe(getattr(export, name)))
    monkeypatch.setattr(export, "_execute", observe(export._execute, positional=True))
    result = export.run_project_spice_export(*case[:7])
    assert len(deadlines) == 5 and len(set(deadlines)) == 1
    assert result.document()["simulation"] == "not_run"


@pytest.mark.parametrize("deadline", (True, float("nan"), float("inf"), 10**1000, 0))
def test_invalid_deadline_refuses_before_binding(tmp_path, monkeypatch, deadline):
    case = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        export, "run_project_spice_model_binding", lambda *a, **k: pytest.fail("must not bind")
    )
    with pytest.raises(export.ProjectSpiceExportError) as error:
        export.run_project_spice_export(*case[:7], deadline=deadline)
    assert error.value.__context__ is None


@pytest.mark.real_kicad
@pytest.mark.skipif(
    not os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI"),
    reason="requires explicit sealed KiCad backend",
)
def test_real_whole_export_is_repeatable_and_preserves_original_files(tmp_path):
    case = _case(tmp_path)
    first = export.run_project_spice_export(*case[:7])
    second = export.run_project_spice_export(*case[:7])
    assert first == second and first.digest == second.digest
    assert first.observation.rows == (
        ("XC1", "AUDIO_OUT", "GND", "CAP"),
        ("XR1", "AUDIO_IN", "AUDIO_OUT", "RES"),
    )
    document = first.document()
    assert document["native_spice_export"] == "verified_within_profile"
    assert document["simulation"] == document["model_accuracy"] == "not_run"
    assert document["apply_authority"] == "none"
    assert "AUDIO" not in repr(first)
    assert all((tmp_path / name).read_bytes() == payload for name, payload in case[-1].items())


def _native_case_with_fields(tmp_path, state):
    case = list(_case(tmp_path))
    sources = dict(case[-1])
    mapping = {"complete": "1=A 2=B", "reversed": "1=B 2=A", "partial": "1=B"}[state]
    sources[case[0].root_path] = _add_fields(
        sources[case[0].root_path], "R1", library="inputs/models.lib", name="RES", pins=mapping
    )
    for name, content in sources.items():
        (tmp_path / name).write_bytes(content)
    capture = capture_schematic_project(
        tmp_path,
        case[0].root_path,
        tuple(
            ProjectFileBinding(name, "sha256:" + hashlib.sha256(content).hexdigest())
            for name, content in sorted(sources.items())
        ),
    )
    case[0], case[-1] = capture, sources
    for index in (4, 5):
        document = json.loads(case[index])
        document["project_capture_digest"] = capture.digest
        if index == 5 and state == "reversed":
            pins = document["models"][1]["references"][0]["pins"]
            pins[0]["binding"]["port"], pins[1]["binding"]["port"] = "B", "A"
        case[index] = json.dumps(document).encode()
    return case


@pytest.mark.real_kicad
@pytest.mark.skipif(
    not os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI"),
    reason="requires explicit sealed KiCad backend",
)
@pytest.mark.parametrize("state", ("complete", "reversed"))
def test_real_existing_assignments_match_the_reviewed_binding(tmp_path, state):
    case = _native_case_with_fields(tmp_path, state)
    result = export.run_project_spice_export(*case[:7])
    nodes = ("AUDIO_OUT", "AUDIO_IN") if state == "reversed" else ("AUDIO_IN", "AUDIO_OUT")
    assert result.observation.rows[1] == ("XR1", *nodes, "RES")
    assert all((tmp_path / name).read_bytes() == payload for name, payload in case[-1].items())


@pytest.mark.real_kicad
@pytest.mark.skipif(
    not os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI"),
    reason="requires explicit sealed KiCad backend",
)
def test_real_partial_assignment_refuses_before_spice_export(tmp_path, monkeypatch):
    case = _native_case_with_fields(tmp_path, "partial")
    prepare = export.prepare_project_spice_source
    reached = []

    def inspect(*args, **kwargs):
        reached.append(True)
        return prepare(*args, **kwargs)

    monkeypatch.setattr(export, "prepare_project_spice_source", inspect)
    monkeypatch.setattr(
        export, "_execute", lambda *a, **k: pytest.fail("partial assignments must not export")
    )
    with pytest.raises(export.ProjectSpiceExportError) as error:
        export.run_project_spice_export(*case[:7])
    assert reached == [True]
    assert error.value.__context__ is None
