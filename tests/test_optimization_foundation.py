"""Synthetic contract tests only: no router run, KiCad evidence, or engineering claim."""

from __future__ import annotations

import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from copper_mcp.optimization.approval import HumanApprovalAuthority
from copper_mcp.optimization.contracts import (
    DOMAINS,
    MAX_MESSAGE_BYTES,
    ClosedModel,
    OptimizationError,
    OptimizationRequest,
    PlacementScope,
    ResourceLimits,
    bounded_json,
    command_schema,
    parse_command,
)
from copper_mcp.optimization.judge import (
    DomainResult,
    EvidenceBinding,
    EvidenceSample,
    JudgeReport,
    unavailable_report,
)
from copper_mcp.optimization.lifecycle import (
    ResourceUsage,
    advance_job,
    approve_job,
    create_job,
    fail_job,
)
from copper_mcp.optimization.package import (
    BackendProvenance,
    CandidateBinding,
    ObjectiveMetrics,
    OptimizationPackage,
)
from copper_mcp.optimization.release_gate import (
    GATES,
    CorpusSummary,
    GateEvidence,
    ReleaseEvidence,
    release_blockers,
)

ROOT = Path(__file__).resolve().parents[1]
OWNER = "sha256:" + "8" * 64


def digest(character: str) -> str:
    return "sha256:" + character * 64


def replace(model: ClosedModel, **updates):
    return type(model).model_validate({**model.model_dump(), **updates})


def request() -> OptimizationRequest:
    return OptimizationRequest(
        schema_version="optimization/v1",
        board_revision=digest("a"),
        snapshot_digest=digest("b"),
        placement_scope=PlacementScope(
            movable_footprint_refs=("footprint:PRIVATE-REFERENCE-CANARY",),
            intent_digest=digest("c"),
            grid_nm=100_000,
            cardinal_rotations=(0, 90, 180, 270),
            preserve_existing_side=True,
        ),
        target_net_scope_digest=digest("0"),
        target_net_count=2,
        routing_profile_digest=digest("4"),
        judge_profile_digest=digest("5"),
        electrical_inputs_digest=None,
        required_domains=("DRC", "DFM"),
        allowed_backends=("internal-layered-v1",),
        seed=42,
        objective_weights=dict.fromkeys(
            (
                "congestion",
                "clearance_margin",
                "vias",
                "copper_length",
                "displacement",
                "intent_residual",
            ),
            1,
        ),
        limits=ResourceLimits(
            max_runtime_ms=10_000,
            max_candidates=4,
            max_placement_evaluations=20,
            max_route_attempts=4,
            max_repair_rounds=1,
            max_expansions=100,
            max_obstacle_checks=1000,
            max_external_output_bytes=1024,
        ),
        human_approval_required=True,
        policy_profile="deterministic-v1",
    )


