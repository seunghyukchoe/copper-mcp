"""Protocol-to-isolated-worker controls using an owned synthetic CLI, never native KiCad."""

import asyncio
import copy
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_optimization_coordinator import run
from test_optimization_coordinator import synthetic_authority as synthetic_authority
from test_optimization_inputs import launch as launch

from copper_mcp.config import Settings
from copper_mcp.mcp_server import CopperMCPServer
from copper_mcp.optimization.inputs import parse_launch, prepare_optimization
from copper_mcp.optimization.mcp import register_optimization_tools
from copper_mcp.optimization.repository import OptimizationJobRepository


def _v2(launch):
    return {**launch, "schema_version": "optimization/v2"}


def _synthetic_cli(root: Path) -> Path:
    cli = root / "synthetic-kicad"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "args = sys.argv\n"
        "assert args[1:3] == ['pcb', 'drc']\n"
        "report = {'$schema':'https://schemas.kicad.org/drc.v1.json',"
        "'source':pathlib.Path(args[-1]).name,'date':'2026-09-08T12:00:00+09:00',"
        "'coordinate_units':'mm','kicad_version':'10.0.5','violations':[],"
        "'unconnected_items':[],'schematic_parity':[],"
        "'included_severities':['error','warning','exclusion'],'ignored_checks':[]}\n"
        "pathlib.Path(args[args.index('--output')+1]).write_text(json.dumps(report))\n"
    )
    cli.chmod(0o700)
    return cli


def test_v2_launch_defaults_and_v1_remains_unversioned(launch):
    v1 = parse_launch(launch)
    v2 = parse_launch({**launch, "schema_version": "optimization/v2"})
    assert "schema_version" not in v1.document()
    assert v1.limits.max_candidates == 8 and v1.limits.max_route_attempts == 24
    assert v2.limits.max_candidates == 4 and v2.limits.max_route_attempts == 256


@pytest.mark.parametrize("case", ["identity", "movement", "tree", "tree_movement"])
def test_mcp_v2_isolated_job_persists_and_exports_measured_composition(tmp_path, launch, case):
    if case == "movement":
        launch = _moving_launch(launch, tmp_path)
    elif case in {"tree", "tree_movement"}:
        import hashlib

        from test_layered_tree_router import FIXTURE

        from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
        from copper_mcp.request_boundary import net_class_constraints

        source = (FIXTURE.parent / "layered-tree-ordinary-4layer.kicad_pcb").read_bytes()
        (tmp_path / "board.kicad_pcb").write_bytes(source)
        constraints = {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        }
        net_class = net_class_constraints(constraints)
        snapshot = parse_kicad_bytes(
            source,
            KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
        ).snapshot
        assert snapshot is not None
        net = next(pad.net_id for pad in snapshot.content.pads if pad.net_id is not None)
        launch = {
            **launch,
            "expect_board_revision": "sha256:" + hashlib.sha256(source).hexdigest(),
            "expect_snapshot_digest": snapshot.snapshot_digest,
            "constraints": {
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            },
            "target_net_refs": [net],
            "routing_settings": {
                "grid_step_nm": 1_000_000,
                "max_grid_nodes": 250_000,
                "max_expansions": 100_000,
                "max_obstacles": 256,
                "max_obstacle_checks": 2_000_000,
            },
        }
        if case == "tree_movement":
            subject = next(
                fp.id for fp in snapshot.content.footprints if fp.id.endswith("000000000005")
            )
            (tmp_path / "placement.json").write_text(
                json.dumps({"proposals": [{"subject": subject, "offset_y_nm": -1_000_000}]})
            )
            launch.update(
                movable_footprint_refs=[subject],
                placement_intent_path="placement.json",
                placement_grid_nm=1_000_000,
                routing_settings={
                    "grid_step_nm": 1_000_000,
                    "max_grid_nodes": 10_000,
                    "max_expansions": 50_000,
                    "max_obstacles": 64,
                    "max_obstacle_checks": 100_000,
                },
                limits={
                    "max_runtime_ms": 120_000,
                    "max_candidates": 2,
                    "max_placement_evaluations": 8,
                    "max_route_attempts": 16,
                    "max_repair_rounds": 0,
                    "max_expansions": 100_000,
                    "max_obstacle_checks": 1_000_000,
                    "max_external_output_bytes": 2_097_152,
                },
            )
    launch = {**launch, "schema_version": "optimization/v2"}
    settings = Settings(
        workspace=tmp_path, kicad_cli=_synthetic_cli(tmp_path), max_route_preview_seconds=120
    )
    server = CopperMCPServer(name="V2 isolated workflow fixture")
    gateway = register_optimization_tools(server, lambda: settings)

    def call(name, request):
        result = asyncio.run(server.call_tool(name, {"request": request}))
        assert not result.is_error
        return result.structured_content

    before = (tmp_path / "board.kicad_pcb").read_bytes()
    try:
        started = call("start_optimization", launch)
        job_id = started["record"]["job_id"]
        service, owner = gateway.service()
        service._jobs[job_id].future.result(timeout=120)
        status = call("get_optimization_job", {"job_id": job_id})
        assert status["record"]["status"] == "awaiting_approval", status
        assert status["record"]["schema_version"] == "optimization/v2"
        exported = call(
            "export_optimization_package",
            {
                "job_id": job_id,
                "expected_record_revision": status["record"]["revision"],
                "expected_package_digest": status["record"]["package_digest"],
            },
        )
        package = exported["package"]
        assert package["schema_version"] == "optimization/v2"
        assert package["comparison"]["outcomes"][0]["role"] == "identity"
        assert package["comparison"]["outcomes"][0]["status"] == "reviewable"
        if case in {"movement", "tree_movement"}:
            assert len(package["comparison"]["outcomes"]) >= 2
            assert any(
                row["metrics"] is not None and row["metrics"]["displacement_nm"] > 0
                for row in package["comparison"]["outcomes"]
            )
        if case in {"tree", "tree_movement"}:
            assert package["metrics"]["fully_connected_target_nets"] == 1
            assert package["metrics"]["via_count"] >= 1
        if case == "tree_movement":
            assert package["metrics"]["displacement_nm"] > 0
            assert (
                package["metrics"]["copper_length_nm"]
                < package["comparison"]["outcomes"][0]["metrics"]["copper_length_nm"]
            )
        assert package["metrics"]["clearance"]["status"] in {"measured", "unavailable"}
        assert (
            package["metrics"]["clearance"]["snapshot_digest"]
            == package["binding"]["final_snapshot_digest"]
        )
        assert (
            package["metrics"]["clearance"]["constraint_digest"]
            == package["binding"]["constraint_digest"]
        )
        with OptimizationJobRepository(service.repository.path) as reopened:
            persisted = reopened.get_package(job_id, owner)
            assert persisted.digest == exported["package_digest"]
            assert persisted.model_dump(mode="json") == package
        assert exported["geometry_disclosure"] == "not_disclosed"
        assert exported["apply_authority"] == "none"
        assert before == (tmp_path / "board.kicad_pcb").read_bytes()
    finally:
        if gateway._service is not None:
            gateway._service.close()
            gateway._service.repository.close()


