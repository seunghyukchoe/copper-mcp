from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.apply import ApplyTokenAuthority, apply_placement_candidate
from copper_mcp.apply import service as apply_service
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


def _preview(tmp_path: Path):
    board = tmp_path / FIXTURE.name
    source = FIXTURE.read_bytes()
    board.write_bytes(source)
    settings = replace(Settings(workspace=tmp_path.resolve()), allow_apply=True)
    conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
    assert conversion.snapshot is not None
    refs = sorted(build_placement_view(source, conversion.snapshot).footprints)
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


def test_post_publish_verification_reparses_the_authorized_placement(tmp_path: Path) -> None:
    board, _, settings, authority, preview = _preview(tmp_path)

    result = apply_placement_candidate(_request(preview, board), settings, authority)

    assert result.status == "applied"
    assert result.verification is not None
    assert preview.candidate is not None
    conversion = parse_kicad_bytes(board.read_bytes(), _profile(), ParseLimits())
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    actual_poses = {
        item.id: (item.origin.x, item.origin.y, item.rotation_udeg)
        for item in conversion.snapshot.content.footprints
    }
    expected_poses = {
        item.ref_id: (item.origin_x_nm, item.origin_y_nm, item.orientation_udeg)
        for item in preview.candidate.placements
    }
    assert actual_poses == expected_poses


def test_concurrent_post_publish_change_is_applied_but_unverified(
    tmp_path: Path, monkeypatch
) -> None:
    board, source, settings, authority, preview = _preview(tmp_path)
    concurrent_content = b"(kicad_pcb)\n"
    original_replace = apply_service.replace_workspace_file
    changed = False

    def replace_then_concurrently_change(*args, **kwargs):
        nonlocal changed
        published = original_replace(*args, **kwargs)
        if not changed:
            changed = True
            published.write_bytes(concurrent_content)
        return published

    monkeypatch.setattr(apply_service, "replace_workspace_file", replace_then_concurrently_change)

    result = apply_placement_candidate(_request(preview, board), settings, authority)

    assert result.status == "applied_but_unverified"
    assert result.diagnostic is not None
    assert result.diagnostic.code == "apply_verification_failed"
    assert result.board_revision_after == f"sha256:{hashlib.sha256(concurrent_content).hexdigest()}"
    assert board.read_bytes() == concurrent_content
    assert (settings.workspace / str(result.backup_path)).read_bytes() == source
