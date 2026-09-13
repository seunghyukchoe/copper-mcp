"""Fixed KiCad Specctra operations for a disposable optimization workspace.

This module is executed by the operator-pinned KiCad-bundled Python. The caller fixes the
working directory and filenames; no request-selected path or command reaches this process.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SOURCE = Path("board.kicad_pcb")
_DSN = Path("board.dsn")
_SES = Path("board.ses")
_ROUTED = Path("routed.kicad_pcb")


def _board(path: Path) -> Any:
    import pcbnew  # type: ignore[import-not-found]

    board = pcbnew.LoadBoard(str(path))
    if board is None:
        raise ValueError("KiCad could not load the disposable board")
    return board


def main() -> int:
    import pcbnew

    if sys.argv[1:] == ["export"]:
        board = _board(_SOURCE)
        for item in board.GetTracks():
            item.SetLocked(True)
        if not pcbnew.ExportSpecctraDSN(board, str(_DSN)):
            raise ValueError("KiCad did not export Specctra DSN")
        return 0
    if sys.argv[1:] == ["import"]:
        board = _board(_SOURCE)
        original_locks = {}
        for item in board.GetTracks():
            identity = item.m_Uuid.AsString()
            if identity in original_locks:
                raise ValueError("Duplicate original copper identity")
            original_locks[identity] = item.IsLocked()
            item.SetLocked(True)
        if not pcbnew.ImportSpecctraSES(board, str(_SES)):
            raise ValueError("KiCad did not import Specctra SES")
        retained = set()
        for item in board.GetTracks():
            identity = item.m_Uuid.AsString()
            if identity in original_locks:
                if identity in retained:
                    raise ValueError("Duplicate retained copper identity")
                retained.add(identity)
                item.SetLocked(original_locks[identity])
        if retained != set(original_locks):
            raise ValueError("KiCad did not retain the original copper")
        if not pcbnew.SaveBoard(str(_ROUTED), board):
            raise ValueError("KiCad did not save the imported disposable board")
        return 0
    raise ValueError("Specctra operation is invalid")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(1) from None
