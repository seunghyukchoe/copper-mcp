from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.routing import (
    LayeredAStarSettings,
    LayeredBoardRouter,
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
from copper_mcp.routing.jobs import RoutingJobError, RoutingJobSpec, RoutingJobStatus

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


def _candidate_and_spec() -> tuple[object, RoutingJobSpec, dict[str, object], str]:
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
