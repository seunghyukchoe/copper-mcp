from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import copper_mcp.layered_route_preview as layered_preview
from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import Layer, NetClass, make_snapshot
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError, LayeredRouteCandidateDrcEvidence
from copper_mcp.layered_route_preview import (
    LayeredRoutePreviewError,
    parse_layered_route_preview_request,
    preview_layered_route,
)
from copper_mcp.models import DrcSummary

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
BLOCKED_PAD_FIXTURE = (
    Path(__file__).parent / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
)
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
DEFAULT = NetClass(
    id="class:request",
    name="Request",
    clearance_nm=250_000,
    track_width_nm=250_000,
    via_diameter_nm=800_000,
    via_drill_nm=400_000,
)


def _workspace(tmp_path: Path) -> tuple[Path, Settings, str, str, str]:
    board = tmp_path / FIXTURE.name
    source = FIXTURE.read_bytes()
    board.write_bytes(source)
    profile = KiCadConstraintProfile(net_classes=(DEFAULT,), default_net_class_id=DEFAULT.id)
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    pads = conversion.snapshot.content.pads
    return (
        board,
        Settings(workspace=tmp_path),
        pads[0].id,
        pads[1].id,
        conversion.snapshot.snapshot_digest,
    )


def _request(
    board: Path,
    start_pad_id: str,
    end_pad_id: str,
    board_revision: str,
    snapshot_digest: str,
    **overrides: Any,
) -> dict[str, object]:
    request: dict[str, object] = {
        "board": board.name,
        "start_pad_id": start_pad_id,
        "end_pad_id": end_pad_id,
        "expect_board_revision": board_revision,
        "expect_snapshot_digest": snapshot_digest,
        "grid_step_nm": 250_000,
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
    }
    request.update(overrides)
    return request


def test_routed_result_is_deterministic_and_contains_only_canonical_geometry(
    tmp_path: Path,
) -> None:
    board, settings, start, end, snapshot_digest = _workspace(tmp_path)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    request = _request(board, start, end, board_revision, snapshot_digest)

    first = preview_layered_route(request, settings)
    second = preview_layered_route(request, settings)

    assert first == second
    assert board.read_bytes() == FIXTURE.read_bytes()
    assert first["status"] == "routed"
    assert first["board_path"] == board.name
    assert first["snapshot_digest"] == snapshot_digest
    assert first["conversion_diagnostic_counts"] == {}
    candidate = first["candidate"]
    assert isinstance(candidate, dict)
    assert candidate["candidate_id"].startswith("sha256:")
    assert candidate["start_pad_id"] == start
    assert candidate["end_pad_id"] == end
    patch = candidate["patch"]
    assert isinstance(patch, dict)
    assert patch["paths"]
    assert "net:name:" not in repr(first["diagnostic"])


def test_file_preview_refuses_internal_three_layer_router_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, settings, start, end, _ = _workspace(tmp_path)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    profile = KiCadConstraintProfile(net_classes=(DEFAULT,), default_net_class_id=DEFAULT.id)
    conversion = parse_kicad_bytes(board.read_bytes(), profile)
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    front, back = snapshot.content.copper_layers
    internal_snapshot = make_snapshot(
        replace(
            snapshot.content,
            copper_layers=(
                front,
                Layer(id="layer:In1.Cu", name="In1.Cu", index=1),
                replace(back, index=2),
            ),
        )
    )
    monkeypatch.setattr(
        layered_preview,
        "parse_kicad_bytes",
        lambda *_args, **_kwargs: replace(conversion, snapshot=internal_snapshot),
    )

    result = preview_layered_route(
        _request(board, start, end, board_revision, internal_snapshot.snapshot_digest), settings
    )

    assert result["status"] == "unsupported_board"
    assert result["candidate"] is None
    assert result["diagnostic"]["code"] == "unsupported_geometry"  # type: ignore[index]


