"""Native-review regressions exercised with pure geometry and synthetic authority only."""

import json
import time
from dataclasses import replace

import pytest
from test_optimization_coordinator import run
from test_optimization_coordinator import synthetic_authority as synthetic_authority
from test_optimization_inputs import launch as launch
from test_optimization_workflow_v2 import _moving_launch, _v2

from copper_mcp import kicad_cli
from copper_mcp.adapters.kicad_route_patch import KiCadRoutePatchError
from copper_mcp.config import Settings
from copper_mcp.optimization import evaluation, evaluation_v2, routing
from copper_mcp.optimization.inputs import prepare_optimization
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.worker import OptimizationExecutionError

KEYS = (
    "footprint_filters_mismatch",
    "footprint_type_mismatch",
    "missing_courtyard",
    "track_not_centered_on_via",
    "tuning_profile_track_geometries",
)


@pytest.mark.parametrize(
    "existing",
    [
        None,
        {},
        {
            "missing_courtyard": "error",
            "track_not_centered_on_via": "warning",
            "footprint_type_mismatch": "ignore",
            "other_check": "ignore",
        },
    ],
)
def test_v2_private_profile_promotes_only_five_defaults(launch, tmp_path, existing):
    before = None
    if existing is not None:
        before = json.dumps(
            {
                "board": {
                    "design_settings": {"rule_severities": existing, "drc_exclusions": ["retained"]}
                },
                "text_variables": {"X": "kept"},
            }
        ).encode()
        (tmp_path / "board.kicad_pro").write_bytes(before)
    prepared = prepare_optimization(_v2(launch), Settings(workspace=tmp_path))
    context = evaluation.composition_context(
        prepared, prepared.source, Settings(workspace=tmp_path)
    )
    effective = json.loads(context["board.kicad_pro"])
    severities = effective["board"]["design_settings"]["rule_severities"]
    assert all(
        severities[key] == ("error" if existing and existing.get(key) == "error" else "warning")
        for key in KEYS
    )
    if before is not None:
        assert (tmp_path / "board.kicad_pro").read_bytes() == before
        assert effective["board"]["design_settings"]["drc_exclusions"] == ["retained"]
        assert effective["text_variables"] == {"X": "kept"}
        if "other_check" in existing:
            assert severities["other_check"] == "ignore"
    else:
        assert not (tmp_path / "board.kicad_pro").exists()
    assert prepared.context.get("board.kicad_pro") == before
    assert prepared.request.drc_profile_digest == prepared.drc_profile.digest


def test_v2_profile_is_bound_and_disclosed_through_package(launch, tmp_path, synthetic_authority):
    prepared = prepare_optimization(
        _v2(launch), Settings(workspace=tmp_path, max_route_preview_seconds=120)
    )
    record, _, package = run(_v2(launch), tmp_path)
    assert record.status == "awaiting_approval"
    assert package.binding.drc_profile_digest == prepared.request.drc_profile_digest
    assert package.judge.drc_profile.digest == prepared.request.drc_profile_digest
    assert {key for key, _ in package.judge.drc_profile.enabled_inventory} == set(KEYS)
    assert not (tmp_path / "board.kicad_pro").exists()


