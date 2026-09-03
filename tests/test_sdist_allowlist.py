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

import pytest

from scripts import check_sdist_tracked

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


def _miniature_repo(root: Path, git: str) -> None:
    """A tracked `src/`, an untracked scratch note, and an ignored cache file."""
    (root / "pyproject.toml").write_text(
        "[tool.hatch.build.targets.sdist]\ninclude = ['/src', '/docs']\n",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "docs").mkdir()
    (root / "src" / "kept.py").write_text("# kept\n", encoding="utf-8")
    (root / "src" / "SCRATCH-NOTE.md").write_text("# scratch\n", encoding="utf-8")
    (root / "src" / "__pycache__").mkdir()
    (root / "src" / "__pycache__" / "kept.pyc").write_bytes(b"\x00")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    subprocess.run([git, "init", "-q"], cwd=root, check=True)  # noqa: S603
    subprocess.run([git, "add", "src/kept.py", ".gitignore"], cwd=root, check=True)  # noqa: S603


def test_an_untracked_scratch_note_under_an_allowlisted_dir_is_named(
    tmp_path: Path,
) -> None:
    """The planted-probe case from #256: an untracked docs/src file must refuse."""

    git = shutil.which("git")
    if git is None:
        pytest.skip("git is required to enumerate untracked sdist inputs")
    _miniature_repo(tmp_path, git)
    directories = check_sdist_tracked._allowlist_directories(tmp_path)
    assert [path.name for path in directories] == ["src", "docs"]
    assert check_sdist_tracked._untracked_files(directories, tmp_path) == ["src/SCRATCH-NOTE.md"]


def test_staging_the_scratch_note_greens_the_gate(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is required to enumerate untracked sdist inputs")
    _miniature_repo(tmp_path, git)
    subprocess.run([git, "add", "src/SCRATCH-NOTE.md"], cwd=tmp_path, check=True)  # noqa: S603
    directories = check_sdist_tracked._allowlist_directories(tmp_path)
    assert check_sdist_tracked._untracked_files(directories, tmp_path) == []


def test_the_working_tree_currently_ships_no_untracked_files() -> None:
    assert check_sdist_tracked.main() == 0
