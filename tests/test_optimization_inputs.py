"""Launch capture honors source identity, private scope, and operator ceilings."""

from pathlib import Path

import pytest
from test_route_bundle import FIXTURE, _payload

from copper_mcp.config import Settings
from copper_mcp.optimization.contracts import OptimizationError
from copper_mcp.optimization.inputs import prepare_optimization


@pytest.fixture
def launch(tmp_path: Path):
    source = FIXTURE.read_bytes()
    (tmp_path / "board.kicad_pcb").write_bytes(source)
    original = _payload("board.kicad_pcb", source)
    return {
        "board": original["board"],
        "expect_board_revision": original["expect_board_revision"],
        "expect_snapshot_digest": original["expect_snapshot_digest"],
        "constraints": original["constraints"],
        "target_net_refs": original["net_ref_ids"],
        "routing_settings": original["settings"],
    }


def test_capture_is_content_bound_canonical_and_does_not_write(launch, tmp_path):
    path = tmp_path / "board.kicad_pcb"
    before = path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino
    first = prepare_optimization(launch, Settings(workspace=tmp_path))
    second = prepare_optimization(
        {**launch, "target_net_refs": list(reversed(launch["target_net_refs"]))},
        Settings(workspace=tmp_path),
    )
    assert first.request.digest == second.request.digest
    assert first.request.board_revision == launch["expect_board_revision"]
    assert first.request.target_net_count == 2
    assert first.request.required_domains == ("DRC", "DFM")
    assert first.request.limits.max_runtime_ms == 30_000
    assert first.placement_intent is None
    assert before == (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
    with pytest.raises(TypeError):
        first.context["board.kicad_pcb"] = b"tampered"


@pytest.mark.parametrize(
    "change",
    [
        {"expect_board_revision": "sha256:" + "f" * 64},
        {"expect_snapshot_digest": "sha256:" + "f" * 64},
        {"target_net_refs": ["net:absent"]},
        {"target_net_refs": []},
        {"movable_footprint_refs": ["footprint:absent"]},
        {"apply_token": "PRIVATE-CANARY"},
        {"routing_settings": {"max_expansions": True}},
        {"seed": True},
    ],
)
def test_invalid_or_stale_inputs_cannot_reach_a_job(launch, tmp_path, change):
    with pytest.raises(OptimizationError) as caught:
        prepare_optimization({**launch, **change}, Settings(workspace=tmp_path))
    assert "PRIVATE-CANARY" not in str(caught.value)


def test_scope_cannot_silently_lose_duplicate_targets(launch, tmp_path):
    launch["target_net_refs"] *= 2
    with pytest.raises(OptimizationError, match="distinct"):
        prepare_optimization(launch, Settings(workspace=tmp_path))


def test_sidecar_cannot_request_apply_or_override_board(launch, tmp_path):
    (tmp_path / "intent.json").write_text('{"include_apply_token":true}')
    launch["placement_intent_path"] = "intent.json"
    with pytest.raises(OptimizationError, match="malformed"):
        prepare_optimization(launch, Settings(workspace=tmp_path))


def test_identical_board_bytes_in_different_contexts_do_not_alias_jobs(launch, tmp_path):
    first = prepare_optimization(launch, Settings(workspace=tmp_path))
    (tmp_path / "other.kicad_pcb").write_bytes(first.source)
    second = prepare_optimization(
        {**launch, "board": "other.kicad_pcb"}, Settings(workspace=tmp_path)
    )
    assert first.request.board_revision == second.request.board_revision
    assert first.request.snapshot_digest == second.request.snapshot_digest
    assert first.request.judge_profile_digest != second.request.judge_profile_digest
    assert first.request.digest != second.request.digest
