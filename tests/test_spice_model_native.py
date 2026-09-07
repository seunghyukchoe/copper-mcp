"""Owned native model-terminal controls, not calibrated circuit or physics evidence."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import subprocess
import time
from pathlib import Path

import pytest
from test_project_erc import build_project

from copper_mcp.config import Settings
from copper_mcp.engineering import kicad_project_execution as execution
from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.project_erc_inputs import prepare_project_erc
from copper_mcp.engineering.project_pin_census import derive_project_pin_census
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    capture_schematic_project,
)
from copper_mcp.engineering.spice_model_library import parse_spice_model_library
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.security import read_workspace_file

_CLI = os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")
_MODEL = (
    b"* CopperMCP-owned terminal-order control, no device calibration\n"
    b".subckt ASYM_D A K\nD1 A K DMARK\n.ends ASYM_D\n"
    b".model DMARK D (IS=1e-14 N=1)\n"
)


def _sha(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 backend")
@pytest.mark.parametrize("swapped", (False, True))
@pytest.mark.parametrize("model_kind", ("diode", "subcircuit-ak", "subcircuit-ka"))
def test_native_model_export_preserves_explicit_terminal_order(tmp_path, swapped, model_kind):
    original, libraries, sources = build_project(tmp_path)
    mapping = "1=K 2=A" if swapped else "1=A 2=K"
    model_name = "DMARK" if model_kind == "diode" else "ASYM_D"
    model_bytes = (
        _MODEL.replace(b".subckt ASYM_D A K", b".subckt ASYM_D K A")
        if model_kind == "subcircuit-ka"
        else _MODEL
    )
    properties = (
        '(property "Sim.Library" "owned-model.lib" (at 0 0 0)'
        " (effects (font (size 1.27 1.27)) hide))"
        f'(property "Sim.Name" "{model_name}" (at 0 0 0)'
        " (effects (font (size 1.27 1.27)) hide))"
        f'(property "Sim.Pins" "{mapping}" (at 0 0 0)'
        " (effects (font (size 1.27 1.27)) hide))"
    ).encode()
    marker = b'(property "Reference" "R1"'
    assert sources[original.root_path].count(marker) == 1
    sources[original.root_path] = sources[original.root_path].replace(marker, properties + marker)
    for name, payload in sources.items():
        (tmp_path / name).write_bytes(payload)
    capture = capture_schematic_project(
        tmp_path,
        original.root_path,
        tuple(ProjectFileBinding(name, _sha(payload)) for name, payload in sorted(sources.items())),
    )
    deadline = time.monotonic() + 45
    interpreted = parse_spice_model_library(model_bytes, deadline=deadline)
    definition = next(item for item in interpreted.definitions if item.name == model_name)
    expected_ports = ("K", "A") if model_kind == "subcircuit-ka" else ("A", "K")
    assert definition.ports == expected_ports
    assert interpreted.digest == _sha(model_bytes)
    limits = CaptureLimits(max_capture_seconds=30)
    census = derive_project_pin_census(capture, libraries, limits=limits, deadline=deadline)
    assert tuple(pin.pin_number for pin in census.pins if pin.effective_reference == "R1") == (
        "1",
        "2",
    )
    prepared = prepare_project_erc(capture, libraries, limits=limits, deadline=deadline)
    # This test-only derivative adds one fixed owned library. It is not the production
    # model capture/binding operation and may never admit caller-supplied file additions.
    prepared = dataclasses.replace(
        prepared,
        files=tuple(sorted((*prepared.files, ("owned-model.lib", model_bytes)))),
        execution_digest=digest_document(
            "copper-mcp/owned-spice-export-control/v1",
            {"prepared_digest": prepared.execution_digest, "model_digest": _sha(model_bytes)},
        ),
    )
    settings = Settings(workspace=tmp_path, kicad_cli=Path(_CLI), kicad_timeout_seconds=45)
    observations = []
    with execution.open_project_execution_context(prepared, settings, deadline) as native:
        for index in range(2):
            output = native.temporary / f"model-{index}.cir"
            log = native.temporary / f"model-{index}.log"
            with log.open("wb") as stream:
                code = execution._invoke(
                    [
                        *native.base_command,
                        "1048576",
                        str(native.executable),
                        "sch",
                        "export",
                        "netlist",
                        "--format",
                        "spice",
                        "--output",
                        str(output),
                        str(native.snapshot / prepared.root_path),
                    ],
                    settings=settings,
                    environment=native.environment,
                    deadline=deadline,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
            native.verify()
            assert code == 0
            assert (
                read_workspace_file(
                    native.temporary, log.name, allowed_suffixes={".log"}, max_bytes=65536
                ).content
                == b""
            )
            content = read_workspace_file(
                native.temporary, output.name, allowed_suffixes={".cir"}, max_bytes=1048576
            ).content.decode("utf-8")
            element_name = "DR1" if model_kind == "diode" else "XR1"
            rows = [
                line.split() for line in content.splitlines() if line.startswith(element_name + " ")
            ]
            reverse = swapped != (model_kind == "subcircuit-ka")
            nodes = ["AUDIO_OUT", "AUDIO_IN"] if reverse else ["AUDIO_IN", "AUDIO_OUT"]
            assert rows == [[element_name, *nodes, model_name]]
            assert "owned-model.lib" in content
            observations.append(content)
    assert observations[0] == observations[1]
    execution._verify_workspace_source(tmp_path, tuple(sources.items()), deadline)
    assert census.document()["model_validation"] == "not_run"
    assert census.document()["apply_authority"] == "none"
