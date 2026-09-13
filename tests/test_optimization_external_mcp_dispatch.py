"""Real MCP/isolation/consumer execution with explicitly synthetic router and KiCad CLIs.

This proves software integration and refusal, not real-router execution, native DRC,
manufacturing quality or consent. Real backend acceptance is in external_native.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import types
from test_optimization_mcp import modern_wire_call
from test_optimization_workflow_v2 import _synthetic_cli

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.config import Settings
from copper_mcp.mcp_server import CopperMCPServer
from copper_mcp.optimization.mcp import register_optimization_tools
from copper_mcp.request_boundary import net_class_constraints

_FIXTURE = Path(__file__).parent / "fixtures/route-candidate/layered-tree-ordinary-4layer.kicad_pcb"
_IMAGE = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    "token,expected,rounded",
    [
        ("20.329728937950208", 20_329_729, True),
        ("-20.329728937950208", -20_329_729, True),
        ("20.329728", 20_329_728, False),
        ("0.0000005", 1, True),
        ("-0.0000005", -1, True),
    ],
)
def test_srg_coordinates_project_to_declared_nanometre_grid(token, expected, rounded):
    from copper_mcp.optimization.external_routing import _coordinate_nm, _NumberToken

    assert _coordinate_nm(_NumberToken(token)) == (expected, rounded)


def test_coordinate_projection_does_not_round_dimensions_or_accept_bad_numbers():
    from copper_mcp.optimization.external_routing import _coordinate_nm, _nm, _NumberToken
    from copper_mcp.optimization.worker import OptimizationExecutionError

    assert _nm(_NumberToken("0.25")) == 250_000
    with pytest.raises(OptimizationExecutionError) as refused:
        _nm(_NumberToken("0.2500000001"))
    assert refused.value.code == "unsupported_geometry"
    for token in ("NaN", "1e1000", "1" * 100):
        with pytest.raises(OptimizationExecutionError):
            _coordinate_nm(_NumberToken(token))
    with pytest.raises(OptimizationExecutionError):
        _coordinate_nm("20.329728937950208")


def test_coordinate_conversion_preserves_tiny_values_and_ignores_ambient_decimal_context():
    from decimal import Inexact, localcontext

    from copper_mcp.optimization.external_routing import _coordinate_nm, _nm, _NumberToken
    from copper_mcp.optimization.worker import OptimizationExecutionError

    with localcontext() as context:
        context.prec = 2
        context.Emax = 6
        context.Emin = -6
        context.traps[Inexact] = True
        assert _coordinate_nm(_NumberToken("100")) == (100_000_000, False)
        assert _nm(_NumberToken("10")) == 10_000_000
        assert _coordinate_nm(_NumberToken("1e-1000069")) == (0, True)
        with pytest.raises(OptimizationExecutionError) as refused:
            _nm(_NumberToken("1e-1000069"))
        assert refused.value.code == "unsupported_geometry"
        for value in ("1e999999999999999999999", "1e-999999999999999999999"):
            with pytest.raises(OptimizationExecutionError):
                _coordinate_nm(_NumberToken(value))


@pytest.mark.parametrize("only_via", [False, True], ids=["refuse-layer-jump", "via-only-route"])
def test_normalizer_checks_layer_transitions_after_projection(only_via):
    from types import SimpleNamespace

    from copper_mcp.optimization.external_routing import _normalize_srj
    from copper_mcp.optimization.worker import OptimizationExecutionError

    net_class = net_class_constraints(
        {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        }
    )
    profile = KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)
    snapshot = parse_kicad_bytes(_FIXTURE.read_bytes(), profile).snapshot
    assert snapshot is not None
    net = next(n.id for n in snapshot.content.nets if n.name == ("POWER" if only_via else "TREE"))
    route = [
        {"route_type": "wire", "x": x, "y": 15, "width": 0.25, "layer": layer}
        for x, layer in ((9, "top"), (10, "top"), (10.0000001, "bottom"), (11, "bottom"))
    ]
    if only_via:
        route = [
            {
                "route_type": "via",
                "x": 20,
                "y": 17.5,
                "from_layer": "top",
                "to_layer": "bottom",
                "via_diameter": 0.8,
                "via_hole_diameter": 0.4,
            }
        ]
    output = json.dumps(
        {
            "traces": [
                {
                    "type": "pcb_trace",
                    "pcb_trace_id": "owned",
                    "connection_name": net,
                    "route": route,
                }
            ]
        }
    ).encode()
    if only_via:
        segments, vias, quantization = _normalize_srj(
            SimpleNamespace(profile=profile), snapshot, b"{}", output, (net,)
        )
        assert not segments and len(vias) == 1
        assert quantization.coordinate_count == 2 and quantization.rounded_coordinate_count == 0
    else:
        with pytest.raises(OptimizationExecutionError) as refused:
            _normalize_srj(SimpleNamespace(profile=profile), snapshot, b"{}", output, (net,))
        assert refused.value.code == "invalid_candidate"


@pytest.mark.parametrize("used", [60, 100])
def test_external_runner_gets_only_remaining_output_allocation(tmp_path, used):
    from types import SimpleNamespace

    from copper_mcp.optimization.container_runner import EngineKind
    from copper_mcp.optimization.evaluation_v2 import SlotProbe
    from copper_mcp.optimization.external_routing import _router_runner
    from copper_mcp.optimization.lifecycle import ResourceUsage
    from copper_mcp.optimization.package import SlotBudget
    from copper_mcp.optimization.worker import OptimizationExecutionError

    slot = SlotProbe(
        SimpleNamespace(remaining_time_ms=lambda: 10000),
        SlotBudget(
            expansions=100,
            route_attempts=10,
            repair_rounds=1,
            routing_checks=100,
            validation_checks=100,
            wall_ms=10000,
            external_output_bytes=100,
        ),
    )
    slot.usage = ResourceUsage(external_output_bytes=used)
    settings = Settings(
        workspace=tmp_path,
        optimization_docker_executable=Path(sys.executable),
        optimization_docker_socket=tmp_path / "synthetic.sock",
        optimization_docker_config_root=tmp_path / "config",
        optimization_freerouting_image=_IMAGE,
        optimization_simpleroutejson_image=_IMAGE,
    )
    if used == 100:
        with pytest.raises(OptimizationExecutionError) as refused:
            _router_runner(settings, slot, EngineKind.SIMPLE_ROUTE_JSON)
        assert refused.value.code == "budget_exhausted"
    else:
        assert (
            _router_runner(settings, slot, EngineKind.SIMPLE_ROUTE_JSON)._limits.max_output_bytes
            == 40
        )


@pytest.mark.parametrize(
    "status", ["invalid_candidate", "judge_failed", "required_domain_inconclusive"]
)
def test_post_normalization_refusal_keeps_execution_evidence_without_candidate(status):
    from copper_mcp.optimization.package import (
        ComparisonOutcome,
        CoordinateQuantizationV2,
        ExternalRunV2,
        SlotWork,
    )

    run = ExternalRunV2(
        backend="simpleroutejson-v1",
        version="0.0.872",
        status="success",
        image_digest=_IMAGE,
        image_identity_kind="local_image_id",
        command_digest=_IMAGE,
        input_digest=_IMAGE,
        output_digest=_IMAGE,
        input_bytes=16,
        output_bytes=16,
        exit_code=0,
        executable_digest=_IMAGE,
        settings_digest=_IMAGE,
        converter_digest=_IMAGE,
        normalized_route_digest=_IMAGE,
        coordinate_quantization=CoordinateQuantizationV2(
            coordinate_count=2, rounded_coordinate_count=0
        ),
    )
    outcome = ComparisonOutcome(
        placement_candidate_id=_IMAGE,
        role="identity",
        status=status,
        candidate_id=None,
        metrics=None,
        route_probes=None,
        charged=SlotWork(route_attempts=1, external_output_bytes=32),
        external_runs=(run,),
    )
    assert outcome.candidate_id is None and outcome.metrics is None
    assert outcome.external_runs == (run,)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExternalRunV2.model_validate({**run.model_dump(), "coordinate_quantization": None})


def test_external_runtime_identity_binds_socket_and_ignores_unused_backend(tmp_path):
    from dataclasses import replace

    from copper_mcp.optimization.external_routing import external_router_settings_digest

    settings = Settings(
        workspace=tmp_path,
        optimization_docker_executable=Path(sys.executable),
        optimization_docker_socket=tmp_path / "one.sock",
        optimization_docker_config_root=tmp_path / "config",
        optimization_simpleroutejson_image=_IMAGE,
    )
    backends = ("simpleroutejson-v1",)
    original = external_router_settings_digest(settings, backends)
    assert (
        external_router_settings_digest(
            replace(
                settings,
                optimization_freerouting_image="unused-invalid-reference",
                optimization_specctra_python=Path(sys.executable),
            ),
            backends,
        )
        == original
    )
    assert (
        external_router_settings_digest(
            replace(
                settings,
                optimization_docker_socket=tmp_path / "two.sock",
            ),
            backends,
        )
        != original
    )


def _owned_router_cli(root: Path, malformed: bool) -> tuple[Path, Path]:
    cli = root / "synthetic-docker"
    witness = root / "synthetic-router-calls.txt"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "assert sys.argv[1] == '--host'\n"
        "args = sys.argv[3:]\n"
        "if args[0] in ('info', 'rm'): sys.exit(0)\n"
        "if args[:2] == ['image', 'inspect']:\n"
        "    print(args[-1]); sys.exit(0)\n"
        "assert args[0] == 'run' and '--network=none' in args\n"
        "raw = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)\n"
        "assert len(raw) <= 16 * 1024 * 1024\n"
        "problem = json.loads(raw)\n"
        "for obstacle in problem['obstacles']:\n"
        "    assert obstacle['zLayers'] == obstacle['__zLayers']\n"
        "    assert obstacle['zLayers'] == sorted(set(obstacle['zLayers']))\n"
        f"with open({str(witness)!r}, 'a') as stream: stream.write('run\\n')\n"
        "traces = []\n"
        "def wire(x, y, layer):\n"
        "    return dict(route_type='wire', x=x, y=y, layer=layer, "
        "width=problem['minTraceWidth'])\n"
        "def via(x, y, start, end):\n"
        "    return dict(route_type='via', x=x, y=y, from_layer=start, to_layer=end,\n"
        "        via_diameter=problem['min_via_pad_diameter'],\n"
        "        via_hole_diameter=problem['min_via_hole_diameter'])\n"
        "for connection in problem['connections']:\n"
        "    first, *rest = connection['pointsToConnect']\n"
        "    start = first.get('layer') or first['layers'][0]\n"
        "    for index, last in enumerate(rest, start=1):\n"
        "        end = last.get('layer') or last['layers'][0]\n"
        "        ax, ay = first['x'] + 3*index, first['y'] + 3*index\n"
        "        bx, by = last['x'] - 3*index, last['y'] + 3*index\n"
        "        route = [wire(first['x'], first['y'], start), wire(ax, ay, start),\n"
        "            via(ax, ay, start, 'inner1'), wire(ax, ay, 'inner1'),\n"
        "            wire(bx, by, 'inner1'), via(bx, by, 'inner1', end),\n"
        "            wire(bx, by, end), wire(last['x'], last['y'], end)]\n"
        "        traces.append(dict(type='pcb_trace', pcb_trace_id=f'owned-{len(traces)}',\n"
        "            connection_name=connection['name'], route=route))\n"
        f"problem['traces'] = [] if {malformed!r} else traces\n"
        "print(json.dumps(problem))\n"
    )
    cli.chmod(0o700)
    return cli, witness


def _owned_specctra_python(
    root: Path, *, disconnect_moved: bool = False, retained_power: bool = False
) -> tuple[Path, Path]:
    """Inject synthetic pcbnew, then execute the production Specctra worker unchanged."""

    python = root / "synthetic-kicad-python"
    witness = root / "synthetic-specctra-calls.txt"
    python.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, re, runpy, sys, types\n"
        "assert sys.argv[1] == '-I' and len(sys.argv) == 4\n"
        "worker, operation = pathlib.Path(sys.argv[2]), sys.argv[3]\n"
        "assert worker.name == 'specctra_worker.py' and operation in ('export', 'import')\n"
        f"with open({str(witness)!r}, 'a') as stream: stream.write(operation + '\\n')\n"
        "class Board:\n"
        "    def __init__(self, path):\n"
        "        self.source = pathlib.Path(path).read_text()\n"
        "    def GetTracks(self): return []\n"
        "def load(path):\n"
        "    assert pathlib.Path(path).name == 'board.kicad_pcb'\n"
        "    assert pathlib.Path('board.kicad_pro').is_file()\n"
        "    return Board(path)\n"
        "def export(board, output):\n"
        "    assert pathlib.Path(output) == pathlib.Path('board.dsn')\n"
        "    pathlib.Path(output).write_text('(pcb board.dsn\\n  (parser)\\n)\\n')\n"
        "    return True\n"
        "def imported(board, ses):\n"
        "    assert pathlib.Path(ses) == pathlib.Path('board.ses')\n"
        "    assert pathlib.Path(ses).read_text().lstrip().startswith('(session input')\n"
        "    return True\n"
        "def save(output, board):\n"
        "    assert pathlib.Path(output) == pathlib.Path('routed.kicad_pcb')\n"
        "    pose = re.search(r'\\(uuid \"70000000-0000-0000-0000-000000000005\"\\)'"
        " r'\\s+\\(at 30 (-?[0-9]+(?:\\.[0-9]+)?)(?: [0-9.-]+)?\\)', board.source)\n"
        "    assert pose is not None\n"
        "    y = pose.group(1)\n"
        f"    if {disconnect_moved!r} and y != '25': y = '25'\n"
        "    copper = f'''\n"
        '  (segment (start 10 15) (end 5 15) (width 0.25) (layer "F.Cu")\n'
        '    (net "TREE") (uuid "81000000-0000-0000-0000-000000000001"))\n'
        '  (segment (start 5 15) (end 5 5) (width 0.25) (layer "F.Cu")\n'
        '    (net "TREE") (uuid "81000000-0000-0000-0000-000000000002"))\n'
        '  (via (at 5 5) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu")\n'
        '    (net "TREE") (uuid "81000000-0000-0000-0000-000000000003"))\n'
        '  (segment (start 5 5) (end 35 5) (width 0.25) (layer "In1.Cu")\n'
        '    (net "TREE") (uuid "81000000-0000-0000-0000-000000000004"))\n'
        '  (via (at 35 5) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu")\n'
        '    (net "TREE") (uuid "81000000-0000-0000-0000-000000000005"))\n'
        '  (segment (start 35 5) (end 30 15) (width 0.25) (layer "B.Cu")\n'
        '    (net "TREE") (uuid "81000000-0000-0000-0000-000000000006"))\n'
        '  (segment (start 10 15) (end 10 {y}) (width 0.25) (layer "F.Cu")\n'
        '    (net "TREE") (uuid "81000000-0000-0000-0000-000000000007"))\n'
        '  (segment (start 10 {y}) (end 30 {y}) (width 0.25) (layer "F.Cu")\n'
        '    (net "TREE") (uuid "81000000-0000-0000-0000-000000000008"))\n'
        '  (via (at 20 17.5) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu")\n'
        '    (net "POWER") (uuid "81000000-0000-0000-0000-000000000009"))\n'
        "'''\n"
        f"    if {retained_power!r}:\n"
        "        copper = copper[:copper.index('  (via (at 20 17.5)')] + '''\n"
        '  (segment (start 20 17.5) (end 20 18) (width 0.25) (layer "F.Cu")\n'
        '    (net "POWER") (uuid "81000000-0000-0000-0000-000000000009"))\n'
        "'''\n"
        "    stripped = board.source.rstrip()\n"
        "    assert stripped.endswith(')')\n"
        "    pathlib.Path(output).write_text(stripped[:-1] + copper + ')\\n')\n"
        "    return True\n"
        "sys.modules['pcbnew'] = types.SimpleNamespace(\n"
        "    LoadBoard=load, ExportSpecctraDSN=export, "
        "ImportSpecctraSES=imported, SaveBoard=save)\n"
        "sys.argv = [str(worker), operation]\n"
        "runpy.run_path(str(worker), run_name='__main__')\n"
    )
    python.chmod(0o700)
    return python, witness


def _owned_freerouting_cli(root: Path, malformed: bool) -> tuple[Path, Path]:
    cli = root / "synthetic-freerouting-docker"
    witness = root / "synthetic-freerouting-calls.txt"
    valid = """(session input
  (base_design input)
  (was_is)
  (routes
    (resolution um 10)
    (parser)
    (library_out
      (padstack VIA
        (shape (circle F.Cu 0.8))))
    (network_out
      (net TREE
        (wire (path F.Cu 0.25 10 15 5 15))
        (wire (path In1.Cu 0.25 5 5 35 5))
        (wire (path B.Cu 0.25 35 5 30 15))
        (via VIA 5 5)
        (via VIA 35 5))
      (net POWER
        (via VIA 20 17.5)))))
