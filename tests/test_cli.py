from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

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

    def test_drc_emits_service_json(self) -> None:
        root = Path(__file__).parent
        stdout = io.StringIO()
        with patch("copper_mcp.cli.run_board_drc", return_value={"passed": True}):
            with redirect_stdout(stdout):
                result = main(["--workspace", str(root), "drc", "fixtures/minimal.kicad_pcb"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"passed": True})

    def test_board_ir_reports_supported_structure(self) -> None:
        root = Path(__file__).parent / "fixtures" / "route-candidate"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = main(
                [
                    "--workspace",
                    str(root),
                    "board-ir",
                    "two-pad.kicad_pcb",
                    "--clearance-nm",
                    "250000",
                    "--track-width-nm",
                    "250000",
                    "--via-diameter-nm",
                    "800000",
                    "--via-drill-nm",
                    "400000",
                ]
            )
        self.assertEqual(result, 0)
        document = json.loads(stdout.getvalue())
        self.assertTrue(document["supported"])
        self.assertEqual(document["copper_layer_ids"], ["layer:B.Cu", "layer:F.Cu"])
        self.assertEqual(document["object_counts"]["pads"], 2)

    def test_preview_route_builds_a_validated_request(self) -> None:
        root = Path(__file__).parent / "fixtures" / "route-candidate"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = main(
                [
                    "--workspace",
                    str(root),
                    "preview-route",
                    "two-pad.kicad_pcb",
                    "--net",
                    "AUDIO",
                    "--layer",
                    "F.Cu",
                    "--clearance-nm",
                    "250000",
                    "--track-width-nm",
                    "250000",
                    "--via-diameter-nm",
                    "800000",
                    "--via-drill-nm",
                    "400000",
                    "--seed",
                    "23",
                    "--grid-step-nm",
                    "250000",
                ]
            )
        self.assertEqual(result, 0)
        document = json.loads(stdout.getvalue())
        self.assertEqual(document["status"], "routed")
        self.assertEqual(document["request"]["settings"]["grid_step_nm"], 250000)
        self.assertIsNone(document["drc_evidence"])
        self.assertEqual(len(document["candidate"]["patch"]["vertices_nm"]), 2)

    def test_preview_route_reports_invalid_requests_without_a_traceback(self) -> None:
        root = Path(__file__).parent / "fixtures" / "route-candidate"
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = main(
                [
                    "--workspace",
                    str(root),
                    "preview-route",
                    "two-pad.kicad_pcb",
                    "--net",
                    "AUDIO",
                    "--layer",
                    "F.Silkscreen",
                    "--clearance-nm",
                    "250000",
                    "--track-width-nm",
                    "250000",
                    "--via-diameter-nm",
                    "800000",
                    "--via-drill-nm",
                    "400000",
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("copper layer", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