def test_v1_never_calls_clearance_and_preserves_zero(
    launch, tmp_path, synthetic_authority, monkeypatch
):
    from copper_mcp.optimization import clearance_score, evaluation_v2

    def forbidden(*args, **kwargs):
        pytest.fail("v1 invoked the v2 measurement")

    monkeypatch.setattr(clearance_score, "measure_composed_clearance", forbidden)
    monkeypatch.setattr(evaluation_v2, "measure_composed_clearance", forbidden)
    record, retained, package = run(launch, tmp_path)
    assert record.status == "awaiting_approval" and retained
    assert package.schema_version == "optimization/v1"
    assert package.metrics.clearance_margin_nm == 0


@pytest.mark.parametrize("required", [[], ["SI"], ["DRC"]])
def test_v2_required_domains_only_add_to_mandatory_profile(launch, tmp_path, required):
    prepared = prepare_optimization(
        {**_v2(launch), "required_domains": required}, Settings(workspace=tmp_path)
    )
    assert {"DRC", "DFM"}.issubset(prepared.request.required_domains)
    assert set(required).issubset(prepared.request.required_domains)


def test_unknown_required_domain_blocks_package_and_review(launch, tmp_path, synthetic_authority):
    record, retained, package = run({**_v2(launch), "required_domains": ["SI"]}, tmp_path)
    assert record.failure_code == "required_domain_inconclusive"
    assert not retained and package is None


@pytest.mark.parametrize("backend", ["freerouting-dsn-ses-v1", "simpleroutejson-v1"])
def test_v2_external_backend_refuses_before_internal_work(launch, tmp_path, monkeypatch, backend):
    from copper_mcp.optimization import evaluation_v2

    monkeypatch.setattr(
        evaluation_v2, "search_placements_v2", lambda *args: pytest.fail("internal placement ran")
    )
    record, retained, package = run({**_v2(launch), "allowed_backends": [backend]}, tmp_path)
    assert record.failure_code == "backend_failure"
    assert not retained and package is None


