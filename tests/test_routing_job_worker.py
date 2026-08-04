from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NoReturn, cast

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.routing import (
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteCandidate,
    LayeredRouteRequest,
)
from copper_mcp.routing.job_worker import (
    CancellationProbe,
    RoutingJobAlreadyClaimedError,
    RoutingJobCancelledError,
    RoutingJobExecutionError,
    RoutingJobLeaseExpiredError,
    RoutingJobWorker,
    WorkerLimits,
)
from copper_mcp.routing.jobs import (
    RoutingJobConflictError,
    RoutingJobFailureCode,
    RoutingJobKind,
    RoutingJobLimits,
    RoutingJobSpec,
    RoutingJobStateError,
    RoutingJobStatus,
    RoutingJobStore,
)

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
F_CU = "layer:F.Cu"


def _digest(fill: str = "a") -> str:
    return f"sha256:{fill * 64}"


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _candidate_and_spec() -> tuple[LayeredRouteCandidate, RoutingJobSpec]:
    conversion = parse_kicad_bytes(FIXTURE.read_bytes(), _profile())
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    pads = tuple(
        pad for pad in snapshot.content.pads if pad.net_id == snapshot.content.pads[0].net_id
    )
    assert pads[0].net_id is not None
    request = LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=pads[0].net_id,
        start_pad_id=pads[0].id,
        end_pad_id=pads[1].id,
        start_layer_id=F_CU,
        end_layer_id=F_CU,
        grid_step_nm=1_000,
        settings=LayeredAStarSettings(via_cost=2),
    )
    result = LayeredBoardRouter().propose(snapshot, request)
    assert result.candidate is not None
    candidate = result.candidate
    spec = RoutingJobSpec.create(
        board_revision=_digest("b"),
        snapshot_digest=snapshot.snapshot_digest,
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        request_digest=_digest("c"),
        request_kind=RoutingJobKind.LAYERED,
        backend="board-layered-a-star-v1",
        router_version=candidate.router_version,
        policy=candidate.policy,
        seed=candidate.seed,
        limits=RoutingJobLimits(max_runtime_ms=60_000, max_attempts=2),
    )
    return candidate, spec


class _FakeClock:
    def __init__(self, now_ms: int = 0) -> None:
        self.now_ms = now_ms
        self.sleeps: list[float] = []

    def now(self) -> int:
        return self.now_ms

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now_ms += max(1, round(seconds * 1_000))


def test_claim_race_allows_only_one_worker(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    with RoutingJobStore(tmp_path / "race.sqlite3", ttl_ms=10_000) as store:
        store.create(spec, now_ms=10)
        workers = [
            RoutingJobWorker(store, clock=lambda: 20),
            RoutingJobWorker(store, clock=lambda: 20),
        ]

        def attempt(worker: RoutingJobWorker) -> object:
            try:
                return worker.claim(spec.job_id)
            except (RoutingJobAlreadyClaimedError, ValueError) as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, workers))
        assert sum(result.__class__.__name__ == "RoutingJobLease" for result in results) == 1
        assert (
            sum(
                isinstance(result, RoutingJobAlreadyClaimedError | RoutingJobConflictError)
                for result in results
            )
            == 1
        )


