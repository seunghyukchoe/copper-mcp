from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from copper_mcp.security import WorkspaceViolationError, resolve_workspace_file


class WorkspaceSecurityTests(unittest.TestCase):
    def test_rejects_parent_directory_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            outside = Path(directory) / "outside.kicad_pcb"
            outside.write_text("(kicad_pcb)", encoding="utf-8")
            with self.assertRaises(WorkspaceViolationError):
                resolve_workspace_file(
                    root,
                    "../outside.kicad_pcb",
                    allowed_suffixes={".kicad_pcb"},
                    max_bytes=1024,
                )

    def test_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            outside = Path(directory) / "outside.kicad_pcb"
            outside.write_text("(kicad_pcb)", encoding="utf-8")
            link = root / "link.kicad_pcb"
            try:
                link.symlink_to(outside)
            except OSError:
                self.skipTest("symlinks are not available")
            with self.assertRaises(WorkspaceViolationError):
                resolve_workspace_file(
                    root,
                    "link.kicad_pcb",
                    allowed_suffixes={".kicad_pcb"},
                    max_bytes=1024,
                )


if __name__ == "__main__":
    unittest.main()
