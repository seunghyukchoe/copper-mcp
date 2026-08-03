from __future__ import annotations

import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from scripts.check_version import _authorized_source_commit

ROOT = Path(__file__).resolve().parents[1]


class VersionGateTests(unittest.TestCase):
    def test_only_ready_row_in_authorization_section_is_accepted(self) -> None:
        source_commit = "a" * 40
        ledger = f"""# Release Ledger

| Version | Date | Tag / commit |
|---|---|---|
| 0.2.0 | 2026-08-03 | {source_commit} |

## Release authorization

| Version | Date | Validated source commit | Full gate evidence | Status |
|---|---|---|---|---|
| 0.2.0 | 2026-08-03 | {source_commit} | make check | Ready |

## Unreleased readiness

| 0.3.0 | 2026-08-03 | {source_commit} | make check | Ready |
"""

        self.assertEqual(_authorized_source_commit(ledger, "0.2.0"), source_commit)
        self.assertIsNone(_authorized_source_commit(ledger, "0.3.0"))

    def test_short_commit_or_non_ready_status_does_not_authorize_tag(self) -> None:
        ledger = """## Release authorization

| Version | Date | Validated source commit | Full gate evidence | Status |
|---|---|---|---|---|
| 0.2.0 | 2026-08-03 | abc1234 | make check | Ready |
| 0.2.0 | 2026-08-03 | aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | pending | Blocked |
"""

        self.assertIsNone(_authorized_source_commit(ledger, "0.2.0"))

    def test_tag_gate_refuses_unreleased_v0_2_0(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["version"], "0.2.0")

        result = subprocess.run(  # noqa: S603
            [sys.executable, str(ROOT / "scripts" / "check_version.py"), "--tag", "v0.2.0"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

        message = f"{result.stdout}\n{result.stderr}"
        self.assertNotEqual(result.returncode, 0, message)
        self.assertIn("0.2.0", message)
        self.assertIn("tag", message.lower())


if __name__ == "__main__":
    unittest.main()
