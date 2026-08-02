#!/usr/bin/env python3
"""Validate the single-source project version and optional release tag."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")


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

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if "## [Unreleased]" not in changelog:
        raise SystemExit("CHANGELOG.md must contain an [Unreleased] section")
    print(f"Version check passed: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
