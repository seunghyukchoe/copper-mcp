#!/usr/bin/env python3
"""Validate the single-source project version and optional release tag."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
RELEASE_METADATA_FILES = frozenset(
    {
        "CHANGELOG.md",
        "docs/ledgers/release-ledger.md",
    }
)


def _authorized_source_commit(release_ledger: str, version: str) -> str | None:
    """Return the validated source commit from an exact Ready authorization row."""
    heading = re.search(r"^## Release authorization\s*$", release_ledger, re.MULTILINE)
    if heading is None:
        return None
    tail = release_ledger[heading.end() :]
    next_heading = re.search(r"^## ", tail, re.MULTILINE)
    section = tail if next_heading is None else tail[: next_heading.start()]
    ready_row = re.compile(
        rf"^\|\s*{re.escape(version)}\s*\|"
        r"\s*\d{4}-\d{2}-\d{2}\s*\|"
        r"\s*\x60?([0-9a-f]{40})\x60?\s*\|"
        r"\s*[^|\r\n]+\|\s*Ready\s*\|$",
        re.MULTILINE,
    )
    match = ready_row.search(section)
    return None if match is None else match.group(1)


def _validate_release_metadata_delta(source_commit: str) -> None:
    """Ensure the tag commit only adds metadata after the validated source commit."""
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is required to validate release authorization")
    try:
        head_commit = subprocess.run(  # noqa: S603
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        if source_commit == head_commit:
            raise SystemExit(
                "release authorization must be committed after its validated source commit"
            )
        subprocess.run(  # noqa: S603
            [git, "merge-base", "--is-ancestor", source_commit, head_commit],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        changed_output = subprocess.run(  # noqa: S603
            [git, "diff", "--name-only", f"{source_commit}..{head_commit}"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout
        dirty_output = subprocess.run(  # noqa: S603
            [git, "status", "--porcelain=v1", "--untracked-files=normal"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(
            "release authorization source commit must be a reachable Git ancestor"
        ) from exc

    changed_files = {line for line in changed_output.splitlines() if line}
    unexpected = changed_files - RELEASE_METADATA_FILES
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise SystemExit(
            "only release metadata may change after the validated source commit; "
            f"unexpected: {names}"
        )
    if dirty_output:
        raise SystemExit("release tag validation requires a clean Git worktree")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Release tag, for example v1.2.3")
    args = parser.parse_args()

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    if not SEMVER.fullmatch(version):
        raise SystemExit(f"project version is not valid SemVer: {version}")
    if args.tag is not None and args.tag != f"v{version}":
        raise SystemExit(f"tag {args.tag!r} does not match project version v{version}")

    package_source = (ROOT / "src" / "copper_mcp" / "__init__.py").read_text(encoding="utf-8")
    source_match = re.search(r'^_SOURCE_VERSION = "([^"]+)"$', package_source, re.MULTILINE)
    if source_match is None or source_match.group(1) != version:
        raise SystemExit("package source version does not match pyproject.toml")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if "## [Unreleased]" not in changelog:
        raise SystemExit("CHANGELOG.md must contain an [Unreleased] section")
    if args.tag is not None:
        release_heading = re.compile(
            rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
            re.MULTILINE,
        )
        if release_heading.search(changelog) is None:
            raise SystemExit(
                f"CHANGELOG.md must contain a dated [{version}] section before tagging"
            )
        release_ledger = (ROOT / "docs" / "ledgers" / "release-ledger.md").read_text(
            encoding="utf-8"
        )
        source_commit = _authorized_source_commit(release_ledger, version)
        if source_commit is None:
            raise SystemExit(
                f"release ledger must contain a Ready authorization row for {version} "
                "before tagging"
            )
        _validate_release_metadata_delta(source_commit)
    print(f"Version check passed: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
