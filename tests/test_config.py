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

    def test_rejects_unknown_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"COPPER_MCP_WORKSPACE": directory, "COPPER_MCP_TRANSPORT": "sse"},
                clear=True,
            ):
                with self.assertRaises(ConfigurationError):
                    Settings.from_env()


if __name__ == "__main__":
    unittest.main()
