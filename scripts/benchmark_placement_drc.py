#!/usr/bin/env python3
"""Measure the supported private placement-candidate KiCad DRC boundary.

This benchmark uses the repository's compact fixture and the installed KiCad CLI. It measures
candidate-bound evidence, source preservation, and private execution only; it makes no claim about
unsupported footprint geometry, electrical behavior, fabrication, or router quality.
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
from dataclasses import replace
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.benchmarks.drc_comparability import (
    LITERAL_KEY,
    comparability_of,
    require_qualified,
)
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.placement import build_placement_view, evaluate_placement, parse_placement_intent
from copper_mcp.placement_drc import run_placement_candidate_drc

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "tests/fixtures/board-ir-v0.2/footprint-pose-courtyard.kicad_pcb"
_DEFAULT_OUTPUT = _ROOT / "benchmarks/results/routing/2026-08-05-placement-drc.json"
_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - git path comes from shutil.which
            [git, "rev-parse", "HEAD"],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:benchmark",
        name="Benchmark",
        clearance_nm=200_000,
        track_width_nm=250_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _candidate(source: bytes, profile: KiCadConstraintProfile) -> object:
    conversion = parse_kicad_bytes(source, profile)
    if conversion.snapshot is None or conversion.diagnostics:
        raise RuntimeError("benchmark fixture is outside the supported Board IR")
    view = build_placement_view(source, conversion.snapshot)
    refs = sorted(view.footprints)
    intent = parse_placement_intent(
        {
            "board": _FIXTURE.name,
            "constraints": {
                "clearance_nm": 200_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 600_000,
                "via_drill_nm": 300_000,
            },
            "subjects": refs,
            "proposals": [
                {
                    "subject": refs[1],
                    "offset_x_nm": 2_000_000,
                    "offset_y_nm": 1_000_000,
                    "orientation_udeg": 180_000_000,
                }
            ],
        }
    )
    result = evaluate_placement(intent, conversion.snapshot, view)
    if result.candidate is None:
        raise RuntimeError("benchmark fixture did not produce a placement candidate")
    return result.candidate


def _run(repetitions: int) -> dict[str, Any]:
    if repetitions < 1 or repetitions > 16:
        raise ValueError("repetitions must be between 1 and 16")
    if not _KICAD_CLI.is_file():
        raise RuntimeError("KiCad CLI is not installed at the reference path")
    source = _FIXTURE.read_bytes()
    profile = _profile()
    candidate = _candidate(source, profile)
    samples: list[int] = []
    passed = 0
    observations: list[dict[str, Any]] = []
    source_preserved = True
    candidate_bound = True
    context_bound = True
    for _ in range(repetitions):
        with tempfile.TemporaryDirectory(prefix="copper-mcp-placement-bench-") as root:
            workspace = Path(root)
            board = workspace / _FIXTURE.name
            board.write_bytes(source)
            before = board.stat()
            started = time.perf_counter_ns()
            evidence = run_placement_candidate_drc(
                board.name,
                candidate,
                profile,
                replace(Settings(workspace=workspace), kicad_cli=_KICAD_CLI),
            )
            samples.append(time.perf_counter_ns() - started)
            after = board.stat()
            passed += int(evidence.summary.passed)
            observations.append(
                {
                    "error_count": evidence.summary.error_count,
                    "warning_count": evidence.summary.warning_count,
                    "exclusion_count": evidence.summary.exclusion_count,
                    "ignored_check_count": evidence.summary.ignored_check_count,
                    "unconnected_count": evidence.summary.unconnected_count,
                    "passed": evidence.summary.passed,
                }
            )
            source_preserved = source_preserved and board.read_bytes() == source
            source_preserved = source_preserved and after.st_ino == before.st_ino
            source_preserved = source_preserved and after.st_mtime_ns == before.st_mtime_ns
            candidate_bound = candidate_bound and evidence.candidate_id == candidate.candidate_id
            context_bound = context_bound and (
                evidence.summary.drc_context_revision == evidence.patched_drc_context_revision
            )
    return {
        "repetitions": repetitions,
        LITERAL_KEY: comparability_of(observations),
        "clean_drc_runs": passed,
        "all_clean": passed == repetitions,
        "candidate_binding": candidate_bound,
        "context_binding": context_bound,
        "source_bytes_inode_mtime_preserved": source_preserved,
        "median_private_drc_ns": statistics.median(samples),
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
        "schema": "copper-mcp/benchmark/placement-candidate-drc/v1",
        "date_utc": "2026-08-05",
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "fixture": "board-ir-v0.2/footprint-pose-courtyard.kicad_pcb",
        "metrics": _run(args.repetitions),
        "not_claimed": [
            "unsupported footprint geometry or back-side placement",
            "public or live placement DRC",
            "placement apply or KiCad undo",
            "electrical or fabrication readiness",
            "FreeRouting parity",
        ],
    }
    require_qualified(payload, where="copper-mcp/benchmark/placement-candidate-drc/v1")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    payload["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