"""
    malformed_output = "(session input (base_design input) (routes (parser)))\n"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        "assert sys.argv[1] == '--host'\n"
        "args = sys.argv[3:]\n"
        "if args[0] in ('info', 'rm'): sys.exit(0)\n"
        "if args[:2] == ['image', 'inspect']:\n"
        "    print(args[-1]); sys.exit(0)\n"
        "assert args[0] == 'run' and '--network=none' in args\n"
        "source = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)\n"
        "assert len(source) <= 16 * 1024 * 1024\n"
        "assert source.lstrip().startswith(b'(pcb board.dsn')\n"
        f"with open({str(witness)!r}, 'a') as stream: stream.write('run\\n')\n"
        f"sys.stdout.write({(malformed_output if malformed else valid)!r})\n"
    )
    cli.chmod(0o700)
    return cli, witness


@pytest.mark.parametrize(
    "malformed,hybrid,unavailable",
    [(False, False, False), (True, False, False), (True, True, False), (True, True, True)],
    ids=[
        "checked-package",
        "blocked-output",
        "explicit-internal-recovery",
        "unavailable-external-recovery",
    ],
)
def test_external_router_mcp_dispatch_compares_and_disposes_complete_candidate(
    tmp_path, malformed, hybrid, unavailable
):
    workspace = tmp_path / "project"
    workspace.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    board = workspace / "board.kicad_pcb"
    source = _FIXTURE.read_bytes()
    board.write_bytes(source)
    constraints = {
        "clearance_nm": 250_000,
        "track_width_nm": 250_000,
        "via_diameter_nm": 800_000,
        "via_drill_nm": 400_000,
    }
    net_class = net_class_constraints(constraints)
    parsed = parse_kicad_bytes(
        source, KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)
    )
    assert parsed.snapshot is not None and not parsed.diagnostics
    snapshot = parsed.snapshot
    assert len(snapshot.content.copper_layers) == 4
    assert {net.name for net in snapshot.content.nets} == {"TREE", "POWER"}
    assert not snapshot.content.segments and not snapshot.content.vias
    movable = next(fp.id for fp in snapshot.content.footprints if fp.id.endswith("000000000005"))
    (workspace / "placement.json").write_text(
        json.dumps({"proposals": [{"subject": movable, "offset_y_nm": -1_000_000}]})
    )
    cli, witness = _owned_router_cli(tools, malformed)
    settings = Settings(
        workspace=workspace,
        kicad_cli=_synthetic_cli(tools),
        max_route_preview_seconds=120,
        optimization_docker_executable=cli,
        optimization_docker_socket=tools / "synthetic.sock",
        optimization_docker_config_root=tools / "docker-config",
        optimization_simpleroutejson_image=_IMAGE,
    )
    if unavailable:
        settings = replace(settings, optimization_docker_executable=None)
    launch = {
        "schema_version": "optimization/v2",
        "board": board.name,
        "expect_board_revision": "sha256:" + hashlib.sha256(source).hexdigest(),
        "expect_snapshot_digest": snapshot.snapshot_digest,
        "constraints": constraints,
        "target_net_refs": sorted(net.id for net in snapshot.content.nets),
        "allowed_backends": ["internal-layered-v1", "simpleroutejson-v1"]
        if hybrid
        else ["simpleroutejson-v1"],
        "movable_footprint_refs": [movable],
        "placement_intent_path": "placement.json",
        "placement_grid_nm": 1_000_000,
        "limits": {
            "max_runtime_ms": 120_000,
            "max_candidates": 2,
            "max_placement_evaluations": 8,
            "max_route_attempts": 32,
            "max_repair_rounds": 0,
            "max_expansions": 100_000,
            "max_obstacle_checks": 1_000_000,
            "max_external_output_bytes": 8_388_608,
        },
    }
    before = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
    server = CopperMCPServer(name="Synthetic external workflow control")
    gateway = register_optimization_tools(server, lambda: settings)

    async def call(name, request):
        result = await modern_wire_call(server, name, request, advertise_elicitation=False)
        assert isinstance(result, types.CallToolResult) and not result.is_error
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    async def exercise():
        started = await call("start_optimization", launch)
        job_id = started["record"]["job_id"]
        service, _owner = gateway.service()
        await asyncio.to_thread(service._jobs[job_id].future.result, timeout=150)
        status = await call("get_optimization_job", {"job_id": job_id})
        record = status["record"]
        assert status["apply_authority"] == "none"
        blocked = malformed and not hybrid
        expected_status = "failed" if blocked else "awaiting_approval"
        assert record["status"] == expected_status, record.get("failure_code")
        if unavailable:
            assert not witness.exists()
        else:
            assert witness.read_text().splitlines() == ["run", "run"]
        exported = await call(
            "export_optimization_package",
            {
                "job_id": job_id,
                "expected_record_revision": record["revision"],
                "expected_package_digest": status["blocked_evaluation_digest"]
                if blocked
                else record["package_digest"],
                "include_geometry": False,
            },
        )
        package = exported["package"]
        if blocked:
            assert record["failure_code"] == "invalid_candidate"
            assert package["status"] == "blocked"
            assert package["evidence_scope"] == "terminal-metadata-only"
            assert "invalid_candidate" in package["blocking_codes"]
            assert service._jobs[job_id].source is None
        else:
            assert exported["required_status"] == "pass"
            assert exported["aggregate_status"] == "inconclusive"
            outcomes = package["comparison"]["outcomes"]
            assert [row["role"] for row in outcomes] == ["identity", "alternative"]
            assert [row["status"] for row in outcomes] == (
                ["backend_failure", "reviewable"] if hybrid else ["reviewable", "reviewable"]
            )
            for row in outcomes:
                if hybrid and row["status"] != "reviewable":
                    assert row["candidate_id"] is None and row["metrics"] is None
                    assert [
                        (attempt["backend"], attempt["outcome"])
                        for attempt in row["routing_attempts"]
                    ] == [
                        (
                            "simpleroutejson-v1",
                            "backend_failure" if unavailable else "invalid_candidate",
                        ),
                        ("internal-layered-v1", "backend_failure"),
                    ]
                    continue
                assert row["metrics"]["fully_connected_target_nets"] == 2
                assert row["metrics"]["via_count"] > 0
                assert row["metrics"]["actual_route_probes"] > 0
                if unavailable:
                    assert not row["external_runs"]
                else:
                    assert row["external_runs"][0]["backend"] == "simpleroutejson-v1"
                if hybrid:
                    if not unavailable:
                        assert row["external_runs"][0]["normalized_route_digest"] is None
                    assert [
                        (attempt["backend"], attempt["outcome"])
                        for attempt in row["routing_attempts"]
                    ] == [
                        (
                            "simpleroutejson-v1",
                            "backend_failure" if unavailable else "invalid_candidate",
                        ),
                        ("internal-layered-v1", "composed"),
                    ]
                    assert row["charged"]["route_attempts"] >= (6 if unavailable else 7)
                else:
                    assert row["external_runs"][0]["normalized_route_digest"] is not None
                    quantization = row["external_runs"][0]["coordinate_quantization"]
                    assert quantization["method"] == "nearest-nm-half-away-from-zero/v1"
                    assert quantization["coordinate_count"] > 0
            if not hybrid:
                assert outcomes[0]["metrics"]["displacement_nm"] == 0
            else:
                assert package["comparison"]["improvement"] == "not_claimed"
                assert package["comparison"]["clearance_ranking"] == "omitted_unavailable"
            assert outcomes[1]["metrics"]["displacement_nm"] > 0
            assert service._jobs[job_id].source is not None
            if hybrid:
                assert {item["backend"] for item in package["backend_provenance"]} == {
                    "internal-layered-v1"
                }
        assert not gateway._artifacts

    try:
        asyncio.run(exercise())
    finally:
        if gateway._service is not None:
            gateway._service.close()
            gateway._service.repository.close()
    assert before == (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)


@pytest.mark.parametrize(
    "case", ["checked-package", "blocked-ses", "disconnected-alternative", "retained-power"]
)
def test_freerouting_mcp_dispatch_runs_specctra_stages_and_disposes_candidate(tmp_path, case):
    """Synthetic DSN/SES and pcbnew exercise production code, never native capability."""

    malformed = case == "blocked-ses"
    disconnect_moved = case == "disconnected-alternative"
    retained_power = case == "retained-power"
    workspace = tmp_path / "project"
    workspace.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    source = _FIXTURE.read_bytes()
    if retained_power:
        source = (
            source.rstrip()[:-1]
            + b"""\n
  (via (at 20 17.5) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu")
    (net "POWER") (uuid "81000000-0000-0000-0000-000000000010"))\n)\n"""
        )
    board = workspace / "board.kicad_pcb"
    board.write_bytes(source)
    project = workspace / "board.kicad_pro"
    project.write_text(
        json.dumps(
            {
                "meta": {"version": 1},
                "net_settings": {
                    "classes": [
                        {
                            "name": "Default",
                            "clearance": 0.25,
                            "track_width": 0.25,
                            "via_diameter": 0.8,
                            "via_drill": 0.4,
                        }
                    ]
                },
            }
        )
    )
    constraints = {
        "clearance_nm": 250_000,
        "track_width_nm": 250_000,
        "via_diameter_nm": 800_000,
        "via_drill_nm": 400_000,
    }
    net_class = net_class_constraints(constraints)
    parsed = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert parsed.snapshot is not None and not parsed.diagnostics
    snapshot = parsed.snapshot
    assert len(snapshot.content.copper_layers) == 4
    assert {net.name for net in snapshot.content.nets} == {"TREE", "POWER"}
    assert not snapshot.content.segments and len(snapshot.content.vias) == int(retained_power)
    movable = next(fp.id for fp in snapshot.content.footprints if fp.id.endswith("000000000005"))
    placement = workspace / "placement.json"
    placement.write_text(
        json.dumps({"proposals": [{"subject": movable, "offset_y_nm": -1_000_000}]})
    )
    docker, router_witness = _owned_freerouting_cli(tools, malformed)
    specctra_python, specctra_witness = _owned_specctra_python(
        tools, disconnect_moved=disconnect_moved, retained_power=retained_power
    )
    settings = Settings(
        workspace=workspace,
        kicad_cli=_synthetic_cli(tools),
        max_route_preview_seconds=120,
        optimization_docker_executable=docker,
        optimization_docker_socket=tools / "synthetic.sock",
        optimization_docker_config_root=tools / "docker-config",
        optimization_freerouting_image=_IMAGE,
        optimization_simpleroutejson_image=_IMAGE,
        optimization_specctra_python=specctra_python,
    )
    launch = {
        "schema_version": "optimization/v2",
        "board": board.name,
        "expect_board_revision": "sha256:" + hashlib.sha256(source).hexdigest(),
        "expect_snapshot_digest": snapshot.snapshot_digest,
        "constraints": constraints,
        "target_net_refs": sorted(net.id for net in snapshot.content.nets),
        "allowed_backends": ["freerouting-dsn-ses-v1"],
        "movable_footprint_refs": [movable],
        "placement_intent_path": placement.name,
        "placement_grid_nm": 1_000_000,
        "limits": {
            "max_runtime_ms": 120_000,
            "max_candidates": 2,
            "max_placement_evaluations": 8,
            "max_route_attempts": 32,
            "max_repair_rounds": 0,
            "max_expansions": 100_000,
            "max_obstacle_checks": 1_000_000,
            "max_external_output_bytes": 8_388_608,
        },
    }
    before = {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in (board, project, placement)
    }
    server = CopperMCPServer(name="Synthetic FreeRouting workflow control")
    gateway = register_optimization_tools(server, lambda: settings)

    async def call(name, request):
        result = await modern_wire_call(server, name, request, advertise_elicitation=False)
        assert isinstance(result, types.CallToolResult) and not result.is_error
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    async def exercise():
        started = await call("start_optimization", launch)
        job_id = started["record"]["job_id"]
        service, _owner = gateway.service()
        await asyncio.to_thread(service._jobs[job_id].future.result, timeout=150)
        status = await call("get_optimization_job", {"job_id": job_id})
        record = status["record"]
        assert status["apply_authority"] == "none"
        assert router_witness.read_text().splitlines() == ["run", "run"]
        expected_stages = (
            ["export", "export"]
            if malformed
            else [
                "export",
                "import",
                "export",
                "import",
            ]
        )
        assert specctra_witness.read_text().splitlines() == expected_stages
        expected_status = "failed" if malformed else "awaiting_approval"
        assert record["status"] == expected_status, record.get("failure_code")
        exported = await call(
            "export_optimization_package",
            {
                "job_id": job_id,
                "expected_record_revision": record["revision"],
                "expected_package_digest": status["blocked_evaluation_digest"]
                if malformed
                else record["package_digest"],
                "include_geometry": False,
            },
        )
        package = exported["package"]
        if malformed:
            assert record["failure_code"] == "invalid_candidate"
            assert package["status"] == "blocked"
            assert "invalid_candidate" in package["blocking_codes"]
            assert service._jobs[job_id].source is None
        else:
            assert exported["required_status"] == "pass"
            assert exported["aggregate_status"] == "inconclusive"
            outcomes = package["comparison"]["outcomes"]
            assert [row["role"] for row in outcomes] == ["identity", "alternative"]
            assert [row["status"] for row in outcomes] == (
                ["reviewable", "invalid_candidate"]
                if disconnect_moved
                else ["reviewable", "reviewable"]
            )
            for row in outcomes:
                if row["status"] == "reviewable":
                    assert row["metrics"]["fully_connected_target_nets"] == 2
                    assert row["metrics"]["via_count"] > 0
                    assert row["metrics"]["actual_route_probes"] > 0
                else:
                    assert row["metrics"] is None and row["candidate_id"] is None
                run = row["external_runs"][0]
                assert run["backend"] == "freerouting-dsn-ses-v1"
                assert run["normalized_route_digest"] is not None
                disposal = run["specctra_disposal"]
                assert disposal["proposal_scope"] == "whole-board"
                assert disposal["original_target_count"] == 2
                assert disposal["routing_target_count"] == (1 if retained_power else 2)
                assert disposal["retained_copper_count"] == int(retained_power)
                assert disposal["discarded_copper_count"] == int(retained_power)
                if retained_power:
                    assert row["metrics"]["via_count"] == 3
                    assert row["metrics"]["added_via_count"] == 2
            assert outcomes[0]["metrics"]["displacement_nm"] == 0
            if disconnect_moved:
                assert package["metrics"]["displacement_nm"] == 0
                assert package["alternate_candidate_ids"] == []
                assert package["comparison"]["clearance_ranking"] == "omitted_unavailable"
            else:
                assert outcomes[1]["metrics"]["displacement_nm"] > 0
            assert service._jobs[job_id].source is not None
            if retained_power:
                final = parse_kicad_bytes(
                    service._jobs[job_id].source,
                    KiCadConstraintProfile(
                        net_classes=(net_class,), default_net_class_id=net_class.id
                    ),
                )
                assert final.snapshot is not None
                power = next(net.id for net in snapshot.content.nets if net.name == "POWER")
                assert not any(item.net_id == power for item in final.snapshot.content.segments)
                assert (
                    tuple(item for item in final.snapshot.content.vias if item.net_id == power)
                    == snapshot.content.vias
                )
        assert not gateway._artifacts

    try:
        asyncio.run(exercise())
    finally:
        if gateway._service is not None:
            gateway._service.close()
            gateway._service.repository.close()
    assert before == {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) for path in before
    }
