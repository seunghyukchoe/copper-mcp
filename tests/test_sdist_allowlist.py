"""The sdist ships an allowlist, and the allowlist stays honest in both directions.

A default hatchling sdist swept twenty-eight agent worktrees into the 0.7.0 artifact — 20,452
files and 124 MB against a 544 KB wheel — because hatchling honours ``.gitignore`` but not
``.git/info/exclude``, which is where this repository keeps ``.claude/worktrees/``.  An exclusion
list can only ever be as complete as the last person to edit it; an allowlist cannot regress when
something new lands in the tree.  These tests pin that it stays an allowlist, that every entry
names something real, and that nothing shipping today falls out of it silently.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SDIST = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"][
    "build"
]["targets"]["sdist"]

# Tracked top-level entries the sdist deliberately does not ship: local tooling and CI wiring that
# a consumer of the source distribution has no use for.
_DELIBERATELY_UNSHIPPED = frozenset(
    {
        ".dockerignore",
        ".editorconfig",
        ".env.example",
        ".gitattributes",
        ".gitignore",
        ".pre-commit-config.yaml",
        ".python-version",
    }
)


def test_the_sdist_is_configured_as_an_allowlist() -> None:
    assert "include" in _SDIST, "the sdist must name what it ships, not what it omits"
    assert "exclude" not in _SDIST, (
        "an exclusion list is only as complete as its last edit; keep the allowlist"
    )


def test_every_allowlist_entry_names_something_that_exists() -> None:
    missing = [entry for entry in _SDIST["include"] if not (ROOT / entry.lstrip("/")).exists()]
    assert not missing, f"allowlist names paths that do not exist: {missing}"


def test_no_tracked_top_level_entry_falls_out_of_the_sdist_unnoticed() -> None:
    tracked = {
        line.split("/", 1)[0]
        for line in subprocess.run(  # noqa: S603 - fixed git argv, no external input
            [shutil.which("git") or "/usr/bin/git", "ls-files"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        if line
    }
    allowed = {entry.lstrip("/") for entry in _SDIST["include"]}
    unaccounted = tracked - allowed - _DELIBERATELY_UNSHIPPED - {".github"}
    assert not unaccounted, (
        "new tracked top-level entries are neither shipped nor recorded as deliberately "
        f"unshipped: {sorted(unaccounted)}"
    )