def test_expired_lease_is_recovered_as_bounded_worker_failure(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    clock = _FakeClock(10)
    with RoutingJobStore(tmp_path / "expiry.sqlite3", ttl_ms=10_000) as store:
        store.create(spec, now_ms=clock.now())
        first = RoutingJobWorker(store, limits=WorkerLimits(lease_ms=10), clock=clock.now)
        first.claim(spec.job_id)
        clock.now_ms = 20
        second = RoutingJobWorker(store, limits=WorkerLimits(lease_ms=10), clock=clock.now)
        recovered = second.recover_expired(spec.job_id)
        assert recovered.status is RoutingJobStatus.FAILED
        assert recovered.diagnostic_code is RoutingJobFailureCode.WORKER_ERROR
        with pytest.raises(RoutingJobStateError):
            second.claim(spec.job_id)


def test_expired_lease_acknowledges_a_pending_cancellation(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    clock = _FakeClock(10)
    with RoutingJobStore(tmp_path / "cancel-expiry.sqlite3", ttl_ms=10_000) as store:
        store.create(spec, now_ms=clock.now())
        first = RoutingJobWorker(store, limits=WorkerLimits(lease_ms=10), clock=clock.now)
        lease = first.claim(spec.job_id)
        requested = store.request_cancel(
            spec.job_id,
            expected_revision=lease.revision,
            now_ms=clock.now(),
        )
        assert requested.status is RoutingJobStatus.CANCEL_REQUESTED
        clock.now_ms = 20
        second = RoutingJobWorker(store, limits=WorkerLimits(lease_ms=10), clock=clock.now)
        recovered = second.recover_expired(spec.job_id)
        assert recovered.status is RoutingJobStatus.CANCELLED
        assert recovered.diagnostic_code is RoutingJobFailureCode.CANCELLED


def test_claim_closes_and_reports_an_expired_running_lease(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    clock = _FakeClock(10)
    with RoutingJobStore(tmp_path / "claim-expiry.sqlite3", ttl_ms=10_000) as store:
        store.create(spec, now_ms=clock.now())
        first = RoutingJobWorker(store, limits=WorkerLimits(lease_ms=10), clock=clock.now)
        first.claim(spec.job_id)
        clock.now_ms = 20
        second = RoutingJobWorker(store, limits=WorkerLimits(lease_ms=10), clock=clock.now)
        with pytest.raises(RoutingJobLeaseExpiredError):
            second.claim(spec.job_id)
        assert store.get(spec.job_id, now_ms=clock.now()).status is RoutingJobStatus.FAILED


def test_recovery_rejects_a_live_lease(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    clock = _FakeClock(10)
    with RoutingJobStore(tmp_path / "live.sqlite3", ttl_ms=10_000) as store:
        store.create(spec, now_ms=clock.now())
        worker = RoutingJobWorker(store, limits=WorkerLimits(lease_ms=10), clock=clock.now)
        worker.claim(spec.job_id)
        with pytest.raises(RoutingJobLeaseExpiredError):
            worker.recover_expired(spec.job_id)


def test_cooperative_cancellation_wins_over_candidate_publication(tmp_path: Path) -> None:
    candidate, spec = _candidate_and_spec()
    clock = _FakeClock(10)
    with RoutingJobStore(tmp_path / "cancel.sqlite3", ttl_ms=10_000) as store:
        store.create(spec, now_ms=clock.now())
        worker = RoutingJobWorker(store, clock=clock.now)

        def executor(probe: CancellationProbe) -> LayeredRouteCandidate:
            lease = worker.active_lease
            assert lease is not None
            store.request_cancel(
                spec.job_id,
                expected_revision=lease.revision,
                now_ms=clock.now(),
                reason="test_stop",
            )
            assert probe.is_cancelled()
            return candidate

        result = worker.execute(spec.job_id, executor)
        assert result.status is RoutingJobStatus.CANCELLED
        assert result.cancel_reason == "test_stop"
        assert result.candidate_id is None


def test_success_publishes_only_candidate_identity_through_cas_store(tmp_path: Path) -> None:
    candidate, spec = _candidate_and_spec()
    clock = _FakeClock(10)
    db_path = tmp_path / "success.sqlite3"
    with RoutingJobStore(db_path, ttl_ms=10_000) as store:
        store.create(spec, now_ms=clock.now())
        worker = RoutingJobWorker(store, clock=clock.now)
        result = worker.execute(spec.job_id, lambda _probe: candidate)
        assert result.status is RoutingJobStatus.COMPLETED
        assert result.candidate_id == candidate.candidate_id
        assert worker.active_lease is None
    with sqlite3.connect(db_path) as connection:
        payload = connection.execute("SELECT record_json FROM routing_jobs").fetchone()[0]
    assert b"vertices" not in payload
    assert FIXTURE.read_bytes() not in payload
    assert candidate.candidate_id.encode("ascii") in payload


def test_bounded_executor_failure_maps_to_failed_status(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    clock = _FakeClock(10)
    with RoutingJobStore(tmp_path / "failure.sqlite3", ttl_ms=10_000) as store:
        store.create(spec, now_ms=clock.now())
        worker = RoutingJobWorker(store, clock=clock.now)
        result = worker.execute(
            spec.job_id,
            lambda _probe: _raise(
                RoutingJobExecutionError(
                    RoutingJobFailureCode.NO_PATH, "private board and prompt details"
                )
            ),
        )
        assert result.status is RoutingJobStatus.FAILED
        assert result.diagnostic_code is RoutingJobFailureCode.NO_PATH
        assert result.diagnostic_message == "routing search found no path"


def test_invalid_candidate_output_maps_to_bounded_request_failure(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    clock = _FakeClock(10)
    with RoutingJobStore(tmp_path / "invalid-candidate.sqlite3", ttl_ms=10_000) as store:
        store.create(spec, now_ms=clock.now())
        worker = RoutingJobWorker(store, clock=clock.now)
        result = worker.execute(
            spec.job_id,
            lambda _probe: cast(LayeredRouteCandidate, object()),
        )
        assert result.status is RoutingJobStatus.FAILED
        assert result.diagnostic_code is RoutingJobFailureCode.INVALID_REQUEST
        assert result.diagnostic_message == "routing executor returned an invalid candidate"


def test_executor_cancellation_exception_acknowledges_requested_cancel(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    clock = _FakeClock(10)
    with RoutingJobStore(tmp_path / "cancel-error.sqlite3", ttl_ms=10_000) as store:
        store.create(spec, now_ms=clock.now())
        worker = RoutingJobWorker(store, clock=clock.now)

        def executor(_probe: CancellationProbe) -> LayeredRouteCandidate:
            lease = worker.active_lease
            assert lease is not None
            store.request_cancel(spec.job_id, expected_revision=lease.revision, now_ms=clock.now())
            raise RoutingJobCancelledError()

        result = worker.execute(spec.job_id, executor)
        assert result.status is RoutingJobStatus.CANCELLED


def test_probe_wait_uses_injected_clock_and_sleep(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    clock = _FakeClock(10)
    with RoutingJobStore(tmp_path / "probe.sqlite3", ttl_ms=10_000) as store:
        store.create(spec, now_ms=clock.now())
        worker = RoutingJobWorker(store, clock=clock.now, sleep=clock.sleep)
        lease = worker.claim(spec.job_id)
        probe = CancellationProbe(worker, lease)
        assert probe.wait(60) is False
        assert clock.sleeps


def _raise(error: Exception) -> NoReturn:
    raise error


__all__: tuple[str, ...] = ()
