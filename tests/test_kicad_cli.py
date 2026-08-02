from __future__ import annotations

import json
import shutil
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError, discover_kicad_cli, run_board_drc
from copper_mcp.kicad_file import inspect_kicad_board

REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")


def finding(
    finding_type: str,
    severity: str,
    *,
    description: str = "finding",
    excluded: bool = False,
) -> dict[str, object]:
    return {
        "type": finding_type,
        "description": description,
        "severity": severity,
        "excluded": excluded,
        "items": [],
    }


def drc_report(
    *,
    schema: str = "https://schemas.kicad.org/drc.v1.json",
    violations: list[dict[str, object]] | None = None,
    unconnected_items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "$schema": schema,
        "source": "board;touch-not-allowed.kicad_pcb",
        "date": "2026-08-03T12:00:00+09:00",
        "coordinate_units": "mm",
        "kicad_version": "10.0.5",
        "violations": violations or [],
        "unconnected_items": unconnected_items or [],
        "schematic_parity": [],
        "included_severities": ["error", "warning", "exclusion"],
        "ignored_checks": [],
    }


class KiCadCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.workspace = Path(self.temporary_directory.name).resolve()
        self.board = self.workspace / "board;touch-not-allowed.kicad_pcb"
        self.board.write_text("(kicad_pcb (version 20240108))", encoding="utf-8")
        self.settings = Settings(workspace=self.workspace, max_drc_report_bytes=1024)

    def _completed_run(
        self,
        report: dict[str, object] | bytes,
        *,
        returncode: int = 0,
        mutate_board: bool = False,
    ) -> object:
        def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            self.assertFalse(kwargs["shell"])
            self.assertNotIn("preexec_fn", kwargs)
            self.assertNotIn("--refill-zones", command)
            self.assertNotIn("--save-board", command)
            self.assertIn("-I", command)
            self.assertIn("/trusted/kicad-cli", command)
            self.assertEqual(Path(command[-1]).name, self.board.name)
            self.assertNotEqual(Path(command[-1]).parent, self.workspace)
            report_path = Path(command[command.index("--output") + 1])
            if isinstance(report, bytes):
                report_path.write_bytes(report)
            else:
                report_path.write_text(json.dumps(report), encoding="utf-8")
            if mutate_board:
                self.board.write_text("(kicad_pcb changed)", encoding="utf-8")
            return subprocess.CompletedProcess(command, returncode)

        return run

    def test_summarizes_violations_without_raw_details(self) -> None:
        report = drc_report(
            violations=[
                finding("clearance", "error", description="private"),
                finding("clearance", "warning"),
                finding("silk_overlap", "error", excluded=True),
            ],
            unconnected_items=[finding("unconnected_items", "error", description="NET_SECRET")],
        )
        with patch(
            "copper_mcp.kicad_cli.discover_kicad_cli", return_value=Path("/trusted/kicad-cli")
        ):
            with patch(
                "copper_mcp.kicad_cli.subprocess.run",
                side_effect=self._completed_run(report, returncode=5),
            ) as mocked_run:
                summary = run_board_drc(self.board.name, self.settings)

        self.assertEqual(summary.error_count, 1)
        self.assertEqual(summary.warning_count, 1)
        self.assertEqual(summary.exclusion_count, 1)
        self.assertEqual(summary.ignored_check_count, 0)
        self.assertEqual(summary.unconnected_count, 1)
        self.assertEqual(
            summary.violation_type_counts,
            {"clearance": 2, "silk_overlap": 1, "unconnected_items": 1},
        )
        self.assertFalse(summary.passed)
        self.assertNotIn("private", json.dumps(summary.to_dict()))
        self.assertNotIn("NET_SECRET", json.dumps(summary.to_dict()))
        command = mocked_run.call_args.args[0]
        self.assertEqual(Path(command[-1]).name, self.board.name)

    def test_accepts_clean_zero_return_code(self) -> None:
        with patch(
            "copper_mcp.kicad_cli.discover_kicad_cli", return_value=Path("/trusted/kicad-cli")
        ):
            with patch(
                "copper_mcp.kicad_cli.subprocess.run",
                side_effect=self._completed_run(drc_report()),
            ):
                summary = run_board_drc(self.board.name, self.settings)
        self.assertTrue(summary.passed)

    def test_rejects_unexpected_return_code(self) -> None:
        with patch(
            "copper_mcp.kicad_cli.discover_kicad_cli", return_value=Path("/trusted/kicad-cli")
        ):
            with patch(
                "copper_mcp.kicad_cli.subprocess.run",
                return_value=subprocess.CompletedProcess([], 3),
            ):
                with self.assertRaisesRegex(KiCadCliError, "exit code 3"):
                    run_board_drc(self.board.name, self.settings)

    def test_rejects_malformed_and_unsupported_reports(self) -> None:
        reports = (b"not-json", drc_report(schema="https://example.invalid/drc.json"))
        for report in reports:
            with self.subTest(report=report):
                with patch(
                    "copper_mcp.kicad_cli.discover_kicad_cli",
                    return_value=Path("/trusted/kicad-cli"),
                ):
                    with patch(
                        "copper_mcp.kicad_cli.subprocess.run",
                        side_effect=self._completed_run(report),
                    ):
                        with self.assertRaises(KiCadCliError):
                            run_board_drc(self.board.name, self.settings)

    def test_rejects_incomplete_or_inconsistent_report_contract(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        missing_date = drc_report()
        missing_date.pop("date")
        cases.append(("missing date", missing_date))
        wrong_source = drc_report()
        wrong_source["source"] = "different.kicad_pcb"
        cases.append(("wrong source", wrong_source))
        partial_severities = drc_report()
        partial_severities["included_severities"] = ["error", "warning"]
        cases.append(("partial severities", partial_severities))
        missing_ignored_checks = drc_report()
        missing_ignored_checks.pop("ignored_checks")
        cases.append(("missing ignored checks", missing_ignored_checks))
        parity_findings = drc_report()
        parity_findings["schematic_parity"] = [finding("footprint_mismatch", "error")]
        cases.append(("schematic parity", parity_findings))
        date_only = drc_report()
        date_only["date"] = "2026-08-03"
        cases.append(("date only", date_only))
        malformed_version = drc_report()
        malformed_version["kicad_version"] = "nightly private build"
        cases.append(("malformed version", malformed_version))

        for name, report in cases:
            with self.subTest(name=name):
                with patch(
                    "copper_mcp.kicad_cli.discover_kicad_cli",
                    return_value=Path("/trusted/kicad-cli"),
                ):
                    with patch(
                        "copper_mcp.kicad_cli.subprocess.run",
                        side_effect=self._completed_run(report),
                    ):
                        with self.assertRaises(KiCadCliError):
                            run_board_drc(self.board.name, self.settings)

    def test_rejects_oversized_report(self) -> None:
        with patch(
            "copper_mcp.kicad_cli.discover_kicad_cli", return_value=Path("/trusted/kicad-cli")
        ):
            with patch(
                "copper_mcp.kicad_cli.subprocess.run",
                side_effect=self._completed_run(b"x" * 1025),
            ):
                with self.assertRaisesRegex(KiCadCliError, "configured limit"):
                    run_board_drc(self.board.name, self.settings)

    def test_rejects_process_file_limit_signal(self) -> None:
        with patch(
            "copper_mcp.kicad_cli.discover_kicad_cli", return_value=Path("/trusted/kicad-cli")
        ):
            with patch(
                "copper_mcp.kicad_cli.subprocess.run",
                return_value=subprocess.CompletedProcess([], -signal.SIGXFSZ),
            ):
                with self.assertRaisesRegex(KiCadCliError, "configured limit"):
                    run_board_drc(self.board.name, self.settings)

    def test_rejects_timeout(self) -> None:
        with patch(
            "copper_mcp.kicad_cli.discover_kicad_cli", return_value=Path("/trusted/kicad-cli")
        ):
            with patch(
                "copper_mcp.kicad_cli.subprocess.run",
                side_effect=subprocess.TimeoutExpired("kicad-cli", 1),
            ):
                with self.assertRaisesRegex(KiCadCliError, "timed out"):
                    run_board_drc(self.board.name, self.settings)

    def test_discards_result_when_board_changes(self) -> None:
        with patch(
            "copper_mcp.kicad_cli.discover_kicad_cli", return_value=Path("/trusted/kicad-cli")
        ):
            with patch(
                "copper_mcp.kicad_cli.subprocess.run",
                side_effect=self._completed_run(drc_report(), mutate_board=True),
            ):
                with self.assertRaisesRegex(KiCadCliError, "board or DRC rules changed"):
                    run_board_drc(self.board.name, self.settings)

    def test_snapshots_and_tracks_project_rule_context(self) -> None:
        rules = self.board.with_suffix(".kicad_dru")
        rules.write_text("(version 1)", encoding="utf-8")
        library_table = self.workspace / "fp-lib-table"
        library_table.write_text("(fp_lib_table)", encoding="utf-8")
        footprint = self.workspace / "local.pretty" / "R.kicad_mod"
        footprint.parent.mkdir()
        footprint.write_text("(footprint R)", encoding="utf-8")

        def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            self.assertFalse(kwargs["shell"])
            snapshot_board = Path(command[-1])
            self.assertEqual(
                snapshot_board.with_suffix(".kicad_dru").read_text(encoding="utf-8"),
                "(version 1)",
            )
            self.assertEqual(
                (snapshot_board.parent / "fp-lib-table").read_text(encoding="utf-8"),
                "(fp_lib_table)",
            )
            self.assertEqual(
                (snapshot_board.parent / "local.pretty" / "R.kicad_mod").read_text(
                    encoding="utf-8"
                ),
                "(footprint R)",
            )
            report_path = Path(command[command.index("--output") + 1])
            report_path.write_text(json.dumps(drc_report()), encoding="utf-8")
            rules.write_text("(version 2)", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)

        with patch(
            "copper_mcp.kicad_cli.discover_kicad_cli", return_value=Path("/trusted/kicad-cli")
        ):
            with patch("copper_mcp.kicad_cli.subprocess.run", side_effect=run):
                with self.assertRaisesRegex(KiCadCliError, "board or DRC rules changed"):
                    run_board_drc(self.board.name, self.settings)

    def test_rejects_cumulative_context_before_starting_kicad(self) -> None:
        footprint = self.workspace / "local.pretty" / "R.kicad_mod"
        footprint.parent.mkdir()
        footprint.write_bytes(b"x" * 64)
        settings = Settings(
            workspace=self.workspace,
            max_drc_context_bytes=self.board.stat().st_size + 32,
        )

        with patch("copper_mcp.kicad_cli.subprocess.run") as mocked_run:
            with self.assertRaisesRegex(KiCadCliError, "cumulative limit"):
                run_board_drc(self.board.name, settings)
        mocked_run.assert_not_called()

    def test_rejects_excessive_context_file_count_before_starting_kicad(self) -> None:
        library = self.workspace / "local.pretty"
        library.mkdir()
        (library / "R1.kicad_mod").write_text("(footprint R1)", encoding="utf-8")
        (library / "R2.kicad_mod").write_text("(footprint R2)", encoding="utf-8")
        settings = Settings(workspace=self.workspace, max_drc_context_files=2)

        with patch("copper_mcp.kicad_cli.subprocess.run") as mocked_run:
            with self.assertRaisesRegex(KiCadCliError, "file-count limit"):
                run_board_drc(self.board.name, settings)
        mocked_run.assert_not_called()

    def test_rejects_context_discovery_timeout_before_starting_kicad(self) -> None:
        settings = Settings(workspace=self.workspace, max_drc_context_scan_seconds=1)

        with patch("copper_mcp.kicad_cli.time.monotonic", side_effect=[0.0, 0.0, 2.0]):
            with patch("copper_mcp.kicad_cli.subprocess.run") as mocked_run:
                with self.assertRaisesRegex(KiCadCliError, "discovery timed out"):
                    run_board_drc(self.board.name, settings)
        mocked_run.assert_not_called()

    def test_rejects_missing_configured_executable(self) -> None:
        settings = Settings(workspace=self.workspace, kicad_cli=self.workspace / "missing")
        with self.assertRaisesRegex(KiCadCliError, "missing or not executable"):
            discover_kicad_cli(settings)

    @unittest.skipUnless(REAL_KICAD_CLI.is_file(), "KiCad CLI is not installed")
    def test_real_kicad_drc_is_read_only(self) -> None:
        source = Path(__file__).parent / "fixtures" / "minimal.kicad_pcb"
        board = self.workspace / "minimal.kicad_pcb"
        shutil.copy2(source, board)
        before_bytes = board.read_bytes()
        before_mtime = board.stat().st_mtime_ns
        settings = Settings(workspace=self.workspace, kicad_cli=REAL_KICAD_CLI)

        summary = run_board_drc(board.name, settings)
        manifest = inspect_kicad_board(board.name, settings)

        self.assertRegex(summary.kicad_version, r"^\d+\.\d+(?:\.\d+)?")
        self.assertEqual(summary.base_revision, manifest.revision)
        self.assertEqual(summary.drc_schema, "https://schemas.kicad.org/drc.v1.json")
        self.assertEqual(summary.coordinate_units, "mm")
        self.assertGreaterEqual(summary.error_count, 1)
        self.assertEqual(summary.unconnected_count, 0)
        self.assertFalse(summary.passed)
        self.assertEqual(board.read_bytes(), before_bytes)
        self.assertEqual(board.stat().st_mtime_ns, before_mtime)
        self.assertEqual(
            {path.name for path in self.workspace.iterdir()},
            {self.board.name, board.name},
        )


if __name__ == "__main__":
    unittest.main()
