from __future__ import annotations

import hashlib
import json
import shutil
from itertools import pairwise
from pathlib import Path

import pytest

import copper_mcp.external_candidate_drc as external_drc
from copper_mcp.adapters import net_id_for_name, parse_kicad_bytes
from copper_mcp.config import Settings
from copper_mcp.external_candidate_drc import (
    ExternalCandidateDrcResult,
    ExternalCandidatePublicError,
)
from copper_mcp.kicad_cli import RouteCandidateDrcEvidence
from copper_mcp.mcp_contracts import ExternalRouteVerificationToolResponse
from copper_mcp.models import DrcSummary
from copper_mcp.route_preview import RoutePreviewRequest
from copper_mcp.routing import AStarRouter, RouteCandidate, RouteRequest
from copper_mcp.routing.external_candidate_verifier import (
    ExternalCandidateFailure,
    _refused,
)
from copper_mcp.tools import server_info, verify_external_route_candidate

FIXTURES = Path(__file__).parent / "fixtures" / "route-candidate"


def _constraints() -> dict[str, int]:
    return {
        "clearance_nm": 250_000,
        "track_width_nm": 250_000,
        "via_diameter_nm": 800_000,
        "via_drill_nm": 400_000,
    }


def _payload(
    tmp_path: Path,
    fixture_name: str = "two-pad.kicad_pcb",
) -> tuple[Path, dict[str, object], RouteCandidate]:
    board = tmp_path / fixture_name
    shutil.copy2(FIXTURES / fixture_name, board)
    source_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    request_document: dict[str, object] = {
        "board": board.name,
        "layer": "F.Cu",
        "constraints": _constraints(),
        "net_ref_id": net_id_for_name("AUDIO"),
        "expect_board_revision": source_revision,
        "expect_snapshot_digest": f"sha256:{'0' * 64}",
        "seed": 23,
    }
    parsed = external_drc._parse_public_request(
        {
            "schema_version": "1.0",
            "request": request_document,
            "document": {},
            "start_pad_id": "pad:placeholder",
            "end_pad_id": "pad:placeholder",
        }
    )
    assert parsed is not None
    typed_request = parsed[0]
    conversion = parse_kicad_bytes(board.read_bytes(), typed_request.profile())
    assert conversion.snapshot is not None and not conversion.diagnostics
    snapshot = conversion.snapshot
    request_document["expect_snapshot_digest"] = snapshot.snapshot_digest
    route_request = RouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=typed_request.net_id,
        layer_id=typed_request.layer_id,
        seed=typed_request.seed,
        settings=typed_request.settings,
    )
    routed = AStarRouter().propose(snapshot, route_request)
    assert routed.candidate is not None
    candidate = routed.candidate
    paths: list[dict[str, object]] = []
    for path in candidate.patch.paths:
        segments = [
            {
                "layer_id": candidate.patch.layer_id,
                "width_nm": candidate.patch.width_nm,
                "start": {"x_nm": start.x, "y_nm": start.y},
                "end": {"x_nm": end.x, "y_nm": end.y},
            }
            for start, end in pairwise(path.vertices)
        ]
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
    return (
        board,
        {
            "schema_version": "1.0",
            "request": request_document,
            "document": document,
            "start_pad_id": candidate.start_pad_id,
            "end_pad_id": candidate.end_pad_id,
        },
        candidate,
    )


def _evidence(
    candidate: RouteCandidate,
    *,
    error_count: int = 0,
) -> RouteCandidateDrcEvidence:
    digest = f"sha256:{'1' * 64}"
    summary = DrcSummary(
        base_revision=digest,
        drc_context_revision=digest,
        kicad_version="10.0.5",
        drc_schema="https://schemas.kicad.org/drc.v1.json",
        coordinate_units="mm",
        error_count=error_count,
        warning_count=0,
        exclusion_count=0,
        ignored_check_count=0,
        unconnected_count=0,
        violation_type_counts={"clearance": error_count} if error_count else {},
        passed=error_count == 0,
    )
    return RouteCandidateDrcEvidence(
        candidate_id=candidate.candidate_id,
        candidate_base_revision=candidate.base_revision,
        source_revision=digest,
        patched_board_revision=digest,
        patched_drc_context_revision=digest,
        summary=summary,
    )


