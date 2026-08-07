#!/usr/bin/env python3
"""Measure what one megabyte of KiCad board costs each structural parse budget, and what
adversarial input costs at the shipped defaults.

Two questions, deliberately answered in one artifact because the answer to the second is only
meaningful against the first. Real-board density says how large the defaults must be for the byte
ceiling to be the binding control; adversarial cost says what that size buys an attacker.

The private four-layer board that motivated issue #112 is **not** measured here and cannot be: it
is not in this repository and is not redistributable. Its numbers are recorded in the ledger and in
`docs/research/parse-budget-calibration-v1.md` as an explicitly out-of-repo observation. Everything
in this artifact is reproducible from a clean checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import statistics
import subprocess
import time
import tracemalloc
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.sexpr import SExpr, SExprError, parse_sexpr
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.board_ir.types import PointNM, Ring
from copper_mcp.board_ir.validation import BoardIRValidationError, _validate_ring

ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024

#: Budgets raised far past reach, so a measurement observes the board rather than the ceiling.
UNBOUNDED = ParseLimits(
    max_input_bytes=1024 * MIB,
    max_depth=4096,
    max_tokens=200_000_000,
    max_nodes=200_000_000,
    max_atom_chars=1_000_000,
    max_children_per_list=50_000_000,
    max_objects=200_000_000,
    max_vertices_per_ring=10_000_000,
    max_total_vertices=200_000_000,
    max_intersection_tests=2_000_000_000,
)

#: The budget set CopperMCP shipped before this calibration, kept so the artifact states the
#: change rather than only the new state.
PREVIOUS_DEFAULTS = replace(
    ParseLimits(),
    max_tokens=1_000_000,
    max_nodes=500_000,
    max_children_per_list=100_000,
    max_total_vertices=1_000_000,
)


def _git_metadata() -> tuple[str, bool | None]:
    git = shutil.which("git")
    if git is None:
        return "unknown", None
    try:
        commit = subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True, timeout=5
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
                via_diameter_nm=800_000,
                via_drill_nm=400_000,
            ),
        ),
        default_net_class_id="class:default",
    )


def _walk(root: SExpr) -> tuple[int, int, int]:
    """Return (nodes, widest list, depth) using exactly ``parse_sexpr``'s node accounting."""

    nodes = 0
    widest = 0
    deepest = 0
    stack: list[tuple[SExpr, int]] = [(root, 1)]
    while stack:
        expression, depth = stack.pop()
        deepest = max(deepest, depth)
        widest = max(widest, len(expression.items))
        nodes += 1
        for item in expression.items:
            if isinstance(item, SExpr):
                stack.append((item, depth + 1))
            else:
                nodes += 1
    return nodes, widest, deepest


def _object_count(content: Any) -> int:
    groups = (
        content.outline,
        content.copper_layers,
        content.nets,
        content.constraints.net_classes,
        content.constraints.assignments,
        content.constraints.differential_pairs,
        content.constraints.length_rules,
        content.footprints,
        content.pads,
        content.vias,
        content.segments,
        content.arcs,
        content.zones,
        content.keepouts,
    )
    return sum(len(group) for group in groups)


def _board_row(path: Path) -> dict[str, Any] | None:
    source = path.read_bytes()
    try:
        root = parse_sexpr(source, UNBOUNDED)
    except SExprError:
        return None
    nodes, widest, deepest = _walk(root)
    conversion = parse_kicad_bytes(source, _profile(), UNBOUNDED)
    if conversion.snapshot is None:
        return None
    mib = len(source) / MIB
    rings = [contour.outer for contour in conversion.snapshot.content.outline]
    rings += [ring for contour in conversion.snapshot.content.outline for ring in contour.holes]
    rings += [zone.boundary for zone in conversion.snapshot.content.zones]
    rings += [keepout.boundary for keepout in conversion.snapshot.content.keepouts]
    rings += [
        courtyard
        for footprint in conversion.snapshot.content.footprints
        for courtyard in footprint.courtyards
    ]
    objects = _object_count(conversion.snapshot.content)
    return {
        "bytes": len(source),
        "children_per_mib": round(widest / mib),
        "max_children_per_list": widest,
        "max_depth": deepest,
        "nodes": nodes,
        "nodes_per_mib": round(nodes / mib),
        "objects": objects,
        "objects_per_mib": round(objects / mib),
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(source).hexdigest(),
        "tokens_per_mib": round(_token_count(source) / mib),
        "total_ring_vertices": sum(len(ring.points) for ring in rings),
    }


def _token_count(source: bytes) -> int:
    from copper_mcp.adapters.sexpr import _tokens

    return sum(1 for _ in _tokens(source.decode("utf-8"), UNBOUNDED))