def package(req: OptimizationRequest | None = None) -> OptimizationPackage:
    """Synthetic repeated observations exercise shape gates, not evidence authenticity."""
    req = req or request()
    binding = CandidateBinding(
        board_revision=req.board_revision,
        snapshot_digest=req.snapshot_digest,
        placement_candidate_id=digest("d"),
        placed_snapshot_digest=digest("e"),
        route_bundle_id=digest("f"),
        route_bundle_base_digest=digest("e"),
        candidate_board_revision=digest("1"),
        rule_context_digest=digest("2"),
    )
    authorities = {"DRC": "kicad-drc-v1", "DFM": "kicad-drc-dfm-v1", "ERC": "kicad-erc-v1"}
    domains = []
    for domain in DOMAINS:
        if domain in authorities and domain in req.required_domains:
            sample = EvidenceSample(verdict="pass", normalized_result_digest=digest("3"))
            evidence = EvidenceBinding(
                candidate_id=binding.digest,
                board_revision=req.board_revision,
                input_digest=(
                    req.electrical_inputs_digest
                    if domain == "ERC"
                    else binding.candidate_board_revision
                ),
                rule_context_digest=binding.rule_context_digest,
                settings_digest=req.judge_profile_digest,
                backend=authorities[domain],
                backend_version="10.0.5",
                executable_digest=digest("6"),
                command_digest=digest("7"),
                samples=(sample, sample),
                suppressed_check_count=0,
            )
            domains.append(
                DomainResult(domain=domain, status="pass", reason="verified", evidence=evidence)
            )
        else:
            domains.append(
                DomainResult(
                    domain=domain,
                    status="inconclusive",
                    evidence=None,
                    reason="insufficient_inputs" if domain == "ERC" else "backend_unavailable",
                )
            )
    return OptimizationPackage(
        schema_version="optimization/v1",
        request_digest=req.digest,
        binding=binding,
        alternate_candidate_ids=(),
        metrics=ObjectiveMetrics(
            hard_legality_errors=0,
            hard_drc_errors=0,
            target_net_count=2,
            fully_connected_target_nets=2,
            congestion_penalty=0,
            clearance_margin_nm=1000,
            via_count=1,
            copper_length_nm=3_000_000,
            displacement_nm=100_000,
            intent_residual=0,
            actual_route_probes=2,
        ),
        judge=JudgeReport(
            schema_version="optimization/v1",
            candidate_id=binding.digest,
            board_revision=req.board_revision,
            input_digest=binding.candidate_board_revision,
            rule_context_digest=binding.rule_context_digest,
            electrical_inputs_digest=req.electrical_inputs_digest,
            settings_digest=req.judge_profile_digest,
            required_domains=req.required_domains,
            domains=tuple(domains),
        ),
        backend_provenance=(
            BackendProvenance(
                backend="internal-layered-v1",
                version="1.0.0",
                executable_digest=digest("6"),
                command_digest=digest("7"),
                settings_digest=req.routing_profile_digest,
                input_digest=binding.placed_snapshot_digest,
                source_board_revision=binding.board_revision,
                placed_snapshot_digest=binding.placed_snapshot_digest,
                route_bundle_id=binding.route_bundle_id,
                normalized_output_digest=digest("9"),
            ),
        ),
    )


def step(job, req, state, **kwargs):
    return advance_job(
        job,
        req,
        expected_revision=job.revision,
        owner_binding=OWNER,
        next_status=state,
        observed_board_revision=req.board_revision,
        observed_snapshot_digest=req.snapshot_digest,
        **kwargs,
    )


def at_judging(req=None):
    req = req or request()
    job = create_job(req, owner_binding=OWNER)
    for state in ("inspecting", "placing", "routing", "judging"):
        job = step(job, req, state)
    return job


def awaiting(req=None):
    req = req or request()
    chosen = package(req)
    return step(at_judging(req), req, "awaiting_approval", package=chosen), chosen


def approve(job, req, chosen, token, authority, **kwargs):
    return approve_job(
        job,
        req,
        expected_revision=job.revision,
        owner_binding=OWNER,
        package=chosen,
        capability=token,
        authority=authority,
        observed_board_revision=req.board_revision,
        observed_snapshot_digest=kwargs.pop("observed_snapshot_digest", req.snapshot_digest),
        **kwargs,
    )


def test_closed_commands_roundtrip_and_request_identity():
    req = request()
    raw = json.dumps({"method": "start_optimization", "request": req.document()}).encode()
    parsed = parse_command(raw)
    assert parsed.request == req
    assert parsed.request.digest == req.digest
    assert req.digest != replace(req, seed=req.seed + 1).digest
    assert req.digest == request().digest


