from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_layered_tree_router import FIXTURE, _case

from copper_mcp.adapters.kicad_layered_tree_patch import (
    render_kicad_layered_tree_candidate_board,
)
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import run_board_drc

_CLI = Path(
    os.environ.get(
        "COPPER_MCP_TEST_PROJECT_ERC_CLI",
        "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    )
)


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CLI.is_file(), reason="KiCad CLI is not installed")
def test_native_drc_confirms_complete_outer_pad_tree_using_inner_copper(tmp_path: Path) -> None:
    # The two unrelated blockers have separate one-pad nets: neither is another
    # unfinished target hidden by a selected-net-only connectivity assertion.
    fixture = (FIXTURE.parent / "layered-tree-ordinary-4layer.kicad_pcb").read_bytes()
    assert fixture.count(b'(net "POWER")') == 2
    source = fixture.replace(b'(net "POWER")', b'(net "BLOCKER_F")', 1).replace(
        b'(net "POWER")', b'(net "BLOCKER_B")', 1
    )
    profile, source, snapshot, request, candidate = _case(source)
    assert len(candidate.terminals) == 3
    assert all(
        terminal.layer_id in {"layer:F.Cu", "layer:B.Cu"} for terminal in candidate.terminals
    )
    assert any(
        path.layer_id.startswith("layer:In")
        for branch in candidate.branches
        for path in branch.paths
    )
    rendered = render_kicad_layered_tree_candidate_board(
        source, snapshot, candidate, profile, request=request
    )
    assert rendered == render_kicad_layered_tree_candidate_board(
        source, snapshot, candidate, profile, request=request
    )
    board = tmp_path / "source.kicad_pcb"
    board.write_bytes(source)
    original_stat = board.stat()
    settings = Settings(workspace=tmp_path, kicad_cli=_CLI)
    before = run_board_drc(board.name, settings)
    assert before.unconnected_count > 0
    result_board = tmp_path / "candidate.kicad_pcb"
    result_board.write_bytes(rendered)
    after = run_board_drc(result_board.name, settings)
    assert after.kicad_version.startswith("10.")
    assert after.passed and after.error_count == 0 and after.unconnected_count == 0
    assert board.read_bytes() == source and result_board.read_bytes() == rendered
    assert board.stat().st_ino == original_stat.st_ino
    assert board.stat().st_mtime_ns == original_stat.st_mtime_ns