@pytest.mark.parametrize("margin", [-((1 << 53) - 1), -1, 0, (1 << 53) - 1])
def test_clearance_preserves_signed_safe_integers(margin):
    from copper_mcp.optimization.contracts import digest_document
    from copper_mcp.optimization.package import ClearanceObservation

    fields = {
        "status": "measured",
        "reason": None,
        "minimum_margin_nm": margin,
        "snapshot_digest": "sha256:" + "a" * 64,
        "constraint_digest": "sha256:" + "b" * 64,
        "method_version": "composed-ir-class-clearance/v1",
        "object_count": 2,
        "pair_checks": 1,
        "comparable_pairs": 1,
        "geometry_checks": 1,
    }
    fields["digest"] = digest_document("copper-mcp/composed-clearance/v1", fields)
    result = ClearanceObservation.model_validate(fields)
    assert result.minimum_margin_nm == margin
    for invalid in [-(1 << 53), 1 << 53, True, "0", 0.0]:
        with pytest.raises(ValidationError):
            ClearanceObservation.model_validate({**fields, "minimum_margin_nm": invalid})


@pytest.mark.parametrize("field", ["minimum_margin_nm", "snapshot_digest", "digest"])
def test_clearance_tamper_is_rejected_on_decode(launch, tmp_path, synthetic_authority, field):
    from copper_mcp.optimization.package import decode_package

    _, _, package = run(_v2(launch), tmp_path)
    fields = package.model_dump(mode="json")
    observed = fields["metrics"]["clearance"]
    observed[field] = (
        (observed[field] or 0) + 1 if field == "minimum_margin_nm" else "sha256:" + "f" * 64
    )
    fields["comparison"]["outcomes"][0]["metrics"]["clearance"] = copy.deepcopy(observed)
    with pytest.raises(ValidationError):
        decode_package(json.dumps(fields))


def test_v2_reload_rejects_changed_slot_allocation(launch, tmp_path, synthetic_authority):
    from copper_mcp.optimization.package import decode_package

    _, _, package = run(_v2(launch), tmp_path)
    fields = package.model_dump(mode="json")
    fields["comparison"]["budget"]["routing_checks"] += 1
    with pytest.raises(ValidationError):
        decode_package(json.dumps(fields))


def test_measurement_cancellation_preserves_terminal_root(
    launch, tmp_path, synthetic_authority, monkeypatch
):
    from copper_mcp.optimization import evaluation_v2

    original = evaluation_v2.measure_composed_clearance

    def cancelled(snapshot, probe, **kwargs):
        root = probe.root
        record = root.record
        root._repository.cancel(
            record.job_id, root._request, root._owner_binding, expected_revision=record.revision
        )
        return original(snapshot, probe, **kwargs)

    monkeypatch.setattr(evaluation_v2, "measure_composed_clearance", cancelled)
    record, retained, package = run(_v2(launch), tmp_path)
    assert record.status == "cancelled" and not retained and package is None


def _moving_launch(launch, tmp_path):
    prepared = prepare_optimization(launch, Settings(workspace=tmp_path))
    footprint = prepared.snapshot.content.footprints[0].id
    (tmp_path / "placement.json").write_text(
        json.dumps({"proposals": [{"subject": footprint, "offset_x_nm": 1_000_000}]})
    )
    return {
        **_v2(launch),
        "movable_footprint_refs": [footprint],
        "placement_intent_path": "placement.json",
        "placement_grid_nm": 1_000_000,
    }


def test_movement_retains_identity_and_equal_allocations(launch, tmp_path, synthetic_authority):
    record, retained, package = run(_moving_launch(launch, tmp_path), tmp_path)
    assert record.status == "awaiting_approval", record.failure_code
    assert retained and package is not None
    population = package.comparison
    assert 2 <= len(population.outcomes) <= 4
    assert population.outcomes[0].role == "identity"
    assert population.outcomes[0].metrics.displacement_nm == 0
    assert any(
        row.metrics is not None and row.metrics.displacement_nm > 0
        for row in population.outcomes[1:]
    )
    assert population.budget == population.allocation.per_slot(len(population.outcomes))
    assert population.budget.routing_checks == population.budget.validation_checks
    assert record.usage.route_attempts == sum(
        row.charged.route_attempts for row in population.outcomes
    )
    assert record.comparison == population