@pytest.mark.parametrize(
    "method,extra",
    [
        ("get_optimization_job", {}),
        ("cancel_optimization_job", {"expected_record_revision": 1}),
        (
            "export_optimization_package",
            {
                "expected_record_revision": 1,
                "expected_package_digest": digest("a"),
                "disclosure_capability": "c" * 64,
            },
        ),
        (
            "approve_optimization_job",
            {
                "expected_record_revision": 1,
                "expected_package_digest": digest("a"),
                "expected_judge_digest": digest("b"),
                "human_confirmation_capability": "c" * 64,
            },
        ),
    ],
)
def test_other_command_shapes(method, extra):
    raw = {"method": method, "job_id": digest("a"), **extra}
    assert parse_command(json.dumps(raw).encode()).method == method
    raw["approved"] = True
    with pytest.raises(OptimizationError, match=r"^optimization message is malformed$"):
        parse_command(json.dumps(raw).encode())


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"[]",
        b"null",
        b'{"method":"get_optimization_job","method":"PRIVATE-SECRET"}',
        b'{"method":"get_optimization_job","job_id":NaN}',
        b'{"method":"get_optimization_job","job_id":Infinity}',
        b"[" * 30 + b"0" + b"]" * 30,
        b"[" * 2000 + b"0" + b"]" * 2000,
        b" " * (MAX_MESSAGE_BYTES + 1),
        b"\xff",
        b'{"PRIVATE-SECRET":1}',
    ],
)
def test_bad_messages_fail_without_echo(payload):
    with pytest.raises(OptimizationError) as error:
        parse_command(payload)
    assert str(error.value) == "optimization message is malformed"
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("human_approval_required", False),
        ("human_approval_required", 1),
        ("human_approval_required", "true"),
        ("seed", True),
        ("seed", -1),
        ("seed", 1.0),
        ("policy_profile", "orcarouter/auto"),
        ("allowed_backends", ["untrusted-router"]),
        ("required_domains", ["SI"]),
        ("required_domains", ["DRC", "DRC"]),
        ("board_revision", "PRIVATE-PATH"),
    ],
)
def test_invalid_request_policy(field, value):
    raw = request().document()
    raw[field] = value
    with pytest.raises(OptimizationError):
        parse_command(json.dumps({"method": "start_optimization", "request": raw}).encode())


@pytest.mark.parametrize(
    "field,value",
    [
        ("preserve_existing_side", False),
        ("preserve_existing_side", 1),
        ("cardinal_rotations", [False]),
        ("cardinal_rotations", [45]),
        ("cardinal_rotations", [0, 0]),
        ("grid_nm", True),
        ("movable_footprint_refs", ["footprint:x", "footprint:x"]),
        ("movable_footprint_refs", ["footprint:z", "footprint:a"]),
    ],
)
def test_placement_scope_is_explicit_and_cardinal(field, value):
    raw = request().document()
    raw["placement_scope"][field] = value
    with pytest.raises(OptimizationError):
        parse_command(json.dumps({"method": "start_optimization", "request": raw}).encode())


def test_electrical_input_requires_erc():
    with pytest.raises(ValidationError):
        replace(request(), electrical_inputs_digest=digest("9"))
    req = replace(
        request(), electrical_inputs_digest=digest("9"), required_domains=("DRC", "ERC", "DFM")
    )
    package(req).require_reviewable_for(req)


def test_limits_and_weights_cannot_be_weakened():
    raw = request().document()
    raw["objective_weights"]["hard_drc_errors"] = 0
    with pytest.raises(OptimizationError):
        parse_command(json.dumps({"method": "start_optimization", "request": raw}).encode())
    for name, value in (
        ("max_repair_rounds", 9),
        ("max_route_attempts", 33),
        ("max_runtime_ms", 0),
    ):
        with pytest.raises(ValidationError):
            replace(request().limits, **{name: value})


def test_models_are_deeply_immutable():
    req = request()
    with pytest.raises(ValidationError):
        req.seed = 100
    with pytest.raises(ValidationError):
        req.placement_scope.grid_nm = 2
    detached = req.document()
    detached["placement_scope"]["movable_footprint_refs"].clear()
    assert req.placement_scope.movable_footprint_refs


def test_unavailable_judge_never_passes():
    req, chosen = request(), package()
    report = unavailable_report(
        req,
        candidate_id=chosen.binding.digest,
        candidate_input_digest=chosen.binding.candidate_board_revision,
        rule_context_digest=chosen.binding.rule_context_digest,
    )
    assert report.aggregate_status == "inconclusive"
    assert report.required_status == "inconclusive"
    assert not report.reviewable
    assert {row.domain for row in report.domains if row.reason == "backend_unavailable"} == {
        "SI",
        "PI",
        "thermal",
        "EMC",
    }