def test_include_drc_returns_candidate_bound_aggregate_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, settings, start, end, snapshot_digest = _workspace(tmp_path)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    captured: dict[str, object] = {}

    def fake_drc(
        requested_path: str,
        candidate: object,
        profile: object,
        drc_settings: Settings,
        *,
        request: object,
    ) -> LayeredRouteCandidateDrcEvidence:
        captured.update(
            {
                "path": requested_path,
                "candidate": candidate,
                "profile": profile,
                "settings": drc_settings,
                "request": request,
            }
        )
        assert hasattr(candidate, "candidate_id")
        candidate_id = candidate.candidate_id  # type: ignore[attr-defined]
        base_revision = candidate.base_revision  # type: ignore[attr-defined]
        patched_revision = "sha256:" + "b" * 64
        context_revision = "sha256:" + "c" * 64
        return LayeredRouteCandidateDrcEvidence(
            candidate_id=candidate_id,
            candidate_base_revision=base_revision,
            source_revision=board_revision,
            patched_board_revision=patched_revision,
            patched_drc_context_revision=context_revision,
            summary=DrcSummary(
                base_revision=patched_revision,
                drc_context_revision=context_revision,
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
            ),
        )

    monkeypatch.setattr(layered_preview, "run_layered_route_candidate_drc", fake_drc)
    result = layered_preview.preview_layered_route(
        _request(board, start, end, board_revision, snapshot_digest, include_drc=True),
        settings,
    )

    assert result["status"] == "routed"
    evidence = result["drc_evidence"]
    assert isinstance(evidence, dict)
    assert evidence["candidate_id"] == result["candidate"]["candidate_id"]  # type: ignore[index]
    assert evidence["source_revision"] == board_revision
    assert captured["path"] == board.name
    assert captured["request"].board_revision == snapshot_digest  # type: ignore[attr-defined]
    assert captured["settings"].kicad_timeout_seconds <= settings.kicad_timeout_seconds  # type: ignore[attr-defined]
    assert board.read_bytes() == FIXTURE.read_bytes()


def test_include_drc_fails_closed_when_authority_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, settings, start, end, snapshot_digest = _workspace(tmp_path)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    monkeypatch.setattr(
        layered_preview,
        "run_layered_route_candidate_drc",
        lambda *args, **kwargs: (_ for _ in ()).throw(KiCadCliError("unavailable")),
    )

    with pytest.raises(LayeredRoutePreviewError, match="DRC evidence is unavailable"):
        layered_preview.preview_layered_route(
            _request(board, start, end, board_revision, snapshot_digest, include_drc=True),
            settings,
        )


def test_include_drc_distinguishes_warning_only_authority_from_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, settings, start, end, snapshot_digest = _workspace(tmp_path)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"

    def warning_drc(
        _requested_path: str,
        candidate: object,
        _profile: object,
        _settings: Settings,
        *,
        request: object,
    ) -> LayeredRouteCandidateDrcEvidence:
        del request
        candidate_id = candidate.candidate_id  # type: ignore[attr-defined]
        base_revision = candidate.base_revision  # type: ignore[attr-defined]
        patched_revision = "sha256:" + "b" * 64
        context_revision = "sha256:" + "c" * 64
        return LayeredRouteCandidateDrcEvidence(
            candidate_id=candidate_id,
            candidate_base_revision=base_revision,
            source_revision=board_revision,
            patched_board_revision=patched_revision,
            patched_drc_context_revision=context_revision,
            summary=DrcSummary(
                base_revision=patched_revision,
                drc_context_revision=context_revision,
                kicad_version="10.0.5",
                drc_schema="https://schemas.kicad.org/drc.v1.json",
                coordinate_units="mm",
                error_count=0,
                warning_count=1,
                exclusion_count=0,
                ignored_check_count=0,
                unconnected_count=0,
                violation_type_counts={"courtyard_overlap": 1},
                passed=True,
            ),
        )

    monkeypatch.setattr(layered_preview, "run_layered_route_candidate_drc", warning_drc)
    result = layered_preview.preview_layered_route(
        _request(board, start, end, board_revision, snapshot_digest, include_drc=True),
        settings,
    )

    evidence = result["drc_evidence"]
    assert isinstance(evidence, dict)
    summary = evidence["summary"]
    assert summary["passed"] is True
    assert summary["clean"] is False
    assert summary["warning_count"] == 1
    assert summary["violation_type_counts"] == {"courtyard_overlap": 1}


