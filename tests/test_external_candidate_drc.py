from __future__ import annotations

import json
import shutil
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

import copper_mcp.external_candidate_drc as external_drc
import copper_mcp.kicad_cli as kicad_cli
from copper_mcp.adapters import net_id_for_name, parse_kicad_bytes
from copper_mcp.adapters.kicad_route_patch import _render_kicad_disposed_candidate_board
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.external_candidate_drc import (
    ExternalCandidateDrcError,
    ExternalCandidateDrcResult,
    _dispose_external_route_candidate,
    verify_external_route_candidate_drc,
)
from copper_mcp.kicad_cli import KiCadCliError, RouteCandidateDrcEvidence
from copper_mcp.models import DrcSummary
from copper_mcp.route_preview import RoutePreviewRequest
from copper_mcp.routing import AStarRouter, AStarSettings, RouteCandidate, RouteRequest

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
_DIGEST = f"sha256:{'1' * 64}"


def _constraints() -> NetClass:
    return NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )


def _request(board: str, **updates: object) -> RoutePreviewRequest:
    arguments: dict[str, object] = {
        "board": board,
        "layer": "F.Cu",
        "constraints": _constraints(),
        "settings": AStarSettings(),
        "seed": 23,
        "include_drc": True,
        "net": "AUDIO",
    }
    arguments.update(updates)
    return RoutePreviewRequest(**arguments)  # type: ignore[arg-type]


def _candidate_and_document(
    board: Path, request: RoutePreviewRequest
) -> tuple[RouteCandidate, dict[str, object]]:
    conversion = parse_kicad_bytes(board.read_bytes(), request.profile())
    assert conversion.snapshot is not None and not conversion.diagnostics
    route_request = RouteRequest(
        board_revision=conversion.snapshot.snapshot_digest,
        net_id=net_id_for_name("AUDIO"),
        layer_id="layer:F.Cu",
        seed=request.seed,
        settings=request.settings,
    )
    routed = AStarRouter().propose(conversion.snapshot, route_request)
    assert routed.candidate is not None
    candidate = routed.candidate
    paths = []
    for path in candidate.patch.paths:
        segments = []
        for start, end in pairwise(path.vertices):
            segments.append(
                {
                    "layer_id": candidate.patch.layer_id,
                    "width_nm": candidate.patch.width_nm,
                    "start": {"x_nm": start.x, "y_nm": start.y},
                    "end": {"x_nm": end.x, "y_nm": end.y},
                }
            )
        paths.append({"segments": segments})
    document: dict[str, object] = {
        "schema": (
            "copper-mcp/external-route-candidate/v1"
            if candidate.pad_count == 2
            else "copper-mcp/external-route-patch/v2"
        ),
        "problem_revision": candidate.base_revision,
        "start_pad_id": candidate.start_pad_id,
        "end_pad_id": candidate.end_pad_id,
        "vias": [],
    }
    if candidate.pad_count == 2:
        document["segments"] = paths[0]["segments"]
    else:
        document["paths"] = paths
    return candidate, document


def _workspace(
    tmp_path: Path, fixture: Path = FIXTURE
) -> tuple[Path, RoutePreviewRequest, RouteCandidate, dict[str, object]]:
    board = tmp_path / fixture.name
    shutil.copy2(fixture, board)
    request = _request(board.name)
    candidate, document = _candidate_and_document(board, request)
    return board, request, candidate, document


def _evidence(candidate: RouteCandidate) -> RouteCandidateDrcEvidence:
    summary = DrcSummary(
        base_revision=_DIGEST,
        drc_context_revision=_DIGEST,
        kicad_version="10.0.5",
        drc_schema="https://schemas.kicad.org/drc.v1.json",
        coordinate_units="mm",
        error_count=0,
        warning_count=0,
        exclusion_count=0,
        ignored_check_count=0,
        unconnected_count=0,
        violation_type_counts={},
        passed=True,
    )
    return RouteCandidateDrcEvidence(
        candidate_id=candidate.candidate_id,
        candidate_base_revision=candidate.base_revision,
        source_revision=_DIGEST,
        patched_board_revision=_DIGEST,
        patched_drc_context_revision=_DIGEST,
        summary=summary,
    )


