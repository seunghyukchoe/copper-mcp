"""Opt-in real-router MCP workflow acceptance, not a format-only smoke.

Run serially with COPPER_MCP_TEST_EXTERNAL_WORKFLOW=1 and the production operator
router configuration. A recording shim forwards to the real Docker executable;
KiCad, conversion, routers, isolation and judging are never replaced. This owned
synthetic development board is not held-out evidence,
project ERC coverage, physical calibration, or proof of human consent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import anyio
import pytest
from mcp import types
from test_optimization_mcp import modern_wire_call

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.config import Settings
from copper_mcp.mcp_server import CopperMCPServer
from copper_mcp.optimization.mcp import register_optimization_tools
from copper_mcp.optimization.package import OptimizationPackageV2
from copper_mcp.request_boundary import net_class_constraints

_FIXTURE = Path(__file__).parent / "fixtures/route-candidate/layered-tree-ordinary-4layer.kicad_pcb"


def _recording_docker_cli(root: Path, docker: Path) -> tuple[Path, Path]:
    """Observe real executions without making the relay a routing/validation authority."""
    docker = docker.resolve(strict=True)
    binary_digest = hashlib.sha256(docker.read_bytes()).hexdigest()
    inode = docker.stat().st_ino
    executable = root / "record-docker"
    observations = root / "docker-invocations.jsonl"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import hashlib, json, os, subprocess, sys\nfrom pathlib import Path\n"
        f"docker = {str(docker)!r}\n"
        "def check_binary():\n"
        f"    if Path(docker).stat().st_ino != {inode} or "
        f"hashlib.sha256(Path(docker).read_bytes()).hexdigest() != {binary_digest!r}:\n"
        "        sys.exit(70)\n"
        "check_binary()\n"
        "args = sys.argv[1:]\n"
        "if len(args) < 3 or args[0] != '--host' or args[2] != 'run':\n"
        "    os.execv(docker, [docker, *args])\n"
        "payload = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)\n"
        "if len(payload) > 16 * 1024 * 1024:\n"
        "    sys.exit(64)\n"
        "result = subprocess.run([docker, *args], input=payload, check=False)\n"
        "check_binary()\n"
        "command = [('<container>' if i and args[i-1] == '--name' else value)\n"
        "           for i, value in enumerate(args) if i >= 2]\n"
        "record = {'container': args[args.index('--name') + 1], 'image': args[-1],\n"
        "          'input_digest': 'sha256:' + hashlib.sha256(payload).hexdigest(),\n"
        "          'argv_digest': 'sha256:' + "
        "hashlib.sha256('\\0'.join(args).encode()).hexdigest(),\n"
        "          'command_digest': 'sha256:' + "
        "hashlib.sha256('\\0'.join(command).encode()).hexdigest(),\n"
        f"          'docker_digest': 'sha256:{binary_digest}', 'docker_inode': {inode},\n"
        "          'exit_code': result.returncode}\n"
        f"fd = os.open({str(observations)!r}, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)\n"
        "try:\n"
        "    record_bytes = (json.dumps(record) + '\\n').encode()\n"
        "    if os.write(fd, record_bytes) != len(record_bytes): sys.exit(74)\n"
        "finally:\n"
        "    os.close(fd)\n"
        "if result.returncode < 0:\n"
        "    import signal\n"
        "    if -result.returncode not in (signal.SIGKILL, signal.SIGSTOP):\n"
        "        signal.signal(-result.returncode, signal.SIG_DFL)\n"
        "    os.kill(os.getpid(), -result.returncode)\n"
        "sys.exit(result.returncode)\n"
    )
    executable.chmod(0o700)
    return executable, observations


@pytest.mark.parametrize("exit_code", [7, -signal.SIGTERM])
def test_recording_docker_cli_preserves_input_output_and_failure(tmp_path, exit_code):
    """Unit check of instrumentation only; the executable here is NOT a native smoke."""
    target = tmp_path / "echo-cli"
    target.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
        "sys.stdout.buffer.flush()\n"
        "sys.stderr.write('diagnostic')\n"
        "sys.stderr.flush()\n"
        f"if {exit_code} < 0: os.kill(os.getpid(), {-exit_code})\n"
        f"sys.exit({exit_code})\n"
    )
    target.chmod(0o700)
    recorder, observations = _recording_docker_cli(tmp_path, target)
    payload = b"private synthetic input\x00\xff"
    result = subprocess.run(  # noqa: S603 - owned test shim and fixed synthetic executable
        [str(recorder), "--host", "unix:///unused.sock", "run", "--name", "owned", "image"],
        input=payload,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert (result.returncode, result.stdout, result.stderr) == (exit_code, payload, b"diagnostic")
    assert json.loads(observations.read_text()) == {
        "container": "owned",
        "image": "image",
        "input_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "argv_digest": "sha256:"
        + hashlib.sha256(b"--host\0unix:///unused.sock\0run\0--name\0owned\0image").hexdigest(),
        "command_digest": "sha256:"
        + hashlib.sha256(b"run\0--name\0<container>\0image").hexdigest(),
        "docker_digest": "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest(),
        "docker_inode": target.stat().st_ino,
        "exit_code": exit_code,
    }
    assert b"private synthetic" not in observations.read_bytes()
    before = observations.read_bytes()
    result = subprocess.run(  # noqa: S603 - same owned relay, non-routing passthrough control
        [str(recorder), "--host", "unix:///unused.sock", "info"],
        input=payload,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert (result.returncode, result.stdout, result.stderr) == (exit_code, payload, b"diagnostic")
    assert observations.read_bytes() == before


@pytest.mark.real_kicad
@pytest.mark.external_router
@pytest.mark.skipif(
    os.environ.get("COPPER_MCP_TEST_EXTERNAL_WORKFLOW") != "1",
    reason="requires explicit real-router workflow opt-in and production operator settings",
)
@pytest.mark.parametrize(
    "backend,hybrid",
    [
        ("freerouting-dsn-ses-v1", False),
        ("simpleroutejson-v1", False),
        ("simpleroutejson-v1", True),
    ],
    ids=["freerouting-dsn-ses-v1", "simpleroutejson-v1", "simpleroutejson-with-internal"],
)
def test_real_external_mcp_compares_placement_routes_every_target_and_replays(
    tmp_path, backend, hybrid
):
    source = _FIXTURE.read_text()
    # Native Specctra uses unique reference designators. Supply them on this owned
    # synthetic derivative without dropping the POWER net or its obstructing pads.
    for index, suffix in enumerate(("001", "003", "005", "007", "009"), start=1):
        identity = f'(uuid "70000000-0000-0000-0000-000000000{suffix}")'
        assert source.count(identity) == 1
        source = source.replace(
            identity,
            identity + f'\n    (property "Reference" "J{index}" (at 0 0 0) (layer "F.SilkS"))',
        )
    board = tmp_path / "board.kicad_pcb"
    board.write_text(source)
    project = board.with_suffix(".kicad_pro")
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
    converted = parse_kicad_bytes(
        board.read_bytes(),
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert converted.snapshot is not None and not converted.diagnostics
    snapshot = converted.snapshot
    assert {
        n.name: sum(p.net_id == n.id for p in snapshot.content.pads) for n in snapshot.content.nets
    } == {"TREE": 3, "POWER": 2}
    assert [
        (layer.name, layer.kind)
        for layer in sorted(snapshot.content.copper_layers, key=lambda layer: layer.index)
    ] == [(name, "signal") for name in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")]
    assert not snapshot.content.segments and not snapshot.content.vias and not snapshot.content.arcs
    movable = next(fp.id for fp in snapshot.content.footprints if fp.id.endswith("000000000005"))
    intent = tmp_path / "placement.json"
    intent.write_text(json.dumps({"proposals": [{"subject": movable, "offset_y_nm": -1_000_000}]}))
    templates = {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in (board, project, intent)
    }
    settings = replace(
        Settings.from_env(),
        workspace=tmp_path,
        transport="stdio",
        max_route_preview_seconds=120,
        allow_apply=False,
        allow_live_apply=False,
        allow_live_ipc=False,
        optimization_host_confirmation=False,
    )
    assert settings.optimization_docker_executable is not None
    assert settings.optimization_docker_executable.is_absolute()
    assert settings.optimization_docker_socket is not None
    docker_host = f"unix://{settings.optimization_docker_socket}"
    real_docker = settings.optimization_docker_executable.resolve(strict=True)
    real_docker_digest = "sha256:" + hashlib.sha256(real_docker.read_bytes()).hexdigest()
    real_docker_inode = real_docker.stat().st_ino
    recorder, observations = _recording_docker_cli(
        tmp_path, settings.optimization_docker_executable
    )
    shim_digest = "sha256:" + hashlib.sha256(recorder.read_bytes()).hexdigest()
    settings = replace(settings, optimization_docker_executable=recorder)
    image = (
        settings.optimization_freerouting_image
        if backend == "freerouting-dsn-ses-v1"
        else settings.optimization_simpleroutejson_image
    )
    assert image is not None
    launch = {
        "schema_version": "optimization/v2",
        "input_mode": "native-full-board",
        "board": board.name,
        "expect_board_revision": "sha256:" + hashlib.sha256(board.read_bytes()).hexdigest(),
        "allowed_backends": ["internal-layered-v1", backend] if hybrid else [backend],
        "constraints": constraints,
        "movable_footprint_refs": [movable],
        "placement_intent_path": intent.name,
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
    identities = []
    container_names: set[str] = set()
    for repetition in range(2):
        observation_count = (
            len(observations.read_text().splitlines()) if observations.exists() else 0
        )
        # Fresh workspaces force two executions instead of returning the durable
        # job already addressed by an identical request in the first repository.
        workspace = tmp_path / f"run-{repetition}"
        workspace.mkdir()
        for path, (content, _inode, _mtime) in templates.items():
            (workspace / path.name).write_bytes(content)
        before = {
            **templates,
            **{
                path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                for path in workspace.iterdir()
            },
        }
        settings = replace(settings, workspace=workspace)
        server = CopperMCPServer(name="Real external optimization workflow")
        gateway = register_optimization_tools(server, lambda settings=settings: settings)

        async def call(name, request, server=server):
            result = await modern_wire_call(server, name, request, advertise_elicitation=False)
            assert isinstance(result, types.CallToolResult) and not result.is_error
            assert isinstance(result.structured_content, dict)
            return result.structured_content

        async def exercise(gateway=gateway, call=call, observation_count=observation_count):
            with anyio.fail_after(180):
                started = await call("start_optimization", launch)
                job_id = started["record"]["job_id"]
                service, _owner = gateway.service()
                await asyncio.to_thread(service._jobs[job_id].future.result, timeout=150)
                status = await call("get_optimization_job", {"job_id": job_id})
                record = status["record"]
                assert record["status"] == "awaiting_approval", record.get("failure_code")
                assert status["apply_authority"] == "none"
                exported = await call(
                    "export_optimization_package",
                    {
                        "job_id": job_id,
                        "expected_record_revision": record["revision"],
                        "expected_package_digest": record["package_digest"],
                        "include_geometry": False,
                    },
                )
                assert exported["required_status"] == "pass"
                assert exported["aggregate_status"] == "inconclusive"
                assert exported["artifact_uri"] is None
                package = OptimizationPackageV2.model_validate_json(json.dumps(exported["package"]))
                assert package.digest == record["package_digest"]
                request = service._jobs[job_id].request
                assert request.input_mode == "native-full-board"
                assert package.native_import is not None
                assert (
                    package.native_import.original_board_revision == launch["expect_board_revision"]
                )
                assert package.native_import.backend_version == "10.0.5"
                assert package.native_import.repetitions == 2
                assert package.native_import.digest == request.native_import_digest
                outcomes = package.comparison.outcomes
                assert [row.role for row in outcomes] == ["identity", "alternative"]
                if not hybrid:
                    assert all(row.status == "reviewable" for row in outcomes)
                else:
                    assert outcomes[1].status == "reviewable"
                    assert package.comparison.improvement == "not_claimed"
                assert package.comparison.budget == package.comparison.allocation.per_slot(2)
                for row in outcomes:
                    if hybrid and row.status != "reviewable":
                        assert row.metrics is None and row.candidate_id is None
                        continue
                    assert row.metrics is not None
                    assert (
                        row.metrics.target_net_count == row.metrics.fully_connected_target_nets == 2
                    )
                    assert row.metrics.hard_drc_errors == row.metrics.hard_legality_errors == 0
                    assert row.metrics.via_count > 0
                    assert row.metrics.actual_route_probes > 0
                if outcomes[0].metrics is not None:
                    assert outcomes[0].metrics.displacement_nm == 0
                assert outcomes[1].metrics.displacement_nm > 0
                assert {row.backend for row in package.backend_provenance} == (
                    {"internal-layered-v1"} if hybrid else {backend}
                )
                assert all(
                    row.source_board_revision == launch["expect_board_revision"]
                    for row in package.backend_provenance
                )
                assert not gateway._artifacts
                actual_runs = [
                    json.loads(line)
                    for line in observations.read_text().splitlines()[observation_count:]
                ]
                assert len(actual_runs) >= 2, "each placement must invoke the real external router"
                assert all(run["image"] == image and run["exit_code"] == 0 for run in actual_runs)
                for run in actual_runs:
                    # Production and observation agreeing on a weakened command is not enough.
                    # This literal policy deliberately does not call the producer's argv helper.
                    expected_args = (
                        "--host",
                        docker_host,
                        "run",
                        "--name",
                        run["container"],
                        "--pull=never",
                        "--network=none",
                        "--read-only",
                        "--user=65532:65532",
                        "--cap-drop=ALL",
                        "--security-opt=no-new-privileges",
                        "--log-driver=none",
                        "--memory=4294967296",
                        "--memory-swap=4294967296",
                        "--cpus=1",
                        "--pids-limit=256",
                        "--tmpfs=/work:rw,noexec,nosuid,mode=0700,uid=65532,gid=65532,size=134217728",
                        "--interactive",
                        image,
                    )
                    assert run["argv_digest"] == (
                        "sha256:" + hashlib.sha256("\0".join(expected_args).encode()).hexdigest()
                    )
                    assert run["docker_digest"] == real_docker_digest
                    assert run["docker_inode"] == real_docker_inode
                names = {run["container"] for run in actual_runs}
                assert len(names) == len(actual_runs) and not names & container_names
                container_names.update(names)
                matched: set[str] = set()
                for row in outcomes:
                    assert row.external_runs, (
                        "each placement needs actual backend execution evidence"
                    )
                    assert row.charged.route_attempts >= len(row.external_runs)
                    for attempt in row.external_runs:
                        assert attempt.backend == backend and attempt.status == "success"
                        assert attempt.executable_digest == shim_digest
                        if backend == "simpleroutejson-v1" and not hybrid:
                            quantization = attempt.coordinate_quantization
                            assert quantization is not None
                            assert quantization.method == "nearest-nm-half-away-from-zero/v1"
                            assert (
                                0
                                < quantization.rounded_coordinate_count
                                <= quantization.coordinate_count
                            )
                        if hybrid:
                            assert attempt.normalized_route_digest is None
                            assert [step.backend for step in row.routing_attempts] == [
                                backend,
                                "internal-layered-v1",
                            ]
                            assert row.routing_attempts[0].outcome == "invalid_candidate"
                            assert row.routing_attempts[0].external_run_digest == attempt.digest
                            assert row.routing_attempts[1].outcome == (
                                "composed" if row.status == "reviewable" else row.status
                            )
                        else:
                            assert attempt.normalized_route_digest is not None
                            assert attempt.converter_digest is not None
                        assert attempt.output_bytes > 0 and attempt.exit_code == 0
                        witnesses = [
                            run
                            for run in actual_runs
                            if run["input_digest"] == attempt.input_digest
                            and run["command_digest"] == attempt.command_digest
                            and run["container"] not in matched
                        ]
                        assert len(witnesses) == 1, (
                            "slot evidence must match one real external invocation"
                        )
                        matched.add(witnesses[0]["container"])
                assert matched == names, (
                    "unaccounted external work cannot disappear from comparison"
                )
                identities.append((package.binding.digest, package.judge.digest))

        try:
            asyncio.run(exercise())
        finally:
            if gateway._service is not None:
                gateway._service.close()
                gateway._service.repository.close()
        assert before == {
            path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
            for path in before
        }
    assert identities[0] == identities[1], (
        "identical pinned inputs must replay candidate/judge identity"
    )