def test_optional_unknown_domains_survive_reviewable_package():
    chosen = package()
    assert chosen.judge.reviewable
    assert chosen.judge.required_status == "pass"
    assert chosen.judge.aggregate_status == "inconclusive"
    assert {"SI", "PI", "thermal", "EMC"} <= set(chosen.judge.inconclusive_domains)
    assert chosen.digest == package().digest
    assert chosen.judge.digest == package().judge.digest


@pytest.mark.parametrize(
    "field", ["candidate_id", "board_revision", "input_digest", "settings_digest"]
)
def test_judge_rejects_borrowed_evidence(field):
    report = package().judge
    domains = list(report.domains)
    domains[0] = replace(domains[0], evidence=replace(domains[0].evidence, **{field: digest("0")}))
    with pytest.raises(ValidationError):
        replace(report, domains=tuple(domains))


@pytest.mark.parametrize("domain", ["SI", "PI", "thermal", "EMC"])
def test_unavailable_physics_cannot_be_promoted(domain):
    evidence = package().judge.domains[0].evidence
    with pytest.raises(ValidationError):
        DomainResult(domain=domain, status="pass", reason="verified", evidence=evidence)


@pytest.mark.parametrize("change", ["single", "disagreement", "suppression", "failed_sample"])
def test_pass_requires_repeated_unsuppressed_agreement(change):
    result = package().judge.domains[0]
    evidence = result.evidence
    if change == "single":
        evidence = replace(evidence, samples=evidence.samples[:1])
    elif change == "disagreement":
        evidence = replace(
            evidence,
            samples=(
                evidence.samples[0],
                replace(evidence.samples[0], normalized_result_digest=digest("0")),
            ),
        )
    elif change == "suppression":
        evidence = replace(evidence, suppressed_check_count=1)
    else:
        failed = replace(evidence.samples[0], verdict="fail")
        evidence = replace(evidence, samples=(failed, failed))
    with pytest.raises(ValidationError):
        replace(result, evidence=evidence)


def test_judge_fail_dominates_missing_domains():
    report = package().judge
    failed = replace(report.domains[0].evidence.samples[0], verdict="fail")
    evidence = replace(report.domains[0].evidence, samples=(failed, failed))
    row = replace(report.domains[0], status="fail", reason="check_failed", evidence=evidence)
    report = replace(report, domains=(row, *report.domains[1:]))
    assert report.aggregate_status == report.required_status == "fail"
    assert not report.reviewable


def test_judge_requires_all_seven_domains():
    report = package().judge
    for rows in (report.domains[:-1], (*report.domains[:-1], report.domains[0])):
        with pytest.raises(ValidationError):
            replace(report, domains=rows)


def test_routes_are_bound_to_the_placed_snapshot():
    chosen = package()
    with pytest.raises(ValidationError):
        replace(chosen.binding, route_bundle_base_digest=chosen.binding.snapshot_digest)
    with pytest.raises(ValidationError):
        replace(chosen, binding=replace(chosen.binding, placement_candidate_id=digest("0")))


@pytest.mark.parametrize(
    "field,value",
    [
        ("hard_legality_errors", 1),
        ("hard_drc_errors", 1),
        ("fully_connected_target_nets", 1),
        ("actual_route_probes", 0),
    ],
)
def test_candidate_failure_cannot_reach_approval(field, value):
    req = request()
    chosen = replace(package(req), metrics=replace(package(req).metrics, **{field: value}))
    job = step(at_judging(req), req, "awaiting_approval", package=chosen)
    assert job.status == "failed"
    assert job.package_digest is None


def test_required_missing_domain_is_a_typed_non_success():
    req = replace(request(), required_domains=("DRC", "DFM", "SI"))
    chosen = package(req)
    job = step(at_judging(req), req, "awaiting_approval", package=chosen)
    assert job.status == "failed"
    assert job.failure_code == "required_domain_inconclusive"


def test_job_identity_is_context_bound_not_authorization():
    req = request()
    one = create_job(req, owner_binding=OWNER)
    assert one == create_job(req, owner_binding=OWNER)
    assert one.job_id != create_job(req, owner_binding=digest("9")).job_id
    with pytest.raises(OptimizationError):
        advance_job(
            one,
            req,
            expected_revision=0,
            owner_binding=digest("9"),
            next_status="inspecting",
            observed_board_revision=req.board_revision,
            observed_snapshot_digest=req.snapshot_digest,
        )


