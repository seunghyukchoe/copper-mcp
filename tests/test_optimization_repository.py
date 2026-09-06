"""Durable optimization metadata tests; all evidence and packages are synthetic."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from copper_mcp.optimization.approval import HumanApprovalAuthority
from copper_mcp.optimization.contracts import (
    DOMAINS,
    ObjectiveWeights,
    OptimizationError,
    OptimizationRequest,
    PlacementScope,
    ResourceLimits,
)
from copper_mcp.optimization.judge import (
    DomainResult,
    EvidenceBinding,
    EvidenceSample,
    JudgeReport,
)
from copper_mcp.optimization.lifecycle import ResourceUsage, advance_job
from copper_mcp.optimization.package import (
    BackendProvenance,
    CandidateBinding,
    ObjectiveMetrics,
    OptimizationPackage,
)
from copper_mcp.optimization.repository import (
    OptimizationJobAlreadyClaimedError,
    OptimizationJobConflictError,
    OptimizationJobLeaseError,
    OptimizationJobLimitError,
    OptimizationJobRepository,
    OptimizationJobUnavailableError,
)

OWNER = "sha256:" + "8" * 64
OTHER_OWNER = "sha256:" + "7" * 64
PRIVATE_CANARY = "PRIVATE-REFERENCE-CANARY"


def digest(character: str) -> str:
    return "sha256:" + character * 64


def optimization_request(*, runtime_ms: int = 10_000) -> OptimizationRequest:
    return OptimizationRequest(
        schema_version="optimization/v1",
        board_revision=digest("a"),
        snapshot_digest=digest("b"),
        placement_scope=PlacementScope(
            movable_footprint_refs=(f"footprint:{PRIVATE_CANARY}",),
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
        objective_weights=ObjectiveWeights(
            congestion=1,
            clearance_margin=1,
            vias=1,
            copper_length=1,
            displacement=1,
            intent_residual=1,
        ),
        limits=ResourceLimits(
            max_runtime_ms=runtime_ms,
            max_candidates=4,
            max_placement_evaluations=20,
            max_route_attempts=4,
            max_repair_rounds=1,
            max_expansions=100,
            max_obstacle_checks=1_000,
            max_external_output_bytes=1_024,
        ),
        human_approval_required=True,
        policy_profile="deterministic-v1",
    )


def optimization_package(request: OptimizationRequest) -> OptimizationPackage:
    binding = CandidateBinding(
        board_revision=request.board_revision,
        snapshot_digest=request.snapshot_digest,
        placement_candidate_id=digest("d"),
        placed_snapshot_digest=digest("e"),
        route_bundle_id=digest("f"),
        route_bundle_base_digest=digest("e"),
        candidate_board_revision=digest("1"),
        rule_context_digest=digest("2"),
    )

    domains: list[DomainResult] = []
    for domain in DOMAINS:
        if domain in request.required_domains:
            sample = EvidenceSample(verdict="pass", normalized_result_digest=digest("3"))
            evidence = EvidenceBinding(
                candidate_id=binding.digest,
                board_revision=request.board_revision,
                input_digest=binding.candidate_board_revision,
                rule_context_digest=binding.rule_context_digest,
                settings_digest=request.judge_profile_digest,
                backend=("kicad-drc-v1" if domain == "DRC" else "kicad-drc-dfm-v1"),
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
                    reason="backend_unavailable",
                    evidence=None,
                )
            )
    return OptimizationPackage(
        schema_version="optimization/v1",
        request_digest=request.digest,
        binding=binding,
        alternate_candidate_ids=(),
        metrics=ObjectiveMetrics(
            hard_legality_errors=0,
            hard_drc_errors=0,
            target_net_count=request.target_net_count,
            fully_connected_target_nets=request.target_net_count,
            congestion_penalty=0,
            clearance_margin_nm=1_000,
            via_count=1,
            copper_length_nm=3_000_000,
            displacement_nm=100_000,
            intent_residual=0,
            actual_route_probes=2,
        ),
        judge=JudgeReport(
            schema_version="optimization/v1",
            candidate_id=binding.digest,
            board_revision=request.board_revision,
            input_digest=binding.candidate_board_revision,
            rule_context_digest=binding.rule_context_digest,
            electrical_inputs_digest=request.electrical_inputs_digest,
            settings_digest=request.judge_profile_digest,
            required_domains=request.required_domains,
            domains=tuple(domains),
        ),
        backend_provenance=(
            BackendProvenance(
                backend="internal-layered-v1",
                version="1.0.0",
                executable_digest=digest("6"),
                command_digest=digest("7"),
                settings_digest=request.routing_profile_digest,
                input_digest=binding.placed_snapshot_digest,
                source_board_revision=binding.board_revision,
                placed_snapshot_digest=binding.placed_snapshot_digest,
                route_bundle_id=binding.route_bundle_id,
                normalized_output_digest=binding.candidate_board_revision,
            ),
        ),
    )


def awaiting_approval(
    repository: OptimizationJobRepository, request: OptimizationRequest
) -> tuple[object, OptimizationPackage]:
    record = repository.create(request, OWNER, now_ms=0)
    lease = repository.claim(record.job_id, request, OWNER, now_ms=1)
    current = repository.lease_record(lease, now_ms=2)
    for status in ("placing", "routing", "judging"):
        replacement = advance_job(
            current,
            request,
            expected_revision=current.revision,
            owner_binding=OWNER,
            next_status=status,
            observed_board_revision=request.board_revision,
            observed_snapshot_digest=request.snapshot_digest,
        )
        current = repository.cas(current, replacement, owner_binding=OWNER, lease=lease, now_ms=2)
        lease = lease.with_revision(current.revision)
    package = optimization_package(request)
    awaiting = advance_job(
        current,
        request,
        expected_revision=current.revision,
        owner_binding=OWNER,
        next_status="awaiting_approval",
        observed_board_revision=request.board_revision,
        observed_snapshot_digest=request.snapshot_digest,
        package=package,
    )
    return (
        repository.cas(
            current, awaiting, owner_binding=OWNER, lease=lease, package=package, now_ms=3
        ),
        package,
    )


def test_create_reopens_redacted_metadata_and_owner_miss_is_uniform(tmp_path: Path) -> None:
    path = tmp_path / "optimization.sqlite3"
    request = optimization_request()
    with OptimizationJobRepository(path, ttl_ms=1_000) as repository:
        created = repository.create(request, OWNER, now_ms=100)
        assert repository.create(request, OWNER, now_ms=101) == created
        with pytest.raises(OptimizationJobUnavailableError):
            repository.get(created.job_id, OTHER_OWNER, now_ms=101)

    with OptimizationJobRepository(path, ttl_ms=1_000) as reopened:
        assert reopened.get(created.job_id, OWNER, now_ms=101) == created

    database = path.read_bytes()
    assert PRIVATE_CANARY.encode() not in database
    assert b"movable_footprint_refs" not in database
    assert b"capability" not in database
    assert b"path" not in database


def test_separate_connections_allow_exactly_one_claim(tmp_path: Path) -> None:
    path = tmp_path / "claim.sqlite3"
    request = optimization_request()
    with OptimizationJobRepository(path) as creator:
        record = creator.create(request, OWNER, now_ms=10)
    first = OptimizationJobRepository(path)
    second = OptimizationJobRepository(path)

    def claim(repository: OptimizationJobRepository) -> object:
        try:
            return repository.claim(record.job_id, request, OWNER, now_ms=20)
        except (OptimizationJobAlreadyClaimedError, OptimizationJobConflictError) as error:
            return error

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, (first, second)))
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(isinstance(result, Exception) for result in results) == 1
    finally:
        first.close()
        second.close()


def test_repeated_create_returns_current_state_after_job_has_started(tmp_path: Path) -> None:
    request = optimization_request()
    with OptimizationJobRepository(tmp_path / "idempotent.sqlite3") as repository:
        queued = repository.create(request, OWNER, now_ms=10)
        lease = repository.claim(queued.job_id, request, OWNER, now_ms=20)
        inspecting = repository.lease_record(lease, now_ms=21)

        repeated = repository.create(request, OWNER, now_ms=22)

        assert repeated == inspecting
        assert repeated.status == "inspecting"
        assert repeated.revision == lease.revision


def test_stale_revision_and_stale_fence_cannot_overwrite(tmp_path: Path) -> None:
    request = optimization_request()
    with OptimizationJobRepository(tmp_path / "cas.sqlite3") as repository:
        created = repository.create(request, OWNER, now_ms=10)
        lease = repository.claim(created.job_id, request, OWNER, now_ms=20)
        inspecting = repository.lease_record(lease, now_ms=21)
        placing = advance_job(
            inspecting,
            request,
            expected_revision=inspecting.revision,
            owner_binding=OWNER,
            next_status="placing",
            observed_board_revision=request.board_revision,
            observed_snapshot_digest=request.snapshot_digest,
            charge=ResourceUsage(placement_evaluations=1),
        )
        assert (
            repository.cas(inspecting, placing, owner_binding=OWNER, lease=lease, now_ms=22)
            == placing
        )
        with pytest.raises(OptimizationJobConflictError):
            repository.cas(inspecting, placing, owner_binding=OWNER, lease=lease, now_ms=23)
        with pytest.raises(OptimizationJobLeaseError):
            repository.heartbeat(lease, now_ms=23)


def test_active_lease_survives_record_ttl_then_expires_interrupted(tmp_path: Path) -> None:
    request = optimization_request()
    path = tmp_path / "ttl.sqlite3"
    with OptimizationJobRepository(path, ttl_ms=5) as repository:
        created = repository.create(request, OWNER, now_ms=0)
        lease = repository.claim(created.job_id, request, OWNER, lease_ms=20, now_ms=1)
        assert repository.get(created.job_id, OWNER, now_ms=10).status == "inspecting"
        assert repository.count(now_ms=10) == 1
        assert repository.get(created.job_id, OWNER, now_ms=21).failure_code == "interrupted"
        with pytest.raises(OptimizationJobLeaseError):
            repository.heartbeat(lease, now_ms=21)
        assert repository.count(now_ms=27) == 0


def test_count_limit_and_payload_byte_limits_are_enforced(tmp_path: Path) -> None:
    request = optimization_request()
    with OptimizationJobRepository(tmp_path / "count.sqlite3", max_records=1) as repository:
        repository.create(request, OWNER, now_ms=0)
        changed = OptimizationRequest.model_validate(
            {**request.model_dump(), "seed": request.seed + 1}
        )
        with pytest.raises(OptimizationJobLimitError):
            repository.create(changed, OWNER, now_ms=1)

    with OptimizationJobRepository(tmp_path / "bytes.sqlite3", max_record_bytes=64) as repository:
        with pytest.raises(OptimizationJobLimitError):
            repository.create(request, OWNER, now_ms=0)


def test_restart_without_private_request_marks_queued_job_interrupted(tmp_path: Path) -> None:
    path = tmp_path / "restart.sqlite3"
    request = optimization_request()
    with OptimizationJobRepository(path) as repository:
        created = repository.create(request, OWNER, now_ms=10)
    with OptimizationJobRepository(path) as restarted:
        recovered = restarted.recover_interrupted(
            created.job_id, OWNER, expected_revision=created.revision, now_ms=20
        )
        assert recovered.status == "failed"
        assert recovered.failure_code == "interrupted"


def test_reviewable_package_is_separate_owner_bound_metadata(tmp_path: Path) -> None:
    request = optimization_request()
    package = optimization_package(request)
    path = tmp_path / "package.sqlite3"
    with OptimizationJobRepository(path) as repository:
        record = repository.create(request, OWNER, now_ms=0)
        lease = repository.claim(record.job_id, request, OWNER, now_ms=1)
        current = repository.lease_record(lease, now_ms=2)
        for status in ("placing", "routing", "judging"):
            replacement = advance_job(
                current,
                request,
                expected_revision=current.revision,
                owner_binding=OWNER,
                next_status=status,
                observed_board_revision=request.board_revision,
                observed_snapshot_digest=request.snapshot_digest,
            )
            current = repository.cas(
                current, replacement, owner_binding=OWNER, lease=lease, now_ms=2
            )
            lease = lease.with_revision(current.revision)
        awaiting = advance_job(
            current,
            request,
            expected_revision=current.revision,
            owner_binding=OWNER,
            next_status="awaiting_approval",
            observed_board_revision=request.board_revision,
            observed_snapshot_digest=request.snapshot_digest,
            package=package,
        )
        repository.cas(
            current,
            awaiting,
            owner_binding=OWNER,
            lease=lease,
            package=package,
            now_ms=3,
        )
        assert repository.get_package(record.job_id, OWNER, now_ms=4) == package
        with pytest.raises(OptimizationJobUnavailableError):
            repository.get_package(record.job_id, OTHER_OWNER, now_ms=4)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM optimization_packages").fetchone() == (1,)
        payload = connection.execute("SELECT package_json FROM optimization_packages").fetchone()
    assert payload is not None and PRIVATE_CANARY.encode() not in bytes(payload[0])


@pytest.mark.parametrize(
    ("observed_board_revision", "observed_snapshot_digest"),
    [
        (None, None),
        (digest("f"), digest("0")),
        (digest("f"), digest("b")),
        (digest("a"), digest("0")),
        ("not-a-digest", digest("b")),
    ],
)
def test_approval_refuses_unobserved_or_stale_state_without_consuming_consent(
    tmp_path: Path,
    observed_board_revision: object,
    observed_snapshot_digest: object,
) -> None:
    request = optimization_request()
    with OptimizationJobRepository(tmp_path / "approval.sqlite3") as repository:
        awaiting, package = awaiting_approval(repository, request)
        authority = HumanApprovalAuthority(enabled=True)
        capability = authority.issue_from_human_channel(awaiting, package, owner_binding=OWNER)

        with pytest.raises(OptimizationError):
            repository.approve(
                awaiting.job_id,
                request,
                OWNER,
                expected_revision=awaiting.revision,
                package=package,
                capability=capability,
                authority=authority,
                observed_board_revision=observed_board_revision,  # type: ignore[arg-type]
                observed_snapshot_digest=observed_snapshot_digest,  # type: ignore[arg-type]
                now_ms=4,
            )
        assert repository.get(awaiting.job_id, OWNER, now_ms=4) == awaiting

        approved = repository.approve(
            awaiting.job_id,
            request,
            OWNER,
            expected_revision=awaiting.revision,
            package=package,
            capability=capability,
            authority=authority,
            observed_board_revision=request.board_revision,
            observed_snapshot_digest=request.snapshot_digest,
            now_ms=5,
        )
        assert approved.status == "approved"


def test_approval_requires_both_observation_arguments_before_consuming_consent(tmp_path):
    request = optimization_request()
    with OptimizationJobRepository(tmp_path / "missing-observations.sqlite3") as repository:
        awaiting, package = awaiting_approval(repository, request)
        authority = HumanApprovalAuthority(enabled=True)
        capability = authority.issue_from_human_channel(awaiting, package, owner_binding=OWNER)
        arguments = {
            "expected_revision": awaiting.revision,
            "package": package,
            "capability": capability,
            "authority": authority,
            "now_ms": 4,
        }
        for partial in (
            {},
            {"observed_board_revision": request.board_revision},
            {"observed_snapshot_digest": request.snapshot_digest},
        ):
            with pytest.raises(TypeError):
                repository.approve(awaiting.job_id, request, OWNER, **arguments, **partial)
            assert repository.get(awaiting.job_id, OWNER, now_ms=4) == awaiting
        approved = repository.approve(
            awaiting.job_id,
            request,
            OWNER,
            **arguments,
            observed_board_revision=request.board_revision,
            observed_snapshot_digest=request.snapshot_digest,
        )
        assert approved.status == "approved"


__all__: tuple[str, ...] = ()