@pytest.mark.parametrize("fixture_name", ["two-pad.kicad_pcb", "tree-star.kicad_pcb"])
def test_accepted_external_candidate_continues_to_redacted_candidate_bound_drc(
    fixture_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = FIXTURE.parent / fixture_name
    board, request, expected_candidate, document = _workspace(tmp_path, fixture)
    calls: list[RouteCandidate] = []

    def run_drc(
        requested_path: str,
        disposition: object,
        profile: object,
        settings: Settings,
        *,
        expected_source_revision: str,
        deadline: float | None = None,
    ) -> RouteCandidateDrcEvidence:
        assert requested_path == board.name
        assert profile == request.profile()
        assert settings.workspace == tmp_path
        assert (
            expected_source_revision
            == f"sha256:{external_drc.hashlib.sha256(board.read_bytes()).hexdigest()}"
        )
        assert deadline is not None
        candidate = getattr(disposition, "candidate", None)
        assert isinstance(candidate, RouteCandidate)
        calls.append(candidate)
        return _evidence(candidate)

    monkeypatch.setattr(external_drc, "run_disposed_route_candidate_drc", run_drc)
    result = verify_external_route_candidate_drc(
        request,
        document,
        Settings(workspace=tmp_path),
        start_pad_id=expected_candidate.start_pad_id,
        end_pad_id=expected_candidate.end_pad_id,
        max_obstacle_checks=request.settings.max_obstacle_checks,
        max_path_edges=4_096,
    )

    assert len(calls) == 1
    assert [candidate.candidate_id for candidate in calls] == [result.verification.candidate_id]
    assert result.physical_validation == "completed"
    payload = result.to_dict()
    assert payload["drc_comparability"] == "single_invocation"
    rendered = json.dumps(payload, sort_keys=True)
    for forbidden in ("x_nm", "y_nm", "segments", "paths", "apply_token", str(tmp_path)):
        assert forbidden not in rendered


def test_structural_refusal_never_invokes_kicad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, request, candidate, document = _workspace(tmp_path)
    document["end_pad_id"] = candidate.start_pad_id
    monkeypatch.setattr(
        external_drc,
        "run_disposed_route_candidate_drc",
        lambda *args, **kwargs: pytest.fail("KiCad must not run for a refused candidate"),
    )

    result = verify_external_route_candidate_drc(
        request,
        document,
        Settings(workspace=tmp_path),
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        max_obstacle_checks=request.settings.max_obstacle_checks,
        max_path_edges=4_096,
    )

    assert result.verification.failure is not None
    assert result.physical_validation == "not_run"
    assert result.to_dict()["drc_evidence"] is None
    assert "drc_comparability" not in result.to_dict()


def test_drc_requires_the_typed_request_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, request, candidate, document = _workspace(tmp_path)
    request = _request(request.board, include_drc=False)
    monkeypatch.setattr(
        external_drc,
        "run_disposed_route_candidate_drc",
        lambda *args, **kwargs: pytest.fail("KiCad must not run without request consent"),
    )

    with pytest.raises(ExternalCandidateDrcError, match="does not authorize"):
        verify_external_route_candidate_drc(
            request,
            document,
            Settings(workspace=tmp_path),
            start_pad_id=candidate.start_pad_id,
            end_pad_id=candidate.end_pad_id,
            max_obstacle_checks=request.settings.max_obstacle_checks,
            max_path_edges=4_096,
        )


def test_disposed_kicad_seam_selects_the_non_replay_renderer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, request, candidate, document = _workspace(tmp_path)
    conversion = parse_kicad_bytes(board.read_bytes(), request.profile())
    assert conversion.snapshot is not None
    disposition = _dispose_external_route_candidate(
        conversion.snapshot,
        RouteRequest(
            board_revision=conversion.snapshot.snapshot_digest,
            net_id=request.net_id,
            layer_id=request.layer_id,
            seed=request.seed,
            settings=request.settings,
        ),
        document,
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        max_obstacle_checks=request.settings.max_obstacle_checks,
        max_path_edges=4_096,
    )
    captured: dict[str, object] = {}

    def run_common(*args: object, **kwargs: object) -> RouteCandidateDrcEvidence:
        captured.update(kwargs)
        assert disposition.candidate is not None
        return _evidence(disposition.candidate)

    monkeypatch.setattr(kicad_cli, "_run_route_candidate_drc", run_common)
    kicad_cli.run_disposed_route_candidate_drc(
        board.name,
        disposition,
        request.profile(),
        Settings(workspace=tmp_path),
        expected_source_revision=f"sha256:{external_drc.hashlib.sha256(board.read_bytes()).hexdigest()}",
        deadline=123.0,
    )

    assert captured["render_candidate"] is _render_kicad_disposed_candidate_board
    assert captured["deadline"] == 123.0
    assert captured["expected_source_revision"] == (
        f"sha256:{external_drc.hashlib.sha256(board.read_bytes()).hexdigest()}"
    )


def test_disposed_kicad_seam_rejects_a_forged_capability(tmp_path: Path) -> None:
    with pytest.raises(KiCadCliError, match="malformed or refused"):
        kicad_cli.run_disposed_route_candidate_drc(
            "two-pad.kicad_pcb",
            object(),
            _request("two-pad.kicad_pcb").profile(),
            Settings(workspace=tmp_path),
            expected_source_revision=_DIGEST,
        )


def test_disposition_rejects_a_candidate_not_bound_to_its_acceptance(tmp_path: Path) -> None:
    board, request, candidate, document = _workspace(tmp_path)
    snapshot = parse_kicad_bytes(board.read_bytes(), request.profile()).snapshot
    assert snapshot is not None
    disposition = _dispose_external_route_candidate(
        snapshot,
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=request.net_id,
            layer_id=request.layer_id,
            seed=request.seed,
            settings=request.settings,
        ),
        document,
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        max_obstacle_checks=request.settings.max_obstacle_checks,
        max_path_edges=4_096,
    )
    assert disposition.candidate is not None

    with pytest.raises(ValueError, match="bound to another candidate"):
        external_drc._ExternalCandidateDisposition(
            verification=disposition.verification,
            candidate=replace(disposition.candidate, candidate_id=_DIGEST),
        )


