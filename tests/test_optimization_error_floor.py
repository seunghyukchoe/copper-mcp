"""Versioned strict-check policy reaches the real MCP/isolated consumer."""

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
from mcp import types
from pydantic import ValidationError
from test_optimization_inputs import launch as launch
from test_optimization_mcp import modern_wire_call

from copper_mcp.config import Settings
from copper_mcp.mcp_server import CopperMCPServer
from copper_mcp.optimization.contracts import OptimizationError
from copper_mcp.optimization.drc_profile import (
    NATIVE_ERROR_CHECKS,
    NativeErrorFloorBinding,
    prepare_drc_profile,
)
from copper_mcp.optimization.inputs import prepare_optimization
from copper_mcp.optimization.mcp import register_optimization_tools
from copper_mcp.optimization.repository import OptimizationJobRepository

POLICY = "kicad-10.0.5-editable-errors/v1"


@pytest.mark.real_kicad
@pytest.mark.skipif(
    not os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI"), reason="requires explicit native KiCad"
)
def test_native_error_floor_exposes_a_previously_suppressed_courtyard_hole(tmp_path):
    from copper_mcp.kicad_cli import _run_captured_drc

    fixture = Path(__file__).parent / "fixtures/board-ir-v0.2/footprint-pose-courtyard.kicad_pcb"
    source = (
        fixture.read_bytes().rstrip()[:-1]
        + b"""\n
      (footprint "Owned_NPTH" (layer "F.Cu")
        (uuid "83000000-0000-0000-0000-000000000011") (at 15 15)
        (pad "" np_thru_hole circle (at 0 0) (size 0.4 0.4) (drill 0.4)
          (layers "*.Cu" "*.Mask") (uuid "83000000-0000-0000-0000-000000000012")))\n)\n"""
    )
    original = source_project()
    context = {"board.kicad_pcb": source, "board.kicad_pro": original}
    settings = Settings(
        workspace=tmp_path, kicad_cli=Path(os.environ["COPPER_MCP_TEST_PROJECT_ERC_CLI"])
    )
    deadline = time.monotonic() + 60
    legacy, _ = prepare_drc_profile(context, "board.kicad_pcb", settings, deadline)
    strict, _ = prepare_drc_profile(
        context, "board.kicad_pcb", settings, deadline, error_floor=POLICY
    )
    before = _run_captured_drc(
        legacy, board_relative="board.kicad_pcb", settings=settings, deadline=deadline
    )
    after = _run_captured_drc(
        strict, board_relative="board.kicad_pcb", settings=settings, deadline=deadline
    )
    assert before.violation_type_counts.get("npth_inside_courtyard", 0) == 0
    assert after.violation_type_counts.get("npth_inside_courtyard", 0) > 0
    assert after.error_count > 0 and not after.passed
    assert context == {"board.kicad_pcb": source, "board.kicad_pro": original}


@pytest.mark.parametrize("value", [None, True, 1, [], "unexpected"])
def test_malformed_error_severity_is_not_silently_repaired(tmp_path, value):
    project = json.loads(source_project())
    project["board"]["design_settings"]["rule_severities"]["clearance"] = value
    with pytest.raises(OptimizationError):
        prepare_drc_profile(
            {"board.kicad_pcb": b"owned fixture", "board.kicad_pro": json.dumps(project).encode()},
            "board.kicad_pcb",
            Settings(workspace=tmp_path),
            time.monotonic() + 10,
            error_floor=POLICY,
        )


def test_v1_cannot_silently_select_the_new_policy(launch, tmp_path):
    with pytest.raises(OptimizationError):
        prepare_optimization({**launch, "drc_error_floor": POLICY}, Settings(workspace=tmp_path))


def source_project():
    return json.dumps(
        {
            "meta": {"version": 1},
            "board": {
                "design_settings": {
                    "rule_severities": {
                        "clearance": "warning",
                        "pth_inside_courtyard": "ignore",
                        "npth_inside_courtyard": "ignore",
                    },
                }
            },
        }
    ).encode()


def test_error_floor_is_explicit_source_preserving_and_self_binding(tmp_path):
    original = source_project()
    context = {"board.kicad_pcb": b"owned fixture", "board.kicad_pro": original}
    settings = Settings(workspace=tmp_path)
    legacy, old = prepare_drc_profile(context, "board.kicad_pcb", settings, time.monotonic() + 10)
    effective, strict = prepare_drc_profile(
        context, "board.kicad_pcb", settings, time.monotonic() + 10, error_floor=POLICY
    )
    assert context["board.kicad_pro"] == original
    assert (
        json.loads(legacy["board.kicad_pro"])["board"]["design_settings"]["rule_severities"][
            "pth_inside_courtyard"
        ]
        == "ignore"
    )
    assert "error_floor" not in old.model_dump()
    assert isinstance(strict, NativeErrorFloorBinding) and old.digest != strict.digest
    assert strict.original_project_digest == "sha256:" + hashlib.sha256(original).hexdigest()
    assert (
        strict.effective_project_digest
        == "sha256:" + hashlib.sha256(effective["board.kicad_pro"]).hexdigest()
    )
    assert strict.raised_error_checks == (
        "clearance",
        "npth_inside_courtyard",
        "pth_inside_courtyard",
    )
    assert all(dict(strict.enabled_inventory)[name] == "error" for name in NATIVE_ERROR_CHECKS)
    value = strict.model_dump(mode="json")
    value["enabled_inventory"] = [
        [name, "warning" if name == "clearance" else severity]
        for name, severity in value["enabled_inventory"]
    ]
    with pytest.raises(ValidationError):
        NativeErrorFloorBinding.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("case", ["pass", "hard-error", "downgraded-error", "wrong-version"])
