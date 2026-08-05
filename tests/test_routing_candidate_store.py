from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from copper_mcp.routing.candidate_store import (
    CandidateManifest,
    CandidateManifestBindingError,
    CandidateManifestConflictError,
    CandidateManifestIntegrityError,
    CandidateManifestNotFoundError,
    CandidateManifestStore,
)
from copper_mcp.routing.jobs import RoutingJobKind, RoutingJobSpec


def _digest(fill: str = "a") -> str:
    return f"sha256:{fill * 64}"


def _manifest(
    *,
    fill: str = "a",
    job_id: str | None = None,
    policy: str = "deterministic",
) -> CandidateManifest:
    return CandidateManifest.create(
        candidate_id=_digest(fill),
        base_revision=_digest("b"),
        start_pad_id="pad:start",
        end_pad_id="pad:end",
        kind="single-layer",
        router="astar-v1",
        policy=policy,
        path_count=1,
        via_count=0,
        cost=123,
        metrics={"wire_length_nm": 1000, "bend_count": 2},
        job_id=job_id,
    )


def _job_spec(*, board_fill: str = "b") -> RoutingJobSpec:
    return RoutingJobSpec.create(
        board_revision=_digest(board_fill),
        snapshot_digest=None,
        start_pad_id="pad:start",
        end_pad_id="pad:end",
        request_digest=_digest("c"),
        request_kind=RoutingJobKind.SINGLE_LAYER,
        backend="astar-v1",
        router_version="astar-v1",
        policy="deterministic",
        seed=0,
    )


def test_store_reopens_and_preserves_redacted_manifest(tmp_path: Path) -> None:
    path = tmp_path / "candidate-manifests.sqlite3"
    manifest = _manifest()
    with CandidateManifestStore(path, ttl_ms=1000) as store:
        stored = store.put(manifest, now_ms=100)
        assert stored.created_at_ms == 100
        assert stored.updated_at_ms == 100
        assert stored.expires_at_ms == 1100
        assert stored.manifest_digest == manifest.manifest_digest

    with CandidateManifestStore(path, ttl_ms=1000) as reopened:
        assert reopened.get(manifest.candidate_id, now_ms=101) == stored


def test_put_is_idempotent_and_rejects_same_id_with_new_content(tmp_path: Path) -> None:
    path = tmp_path / "candidate-manifests.sqlite3"
    manifest = _manifest()
    with CandidateManifestStore(path, ttl_ms=1000) as store:
        first = store.put(manifest, now_ms=100)
        second = store.put(manifest, now_ms=900)
        assert second == first
        with pytest.raises(CandidateManifestConflictError):
            store.put(_manifest(policy="alternate"), now_ms=901)
        row = (
            sqlite3.connect(path)
            .execute("SELECT COUNT(*) FROM routing_candidate_manifests")
            .fetchone()
        )
        assert row == (1,)


def test_expiry_is_bounded_and_unknown_and_expired_are_uniform(tmp_path: Path) -> None:
    path = tmp_path / "candidate-manifests.sqlite3"
    manifest = _manifest()
    with CandidateManifestStore(path, ttl_ms=10) as store:
        store.put(manifest, now_ms=100)
        with pytest.raises(
            CandidateManifestNotFoundError, match="candidate manifest is unavailable"
        ):
            store.get(_digest("d"), now_ms=100)
        with pytest.raises(
            CandidateManifestNotFoundError, match="candidate manifest is unavailable"
        ):
            store.get(manifest.candidate_id, now_ms=110)
        with pytest.raises(CandidateManifestNotFoundError) as expired:
            store.get(manifest.candidate_id, now_ms=110)
        with pytest.raises(CandidateManifestNotFoundError) as unknown:
            store.get(_digest("d"), now_ms=110)
        assert str(expired.value) == str(unknown.value)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM routing_candidate_manifests").fetchone() == (0,)
    connection.close()


def test_malformed_candidate_id_commits_expired_manifest_purge(tmp_path: Path) -> None:
    """Malformed manifest handles cannot bypass the durable TTL retention boundary."""

    path = tmp_path / "malformed-manifest-expiry.sqlite3"
    manifest = _manifest()
    with CandidateManifestStore(path, ttl_ms=10) as store:
        store.put(manifest, now_ms=100)
        with pytest.raises(CandidateManifestNotFoundError) as malformed:
            store.get("malformed-candidate-id", now_ms=110)
        with pytest.raises(CandidateManifestNotFoundError) as unknown:
            store.get(_digest("d"), now_ms=110)
        assert str(malformed.value) == str(unknown.value)

    with sqlite3.connect(path) as connection:
        retained = connection.execute(
            "SELECT manifest_json FROM routing_candidate_manifests WHERE candidate_id = ?",
            (manifest.candidate_id,),
        ).fetchone()
    assert retained is None


