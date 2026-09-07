"""Owned end-to-end native preparation/export controls; no simulation authority."""

from __future__ import annotations

import os
import subprocess
import time

import pytest
from test_project_spice_model_binding import _case, _run

from copper_mcp.engineering import kicad_project_execution as execution
from copper_mcp.engineering.capture import CaptureLimits, capture_electrical_artifacts
from copper_mcp.engineering.project_spice_source import prepare_project_spice_source
from copper_mcp.engineering.spice_export import expected_spice_rows, parse_spice_export
from copper_mcp.security import read_workspace_file


@pytest.mark.real_kicad
@pytest.mark.skipif(
    not os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI"),
    reason="requires explicit sealed KiCad 10.0.5 backend",
)
def test_actual_binding_prepares_and_verifies_complete_native_spice_export(tmp_path):
    case = _case(tmp_path)
    binding = _run(case)
    capture, libraries, declaration, paths, _bom, _terminals, settings, sources = case
    deadline = time.monotonic() + 60
    limits = CaptureLimits(max_capture_seconds=30)
    artifacts = capture_electrical_artifacts(
        declaration, paths, tmp_path, limits=limits, deadline=deadline
    )
    prepared = prepare_project_spice_source(
        capture, libraries, binding, artifacts, paths, limits=limits, deadline=deadline
    )
    expected = expected_spice_rows(binding, deadline=deadline)
    assert expected == (("XC1", "AUDIO_OUT", "GND", "CAP"), ("XR1", "AUDIO_IN", "AUDIO_OUT", "RES"))
    observations, payloads = [], []
    with execution.open_project_execution_context(prepared.prepared, settings, deadline) as native:
        includes = tuple(
            (str(native.snapshot / path), path) for _artifact, path in prepared.model_paths
        )
        for index in range(2):
            output = native.temporary / f"spice-{index}.cir"
            log = native.temporary / f"spice-{index}.log"
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
                        str(native.snapshot / prepared.prepared.root_path),
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
            payload = read_workspace_file(
                native.temporary, output.name, allowed_suffixes={".cir"}, max_bytes=1048576
            ).content
            observations.append(
                parse_spice_export(
                    payload,
                    expected_rows=expected,
                    expected_includes=includes,
                    deadline=deadline,
                    max_bytes=1048576,
                )
            )
            payloads.append(payload)
    assert payloads[0] == payloads[1]
    assert observations[0] == observations[1]
    assert observations[0].digest(deadline=deadline) == observations[1].digest(deadline=deadline)
    assert (
        capture_electrical_artifacts(
            declaration, paths, tmp_path, limits=limits, deadline=deadline
        ).digest
        == artifacts.digest
    )
    execution._verify_workspace_source(tmp_path, tuple(sources.items()), deadline)
    assert all((tmp_path / name).read_bytes() == payload for name, payload in sources.items())
    assert binding.document()["simulation"] == "not_run"
