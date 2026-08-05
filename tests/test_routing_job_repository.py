from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.routing import (
    CandidateManifestNotFoundError,
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteCandidate,
    LayeredRouteRequest,
    RoutingJobKind,
    RoutingJobLimits,
    RoutingJobRepository,
    RoutingJobRequestEnvelope,
    RoutingJobRequestStore,
)
from copper_mcp.routing.job_repository import (
    RoutingCandidateExportUnavailableError,
    RoutingJobRequestUnavailableError,
)
from copper_mcp.routing.jobs import (
    RoutingJobConflictError,
    RoutingJobError,
    RoutingJobFailureCode,
    RoutingJobSpec,
    RoutingJobStateError,
    RoutingJobStatus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
F_CU = "layer:F.Cu"


def _digest(fill: str) -> str:
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


def _candidate_and_spec() -> tuple[LayeredRouteCandidate, RoutingJobSpec, dict[str, object], str]:
    conversion = parse_kicad_bytes(FIXTURE.read_bytes(), _profile())
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    pads = tuple(
        pad for pad in snapshot.content.pads if pad.net_id == snapshot.content.pads[0].net_id
    )
    assert len(pads) == 2
    assert pads[0].net_id is not None
    route = LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=pads[0].net_id,
        start_pad_id=pads[0].id,
        end_pad_id=pads[1].id,
        start_layer_id=F_CU,
        end_layer_id=F_CU,
        grid_step_nm=1_000,
        settings=LayeredAStarSettings(via_cost=2),
    )
    result = LayeredBoardRouter().propose(snapshot, route)
    assert result.candidate is not None
    candidate = result.candidate
    request: dict[str, object] = {
        "board": "tests/fixtures/route-candidate/blocked-pad.kicad_pcb",
        "start_pad_id": candidate.start_pad_id,
        "end_pad_id": candidate.end_pad_id,
        "expect_board_revision": _digest("b"),
        "expect_snapshot_digest": candidate.base_revision,
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
        "grid_step_nm": 1_000,
        "seed": candidate.seed,
        "settings": {
            "move_cost": 1,
            "via_cost": 2,
            "max_expansions": 100_000,
            "max_nodes": 250_000,
            "max_obstacles": 256,
            "max_obstacle_checks": 2_000_000,
        },
        "start_layer_id": "F.Cu",
        "end_layer_id": "F.Cu",
    }
    request_bytes = json.dumps(request, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    request_digest = f"sha256:{hashlib.sha256(request_bytes).hexdigest()}"
    spec = RoutingJobSpec.create(
        board_revision=_digest("b"),
        snapshot_digest=candidate.base_revision,
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        request_digest=request_digest,
        request_kind=RoutingJobKind.LAYERED,
        backend=candidate.router_version,
        router_version=candidate.router_version,
        policy=candidate.policy,
        seed=candidate.seed,
        limits=RoutingJobLimits(
            max_runtime_ms=60_000,
            max_expansions=candidate.settings.max_expansions,
            max_obstacle_checks=candidate.settings.max_obstacle_checks,
        ),
    )
    return candidate, spec, request, _digest("e")


def _completion_mismatch(
    spec: RoutingJobSpec,
    candidate: LayeredRouteCandidate,
    kind: str,
) -> RoutingJobSpec:
    request_kind = spec.request_kind
    router_version = spec.router_version
    policy = spec.policy
    seed = spec.seed
    limits = spec.limits
    if kind == "kind":
        request_kind = RoutingJobKind.SINGLE_LAYER
    elif kind == "router":
        router_version = "different-router-v1"
    elif kind == "policy":
        policy = "different-policy-v1"
    elif kind == "seed":
        seed += 1
    elif kind == "metrics":
        if candidate.metrics.expanded_states > 1:
            limits = RoutingJobLimits(
                max_runtime_ms=limits.max_runtime_ms,
                max_attempts=limits.max_attempts,
                max_expansions=candidate.metrics.expanded_states - 1,
                max_obstacle_checks=limits.max_obstacle_checks,
            )
        else:
            assert candidate.metrics.obstacle_checks > 1
            limits = RoutingJobLimits(
                max_runtime_ms=limits.max_runtime_ms,
                max_attempts=limits.max_attempts,
                max_expansions=limits.max_expansions,
                max_obstacle_checks=candidate.metrics.obstacle_checks - 1,
            )
    else:  # pragma: no cover - parameterization below owns the closed mismatch set
        raise AssertionError(f"unsupported completion mismatch {kind!r}")
    return RoutingJobSpec.create(
        board_revision=spec.board_revision,
        snapshot_digest=spec.snapshot_digest,
        start_pad_id=spec.start_pad_id,
        end_pad_id=spec.end_pad_id,
        request_digest=spec.request_digest,
        request_kind=request_kind,
        backend=spec.backend,
        router_version=router_version,
        policy=policy,
        seed=seed,
        limits=limits,
    )


def _candidate_artifact_counts(path: Path) -> tuple[int, int]:
    with sqlite3.connect(path) as connection:
        export_count = connection.execute(
            "SELECT COUNT(*) FROM routing_candidate_exports"
        ).fetchone()
        manifest_count = connection.execute(
            "SELECT COUNT(*) FROM routing_candidate_manifests"
        ).fetchone()
    assert export_count is not None
    assert manifest_count is not None
    return int(export_count[0]), int(manifest_count[0])


def test_request_store_reopens_and_binds_authorization_without_board_content(
    tmp_path: Path,
) -> None:
    _, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / "routing.sqlite3"
    envelope = RoutingJobRequestEnvelope.create(
        spec=spec,
        request=request,
        authorization_digest=authorization,
        now_ms=100,
        expires_at_ms=1_100,
    )
    with RoutingJobRequestStore(path, ttl_ms=1_000) as store:
        assert store.put(envelope) == envelope
        assert store.get(spec.job_id, authorization, now_ms=101).request == request
        request["settings"]["move_cost"] = 999  # type: ignore[index]
        stored_request = store.get(spec.job_id, authorization, now_ms=101).request
        assert stored_request["settings"]["move_cost"] == 1  # type: ignore[index]
        with pytest.raises(RoutingJobRequestUnavailableError):
            store.get(spec.job_id, _digest("f"), now_ms=101)

    with RoutingJobRequestStore(path, ttl_ms=1_000) as reopened:
        assert reopened.get(spec.job_id, authorization, now_ms=101) == envelope
    raw = (
        sqlite3.connect(path).execute("SELECT request_json FROM routing_job_requests").fetchone()[0]
    )
    assert isinstance(raw, bytes)
    assert b"board_bytes" not in raw
    assert b"prompt" not in raw
    assert b"credentials" not in raw


def test_repository_publishes_redacted_manifest_and_explicit_geometry_export(
    tmp_path: Path,
) -> None:
    candidate, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / "routing.sqlite3"
    with RoutingJobRepository(path) as repository:
        queued = repository.create(spec, request, authorization, now_ms=100)
        assert queued.status is RoutingJobStatus.QUEUED
        running = repository.jobs.start(spec.job_id, expected_revision=queued.revision, now_ms=101)
        completed = repository.publish_candidate(
            spec.job_id,
            candidate,
            expected_revision=running.revision,
            authorization_digest=authorization,
            now_ms=102,
        )
        assert completed.status is RoutingJobStatus.COMPLETED
        assert completed.candidate_id == candidate.candidate_id
        record, envelope = repository.get(spec.job_id, authorization, now_ms=103)
        assert record == completed
        assert envelope.request_digest == spec.request_digest
        exported = repository.exports.get(
            spec.job_id,
            candidate.candidate_id,
            authorization,
            now_ms=103,
        )
        assert exported["candidate_id"] == candidate.candidate_id
        assert "vertices" in json.dumps(exported)
        assert repository.manifests.get(candidate.candidate_id, now_ms=103).job_id == spec.job_id
        with pytest.raises(RoutingCandidateExportUnavailableError):
            repository.exports.get(spec.job_id, candidate.candidate_id, _digest("f"), now_ms=103)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE routing_candidate_exports SET candidate_json = ? WHERE candidate_id = ?",
                (
                    sqlite3.Binary(b'{"candidate_id":"' + candidate.candidate_id.encode() + b'"}'),
                    candidate.candidate_id,
                ),
            )
        with pytest.raises(RoutingJobError):
            repository.exports.get(spec.job_id, candidate.candidate_id, authorization, now_ms=103)


