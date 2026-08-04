from __future__ import annotations

import tempfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.apply import (
    ApplyBinding,
    ApplyTokenAuthority,
    apply_placement_candidate,
    apply_placement_candidate_bytes,
    lockfile_for,
)
from copper_mcp.apply.engine import ApplyEngineError
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.config import Settings
from copper_mcp.placement import build_placement_view
from copper_mcp.placement_preview import preview_placement

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "placement-v0.1" / "placement-legal.kicad_pcb"
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:request", name="Request", **CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _fixture(tmp: Path, *, allow_apply: bool = True):
    board = tmp / FIXTURE.name
    source = FIXTURE.read_bytes()
    board.write_bytes(source)
    settings = replace(Settings(workspace=tmp.resolve()), allow_apply=allow_apply)
    conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
    assert conversion.snapshot is not None
    view = build_placement_view(source, conversion.snapshot)
    refs = sorted(view.footprints)
    authority = ApplyTokenAuthority()
    preview = preview_placement(
        {
            "board": board.name,
            "constraints": CONSTRAINTS,
            "subjects": refs,
            "proposals": [{"subject": refs[0], "offset_x_nm": 1_000_000}],
            "include_apply_token": True,
        },
        settings,
        authority,
    )
    assert preview.candidate is not None
    assert preview.apply_token is not None
    return board, source, settings, authority, preview


def _request(preview, board: Path) -> dict[str, object]:
    assert preview.candidate is not None
    assert preview.apply_token is not None
    return {
        "board": board.name,
        "candidate": preview.candidate.to_dict(),
        "apply_token": preview.apply_token,
        "expect_board_revision": preview.board_revision,
        "constraints": CONSTRAINTS,
    }


def test_preview_mints_no_capability_without_explicit_request() -> None:
    with tempfile.TemporaryDirectory() as directory:
        board = Path(directory) / FIXTURE.name
        board.write_bytes(FIXTURE.read_bytes())
        settings = replace(Settings(workspace=Path(directory).resolve()), allow_apply=True)
        authority = ApplyTokenAuthority()
        source = board.read_bytes()
        conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
        assert conversion.snapshot is not None
        ref = sorted(build_placement_view(source, conversion.snapshot).footprints)[0]
        result = preview_placement(
            {
                "board": board.name,
                "constraints": CONSTRAINTS,
                "subjects": [ref],
                "proposals": [{"subject": ref, "offset_x_nm": 1_000_000}],
            },
            settings,
            authority,
        )
        assert result.candidate is not None
        assert result.apply_token is None


def test_authorized_apply_moves_only_the_requested_footprint_and_keeps_undo_copy() -> None:
    with tempfile.TemporaryDirectory() as directory:
        board, original, settings, authority, preview = _fixture(Path(directory))
        result = apply_placement_candidate(_request(preview, board), settings, authority)

        assert result.status == "applied"
        assert result.footprints_moved == 1
        assert result.bytes_changed > 0
        assert result.verification is not None
        backup = settings.workspace / str(result.backup_path)
        assert backup.read_bytes() == original
        assert board.read_bytes() != original

        parsed = parse_kicad_bytes(board.read_bytes(), _profile(), ParseLimits())
        assert parsed.diagnostics == ()
        assert parsed.snapshot is not None
        assert parsed.snapshot.content.source.revision == result.board_revision_after


def test_route_scoped_token_cannot_authorize_placement() -> None:
    with tempfile.TemporaryDirectory() as directory:
        board, _, settings, authority, preview = _fixture(Path(directory))
        assert preview.candidate is not None
        route_token = authority.issue(
            ApplyBinding(
                candidate_id=preview.candidate.candidate_id,
                base_revision=preview.candidate.base_revision,
                board_revision=preview.board_revision,
                relative_path=board.name,
                operation="route",
            )
        )
        request = _request(preview, board)
        request["apply_token"] = route_token
        result = apply_placement_candidate(request, settings, authority)
        assert result.status == "refused"
        assert result.diagnostic is not None
        assert result.diagnostic.code == "invalid_token"


def test_placement_token_is_single_use_and_stale_board_is_never_refreshed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        board, _, settings, authority, preview = _fixture(Path(directory))
        request = _request(preview, board)
        first = apply_placement_candidate(request, settings, authority)
        assert first.status == "applied"
        second = apply_placement_candidate(request, settings, authority)
        assert second.status == "refused"
        assert second.diagnostic is not None
        assert second.diagnostic.code == "token_already_used"


def test_lockfile_refusal_does_not_consume_the_placement_token() -> None:
    with tempfile.TemporaryDirectory() as directory:
        board, _, settings, authority, preview = _fixture(Path(directory))
        lock = lockfile_for(board)
        lock.write_text("user@host", encoding="utf-8")
        blocked = apply_placement_candidate(_request(preview, board), settings, authority)
        assert blocked.status == "refused"
        assert blocked.diagnostic is not None
        assert blocked.diagnostic.code == "kicad_open"
        lock.unlink()
        assert apply_placement_candidate(_request(preview, board), settings, authority).status == (
            "applied"
        )


def test_pure_engine_refuses_a_noop_candidate() -> None:
    with tempfile.TemporaryDirectory() as directory:
        board = Path(directory) / FIXTURE.name
        source = FIXTURE.read_bytes()
        board.write_bytes(source)
        conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
        assert conversion.snapshot is not None
        view = build_placement_view(source, conversion.snapshot)
        preview = preview_placement(
            {
                "board": board.name,
                "constraints": CONSTRAINTS,
                "subjects": sorted(view.footprints),
            },
            Settings(workspace=Path(directory).resolve()),
        )
        assert preview.candidate is not None
        with pytest.raises(ApplyEngineError, match="no pose changes"):
            apply_placement_candidate_bytes(
                source, conversion.snapshot, preview.candidate, _profile()
            )


def test_malformed_placement_pose_is_refused_without_a_partial_write() -> None:
    with tempfile.TemporaryDirectory() as directory:
        board, original, settings, authority, preview = _fixture(Path(directory))
        request = _request(preview, board)
        candidate = deepcopy(request["candidate"])
        assert isinstance(candidate, dict)
        placements = candidate["placements"]
        assert isinstance(placements, list)
        placements[0]["moved"] = 1
        request["candidate"] = candidate
        result = apply_placement_candidate(request, settings, authority)
        assert result.status == "refused"
        assert result.diagnostic is not None
        assert result.diagnostic.code == "invalid_request"
        assert board.read_bytes() == original
