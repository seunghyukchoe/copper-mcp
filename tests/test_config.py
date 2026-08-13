from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from copper_mcp.config import ConfigurationError, Settings


class SettingsTests(unittest.TestCase):
    def test_defaults_are_local_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"COPPER_MCP_WORKSPACE": directory}, clear=True):
                settings = Settings.from_env()
        self.assertEqual(settings.workspace, Path(directory).resolve())
        self.assertEqual(settings.transport, "stdio")
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertIsNone(settings.kicad_cli)
        self.assertEqual(settings.kicad_timeout_seconds, 120)
        self.assertEqual(settings.max_drc_report_bytes, 8 * 1024 * 1024)
        self.assertEqual(settings.max_drc_context_bytes, 128 * 1024 * 1024)
        self.assertEqual(settings.max_drc_context_files, 10_000)
        self.assertEqual(settings.max_drc_context_scan_seconds, 10)
        self.assertEqual(settings.max_route_preview_seconds, 30)

    def test_the_fill_vertex_budget_is_calibrated_and_not_a_round_number(self) -> None:
        """ADR-0104: 16 MiB x the densest observed pour, 29,503 vertices/MiB, rounded up.

        The dataclass default and the environment default must be the same number. They are not
        interchangeable in practice -- ``Settings`` constructed directly, as every committed
        corpus runner does so that an ambient allow-flag cannot reach it, takes the dataclass
        default and never consults the environment -- so a divergence between them would mean
        this project's own measurements were taken at a budget no deployment uses.
        """

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"COPPER_MCP_WORKSPACE": directory}, clear=True):
                from_environment = Settings.from_env()
            constructed = Settings(workspace=Path(directory))

        self.assertEqual(constructed.max_fill_vertices, 500_000)
        self.assertEqual(from_environment.max_fill_vertices, constructed.max_fill_vertices)

    def test_the_fill_vertex_budget_keeps_its_range(self) -> None:
        """A pour needs three vertices to be a polygon; the ceiling is a DoS control (ADR-0104)."""

        for value, accepted in (("2", False), ("3", True), ("1000000", True), ("1000001", False)):
            with tempfile.TemporaryDirectory() as directory:
                environment = {
                    "COPPER_MCP_WORKSPACE": directory,
                    "COPPER_MCP_MAX_FILL_VERTICES": value,
                }
                with patch.dict(os.environ, environment, clear=True):
                    if accepted:
                        self.assertEqual(Settings.from_env().max_fill_vertices, int(value))
                    else:
                        with self.assertRaises(ConfigurationError):
                            Settings.from_env()

    def test_rejects_unknown_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"COPPER_MCP_WORKSPACE": directory, "COPPER_MCP_TRANSPORT": "sse"},
                clear=True,
            ):
                with self.assertRaises(ConfigurationError):
                    Settings.from_env()

    def test_reads_bounded_kicad_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "COPPER_MCP_WORKSPACE": directory,
                    "COPPER_MCP_KICAD_CLI": "/opt/kicad-cli",
                    "COPPER_MCP_KICAD_TIMEOUT_SECONDS": "30",
                    "COPPER_MCP_MAX_DRC_REPORT_BYTES": "4096",
                    "COPPER_MCP_MAX_DRC_CONTEXT_BYTES": "8192",
                    "COPPER_MCP_MAX_DRC_CONTEXT_FILES": "64",
                    "COPPER_MCP_MAX_DRC_CONTEXT_SCAN_SECONDS": "4",
                    "COPPER_MCP_MAX_ROUTE_PREVIEW_SECONDS": "5",
                },
                clear=True,
            ):
                settings = Settings.from_env()
        self.assertEqual(settings.kicad_cli, Path("/opt/kicad-cli"))
        self.assertEqual(settings.kicad_timeout_seconds, 30)
        self.assertEqual(settings.max_drc_report_bytes, 4096)
        self.assertEqual(settings.max_drc_context_bytes, 8192)
        self.assertEqual(settings.max_drc_context_files, 64)
        self.assertEqual(settings.max_drc_context_scan_seconds, 4)
        self.assertEqual(settings.max_route_preview_seconds, 5)


class LiveIpcOptInTests(unittest.TestCase):
    """COPPER_MCP_ALLOW_LIVE_IPC follows the apply flag's exact-membership rule."""

    def _from_env(self, value: str | None) -> Settings:
        with tempfile.TemporaryDirectory() as directory:
            environment = {"COPPER_MCP_WORKSPACE": directory}
            if value is not None:
                environment["COPPER_MCP_ALLOW_LIVE_IPC"] = value
            with patch.dict(os.environ, environment, clear=True):
                return Settings.from_env()

    def test_absent_means_off(self) -> None:
        self.assertFalse(self._from_env(None).allow_live_ipc)

    def test_only_the_exact_string_one_enables_it(self) -> None:
        self.assertTrue(self._from_env("1").allow_live_ipc)
        self.assertFalse(self._from_env("0").allow_live_ipc)

    def test_every_ambiguous_spelling_is_a_configuration_error(self) -> None:
        # bool() would read "false", "no" and " 1" as enabling. A flag that opens a socket to
        # the operator's running editor must not be switched on by a near miss.
        for value in (
            "true",
            "True",
            "TRUE",
            "yes",
            "on",
            "false",
            "no",
            "off",
            "",
            " 1",
            "1 ",
            "01",
            "2",
            "-1",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ConfigurationError):
                    self._from_env(value)


if __name__ == "__main__":
    unittest.main()
