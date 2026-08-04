from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.layered_route_preview import (
    LayeredRoutePreviewError,
    parse_layered_route_preview_request,
    preview_layered_route,
)

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
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
