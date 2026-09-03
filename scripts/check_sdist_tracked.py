#!/usr/bin/env python3
"""Refuse a release when the sdist ships files Git never recorded.

A scratch note, a private draft, or a personal document left on the author's
disk under a shipped directory sails into the release tarball and no gate
notices (#256) -- the same disk-vs-git population mismatch as #244, pointing the
other way (over-inclusion rather than under-counting).

**This gate observes the artifact rather than modelling the build.** The first
version of it asked Git which files were untracked under the sdist allowlist and
called that "exactly the set the build would over-include". It was not: ``git
ls-files --others --exclude-standard`` honours ``.gitignore``, ``.git/info/
exclude`` *and* ``core.excludesFile``, while hatchling honours ``.gitignore``
alone -- the very asymmetry recorded above the allowlist in ``pyproject.toml``,
and the mechanism of the 0.7.0 incident that swept twenty-eight worktrees into
the tarball. Review demonstrated the gap: a file hidden only by ``$GIT_DIR/info/
exclude`` was packed into the sdist while the gate printed "passed". A model of
the build passes by omission whenever the model and the build disagree, so the
model is gone. The sdist is built and its member list is read.

Direction of error: shipping bytes the repository never recorded is the
forbidden direction for a release artifact, so every member must be tracked. The
single exception is ``PKG-INFO``, which the build backend generates and no
repository tracks. A second generated member would make this gate refuse until
someone adds it to ``GENERATED_MEMBERS`` -- refusing too much, which is the loud
direction, rather than passing by omission.

The cost is honest: this runs a real build (a few seconds on this repository)
and needs the ``build`` package, which is already a development dependency and
already what CI's "Build packages" step invokes.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Container, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Members every PEP 517 sdist carries that no repository tracks, because the
# build backend writes them. Keep this as small as the evidence allows: each
# entry is a file the gate stops being able to report.
GENERATED_MEMBERS = frozenset({"PKG-INFO"})


def build_sdist(root: Path, outdir: Path) -> Path:
    """Build ``root``'s source distribution into ``outdir`` and return the tarball.

    Isolated (the default) rather than ``--no-isolation``: hatchling is not
    importable from this repository's development environment, and an isolated
    build is also what CI's "Build packages" step runs.
    """

    subprocess.run(  # noqa: S603 - fixed argv, paths from this repository
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--outdir",
            str(outdir),
            str(root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    tarballs = sorted(outdir.glob("*.tar.gz"))
    if len(tarballs) != 1:
        raise SystemExit(f"expected exactly one sdist in {outdir}, found {len(tarballs)}")
    return tarballs[0]


def sdist_members(tarball: Path) -> list[str]:
    """File members of ``tarball``, relative to its single top-level directory.

    A PEP 517 sdist wraps everything in one ``<name>-<version>/`` directory. That
    prefix is stripped here so member names are comparable with ``git ls-files``
    output; refusing an archive with more than one top-level directory keeps a
    surprising layout from silently making every member look untracked.
    """

    with tarfile.open(tarball) as archive:
        names = [member.name for member in archive.getmembers() if member.isfile()]
    roots = {name.split("/", 1)[0] for name in names}
    if len(roots) != 1:
        raise SystemExit(
            f"sdist has {len(roots)} top-level directories, expected 1: {sorted(roots)}"
        )
    prefix = f"{roots.pop()}/"
    return sorted(name[len(prefix) :] for name in names)


def untracked_members(
    members: Iterable[str],
    tracked: Container[str],
    generated: frozenset[str] = GENERATED_MEMBERS,
) -> list[str]:
    """Members the repository never recorded, excluding backend-generated files."""

    return sorted(name for name in members if name not in generated and name not in tracked)


def _tracked_files(root: Path = ROOT) -> set[str]:
    """Every path Git records for ``root``, as repository-relative names."""

    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is required to enumerate the tracked set")
    result = subprocess.run(  # noqa: S603 - fixed git argv, no external input
        [git, "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return {name for name in result.stdout.split("\0") if name}


def main(root: Path = ROOT) -> int:
    with tempfile.TemporaryDirectory() as staging:
        tarball = build_sdist(root, Path(staging))
        name = tarball.name
        members = sdist_members(tarball)
    extras = untracked_members(members, _tracked_files(root))
    if extras:
        raise SystemExit(
            f"{name} ships files Git never recorded:\n- "
            + "\n- ".join(extras)
            + "\nstage, remove, or ignore them and re-run"
        )
    print(f"sdist track check passed ({len(members)} members in {name}; every one tracked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
