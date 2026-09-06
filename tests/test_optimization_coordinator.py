"""Real native geometry composition with explicitly synthetic KiCad authority observations."""

from pathlib import Path

import pytest
from test_optimization_inputs import launch as launch

from copper_mcp import kicad_cli
from copper_mcp.config import Settings
from copper_mcp.models import DrcSummary, ErcSummary
from copper_mcp.optimization.coordinator import coordinate_optimization
from copper_mcp.optimization.inputs import prepare_optimization
from copper_mcp.optimization.repository import OptimizationJobRepository
from copper_mcp.optimization.worker import execute_optimization_job

OWNER = "sha256:" + "8" * 64


@pytest.fixture
def synthetic_authority(tmp_path: Path, monkeypatch):
    from copper_mcp.optimization import service

    def inline_test_runner(repository, job_id, prepared, owner, settings, _launch, retain, observe):
        # Explicit test instrumentation only; production services use fresh isolated imports.
        return execute_optimization_job(
            repository,
            job_id,
            prepared.request,
            owner,
            lambda probe: coordinate_optimization(
                prepared, settings, probe, retain_private_result=retain, observe_judge=observe
            ),
            absolute_deadline_ms=int(prepared.started_at * 1000)
            + prepared.request.limits.max_runtime_ms,
        )

    monkeypatch.setattr(service, "run_isolated_job", inline_test_runner)
    executable = tmp_path / "synthetic-authority"
    executable.write_bytes(b"TEST FIXTURE: not an executable authority")
    monkeypatch.setattr(kicad_cli, "discover_kicad_cli", lambda _settings: executable)
    calls = []

    def observed(context, *, board_relative, settings, deadline=None):
        calls.append(context[board_relative])
        return DrcSummary(
            base_revision=kicad_cli._revision(context[board_relative]),
            drc_context_revision=kicad_cli._context_revision(context),
            kicad_version="10.0.5",
            drc_schema="https://schemas.kicad.org/drc.v1.json",
            coordinate_units="mm",
            error_count=0,
            warning_count=0,
            exclusion_count=0,
            ignored_check_count=0,
            unconnected_count=0,
            violation_type_counts={},
            passed=True,
        )

    monkeypatch.setattr(kicad_cli, "_run_captured_drc", observed)
    return calls


def run(launch, tmp_path):
    settings = Settings(workspace=tmp_path, max_route_preview_seconds=120)
    prepared = prepare_optimization(launch, settings)
    retained = []
    with OptimizationJobRepository(tmp_path / "jobs.sqlite3") as repository:
        record = repository.create(prepared.request, OWNER)
        result = execute_optimization_job(
            repository,
            record.job_id,
            prepared.request,
            OWNER,
            lambda probe: coordinate_optimization(
                prepared,
                settings,
                probe,
                retain_private_result=lambda package, source: retained.append((package, source)),
            ),
            absolute_deadline_ms=int(prepared.started_at * 1000)
            + prepared.request.limits.max_runtime_ms,
        )
        exported = (
            repository.get_package(result.job_id, OWNER)
            if result.status == "awaiting_approval"
            else None
        )
    return result, retained, exported


def test_final_judge_targets_composed_source_and_source_stays_unchanged(
    launch, tmp_path, synthetic_authority
):
    board = tmp_path / "board.kicad_pcb"
    before = board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns
    result, retained, package = run(launch, tmp_path)
    assert result.status == "awaiting_approval", result.failure_code
    assert len(retained) == 1
    assert package is not None
    assert package.digest == retained[0][0].digest == result.package_digest
    assert synthetic_authority == [retained[0][1]] * 2
    assert package.binding.candidate_board_revision == kicad_cli._revision(retained[0][1])
    assert package.binding.route_bundle_base_digest == package.binding.placed_snapshot_digest
    assert package.metrics.fully_connected_target_nets == 2
    assert package.judge.aggregate_status == "inconclusive"
    assert package.judge.required_status == "pass"
    assert package.document()["apply_authority"] == "none"
    assert before == (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)


def test_unavailable_authority_cannot_produce_reviewable_package(launch, tmp_path, monkeypatch):
    def unavailable(_settings):
        raise kicad_cli.KiCadCliError("unavailable test authority")

    monkeypatch.setattr(kicad_cli, "discover_kicad_cli", unavailable)
    result, retained, package = run(launch, tmp_path)
    assert result.failure_code == "required_domain_inconclusive"
    assert not retained and package is None


def test_requested_external_engine_is_not_silently_replaced(launch, tmp_path):
    launch["allowed_backends"] = ["freerouting-dsn-ses-v1", "internal-layered-v1"]
    result, retained, package = run(launch, tmp_path)
    assert result.failure_code == "backend_failure"
    assert not retained and package is None


def test_erc_binds_captured_electrical_intent_separately(
    launch, tmp_path, synthetic_authority, monkeypatch
):
    fixture = (
        Path(__file__).resolve().parents[1] / "benchmarks/audio/fixtures/rc-low-pass-intent-v1.json"
    )
    (tmp_path / "electrical.json").write_bytes(fixture.read_bytes())
    launch["electrical_intent_path"] = "electrical.json"
    calls = []

    def erc(schematic, *, intent_digest, schematic_digest, settings):
        calls.append((intent_digest, schematic_digest))
        assert schematic.startswith(b"(kicad_sch")
        return ErcSummary(
            intent_digest=intent_digest,
            schematic_digest=schematic_digest,
            kicad_version="10.0.5",
            erc_schema="https://schemas.kicad.org/erc.v1.json",
            coordinate_units="mm",
            error_count=0,
            warning_count=0,
            exclusion_count=0,
            ignored_check_count=0,
            sheet_count=1,
            violation_type_counts={},
            passed=True,
        )

    monkeypatch.setattr(kicad_cli, "run_circuit_schematic_erc", erc)
    result, _retained, package = run(launch, tmp_path)
    assert result.status == "awaiting_approval"
    assert package is not None and len(calls) == 2 and calls[0] == calls[1]
    assert package.judge.required_domains == ("DRC", "ERC", "DFM")
    assert package.judge.domains[1].status == "pass"
    assert package.judge.domains[1].evidence.input_digest == calls[0][0]
    assert calls[0][0] != package.binding.candidate_board_revision
    assert package.judge.aggregate_status == "inconclusive"
