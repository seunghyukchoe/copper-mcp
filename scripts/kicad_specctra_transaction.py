#!/usr/bin/env python3
"""Run one bounded KiCad Specctra export or session-import transaction.

This adapter is intentionally executed by the KiCad-bundled Python interpreter, rather than by
the benchmark process itself.  It uses KiCad's documented ``pcbnew.ExportSpecctraDSN`` and
``pcbnew.ImportSpecctraSES`` bindings on a disposable board copy supplied by the harness.  The
caller owns all path selection, byte ceilings, process lifetime, and output hashing; this small
adapter owns no benchmark policy and emits no board contents or diagnostics.

Official API reference (KiCad 10.0.5 locally):
https://docs.kicad.org/doxygen-python-10.0/namespacepcbnew.html
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _board(path: Path) -> object:
    import pcbnew  # type: ignore[import-not-found]

    board = pcbnew.LoadBoard(str(path))
    if board is None:
        raise ValueError("KiCad could not load the disposable board")
    return board


def export_dsn(source: Path, output: Path) -> None:
    import pcbnew  # type: ignore[import-not-found]

    if not pcbnew.ExportSpecctraDSN(_board(source), str(output)):
        raise ValueError("KiCad did not export Specctra DSN")


def import_ses(source: Path, ses: Path, output: Path) -> None:
    import pcbnew  # type: ignore[import-not-found]

    board = _board(source)
    if not pcbnew.ImportSpecctraSES(board, str(ses)):
        raise ValueError("KiCad did not import Specctra SES")
    if not pcbnew.SaveBoard(str(output), board):
        raise ValueError("KiCad did not save the imported disposable board")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export-dsn")
    export.add_argument("--source", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    imported = commands.add_parser("import-ses")
    imported.add_argument("--source", type=Path, required=True)
    imported.add_argument("--ses", type=Path, required=True)
    imported.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "export-dsn":
        export_dsn(args.source, args.output)
    else:
        import_ses(args.source, args.ses, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
