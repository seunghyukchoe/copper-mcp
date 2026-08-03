from __future__ import annotations

import asyncio
import unittest

from copper_mcp.mcp_server import mcp


class McpServerTests(unittest.TestCase):
    def test_declares_expected_read_only_tools(self) -> None:
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual(
            {tool.name for tool in tools},
            {
                "compare_candidates",
                "inspect_board",
                "preview_route",
                "run_board_drc",
                "server_info",
                "validate_candidate",
            },
        )


if __name__ == "__main__":
    unittest.main()
