from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import copper_mcp.layered_route_preview as layered_preview
from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import Layer, NetClass, PointNM, make_snapshot
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    FillIsland,
    KiCadCliError,
    LayeredRouteCandidateDrcEvidence,
    ZoneFillAuthority,
    ZoneFillStaleError,
)
from copper_mcp.layered_route_preview import (
    LayeredRoutePreviewError,
    parse_layered_route_preview_request,
    preview_layered_route,
)
from copper_mcp.models import DrcSummary
from copper_mcp.routing import fill_binding_for

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
BLOCKED_PAD_FIXTURE = (
    Path(__file__).parent / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
)
FOUR_LAYER_FIXTURE = (
    Path(__file__).parent / "fixtures" / "route-candidate" / "four-layer-blocked-outers.kicad_pcb"
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


def test_file_preview_refuses_a_real_four_layer_board(tmp_path: Path) -> None:
    """Prove the public two-layer boundary on committed KiCad bytes, not a patched snapshot.

    Every other boundary regression monkeypatches ``parse_kicad_bytes`` with a hand-built stack, so
    none of them can observe a real parse producing four copper layers.  This one routes the
    committed, KiCad 10.0.5-accepted four-layer fixture through the real public entry point.
    """

    board = tmp_path / FOUR_LAYER_FIXTURE.name
    source = FOUR_LAYER_FIXTURE.read_bytes()
    board.write_bytes(source)
    profile = KiCadConstraintProfile(net_classes=(DEFAULT,), default_net_class_id=DEFAULT.id)
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    assert len(conversion.snapshot.content.copper_layers) == 4
    endpoints = tuple(
        pad for pad in conversion.snapshot.content.pads if pad.center.x in (10_000_000, 30_000_000)
    )

    result = preview_layered_route(
        _request(
            board,
            endpoints[0].id,
            endpoints[1].id,
            f"sha256:{hashlib.sha256(source).hexdigest()}",
            conversion.snapshot.snapshot_digest,
            grid_step_nm=1_000_000,
        ),
        Settings(workspace=tmp_path),
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


def test_the_file_backed_surface_mints_no_capability_even_with_live_apply_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the live surface mints. The contract's comment says so; this pins it.

    Both operator grants are set to the state that authorizes a live mint, because the property
    worth pinning is that they do not reach this surface at all -- not that they happen to be
    absent in the other tests.
    """

    monkeypatch.setenv("COPPER_MCP_ALLOW_LIVE_APPLY", "1")
    monkeypatch.setenv("COPPER_MCP_ALLOW_LIVE_IPC", "1")
    board, settings, start, end, snapshot_digest = _workspace(tmp_path)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    request = _request(board, start, end, board_revision, snapshot_digest)

    result = preview_layered_route(request, settings)

    assert result["status"] == "routed"
    assert result["apply_token"] is None


# --- Public layered fill authority and routing_effect provenance (ADR-0106, issue #164) --------

ZONE_FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "blocked-zone.kicad_pcb"


def _zone_workspace(tmp_path: Path) -> tuple[Path, Settings, str, str, str, str, Any]:
    board = tmp_path / ZONE_FIXTURE.name
    source = ZONE_FIXTURE.read_bytes()
    board.write_bytes(source)
    profile = KiCadConstraintProfile(net_classes=(DEFAULT,), default_net_class_id=DEFAULT.id)
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    pads = snapshot.content.pads
    return (
        board,
        Settings(workspace=tmp_path),
        pads[0].id,
        pads[1].id,
        f"sha256:{hashlib.sha256(source).hexdigest()}",
        snapshot.snapshot_digest,
        snapshot,
    )


def _authority(board_revision: str) -> ZoneFillAuthority:
    return ZoneFillAuthority(
        source_revision=board_revision,
        context_revision=f"sha256:{'a' * 64}",
        source_fill_digest=f"sha256:{'b' * 64}",
        refilled_fill_digest=f"sha256:{'b' * 64}",
        kicad_version="10.0.5",
        fill_polygon_count=1,
        fill_vertex_count=4,
    )


def _fill_island(snapshot: Any) -> FillIsland:
    """The pour inside the fixture's blocking zone, clipped to its lower half."""

    return FillIsland(
        net_id=snapshot.content.zones[0].net_id,
        layer_id="layer:F.Cu",
        points=(
            PointNM(18_000_000, 11_000_000),
            PointNM(22_000_000, 11_000_000),
            PointNM(22_000_000, 14_000_000),
            PointNM(18_000_000, 14_000_000),
        ),
    )


def test_layered_fill_authority_is_opt_in_and_reports_its_routing_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, settings, start, end, board_revision, snapshot_digest, snapshot = _zone_workspace(
        tmp_path
    )
    authority = _authority(board_revision)
    monkeypatch.setattr(
        layered_preview,
        "run_zone_fill_authority",
        lambda *_: (authority, (_fill_island(snapshot),)),
    )

    fill_routed = preview_layered_route(
        _request(
            board,
            start,
            end,
            board_revision,
            snapshot_digest,
            grid_step_nm=500_000,
            include_fill_authority=True,
        ),
        settings,
    )
    envelope = preview_layered_route(
        _request(board, start, end, board_revision, snapshot_digest, grid_step_nm=500_000),
        settings,
    )

    assert fill_routed["status"] == "routed"
    fill_record = fill_routed["fill_authority"]
    assert isinstance(fill_record, dict)
    assert fill_record["source_revision"] == board_revision
    # The pour is foreign to the routed net, so it can only have acted as an obstacle.
    assert fill_record["routing_effect"] == "foreign_zone_obstacles"
    assert fill_routed["candidate"]["fill_binding"] is not None  # type: ignore[index]
    assert fill_routed["request"]["include_fill_authority"] is True  # type: ignore[index]
    # Not vacuous: the same request without the flag reaches no fill at all, and its candidate
    # records that it was routed under the conservative envelope.
    assert envelope["status"] == "routed"
    assert envelope["fill_authority"] is None
    assert envelope["candidate"]["fill_binding"] is None  # type: ignore[index]
    assert envelope["request"]["include_fill_authority"] is False  # type: ignore[index]
    assert envelope["candidate"]["patch"] != fill_routed["candidate"]["patch"]  # type: ignore[index]
    assert board.read_bytes() == ZONE_FIXTURE.read_bytes()


def test_layered_fill_authority_fails_closed_on_a_stale_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache KiCad does not reproduce is not evidence, so the proposal refuses rather than
    routing against it."""

    board, settings, start, end, board_revision, snapshot_digest, _ = _zone_workspace(tmp_path)

    def stale(*_: object) -> tuple[ZoneFillAuthority, tuple[FillIsland, ...]]:
        raise ZoneFillStaleError("cached zone fill does not match a fresh refill")

    monkeypatch.setattr(layered_preview, "run_zone_fill_authority", stale)

    result = preview_layered_route(
        _request(
            board,
            start,
            end,
            board_revision,
            snapshot_digest,
            grid_step_nm=500_000,
            include_fill_authority=True,
        ),
        settings,
    )

    assert result["status"] == "not_routed"
    assert result["candidate"] is None
    assert result["fill_authority"] is None
    diagnostic = result["diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["code"] == "stale_fill"
    # A refusal, not a fallback: the same request without the flag routes on this board.
    unflagged = preview_layered_route(
        _request(board, start, end, board_revision, snapshot_digest, grid_step_nm=500_000),
        settings,
    )
    assert unflagged["status"] == "routed"


def test_layered_fill_authority_is_not_run_for_a_board_with_no_searched_zone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag is a request to prove a cache fresh, and there is no cache to prove."""

    board, settings, start, end, snapshot_digest = _workspace(tmp_path)
    board_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"

    def refuse(*_: object) -> tuple[ZoneFillAuthority, tuple[FillIsland, ...]]:
        raise AssertionError("zone fill authority must not run for a zoneless board")

    monkeypatch.setattr(layered_preview, "run_zone_fill_authority", refuse)

    result = preview_layered_route(
        _request(board, start, end, board_revision, snapshot_digest, include_fill_authority=True),
        settings,
    )

    assert result["status"] == "routed"
    assert result["fill_authority"] is None
    assert result["candidate"]["fill_binding"] is None  # type: ignore[index]


def test_layered_candidate_drc_receives_the_fill_the_candidate_was_routed_under(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview holds the evidence, so it is the only caller that can supply it.

    The layered DRC path replays the candidate inside the serializer, and that replay refuses any
    obstacle model but the recorded one -- so a request that did not carry the fill forward would
    make ``include_drc`` with ``include_fill_authority`` permanently unusable.
    """

    board, settings, start, end, board_revision, snapshot_digest, snapshot = _zone_workspace(
        tmp_path
    )
    authority = _authority(board_revision)
    monkeypatch.setattr(
        layered_preview,
        "run_zone_fill_authority",
        lambda *_: (authority, (_fill_island(snapshot),)),
    )
    observed: dict[str, Any] = {}

    def capture(
        requested_path: str,
        candidate: object,
        profile: object,
        drc_settings: Settings,
        *,
        request: Any,
    ) -> LayeredRouteCandidateDrcEvidence:
        observed["fill"] = request.verified_fill
        observed["candidate"] = candidate
        raise KiCadCliError("stop after capturing the forwarded evidence")

    monkeypatch.setattr(layered_preview, "run_layered_route_candidate_drc", capture)

    with pytest.raises(LayeredRoutePreviewError):
        preview_layered_route(
            _request(
                board,
                start,
                end,
                board_revision,
                snapshot_digest,
                grid_step_nm=500_000,
                include_drc=True,
                include_fill_authority=True,
            ),
            settings,
        )

    assert observed["candidate"].fill_binding is not None
    assert fill_binding_for(observed["fill"]) == observed["candidate"].fill_binding


def test_layered_include_fill_authority_requires_a_real_boolean() -> None:
    with pytest.raises(LayeredRoutePreviewError, match="include_fill_authority"):
        parse_layered_route_preview_request(
            {
                "board": "board.kicad_pcb",
                "start_pad_id": "pad:a",
                "end_pad_id": "pad:b",
                "expect_board_revision": f"sha256:{'a' * 64}",
                "expect_snapshot_digest": f"sha256:{'b' * 64}",
                "constraints": {
                    "clearance_nm": 250_000,
                    "track_width_nm": 250_000,
                    "via_diameter_nm": 800_000,
                    "via_drill_nm": 400_000,
                },
                "include_fill_authority": "yes",
            }
        )


def test_a_layered_routing_effect_cannot_be_reported_without_the_authority_behind_it() -> None:
    """A label is a claim about evidence, so it must not be constructible without the evidence."""

    request = parse_layered_route_preview_request(
        {
            "board": "board.kicad_pcb",
            "start_pad_id": "pad:a",
            "end_pad_id": "pad:b",
            "expect_board_revision": f"sha256:{'a' * 64}",
            "expect_snapshot_digest": f"sha256:{'b' * 64}",
            "constraints": {
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            },
        }
    )

    with pytest.raises(LayeredRoutePreviewError, match="requires fill authority"):
        layered_preview._empty_result(
            "routed",
            request,
            "board.kicad_pcb",
            f"sha256:{'a' * 64}",
            fill_routing_effect="foreign_zone_obstacles",
        )
    with pytest.raises(LayeredRoutePreviewError, match="routing effect is malformed"):
        layered_preview._empty_result(
            "routed",
            request,
            "board.kicad_pcb",
            f"sha256:{'a' * 64}",
            fill_authority=_authority(f"sha256:{'a' * 64}"),
            fill_routing_effect="mostly_harmless",
        )
