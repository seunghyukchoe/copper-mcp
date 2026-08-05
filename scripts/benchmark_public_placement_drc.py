#!/usr/bin/env python3
"""Measure public, file-backed placement preview with opt-in KiCad DRC evidence.

The benchmark uses a disposable workspace and the compact committed fixture.  It records only
aggregate evidence, content digests, and preservation checks; it does not claim live KiCad
operation, placement apply, electrical validation, fabrication readiness, or FreeRouting parity.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from copper_mcp.config import Settings
from copper_mcp.placement_preview import preview_placement

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "tests/fixtures/placement-v0.1/placement-legal.kicad_pcb"
_DEFAULT_OUTPUT = _ROOT / "benchmarks/results/placement/2026-08-05-public-placement-drc.json"
_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
_CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}
_SUBJECTS = [
    "footprint:kicad:90000000-0000-0000-0000-000000000001",
    "footprint:kicad:90000000-0000-0000-0000-000000000003",
]


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - git is resolved from the local PATH
            [git, "rev-parse", "HEAD"],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _request() -> dict[str, Any]:
    return {
        "board": _FIXTURE.name,
        "constraints": dict(_CONSTRAINTS),
        "subjects": list(_SUBJECTS),
        "include_drc": True,
    }


def _run(repetitions: int) -> dict[str, Any]:
    if repetitions < 1 or repetitions > 16:
        raise ValueError("repetitions must be between 1 and 16")
    if not _KICAD_CLI.is_file():
        raise RuntimeError("KiCad CLI is not installed at the reference path")

    source = _FIXTURE.read_bytes()
    samples: list[int] = []
    evidence_digests: set[str] = set()
    source_preserved = True
    candidate_bound = True
    context_bound = True
    passed_runs = 0
    clean_runs = 0
    for _ in range(repetitions):
        with tempfile.TemporaryDirectory(prefix="copper-mcp-public-placement-drc-") as root:
            workspace = Path(root)
            board = workspace / _FIXTURE.name
            board.write_bytes(source)
            before = board.stat()
            started = time.perf_counter_ns()
            document = preview_placement(
                _request(), Settings(workspace=workspace, kicad_cli=_KICAD_CLI)
            ).to_dict()
            samples.append(time.perf_counter_ns() - started)
            after = board.stat()
            candidate = document["candidate"]
            evidence = document["drc_evidence"]
            if candidate is None or evidence is None:
                raise RuntimeError("public preview did not return candidate-bound DRC evidence")
            summary = evidence["summary"]
            assert isinstance(summary, dict)
            candidate_bound = candidate_bound and (
                evidence["candidate_id"] == candidate["candidate_id"]
                and evidence["candidate_base_revision"] == candidate["base_revision"]
                and evidence["source_revision"] == document["board_revision"]
            )
            context_bound = context_bound and (
                summary["base_revision"] == evidence["patched_board_revision"]
                and summary["drc_context_revision"] == evidence["patched_drc_context_revision"]
            )
            source_preserved = source_preserved and board.read_bytes() == source
            source_preserved = source_preserved and after.st_ino == before.st_ino
            source_preserved = source_preserved and after.st_mtime_ns == before.st_mtime_ns
            passed_runs += int(summary["passed"] is True)
            clean_runs += int(summary["clean"] is True)
            evidence_bytes = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
            evidence_digests.add("sha256:" + hashlib.sha256(evidence_bytes).hexdigest())
    return {
        "repetitions": repetitions,
        "passed_drc_runs": passed_runs,
        "clean_drc_runs": clean_runs,
        "candidate_binding": candidate_bound,
        "context_binding": context_bound,
        "source_bytes_inode_mtime_preserved": source_preserved,
        "deterministic_evidence_digests": len(evidence_digests),
        "median_public_preview_drc_ns": statistics.median(samples),
        "kicad_version": "10.x",
        "workspace_mutations": 0,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/public-placement-preview-drc/v1",
        "date_utc": "2026-08-05",
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "fixture": "placement-v0.1/placement-legal.kicad_pcb",
        "metrics": _run(args.repetitions),
        "not_claimed": [
            "live placement DRC or IPC mutation",
            "placement apply or KiCad undo",
            "electrical or fabrication readiness",
            "FreeRouting parity",
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    payload["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
