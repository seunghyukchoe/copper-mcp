"""Captured-byte and native-component integration, not simulation evidence."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from test_bom_reconciliation import (
    _bindings,
    _component,
    _csv,
    _declaration,
    _inventory,
    _write_artifacts,
)
from test_project_erc import build_project
from test_project_pin_net_map import _alias_project

from copper_mcp.config import Settings
from copper_mcp.engineering import project_spice_model_binding as binding
from copper_mcp.engineering.inputs import parse_electrical_inputs
from copper_mcp.engineering.native_pin_net_map import NativePinNet, NativePinNetMap, SourcePinAlias
from copper_mcp.engineering.project_pin_net_map import ProjectPinNetMap
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    capture_schematic_project,
)
from copper_mcp.engineering.spice_model_library import parse_spice_model_library

_CLI = os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")
_MODEL = b".subckt CAP A B\nC1 A B 100n\n.ends CAP\n.subckt RES A B\nR1 A B 1k\n.ends RES\n"


def _sha(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _case(tmp_path, monkeypatch=None):
    capture, libraries, sources = build_project(tmp_path)
    artifacts = {
        "bom-main": (
            "inputs/bom.csv",
            _csv(("C1", "100n", "", 1, False), ("R1", "1k", "", 1, False)),
        ),
        "models-main": ("inputs/models.lib", _MODEL),
    }
    declaration = json.loads(
        _declaration(artifacts, (("capacitors", "bom-main", 1), ("resistors", "bom-main", 1)))
    )
    # v1 model_digest remains opaque. The new document binds actual definition bytes separately.
    declaration["model_bindings"] = [
        {
            "model_id": item,
            "bom_item_id": item,
            "artifact_id": "models-main",
            "model_kind": "spice",
            "model_digest": "sha256:" + "a" * 64,
        }
        for item in ("capacitors", "resistors")
    ]
    declaration_json = json.dumps(declaration).encode()
    paths = _write_artifacts(tmp_path, artifacts)
    bom = _bindings(declaration_json, capture.digest, {"capacitors": ("C1",), "resistors": ("R1",)})
    definitions = parse_spice_model_library(_MODEL, deadline=time.monotonic() + 30).definitions
    terminals = {
        "schema_version": "project-spice-terminal-bindings/v1",
        "declaration_digest": parse_electrical_inputs(declaration_json).digest,
        "project_capture_digest": capture.digest,
        "models": [
            {
                "model_id": item,
                "definition_name": definition.name,
                "definition_digest": definition.digest,
                "references": [
                    {
                        "reference": ref,
                        "pins": [
                            {"pin_number": str(index), "binding": {"kind": "port", "port": port}}
                            for index, port in enumerate(definition.ports, 1)
                        ],
                    }
                ],
            }
            for item, ref, definition in zip(
                ("capacitors", "resistors"), ("C1", "R1"), definitions, strict=True
            )
        ],
    }
    if monkeypatch is not None:
        inventory = _inventory(capture.digest, (_component("C1", "100n"), _component("R1", "1k")))
        nodes = tuple(
            NativePinNet(
                ref,
                str(index),
                "Device:Part",
                str(index),
                f"/NET_{index}",
                "Default",
                None,
                "passive",
                False,
                (
                    SourcePinAlias(
                        "/10000000-0000-4000-8000-000000000001",
                        f"20000000-0000-4000-8000-{component:012d}",
                        f"30000000-0000-4000-8000-{component * 10 + index:012d}",
                    ),
                ),
            )
            for component, ref in enumerate(("C1", "R1"), 1)
            for index in (1, 2)
        )
        pin_map = ProjectPinNetMap(
            capture.digest,
            *("sha256:" + str(index) * 64 for index in range(1, 6)),
            NativePinNetMap("sha256:" + "6" * 64, inventory.inventory_digest, "10.0.5", nodes, 0),
        )
        monkeypatch.setattr(
            binding,
            "run_project_native_inventory",
            lambda *args, **kwargs: (inventory, pin_map),
        )
    settings = Settings(
        workspace=tmp_path, kicad_cli=Path(_CLI) if _CLI else None, kicad_timeout_seconds=60
    )
    return (
        capture,
        libraries,
        declaration_json,
        paths,
        bom,
        json.dumps(terminals).encode(),
        settings,
        sources,
    )


def _run(case):
    return binding.run_project_spice_model_binding(*case[:7])


def test_actual_captured_models_join_complete_references_and_pins(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    first, second = _run(case), _run(case)
    assert first == second and first.digest == second.digest
    assert tuple(item.reference for item in first.references) == ("C1", "R1")
    assert sum(len(item.pins) for item in first.references) == 4
    assert first.references[0].definition.source == b".subckt CAP A B\nC1 A B 100n\n.ends CAP\n"
    projection = first.document()
    assert projection["logical_pin_count"] == projection["source_pin_occurrence_count"] == 4
    assert (
        projection["source_settings_validation"] == projection["native_spice_export"] == "not_run"
    )
    assert projection["simulation"] == projection["model_accuracy"] == "not_run"
    assert projection["apply_authority"] == "none"
    assert "C1" not in json.dumps(projection) and "CAP" not in repr(first)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.references = ()
    assert all((tmp_path / name).read_bytes() == payload for name, payload in case[-1].items())


def test_binding_consumes_one_joint_inventory_pair(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    producer = binding.run_project_native_inventory
    reconcile = binding._reconcile_bom_with_inventory
    produced = []
    consumed = []

    def counted_producer(*args, **kwargs):
        result = producer(*args, **kwargs)
        produced.append(result)
        return result

    def counted_reconcile(*args, **kwargs):
        consumed.append(args[6])
        return reconcile(*args, **kwargs)

    monkeypatch.setattr(binding, "run_project_native_inventory", counted_producer)
    monkeypatch.setattr(binding, "_reconcile_bom_with_inventory", counted_reconcile)
    report = _run(case)
    assert len(produced) == 1
    assert consumed == [produced[0][0]]
    assert report.pin_map_digest == produced[0][1].digest
    assert "run_project_pin_net_map" not in binding.__dict__


@pytest.mark.parametrize("location", ("source", "model"))
def test_source_or_model_change_during_final_hash_refuses_delivery(tmp_path, monkeypatch, location):
    case = _case(tmp_path, monkeypatch)
    original = binding.ProjectSpiceModelBinding._digest

    def changed(report, deadline):
        digest = original(report, deadline)
        target = case[0].root_path if location == "source" else "inputs/models.lib"
        (tmp_path / target).write_bytes(b"owned changed bytes")
        return digest

    monkeypatch.setattr(binding.ProjectSpiceModelBinding, "_digest", changed)
    with pytest.raises(binding.ProjectSpiceModelBindingError) as error:
        _run(case)
    assert error.value.__context__ is None
    assert "owned" not in str(error.value)


@pytest.mark.parametrize("deadline", (True, float("nan"), float("inf"), "later", 10**1000, 0))
def test_invalid_or_expired_controls_refuse_before_capture(tmp_path, monkeypatch, deadline):
    case = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        binding,
        "capture_electrical_artifacts",
        lambda *args, **kwargs: pytest.fail("must not capture"),
    )
    with pytest.raises(binding.ProjectSpiceModelBindingError) as error:
        binding.run_project_spice_model_binding(*case[:7], deadline=deadline)
    assert error.value.__context__ is None


@pytest.mark.parametrize("field", ("declaration_digest", "project_capture_digest"))
def test_document_identity_mismatch_refuses_before_native(tmp_path, monkeypatch, field):
    case = list(_case(tmp_path, monkeypatch))
    document = json.loads(case[5])
    document[field] = "sha256:" + "f" * 64
    case[5] = json.dumps(document).encode()
    monkeypatch.setattr(
        binding,
        "run_project_native_inventory",
        lambda *args, **kwargs: pytest.fail("must not execute"),
    )
    with pytest.raises(binding.ProjectSpiceModelBindingError):
        _run(case)


@pytest.mark.parametrize("kind", ("malformed", "stale-project", "unequal-items"))
def test_bom_binding_admission_precedes_joint_native_acquisition(tmp_path, monkeypatch, kind):
    case = list(_case(tmp_path, monkeypatch))
    if kind == "malformed":
        case[4] = b"{}"
    else:
        document = json.loads(case[4])
        if kind == "stale-project":
            document["project_capture_digest"] = "sha256:" + "f" * 64
        else:
            document["items"].pop()
        case[4] = json.dumps(document).encode()
    monkeypatch.setattr(
        binding,
        "run_project_native_inventory",
        lambda *_args, **_kwargs: pytest.fail("BOM admission must precede native acquisition"),
    )
    with pytest.raises(binding.ProjectSpiceModelBindingError) as error:
        _run(case)
    assert error.value.__cause__ is None and error.value.__context__ is None


def _two_alias_report(tmp_path, monkeypatch):
    report = _run(_case(tmp_path, monkeypatch))
    reference = report.references[0]
    pin = reference.pins[0]
    extra = dataclasses.replace(
        pin.node.aliases[0],
        source_symbol_uuid="20000000-0000-4000-8000-000000000099",
        source_pin_uuid="30000000-0000-4000-8000-000000000099",
    )
    node = dataclasses.replace(pin.node, aliases=(*pin.node.aliases, extra))
    reference = dataclasses.replace(
        reference, pins=(dataclasses.replace(pin, node=node), *reference.pins[1:])
    )
    return dataclasses.replace(report, references=(reference, *report.references[1:]))


def test_final_hash_stops_before_converting_next_alias_after_expiry(tmp_path, monkeypatch):
    report = _two_alias_report(tmp_path, monkeypatch)
    original = dataclasses._asdict_inner
    clock = [0.0]
    converted = []

    def watched(value, *args, **kwargs):
        if type(value) is SourcePinAlias:
            converted.append(value)
            result = original(value, *args, **kwargs)
            clock[0] = 2.0
            return result
        return original(value, *args, **kwargs)

    monkeypatch.setattr(dataclasses, "_asdict_inner", watched)
    monkeypatch.setattr(binding.time, "monotonic", lambda: clock[0])
    with pytest.raises(binding.ProjectSpiceModelBindingError):
        report._digest(1.0)
    assert converted == [report.references[0].pins[0].node.aliases[0]]


def test_incremental_alias_conversion_preserves_canonical_digest_bytes(tmp_path, monkeypatch):
    report = _two_alias_report(tmp_path, monkeypatch)
    values = [
        (
            report.project_capture_digest,
            report.declaration_digest,
            report.electrical_artifact_capture_digest,
            report.bom_report_digest,
            report.pin_map_digest,
            report.binding_document_digest,
        )
    ]
    for reference in report.references:
        definition = reference.definition
        values.append(
            (
                reference.model_id,
                reference.artifact_id,
                reference.reference,
                definition.name,
                definition.kind,
                definition.ports,
                definition.digest,
            )
        )
        values.extend((dataclasses.asdict(pin.node), pin.port) for pin in reference.pins)
    payload = b"copper-mcp/project-spice-model-binding/v1\x00" + b"".join(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode("ascii")
        + b"\x00"
        for value in values
    )
    assert report.digest == _sha(payload)


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 backend")
def test_real_native_complete_rc_terminal_join(tmp_path):
    case = _case(tmp_path)
    first, second = _run(case), _run(case)
    assert first == second and first.digest == second.digest
    assert {
        (item.reference, pin.node.pin_number) for item in first.references for pin in item.pins
    } == {("C1", "1"), ("C1", "2"), ("R1", "1"), ("R1", "2")}
    assert {pin.node.net_name for item in first.references for pin in item.pins} == {
        "AUDIO_IN",
        "AUDIO_OUT",
        "GND",
    }
    assert first.document()["simulation"] == "not_run"
    assert all((tmp_path / name).read_bytes() == payload for name, payload in case[-1].items())


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 backend")
def test_real_native_omitted_pin_cannot_produce_complete_binding(tmp_path):
    case = list(_case(tmp_path))
    document = json.loads(case[5])
    document["models"][0]["references"][0]["pins"].pop()
    case[5] = json.dumps(document).encode()
    with pytest.raises(binding.ProjectSpiceModelBindingError) as error:
        _run(case)
    assert error.value.__context__ is None


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 backend")
def test_real_native_shared_aliases_and_explicit_nc_reach_model_join(tmp_path):
    original, libraries = _alias_project(tmp_path, no_connect=True)
    sources = {item.path: item.content for item in original._files}
    marker = b'(property "Reference" "STALE-U1")'
    assert sources[original.root_path].count(marker) == 2
    sources[original.root_path] = sources[original.root_path].replace(
        marker, marker + b'(property "Value" "OwnedPart")'
    )
    for name, payload in sources.items():
        (tmp_path / name).write_bytes(payload)
    capture = capture_schematic_project(
        tmp_path,
        original.root_path,
        tuple(ProjectFileBinding(name, _sha(payload)) for name, payload in sorted(sources.items())),
    )
    model = b".model SHARED D(IS=1e-14)\n"
    artifacts = {
        "bom-main": ("inputs/bom.csv", _csv(("U1", "OwnedPart", "", 1, False))),
        "models-main": ("inputs/models.lib", model),
    }
    declaration = _declaration(artifacts, (("parts", "bom-main", 1),), model_item="parts")
    paths = _write_artifacts(tmp_path, artifacts)
    bom = _bindings(declaration, capture.digest, {"parts": ("U1",)})
    terminals = json.dumps(
        {
            "schema_version": "project-spice-terminal-bindings/v1",
            "declaration_digest": parse_electrical_inputs(declaration).digest,
            "project_capture_digest": capture.digest,
            "models": [
                {
                    "model_id": "checked-model",
                    "definition_name": "SHARED",
                    "definition_digest": _sha(model),
                    "references": [
                        {
                            "reference": "U1",
                            "pins": [
                                {"pin_number": "1", "binding": {"kind": "nc"}},
                                {"pin_number": "2", "binding": {"kind": "port", "port": "A"}},
                                {"pin_number": "3", "binding": {"kind": "port", "port": "K"}},
                            ],
                        }
                    ],
                }
            ],
        }
    ).encode()
    result = binding.run_project_spice_model_binding(
        capture,
        libraries,
        declaration,
        paths,
        bom,
        terminals,
        Settings(workspace=tmp_path, kicad_cli=Path(_CLI), kicad_timeout_seconds=60),
    )
    pins = result.references[0].pins
    assert [(pin.node.pin_number, pin.port) for pin in pins] == [
        ("1", None),
        ("2", "A"),
        ("3", "K"),
    ]
    assert len(pins[2].node.aliases) == 2
    assert result.document()["source_pin_occurrence_count"] == 4
    assert result.document()["no_connect_count"] == 1
    assert all((tmp_path / name).read_bytes() == payload for name, payload in sources.items())
