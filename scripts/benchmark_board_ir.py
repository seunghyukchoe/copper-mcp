#!/usr/bin/env python3
"""Measure the bounded KiCad-to-Board-IR conversion without claiming routing speed."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
import tracemalloc
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import BoardIRSnapshot, NetClass, ParseLimits, encode_snapshot

DEFAULT_INPUT = Path("hardware/coppertone-buffer/coppertone-buffer.kicad_pcb")


def _positive_count(value: str) -> int:
    count = int(value)
    if not 1 <= count <= 100:
        raise argparse.ArgumentTypeError("count must be between 1 and 100")
    return count


def _git_metadata() -> tuple[str, bool | None]:
    git = shutil.which("git")
    if git is None:
        return "unknown", None
    try:
        commit = subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(  # noqa: S603 - fixed local Git argv
                [git, "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", None
    return commit, dirty


def _profile() -> KiCadConstraintProfile:
    return KiCadConstraintProfile(
        net_classes=(
            NetClass(
                id="class:default",
                name="Default",
                clearance_nm=200_000,
                track_width_nm=250_000,
                via_diameter_nm=600_000,
                via_drill_nm=300_000,
            ),
        ),
        default_net_class_id="class:default",
    )


def _physical_memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, TypeError, ValueError):
        return None


def _parse_once(source: bytes, profile: KiCadConstraintProfile) -> tuple[int, int, BoardIRSnapshot]:
    tracemalloc.start()
    started_ns = time.perf_counter_ns()
    result = parse_kicad_bytes(source, profile)
    elapsed_ns = time.perf_counter_ns() - started_ns
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if result.snapshot is None:
        codes = ", ".join(item.code for item in result.diagnostics)
        raise RuntimeError(f"Board IR conversion failed: {codes or 'unknown error'}")
    return elapsed_ns, peak_bytes, result.snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--iterations", type=_positive_count, default=7)
    parser.add_argument("--warmups", type=_positive_count, default=2)
    args = parser.parse_args()

    try:
        source = args.input.read_bytes()
    except OSError as error:
        parser.error(str(error))

    profile = _profile()
    for _ in range(args.warmups):
        _parse_once(source, profile)

    timings_ns: list[int] = []
    peaks_bytes: list[int] = []
    snapshots = []
    for _ in range(args.iterations):
        elapsed_ns, peak_bytes, snapshot = _parse_once(source, profile)
        timings_ns.append(elapsed_ns)
        peaks_bytes.append(peak_bytes)
        snapshots.append(snapshot)

    snapshot = snapshots[0]
    if any(item != snapshot for item in snapshots[1:]):
        raise RuntimeError("repeated conversions produced different Board IR snapshots")

    commit, dirty = _git_metadata()
    content = snapshot.content
    report: dict[str, Any] = {
        "benchmark": "board-ir-kicad-conversion-v1",
        "commit": commit,
        "configuration": {
            "limits": asdict(ParseLimits()),
            "net_classes": [
                {
                    "clearance_nm": item.clearance_nm,
                    "id": item.id,
                    "track_width_nm": item.track_width_nm,
                    "via_diameter_nm": item.via_diameter_nm,
                    "via_drill_nm": item.via_drill_nm,
                }
                for item in profile.net_classes
            ],
        },
        "dirty": dirty,
        "environment": {
            "accelerator": "none (CPU-only)",
            "machine": platform.machine(),
            "physical_memory_bytes": _physical_memory_bytes(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor() or "unknown",
        },
        "input": {
            "bytes": len(source),
            "license": "CERN-OHL-S-2.0",
            "path": args.input.as_posix(),
            "sha256": hashlib.sha256(source).hexdigest(),
        },
        "iterations": args.iterations,
        "instrumentation": "perf_counter_ns with tracemalloc enabled",
        "objects": {
            "arcs": len(content.arcs),
            "footprints": len(content.footprints),
            "keepouts": len(content.keepouts),
            "nets": len(content.nets),
            "pads": len(content.pads),
            "segments": len(content.segments),
            "vias": len(content.vias),
            "zones": len(content.zones),
        },
        "output": {
            "canonical_json_bytes": len(encode_snapshot(snapshot)),
            "schema_version": snapshot.schema_version,
            "snapshot_digest": snapshot.snapshot_digest,
        },
        "peak_memory_bytes": {
            "max": max(peaks_bytes),
            "median": int(statistics.median(peaks_bytes)),
            "samples": peaks_bytes,
        },
        "recorded_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "timing_ns": {
            "max": max(timings_ns),
            "median": int(statistics.median(timings_ns)),
            "min": min(timings_ns),
            "samples": timings_ns,
        },
        "warmups": args.warmups,
    }
    report["run_id"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