@pytest.mark.parametrize(
    "authority_result, expected_message",
    [
        (KiCadCliError("KiCad DRC timed out"), "evidence is unavailable"),
        (object(), "evidence is malformed"),
    ],
)
def test_include_drc_refuses_timeout_or_malformed_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authority_result: object,
    expected_message: str,
) -> None:
    board, settings, start, end, snapshot_digest = _workspace(tmp_path)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"

    def authority(*_args: object, **_kwargs: object) -> object:
        if isinstance(authority_result, BaseException):
            raise authority_result
        return authority_result

    monkeypatch.setattr(layered_preview, "run_layered_route_candidate_drc", authority)
    with pytest.raises(LayeredRoutePreviewError, match=expected_message):
        layered_preview.preview_layered_route(
            _request(board, start, end, board_revision, snapshot_digest, include_drc=True),
            settings,
        )


def test_include_drc_refuses_evidence_bound_to_a_different_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, settings, start, end, snapshot_digest = _workspace(tmp_path)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"

    def foreign_drc(
        _requested_path: str,
        candidate: object,
        _profile: object,
        _settings: Settings,
        *,
        request: object,
    ) -> LayeredRouteCandidateDrcEvidence:
        del request
        patched_revision = "sha256:" + "b" * 64
        context_revision = "sha256:" + "c" * 64
        return LayeredRouteCandidateDrcEvidence(
            candidate_id="sha256:" + "d" * 64,
            candidate_base_revision=candidate.base_revision,  # type: ignore[attr-defined]
            source_revision=board_revision,
            patched_board_revision=patched_revision,
            patched_drc_context_revision=context_revision,
            summary=DrcSummary(
                base_revision=patched_revision,
                drc_context_revision=context_revision,
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
            ),
        )

    monkeypatch.setattr(layered_preview, "run_layered_route_candidate_drc", foreign_drc)
    with pytest.raises(LayeredRoutePreviewError, match="not bound"):
        layered_preview.preview_layered_route(
            _request(board, start, end, board_revision, snapshot_digest, include_drc=True),
            settings,
        )


def test_board_revision_cas_is_checked_before_conversion(tmp_path: Path) -> None:
    board, settings, start, end, snapshot_digest = _workspace(tmp_path)
    stale = _request(board, start, end, "sha256:" + "0" * 64, snapshot_digest)

    result = preview_layered_route(stale, settings)

    assert result["status"] == "not_routed"
    assert result["diagnostic"] == {
        "code": "stale_revision",
        "message": "board revision is stale",
        "expanded_states": 0,
        "obstacle_checks": 0,
    }
    assert result["candidate"] is None


def test_snapshot_cas_is_checked_after_conversion(tmp_path: Path) -> None:
    board, settings, start, end, _ = _workspace(tmp_path)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    stale = _request(board, start, end, board_revision, "sha256:" + "0" * 64)

    result = preview_layered_route(stale, settings)

    assert result["status"] == "not_routed"
    assert result["snapshot_digest"] is not None
    assert result["diagnostic"]["message"] == "Board IR snapshot revision is stale"  # type: ignore[index]


def test_missing_revision_preconditions_are_rejected() -> None:
    with pytest.raises(LayeredRoutePreviewError):
        parse_layered_route_preview_request(
            {"board": "board.kicad_pcb", "start_pad_id": "pad:a", "end_pad_id": "pad:b"}
        )