def test_capacity_purges_expired_rows_before_refusing_new_manifest(tmp_path: Path) -> None:
    path = tmp_path / "candidate-manifests.sqlite3"
    first = _manifest(fill="a")
    second = _manifest(fill="c")
    with CandidateManifestStore(path, max_records=1, ttl_ms=10) as store:
        store.put(first, now_ms=100)
        store.put(second, now_ms=110)
        assert store.get(second.candidate_id, now_ms=110).candidate_id == second.candidate_id


def test_tampered_sqlite_payload_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "candidate-manifests.sqlite3"
    manifest = _manifest()
    with CandidateManifestStore(path, ttl_ms=1000) as store:
        store.put(manifest, now_ms=100)
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE routing_candidate_manifests SET manifest_json = ? WHERE candidate_id = ?",
            (sqlite3.Binary(b'{"tampered":true}'), manifest.candidate_id),
        )
        connection.commit()
        connection.close()
        with pytest.raises(CandidateManifestIntegrityError):
            store.get(manifest.candidate_id, now_ms=101)


def test_job_binding_checks_identity_revision_endpoints_kind_router_and_policy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate-manifests.sqlite3"
    spec = _job_spec()
    matching = CandidateManifest.create(
        candidate_id=_digest("a"),
        base_revision=spec.expected_candidate_revision,
        start_pad_id=spec.start_pad_id,
        end_pad_id=spec.end_pad_id,
        kind=spec.request_kind.value,
        router=spec.router_version,
        policy=spec.policy,
        path_count=1,
        via_count=0,
        cost=1,
        metrics={"wire_length_nm": 1},
        job_id=spec.job_id,
    )
    with CandidateManifestStore(path, ttl_ms=1000) as store:
        assert store.put(matching, job_spec=spec, now_ms=100).job_id == spec.job_id

        wrong_revision = CandidateManifest.create(
            candidate_id=_digest("c"),
            base_revision=_digest("e"),
            start_pad_id=matching.start_pad_id,
            end_pad_id=matching.end_pad_id,
            kind=matching.kind,
            router=matching.router,
            policy=matching.policy,
            path_count=1,
            via_count=0,
            cost=1,
            metrics={"wire_length_nm": 1},
        )
        with pytest.raises(CandidateManifestBindingError):
            store.put(wrong_revision, job_spec=spec, now_ms=101)

        wrong_endpoint = CandidateManifest.create(
            candidate_id=_digest("d"),
            base_revision=matching.base_revision,
            start_pad_id="pad:other",
            end_pad_id=matching.end_pad_id,
            kind=matching.kind,
            router=matching.router,
            policy=matching.policy,
            path_count=1,
            via_count=0,
            cost=1,
            metrics={"wire_length_nm": 1},
        )
        with pytest.raises(CandidateManifestBindingError):
            store.put(wrong_endpoint, job_spec=spec, now_ms=102)

        wrong_router = CandidateManifest.create(
            candidate_id=_digest("e"),
            base_revision=matching.base_revision,
            start_pad_id=matching.start_pad_id,
            end_pad_id=matching.end_pad_id,
            kind=matching.kind,
            router="other-router-v1",
            policy=matching.policy,
            path_count=1,
            via_count=0,
            cost=1,
            metrics={"wire_length_nm": 1},
            job_id=spec.job_id,
        )
        with pytest.raises(CandidateManifestBindingError):
            store.put(wrong_router, job_spec=spec, now_ms=103)

        missing_job_id = CandidateManifest.create(
            candidate_id=_digest("f"),
            base_revision=matching.base_revision,
            start_pad_id=matching.start_pad_id,
            end_pad_id=matching.end_pad_id,
            kind=matching.kind,
            router=matching.router,
            policy=matching.policy,
            path_count=1,
            via_count=0,
            cost=1,
            metrics={"wire_length_nm": 1},
        )
        with pytest.raises(CandidateManifestBindingError):
            store.put(missing_job_id, job_spec=spec, now_ms=104)


def test_sqlite_payload_contains_no_geometry_or_board_content(tmp_path: Path) -> None:
    path = tmp_path / "candidate-manifests.sqlite3"
    manifest = _manifest()
    with CandidateManifestStore(path, ttl_ms=1000) as store:
        store.put(manifest, now_ms=100)
    connection = sqlite3.connect(path)
    raw = connection.execute("SELECT manifest_json FROM routing_candidate_manifests").fetchone()[0]
    connection.close()
    assert isinstance(raw, bytes)
    assert b"vertices" not in raw
    assert b"board_bytes" not in raw
    assert b"raw_net" not in raw
    assert b"prompt" not in raw
    assert b"credentials" not in raw
    assert b"drc" not in raw


def test_injected_clock_drives_expiry_without_wall_clock(tmp_path: Path) -> None:
    path = tmp_path / "candidate-manifests.sqlite3"
    now = [100]
    manifest = _manifest()
    with CandidateManifestStore(path, ttl_ms=10, clock=lambda: now[0]) as store:
        stored = store.put(manifest)
        assert stored.created_at_ms == 100
        now[0] = 109
        assert store.get(manifest.candidate_id).candidate_id == manifest.candidate_id
        now[0] = 110
        with pytest.raises(CandidateManifestNotFoundError):
            store.get(manifest.candidate_id)