@pytest.mark.parametrize(
    "next_status", ["routing", "judging", "awaiting_approval", "approved", "completed"]
)
def test_phases_and_approval_cannot_be_skipped(next_status):
    req = request()
    with pytest.raises(OptimizationError):
        step(create_job(req, owner_binding=OWNER), req, next_status, package=package(req))


@pytest.mark.parametrize("field", ["board", "snapshot"])
def test_stale_observation_is_terminal(field):
    req = request()
    job = create_job(req, owner_binding=OWNER)
    result = advance_job(
        job,
        req,
        expected_revision=0,
        owner_binding=OWNER,
        next_status="inspecting",
        observed_board_revision=digest("0") if field == "board" else req.board_revision,
        observed_snapshot_digest=digest("0") if field == "snapshot" else req.snapshot_digest,
    )
    assert result.status == result.failure_code == "stale_revision"
    with pytest.raises(OptimizationError):
        step(result, req, "inspecting")


@pytest.mark.parametrize("field", list(ResourceUsage.model_fields))
def test_every_resource_budget_fails_closed(field):
    req = request()
    charge = ResourceUsage(**{field: getattr(req.limits, "max_" + field) + 1})
    job = step(create_job(req, owner_binding=OWNER), req, "inspecting", charge=charge)
    assert job.status == job.failure_code == "budget_exhausted"
    assert job.usage == ResourceUsage()


def test_budget_is_cumulative_and_repair_is_bounded():
    req = request()
    job = step(
        create_job(req, owner_binding=OWNER),
        req,
        "inspecting",
        charge=ResourceUsage(expansions=100),
    )
    assert job.status == "inspecting"
    result = step(job, req, "placing", charge=ResourceUsage(expansions=1))
    assert result.status == "budget_exhausted"
    job = at_judging(req)
    for state in ("repairing", "routing", "judging"):
        job = step(job, req, state)
    assert job.usage.route_attempts == 2
    assert job.usage.repair_rounds == 1
    assert step(job, req, "repairing").status == "budget_exhausted"


def test_runtime_ceiling_is_not_success():
    req = request()
    result = step(
        create_job(req, owner_binding=OWNER),
        req,
        "inspecting",
        charge=ResourceUsage(runtime_ms=req.limits.max_runtime_ms),
    )
    assert result.status == "budget_exhausted"


@pytest.mark.parametrize(
    "code", ["cancelled", "backend_failure", "unsupported_geometry", "interrupted"]
)
def test_expected_failures_are_terminal_non_success(code):
    req = request()
    result = fail_job(
        create_job(req, owner_binding=OWNER),
        req,
        expected_revision=0,
        owner_binding=OWNER,
        code=code,
    )
    assert result.status != "completed"
    assert result.failure_code == code
    with pytest.raises(OptimizationError):
        step(result, req, "inspecting")


def test_compare_and_swap_and_request_changes_are_refused():
    req = request()
    job = step(create_job(req, owner_binding=OWNER), req, "inspecting")
    with pytest.raises(OptimizationError):
        advance_job(
            job,
            req,
            expected_revision=0,
            owner_binding=OWNER,
            next_status="placing",
            observed_board_revision=req.board_revision,
            observed_snapshot_digest=req.snapshot_digest,
        )
    with pytest.raises(OptimizationError):
        step(job, replace(req, seed=10), "placing")
    forged = job.model_copy(update={"job_id": digest("0")})
    with pytest.raises(ValidationError):
        step(forged, req, "placing")


