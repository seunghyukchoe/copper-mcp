#!/usr/bin/env python3
"""Synchronize the documented label catalog through the authenticated GitHub CLI."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> None:
    gh = shutil.which("gh")
    if gh is None:
        raise SystemExit("GitHub CLI is required to synchronize labels")
    subprocess.run([gh, *args], cwd=ROOT, check=True)  # noqa: S603


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="OWNER/REPOSITORY")
    args = parser.parse_args()
    labels = json.loads((ROOT / ".github" / "labels.json").read_text(encoding="utf-8"))
    for label in labels:
        run(
            "label",
            "create",
            label["name"],
            "--repo",
            args.repo,
            "--color",
            label["color"],
            "--description",
            label["description"],
            "--force",
        )
    print(f"Synchronized {len(labels)} labels to {args.repo}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
