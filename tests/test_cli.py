from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from copper_mcp.cli import main


class CliTests(unittest.TestCase):
    def test_info_is_machine_readable(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = main(["info"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue())["name"], "CopperMCP")

    def test_inspect_uses_explicit_workspace(self) -> None:
        root = Path(__file__).parent
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = main(["--workspace", str(root), "inspect", "fixtures/minimal.kicad_pcb"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue())["counts"]["nets"], 2)


if __name__ == "__main__":
    unittest.main()