def test_repository_cancellation_after_compute_publishes_no_candidate_artifacts(
    tmp_path: Path,
) -> None:
    candidate, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / "cancel-after-compute.sqlite3"
    with RoutingJobRepository(path) as repository:
        repository.create(spec, request, authorization)

        def executor(_probe: object) -> object:
            running = repository.jobs.get(spec.job_id)
            repository.jobs.request_cancel(
                spec.job_id,
                expected_revision=running.revision,
            )
            return candidate

        result = repository.execute(spec.job_id, authorization, executor)

        assert result.status is RoutingJobStatus.CANCELLED
        with pytest.raises(RoutingCandidateExportUnavailableError):
            repository.exports.get(spec.job_id, candidate.candidate_id, authorization)
        with pytest.raises(CandidateManifestNotFoundError):
            repository.manifests.get(candidate.candidate_id)


def test_repository_execute_publishes_candidate_artifacts_after_completion(
    tmp_path: Path,
) -> None:
    candidate, spec, request, authorization = _candidate_and_spec()
    with RoutingJobRepository(tmp_path / "execute-success.sqlite3") as repository:
        repository.create(spec, request, authorization)

        result = repository.execute(spec.job_id, authorization, lambda _probe: candidate)

        assert result.status is RoutingJobStatus.COMPLETED
        exported = repository.exports.get(spec.job_id, candidate.candidate_id, authorization)
        assert exported["candidate_id"] == candidate.candidate_id
        assert repository.manifests.get(candidate.candidate_id).job_id == spec.job_id


