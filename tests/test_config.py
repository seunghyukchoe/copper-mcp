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


if __name__ == "__main__":
    unittest.main()
