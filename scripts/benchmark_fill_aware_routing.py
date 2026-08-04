#!/usr/bin/env python3
"""Measure the route-quality change from freshness-verified foreign fill islands."""

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

from copper_mcp.board_ir import (
    BoardIRSnapshot,
    ConstraintSet,
    Footprint,
    FootprintSide,
    Layer,
    Net,
    NetClass,
    NetClassAssignment,
    OutlineContour,
    Pad,
    PadKind,
    PadShape,
    PointNM,
    Ring,
    SourceInfo,
    Zone,
    make_content,
    make_snapshot,
)
from copper_mcp.routing import (
    AStarRouter,
    AStarSettings,
    RouteCandidate,
    RouteFailureCode,
    RouteRequest,
    RouteResult,
    VerifiedFill,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path("scripts/benchmark_fill_aware_routing.py")
LAYER_ID = "layer:F.Cu"
NET_ID = "net:audio"
OTHER_NET_ID = "net:power"
SOURCE_REVISION = f"sha256:{'c' * 64}"


def _rectangle(min_x: int, min_y: int, max_x: int, max_y: int) -> Ring:
    return Ring(
        (
            PointNM(min_x, min_y),
            PointNM(max_x, min_y),
            PointNM(max_x, max_y),
            PointNM(min_x, max_y),
        )
    )


def _pad(identifier: str, center: tuple[int, int]) -> Pad:
    return Pad(
        id=identifier,
        net_id=NET_ID,
        center=PointNM(*center),
        rotation_udeg=0,
        shape=PadShape.RECT,
        kind=PadKind.SMD,
        size_x_nm=400,
        size_y_nm=400,
        roundrect_radius_nm=None,
        drill_x_nm=None,
        drill_y_nm=None,
        layer_ids=(LAYER_ID,),
    )


def _snapshot(*, include_foreign_zone: bool = True) -> BoardIRSnapshot:
    route_class = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=100,
        track_width_nm=200,
        via_diameter_nm=600,
        via_drill_nm=300,
    )
    power_class = NetClass(
        id="class:power",
        name="Power",
        clearance_nm=100,
        track_width_nm=200,
        via_diameter_nm=600,
        via_drill_nm=300,
    )
    return make_snapshot(
        make_content(
            source=SourceInfo(
                format="synthetic-benchmark",
                revision=SOURCE_REVISION,
                format_version="1",
                generator="fill-aware-routing-v1",
            ),
            outline=(
                OutlineContour(
                    id="contour:main",
                    outer=_rectangle(0, 0, 10_000, 10_000),
                ),
            ),
            copper_layers=(Layer(id=LAYER_ID, name="F.Cu", index=0, kind="signal"),),
            nets=(Net(id=NET_ID, name="AUDIO"), Net(id=OTHER_NET_ID, name="POWER")),
            constraints=ConstraintSet(
                net_classes=(route_class, power_class),
                assignments=(
                    NetClassAssignment(net_id=NET_ID, net_class_id=route_class.id),
                    NetClassAssignment(net_id=OTHER_NET_ID, net_class_id=power_class.id),
                ),
            ),
            footprints=(
                Footprint(
                    id="footprint:audio:00",
                    origin=PointNM(1_000, 5_000),
                    rotation_udeg=0,
                    side=FootprintSide.FRONT,
                    pad_ids=("pad:01", "pad:02"),
                ),
            ),
            pads=(_pad("pad:01", (1_000, 5_000)), _pad("pad:02", (9_000, 5_000))),
            zones=(
                Zone(
                    id="zone:power:00",
                    net_id=OTHER_NET_ID,
                    layer_id=LAYER_ID,
                    boundary=_rectangle(3_000, 3_000, 7_000, 7_000),
                    clearance_nm=100,
                    min_thickness_nm=100,
                    thermal_gap_nm=100,
                    thermal_bridge_width_nm=100,
                ),
            )
            if include_foreign_zone
            else (),
        )
    )


def _request(snapshot: BoardIRSnapshot) -> RouteRequest:
    return RouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=NET_ID,
        layer_id=LAYER_ID,
        seed=7,
        settings=AStarSettings(
            grid_step_nm=1_000,
            bend_penalty_nm=500,
            proximity_penalty_nm=50,
            max_grid_nodes=1_000,
            max_expansions=5_000,
            max_obstacles=128,
            max_obstacle_checks=100_000,
        ),
    )


def _candidate(result: RouteResult) -> RouteCandidate:
    if result.candidate is None or result.diagnostic is not None:
        raise RuntimeError("benchmark fixture did not produce a route candidate")
    return result.candidate


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - fixed local Git argv
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
    snapshot = _snapshot()
    request = _request(snapshot)
    fill = VerifiedFill(
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        points=(
            PointNM(3_000, 6_000),
            PointNM(7_000, 6_000),
            PointNM(7_000, 7_000),
            PointNM(3_000, 7_000),
        ),
        source_revision=SOURCE_REVISION,
    )
    router = AStarRouter()
    conservative = [_candidate(router.propose(snapshot, request)) for _ in range(repetitions)]
    fill_aware = [
        _candidate(router.propose(snapshot, request, verified_fill=(fill,)))
        for _ in range(repetitions)
    ]
    # A fill island without a corresponding Board IR zone must fail closed.  Keep this as a
    # real invocation rather than a hard-coded metadata claim so the benchmark catches future
    # regressions that accidentally trust orphaned or misidentified fill geometry.
    no_zone_snapshot = _snapshot(include_foreign_zone=False)
    no_zone_result = router.propose(
        no_zone_snapshot,
        _request(no_zone_snapshot),
        verified_fill=(fill,),
    )
    no_zone_diagnostic = no_zone_result.diagnostic
    if no_zone_diagnostic is None:
        raise RuntimeError("orphaned fill was not refused with a diagnostic")
    matching_zone_required = bool(
        no_zone_result.candidate is None
        and no_zone_diagnostic.code is RouteFailureCode.UNSUPPORTED_GEOMETRY
        and "matching Board IR zone" in no_zone_diagnostic.message
    )
    if not matching_zone_required:
        raise RuntimeError("orphaned fill was not refused with a matching-zone diagnostic")
    if len({candidate.candidate_id for candidate in conservative}) != 1:
        raise RuntimeError("conservative route is not deterministic")
    if len({candidate.candidate_id for candidate in fill_aware}) != 1:
        raise RuntimeError("fill-aware route is not deterministic")
    conservative_length = conservative[0].cost.length_nm
    fill_length = fill_aware[0].cost.length_nm
    if not conservative_length > fill_length or fill_length != 8_000:
        raise RuntimeError("fill-aware route did not open the verified corridor")
    return {
        "repetitions": repetitions,
        "deterministic_conservative": True,
        "deterministic_fill_aware": True,
        "conservative_length_nm": conservative_length,
        "fill_aware_length_nm": fill_length,
        "wire_length_reduction_nm": conservative_length - fill_length,
        "conservative_candidate_id": conservative[0].candidate_id,
        "fill_aware_candidate_id": fill_aware[0].candidate_id,
        "verified_fill_source_revision": fill.source_revision,
        "matching_zone_required": matching_zone_required,
        "matching_zone_refusal_code": no_zone_diagnostic.code.value,
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
        "schema": "copper-mcp/benchmark/fill-aware-routing/v1",
        "date_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture": "synthetic-fill-corridor-v1",
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
