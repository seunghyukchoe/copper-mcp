"""Internal optimization worker tests; no backend, KiCad, MCP, or apply invocation occurs."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import NoReturn, cast

import pytest

from copper_mcp.optimization.approval import HumanApprovalAuthority
from copper_mcp.optimization.lifecycle import ResourceUsage, advance_job, fail_job
from copper_mcp.optimization.package import OptimizationPackage
from copper_mcp.optimization.repository import (
    OptimizationJobConflictError,
    OptimizationJobRepository,
    OptimizationJobUnavailableError,
)
from copper_mcp.optimization.worker import (
    OptimizationExecutionError,
    OptimizationExecutionProbe,
    execute_optimization_job,
)
from tests.test_optimization_repository import (
    OWNER,
    PRIVATE_CANARY,
    optimization_package,
    optimization_request,
)


class Clock:
    def __init__(self, wall_ms: int = 0, monotonic_ms: int = 0) -> None:
        self.wall_ms = wall_ms
        self.monotonic_ms = monotonic_ms

    def wall(self) -> int:
        return self.wall_ms

    def monotonic(self) -> int:
        return self.monotonic_ms


def advance_to_judging(probe: OptimizationExecutionProbe) -> None:
    probe.advance("placing")
    probe.reserve(ResourceUsage(placement_evaluations=1))
    probe.reserve(ResourceUsage(placement_evaluations=1))
    probe.advance("routing")
    probe.reserve(ResourceUsage(expansions=4, obstacle_checks=8))
    probe.reserve(ResourceUsage(expansions=6, obstacle_checks=12))
    probe.advance("judging", charge=ResourceUsage(obstacle_checks=20))


def test_worker_publishes_reviewable_package_without_apply_or_private_request(
    tmp_path: Path,
) -> None:
    request = optimization_request()
    package = optimization_package(request)
    path = tmp_path / "success.sqlite3"
    with OptimizationJobRepository(path) as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            assert probe.cancelled() is False
            advance_to_judging(probe)
            return package

        result = execute_optimization_job(repository, record.job_id, request, OWNER, executor)
        assert result.status == "awaiting_approval"
        assert result.package_digest == package.digest
        assert repository.get_package(record.job_id, OWNER) == package

    database = path.read_bytes()
    assert PRIVATE_CANARY.encode() not in database
    assert b"apply_candidate" not in database


def test_cumulative_route_and_repair_attempts_exhaust_before_work(tmp_path: Path) -> None:
    request = optimization_request()
    with OptimizationJobRepository(tmp_path / "attempts.sqlite3") as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> NoReturn:
            probe.advance("placing")
            probe.advance("routing")
            probe.advance("judging")
            probe.advance("repairing")
            exhausted = probe.advance("routing", charge=ResourceUsage(route_attempts=3))
            assert exhausted.failure_code == "budget_exhausted"
            raise OptimizationExecutionError("budget_exhausted")

        result = execute_optimization_job(repository, record.job_id, request, OWNER, executor)
        assert result.status == "budget_exhausted"
        assert result.failure_code == "budget_exhausted"
        assert result.package_digest is None


def test_same_phase_failed_reservation_preserves_prior_billed_work(tmp_path: Path) -> None:
    request = optimization_request()
    with OptimizationJobRepository(tmp_path / "same-phase.sqlite3") as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> NoReturn:
            probe.advance("placing")
            first = probe.reserve(ResourceUsage(placement_evaluations=7))
            assert first.status == "placing"
            exhausted = probe.reserve(ResourceUsage(placement_evaluations=20))
            assert exhausted.failure_code == "budget_exhausted"
            assert exhausted.usage.placement_evaluations == 7
            raise OptimizationExecutionError("budget_exhausted")

        result = execute_optimization_job(repository, record.job_id, request, OWNER, executor)
        assert result.failure_code == "budget_exhausted"
        assert result.usage.placement_evaluations == 7


def test_cancel_during_callback_wins_and_never_publishes(tmp_path: Path) -> None:
    request = optimization_request()
    package = optimization_package(request)
    with OptimizationJobRepository(tmp_path / "cancel.sqlite3") as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            current = probe.record
            cancelled = repository.cancel(
                record.job_id,
                request,
                OWNER,
                expected_revision=current.revision,
            )
            assert cancelled.status == "cancelled"
            assert probe.cancelled()
            return package

        result = execute_optimization_job(repository, record.job_id, request, OWNER, executor)
        assert result.status == "cancelled"
        assert result.package_digest is None
        with sqlite3.connect(tmp_path / "cancel.sqlite3") as connection:
            assert connection.execute("SELECT COUNT(*) FROM optimization_packages").fetchone() == (
                0,
            )


def test_cancel_awaiting_approval_clears_redacted_package(
    tmp_path: Path,
) -> None:
    request = optimization_request()
    package = optimization_package(request)
    with OptimizationJobRepository(tmp_path / "cancel-review.sqlite3") as repository:
        created = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            advance_to_judging(probe)
            return package

        awaiting = execute_optimization_job(repository, created.job_id, request, OWNER, executor)
        cancelled = repository.cancel(
            created.job_id,
            request,
            OWNER,
            expected_revision=awaiting.revision,
        )
        assert cancelled.status == "cancelled"
        assert cancelled.package_digest is None
        with pytest.raises(OptimizationJobUnavailableError):
            repository.get_package(created.job_id, OWNER)


def test_approval_is_repository_owned_and_completed_package_survives_ttl(
    tmp_path: Path,
) -> None:
    request = optimization_request()
    package = optimization_package(request)
    clock = Clock()
    with OptimizationJobRepository(
        tmp_path / "approve.sqlite3",
        ttl_ms=1_000,
        clock=clock.wall,
        monotonic_clock=clock.monotonic,
    ) as repository:
        created = repository.create(request, OWNER, now_ms=0)

        def executor(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            advance_to_judging(probe)
            return package

        awaiting = execute_optimization_job(repository, created.job_id, request, OWNER, executor)
        forged = type(awaiting).model_validate(
            {
                **awaiting.model_dump(),
                "revision": awaiting.revision + 1,
                "status": "approved",
                "approval_receipt_digest": "sha256:" + "0" * 64,
            }
        )
        with pytest.raises(OptimizationJobConflictError):
            repository.cas(awaiting, forged, owner_binding=OWNER)

        authority = HumanApprovalAuthority(enabled=True, clock=lambda: 0.0)
        capability = authority.issue_from_human_channel(awaiting, package, owner_binding=OWNER)
        approved = repository.approve(
            created.job_id,
            request,
            OWNER,
            expected_revision=awaiting.revision,
            package=package,
            capability=capability,
            authority=authority,
            observed_board_revision=request.board_revision,
            observed_snapshot_digest=request.snapshot_digest,
            now_ms=10,
        )
        assert approved.status == "approved"
        assert repository.get_package(created.job_id, OWNER, now_ms=11) == package
        completed = advance_job(
            approved,
            request,
            expected_revision=approved.revision,
            owner_binding=OWNER,
            next_status="completed",
            observed_board_revision=request.board_revision,
            observed_snapshot_digest=request.snapshot_digest,
            package=package,
        )
        persisted_completed = repository.cas(
            approved,
            completed,
            owner_binding=OWNER,
            package=package,
            now_ms=12,
        )
        assert repository.create(request, OWNER, now_ms=13) == persisted_completed
        assert repository.get_package(created.job_id, OWNER, now_ms=999) == package
        with pytest.raises(OptimizationJobUnavailableError):
            repository.get_package(created.job_id, OWNER, now_ms=1_013)


def test_failure_after_publication_clears_package(tmp_path: Path) -> None:
    request = optimization_request()
    package = optimization_package(request)
    with OptimizationJobRepository(tmp_path / "published-failure.sqlite3") as repository:
        created = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            advance_to_judging(probe)
            return package

        awaiting = execute_optimization_job(repository, created.job_id, request, OWNER, executor)
        failed = fail_job(
            awaiting,
            request,
            expected_revision=awaiting.revision,
            owner_binding=OWNER,
            code="backend_failure",
        )
        repository.cas(awaiting, failed, owner_binding=OWNER)
        with pytest.raises(OptimizationJobUnavailableError):
            repository.get_package(created.job_id, OWNER)


def test_lost_lease_after_executor_output_never_publishes(tmp_path: Path) -> None:
    request = optimization_request(runtime_ms=100)
    package = optimization_package(request)
    clock = Clock(wall_ms=0, monotonic_ms=0)
    path = tmp_path / "lost.sqlite3"
    with OptimizationJobRepository(
        path, clock=clock.wall, monotonic_clock=clock.monotonic
    ) as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            advance_to_judging(probe)
            clock.wall_ms = 101
            return package

        result = execute_optimization_job(repository, record.job_id, request, OWNER, executor)
        assert result.failure_code == "interrupted"
        with pytest.raises(OptimizationJobUnavailableError):
            repository.get_package(record.job_id, OWNER)


def test_global_runtime_lease_does_not_expire_at_thirty_seconds(tmp_path: Path) -> None:
    request = optimization_request(runtime_ms=60_000)
    clock = Clock()
    with OptimizationJobRepository(
        tmp_path / "long-phase.sqlite3",
        clock=clock.wall,
        monotonic_clock=clock.monotonic,
    ) as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> NoReturn:
            # Model an uninterruptible call returning after the old 30 second lease boundary.
            clock.wall_ms = 31_000
            clock.monotonic_ms = 31_000
            assert probe.record.status == "inspecting"
            raise OptimizationExecutionError("backend_failure")

        result = execute_optimization_job(repository, record.job_id, request, OWNER, executor)
        assert result.failure_code == "backend_failure"
        assert result.usage.runtime_ms == 31_000


def test_late_heartbeat_cannot_extend_lease_past_runtime_deadline(tmp_path: Path) -> None:
    request = optimization_request(runtime_ms=1_000)
    clock = Clock()
    with OptimizationJobRepository(
        tmp_path / "bounded-heartbeat.sqlite3",
        clock=clock.wall,
        monotonic_clock=clock.monotonic,
    ) as repository:
        record = repository.create(request, OWNER)
        lease = repository.claim(record.job_id, request, OWNER, lease_ms=1_000)
        probe = OptimizationExecutionProbe(repository, lease, request, OWNER)

        clock.wall_ms = 900
        clock.monotonic_ms = 900
        probe.checkpoint()
        assert probe._lease.lease_ms == 100
        assert probe._lease.expires_at_ms == 1_000
        assert probe._lease.lease_ms <= lease.lease_ms

        clock.wall_ms = 950
        clock.monotonic_ms = 950
        probe.checkpoint()
        assert probe._lease.lease_ms == 50
        assert probe._lease.expires_at_ms == 1_000

        clock.wall_ms = 1_000
        clock.monotonic_ms = 1_000
        with pytest.raises(OptimizationExecutionError, match="optimization execution failed"):
            probe.checkpoint()
        recovered = repository.recover_interrupted(record.job_id, OWNER)
        assert recovered.failure_code == "interrupted"


def test_monotonic_remaining_time_never_increases(tmp_path: Path) -> None:
    request = optimization_request(runtime_ms=20)
    package = optimization_package(request)
    clock = Clock(wall_ms=0, monotonic_ms=100)
    with OptimizationJobRepository(
        tmp_path / "clock.sqlite3", clock=clock.wall, monotonic_clock=clock.monotonic
    ) as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            assert probe.remaining_time_ms() == 20
            clock.monotonic_ms = 105
            assert probe.remaining_time_ms() == 15
            clock.monotonic_ms = 103
            assert probe.remaining_time_ms() == 15
            advance_to_judging(probe)
            return package

        assert (
            execute_optimization_job(repository, record.job_id, request, OWNER, executor).status
            == "awaiting_approval"
        )


def test_absolute_deadline_includes_preparation_and_queue_time(tmp_path: Path) -> None:
    request = optimization_request(runtime_ms=20)
    clock = Clock(wall_ms=0, monotonic_ms=125)
    called = False
    with OptimizationJobRepository(
        tmp_path / "deadline.sqlite3", clock=clock.wall, monotonic_clock=clock.monotonic
    ) as repository:
        record = repository.create(request, OWNER)

        def executor(_probe: OptimizationExecutionProbe) -> OptimizationPackage:
            nonlocal called
            called = True
            return optimization_package(request)

        # Equivalent to a PreparedOptimization captured at monotonic 0 with a 20 ms limit.
        result = execute_optimization_job(
            repository,
            record.job_id,
            request,
            OWNER,
            executor,
            absolute_deadline_ms=20,
        )
        assert result.failure_code == "budget_exhausted"
        assert called is False


def test_initial_elapsed_runtime_is_billed_once(tmp_path: Path) -> None:
    request = optimization_request(runtime_ms=100)
    package = optimization_package(request)
    clock = Clock(wall_ms=0, monotonic_ms=40)
    with OptimizationJobRepository(
        tmp_path / "initial-runtime.sqlite3",
        clock=clock.wall,
        monotonic_clock=clock.monotonic,
    ) as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            assert probe.record.usage.runtime_ms == 40
            probe.advance("placing")
            assert probe.record.usage.runtime_ms == 40
            clock.monotonic_ms = 45
            probe.reserve(ResourceUsage(placement_evaluations=1))
            assert probe.record.usage.runtime_ms == 45
            probe.advance("routing")
            probe.advance("judging")
            return package

        result = execute_optimization_job(
            repository,
            record.job_id,
            request,
            OWNER,
            executor,
            absolute_deadline_ms=100,
        )
        assert result.status == "awaiting_approval"
        assert result.usage.runtime_ms == 45


def test_executor_exception_is_redacted_backend_failure(tmp_path: Path) -> None:
    request = optimization_request()
    path = tmp_path / "failure.sqlite3"
    with OptimizationJobRepository(path) as repository:
        record = repository.create(request, OWNER)

        def executor(_probe: OptimizationExecutionProbe) -> NoReturn:
            raise RuntimeError(f"{PRIVATE_CANARY}: /private/board.kicad_pcb")

        result = execute_optimization_job(repository, record.job_id, request, OWNER, executor)
        assert result.status == "backend_failure"
        assert result.failure_code == "backend_failure"

    database = path.read_bytes()
    assert PRIVATE_CANARY.encode() not in database
    assert b"board.kicad_pcb" not in database


def test_executor_can_close_changed_source_or_rules_as_stale_revision(tmp_path: Path) -> None:
    request = optimization_request()
    path = tmp_path / "stale-revision.sqlite3"
    with OptimizationJobRepository(path) as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> NoReturn:
            probe.reserve(ResourceUsage(obstacle_checks=1))
            raise OptimizationExecutionError("stale_revision")

        result = execute_optimization_job(repository, record.job_id, request, OWNER, executor)
        assert result.status == "stale_revision"
        assert result.failure_code == "stale_revision"
        assert result.usage.obstacle_checks == 1
        assert result.package_digest is None
        with pytest.raises(OptimizationJobUnavailableError):
            repository.get_package(record.job_id, OWNER)


def test_invalid_package_does_not_reach_awaiting_approval(tmp_path: Path) -> None:
    request = optimization_request()
    with OptimizationJobRepository(tmp_path / "invalid.sqlite3") as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            advance_to_judging(probe)
            return cast(OptimizationPackage, object())

        result = execute_optimization_job(repository, record.job_id, request, OWNER, executor)
        assert result.failure_code == "invalid_candidate"
        assert result.package_digest is None


def test_failed_judge_package_uses_lifecycle_classification(tmp_path: Path) -> None:
    request = optimization_request()
    package = optimization_package(request)
    failed_domain = package.judge.domains[0]
    assert failed_domain.evidence is not None
    failed_sample = type(failed_domain.evidence.samples[0])(
        verdict="fail",
        normalized_result_digest=failed_domain.evidence.samples[0].normalized_result_digest,
    )
    failed_evidence = type(failed_domain.evidence).model_validate(
        {
            **failed_domain.evidence.model_dump(),
            "samples": (failed_sample, failed_sample),
        }
    )
    failed_row = type(failed_domain)(
        domain=failed_domain.domain,
        status="fail",
        reason="check_failed",
        evidence=failed_evidence,
    )
    failed_judge = type(package.judge).model_validate(
        {
            **package.judge.model_dump(),
            "domains": (failed_row, *package.judge.domains[1:]),
        }
    )
    failed_package = OptimizationPackage.model_validate(
        {**package.model_dump(), "judge": failed_judge}
    )
    with OptimizationJobRepository(tmp_path / "judge-failed.sqlite3") as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            advance_to_judging(probe)
            return failed_package

        result = execute_optimization_job(repository, record.job_id, request, OWNER, executor)
        assert result.failure_code == "judge_failed"
        assert result.package_digest is None


def test_package_byte_limit_fails_closed_without_publishing(tmp_path: Path) -> None:
    request = optimization_request()
    package = optimization_package(request)
    with OptimizationJobRepository(
        tmp_path / "package-bytes.sqlite3", max_package_bytes=64
    ) as repository:
        record = repository.create(request, OWNER)

        def executor(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            advance_to_judging(probe)
            return package

        result = execute_optimization_job(repository, record.job_id, request, OWNER, executor)
        assert result.failure_code == "invalid_candidate"
        assert result.package_digest is None


__all__: tuple[str, ...] = ()
