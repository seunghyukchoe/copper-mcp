"""Native-import protocol controls use an owned synthetic CLI, never installed KiCad."""

import asyncio
import hashlib
import sys
import time

import pytest
from test_optimization_identity_v2 import connected_launch as connected_launch
from test_optimization_inputs import launch as launch

from copper_mcp.config import Settings
from copper_mcp.mcp_server import CopperMCPServer
from copper_mcp.optimization.contracts import OptimizationError
from copper_mcp.optimization.inputs import parse_launch, prepare_optimization
from copper_mcp.optimization.mcp import register_optimization_tools


def synthetic_import_cli(tmp_path):
    cli = tmp_path / "synthetic-import-cli"
    cli.write_text(
        f"#!{sys.executable}\n"
        + """import json,pathlib,re,sys,uuid
args=sys.argv[1:]
if args==['--version']:
 print('10.0.5')
elif args[:2]==['pcb','upgrade']:
 p=pathlib.Path(args[2]); text=p.read_text()
 text=text.replace('(version 20240108)','(version 20260206)')
 def fields(match):
  return match[0]+''.join(
   '(property "'+name+'" "" (at 0 0) (layer "F.Fab")'
   +' (uuid "'+str(uuid.uuid4())+'"))' for name in ('Datasheet','Description'))
 pattern=r'(\\(footprint "[^"\\n]+"\\s+\\(layer "[^"\\n]+"\\)\\s+'
 pattern+=r'\\(uuid "[0-9a-f-]+"\\))'
 text=re.sub(pattern,fields,text)
 p.write_text(text)
elif args[:2]==['pcb','drc']:
 report={'$schema':'https://schemas.kicad.org/drc.v1.json',
  'source':pathlib.Path(args[-1]).name,'date':'2026-09-09T00:00:00Z',
  'coordinate_units':'mm','kicad_version':'10.0.5','violations':[],
  'unconnected_items':[],'schematic_parity':[],
  'included_severities':['error','warning','exclusion'],'ignored_checks':[]}
 pathlib.Path(args[args.index('--output')+1]).write_text(json.dumps(report))
else:
 sys.exit(2)
"""
    )
    cli.chmod(0o700)
    return cli


@pytest.fixture
def imported_launch(launch, tmp_path, request):
    case = getattr(request, "param", "routing")
    if case == "identity":
        launch = request.getfixturevalue("connected_launch")
    elif case == "movement":
        from test_optimization_workflow_v2 import _moving_launch

        launch = _moving_launch(launch, tmp_path)
    board = tmp_path / launch["board"]
    board.write_bytes(board.read_bytes().replace(b"(version 20260206)", b"(version 20240108)"))
    value = {
        **launch,
        "schema_version": "optimization/v2",
        "input_mode": "native-full-board",
        "expect_board_revision": "sha256:" + hashlib.sha256(board.read_bytes()).hexdigest(),
    }
    del value["expect_snapshot_digest"]
    del value["target_net_refs"]
    cli = synthetic_import_cli(tmp_path)
    if case == "preferences":
        cli.write_text(
            cli.read_text().replace(
                "p.write_text(text)",
                "p.write_text(text)\n "
                "p.with_suffix('.kicad_prl').write_bytes(b'opaque preferences')",
            )
        )
    return value, Settings(workspace=tmp_path, kicad_cli=cli, max_route_preview_seconds=120)


@pytest.mark.parametrize(
    "imported_launch", ["routing", "identity", "movement", "preferences"], indirect=True
)
def test_native_full_board_runs_mcp_isolated_persistence_and_export(
    imported_launch, tmp_path, monkeypatch
):
    value, settings = imported_launch
    original = (tmp_path / value["board"]).read_bytes()
    server = CopperMCPServer(name="synthetic native-import flow")
    gateway = register_optimization_tools(server, lambda: settings)

    def call(name, request):
        return asyncio.run(server.call_tool(name, {"request": request})).structured_content

    try:
        record = call("start_optimization", value)["record"]
        service, owner = gateway.service()
        service._jobs[record["job_id"]].future.result(timeout=120)
        record = call("get_optimization_job", {"job_id": record["job_id"]})["record"]
        assert record["status"] == "awaiting_approval", (
            record["status"],
            record["failure_code"],
            record["usage"],
        )
        exported = call(
            "export_optimization_package",
            {
                "job_id": record["job_id"],
                "expected_record_revision": record["revision"],
                "expected_package_digest": record["package_digest"],
            },
        )
        package = exported["package"]
        assert package["native_import"]["original_board_revision"] == value["expect_board_revision"]
        assert package["binding"]["board_revision"] == value["expect_board_revision"]
        assert package["native_import"]["imported_board_revision"] != value["expect_board_revision"]
        assert (
            package["metrics"]["target_net_count"]
            == service._jobs[record["job_id"]].request.target_net_count
        )
        assert (
            package["metrics"]["fully_connected_target_nets"]
            == package["metrics"]["target_net_count"]
        )
        binding = service.repository.get_package(record["job_id"], owner).native_import
        assert binding.digest == service._jobs[record["job_id"]].request.native_import_digest
        assert package["binding"]["native_import_digest"] == binding.digest
        if tmp_path.joinpath("synthetic-import-cli").read_text().find("opaque preferences") >= 0:
            assert binding.discard_policy == "same-stem-private-kicad-prl/v1"
        assert record["usage"]["external_output_bytes"] > 4 * len(original)
        if package["metrics"]["target_net_count"] == 1:
            assert package["metrics"]["actual_route_probes"] == 0
            assert package["binding"]["candidate_board_revision"] == binding.imported_board_revision
            assert package["comparison"]["outcomes"][0]["role"] == "identity"
        if value.get("placement_intent_path"):
            assert len(package["comparison"]["outcomes"]) >= 2
            assert any(
                row["metrics"] is not None and row["metrics"]["displacement_nm"] > 0
                for row in package["comparison"]["outcomes"]
            )
        assert (
            service.repository.get_package(record["job_id"], owner).digest
            == exported["package_digest"]
        )
        assert (tmp_path / value["board"]).read_bytes() == original
        # Review must reproduce the same imported baseline before reaching the existing
        # disabled host authority. This does not confirm any human decision.
        from copper_mcp.optimization import service as service_module

        calls = []
        real_import = service_module.import_native_board

        def observed_import(*args, **kwargs):
            result = real_import(*args, **kwargs)
            calls.append(result.binding)
            return result

        monkeypatch.setattr(service_module, "import_native_board", observed_import)
        with pytest.raises(OptimizationError):
            service.approve_from_host(
                record["job_id"],
                owner,
                record["revision"],
                exported["package_digest"],
                service.repository.get_package(record["job_id"], owner).judge.digest,
            )
        assert calls == [binding]
        (tmp_path / value["board"]).write_bytes(original + b"\n")
        with pytest.raises(OptimizationError, match="source"):
            service.approve_from_host(
                record["job_id"],
                owner,
                record["revision"],
                exported["package_digest"],
                service.repository.get_package(record["job_id"], owner).judge.digest,
            )
        assert calls == [binding]
    finally:
        if gateway._service is not None:
            gateway._service.close()
            gateway._service.repository.close()