def test_candidate_drc_deadline_reduces_every_phase_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = 100.25
    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: clock)
    settings = Settings(
        workspace=tmp_path,
        kicad_timeout_seconds=120,
        max_drc_context_scan_seconds=10,
    )

    limited = kicad_cli._candidate_drc_deadline_settings(settings, 105.9)

    assert limited.kicad_timeout_seconds == 5
    assert limited.max_drc_context_scan_seconds == 5
    with pytest.raises(KiCadCliError, match="deadline exceeded"):
        kicad_cli._candidate_drc_deadline_settings(settings, 101.0)


def test_external_coordinator_rejects_drc_finishing_after_its_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, request, candidate, document = _workspace(tmp_path)
    clock = [100.0]
    monkeypatch.setattr(external_drc.time, "monotonic", lambda: clock[0])

    def finish_late(
        requested_path: str,
        disposition: object,
        profile: object,
        settings: Settings,
        *,
        expected_source_revision: str,
        deadline: float | None = None,
    ) -> RouteCandidateDrcEvidence:
        assert requested_path == request.board
        assert profile == request.profile()
        assert deadline == 130.0
        assert expected_source_revision.startswith("sha256:")
        accepted = getattr(disposition, "candidate", None)
        assert isinstance(accepted, RouteCandidate)
        clock[0] = 131.0
        return _evidence(accepted)

    monkeypatch.setattr(external_drc, "run_disposed_route_candidate_drc", finish_late)
    with pytest.raises(ExternalCandidateDrcError, match="expired during authoritative DRC"):
        verify_external_route_candidate_drc(
            request,
            document,
            Settings(workspace=tmp_path),
            start_pad_id=candidate.start_pad_id,
            end_pad_id=candidate.end_pad_id,
            max_obstacle_checks=request.settings.max_obstacle_checks,
            max_path_edges=4_096,
        )