def test_mcp_error_floor_runs_private_checks_and_preserves_original_project(tmp_path, launch, case):
    project = tmp_path / "board.kicad_pro"
    original = source_project()
    project.write_bytes(original)
    before = (project.read_bytes(), project.stat().st_ino, project.stat().st_mtime_ns)
    cli = tmp_path / "owned-drc-cli"
    version = "10.0.6" if case == "wrong-version" else "10.0.5"
    violation = (
        [
            {
                "type": "npth_inside_courtyard",
                "description": "owned control",
                "severity": "warning" if case == "downgraded-error" else "error",
                "excluded": False,
                "items": [],
            }
        ]
        if case in {"hard-error", "downgraded-error"}
        else []
    )
    cli.write_text(
        f"#!{sys.executable}\nimport json,pathlib,sys\n"
        "args=sys.argv\nassert args[1:3]==['pcb','drc']\n"
        "settings=json.loads(pathlib.Path(args[-1]).with_suffix('.kicad_pro').read_bytes())\n"
        "rules=settings['board']['design_settings']['rule_severities']\n"
        "assert all(rules[key]=='error' for key in "
        "('clearance','pth_inside_courtyard','npth_inside_courtyard'))\n"
        "report={'$schema':'https://schemas.kicad.org/drc.v1.json','source':pathlib.Path(args[-1]).name,"
        f"'date':'2026-09-14T12:00:00+09:00','coordinate_units':'mm','kicad_version':{version!r},"
        f"'violations':{violation!r},'unconnected_items':[],'schematic_parity':[],"
        "'included_severities':['error','warning','exclusion'],'ignored_checks':[]}\n"
        "pathlib.Path(args[args.index('--output')+1]).write_text(json.dumps(report))\n"
        f"sys.exit({5 if violation else 0})\n"
    )
    cli.chmod(0o700)
    settings = Settings(workspace=tmp_path, kicad_cli=cli, max_route_preview_seconds=120)
    server = CopperMCPServer(name="Owned error-floor control")
    gateway = register_optimization_tools(server, lambda: settings)

    async def call(name, request):
        result = await modern_wire_call(server, name, request, advertise_elicitation=False)
        assert isinstance(result, types.CallToolResult) and not result.is_error
        return result.structured_content

    async def exercise():
        started = await call(
            "start_optimization",
            {**launch, "schema_version": "optimization/v2", "drc_error_floor": POLICY},
        )
        job = started["record"]["job_id"]
        service, _owner = gateway.service()
        await asyncio.to_thread(service._jobs[job].future.result, timeout=120)
        status = await call("get_optimization_job", {"job_id": job})
        record = status["record"]
        if case != "pass":
            assert record["status"] != "awaiting_approval"
            assert record["failure_code"] == (
                "judge_failed"
                if case in {"hard-error", "downgraded-error"}
                else "required_domain_inconclusive"
            )
            assert service._jobs[job].source is None
            return
        assert record["status"] == "awaiting_approval", record["failure_code"]
        exported = await call(
            "export_optimization_package",
            {
                "job_id": job,
                "expected_record_revision": record["revision"],
                "expected_package_digest": record["package_digest"],
                "include_geometry": False,
            },
        )
        bound = exported["package"]["judge"]["drc_profile"]
        assert bound["error_floor"] == POLICY
        assert bound["original_project_digest"] == "sha256:" + hashlib.sha256(original).hexdigest()
        assert (
            exported["required_status"] == "pass" and exported["aggregate_status"] == "inconclusive"
        )
        assert exported["apply_authority"] == "none"
        with OptimizationJobRepository(service.repository.path) as reopened:
            persisted = reopened.get_package(job, _owner)
            assert isinstance(persisted.judge.drc_profile, NativeErrorFloorBinding)
            assert persisted.digest == exported["package_digest"]

    try:
        asyncio.run(exercise())
    finally:
        if gateway._service is not None:
            gateway._service.close()
            gateway._service.repository.close()
    assert before == (project.read_bytes(), project.stat().st_ino, project.stat().st_mtime_ns)
