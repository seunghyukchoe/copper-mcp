#!/usr/bin/env python3
"""Refuse a release when the sdist would ship files Git never recorded.

The sdist is built from the working tree under the allowlist in
``pyproject.toml`` (`tool.hatch.build.targets.sdist.include`), while the
repository's notion of "what ships" is what Git tracks. A scratch note, a
private draft, or a personal document left on the author's disk under one of
those directories would therefore sail into the release tarball and no gate
would notice (#256) -- the same disk-vs-git population mismatch as #244,
pointing the other way (over-inclusion rather than under-counting).

Direction of error: shipping bytes the repository never recorded is the
forbidden direction for a release artifact. Refusing while untracked,
non-ignored files exist under an allowlisted directory is safe and loud; the
author stages, removes, or ignores them and re-runs.

Ignored files (``__pycache__``, build outputs) are hatchling's own business:
the build backend honours ``.gitignore``, so only untracked *non-ignored*
files can reach the tarball. The check therefore mirrors exactly the set the
build would over-include.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _allowlist_directories(root: Path = ROOT) -> list[Path]:
    """Directories from the sdist allowlist that exist in the working tree."""
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "hatch"
    ]["build"]["targets"]["sdist"]
    include = manifest.get("include", [])
    directories = []
    for entry in include:
        candidate = root / entry.lstrip("/")
        if candidate.is_dir():
            directories.append(candidate)
    return directories


def _untracked_files(paths: list[Path], root: Path = ROOT) -> list[str]:
    """Untracked, non-ignored files below ``paths``, as repository-relative names."""
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is required to enumerate untracked sdist inputs")
    result = subprocess.run(  # noqa: S603
        [git, "ls-files", "--others", "--exclude-standard", "-z", "--"]
        + [path.as_posix() for path in paths],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(name for name in result.stdout.split("\0") if name)


def main() -> int:
    directories = _allowlist_directories()
    if not directories:
        raise SystemExit("sdist allowlist names no directories; refusing a blind pass")
    extras = _untracked_files(directories)
    if extras:
        raise SystemExit(
            "sdist would ship untracked files Git never recorded:\n- "
            + "\n- ".join(extras)
            + "\nstage, remove, or ignore them and re-run"
        )
    print(
        f"sdist track check passed ({len(directories)} allowlisted directories; "
        "no untracked inputs)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
