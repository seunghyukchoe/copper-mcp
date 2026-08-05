#!/usr/bin/env python3
"""Replay the deterministic placement-solver baseline on a committed fixture.

The metric is a same-net Manhattan connectivity proxy.  Every retained state is evaluated by
the existing placement legalizer, so the benchmark makes no direct geometry, DRC, electrical,
fabrication, optimality, or live-editor claim.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.placement import build_placement_view, parse_placement_intent
from copper_mcp.placement.solver import PlacementSolverSettings, solve_placement

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "tests/fixtures/board-ir-v0.1/footprint-rotation.kicad_pcb"
_DEFAULT_OUTPUT = (
    _ROOT / "benchmarks/results/placement/2026-08-05-placement-solver-baseline-v1.json"
)
_CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}
_SETTINGS = PlacementSolverSettings(
    max_evaluations=128,
    max_rounds=5,
    beam_width=4,
    max_ranked=8,
    step_nm=1_000_000,
    deadline_seconds=10.0,
    legalizer_max_checks=100_000,
    legalizer_deadline_seconds=2.0,
)


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - executable is resolved locally
            [git, "rev-parse", "HEAD"],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:placement-benchmark", name="Placement benchmark", **_CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _run(repetitions: int) -> dict[str, Any]:
    if repetitions < 2 or repetitions > 16:
        raise ValueError("repetitions must be between 2 and 16")
    source = _FIXTURE.read_bytes()
    conversion = parse_kicad_bytes(source, _profile())
    if conversion.snapshot is None or conversion.diagnostics:
        raise RuntimeError("benchmark fixture is outside the supported Board IR subset")
    view = build_placement_view(source, conversion.snapshot)
    intent = parse_placement_intent(
        {
            "board": _FIXTURE.name,
            "constraints": _CONSTRAINTS,
            "subjects": sorted(view.footprints),
            "placement_grid_nm": _SETTINGS.step_nm,
        }
    )

    samples: list[int] = []
    replay_signatures: list[tuple[object, ...]] = []
    before: int | None = None
    after: int | None = None
    all_legal = True
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        result = solve_placement(intent, conversion.snapshot, view, settings=_SETTINGS)
        samples.append(time.perf_counter_ns() - started)
        if result.initial_score is None or not result.ranked:
            raise RuntimeError(f"solver did not retain a candidate: {result.status}")
        best = min(result.ranked, key=lambda item: (item.score, item.candidate.candidate_id))
        before = result.initial_score.connectivity_manhattan_nm
        after = best.score.connectivity_manhattan_nm
        all_legal = all_legal and all(
            item.candidate.evidence.legality.legal for item in result.ranked
        )
        replay_signatures.append(
            (
                result.status,
                result.evaluations,
                tuple((item.candidate.candidate_id, item.score) for item in result.ranked),
            )
        )
    assert before is not None and after is not None
    if after >= before:
        raise RuntimeError("the fixture did not show a strict connectivity-proxy improvement")
    deterministic = all(signature == replay_signatures[0] for signature in replay_signatures[1:])
    if not deterministic:
        raise RuntimeError("deterministic replay signatures diverged")
    return {
        "repetitions": repetitions,
        "all_legalizer_candidates_legal": all_legal,
        "deterministic_replays": deterministic,
        "initial_connectivity_manhattan_nm": before,
        "best_connectivity_manhattan_nm": after,
        "connectivity_improvement_nm": before - after,
        "median_elapsed_ns": statistics.median(samples),
        "solver_settings": {
            "max_evaluations": _SETTINGS.max_evaluations,
            "max_rounds": _SETTINGS.max_rounds,
            "beam_width": _SETTINGS.beam_width,
            "step_nm": _SETTINGS.step_nm,
        },
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/placement-solver-baseline/v1",
        "date_utc": "2026-08-05",
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "fixture": "tests/fixtures/board-ir-v0.1/footprint-rotation.kicad_pcb",
        "metrics": _run(args.repetitions),
        "not_claimed": [
            "optimal placement or an approximation guarantee",
            "DRC, electrical, timing, signal-integrity, thermal, or fabrication readiness",
            "KiCad file mutation, editor mutation, apply authority, undo, or live-editor behavior",
            "routing feasibility, route length, congestion, impedance, or manufacturing clearance",
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
