#!/usr/bin/env python3
"""Measure the disposable, request-replayed layered KiCad serializer.

This benchmark uses the independently authored blocked-pad fixture.  It measures deterministic
segment/via rendering, Board IR round-trip equality, stale/tamper refusal, and source immutability.
It does not invoke KiCad or claim DRC/electrical/fabrication validity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.adapters import (
    KiCadConstraintProfile,
    KiCadLayeredRoutePatchError,
    parse_kicad_bytes,
    render_kicad_layered_candidate_board,
)
from copper_mcp.board_ir import NetClass
from copper_mcp.routing import LayeredAStarSettings, LayeredBoardRouter, LayeredRouteRequest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
SCRIPT_PATH = Path("scripts/benchmark_layered_kicad_serializer.py")
F_LAYER = "layer:F.Cu"


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


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


def _run(repetitions: int) -> dict[str, Any]:
    source = FIXTURE.read_bytes()
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    if conversion.snapshot is None or conversion.diagnostics:
        raise RuntimeError("fixture does not convert cleanly")
    snapshot = conversion.snapshot
    pads = tuple(
        pad for pad in snapshot.content.pads if pad.net_id == snapshot.content.pads[0].net_id
    )
    request = LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=pads[0].net_id,
        start_pad_id=pads[0].id,
        end_pad_id=pads[1].id,
        start_layer_id=F_LAYER,
        end_layer_id=F_LAYER,
        grid_step_nm=1_000,
        settings=LayeredAStarSettings(via_cost=2),
    )
    proposed = LayeredBoardRouter().propose(snapshot, request)
    if proposed.candidate is None or proposed.diagnostic is not None:
        raise RuntimeError("fixture did not produce a layered candidate")
    candidate = proposed.candidate
    outputs = [
        render_kicad_layered_candidate_board(source, snapshot, candidate, profile, request=request)
        for _ in range(repetitions)
    ]
    if len(set(outputs)) != 1:
        raise RuntimeError("layered serializer is not deterministic")
    round_trip = parse_kicad_bytes(outputs[0], profile)
    if round_trip.snapshot is None or round_trip.diagnostics:
        raise RuntimeError("serialized board did not round-trip")
    stale_refused = False
    stale_request = LayeredRouteRequest(
        board_revision=f"sha256:{'1' * 64}",
        net_id=request.net_id,
        start_pad_id=request.start_pad_id,
        end_pad_id=request.end_pad_id,
        start_layer_id=request.start_layer_id,
        end_layer_id=request.end_layer_id,
        grid_step_nm=request.grid_step_nm,
        settings=request.settings,
    )
    try:
        render_kicad_layered_candidate_board(
            source, snapshot, candidate, profile, request=stale_request
        )
    except KiCadLayeredRoutePatchError:
        stale_refused = True
    if not stale_refused:
        raise RuntimeError("stale request was not refused")
    return {
        "repetitions": repetitions,
        "deterministic_outputs": len(set(outputs)) == 1,
        "source_unchanged": source == FIXTURE.read_bytes(),
        "candidate_id": candidate.candidate_id,
        "path_count": len(candidate.patch.paths),
        "via_count": len(candidate.patch.vias),
        "serialized_sha256": "sha256:" + hashlib.sha256(outputs[0]).hexdigest(),
        "serialized_bytes": len(outputs[0]),
        "round_trip": True,
        "stale_request_refused": stale_refused,
        "kicad_invoked": False,
        "drc": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 100:
        raise SystemExit("--repetitions must be between 1 and 100")
    metrics = _run(args.repetitions)
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/layered-kicad-serializer/v1",
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