def test_approval_does_not_upgrade_judge_or_apply():
    req = request()
    job, chosen = awaiting(req)
    original = copy.deepcopy(chosen.document())
    authority = HumanApprovalAuthority(enabled=True)
    token = authority.issue_from_human_channel(job, chosen, owner_binding=OWNER)
    approved = approve(job, req, chosen, token, authority)
    assert approved.status == "approved"
    assert approved.judge_digest == job.judge_digest
    assert chosen.document() == original
    assert chosen.judge.aggregate_status == "inconclusive"
    assert chosen.document()["apply_authority"] == "none"
    completed = step(approved, req, "completed", package=chosen)
    assert completed.status == "completed"
    assert completed.approval_receipt_digest
    with pytest.raises(OptimizationError):
        approve(job, req, chosen, token, authority)


def test_approval_disabled_unknown_expired_and_wrong_scope():
    req = request()
    job, chosen = awaiting(req)
    with pytest.raises(OptimizationError):
        HumanApprovalAuthority().issue_from_human_channel(job, chosen, owner_binding=OWNER)
    now = [0.0]
    authority = HumanApprovalAuthority(enabled=True, clock=lambda: now[0])
    token = authority.issue_from_human_channel(job, chosen, owner_binding=OWNER)
    with pytest.raises(OptimizationError):
        authority.consume(job, chosen, token, owner_binding=digest("0"))
    with pytest.raises(OptimizationError):
        approve(job, req, chosen, "0" * 64, authority)
    now[0] = 600.0
    with pytest.raises(OptimizationError):
        approve(job, req, chosen, token, authority)


def test_replay_is_atomic_and_ephemeral():
    req = request()
    job, chosen = awaiting(req)
    authority = HumanApprovalAuthority(enabled=True)
    token = authority.issue_from_human_channel(job, chosen, owner_binding=OWNER)
    with pytest.raises(OptimizationError):
        approve(job, req, chosen, token, HumanApprovalAuthority(enabled=True))

    def consume(_):
        try:
            authority.consume(job, chosen, token, owner_binding=OWNER)
            return True
        except OptimizationError:
            return False

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(consume, range(8))) == 1


def test_stale_approval_does_not_consume_or_succeed():
    req = request()
    job, chosen = awaiting(req)
    authority = HumanApprovalAuthority(enabled=True)
    token = authority.issue_from_human_channel(job, chosen, owner_binding=OWNER)
    result = approve(job, req, chosen, token, authority, observed_snapshot_digest=digest("0"))
    assert result.status == "stale_revision"
    assert result.approval_receipt_digest is None


def test_approval_capability_cannot_follow_a_different_package():
    req = request()
    job, chosen = awaiting(req)
    authority = HumanApprovalAuthority(enabled=True)
    token = authority.issue_from_human_channel(job, chosen, owner_binding=OWNER)
    changed = replace(chosen, metrics=replace(chosen.metrics, copper_length_nm=20))
    with pytest.raises(OptimizationError):
        approve(job, req, changed, token, authority)


def test_retention_projection_excludes_requests_refs_and_capabilities():
    req = request()
    job, chosen = awaiting(req)
    authority = HumanApprovalAuthority(enabled=True)
    token = authority.issue_from_human_channel(job, chosen, owner_binding=OWNER)
    job = approve(job, req, chosen, token, authority)
    encoded = json.dumps(job.document())
    assert "PRIVATE-REFERENCE-CANARY" not in encoded
    assert token not in encoded
    for forbidden in (
        "request",
        "board_bytes",
        "geometry",
        "prompt",
        "credentials",
        "solver_log",
        "apply_token",
    ):
        with pytest.raises(ValidationError):
            replace(job, **{forbidden: "PRIVATE-SECRET"})
    assert "placement_scope" not in encoded


def release_evidence():
    return ReleaseEvidence(
        schema_version="optimization-release/v1",
        target_version="0.13.0",
        evaluated_commit="a" * 40,
        corpus=CorpusSummary(
            manifest_digest=digest("a"),
            frozen_eligibility_digest=digest("b"),
            frozen_profiles_digest=digest("c"),
            board_count=12,
            project_family_count=3,
            held_out_board_count=6,
            copper_layer_counts=(2, 4, 6, 8),
            eligible_held_out_target_nets=100,
            fully_routed_held_out_target_nets=90,
            improved_held_out_placement_cases=3,
        ),
        checks=tuple(
            GateEvidence(gate=gate, status="pass", artifact_digest=digest("d")) for gate in GATES
        ),
    )