def test_include_drc_requires_a_real_boolean() -> None:
    with pytest.raises(LayeredRoutePreviewError):
        parse_layered_route_preview_request(
            {
                "board": "board.kicad_pcb",
                "start_pad_id": "pad:a",
                "end_pad_id": "pad:b",
                "expect_board_revision": "sha256:" + "0" * 64,
                "expect_snapshot_digest": "sha256:" + "0" * 64,
                "constraints": {
                    "clearance_nm": 250_000,
                    "track_width_nm": 250_000,
                    "via_diameter_nm": 800_000,
                    "via_drill_nm": 400_000,
                },
                "include_drc": 1,
            }
        )


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_public_include_drc_binds_real_kicad_evidence_without_source_mutation(
    tmp_path: Path,
) -> None:
    board = tmp_path / BLOCKED_PAD_FIXTURE.name
    source = BLOCKED_PAD_FIXTURE.read_bytes()
    board.write_bytes(source)
    profile = KiCadConstraintProfile(net_classes=(DEFAULT,), default_net_class_id=DEFAULT.id)
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    pads = tuple(
        pad
        for pad in conversion.snapshot.content.pads
        if pad.net_id == conversion.snapshot.content.pads[0].net_id
    )
    assert len(pads) >= 2
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"
    before = board.stat()
    result = preview_layered_route(
        {
            "board": board.name,
            "start_pad_id": pads[0].id,
            "end_pad_id": pads[1].id,
            "expect_board_revision": board_revision,
            "expect_snapshot_digest": conversion.snapshot.snapshot_digest,
            "grid_step_nm": 250_000,
            "constraints": {
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            },
            "include_drc": True,
        },
        Settings(workspace=tmp_path, kicad_cli=REAL_KICAD_CLI, max_route_preview_seconds=30),
    )
    after = board.stat()
    assert result["status"] == "routed"
    evidence = result["drc_evidence"]
    assert isinstance(evidence, dict)
    assert evidence["candidate_id"] == result["candidate"]["candidate_id"]  # type: ignore[index]
    summary = evidence["summary"]
    assert summary["passed"] is True
    assert summary["error_count"] == 0
    assert summary["warning_count"] == 0
    assert summary["unconnected_count"] == 0
    assert before.st_ino == after.st_ino
    assert before.st_mtime_ns == after.st_mtime_ns
    assert board.read_bytes() == source


def test_raw_net_selector_is_not_accepted() -> None:
    with pytest.raises(LayeredRoutePreviewError):
        parse_layered_route_preview_request(
            {
                "board": "board.kicad_pcb",
                "start_pad_id": "pad:a",
                "end_pad_id": "pad:b",
                "expect_board_revision": "sha256:" + "0" * 64,
                "expect_snapshot_digest": "sha256:" + "0" * 64,
                "constraints": {
                    "clearance_nm": 250_000,
                    "track_width_nm": 250_000,
                    "via_diameter_nm": 800_000,
                    "via_drill_nm": 400_000,
                },
                "net": "SECRET_NET_NAME",
            }
        )


def test_board_changes_are_stale_without_echoing_board_data(tmp_path: Path) -> None:
    board, settings, start, end, snapshot_digest = _workspace(tmp_path)
    source = board.read_bytes().replace(b"AUDIO", b"OTHER")
    board.write_bytes(source)
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"

    result = preview_layered_route(
        _request(board, start, end, board_revision, snapshot_digest), settings
    )

    assert result["status"] == "not_routed"
    assert "OTHER" not in repr(result)
    assert "AUDIO" not in repr(result)


def test_layer_selectors_are_normalized() -> None:
    request = parse_layered_route_preview_request(
        {
            "board": "board.kicad_pcb",
            "start_pad_id": "pad:a",
            "end_pad_id": "pad:b",
            "expect_board_revision": "sha256:" + "0" * 64,
            "expect_snapshot_digest": "sha256:" + "0" * 64,
            "constraints": {
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            },
            "start_layer_id": "layer:F.Cu",
            "end_layer_id": "layer:B.Cu",
        }
    )
    assert request.start_layer_id == "layer:F.Cu"
    assert request.end_layer_id == "layer:B.Cu"


def test_undocumented_layer_alias_is_rejected() -> None:
    with pytest.raises(LayeredRoutePreviewError):
        parse_layered_route_preview_request(
            {
                "board": "board.kicad_pcb",
                "start_pad_id": "pad:a",
                "end_pad_id": "pad:b",
                "constraints": {
                    "clearance_nm": 250_000,
                    "track_width_nm": 250_000,
                    "via_diameter_nm": 800_000,
                    "via_drill_nm": 400_000,
                },
                "expect_board_revision": "sha256:" + "0" * 64,
                "expect_snapshot_digest": "sha256:" + "0" * 64,
                "start_layer": "F.Cu",
            }
        )
