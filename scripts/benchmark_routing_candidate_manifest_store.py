#!/usr/bin/env python3
"""Benchmark redacted candidate-manifest persistence without route geometry."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.routing.candidate_store import (
    CandidateManifest,
    CandidateManifestIntegrityError,
    CandidateManifestNotFoundError,
    CandidateManifestStore,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).relative_to(ROOT)


def _digest(fill: str) -> str:
    return f"sha256:{fill * 64}"


def _manifest() -> CandidateManifest:
    return CandidateManifest.create(
        candidate_id=_digest("a"),
        base_revision=_digest("b"),
        start_pad_id="pad:start",
        end_pad_id="pad:end",
        kind="single-layer",
        router="astar-v1",
        policy="deterministic",
        path_count=1,
        via_count=0,
        cost=123,
        metrics={"wire_length_nm": 1000, "bend_count": 2},
    )


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _run() -> dict[str, bool]:
    manifest = _manifest()
    with tempfile.TemporaryDirectory(prefix="copper-mcp-manifest-benchmark-") as directory:
        path = Path(directory) / "manifests.sqlite3"
        with CandidateManifestStore(path, ttl_ms=10) as store:
            first = store.put(manifest, now_ms=100)
            second = store.put(manifest, now_ms=101)
            idempotent = first == second
            with sqlite3.connect(path) as connection:
                raw = connection.execute(
                    "SELECT manifest_json FROM routing_candidate_manifests"
                ).fetchone()[0]
                payload = raw if isinstance(raw, bytes) else str(raw).encode()
            redacted = all(
                token not in payload
                for token in (b"vertices", b"board_bytes", b"raw_net", b"prompt", b"drc")
            )
            try:
                store.get(manifest.candidate_id, now_ms=110)
            except CandidateManifestNotFoundError as expired_error:
                expiry_message = str(expired_error)
            else:
                expiry_message = ""
            try:
                store.get(_digest("d"), now_ms=110)
            except CandidateManifestNotFoundError as unknown_error:
                unknown_message = str(unknown_error)
            else:
                unknown_message = "different"
            expiry = bool(expiry_message)
            uniform_unknown = expiry_message == unknown_message
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE routing_candidate_manifests SET manifest_json = ? WHERE candidate_id = ?",
                (sqlite3.Binary(b'{"tampered":true}'), manifest.candidate_id),
            )
            connection.commit()
            connection.close()
            tampered = False
            try:
                store.get(manifest.candidate_id, now_ms=101)
            except CandidateManifestIntegrityError:
                tampered = True
    return {
        "reopen_preserved": _reopen(manifest),
        "idempotent_put": idempotent,
        "expiry_refused": expiry,
        "unknown_expiry_error_uniform": uniform_unknown,
        "tamper_refused": tampered,
        "redacted_payload": redacted,
        "geometry_export": False,
        "mcp_tasks": False,
    }


def _reopen(manifest: CandidateManifest) -> bool:
    with tempfile.TemporaryDirectory(prefix="copper-mcp-manifest-reopen-") as directory:
        path = Path(directory) / "manifests.sqlite3"
        with CandidateManifestStore(path, ttl_ms=1000) as store:
            stored = store.put(manifest, now_ms=100)
        with CandidateManifestStore(path, ttl_ms=1000) as reopened:
            return reopened.get(manifest.candidate_id, now_ms=101) == stored


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metrics = _run()
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/routing-candidate-manifest-store/v1",
        "date_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture": "synthetic-redacted-manifest-v1",
        "script": str(SCRIPT_PATH),
        "metrics": metrics,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    payload["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