def test_release_numeric_threshold_and_zero_denominator():
    evidence = release_evidence()
    assert release_blockers(evidence) == ()
    low = replace(evidence, corpus=replace(evidence.corpus, fully_routed_held_out_target_nets=89))
    assert "held_out_routing_below_90_percent" in release_blockers(low)
    zero = replace(
        evidence,
        corpus=replace(
            evidence.corpus, eligible_held_out_target_nets=0, fully_routed_held_out_target_nets=0
        ),
    )
    assert "no_eligible_held_out_target_nets" in release_blockers(zero)
    assert "corpus_not_measured" in release_blockers(replace(evidence, corpus=None))


@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize("status", ["fail", "not_run", "inconclusive"])
def test_every_release_gate_is_mandatory(gate, status):
    evidence = release_evidence()
    checks = tuple(
        replace(row, status=status) if row.gate == gate else row for row in evidence.checks
    )
    assert gate in release_blockers(replace(evidence, checks=checks))


def test_release_cannot_omit_checks_or_claim_no_evidence_pass():
    evidence = release_evidence()
    with pytest.raises(ValidationError):
        replace(evidence, checks=evidence.checks[:-1])
    with pytest.raises(ValidationError):
        replace(evidence, checks=(evidence.checks[0], *evidence.checks[:-1]))
    with pytest.raises(ValidationError):
        replace(evidence.checks[0], artifact_digest=None)


@pytest.mark.parametrize(
    "field,value,blocker",
    [
        ("board_count", 11, "fewer_than_12_boards"),
        ("project_family_count", 2, "fewer_than_3_project_families"),
        ("copper_layer_counts", (2, 4), "declared_2_to_8_layer_coverage_unproven"),
        ("improved_held_out_placement_cases", 2, "fewer_than_3_improved_held_out_placement_cases"),
    ],
)
def test_corpus_release_requirements(field, value, blocker):
    evidence = release_evidence()
    changed = replace(evidence, corpus=replace(evidence.corpus, **{field: value}))
    assert blocker in release_blockers(changed)


def test_no_optimization_tool_is_registered_prematurely():
    from copper_mcp.mcp_server import mcp

    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert not names.intersection(
        {
            "start_optimization",
            "get_optimization_job",
            "cancel_optimization_job",
            "export_optimization_package",
            "approve_optimization_job",
        }
    )


def test_schema_snapshot_matches_models():
    path = ROOT / "schemas" / "optimization-v1.schema.json"
    assert json.loads(path.read_text()) == command_schema()


def test_bounded_json_value_count_and_field_count():
    for raw in (
        json.dumps(list(range(17_000))).encode(),
        json.dumps({str(i): i for i in range(65)}).encode(),
    ):
        with pytest.raises(OptimizationError):
            bounded_json(raw)


def test_target_denominator_cannot_shrink_after_request():
    req = request()
    chosen = package(req)
    smaller = replace(
        chosen, metrics=replace(chosen.metrics, target_net_count=1, fully_connected_target_nets=1)
    )
    with pytest.raises(OptimizationError):
        smaller.require_reviewable_for(req)
    assert replace(req, target_net_scope_digest=digest("9")).digest != req.digest


@pytest.mark.parametrize("domain", ["SI", "PI", "thermal", "EMC"])
@pytest.mark.parametrize("status,reason", [("not_run", "not_requested"), ("fail", "check_failed")])
def test_missing_physics_is_explicitly_inconclusive(domain, status, reason):
    with pytest.raises(ValidationError):
        DomainResult(domain=domain, status=status, reason=reason, evidence=None)


def test_erc_is_bound_to_electrical_inputs_not_board_bytes():
    req = replace(
        request(), electrical_inputs_digest=digest("9"), required_domains=("DRC", "ERC", "DFM")
    )
    chosen = package(req)
    row = chosen.judge.domains[1]
    assert row.evidence.input_digest == req.electrical_inputs_digest
    wrong = replace(
        row, evidence=replace(row.evidence, input_digest=chosen.binding.candidate_board_revision)
    )
    with pytest.raises(ValidationError):
        replace(chosen.judge, domains=(chosen.judge.domains[0], wrong, *chosen.judge.domains[2:]))


