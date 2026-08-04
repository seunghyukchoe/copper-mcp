#!/usr/bin/env python3
"""Replay the bounded same-side rectangular-courtyard placement oracle.

This benchmark exercises the deterministic legalizer without invoking KiCad. It measures the
contract boundary (same-side overlap is refused, cross-side overlap is clear, and boards without
courtyards remain clear) and does not claim configurable courtyard clearance or general topology.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.placement import build_placement_view, evaluate_placement, parse_placement_intent

ROOT = Path(__file__).resolve().parents[1]
FRONT_BACK = ROOT / "tests/fixtures/board-ir-v0.2/footprint-front-back-pose.kicad_pcb"
NO_COURTYARD = ROOT / "tests/fixtures/placement-v0.1/placement-legal.kicad_pcb"


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


def _evaluate(source: bytes, board_name: str) -> dict[str, Any]:
    conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
    if conversion.snapshot is None or conversion.diagnostics:
        raise RuntimeError("benchmark source is outside the supported Board IR subset")
    view = build_placement_view(source, conversion.snapshot)
    intent = parse_placement_intent(
        {
            "board": board_name,
            "constraints": {
                "clearance_nm": 200_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 600_000,
                "via_drill_nm": 300_000,
            },
            "subjects": sorted(view.footprints),
        }
    )
    result = evaluate_placement(intent, conversion.snapshot, view)
    if result.candidate is not None:
        legality = result.candidate.evidence.legality
        checks = result.candidate.evidence.checks_used
    elif result.diagnostic is not None and result.diagnostic.legality is not None:
        legality = result.diagnostic.legality
        checks = result.diagnostic.checks_used
    else:
        raise RuntimeError("benchmark placement produced no legality evidence")
    return {
        "status": result.status,
        "courtyard_overlap": legality.courtyard_overlap,
        "pad_overlap": legality.pad_overlap,
        "checks_used": checks,
    }


def _run() -> dict[str, Any]:
    source = FRONT_BACK.read_bytes()
    same_side = source.replace(b'(layer "B.Cu")', b'(layer "F.Cu")', 1)
    same_side = same_side.replace(b'(layer "B.CrtYd")', b'(layer "F.CrtYd")', 1)
    same_side = same_side.replace(b"(at 60 20 0)", b"(at 20 20 0)", 1)
    cross_side = source.replace(b"(at 60 20 0)", b"(at 20 20 0)", 1)
    no_courtyard = NO_COURTYARD.read_bytes()
    same = _evaluate(same_side, "same-side-overlap.kicad_pcb")
    cross = _evaluate(cross_side, "cross-side-overlap.kicad_pcb")
    absent = _evaluate(no_courtyard, "no-courtyard.kicad_pcb")
    return {
        "same_side_overlap_refused": same["status"] == "refused"
        and same["courtyard_overlap"] == "violated"
        and same["pad_overlap"] == "proven_clear",
        "cross_side_overlap_clear": cross["status"] == "previewed"
        and cross["courtyard_overlap"] == "proven_clear",
        "absent_courtyard_clear": absent["status"] == "previewed"
        and absent["courtyard_overlap"] == "proven_clear",
        "same_side": same,
        "cross_side": cross,
        "absent": absent,
        "kicad_invoked": False,
        "workspace_mutations": 0,
        "general_topology_claim": False,
        "custom_clearance_claim": False,
        "placement_apply_claim": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "benchmarks/results/placement/2026-08-05-courtyard-legality.json",
    )
    args = parser.parse_args()
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/placement-courtyard-legality/v1",
        "date_utc": "2026-08-05",
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "fixtures": [
            str(FRONT_BACK.relative_to(ROOT)),
            str(NO_COURTYARD.relative_to(ROOT)),
        ],
        "metrics": _run(),
        "not_claimed": [
            "configurable nonzero courtyard clearance",
            "line-chain or polygon courtyard topology",
            "KiCad DRC or placement apply",
            "post-placement connectivity",
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
