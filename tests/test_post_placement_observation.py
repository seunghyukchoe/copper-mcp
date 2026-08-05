from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from copper_mcp.config import Settings
from copper_mcp.models import DrcSummary
from copper_mcp.post_placement_observation import (
    PostPlacementObservationError,
    observe_post_placement,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "placement-v0.1" / "placement-legal.kicad_pcb"
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


def _payload(board: Path) -> dict[str, object]:
    revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    return {
        "board": board.name,
        "expect_board_revision": revision,
        "constraints": CONSTRAINTS,
        "region": {
            "min_x_nm": -10_000_000,
            "min_y_nm": -10_000_000,
            "max_x_nm": 100_000_000,
            "max_y_nm": 100_000_000,
        },
    }


def _summary(base: str, context: str) -> DrcSummary:
    return DrcSummary(
        base_revision=base,
        drc_context_revision=context,
        kicad_version="10.0.5",
        drc_schema="https://schemas.kicad.org/drc.v1.json",
        coordinate_units="mm",
        error_count=0,
        warning_count=0,
        exclusion_count=0,
        ignored_check_count=1,
        unconnected_count=0,
        violation_type_counts={},
        passed=True,
    )


def test_observation_binds_scene_and_drc_to_one_capture(tmp_path: Path, monkeypatch) -> None:
    board = tmp_path / FIXTURE.name
    original = FIXTURE.read_bytes()
    board.write_bytes(original)
    settings = Settings(workspace=tmp_path.resolve())
    calls = 0

    def fake_drc(context, *, board_relative, settings):
        nonlocal calls
        calls += 1
        assert context[board_relative] == original
        from copper_mcp.kicad_cli import _context_revision, _revision

        return _summary(_revision(context[board_relative]), _context_revision(context))

    monkeypatch.setattr("copper_mcp.post_placement_observation._run_captured_drc", fake_drc)
    before = board.stat()
    result = observe_post_placement(_payload(board), settings)
    after = board.stat()

    assert calls == 1
    assert result.board_revision == result.scene.board_revision == result.drc_summary.base_revision
    assert result.snapshot_digest == result.scene.snapshot_digest
    assert result.drc_summary.passed is True
    assert result.drc_summary.clean is False
    assert board.read_bytes() == original
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_stale_revision_refuses_before_scene_or_drc(tmp_path: Path, monkeypatch) -> None:
    board = tmp_path / FIXTURE.name
    board.write_bytes(FIXTURE.read_bytes())
    payload = _payload(board)
    payload["expect_board_revision"] = "sha256:" + "0" * 64
    settings = Settings(workspace=tmp_path.resolve())
    monkeypatch.setattr(
        "copper_mcp.post_placement_observation._observe_board_scene",
        lambda *args, **kwargs: pytest.fail("scene must not run for stale input"),
    )
    monkeypatch.setattr(
        "copper_mcp.post_placement_observation._run_captured_drc",
        lambda *args, **kwargs: pytest.fail("DRC must not run for stale input"),
    )

    with pytest.raises(PostPlacementObservationError, match="revision is stale"):
        observe_post_placement(payload, settings)


def test_context_race_discards_composite_evidence(tmp_path: Path, monkeypatch) -> None:
    board = tmp_path / FIXTURE.name
    board.write_bytes(FIXTURE.read_bytes())
    settings = Settings(workspace=tmp_path.resolve())

    def fake_drc(context, *, board_relative, settings):
        from copper_mcp.kicad_cli import _context_revision, _revision

        board.write_bytes(b"(kicad_pcb)\n")
        return _summary(_revision(context[board_relative]), _context_revision(context))

    monkeypatch.setattr("copper_mcp.post_placement_observation._run_captured_drc", fake_drc)
    with pytest.raises(PostPlacementObservationError, match="context changed"):
        observe_post_placement(_payload(board), settings)


def test_closed_request_rejects_tokens_and_render(tmp_path: Path) -> None:
    board = tmp_path / FIXTURE.name
    board.write_bytes(FIXTURE.read_bytes())
    settings = Settings(workspace=tmp_path.resolve())
    token_payload = _payload(board) | {"apply_token": "not-permitted"}
    with pytest.raises(PostPlacementObservationError, match="request is malformed"):
        observe_post_placement(token_payload, settings)
    render_payload = _payload(board) | {"include_render": True}
    with pytest.raises(PostPlacementObservationError, match="does not render"):
        observe_post_placement(render_payload, settings)