def test_source_byte_change_between_public_check_and_drc_is_discarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, request, candidate, document = _workspace(tmp_path)
    request = _request(
        request.board,
        expect_board_revision=f"sha256:{external_drc.hashlib.sha256(board.read_bytes()).hexdigest()}",
    )
    original = kicad_cli.run_disposed_route_candidate_drc

    def mutate_then_run(
        requested_path: str,
        disposition: object,
        profile: object,
        settings: Settings,
        *,
        expected_source_revision: str,
        deadline: float | None = None,
    ) -> RouteCandidateDrcEvidence:
        board.write_bytes(board.read_bytes() + b"\n")
        return original(
            requested_path,
            disposition,
            profile,  # type: ignore[arg-type]
            settings,
            expected_source_revision=expected_source_revision,
            deadline=deadline,
        )

    monkeypatch.setattr(external_drc, "run_disposed_route_candidate_drc", mutate_then_run)
    with pytest.raises(ExternalCandidateDrcError, match="could not verify"):
        verify_external_route_candidate_drc(
            request,
            document,
            Settings(workspace=tmp_path),
            start_pad_id=candidate.start_pad_id,
            end_pad_id=candidate.end_pad_id,
            max_obstacle_checks=request.settings.max_obstacle_checks,
            max_path_edges=4_096,
        )


def test_redacted_result_rejects_evidence_for_another_candidate(tmp_path: Path) -> None:
    _, request, candidate, document = _workspace(tmp_path)
    snapshot = parse_kicad_bytes(
        (tmp_path / request.board).read_bytes(), request.profile()
    ).snapshot
    assert snapshot is not None
    verification = _dispose_external_route_candidate(
        snapshot,
        RouteRequest(
            board_revision=document["problem_revision"],
            net_id=request.net_id,
            layer_id=request.layer_id,
            seed=request.seed,
            settings=request.settings,
        ),
        document,
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        max_obstacle_checks=request.settings.max_obstacle_checks,
        max_path_edges=4_096,
    ).verification

    with pytest.raises(ValueError, match="another candidate"):
        ExternalCandidateDrcResult(
            verification=verification,
            drc_evidence=_evidence(candidate),
        )


@pytest.mark.parametrize("precondition", ["board", "snapshot"])
def test_stale_file_preconditions_refuse_before_kicad(
    precondition: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, request, candidate, document = _workspace(tmp_path)
    update = (
        {"expect_board_revision": _DIGEST}
        if precondition == "board"
        else {"expect_snapshot_digest": _DIGEST}
    )
    request = _request(request.board, **update)
    monkeypatch.setattr(
        external_drc,
        "run_disposed_route_candidate_drc",
        lambda *args, **kwargs: pytest.fail("KiCad must not run for stale input"),
    )

    result = verify_external_route_candidate_drc(
        request,
        document,
        Settings(workspace=tmp_path),
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        max_obstacle_checks=request.settings.max_obstacle_checks,
        max_path_edges=4_096,
    )

    assert result.verification.failure is external_drc.ExternalCandidateFailure.STALE_REVISION
    assert result.physical_validation == "not_run"


def test_kicad_failure_is_redacted_and_never_returns_structural_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, request, candidate, document = _workspace(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise KiCadCliError("secret board name and coordinates")

    monkeypatch.setattr(external_drc, "run_disposed_route_candidate_drc", fail)
    with pytest.raises(ExternalCandidateDrcError) as captured:
        verify_external_route_candidate_drc(
            request,
            document,
            Settings(workspace=tmp_path),
            start_pad_id=candidate.start_pad_id,
            end_pad_id=candidate.end_pad_id,
            max_obstacle_checks=request.settings.max_obstacle_checks,
            max_path_edges=4_096,
        )
    assert str(captured.value) == "authoritative KiCad DRC could not verify the external candidate"


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
@pytest.mark.parametrize("fixture_name", ["two-pad.kicad_pcb", "tree-star.kicad_pcb"])
def test_real_kicad_external_candidate_drc_is_private_and_read_only(
    fixture_name: str, tmp_path: Path
) -> None:
    board, request, candidate, document = _workspace(tmp_path, FIXTURE.parent / fixture_name)
    source = board.read_bytes()
    before = board.stat()
    entries = frozenset(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    result = verify_external_route_candidate_drc(
        request,
        document,
        Settings(workspace=tmp_path, kicad_cli=REAL_KICAD_CLI),
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        max_obstacle_checks=request.settings.max_obstacle_checks,
        max_path_edges=4_096,
    )

    after = board.stat()
    assert result.drc_evidence is not None
    assert result.drc_evidence.summary.passed
    assert result.drc_evidence.candidate_id == result.verification.candidate_id
    assert board.read_bytes() == source
    assert after.st_ino == before.st_ino
    assert after.st_mtime_ns == before.st_mtime_ns
    assert frozenset(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == entries