@pytest.mark.parametrize(
    "field", ["source_board_revision", "placed_snapshot_digest", "route_bundle_id"]
)
def test_backend_provenance_targets_selected_composition(field):
    chosen = package()
    row = replace(chosen.backend_provenance[0], **{field: digest("0")})
    with pytest.raises(ValidationError):
        replace(chosen, backend_provenance=(row,))


def test_judge_rule_context_cannot_be_borrowed():
    report = package().judge
    with pytest.raises(ValidationError):
        replace(report, rule_context_digest=digest("0"))


def test_budget_integer_overflow_is_typed_exhaustion():
    req = request()
    job = step(
        create_job(req, owner_binding=OWNER), req, "inspecting", charge=ResourceUsage(expansions=1)
    )
    result = step(job, req, "placing", charge=ResourceUsage(expansions=(1 << 53) - 1))
    assert result.status == result.failure_code == "budget_exhausted"
    assert result.usage == job.usage


def test_confirmation_capacity_expiry_and_clock_validation():
    job, chosen = awaiting()
    now = [0.0]
    authority = HumanApprovalAuthority(enabled=True, clock=lambda: now[0])
    for _ in range(128):
        authority.issue_from_human_channel(job, chosen, owner_binding=OWNER)
    with pytest.raises(OptimizationError):
        authority.issue_from_human_channel(job, chosen, owner_binding=OWNER)
    now[0] = 600.0
    assert authority.issue_from_human_channel(job, chosen, owner_binding=OWNER)
    now[0] = float("nan")
    with pytest.raises(OptimizationError):
        authority.issue_from_human_channel(job, chosen, owner_binding=OWNER)


def test_release_cli_never_authorizes_or_edits_evidence(tmp_path, capsys):
    from scripts.check_optimization_release import main

    path = tmp_path / "evidence.json"
    original = json.dumps(release_evidence().document()).encode()
    path.write_bytes(original)
    assert main([str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["release_authorized"] is False
    assert output["artifact_authenticity_verified"] is False
    assert path.read_bytes() == original
    path.write_text(json.dumps(replace(release_evidence(), corpus=None).document()))
    assert main([str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    path.write_text('{"PRIVATE-SECRET": NaN}')
    assert main([str(path)]) == 2
    assert "PRIVATE-SECRET" not in capsys.readouterr().out
    link = tmp_path / "symlink.json"
    link.symlink_to(path)
    assert main([str(link)]) == 2


def test_server_info_separates_existing_observation_from_future_optimization():
    from copper_mcp.tools import server_info

    info = server_info()
    assert "authoritative KiCad DRC binding for placement candidates" not in info["planned"]
    assert (
        "revision-bound post-placement scene and DRC observation (read-only)" in info["implemented"]
    )
    assert "supervised optimization workflow with human review (optimization/v1)" in info["planned"]
    assert not any("optimization" in item for item in info["implemented"])


def test_optimization_messages_match_published_schema():
    schema = json.loads((ROOT / "schemas" / "optimization-v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    common = {"job_id": digest("a"), "expected_record_revision": 1}
    raw_commands = (
        {"method": "start_optimization", "request": request().document()},
        {"method": "get_optimization_job", "job_id": digest("a")},
        {"method": "cancel_optimization_job", **common},
        {
            "method": "export_optimization_package",
            **common,
            "expected_package_digest": digest("b"),
            "disclosure_capability": "c" * 64,
        },
        {
            "method": "approve_optimization_job",
            **common,
            "expected_package_digest": digest("b"),
            "expected_judge_digest": digest("d"),
            "human_confirmation_capability": "c" * 64,
        },
    )
    for raw in raw_commands:
        emitted = parse_command(json.dumps(raw).encode()).document()
        validator.validate(emitted)
        assert list(validator.iter_errors({**emitted, "unrecognized": True}))
    invalid = copy.deepcopy(raw_commands[0])
    invalid["request"]["human_approval_required"] = 1
    assert list(validator.iter_errors(invalid))
