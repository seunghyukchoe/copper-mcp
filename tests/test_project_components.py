"""Bounded orchestration tests for private native component inventory evidence."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_project_erc import ROOT, build_project

from copper_mcp.adapters.kicad_schematic import render_kicad_schematic
from copper_mcp.adapters.sexpr import atoms, child, children, parse_sexpr
from copper_mcp.circuit_ir import decode_snapshot_json
from copper_mcp.config import Settings
from copper_mcp.engineering import component_netlist as netlist
from copper_mcp.engineering import project_components as components
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    capture_schematic_project,
)


def _inventory():
    return netlist.ComponentNetlist(
        (
            netlist.NativeComponent(
                "R1",
                "1k",
                "Resistor_SMD:R_0603_1608Metric",
                "",
                "Device:R",
                "/",
                ("00000000-0000-4000-8000-000000000001",),
                False,
                False,
                False,
            ),
        ),
        (
            "/",
            "/20000000-0000-4000-8000-000000000002/",
            "/20000000-0000-4000-8000-000000000003/",
        ),
        "10.0.5",
    )


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _recapture_mutated_fixture(tmp_path, capture, context, updated_root: bytes):
    """Bind changed captured source bytes while retaining the supplied library closure."""

    mutated = dict(context)
    assert updated_root != mutated[capture.root_path]
    mutated[capture.root_path] = updated_root
    (tmp_path / capture.root_path).write_bytes(updated_root)
    recaptured = capture_schematic_project(
        tmp_path,
        capture.root_path,
        tuple(ProjectFileBinding(path, _digest(payload)) for path, payload in mutated.items()),
    )
    return recaptured, mutated


def _run(tmp_path, monkeypatch, mode="pass"):
    capture, libraries, context = build_project(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    calls: list[list[str]] = []
    verifies: list[bool] = []

    def fake_verify():
        verifies.append(True)
        if (runtime / "input").exists():
            raise ValueError("synthetic snapshot mutation")

    @contextmanager
    def fake_context(prepared, settings, deadline):
        yield SimpleNamespace(
            temporary=runtime,
            snapshot=runtime / "input",
            environment={"TMPDIR": str(runtime)},
            executable=Path("/fake/kicad-cli"),
            executable_digest="sha256:" + "1" * 64,
            authentication_digest="sha256:" + "2" * 64,
            version="10.0.5",
            base_command=("python", "-I", "bounded.py"),
            native_syntax_digest="sha256:" + "3" * 64,
            verify=fake_verify,
        )

    def fake_invoke(command, *, stdout, stderr, **kwargs):
        calls.append(list(command))
        assert tuple(command[5:9]) == ("sch", "export", "netlist", "--format")
        assert command[9] == "kicadxml" and command[10] == "--output"
        output = Path(command[11])
        output.write_bytes(b"<export />")
        if mode == "warning":
            stdout.write(b"native warning\n")
        if mode == "changed-snapshot":
            (runtime / "input").mkdir(exist_ok=True)
        if mode == "source-drift" and len(calls) == 2:
            (tmp_path / "child.kicad_sch").write_bytes(b"changed live source")
        return 4 if mode == "nonzero" else 0

    parsed = _inventory()
    parsed_calls = []

    def fake_parse(payload, **kwargs):
        assert payload == b"<export />"
        assert kwargs["expected_source"] == str(runtime / "input" / capture.root_path)
        assert kwargs["expected_sheet_paths"] == parsed.sheet_paths
        parsed_calls.append((payload, kwargs))
        if mode == "divergent" and len(parsed_calls) == 2:
            return dataclasses.replace(parsed, backend_version="10.0.4")
        return parsed

    monkeypatch.setattr(components.execution, "open_project_execution_context", fake_context)
    monkeypatch.setattr(components.execution, "_invoke", fake_invoke)
    monkeypatch.setattr(components, "parse_component_netlist", fake_parse)
    result = components.run_project_component_inventory(
        capture, libraries, Settings(workspace=tmp_path)
    )
    assert {name: (tmp_path / name).read_bytes() for name in context} == context
    return result, calls, verifies, parsed_calls


def test_fake_execution_is_not_native_evidence(tmp_path, monkeypatch):
    report, calls, verifies, parsed_calls = _run(tmp_path, monkeypatch)
    assert len(calls) == len(verifies) == len(parsed_calls) == 2
    assert report.document() == {
        "capture_digest": report.capture_digest,
        "execution_digest": report.execution_digest,
        "native_syntax_digest": report.native_syntax_digest,
        "command_digest": report.command_digest,
        "executable_digest": report.executable_digest,
        "backend_authentication_digest": report.backend_authentication_digest,
        "backend_version": "10.0.5",
        "component_count": 1,
        "sheet_count": 3,
        "native_inventory_digest": _inventory().digest,
        "model_validation": "not_run",
        "bom_validation": "not_run",
        "engineering_verdict": "not_run",
        "apply_authority": "none",
    }
    assert "R1" not in repr(report) and "R1" not in str(report.document())
    altered = dataclasses.replace(report, components=())
    assert altered.digest != report.digest
    assert (
        altered.document()["native_inventory_digest"]
        != report.document()["native_inventory_digest"]
    )


@pytest.mark.parametrize("mode", ("warning", "nonzero", "changed-snapshot", "source-drift"))
def test_unbound_execution_cannot_return_inventory(tmp_path, monkeypatch, mode):
    with pytest.raises(components.ProjectComponentInventoryError) as caught:
        _run(tmp_path, monkeypatch, mode)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_repeat_divergence_refuses(tmp_path, monkeypatch):
    with pytest.raises(components.ProjectComponentInventoryError):
        _run(tmp_path, monkeypatch, "divergent")


def test_bad_deadline_refuses_before_execution(tmp_path, monkeypatch):
    capture, libraries, _ = build_project(tmp_path)
    monkeypatch.setattr(components, "_execute", lambda *_args: pytest.fail("must not execute"))
    with pytest.raises(components.ProjectComponentInventoryError):
        components.run_project_component_inventory(
            capture, libraries, Settings(workspace=tmp_path), deadline=time.monotonic() - 1
        )


def test_final_inventory_conversion_stops_at_expiry(tmp_path, monkeypatch):
    original_inventory = _inventory()
    expanded = dataclasses.replace(
        original_inventory,
        components=tuple(
            dataclasses.replace(original_inventory.components[0], reference=f"R{index}")
            for index in range(3)
        ),
    )
    monkeypatch.setattr(f"{__name__}._inventory", lambda: expanded)
    original_clock = time.monotonic
    original_asdict = netlist.asdict
    converted = []

    def expire_after_record(record):
        converted.append(record)
        return original_asdict(record)

    monkeypatch.setattr(netlist, "asdict", expire_after_record)
    monkeypatch.setattr(time, "monotonic", lambda: original_clock() + (3601 if converted else 0))
    with pytest.raises(components.ProjectComponentInventoryError):
        _run(tmp_path, monkeypatch)
    assert len(converted) == 1


def test_final_inventory_encoding_stops_at_expiry(tmp_path, monkeypatch):
    original_clock = time.monotonic
    original_encode = json.JSONEncoder.iterencode
    expired = False

    def expire_after_token(encoder, document, *args, **kwargs):
        nonlocal expired
        is_inventory = (
            isinstance(document, dict)
            and "components" in document
            and "backend_version" in document
        )
        for token in original_encode(encoder, document, *args, **kwargs):
            if is_inventory:
                expired = True
            yield token
            if is_inventory:
                pytest.fail("encoding continued after the inventory deadline")

    monkeypatch.setattr(json.JSONEncoder, "iterencode", expire_after_token)
    monkeypatch.setattr(time, "monotonic", lambda: original_clock() + (3601 if expired else 0))
    with pytest.raises(components.ProjectComponentInventoryError):
        _run(tmp_path, monkeypatch)


_CONFIGURED_CLI = os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")


@pytest.mark.real_kicad
@pytest.mark.skipif(
    not _CONFIGURED_CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 test backend"
)
def test_real_native_component_inventory_is_repeatable_and_redacted(tmp_path):
    capture, libraries, context = build_project(tmp_path, nested=True)
    report = components.run_project_component_inventory(
        capture,
        libraries,
        Settings(workspace=tmp_path, kicad_cli=Path(_CONFIGURED_CLI), kicad_timeout_seconds=30),
    )
    assert report.components and report.sheet_paths
    assert report.document()["apply_authority"] == "none"
    assert all((tmp_path / name).read_bytes() == data for name, data in context.items())


@pytest.mark.real_kicad
@pytest.mark.skipif(
    not _CONFIGURED_CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 test backend"
)
def test_real_native_inventory_uses_instance_reference_not_stale_display_field(tmp_path):
    capture, libraries, context = build_project(tmp_path)
    stale = context[capture.root_path].replace(
        b'(property "Reference" "R1"', b'(property "Reference" "STALE_R99"', 1
    )
    capture, mutated = _recapture_mutated_fixture(tmp_path, capture, context, stale)

    report = components.run_project_component_inventory(
        capture,
        libraries,
        Settings(workspace=tmp_path, kicad_cli=Path(_CONFIGURED_CLI), kicad_timeout_seconds=30),
    )

    assert {component.reference for component in report.components} == {"R1", "C1"}
    assert all(component.reference != "STALE_R99" for component in report.components)
    assert all((tmp_path / name).read_bytes() == data for name, data in mutated.items())


@pytest.mark.real_kicad
@pytest.mark.skipif(
    not _CONFIGURED_CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 test backend"
)
def test_real_native_annotation_warning_for_duplicate_references_refuses(tmp_path):
    capture, libraries, context = build_project(tmp_path)
    duplicate = context[capture.root_path].replace(b'(reference "R1")', b'(reference "C1")', 1)
    capture, mutated = _recapture_mutated_fixture(tmp_path, capture, context, duplicate)

    with pytest.raises(components.ProjectComponentInventoryError) as caught:
        components.run_project_component_inventory(
            capture,
            libraries,
            Settings(workspace=tmp_path, kicad_cli=Path(_CONFIGURED_CLI), kicad_timeout_seconds=30),
        )

    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert all((tmp_path / name).read_bytes() == data for name, data in mutated.items())


@pytest.mark.real_kicad
@pytest.mark.skipif(
    not _CONFIGURED_CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 test backend"
)
def test_real_reused_child_symbols_have_distinct_instance_references(tmp_path):
    capture, libraries, context = build_project(tmp_path)
    intent = decode_snapshot_json(
        (ROOT / "benchmarks/audio/fixtures/rc-low-pass-intent-v1.json").read_bytes()
    )
    rendered = render_kicad_schematic(intent).content
    parsed = parse_sexpr(rendered)
    root_uuid = atoms(child(parsed, "uuid"))[0]
    text = rendered.decode().replace(
        f'(uuid "{root_uuid}")', '(uuid "10000000-0000-4000-8000-000000000001")', 1
    )
    for symbol in children(parsed, "symbol"):
        old_uuid = atoms(child(symbol, "uuid"))[0]
        new_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "component-child-" + old_uuid))
        text = text.replace(old_uuid, new_uuid)

    def instances(match):
        prefix = match.group(1)[0]
        return " ".join(
            f'(path "/{root_uuid}/20000000-0000-4000-8000-{number:012d}" '
            f'(reference "{prefix}{number}") (unit 1))'
            for number in (2, 3)
        )

    pattern = rf'\(path "/{root_uuid}"\s+\(reference "(R1|C1)"\)\s+\(unit 1\)\s*\)'
    text, replacements = re.subn(pattern, instances, text)
    assert replacements == 2
    context["child.kicad_sch"] = text.encode()
    (tmp_path / "child.kicad_sch").write_bytes(context["child.kicad_sch"])
    capture = capture_schematic_project(
        tmp_path,
        capture.root_path,
        tuple(ProjectFileBinding(path, _digest(data)) for path, data in context.items()),
    )
    report = components.run_project_component_inventory(
        capture,
        libraries,
        Settings(workspace=tmp_path, kicad_cli=Path(_CONFIGURED_CLI), kicad_timeout_seconds=60),
    )
    observed = {component.reference: component for component in report.components}
    assert set(observed) == {"R1", "C1", "R2", "C2", "R3", "C3"}
    for prefix in ("R", "C"):
        first, second = observed[prefix + "2"], observed[prefix + "3"]
        assert first.symbol_uuids == second.symbol_uuids
        assert first.sheet_path != second.sheet_path
    assert all((tmp_path / name).read_bytes() == data for name, data in context.items())