def test_failed_identity_work_remains_charged_without_baseline_metrics(
    launch, tmp_path, synthetic_authority, monkeypatch
):
    from copper_mcp.optimization import routing
    from copper_mcp.optimization.lifecycle import ResourceUsage
    from copper_mcp.optimization.worker import OptimizationExecutionError

    original = routing.route_targets
    calls = []

    def fail_identity(prepared, source, snapshot, settings, probe):
        calls.append((probe.budget, prepared.request.seed))
        if len(calls) == 1:
            probe.reserve(ResourceUsage(route_attempts=1, expansions=7, obstacle_checks=11))
            raise OptimizationExecutionError("backend_failure")
        return original(prepared, source, snapshot, settings, probe)

    monkeypatch.setattr(routing, "route_targets", fail_identity)
    record, retained, package = run(_moving_launch(launch, tmp_path), tmp_path)
    assert retained and package is not None, record.failure_code
    first = package.comparison.outcomes[0]
    assert first.status == "backend_failure" and first.metrics is None
    assert first.charged.expansions == 7 and first.charged.routing_checks == 11
    assert all(call == calls[0] for call in calls)
    assert record.usage.route_attempts == sum(
        row.charged.route_attempts for row in package.comparison.outcomes
    )
    assert package.comparison.clearance_ranking == "omitted_unavailable"
    assert package.comparison.improvement == "not_claimed"


def test_one_unavailable_measurement_omits_clearance_for_population(
    launch, tmp_path, synthetic_authority, monkeypatch
):
    from copper_mcp.optimization import evaluation_v2
    from copper_mcp.optimization.contracts import digest_document

    original = evaluation_v2.measure_composed_clearance
    count = 0

    def measure(*args, **kwargs):
        nonlocal count
        count += 1
        observed = original(*args, **kwargs)
        if count == 1:
            observed = replace(
                observed,
                status="unavailable",
                reason="unsupported_rules",
                minimum_margin_nm=None,
                comparable_pairs=0,
            )
            fields = asdict(observed)
            del fields["digest"]
            observed = replace(
                observed, digest=digest_document("copper-mcp/composed-clearance/v1", fields)
            )
        return observed

    monkeypatch.setattr(evaluation_v2, "measure_composed_clearance", measure)
    record, _, package = run(_moving_launch(launch, tmp_path), tmp_path)
    assert package is not None, record.failure_code
    assert count > 1 and package.comparison.clearance_ranking == "omitted_unavailable"
    assert package.comparison.outcomes[0].metrics.clearance.minimum_margin_nm is None


def test_unused_and_failed_slot_allowances_cannot_fund_next_slot(launch, tmp_path):
    from copper_mcp.optimization.evaluation_v2 import SlotProbe, allocate_slots
    from copper_mcp.optimization.lifecycle import ResourceUsage
    from copper_mcp.optimization.worker import OptimizationExecutionError, execute_optimization_job

    prepared = prepare_optimization(_v2(launch), Settings(workspace=tmp_path))
    owner = "sha256:" + "a" * 64

    def execute(root):
        root.advance("placing")
        allocation, budget = allocate_slots(prepared, root, 2)
        assert budget.routing_checks == budget.validation_checks
        first, second = SlotProbe(root, budget), SlotProbe(root, budget)
        first.reserve(ResourceUsage(expansions=7, obstacle_checks=11, route_attempts=1))
        with pytest.raises(OptimizationExecutionError, match="budget_exhausted"):
            second.reserve(ResourceUsage(expansions=budget.expansions + 1))
        assert root.record.usage.expansions == 7
        second.reserve(ResourceUsage(expansions=budget.expansions))
        assert root.record.usage.expansions == 7 + budget.expansions
        assert allocation.per_slot(2) == first.budget == second.budget
        raise OptimizationExecutionError("backend_failure")

    with OptimizationJobRepository(tmp_path / "budget.sqlite3") as repository:
        queued = repository.create(prepared.request, owner)
        result = execute_optimization_job(
            repository, queued.job_id, prepared.request, owner, execute
        )
        assert result.failure_code == "backend_failure"
        assert result.usage.route_attempts == 1 and result.usage.obstacle_checks == 11


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_published_attempt_bounds_refuse_without_coercion(launch, version):
    value = launch if version == "v1" else _v2(launch)
    limit = 32 if version == "v1" else 4096
    fields = parse_launch(value).limits.model_dump()
    accepted = parse_launch({**value, "limits": {**fields, "max_route_attempts": limit}})
    assert accepted.limits.max_route_attempts == limit
    from copper_mcp.optimization.contracts import OptimizationError

    for bad in (limit + 1, True, float(limit), str(limit)):
        with pytest.raises(OptimizationError):
            parse_launch({**value, "limits": {**fields, "max_route_attempts": bad}})
