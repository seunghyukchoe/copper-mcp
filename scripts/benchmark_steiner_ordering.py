#!/usr/bin/env python3
"""Measure the low-degree one-Steiner ordering against the prior component-MST order.

The comparison reuses the public file-backed preview service on an independently authored
four-pad KiCad board.  Only the topology ordering function is swapped for the baseline; both
variants use the same Board IR conversion, A* leg search, budgets, obstacle model, and immutable
source.  No KiCad process is invoked by this benchmark and no claim of Steiner optimality or
FreeRouting parity is made.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import copper_mcp.routing.astar as astar
from copper_mcp.config import Settings
from copper_mcp.route_preview import RoutePreviewStatus, preview_route

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "tree-star.kicad_pcb"
SCRIPT_PATH = Path(__file__).relative_to(ROOT)


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


def _request() -> dict[str, Any]:
    return {
        "board": FIXTURE.name,
        "net": "AUDIO",
        "layer": "F.Cu",
        "seed": 23,
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
    }


def _run() -> dict[str, Any]:
    source = FIXTURE.read_bytes()
    source_digest = f"sha256:{hashlib.sha256(source).hexdigest()}"
    with tempfile.TemporaryDirectory(prefix="copper-mcp-steiner-benchmark-") as directory:
        workspace = Path(directory)
        board = workspace / FIXTURE.name
        board.write_bytes(source)
        settings = Settings(workspace=workspace, max_route_preview_seconds=10)
        request = _request()
        before = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)

        steiner_replays = [preview_route(request, settings) for _ in range(10)]
        steiner = steiner_replays[0]
        if any(
            replay.status is not RoutePreviewStatus.ROUTED or replay.candidate is None
            for replay in steiner_replays
        ):
            raise RuntimeError("one-Steiner preview did not route the fixture")

        # The old implementation is retained as a deterministic, internal baseline.  Swapping
        # only this pure ordering function leaves every route legality and A* budget identical.
        with patch.object(astar, "_steiner_merge_order", astar._merge_order):
            baseline_replays = [preview_route(request, settings) for _ in range(10)]
        baseline = baseline_replays[0]
        if any(
            replay.status is not RoutePreviewStatus.ROUTED or replay.candidate is None
            for replay in baseline_replays
        ):
            raise RuntimeError("component-MST baseline did not route the fixture")

        after = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
        if before != after:
            raise RuntimeError("routing preview mutated the fixture")
        if steiner.candidate.cost.length_nm >= baseline.candidate.cost.length_nm:
            raise RuntimeError("one-Steiner ordering did not improve wire length")
        return {
            "fixture_sha256": source_digest,
            # The production candidate keeps the new policy label because the route constructor
            # is intentionally not a public baseline switch; this metric names the function we
            # patched for the comparison rather than mislabeling the candidate identity.
            "baseline_policy": "component-mst-v1",
            "steiner_policy": steiner.candidate.ordering_policy,
            "baseline_wire_length_nm": baseline.candidate.cost.length_nm,
            "steiner_wire_length_nm": steiner.candidate.cost.length_nm,
            "wire_length_reduction_nm": (
                baseline.candidate.cost.length_nm - steiner.candidate.cost.length_nm
            ),
            "wire_length_reduction_ratio": round(
                1 - steiner.candidate.cost.length_nm / baseline.candidate.cost.length_nm,
                6,
            ),
            "replay_count": len(steiner_replays),
            "deterministic_steiner_candidate": all(
                replay.candidate == steiner.candidate for replay in steiner_replays
            ),
            "deterministic_baseline_candidate": all(
                replay.candidate == baseline.candidate for replay in baseline_replays
            ),
            "source_unchanged": True,
            "kicad_invoked": False,
            "steiner_optimality_claim": False,
            "freerouting_parity_claim": False,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    metrics = _run()
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/steiner-ordering/v1",
        "date_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "script": str(SCRIPT_PATH),
        "metrics": metrics,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    payload["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
