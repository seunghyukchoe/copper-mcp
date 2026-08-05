#!/usr/bin/env python3
"""Replay a bounded, local end-to-end performance profile without changing authority.

Wall-clock samples use :func:`time.perf_counter_ns`.  ``cProfile`` runs separately after those
samples, so its instrumentation overhead cannot be mistaken for a timing result.  The report's
``identity`` section intentionally excludes timing, wall-clock, and host values; its digest binds
only the deterministic fixture/configuration manifest.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import platform
import pstats
import shutil
import statistics
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.circuit_scene import observe_board_scene
from copper_mcp.config import Settings
from copper_mcp.placement import build_placement_view, parse_placement_intent
from copper_mcp.placement.solver import PlacementSolverSettings, solve_placement
from copper_mcp.route_preview import preview_route

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "performance_profile_v1.py"
_DEFAULT_OUTPUT = (
    _ROOT / "benchmarks" / "results" / "performance" / "2026-08-05-performance-profile-v1.json"
)
_SCHEMA = "copper-mcp/performance-profile/v1"
_SEED = 23
_TOP_FUNCTIONS = 8
_ROUTING_FIXTURE = _ROOT / "tests" / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
_PLACEMENT_FIXTURE = _ROOT / "tests" / "fixtures" / "board-ir-v0.1" / "footprint-rotation.kicad_pcb"
_SCENE_FIXTURE = _ROOT / "tests" / "fixtures" / "circuit-scene-v0.1" / "scene-region.kicad_pcb"
_CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}
_PLACEMENT_SETTINGS = PlacementSolverSettings(
    max_evaluations=128,
    max_rounds=5,
    beam_width=4,
    max_ranked=8,
    step_nm=1_000_000,
    deadline_seconds=10.0,
    legalizer_max_checks=100_000,
    legalizer_deadline_seconds=2.0,
)


def _count(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 20:
        raise argparse.ArgumentTypeError("count must be between 1 and 20")
    return parsed


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("ascii")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:performance", name="Performance", **_CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _route_pipeline() -> str:
    result = preview_route(
        {
            "board": _ROUTING_FIXTURE.name,
            "constraints": _CONSTRAINTS,
            "layer": "F.Cu",
            "net": "AUDIO",
            "seed": _SEED,
        },
        Settings(workspace=_ROUTING_FIXTURE.parent),
    )
    if result.candidate is None or result.diagnostic is not None:
        raise RuntimeError("fixed routing fixture did not produce a candidate")
    return result.candidate.candidate_id


def _placement_pipeline() -> str:
    source = _PLACEMENT_FIXTURE.read_bytes()
    conversion = parse_kicad_bytes(source, _profile())
    if conversion.snapshot is None or conversion.diagnostics:
        raise RuntimeError("fixed placement fixture is outside the supported Board IR subset")
    view = build_placement_view(source, conversion.snapshot)
    intent = parse_placement_intent(
        {
            "board": _PLACEMENT_FIXTURE.name,
            "constraints": _CONSTRAINTS,
            "placement_grid_nm": _PLACEMENT_SETTINGS.step_nm,
            "subjects": sorted(view.footprints),
        }
    )
    result = solve_placement(intent, conversion.snapshot, view, settings=_PLACEMENT_SETTINGS)
    if result.initial_score is None or not result.ranked:
        raise RuntimeError(f"fixed placement fixture produced no ranked candidate: {result.status}")
    return result.ranked[0].candidate.candidate_id


def _scene_pipeline() -> str:
    scene = observe_board_scene(
        {
            "board": _SCENE_FIXTURE.name,
            "constraints": _CONSTRAINTS,
            "region": {
                "max_x_nm": 1_000_000_000,
                "max_y_nm": 1_000_000_000,
                "min_x_nm": -1_000_000_000,
                "min_y_nm": -1_000_000_000,
            },
        },
        Settings(workspace=_SCENE_FIXTURE.parent),
    )
    if not scene.supported or scene.snapshot_digest is None:
        raise RuntimeError("fixed Circuit Scene fixture was not supported")
    return scene.snapshot_digest


def _redacted_label(function: tuple[str, int, str]) -> str:
    """Return a stable function label without leaking an absolute local path."""

    filename, _line, name = function
    if filename == "~":
        return f"builtins:{name}"
    try:
        relative = Path(filename).resolve().relative_to(_ROOT / "src")
    except (OSError, ValueError):
        return f"external:{Path(filename).name}:{name}"
    return f"copper_mcp:{relative.with_suffix('').as_posix().replace('/', '.')}:{name}"


def _cumulative_hotspots(profile: cProfile.Profile) -> list[dict[str, int | str]]:
    stats = pstats.Stats(profile)
    rows: list[dict[str, int | str]] = []
    for function, values in stats.stats.items():
        primitive_calls, total_calls, total_seconds, cumulative_seconds, _callers = values
        rows.append(
            {
                "cumulative_time_ns": round(cumulative_seconds * 1_000_000_000),
                "function": _redacted_label(function),
                "primitive_calls": primitive_calls,
                "self_time_ns": round(total_seconds * 1_000_000_000),
                "total_calls": total_calls,
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            -int(item["cumulative_time_ns"]),
            -int(item["self_time_ns"]),
            str(item["function"]),
        ),
    )[:_TOP_FUNCTIONS]


def _timing_summary(samples_ns: list[int]) -> dict[str, int | list[int]]:
    return {
        "max_ns": max(samples_ns),
        "median_ns": int(statistics.median(samples_ns)),
        "min_ns": min(samples_ns),
        "samples_ns": samples_ns,
    }


def _scenario(
    name: str,
    fixture: Path,
    runner: Callable[[], str],
    *,
    warmups: int,
    samples: int,
) -> dict[str, Any]:
    for _ in range(warmups):
        runner()
    outputs: list[str] = []
    timings_ns: list[int] = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        outputs.append(runner())
        timings_ns.append(time.perf_counter_ns() - started)
    if len(set(outputs)) != 1:
        raise RuntimeError(f"{name} did not replay deterministically")

    profile = cProfile.Profile()
    profile.enable()
    try:
        profiled_output = runner()
    finally:
        profile.disable()
    if profiled_output != outputs[0]:
        raise RuntimeError(f"{name} profile pass diverged from the unprofiled replay")
    return {
        "fixture": {"id": name, "sha256": _sha256(fixture.read_bytes())},
        "hotspots_cumulative": _cumulative_hotspots(profile),
        "output_digest": _sha256(outputs[0].encode("ascii")),
        "timing_perf_counter_ns": _timing_summary(timings_ns),
    }


def _git_output(*arguments: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("performance profile baseline requires local Git provenance")
    try:
        return subprocess.run(  # noqa: S603 - executable is resolved locally with shutil.which
            [git, *arguments],
            check=True,
            capture_output=True,
            cwd=_ROOT,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "performance profile baseline cannot establish Git provenance"
        ) from error


def _clean_source_provenance() -> dict[str, bool | str]:
    """Return bound source provenance only when no tracked or untracked input can drift."""

    if _git_output("status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError(
            "performance profile baseline requires a clean tracked and untracked tree"
        )
    git_head = _git_output("rev-parse", "HEAD")
    if len(git_head) != 40 or any(character not in "0123456789abcdef" for character in git_head):
        raise RuntimeError("performance profile baseline Git provenance is malformed")
    return {"clean_worktree": True, "git_head": git_head}


def _identity(
    *,
    warmups: int,
    samples: int,
    source_provenance: Mapping[str, bool | str],
) -> dict[str, Any]:
    return {
        "fixed_seed": _SEED,
        "fixture_manifest": {
            "placement": _sha256(_PLACEMENT_FIXTURE.read_bytes()),
            "routing": _sha256(_ROUTING_FIXTURE.read_bytes()),
            "scene": _sha256(_SCENE_FIXTURE.read_bytes()),
        },
        "measurement_configuration": {
            "hotspot_limit": _TOP_FUNCTIONS,
            "samples": samples,
            "warmups": warmups,
        },
        "schema": _SCHEMA,
        "script_sha256": _sha256(_SCRIPT.read_bytes()),
        "source_provenance": dict(source_provenance),
    }


def _validate_report(document: Mapping[str, Any]) -> None:
    if document.get("schema") != _SCHEMA or not isinstance(document.get("identity"), dict):
        raise ValueError("performance profile schema is malformed")
    expected_identity_digest = _sha256(_canonical_bytes(document["identity"]))
    if document.get("identity_digest") != expected_identity_digest:
        raise ValueError("performance profile identity digest is malformed")
    source_provenance = document["identity"].get("source_provenance")
    if (
        not isinstance(source_provenance, dict)
        or source_provenance.get("clean_worktree") is not True
    ):
        raise ValueError("performance profile clean provenance is malformed")
    if source_provenance.get("git_head") != document.get("provenance", {}).get("git_head"):
        raise ValueError("performance profile source provenance is not bound")
    report_without_run_id = dict(document)
    run_id = report_without_run_id.pop("run_id", None)
    if run_id != _sha256(_canonical_bytes(report_without_run_id)):
        raise ValueError("performance profile run identifier is malformed")
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != {"placement", "routing", "scene"}:
        raise ValueError("performance profile scenarios are malformed")
    for name, scenario in scenarios.items():
        if not isinstance(scenario, dict) or set(scenario) != {
            "fixture",
            "hotspots_cumulative",
            "output_digest",
            "timing_perf_counter_ns",
        }:
            raise ValueError(f"{name} scenario is malformed")
        hotspots = scenario["hotspots_cumulative"]
        if not isinstance(hotspots, list) or not 1 <= len(hotspots) <= _TOP_FUNCTIONS:
            raise ValueError(f"{name} hotspot output is malformed")
        ordering = [
            (-item["cumulative_time_ns"], -item["self_time_ns"], item["function"])
            for item in hotspots
        ]
        if ordering != sorted(ordering):
            raise ValueError(f"{name} hotspots are not cumulatively ranked")
        if any("/" in item["function"] or "\\" in item["function"] for item in hotspots):
            raise ValueError(f"{name} hotspot output leaks a path")


def build_report(*, warmups: int, samples: int) -> dict[str, Any]:
    source_provenance = _clean_source_provenance()
    identity = _identity(
        warmups=warmups,
        samples=samples,
        source_provenance=source_provenance,
    )
    run_started = time.monotonic_ns()
    document: dict[str, Any] = {
        "identity": identity,
        "identity_digest": _sha256(_canonical_bytes(identity)),
        "instrumentation": {
            "hotspots": "cProfile cumulative time; bounded redacted function summaries only",
            "run_span": "time.monotonic_ns; operational span only, excluded from identity",
            "timings": (
                "time.perf_counter_ns; unprofiled wall-clock samples, excluded from identity"
            ),
        },
        "not_claimed": [
            "an acceleration, Rust, GPU, KiCad CLI, DRC, fabrication, or hardware result",
            "a cross-machine timing comparison or stable nanosecond precision",
            "public-contract, routing-policy, placement-policy, or mutation behavior changes",
        ],
        "provenance": {
            **source_provenance,
            "machine": platform.machine() or "unknown",
            "python": platform.python_version(),
        },
        "schema": _SCHEMA,
        "scenarios": {
            "placement": _scenario(
                "placement",
                _PLACEMENT_FIXTURE,
                _placement_pipeline,
                warmups=warmups,
                samples=samples,
            ),
            "routing": _scenario(
                "routing", _ROUTING_FIXTURE, _route_pipeline, warmups=warmups, samples=samples
            ),
            "scene": _scenario(
                "scene", _SCENE_FIXTURE, _scene_pipeline, warmups=warmups, samples=samples
            ),
        },
    }
    document["monotonic_run_span_ns"] = time.monotonic_ns() - run_started
    document["run_id"] = _sha256(_canonical_bytes(document))
    _validate_report(document)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--samples", type=_count, default=5)
    parser.add_argument("--warmups", type=_count, default=2)
    args = parser.parse_args(argv)

    report = build_report(warmups=args.warmups, samples=args.samples)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