@pytest.mark.parametrize("fixture_name", ["two-pad.kicad_pcb", "tree-star.kicad_pcb"])
def test_public_service_accepts_v1_and_v2_without_leaks_or_workspace_changes(
    fixture_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, payload, _ = _payload(tmp_path, fixture_name)
    source = board.read_bytes()
    before = board.stat()
    entries = frozenset(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    disposed_candidates: list[RouteCandidate] = []

    def run_drc(
        path: str,
        disposition: object,
        profile: object,
        settings: Settings,
        *,
        deadline: float | None = None,
    ) -> RouteCandidateDrcEvidence:
        del path, profile, settings, deadline
        accepted = getattr(disposition, "candidate", None)
        assert isinstance(accepted, RouteCandidate)
        disposed_candidates.append(accepted)
        return _evidence(accepted)

    monkeypatch.setattr(external_drc, "run_disposed_route_candidate_drc", run_drc)
    result = verify_external_route_candidate(payload, Settings(workspace=tmp_path))
    validated = ExternalRouteVerificationToolResponse.model_validate(result)

    assert validated.root.status == "accepted"
    assert result["schema_version"] == "1.0"
    assert result["status"] == "accepted"
    assert len(disposed_candidates) == 1
    assert result["candidate_id"] == disposed_candidates[0].candidate_id
    assert result["physical_validation"] == "completed"
    assert result["drc_comparability"] == "single_invocation"
    encoded = json.dumps(result, sort_keys=True)
    for forbidden in (
        "x_nm",
        "y_nm",
        "segments",
        "paths",
        "apply_token",
        "fill_authority",
        str(tmp_path),
        board.name,
    ):
        assert forbidden not in encoded
    after = board.stat()
    assert board.read_bytes() == source
    assert after.st_ino == before.st_ino
    assert after.st_mtime_ns == before.st_mtime_ns
    assert frozenset(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == entries


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: None,
        lambda payload: {**payload, "schema_version": True},
        lambda payload: {**payload, "extra": 1},
        lambda payload: {key: value for key, value in payload.items() if key != "document"},
        lambda payload: {**payload, "request": True},
        lambda payload: {**payload, "request": {**payload["request"], "net": "secret"}},
        lambda payload: {
            **payload,
            "request": {**payload["request"], "settings": {"max_grid_nodes": True}},
        },
        lambda payload: {
            **payload,
            "request": {f"hostile_{index}": index for index in range(10_000)},
        },
    ],
)
def test_public_envelope_rejects_malformed_missing_extra_bool_and_oversized_input(
    mutation: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, payload, _ = _payload(tmp_path)
    hostile = mutation(payload)  # type: ignore[operator]
    monkeypatch.setattr(
        external_drc,
        "verify_external_route_candidate_drc",
        lambda *args, **kwargs: pytest.fail("malformed input must stop before coordination"),
    )

    result = verify_external_route_candidate(hostile, Settings(workspace=tmp_path))

    assert result == {
        "schema_version": "1.0",
        "status": "refused",
        "physical_validation": "not_run",
        "segment_count": 0,
        "edge_checks": 0,
        "obstacle_checks": 0,
        "code": "invalid_request",
        "diagnostic": "external candidate verification input is invalid",
        "drc_evidence": None,
    }


@pytest.mark.parametrize(("max_grid_nodes", "expected_path_edges"), [(9_999, 4_096), (321, 321)])
def test_public_service_derives_budgets_and_mandatory_drc_from_validated_settings(
    max_grid_nodes: int,
    expected_path_edges: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, payload, _ = _payload(tmp_path)
    request = payload["request"]
    assert isinstance(request, dict)
    request["settings"] = {
        "max_obstacle_checks": 123,
        "max_grid_nodes": max_grid_nodes,
    }
    captured: dict[str, object] = {}

    def coordinate(
        typed_request: RoutePreviewRequest,
        document: object,
        settings: Settings,
        **kwargs: object,
    ) -> ExternalCandidateDrcResult:
        del document, settings
        captured.update(kwargs)
        captured["request"] = typed_request
        return ExternalCandidateDrcResult(
            verification=_refused(ExternalCandidateFailure.CANCELLED),
            drc_evidence=None,
        )

    monkeypatch.setattr(external_drc, "verify_external_route_candidate_drc", coordinate)
    result = verify_external_route_candidate(payload, Settings(workspace=tmp_path))

    typed_request = captured["request"]
    assert isinstance(typed_request, RoutePreviewRequest)
    assert typed_request.include_drc is True
    assert typed_request.include_fill_authority is False
    assert typed_request.include_apply_token is False
    assert captured["max_obstacle_checks"] == 123
    assert captured["max_path_edges"] == expected_path_edges
    assert result["code"] == "cancelled"


def test_stale_public_request_refuses_before_kicad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, payload, _ = _payload(tmp_path)
    request = payload["request"]
    assert isinstance(request, dict)
    request["expect_board_revision"] = f"sha256:{'f' * 64}"
    monkeypatch.setattr(
        external_drc,
        "run_disposed_route_candidate_drc",
        lambda *args, **kwargs: pytest.fail("stale input must stop before KiCad"),
    )

    result = verify_external_route_candidate(payload, Settings(workspace=tmp_path))

    assert result["status"] == "refused"
    assert result["code"] == "stale_revision"
    assert result["physical_validation"] == "not_run"


def test_public_operational_failure_is_one_fixed_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, payload, _ = _payload(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret board, path, and coordinates")

    monkeypatch.setattr(external_drc, "verify_external_route_candidate_drc", fail)
    with pytest.raises(ExternalCandidatePublicError) as captured:
        verify_external_route_candidate(payload, Settings(workspace=tmp_path))

    assert str(captured.value) == "external candidate verification could not be completed"


def test_unexpected_request_parser_failure_is_one_fixed_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, payload, _ = _payload(tmp_path)

    def fail_parser(payload: object) -> None:
        del payload
        raise RuntimeError("private parser detail")

    monkeypatch.setattr(external_drc, "parse_route_preview_request", fail_parser)
    with pytest.raises(ExternalCandidatePublicError) as captured:
        verify_external_route_candidate(payload, Settings(workspace=tmp_path))

    assert str(captured.value) == "external candidate verification could not be completed"


def test_non_clean_drc_is_completed_evidence_not_a_structural_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, payload, _ = _payload(tmp_path)

    def run_drc(
        path: str,
        disposition: object,
        profile: object,
        settings: Settings,
        *,
        deadline: float | None = None,
    ) -> RouteCandidateDrcEvidence:
        del path, profile, settings, deadline
        accepted = getattr(disposition, "candidate", None)
        assert isinstance(accepted, RouteCandidate)
        return _evidence(accepted, error_count=1)

    monkeypatch.setattr(external_drc, "run_disposed_route_candidate_drc", run_drc)
    result = verify_external_route_candidate(payload, Settings(workspace=tmp_path))
    validated = ExternalRouteVerificationToolResponse.model_validate(result)

    assert validated.root.status == "accepted"
    assert result["physical_validation"] == "completed"
    evidence = result["drc_evidence"]
    assert isinstance(evidence, dict)
    summary = evidence["summary"]
    assert isinstance(summary, dict)
    assert summary["passed"] is False
    assert summary["clean"] is False


def test_server_info_lists_external_verification_as_implemented() -> None:
    info = server_info()
    implemented = info["implemented"]
    planned = info["planned"]
    assert isinstance(implemented, list)
    assert isinstance(planned, list)
    marker = "versioned external route verification with mandatory candidate-bound KiCad DRC"
    assert marker in implemented
    assert marker not in planned


def test_document_schema_refusal_remains_typed_and_never_invokes_kicad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, payload, _ = _payload(tmp_path)
    document = payload["document"]
    assert isinstance(document, dict)
    document["forged_identity"] = "secret"
    monkeypatch.setattr(
        external_drc,
        "run_disposed_route_candidate_drc",
        lambda *args, **kwargs: pytest.fail("invalid document must stop before KiCad"),
    )

    result = verify_external_route_candidate(payload, Settings(workspace=tmp_path))

    assert result["status"] == "refused"
    assert result["code"] == "invalid_candidate"
    assert result["physical_validation"] == "not_run"
