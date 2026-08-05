from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.routing import (
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteCandidate,
    LayeredRouteRequest,
)
from copper_mcp.routing.jobs import (
    RoutingJobConflictError,
    RoutingJobFailureCode,
    RoutingJobKind,
    RoutingJobLimitError,
    RoutingJobLimits,
    RoutingJobNotFoundError,
    RoutingJobRecord,
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


def test_spec_is_content_addressed_and_record_round_trips_without_geometry() -> None:
    _, spec = _candidate_and_spec()
    assert spec == RoutingJobSpec.from_dict(spec.to_dict())
    assert spec.job_id == RoutingJobSpec.from_dict(spec.to_dict()).job_id

    record = RoutingJobRecord.create(spec, now_ms=10)
    encoded = record.to_json()
    restored = RoutingJobRecord.from_json(encoded)
    assert restored == record
    assert b"vertices" not in encoded
    assert b"board_revision" in encoded
    assert json.loads(encoded)["candidate_id"] is None


def test_start_requires_compare_and_swap_and_completion_binds_candidate_revision() -> None:
    candidate, spec = _candidate_and_spec()
    queued = RoutingJobRecord.create(spec, now_ms=10)
    running = queued.start(expected_revision=0, now_ms=20)
    assert running.status is RoutingJobStatus.RUNNING
    assert running.attempt == 1
    with pytest.raises(RoutingJobConflictError):
        running.request_cancel(expected_revision=0, now_ms=21)

    completed = running.complete(candidate, expected_revision=running.revision, now_ms=30)
    assert completed.status is RoutingJobStatus.COMPLETED
    assert completed.candidate_id == candidate.candidate_id
    assert completed.candidate_base_revision == candidate.base_revision
    with pytest.raises(RoutingJobStateError):
        completed.start(expected_revision=completed.revision, now_ms=31)


def test_completion_binds_kind_router_policy_seed_and_work_limits() -> None:
    candidate, spec = _candidate_and_spec()
    mismatched = RoutingJobSpec.create(
        board_revision=spec.board_revision,
        snapshot_digest=spec.snapshot_digest,
        start_pad_id=spec.start_pad_id,
        end_pad_id=spec.end_pad_id,
        request_digest=spec.request_digest,
        request_kind=RoutingJobKind.SINGLE_LAYER,
        backend=spec.backend,
        router_version=spec.router_version,
        policy=spec.policy,
        seed=spec.seed,
        limits=spec.limits,
    )
    running = RoutingJobRecord.create(mismatched).start(expected_revision=0, now_ms=1)
    with pytest.raises(ValueError, match="candidate kind"):
        running.complete(candidate, expected_revision=running.revision, now_ms=2)

    mismatched = RoutingJobSpec.create(
        board_revision=spec.board_revision,
        snapshot_digest=spec.snapshot_digest,
        start_pad_id=spec.start_pad_id,
        end_pad_id=spec.end_pad_id,
        request_digest=spec.request_digest,
        request_kind=spec.request_kind,
        backend=spec.backend,
        router_version=spec.router_version,
        policy=spec.policy,
        seed=spec.seed + 1,
        limits=spec.limits,
    )
    running = RoutingJobRecord.create(mismatched).start(expected_revision=0, now_ms=3)
    with pytest.raises(ValueError, match="candidate seed"):
        running.complete(candidate, expected_revision=running.revision, now_ms=4)


def test_cancellation_is_cooperative_and_wins_over_candidate_publication() -> None:
    candidate, spec = _candidate_and_spec()
    running = RoutingJobRecord.create(spec, now_ms=10).start(expected_revision=0, now_ms=20)
    requested = running.request_cancel(expected_revision=running.revision, now_ms=30)
    assert requested.status is RoutingJobStatus.CANCEL_REQUESTED
    with pytest.raises(RoutingJobStateError):
        requested.complete(candidate, expected_revision=requested.revision, now_ms=31)
    cancelled = requested.acknowledge_cancel(expected_revision=requested.revision, now_ms=40)
    assert cancelled.status is RoutingJobStatus.CANCELLED
    assert cancelled.diagnostic_code is RoutingJobFailureCode.CANCELLED
    assert cancelled.cancel_reason == "caller_requested"


def test_queued_cancellation_never_consumes_an_attempt() -> None:
    _, spec = _candidate_and_spec()
    cancelled = RoutingJobRecord.create(spec, now_ms=10).request_cancel(
        expected_revision=0, now_ms=11, reason="operator_stop"
    )
    assert cancelled.status is RoutingJobStatus.CANCELLED
    assert cancelled.attempt == 0
    assert cancelled.revision == 1


def test_candidate_from_a_different_snapshot_is_rejected() -> None:
    candidate, spec = _candidate_and_spec()
    no_snapshot_spec = RoutingJobSpec.create(
        board_revision=spec.board_revision,
        snapshot_digest=None,
        start_pad_id=spec.start_pad_id,
        end_pad_id=spec.end_pad_id,
        request_digest=spec.request_digest,
        request_kind=spec.request_kind,
        backend=spec.backend,
        router_version=spec.router_version,
        policy=spec.policy,
        seed=spec.seed,
        limits=spec.limits,
    )
    running = RoutingJobRecord.create(no_snapshot_spec).start(expected_revision=0, now_ms=1)
    with pytest.raises(ValueError, match="base revision"):
        running.complete(candidate, expected_revision=running.revision, now_ms=2)


def test_record_decoder_rejects_extra_fields_and_tampered_job_id() -> None:
    _, spec = _candidate_and_spec()
    payload = RoutingJobRecord.create(spec).to_dict()
    payload["unexpected"] = "nope"
    with pytest.raises(ValueError, match="closed object"):
        RoutingJobRecord.from_dict(payload)

    clean = RoutingJobRecord.create(spec).to_dict()
    clean["job_id"] = _digest("d")
    with pytest.raises(ValueError, match="does not match"):
        RoutingJobRecord.from_dict(clean)


def test_limits_reject_boolean_and_unbounded_values() -> None:
    with pytest.raises(ValueError):
        RoutingJobLimits(max_attempts=True)
    with pytest.raises(ValueError):
        RoutingJobLimits(max_runtime_ms=86_400_001)


def test_sqlite_store_reopens_queued_running_and_terminal_records(tmp_path: Path) -> None:
    candidate, spec = _candidate_and_spec()
    failed_spec = RoutingJobSpec.create(
        board_revision=spec.board_revision,
        snapshot_digest=spec.snapshot_digest,
        start_pad_id=spec.start_pad_id,
        end_pad_id=spec.end_pad_id,
        request_digest=spec.request_digest,
        request_kind=spec.request_kind,
        backend=spec.backend,
        router_version=spec.router_version,
        policy=spec.policy,
        seed=spec.seed + 1,
        limits=spec.limits,
    )
    running_spec = RoutingJobSpec.create(
        board_revision=spec.board_revision,
        snapshot_digest=spec.snapshot_digest,
        start_pad_id=spec.start_pad_id,
        end_pad_id=spec.end_pad_id,
        request_digest=spec.request_digest,
        request_kind=spec.request_kind,
        backend=spec.backend,
        router_version=spec.router_version,
        policy=spec.policy,
        seed=spec.seed + 2,
        limits=spec.limits,
    )
    path = tmp_path / "routing-jobs.sqlite3"
    with RoutingJobStore(path, ttl_ms=10_000) as store:
        queued = store.create(spec, now_ms=100)
        running = store.create(failed_spec, now_ms=101)
        running = store.start(running.spec.job_id, expected_revision=0, now_ms=102)
        running_record = store.create(running_spec, now_ms=101)
        running_record = store.start(running_record.spec.job_id, expected_revision=0, now_ms=102)
        failed = store.fail(
            running.spec.job_id,
            RoutingJobFailureCode.NO_PATH,
            "no legal path within budget",
            expected_revision=running.revision,
            now_ms=103,
        )
        assert failed.diagnostic_message == "routing search found no path"
        completed_running = store.start(queued.spec.job_id, expected_revision=0, now_ms=104)
        completed = store.complete(
            queued.spec.job_id,
            candidate,
            expected_revision=completed_running.revision,
            now_ms=105,
        )
        assert store.get(queued.spec.job_id, now_ms=106) == completed
        assert store.get(failed.spec.job_id, now_ms=106) == failed
        assert store.get(running_record.spec.job_id, now_ms=106) == running_record

    with RoutingJobStore(path, ttl_ms=10_000) as reopened:
        assert reopened.get(queued.spec.job_id, now_ms=107) == completed
        assert reopened.get(failed.spec.job_id, now_ms=107) == failed
        assert reopened.get(running_record.spec.job_id, now_ms=107) == running_record
        # A separate queued record proves that non-terminal state survives a process boundary.
        queued_again_spec = RoutingJobSpec.create(
            board_revision=spec.board_revision,
            snapshot_digest=spec.snapshot_digest,
            start_pad_id=spec.start_pad_id,
            end_pad_id=spec.end_pad_id,
            request_digest=spec.request_digest,
            request_kind=spec.request_kind,
            backend=spec.backend,
            router_version=spec.router_version,
            policy=spec.policy,
            seed=spec.seed + 3,
            limits=spec.limits,
        )
        queued_again = reopened.create(queued_again_spec, now_ms=108)
        assert queued_again.status is RoutingJobStatus.QUEUED
        reopened.close()

    with RoutingJobStore(path, ttl_ms=10_000) as reopened_again:
        assert reopened_again.get(queued_again_spec.job_id, now_ms=109) == queued_again


def test_sqlite_create_is_idempotent_and_capacity_is_bounded(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    path = tmp_path / "capacity.sqlite3"
    with RoutingJobStore(path, max_records=1, ttl_ms=100) as store:
        first = store.create(spec, now_ms=10)
        assert store.create(spec, now_ms=99) == first
        second_spec = RoutingJobSpec.create(
            board_revision=spec.board_revision,
            snapshot_digest=spec.snapshot_digest,
            start_pad_id=spec.start_pad_id,
            end_pad_id=spec.end_pad_id,
            request_digest=spec.request_digest,
            request_kind=spec.request_kind,
            backend=spec.backend,
            router_version=spec.router_version,
            policy=spec.policy,
            seed=spec.seed + 10,
            limits=spec.limits,
        )
        with pytest.raises(RoutingJobLimitError, match="capacity"):
            store.create(second_spec, now_ms=99)


def test_sqlite_expiry_and_unknown_ids_share_one_not_found_error(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    path = tmp_path / "expiry.sqlite3"
    unknown = _digest("e")
    with RoutingJobStore(path, ttl_ms=10) as store:
        store.create(spec, now_ms=100)
        with pytest.raises(RoutingJobNotFoundError) as unknown_error:
            store.get(unknown, now_ms=101)
        with pytest.raises(RoutingJobNotFoundError) as expired_error:
            store.get(spec.job_id, now_ms=110)
        assert str(unknown_error.value) == str(expired_error.value)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM routing_jobs").fetchone() == (0,)
    connection.close()
    with RoutingJobStore(path, ttl_ms=10) as store:
        recreated = store.create(spec, now_ms=111)
        assert recreated.created_at_ms == 111


def test_sqlite_invalid_get_durably_purges_expired_rows(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    path = tmp_path / "invalid-get-expiry.sqlite3"
    with RoutingJobStore(path, ttl_ms=10) as store:
        store.create(spec, now_ms=100)
        with pytest.raises(RoutingJobNotFoundError):
            store.get("malformed-job-id", now_ms=110)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM routing_jobs").fetchone() == (0,)
    connection.close()


def test_sqlite_mutations_enforce_revision_cas_and_are_thread_safe(tmp_path: Path) -> None:
    _, spec = _candidate_and_spec()
    path = tmp_path / "cas.sqlite3"
    with RoutingJobStore(path, ttl_ms=10_000) as store:
        queued = store.create(spec, now_ms=10)
        running = store.start(spec.job_id, expected_revision=queued.revision, now_ms=11)
        with pytest.raises(RoutingJobConflictError):
            store.request_cancel(spec.job_id, expected_revision=queued.revision, now_ms=12)

        def cancel() -> RoutingJobRecord:
            return store.request_cancel(
                spec.job_id,
                expected_revision=running.revision,
                reason="race",
                now_ms=13,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: _attempt(cancel), range(2)))
        successes = [result for result in results if isinstance(result, RoutingJobRecord)]
        conflicts = [result for result in results if isinstance(result, RoutingJobConflictError)]
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert successes[0].status is RoutingJobStatus.CANCEL_REQUESTED


def _attempt(operation: Callable[[], RoutingJobRecord]) -> object:
    try:
        return operation()
    except RoutingJobConflictError as error:
        return error


def test_sqlite_payload_never_contains_board_bytes_or_candidate_geometry(tmp_path: Path) -> None:
    candidate, spec = _candidate_and_spec()
    path = tmp_path / "redacted.sqlite3"
    with RoutingJobStore(path, ttl_ms=10_000) as store:
        running = store.start(
            spec.job_id,
            expected_revision=store.create(spec, now_ms=10).revision,
            now_ms=11,
        )
        store.complete(spec.job_id, candidate, expected_revision=running.revision, now_ms=12)
    with sqlite3.connect(path) as connection:
        payload = connection.execute("SELECT record_json FROM routing_jobs").fetchone()[0]
    assert b"vertices" not in payload
    assert FIXTURE.read_bytes() not in payload
    assert candidate.candidate_id.encode("ascii") in payload
