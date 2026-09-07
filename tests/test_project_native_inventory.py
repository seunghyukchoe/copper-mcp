"""Joint native inventory controls; raw XML is never returned as authority."""

from __future__ import annotations

import dataclasses
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_project_components import _inventory
from test_project_erc import build_project

from copper_mcp.config import Settings
from copper_mcp.engineering import project_components, project_pin_net_map
from copper_mcp.engineering import project_native_inventory as joint
from copper_mcp.engineering.native_pin_net_map import NativePinNetMap
from copper_mcp.optimization.contracts import digest_document


def _pin_mapping(virtual_pin_count: int = 0) -> NativePinNetMap:
    return NativePinNetMap(
        "sha256:" + "4" * 64,
        _inventory().digest,
        "10.0.5",
        (),
        virtual_pin_count,
    )


def _run(tmp_path, monkeypatch, mode="pass"):
    capture, libraries, sources = build_project(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    events = []
    calls = []
    component_parses = []
    pin_parses = []
    counts = {"census": 0, "prepare": 0}

    def verify():
        events.append("verify")
        if mode == "snapshot" and len(calls) == 2:
            raise ValueError("synthetic snapshot mutation")

    @contextmanager
    def context(prepared, settings, deadline):
        events.append("auth-before")
        try:
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
                verify=verify,
            )
        finally:
            events.append("auth-after")

    def invoke(command, *, stdout, stderr, **kwargs):
        calls.append(tuple(command))
        output = Path(command[-2])
        output.write_bytes(f'<export date="{len(calls)}"/>'.encode())
        if mode == "warning":
            stdout.write(b"owned warning\n")
        if mode == "source" and len(calls) == 2:
            (tmp_path / capture.root_path).write_bytes(b"owned changed source")
        return 7 if mode == "nonzero" else 0

    component = _inventory()
    mapping = _pin_mapping()

    def parse_component(payload, **kwargs):
        component_parses.append(payload)
        if mode == "component-divergence" and len(component_parses) == 2:
            return dataclasses.replace(component, components=())
        if mode == "backend" and len(component_parses) == 1:
            return dataclasses.replace(component, backend_version="10.0.4")
        return component

    def parse_pins(payload, **kwargs):
        pin_parses.append(payload)
        if mode == "pin-divergence" and len(pin_parses) == 2:
            return dataclasses.replace(mapping, virtual_pin_count=1)
        return mapping

    original_census = joint.derive_project_pin_census
    original_prepare = joint.prepare_project_erc
    original_source_check = joint.execution._verify_workspace_source

    def census(*args, **kwargs):
        counts["census"] += 1
        return original_census(*args, **kwargs)

    def prepare(*args, **kwargs):
        counts["prepare"] += 1
        return original_prepare(*args, **kwargs)

    def source_check(*args, **kwargs):
        events.append("source-check")
        return original_source_check(*args, **kwargs)

    monkeypatch.setattr(joint.execution, "open_project_execution_context", context)
    monkeypatch.setattr(joint.execution, "_invoke", invoke)
    monkeypatch.setattr(joint.execution, "_verify_workspace_source", source_check)
    monkeypatch.setattr(joint, "parse_component_netlist", parse_component)
    monkeypatch.setattr(joint, "parse_native_pin_net_map", parse_pins)
    monkeypatch.setattr(joint, "derive_project_pin_census", census)
    monkeypatch.setattr(joint, "prepare_project_erc", prepare)
    result = joint.run_project_native_inventory(capture, libraries, Settings(workspace=tmp_path))
    assert {name: (tmp_path / name).read_bytes() for name in sources} == sources
    return result, capture, events, calls, component_parses, pin_parses, counts


def test_one_context_two_exports_produce_both_exact_v1_reports(tmp_path, monkeypatch):
    (inventory, pin_map), capture, events, calls, component_parses, pin_parses, counts = _run(
        tmp_path, monkeypatch
    )
    assert events == [
        "source-check",
        "auth-before",
        "verify",
        "verify",
        "auth-after",
        "source-check",
    ]
    assert counts == {"census": 1, "prepare": 1}
    assert len(calls) == 2
    assert (
        component_parses
        == pin_parses
        == [
            b'<export date="1"/>',
            b'<export date="2"/>',
        ]
    )
    command_fields = {
        "executable_digest": "sha256:" + "1" * 64,
        "flags": ("sch", "export", "netlist", "--format", "kicadxml"),
        "input": capture.root_path,
        "output": "<private-netlist>",
        "repetitions": 2,
        "native_syntax_digest": "sha256:" + "3" * 64,
    }
    assert inventory.command_digest == digest_document(
        "copper-mcp/project-component-inventory-command/v1", command_fields
    )
    assert pin_map.command_digest == digest_document(
        "copper-mcp/project-pin-net-map-command/v1", command_fields
    )
    assert inventory.digest == inventory._digest(float("inf"))
    assert pin_map.digest == pin_map._digest(float("inf"))
    assert repr(inventory) == "<ProjectComponentInventory redacted>"
    assert repr(pin_map) == "<ProjectPinNetMap redacted>"


@pytest.mark.parametrize(
    "mode",
    (
        "warning",
        "nonzero",
        "snapshot",
        "source",
        "component-divergence",
        "pin-divergence",
        "backend",
    ),
)
def test_joint_acquisition_refuses_unverified_or_divergent_observations(
    tmp_path, monkeypatch, mode
):
    with pytest.raises(joint.ProjectNativeInventoryError) as error:
        _run(tmp_path, monkeypatch, mode)
    assert error.value.__cause__ is None and error.value.__context__ is None


def test_bad_controls_refuse_before_context(tmp_path, monkeypatch):
    capture, libraries, _ = build_project(tmp_path)
    monkeypatch.setattr(
        joint.execution,
        "open_project_execution_context",
        lambda *_args: pytest.fail("must not open native context"),
    )
    with pytest.raises(joint.ProjectNativeInventoryError):
        joint.run_project_native_inventory(
            capture,
            libraries,
            Settings(workspace=tmp_path),
            deadline=True,
        )


_CLI = os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 backend")
def test_real_joint_reports_and_digests_equal_legacy_acquisition(tmp_path):
    capture, libraries, sources = build_project(tmp_path)
    settings = Settings(workspace=tmp_path, kicad_cli=Path(_CLI), kicad_timeout_seconds=90)
    joint_inventory, joint_pin_map = joint.run_project_native_inventory(
        capture, libraries, settings
    )
    legacy_inventory = project_components.run_project_component_inventory(
        capture, libraries, settings
    )
    legacy_pin_map = project_pin_net_map.run_project_pin_net_map(capture, libraries, settings)
    assert joint_inventory == legacy_inventory
    assert joint_pin_map == legacy_pin_map
    assert joint_inventory.digest == legacy_inventory.digest
    assert joint_pin_map.digest == legacy_pin_map.digest
    assert {name: (tmp_path / name).read_bytes() for name in sources} == sources
