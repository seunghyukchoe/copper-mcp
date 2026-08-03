from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from copper_mcp.circuit_intent_service import build_schematic_from_snapshot_json
from copper_mcp.cli import main

ROOT = Path(__file__).resolve().parents[1]
CIRCUIT_FIXTURE = (
    ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-intent-v1.json"
)


def _file_state(path: Path) -> tuple[bytes, int, int, int]:
    stat = path.stat()
    return path.read_bytes(), stat.st_ino, stat.st_size, stat.st_mtime_ns


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

    def test_render_schematic_creates_one_exact_new_file_with_redacted_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "intent.json"
            output = workspace / "generated.kicad_sch"
            source.write_bytes(CIRCUIT_FIXTURE.read_bytes())
            source_before = _file_state(source)
            expected = build_schematic_from_snapshot_json(source_before[0])
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                result = main(
                    [
                        "--workspace",
                        str(workspace),
                        "render-schematic",
                        source.name,
                        "--output",
                        output.name,
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(output.read_bytes(), expected.artifact.content)
            self.assertEqual(_file_state(source), source_before)
            self.assertEqual(
                {path.name for path in workspace.iterdir()},
                {source.name, output.name},
            )
            document = json.loads(stdout.getvalue())
            expected_document = expected.to_dict()
            expected_document["export"] = {
                "created": True,
                "output_path": output.name,
            }
            self.assertEqual(document, expected_document)
            build_schema = json.loads(
                (
                    ROOT
                    / "schemas"
                    / "circuit-schematic-build"
                    / "0.1.0.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(build_schema)
            Draft202012Validator(build_schema).validate(document)
            serialized = json.dumps(document, sort_keys=True)
            for private_value in (
                "AUDIO_IN",
                "AUDIO_OUT",
                "GND",
                "100n",
                "1k",
                "component:c-filter",
                "(kicad_sch",
            ):
                self.assertNotIn(private_value, serialized)

    def test_render_schematic_refuses_to_overwrite_an_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "intent.json"
            output = workspace / "existing.kicad_sch"
            source.write_bytes(CIRCUIT_FIXTURE.read_bytes())
            output.write_bytes(b"user-owned schematic")
            source_before = _file_state(source)
            output_before = _file_state(output)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = main(
                    [
                        "--workspace",
                        str(workspace),
                        "render-schematic",
                        source.name,
                        "--output",
                        output.name,
                    ]
                )

            self.assertEqual(result, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(_file_state(source), source_before)
            self.assertEqual(_file_state(output), output_before)

    def test_render_schematic_rejects_uppercase_output_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "intent.json"
            output = workspace / "generated.KICAD_SCH"
            source.write_bytes(CIRCUIT_FIXTURE.read_bytes())
            source_before = _file_state(source)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = main(
                    [
                        "--workspace",
                        str(workspace),
                        "render-schematic",
                        source.name,
                        "--output",
                        output.name,
                    ]
                )

            self.assertEqual(result, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertFalse(output.exists())
            self.assertEqual(_file_state(source), source_before)

    def test_render_schematic_rejects_output_traversal_and_wrong_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            workspace.mkdir()
            source = workspace / "intent.json"
            source.write_bytes(CIRCUIT_FIXTURE.read_bytes())
            source_before = _file_state(source)

            for requested_output in ("../escaped.kicad_sch", "wrong-extension.txt"):
                with self.subTest(output=requested_output):
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        result = main(
                            [
                                "--workspace",
                                str(workspace),
                                "render-schematic",
                                source.name,
                                "--output",
                                requested_output,
                            ]
                        )
                    self.assertEqual(result, 2)

            self.assertFalse((base / "escaped.kicad_sch").exists())
            self.assertFalse((workspace / "wrong-extension.txt").exists())
            self.assertEqual(_file_state(source), source_before)

    def test_render_schematic_rejects_a_symlinked_output_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            outside = base / "outside"
            workspace.mkdir()
            outside.mkdir()
            source = workspace / "intent.json"
            source.write_bytes(CIRCUIT_FIXTURE.read_bytes())
            source_before = _file_state(source)
            (workspace / "linked").symlink_to(outside, target_is_directory=True)
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                result = main(
                    [
                        "--workspace",
                        str(workspace),
                        "render-schematic",
                        source.name,
                        "--output",
                        "linked/escaped.kicad_sch",
                    ]
                )

            self.assertEqual(result, 2)
            self.assertFalse((outside / "escaped.kicad_sch").exists())
            self.assertEqual(_file_state(source), source_before)

    def test_render_schematic_leaves_no_output_for_invalid_intent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "invalid.json"
            output = workspace / "must-not-exist.kicad_sch"
            source.write_text('{"content":{"title":"SECRET_CIRCUIT"}}', encoding="utf-8")
            source_before = _file_state(source)
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                result = main(
                    [
                        "--workspace",
                        str(workspace),
                        "render-schematic",
                        source.name,
                        "--output",
                        output.name,
                    ]
                )

            self.assertEqual(result, 2)
            self.assertFalse(output.exists())
            self.assertEqual(_file_state(source), source_before)
            self.assertNotIn("SECRET_CIRCUIT", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