def test_import_mode_conflicts_and_observed_requirements(imported_launch, launch):
    value, _ = imported_launch
    assert parse_launch(value).input_mode == "native-full-board"
    for bad in (
        {**value, "target_net_refs": ["net:caller"]},
        {**value, "input_mode": "observed-snapshot"},
        {**value, "schema_version": "optimization/v1"},
        {**value, "kicad_cli": "caller"},
    ):
        with pytest.raises(OptimizationError):
            parse_launch(bad)
    assert "input_mode" not in parse_launch(launch).document()


def test_preparation_repeats_only_generated_field_ids_and_binds_derived_input(imported_launch):
    value, settings = imported_launch
    first = prepare_optimization(value, settings)
    second = prepare_optimization(value, settings)
    assert first.source == second.source
    assert first.request == second.request
    assert first.native_import.generated_field_count > 0
    assert first.native_import == second.native_import
    assert first.snapshot.content.source.revision == first.native_import.imported_board_revision
    assert first.context[first.board_path] != first.source
    assert first.request.board_revision == value["expect_board_revision"]
    with pytest.raises(OptimizationError):
        prepare_optimization({**value, "expect_snapshot_digest": "sha256:" + "f" * 64}, settings)


def test_raw_staleness_refuses_before_native_and_inherited_deadline_is_forwarded(
    imported_launch, monkeypatch
):
    from copper_mcp.optimization import inputs

    value, settings = imported_launch
    real_import = inputs.import_native_board
    calls = []

    def bounded(*args, **kwargs):
        calls.append(kwargs["deadline"])
        return real_import(*args, **kwargs)

    monkeypatch.setattr(inputs, "import_native_board", bounded)
    with pytest.raises(OptimizationError, match="stale"):
        prepare_optimization({**value, "expect_board_revision": "sha256:" + "f" * 64}, settings)
    assert not calls
    deadline = time.monotonic() + 30
    prepared = prepare_optimization(
        value, settings, deadline=deadline, started_at=time.monotonic() - 5
    )
    assert calls == [deadline]
    assert prepared.native_import is not None


def test_import_replay_failure_is_context_free_and_charges_observed_output(imported_launch):
    from copper_mcp.optimization.native_import import NativeImportError, import_native_board

    value, settings = imported_launch
    cli = settings.kicad_cli
    cli.write_text(
        cli.read_text().replace(
            "p.write_text(text)", "p.write_text(text.replace('copper-mcp',str(uuid.uuid4())))"
        )
    )
    charges = []
    source = (settings.workspace / value["board"]).read_bytes()
    with pytest.raises(NativeImportError) as caught:
        import_native_board(
            {value["board"]: source},
            value["board"],
            settings,
            deadline=time.monotonic() + 30,
            max_output_bytes=1_000_000,
            account_output=charges.append,
        )
    assert caught.value.__context__ is None
    assert len(charges) == 3 and sum(charges) > 2 * len(source)


def test_failed_upgrade_never_returns_partial_or_unbounded_output(imported_launch):
    from copper_mcp.optimization.native_import import NativeImportError, import_native_board

    value, settings = imported_launch
    source = (settings.workspace / value["board"]).read_bytes()
    with pytest.raises(NativeImportError) as caught:
        import_native_board(
            {value["board"]: source},
            value["board"],
            settings,
            deadline=time.monotonic() + 30,
            max_output_bytes=10,
        )
    assert caught.value.__context__ is None
    assert (settings.workspace / value["board"]).read_bytes() == source


def test_repeated_corrupt_native_pad_id_swap_refuses(imported_launch):
    from copper_mcp.optimization.native_import import NativeImportError

    value, settings = imported_launch
    cli = settings.kicad_cli
    first = "30000000-0000-0000-0000-000000000002"
    second = "30000000-0000-0000-0000-000000000004"
    cli.write_text(
        cli.read_text().replace(
            "p.write_text(text)",
            f"p.write_text(text.replace({first!r},'SWAP').replace({second!r},{first!r}).replace('SWAP',{second!r}))",
        )
    )
    original = (settings.workspace / value["board"]).read_bytes()
    assert first.encode() in original and second.encode() in original
    with pytest.raises(NativeImportError):
        prepare_optimization(value, settings)
    assert (settings.workspace / value["board"]).read_bytes() == original
