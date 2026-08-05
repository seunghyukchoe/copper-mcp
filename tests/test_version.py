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

    def test_latest_ready_correction_supersedes_an_older_ready_row(self) -> None:
        old_source = "a" * 40
        corrected_source = "b" * 40
        ledger = f"""## Release authorization

| Version | Date | Validated source commit | Full gate evidence | Status |
|---|---|---|---|---|
| 0.5.0 | 2026-08-05 | {old_source} | old hosted gate | Ready |
| 0.5.0 | 2026-08-05 | {corrected_source} | corrected hosted gate | Ready |
"""

        self.assertEqual(_authorized_source_commit(ledger, "0.5.0"), corrected_source)

    def test_latest_blocked_row_revokes_an_older_ready_row(self) -> None:
        source_commit = "a" * 40
        ledger = f"""## Release authorization

| Version | Date | Validated source commit | Full gate evidence | Status |
|---|---|---|---|---|
| 0.5.0 | 2026-08-05 | {source_commit} | old hosted gate | Ready |
| 0.5.0 | 2026-08-05 | {source_commit} | later failure | Blocked |
"""

        self.assertIsNone(_authorized_source_commit(ledger, "0.5.0"))

    def test_malformed_latest_row_does_not_fall_back_to_an_older_ready_row(self) -> None:
        source_commit = "a" * 40
        ledger = f"""## Release authorization

| Version | Date | Validated source commit | Full gate evidence | Status |
|---|---|---|---|---|
| 0.5.0 | 2026-08-05 | {source_commit} | old hosted gate | Ready |
| 0.5.0 | 2026-08-05 | short | malformed correction | Ready |
"""

        self.assertIsNone(_authorized_source_commit(ledger, "0.5.0"))

    def test_tag_gate_refuses_a_tag_that_does_not_match_the_project_version(self) -> None:
        # A live-state assertion ("the current version is unreleased") flips the moment a
        # release is legitimately authorized, so the executable regression pins the
        # state-independent refusal path instead: a mismatched tag never passes.
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version = metadata["project"]["version"]
        mismatched = "v999.999.999"
        self.assertNotEqual(f"v{version}", mismatched)

        result = subprocess.run(  # noqa: S603
            [sys.executable, str(ROOT / "scripts" / "check_version.py"), "--tag", mismatched],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

        message = f"{result.stdout}\n{result.stderr}"
        self.assertNotEqual(result.returncode, 0, message)
        self.assertIn(mismatched, message)
        self.assertIn("does not match", message)


if __name__ == "__main__":
    unittest.main()