def _adversarial_payloads(ceiling: int) -> dict[str, bytes]:
    deep_unit = "(" * 126 + "a" + ")" * 126
    chunk = "(g" + " a" * 1000 + ")"
    group = "(p" + chunk * 100 + ")"
    return {
        "deep": (
            "(kicad_pcb" + deep_unit * max(1, (ceiling - 16) // len(deep_unit)) + ")"
        ).encode(),
        "flat": ("(kicad_pcb" + " a" * ((ceiling - 12) // 2) + ")").encode(),
        "strings": (
            "(kicad_pcb" + (' "' + "a" * 4096 + '"') * max(1, (ceiling - 16) // 4099) + ")"
        ).encode(),
        "tree": ("(kicad_pcb" + group * ((ceiling - 64) // len(group)) + ")").encode(),
        "wide": ("(kicad_pcb(gr_poly" + " a" * ((ceiling - 24) // 2) + "))").encode(),
    }


def _parse_outcome(payload: bytes, limits: ParseLimits) -> str:
    try:
        parse_sexpr(payload, limits)
    except SExprError as error:
        return error.code
    return "parsed"


def _adversarial_row(name: str, payload: bytes, limits: ParseLimits) -> dict[str, Any]:
    """Time the shape twice: once clean, once traced.

    ``tracemalloc`` hooks every allocation and inflates parse time by roughly an order of
    magnitude, so a timing taken under it is not a wall-clock claim. Recording both keeps the
    memory figure and the time figure from being read off the same instrumented run.
    """

    started_ns = time.perf_counter_ns()
    outcome = _parse_outcome(payload, limits)
    uninstrumented_ns = time.perf_counter_ns() - started_ns

    tracemalloc.start()
    started_ns = time.perf_counter_ns()
    traced_outcome = _parse_outcome(payload, limits)
    traced_ns = time.perf_counter_ns() - started_ns
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if traced_outcome != outcome:
        raise RuntimeError(f"{name} parsed differently under instrumentation")
    return {
        "bytes": len(payload),
        "elapsed_ns": uninstrumented_ns,
        "outcome": outcome,
        "peak_traced_bytes": peak_bytes,
        "shape": name,
        "traced_elapsed_ns": traced_ns,
    }


def _ring_cost_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for points in (500, 1000, 2001, 4000):
        half = points // 2
        ring = Ring(
            tuple(
                [PointNM(x=index * 1000, y=0) for index in range(half)]
                + [PointNM(x=index * 1000, y=1000) for index in reversed(range(half))]
            )
        )
        budget = [0]
        started_ns = time.perf_counter_ns()
        try:
            _validate_ring(
                ring,
                locator="ring",
                limits=replace(UNBOUNDED, max_vertices_per_ring=100_000),
                intersection_budget=budget,
            )
            outcome = "clean"
        except BoardIRValidationError as error:
            outcome = error.code
        rows.append(
            {
                "elapsed_ns": time.perf_counter_ns() - started_ns,
                "intersection_tests": budget[0],
                "outcome": outcome,
                "points": points,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adversarial-bytes", type=int, default=16 * MIB)
    args = parser.parse_args()

    boards = [
        row
        for path in sorted(ROOT.rglob("*.kicad_pcb"))
        if ".git" not in path.parts
        for row in (_board_row(path),)
        if row is not None
    ]
    payloads = _adversarial_payloads(args.adversarial_bytes)
    adversarial = {
        "previous_defaults": [
            _adversarial_row(name, payload, PREVIOUS_DEFAULTS)
            for name, payload in sorted(payloads.items())
        ],
        "shipped_defaults": [
            _adversarial_row(name, payload, ParseLimits())
            for name, payload in sorted(payloads.items())
        ],
    }

    commit, dirty = _git_metadata()
    report: dict[str, Any] = {
        "adversarial": adversarial,
        "adversarial_input_bytes": args.adversarial_bytes,
        "benchmark": "parse-budget-calibration-v1",
        "boards": boards,
        "boards_measured": len(boards),
        "commit": commit,
        "configuration": {
            "previous_defaults": asdict(PREVIOUS_DEFAULTS),
            "shipped_defaults": asdict(ParseLimits()),
        },
        "density_per_mib": {
            name: {
                "max": max(row[key] for row in boards),
                "median": int(statistics.median(row[key] for row in boards)),
                "min": min(row[key] for row in boards),
            }
            for name, key in (
                ("children_per_list", "children_per_mib"),
                ("nodes", "nodes_per_mib"),
                ("objects", "objects_per_mib"),
                ("tokens", "tokens_per_mib"),
            )
        },
        "dirty": dirty,
        "environment": {
            "accelerator": "none (CPU-only)",
            "machine": platform.machine(),
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "python": platform.python_version(),
        },
        "instrumentation": "perf_counter_ns with tracemalloc enabled",
        "observed_maxima": {
            "children_per_list": max(row["max_children_per_list"] for row in boards),
            "depth": max(row["max_depth"] for row in boards),
            "total_ring_vertices": max(row["total_ring_vertices"] for row in boards),
        },
        "recorded_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "ring_intersection_cost": _ring_cost_rows(),
        "scope": (
            "In-repository KiCad boards only. The private 4,386,848-byte four-layer board that "
            "motivated issue #112 is not redistributable and is recorded in the ledger as an "
            "explicitly out-of-repo observation."
        ),
    }
    report["run_id"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
