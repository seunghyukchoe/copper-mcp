#!/usr/bin/env python3
"""Fetch the SimpleRouteJson boards this repository records but does not commit.

The tscircuit-benchmark corpus is MIT, so the first 20 of its 36 boards are committed under
``benchmarks/corpora/tscircuit-benchmark/samples``.  The remaining 16 are omitted only to keep the
repository small; this script clones the pinned upstream commit into a caller-named directory and
verifies every file against the digests already recorded in ``manifest.json``.

It is deliberately narrow.  It contacts exactly one host, over one fixed argv, with no shell, and
it writes only below the destination the caller named.  It refuses to write into the repository's
own committed corpus directory, so a fetched file can never masquerade as a reviewed one.  Nothing
in the test suite or the default benchmark run calls it: a corpus that is not present is an
environment-skipped benchmark, not a network fetch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "benchmarks/corpora/tscircuit-benchmark"
MANIFEST = CORPUS / "manifest.json"


class CorpusFetchError(RuntimeError):
    """Raised when the upstream clone, the pinned commit, or a digest does not check out."""


def _manifest() -> dict[str, object]:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise CorpusFetchError("corpus manifest is malformed")
    return document


def fetch(destination: Path, *, timeout: int = 300) -> dict[str, int]:
    """Clone the pinned upstream commit and verify every sample against the manifest."""

    document = _manifest()
    repository = str(document["upstream_repository"])
    commit = str(document["upstream_commit"])
    sample_path = str(document["upstream_path"])
    files = document["files"]
    if not isinstance(files, list):
        raise CorpusFetchError("corpus manifest carries no file list")

    destination = destination.resolve()
    if destination == CORPUS.resolve() or CORPUS.resolve() in destination.parents:
        raise CorpusFetchError(
            "refusing to fetch into the committed corpus directory; name a separate destination"
        )
    git = shutil.which("git")
    if git is None:
        raise CorpusFetchError("git is not available on PATH")

    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as workspace:
        checkout = Path(workspace) / "upstream"
        clone = subprocess.run(  # noqa: S603 - fixed local Git executable and argv
            [git, "clone", "--quiet", "--no-checkout", repository, str(checkout)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if clone.returncode != 0:
            raise CorpusFetchError("upstream clone failed")
        checked_out = subprocess.run(  # noqa: S603 - fixed local Git executable and argv
            [git, "checkout", "--quiet", "--detach", commit],
            cwd=checkout,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if checked_out.returncode != 0:
            raise CorpusFetchError("pinned upstream commit is not available")
        written = 0
        verified = 0
        for entry in files:
            if not isinstance(entry, dict):
                raise CorpusFetchError("corpus manifest file entry is malformed")
            name = str(entry["name"])
            if "/" in name or name in {".", ".."}:
                raise CorpusFetchError("corpus manifest names an unsupported path")
            payload = (checkout / sample_path / name).read_bytes()
            if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise CorpusFetchError(f"upstream file digest does not match the manifest: {name}")
            verified += 1
            (destination / name).write_bytes(payload)
            written += 1
    return {"verified": verified, "written": written}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="directory to write the verified samples into; must not be the committed corpus",
    )
    arguments = parser.parse_args()
    try:
        counts = fetch(arguments.destination)
    except (CorpusFetchError, OSError, KeyError, ValueError) as error:
        print(f"corpus fetch failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