def test_project_freshness_receives_slot_deadline(launch, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from copper_mcp.optimization import project_evidence

    prepared = prepare_optimization(_v2(launch), Settings(workspace=tmp_path))
    # No project is parsed by this seam control; only the existing recapture boundary is spied.
    prepared = replace(prepared, project=object())
    deadline = time.monotonic() + 3
    seen = []

    def capture(declaration, settings, observed_deadline):
        seen.append(observed_deadline)
        return SimpleNamespace(digest=None), (), None

    monkeypatch.setattr(project_evidence, "capture_project_inputs", capture)
    evaluation.verify_original_context(prepared, Settings(workspace=tmp_path), deadline=deadline)
    assert seen == [deadline]


def test_circuit_intent_erc_uses_slot_allowance(launch, tmp_path, synthetic_authority, monkeypatch):
    from pathlib import Path

    from copper_mcp.models import ErcSummary

    fixture = (
        Path(__file__).resolve().parents[1] / "benchmarks/audio/fixtures/rc-low-pass-intent-v1.json"
    )
    (tmp_path / "electrical.json").write_bytes(fixture.read_bytes())
    value = {**_v2(launch), "electrical_intent_path": "electrical.json"}
    original = evaluation.judge_composition

    def judge(prepared, binding, source, settings, probe):
        # Exercise three seconds of *remaining judging time*, not host-dependent routing
        # speed. This only shortens the existing slot; it never renews an expired budget.
        probe.deadline = min(probe.deadline, time.monotonic() + 3)
        return original(prepared, binding, source, settings, probe)

    monkeypatch.setattr(evaluation, "judge_composition", judge)
    seen = []

    def erc(source, *, intent_digest, schematic_digest, settings):
        seen.append(settings.kicad_timeout_seconds)
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
    record, _, _ = run(value, tmp_path)
    assert seen and all(0 < seconds <= 3 for seconds in seen), (
        record.status,
        record.failure_code,
        record.usage,
    )
    assert record.status in {"awaiting_approval", "budget_exhausted"}


def test_reserve_search_counts_proposal_and_replay_per_branch(launch, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from copper_mcp.optimization.package import SlotBudget
    from copper_mcp.routing.contracts import AStarSettings

    charges = []
    root = SimpleNamespace(remaining_time_ms=lambda: 10_000, checkpoint=lambda: None)
    slot = evaluation_v2.SlotProbe(
        root,
        SlotBudget(
            expansions=100,
            route_attempts=10,
            repair_rounds=0,
            routing_checks=100,
            validation_checks=100,
            wall_ms=10_000,
        ),
    )
    monkeypatch.setattr(slot, "reserve", lambda charge: charges.append(charge))
    slot.reserve_search(AStarSettings(max_expansions=10, max_obstacle_checks=10), attempts=3)
    assert charges == [ResourceUsage(expansions=20, obstacle_checks=20, route_attempts=6)]


def test_failed_patch_is_retained_and_next_slot_runs(
    launch, tmp_path, synthetic_authority, monkeypatch
):
    original = routing.render_kicad_candidate_board
    calls = 0

    def render(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KiCadRoutePatchError("PRIVATE patch details")
        return original(*args, **kwargs)

    monkeypatch.setattr(routing, "render_kicad_candidate_board", render)
    record, retained, package = run(_moving_launch(launch, tmp_path), tmp_path)
    assert record.status == "awaiting_approval" and retained
    first = package.comparison.outcomes[0]
    assert first.status == "invalid_candidate" and first.metrics is None
    assert first.route_probes is None
    assert first.charged.route_attempts >= 2
    assert any(row.status == "reviewable" for row in package.comparison.outcomes[1:])


@pytest.mark.parametrize("suppression", ["ignored_check_count", "exclusion_count"])
def test_v2_profile_does_not_waive_other_suppressed_checks(
    launch, tmp_path, synthetic_authority, monkeypatch, suppression
):
    original = kicad_cli._run_captured_drc

    def suppressed(*args, **kwargs):
        summary = original(*args, **kwargs)
        if suppression == "exclusion_count":
            return replace(
                summary, exclusion_count=1, violation_type_counts={"courtyard_overlap": 1}
            )
        return replace(summary, ignored_check_count=1)

    monkeypatch.setattr(kicad_cli, "_run_captured_drc", suppressed)
    record, retained, package = run(_v2(launch), tmp_path)
    assert record.failure_code == "required_domain_inconclusive"
    assert not retained and package is None


def test_profile_changed_inventory_or_context_cannot_reload(launch, tmp_path, synthetic_authority):
    from pydantic import ValidationError

    from copper_mcp.optimization.package import decode_package

    record, _, package = run(_v2(launch), tmp_path)
    assert record.status == "awaiting_approval"
    for field in ("effective_context_digest", "enabled_inventory"):
        value = package.model_dump(mode="json")
        profile = value["judge"]["drc_profile"]
        if field == "enabled_inventory":
            profile[field][0][1] = "error"
        else:
            profile[field] = "sha256:" + "f" * 64
        with pytest.raises(ValidationError):
            decode_package(json.dumps(value))


@pytest.mark.parametrize("phase", ["source", "cancel"])
def test_patch_failure_cannot_hide_global_source_change_or_cancel(
    launch, tmp_path, synthetic_authority, monkeypatch, phase
):
    calls = 0

    def failure(prepared, source, snapshot, settings, slot):
        nonlocal calls
        calls += 1
        if phase == "source":
            (tmp_path / "board.kicad_pcb").write_bytes(source + b"\n")
        else:
            root = slot.root
            record = root.record
            root._repository.cancel(
                record.job_id, root._request, root._owner_binding, expected_revision=record.revision
            )
        raise KiCadRoutePatchError("PRIVATE source error")

    monkeypatch.setattr(routing, "route_targets", failure)
    record, retained, package = run(_moving_launch(launch, tmp_path), tmp_path)
    assert record.status == ("stale_revision" if phase == "source" else "cancelled")
    assert calls == 1 and not retained and package is None


def _three_pad_launch(launch, tmp_path):
    import hashlib

    from test_layered_tree_detour import _source_contact_case

    from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
    from copper_mcp.request_boundary import net_class_constraints

    source = _source_contact_case(("F", "F", "F"), (8, 12, 16))
    (tmp_path / "board.kicad_pcb").write_bytes(source)
    net_class = net_class_constraints(launch["constraints"])
    snapshot = parse_kicad_bytes(
        source, KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)
    ).snapshot
    assert snapshot is not None
    return {
        **_v2(launch),
        "expect_board_revision": "sha256:" + hashlib.sha256(source).hexdigest(),
        "expect_snapshot_digest": snapshot.snapshot_digest,
        "target_net_refs": [snapshot.content.pads[0].net_id],
    }


def test_common_layer_multipin_reserves_and_reports_each_branch_replay(
    launch, tmp_path, synthetic_authority
):
    record, _, package = run(_three_pad_launch(launch, tmp_path), tmp_path)
    assert record.status == "awaiting_approval", record.failure_code
    assert record.usage.route_attempts == 4
    assert package.metrics.actual_route_probes == 4
    assert package.comparison.outcomes[0].route_probes == 4
    assert (
        package.comparison.outcomes[0].charged.search_accounting
        == "conservative-proposal-replay-reservations/v1"
    )


def test_insufficient_branch_replay_reservation_refuses_before_proposal(
    launch, tmp_path, monkeypatch
):
    value = _three_pad_launch(launch, tmp_path)
    limits = prepare_optimization(value, Settings(workspace=tmp_path)).request.limits.model_dump()
    value["limits"] = {**limits, "max_route_attempts": 3}
    monkeypatch.setattr(
        routing.AStarRouter,
        "propose",
        lambda *args, **kwargs: pytest.fail("unreserved branch search ran"),
    )
    record, retained, package = run(value, tmp_path)
    assert record.status == "budget_exhausted"
    assert record.usage.route_attempts == 0
    assert record.comparison.outcomes[0].route_probes is None
    assert not retained and package is None


def test_profile_derivative_cannot_exceed_source_context_budget(launch, tmp_path):
    from copper_mcp.optimization.contracts import OptimizationError

    source = (tmp_path / "board.kicad_pcb").read_bytes()
    with pytest.raises(OptimizationError):
        prepare_optimization(
            _v2(launch), Settings(workspace=tmp_path, max_drc_context_bytes=len(source))
        )
    assert not (tmp_path / "board.kicad_pro").exists()


def test_profile_builder_expiry_remains_a_slot_budget_outcome(launch, tmp_path, monkeypatch):
    from types import SimpleNamespace

    prepared = prepare_optimization(_v2(launch), Settings(workspace=tmp_path))
    now = time.monotonic()
    deadline = now + 3
    clock = [now]

    def expired(*args, **kwargs):
        clock[0] = deadline + 1
        raise ValueError("PRIVATE profile failure")

    monkeypatch.setattr(evaluation, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(evaluation, "prepare_drc_profile", expired)
    with pytest.raises(OptimizationExecutionError) as error:
        evaluation.composition_context(
            prepared, prepared.source, Settings(workspace=tmp_path), deadline=deadline
        )
    assert error.value.code == "budget_exhausted"
    assert error.value.__context__ is None
