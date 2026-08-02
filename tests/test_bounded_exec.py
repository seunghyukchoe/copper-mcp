from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from copper_mcp import _bounded_exec
from copper_mcp._bounded_exec import main


class BoundedExecTests(unittest.TestCase):
    def test_rejects_missing_or_invalid_arguments(self) -> None:
        self.assertEqual(main([]), 64)
        self.assertEqual(main(["not-an-integer", "/bin/false"]), 70)
        self.assertEqual(main(["0", "/bin/false"]), 64)

    def test_sets_limit_before_replacing_process(self) -> None:
        with patch("resource.setrlimit") as set_limit:
            with patch("copper_mcp._bounded_exec.os.execv", side_effect=OSError) as execv:
                self.assertEqual(main(["4096", "/trusted/kicad-cli", "pcb", "drc"]), 70)

        set_limit.assert_called_once()
        execv.assert_called_once_with("/trusted/kicad-cli", ["/trusted/kicad-cli", "pcb", "drc"])

    @unittest.skipUnless(os.name == "posix", "POSIX resource limits are required")
    def test_enforces_file_limit_during_child_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "oversized.bin"
            writer = (
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'x'*8192)"
            )
            child_environment = {
                key: value for key, value in os.environ.items() if not key.startswith("COV_CORE_")
            }
            completed = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    "-I",
                    str(Path(_bounded_exec.__file__).resolve()),
                    "1024",
                    sys.executable,
                    "-I",
                    "-c",
                    writer,
                    str(output),
                ],
                check=False,
                env=child_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertTrue(output.is_file())
            self.assertLessEqual(output.stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