def test_repository_execute_fails_before_completion_when_artifact_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker execution must not expose a completed job without its candidate export."""

    candidate, spec, request, authorization = _candidate_and_spec()
    with RoutingJobRepository(tmp_path / "execute-export-failure.sqlite3") as repository:
        repository.create(spec, request, authorization)

        def fail_export(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise RoutingJobError("candidate export store capacity is exhausted")

        monkeypatch.setattr(repository.exports, "put", fail_export)
        result = repository.execute(spec.job_id, authorization, lambda _probe: candidate)

        assert result.status is RoutingJobStatus.FAILED
        assert result.candidate_id is None


@pytest.mark.parametrize("mismatch", ("kind", "router", "policy", "seed", "metrics"))
def test_direct_publication_preflight_rejects_invalid_candidate_without_artifacts(
    tmp_path: Path, mismatch: str
) -> None:
    """The retryable direct path validates completion identity before writing artifacts."""

    candidate, original_spec, request, authorization = _candidate_and_spec()
    spec = _completion_mismatch(original_spec, candidate, mismatch)
    path = tmp_path / f"direct-preflight-{mismatch}.sqlite3"
    with RoutingJobRepository(path) as repository:
        queued = repository.create(spec, request, authorization, now_ms=100)
        running = repository.jobs.start(spec.job_id, expected_revision=queued.revision, now_ms=101)

        with pytest.raises(RoutingJobError):
            repository.publish_candidate(
                spec.job_id,
                candidate,
                expected_revision=running.revision,
                authorization_digest=authorization,
                now_ms=102,
            )

        assert repository.jobs.get(spec.job_id, now_ms=102) == running
        assert _candidate_artifact_counts(path) == (0, 0)


@pytest.mark.parametrize("mismatch", ("kind", "router", "policy", "seed", "metrics"))
def test_worker_publication_preflight_rejects_invalid_candidate_without_artifacts(
    tmp_path: Path, mismatch: str
) -> None:
    """The worker converts preflight rejection into its fixed invalid-candidate failure."""

    candidate, original_spec, request, authorization = _candidate_and_spec()
    spec = _completion_mismatch(original_spec, candidate, mismatch)
    path = tmp_path / f"worker-preflight-{mismatch}.sqlite3"
    with RoutingJobRepository(path) as repository:
        repository.create(spec, request, authorization)

        result = repository.execute(spec.job_id, authorization, lambda _probe: candidate)

        assert result.status is RoutingJobStatus.FAILED
        assert result.diagnostic_code.value == "invalid_request"
        assert result.candidate_id is None
        assert _candidate_artifact_counts(path) == (0, 0)


@pytest.mark.parametrize(
    "state",
    ("queued", "completed", "failed", "cancelled", "cancel_requested", "wrong_revision"),
)
def test_direct_publication_requires_current_running_lifecycle_before_artifacts(
    tmp_path: Path, state: str
) -> None:
    """Lifecycle preflight prevents state/refusal orphans before artifact persistence."""

    candidate, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / f"direct-lifecycle-preflight-{state}.sqlite3"
    with RoutingJobRepository(path) as repository:
        queued = repository.create(spec, request, authorization, now_ms=100)
        expected_revision = queued.revision
        before = queued
        if state == "cancelled":
            before = repository.jobs.request_cancel(
                spec.job_id,
                expected_revision=queued.revision,
                now_ms=101,
            )
            expected_revision = before.revision
        elif state != "queued":
            running = repository.jobs.start(
                spec.job_id, expected_revision=queued.revision, now_ms=101
            )
            before = running
            expected_revision = running.revision
            if state == "completed":
                before = repository.jobs.complete(
                    spec.job_id,
                    candidate,
                    expected_revision=running.revision,
                    now_ms=102,
                )
                expected_revision = before.revision
            elif state == "failed":
                before = repository.jobs.fail(
                    spec.job_id,
                    RoutingJobFailureCode.WORKER_ERROR,
                    "routing worker failed",
                    expected_revision=running.revision,
                    now_ms=102,
                )
                expected_revision = before.revision
            elif state == "cancel_requested":
                before = repository.jobs.request_cancel(
                    spec.job_id,
                    expected_revision=running.revision,
                    now_ms=102,
                )
                expected_revision = before.revision
            elif state == "wrong_revision":
                expected_revision += 1

        with pytest.raises((RoutingJobStateError, RoutingJobConflictError)):
            repository.publish_candidate(
                spec.job_id,
                candidate,
                expected_revision=expected_revision,
                authorization_digest=authorization,
                now_ms=103,
            )

        assert repository.jobs.get(spec.job_id, now_ms=103) == before
        assert _candidate_artifact_counts(path) == (0, 0)


def test_request_and_export_expiry_are_uniform(tmp_path: Path) -> None:
    candidate, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / "routing.sqlite3"
    with RoutingJobRepository(path, ttl_ms=10) as repository:
        queued = repository.create(spec, request, authorization, now_ms=100)
        assert queued.status is RoutingJobStatus.QUEUED
        with pytest.raises(RoutingJobRequestUnavailableError):
            repository.get(spec.job_id, authorization, now_ms=110)
        # The explicit export API also refuses unknown, expired, and wrong-context handles with
        # the same message, even though this candidate was never published.
        with pytest.raises(RoutingCandidateExportUnavailableError) as missing:
            repository.exports.get(spec.job_id, candidate.candidate_id, authorization, now_ms=110)
        with pytest.raises(RoutingCandidateExportUnavailableError) as unknown:
            repository.exports.get(_digest("f"), _digest("f"), authorization, now_ms=110)
        assert str(missing.value) == str(unknown.value)


def test_repository_lookup_purges_expired_lifecycle_with_request(tmp_path: Path) -> None:
    """A request expiry cannot retain its matching lifecycle metadata."""

    _, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / "expired-repository-request.sqlite3"
    with RoutingJobRepository(path, ttl_ms=10) as repository:
        repository.create(spec, request, authorization, now_ms=100)

        with pytest.raises(RoutingJobRequestUnavailableError):
            repository.get(spec.job_id, authorization, now_ms=110)

        with sqlite3.connect(path) as connection:
            request_row = connection.execute(
                "SELECT job_id FROM routing_job_requests WHERE job_id = ?", (spec.job_id,)
            ).fetchone()
            lifecycle_row = connection.execute(
                "SELECT job_id FROM routing_jobs WHERE job_id = ?", (spec.job_id,)
            ).fetchone()
        assert request_row is None
        assert lifecycle_row is None


def test_export_capacity_refuses_before_lifecycle_completion(tmp_path: Path) -> None:
    """A valid candidate cannot make a completed job if geometry persistence is full."""

    candidate, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / "full-export-before-completion.sqlite3"
    with RoutingJobRepository(path, max_records=1, ttl_ms=1_000) as repository:
        queued = repository.create(spec, request, authorization, now_ms=100)
        running = repository.jobs.start(spec.job_id, expected_revision=queued.revision, now_ms=101)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO routing_candidate_exports(candidate_id, job_id, base_revision, "
                "kind, authorization_digest, created_at_ms, expires_at_ms, candidate_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _digest("d"),
                    _digest("c"),
                    candidate.base_revision,
                    "layered",
                    authorization,
                    100,
                    1_000,
                    sqlite3.Binary(b'{"candidate_id":"retained-capacity"}'),
                ),
            )

        with pytest.raises(RoutingJobError, match="candidate export store capacity"):
            repository.publish_candidate(
                spec.job_id,
                candidate,
                expected_revision=running.revision,
                authorization_digest=authorization,
                now_ms=102,
            )

        record = repository.jobs.get(spec.job_id, now_ms=102)
        assert record.status is RoutingJobStatus.RUNNING
        assert record.candidate_id is None


def test_expired_request_is_deleted_before_uniform_unavailable_response(tmp_path: Path) -> None:
    """TTL expiry removes the request payload even when lookup deliberately raises."""

    _, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / "expired-request.sqlite3"
    with RoutingJobRepository(path, ttl_ms=10) as repository:
        repository.create(spec, request, authorization, now_ms=100)

        with sqlite3.connect(path) as connection:
            stored = connection.execute(
                "SELECT request_json FROM routing_job_requests WHERE job_id = ?",
                (spec.job_id,),
            ).fetchone()
        assert stored is not None
        assert isinstance(stored[0], bytes)

        with pytest.raises(RoutingJobRequestUnavailableError) as expired:
            repository.requests.get(spec.job_id, authorization, now_ms=110)
        with pytest.raises(RoutingJobRequestUnavailableError) as unknown:
            repository.requests.get(_digest("f"), authorization, now_ms=110)
        assert str(expired.value) == str(unknown.value)

        with sqlite3.connect(path) as connection:
            retained = connection.execute(
                "SELECT request_json FROM routing_job_requests WHERE job_id = ?",
                (spec.job_id,),
            ).fetchone()
        assert retained is None


def test_malformed_request_handle_commits_expired_request_purge(tmp_path: Path) -> None:
    """Malformed request handles do not bypass durable TTL cleanup."""

    _, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / "malformed-request-expiry.sqlite3"
    with RoutingJobRepository(path, ttl_ms=10) as repository:
        repository.create(spec, request, authorization, now_ms=100)

        with pytest.raises(RoutingJobRequestUnavailableError) as malformed:
            repository.requests.get("malformed-job-id", authorization, now_ms=110)
        with pytest.raises(RoutingJobRequestUnavailableError) as unknown:
            repository.requests.get(_digest("f"), authorization, now_ms=110)
        assert str(malformed.value) == str(unknown.value)

        with sqlite3.connect(path) as connection:
            retained = connection.execute(
                "SELECT request_json FROM routing_job_requests WHERE job_id = ?",
                (spec.job_id,),
            ).fetchone()
        assert retained is None


def test_expired_candidate_export_is_deleted_before_unavailable_response(tmp_path: Path) -> None:
    """An expired geometry export is removed even though lookup reports a uniform miss."""

    candidate, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / "expired-export.sqlite3"
    with RoutingJobRepository(path, ttl_ms=10) as repository:
        queued = repository.create(spec, request, authorization, now_ms=100)
        running = repository.jobs.start(spec.job_id, expected_revision=queued.revision, now_ms=101)
        repository.publish_candidate(
            spec.job_id,
            candidate,
            expected_revision=running.revision,
            authorization_digest=authorization,
            now_ms=102,
        )

        with sqlite3.connect(path) as connection:
            stored = connection.execute(
                "SELECT candidate_json FROM routing_candidate_exports WHERE candidate_id = ?",
                (candidate.candidate_id,),
            ).fetchone()
        assert stored is not None
        assert isinstance(stored[0], bytes)
        assert b'"vertices"' in stored[0]

        with pytest.raises(RoutingCandidateExportUnavailableError) as expired:
            repository.exports.get(
                spec.job_id,
                candidate.candidate_id,
                authorization,
                now_ms=112,
            )
        with pytest.raises(RoutingCandidateExportUnavailableError) as unknown:
            repository.exports.get(_digest("f"), _digest("f"), authorization, now_ms=112)
        assert str(expired.value) == str(unknown.value)

        with sqlite3.connect(path) as connection:
            retained = connection.execute(
                "SELECT candidate_json FROM routing_candidate_exports WHERE candidate_id = ?",
                (candidate.candidate_id,),
            ).fetchone()
        assert retained is None


def test_malformed_export_handle_commits_expired_geometry_purge(tmp_path: Path) -> None:
    """Malformed export handles do not bypass durable private-geometry expiry cleanup."""

    candidate, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / "malformed-export-expiry.sqlite3"
    with RoutingJobRepository(path, ttl_ms=10) as repository:
        queued = repository.create(spec, request, authorization, now_ms=100)
        running = repository.jobs.start(spec.job_id, expected_revision=queued.revision, now_ms=101)
        repository.publish_candidate(
            spec.job_id,
            candidate,
            expected_revision=running.revision,
            authorization_digest=authorization,
            now_ms=102,
        )

        with pytest.raises(RoutingCandidateExportUnavailableError) as malformed:
            repository.exports.get(
                spec.job_id,
                "malformed-candidate-id",
                authorization,
                now_ms=112,
            )
        with pytest.raises(RoutingCandidateExportUnavailableError) as unknown:
            repository.exports.get(_digest("f"), _digest("f"), authorization, now_ms=112)
        assert str(malformed.value) == str(unknown.value)

        with sqlite3.connect(path) as connection:
            retained = connection.execute(
                "SELECT candidate_json FROM routing_candidate_exports WHERE candidate_id = ?",
                (candidate.candidate_id,),
            ).fetchone()
        assert retained is None


def test_unauthorized_live_export_lookup_commits_other_expired_export_purges(
    tmp_path: Path,
) -> None:
    """An authorization miss cannot roll back expiry cleanup for a different export."""

    candidate, spec, request, authorization = _candidate_and_spec()
    path = tmp_path / "unauthorized-export-purge.sqlite3"
    expired_candidate_id = _digest("d")
    with RoutingJobRepository(path, ttl_ms=10) as repository:
        queued = repository.create(spec, request, authorization, now_ms=100)
        running = repository.jobs.start(spec.job_id, expected_revision=queued.revision, now_ms=101)
        repository.publish_candidate(
            spec.job_id,
            candidate,
            expected_revision=running.revision,
            authorization_digest=authorization,
            now_ms=102,
        )
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO routing_candidate_exports(candidate_id, job_id, base_revision, "
                "kind, authorization_digest, created_at_ms, expires_at_ms, candidate_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    expired_candidate_id,
                    spec.job_id,
                    candidate.base_revision,
                    "layered",
                    authorization,
                    100,
                    110,
                    sqlite3.Binary(b'{"private_geometry":"must-expire"}'),
                ),
            )

        with pytest.raises(RoutingCandidateExportUnavailableError) as unauthorized:
            repository.exports.get(
                _digest("f"),
                candidate.candidate_id,
                _digest("f"),
                now_ms=111,
            )
        with pytest.raises(RoutingCandidateExportUnavailableError) as unknown:
            repository.exports.get(_digest("f"), _digest("f"), _digest("f"), now_ms=111)
        assert str(unauthorized.value) == str(unknown.value)

        with sqlite3.connect(path) as connection:
            expired = connection.execute(
                "SELECT candidate_json FROM routing_candidate_exports WHERE candidate_id = ?",
                (expired_candidate_id,),
            ).fetchone()
            live = connection.execute(
                "SELECT authorization_digest, candidate_json FROM routing_candidate_exports "
                "WHERE candidate_id = ?",
                (candidate.candidate_id,),
            ).fetchone()
        assert expired is None
        assert live is not None
        assert live[0] == authorization
        assert isinstance(live[1], bytes)
        assert b'"vertices"' in live[1]
