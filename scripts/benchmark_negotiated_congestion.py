#!/usr/bin/env python3
"""Measure deterministic multi-net negotiated routing against a sequential baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, net_id_for_name, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.routing import (
    AStarRouter,
    AStarSettings,
    CongestionLedger,
    NegotiatedRoutingRequest,
    RouteRequest,
    negotiate_routes,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "audio" / "fixtures" / "negotiated-crossing-v1.kicad_pcb"
OUTPUT = ROOT / "benchmarks" / "results" / "routing" / "2026-08-05-negotiated-congestion.json"
SCRIPT_PATH = Path("scripts/benchmark_negotiated_congestion.py")
BENCHMARK_NAME = "negotiated-congestion-kicad-crossing-v1"


def _replays(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 20:
        raise argparse.ArgumentTypeError("replays must be between 1 and 20")
    return parsed


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


def _settings() -> AStarSettings:
    return AStarSettings(
        grid_step_nm=1_000_000,
        bend_penalty_nm=500_000,
        proximity_penalty_nm=0,
        max_grid_nodes=512,
        max_expansions=20_000,
        max_obstacles=128,
        max_obstacle_checks=200_000,
    )


def _load_fixture() -> tuple[Any, tuple[RouteRequest, ...], bytes]:
    source = FIXTURE.read_bytes()
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=100_000,
        track_width_nm=200_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    converted = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    if converted.diagnostics or converted.snapshot is None:
        raise RuntimeError("the committed KiCad benchmark fixture failed Board IR conversion")
    snapshot = converted.snapshot
    settings = _settings()
    requests = tuple(
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net_id_for_name(name),
            layer_id="layer:F.Cu",
            seed=seed,
            settings=settings,
        )
        for name, seed in (("HORIZONTAL", 7), ("VERTICAL", 11))
    )
    return snapshot, requests, source


def _baseline(snapshot: Any, requests: tuple[RouteRequest, ...]) -> dict[str, Any]:
    ledger = CongestionLedger(
        grid_step_nm=requests[0].settings.grid_step_nm,
        present_penalty_nm=0,
        history_penalty_nm=0,
    )
    router = AStarRouter()
    candidates = []
    for request in sorted(requests, key=lambda item: (item.net_id, item.seed)):
        result = router.propose(snapshot, request, congestion_penalty=ledger.penalty)
        if result.candidate is None:
            raise RuntimeError("baseline fixture unexpectedly failed to route")
        candidates.append(result.candidate)
        ledger.add_candidate(result.candidate)
    overflow = ledger.overflow_resources()
    return {
        "candidate_ids": [
            item.candidate_id for item in sorted(candidates, key=lambda item: item.patch.net_id)
        ],
        "overflow_resources": [
            {
                "end": {"x_nm": item.end.x, "y_nm": item.end.y},
                "kind": item.kind,
                "start": {"x_nm": item.start.x, "y_nm": item.start.y},
                "usage": item.usage,
            }
            for item in overflow
        ],
        "overflow_units": sum(item.usage - 1 for item in overflow),
        "status": "sequential",
        "total_wire_length_nm": sum(item.patch.length_nm for item in candidates),
    }


def _negotiated(snapshot: Any, requests: tuple[RouteRequest, ...]) -> dict[str, Any]:
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=requests,
        max_iterations=8,
    )
    result = negotiate_routes(snapshot, envelope)
    return {
        "candidate_ids": [item.candidate_id for item in result.candidates],
        "diagnostic": result.diagnostic,
        "iterations": result.iterations,
        "overflow_units": result.overflow_units,
        "ripups": result.ripups,
        "status": result.status.value,
        "total_wire_length_nm": result.total_wire_length_nm,
        "unrouted_nets": list(result.unrouted_nets),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replays", type=_replays, default=3)
    parser.add_argument(
        "--write", action="store_true", help="write the report to the committed path"
    )
    args = parser.parse_args()

    snapshot, requests, source = _load_fixture()
    baseline = _baseline(snapshot, requests)
    replays = [_negotiated(snapshot, requests) for _ in range(args.replays)]
    if any(item != replays[0] for item in replays[1:]):
        raise RuntimeError("negotiated routing replay was not deterministic")
    commit, dirty = _git_metadata()
    report: dict[str, Any] = {
        "authoritative_drc": {
            "reason": "the metric is exact lattice occupancy only; no KiCad DRC was invoked",
            "status": "not_run",
        },
        "baseline": baseline,
        "benchmark": BENCHMARK_NAME,
        "commit": commit,
        "configuration": {
            "history_penalty_nm": 5_000_000,
            "max_iterations": 8,
            "max_total_expansions": 2_000_000,
            "max_total_obstacle_checks": 10_000_000,
            "present_penalty_nm": 20_000_000,
            "settings": asdict(requests[0].settings),
        },
        "dataset": {
            "fixture": FIXTURE.relative_to(ROOT).as_posix(),
            "fixture_sha256": hashlib.sha256(source).hexdigest(),
            "generator_path": SCRIPT_PATH.as_posix(),
            "generator_sha256": hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest(),
            "license": "Apache-2.0",
            "provenance": "Committed KiCad fixture; converted through the Board IR adapter.",
        },
        "dirty": dirty,
        "environment": {
            "accelerator": "none (CPU-only)",
            "kicad": "not invoked; Board IR adapter only",
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "iterations": args.replays,
        "metrics": {
            "baseline_overflow_units": baseline["overflow_units"],
            "negotiated_overflow_units": replays[0]["overflow_units"],
            "overflow_reduction_units": baseline["overflow_units"] - replays[0]["overflow_units"],
            "replay_deterministic": True,
        },
        "recorded_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "replays": replays,
        "snapshot_digest": snapshot.snapshot_digest,
    }
    report["run_id"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.write:
        OUTPUT.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
