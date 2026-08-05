from __future__ import annotations

import os

from scripts.benchmark_post_placement_observation import _workspace_state


def test_workspace_state_detects_metadata_only_touch(tmp_path) -> None:
    board = tmp_path / "board.kicad_pcb"
    board.write_bytes(b"stable board bytes")

    before = _workspace_state(tmp_path)
    stat = board.stat()
    os.utime(board, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    after = _workspace_state(tmp_path)

    assert before["digest"] != after["digest"]
    assert before["entries"] == after["entries"] == 1
