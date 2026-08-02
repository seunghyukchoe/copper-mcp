from __future__ import annotations

import unittest
from pathlib import Path

from copper_mcp.config import Settings
from copper_mcp.kicad_file import inspect_kicad_board


class KiCadInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).parent
        self.settings = Settings(workspace=self.root)

    def test_inspects_documented_board_shape(self) -> None:
        manifest = inspect_kicad_board("fixtures/minimal.kicad_pcb", self.settings)
        self.assertEqual(manifest.format, "kicad_pcb")
        self.assertEqual(manifest.source_version, "20240108")
        self.assertEqual(manifest.source_generator, "pcbnew")
        self.assertEqual(manifest.counts.copper_layers, 2)
        self.assertEqual(manifest.counts.footprints, 1)
        self.assertEqual(manifest.counts.nets, 2)
        self.assertEqual(manifest.counts.segments, 1)
        self.assertEqual(manifest.counts.vias, 1)
        self.assertEqual(manifest.counts.zones, 1)
        self.assertTrue(manifest.revision.startswith("sha256:"))

    def test_counts_kicad_10_named_nets(self) -> None:
        manifest = inspect_kicad_board("fixtures/kicad10-named-nets.kicad_pcb", self.settings)

        self.assertEqual(manifest.source_version, "20260206")
        self.assertEqual(manifest.counts.nets, 2)
        self.assertEqual(manifest.counts.segments, 1)
        self.assertEqual(manifest.counts.vias, 1)
        self.assertEqual(manifest.counts.zones, 1)


if __name__ == "__main__":
    unittest.main()
