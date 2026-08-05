#!/usr/bin/env python3
"""Measure deterministic, source-preserving placement projection on a KiCad fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.kicad_placement_patch import render_kicad_placement_candidate_board
from copper_mcp.board_ir import NetClass
from copper_mcp.placement import build_placement_view, evaluate_placement, parse_placement_intent

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path("scripts/benchmark_kicad_placement_projection.py")
FIXTURE = ROOT / "tests" / "fixtures" / "board-ir-v0.2" / "footprint-pose-courtyard.kicad_pcb"
BENCHMARK_NAME = "kicad-source-preserving-placement-v1"
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


class BenchmarkError(RuntimeError):
    """The placement projection oracle was not deterministic or bounded."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - executable is discovered from PATH
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _repetitions(value: str) -> int:
    count = int(value)
    if not 2 <= count <= 50:
        raise argparse.ArgumentTypeError("repetitions must be between 2 and 50")
    return count


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:request", name="Request", **CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _candidate(source: bytes, profile: KiCadConstraintProfile) -> tuple[Any, Any]:
    conversion = parse_kicad_bytes(source, profile)
    if conversion.snapshot is None or conversion.diagnostics:
        raise BenchmarkError("fixture did not produce a supported Board IR snapshot")
    snapshot = conversion.snapshot
    view = build_placement_view(source, snapshot)
    refs = sorted(view.footprints)
    intent = parse_placement_intent(
        {
            "board": str(FIXTURE.relative_to(ROOT)),
            "constraints": CONSTRAINTS,
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
    result = evaluate_placement(intent, snapshot, view)
    if result.candidate is None:
        raise BenchmarkError("placement legalizer did not produce a candidate")
    return snapshot, result.candidate


def _run(repetitions: int) -> dict[str, Any]:
    source = FIXTURE.read_bytes()
    profile = _profile()
    snapshot, candidate = _candidate(source, profile)
    outputs: list[bytes] = []
    latencies: list[int] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        rendered = render_kicad_placement_candidate_board(source, snapshot, candidate, profile)
        latencies.append(time.perf_counter_ns() - started)
        outputs.append(rendered)
        if source != FIXTURE.read_bytes():
            raise BenchmarkError("source fixture changed during projection")
        parsed = parse_kicad_bytes(rendered, profile)
        if parsed.snapshot is None or parsed.diagnostics:
            raise BenchmarkError("projected board did not round-trip through Board IR")

    if len(set(outputs)) != 1:
        raise BenchmarkError("placement projection replay was not deterministic")
    rendered = outputs[0]
    if rendered == source:
        raise BenchmarkError("fixture proposal produced no source change")
    changed_bytes = sum(left != right for left, right in zip(source, rendered, strict=False)) + abs(
        len(source) - len(rendered)
    )
    return {
        "benchmark": BENCHMARK_NAME,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_commit": _git_commit(),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "physical_memory_bytes": _physical_memory_bytes(),
            "dependencies": {
                "mcp": _package_version("mcp"),
                "pydantic": _package_version("pydantic"),
            },
            "kicad_invoked": False,
        },
        "configuration": {
            "script_sha256": hashlib.sha256((ROOT / SCRIPT_PATH).read_bytes()).hexdigest(),
            "repetitions": repetitions,
            "fixture_sha256": hashlib.sha256(source).hexdigest(),
            "profile": "request-200um-v1",
            "projection": "front-orthogonal-unfilled-courtyard-v1",
        },
        "metrics": {
            "deterministic_replays": repetitions,
            "median_projection_latency_ns": statistics.median(latencies),
            "source_unchanged": True,
            "roundtrip_replays": repetitions,
            "candidate_id": candidate.candidate_id,
            "candidate_base_revision": candidate.base_revision,
            "candidate_view_revision": candidate.view_revision,
            "output_sha256": "sha256:" + hashlib.sha256(rendered).hexdigest(),
            "source_bytes": len(source),
            "output_bytes": len(rendered),
            "changed_byte_count": changed_bytes,
            "kicad_invoked": False,
        },
        "not_claimed": [
            "placement-drc",
            "live-kicad-mutation",
            "single-undo-transaction",
            "back-side-or-non-orthogonal-footprints",
            "courtyard-overlap-legality",
            "electrical-validation",
            "fabrication-readiness",
        ],
    }


def _physical_memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=_repetitions, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = _run(args.repetitions)
    document = {
        "run_id": "sha256:" + hashlib.sha256(_canonical_bytes(document)).hexdigest(),
        **document,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical_bytes(document) + b"\n")
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
