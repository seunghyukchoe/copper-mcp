#!/usr/bin/env python3
"""Measure the layered route-quality change from freshness-verified foreign fill islands."""

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
    Keepout,
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
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteCandidate,
    LayeredRouteFailureCode,
    LayeredRouteRequest,
    LayeredRouteResult,
    VerifiedFill,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path("scripts/benchmark_layered_fill_obstacles.py")
LAYER_ID = "layer:F.Cu"
BACK_LAYER_ID = "layer:B.Cu"
NET_ID = "net:audio"
OTHER_NET_ID = "net:power"
SOURCE_REVISION = f"sha256:{'d' * 64}"
OTHER_REVISION = f"sha256:{'e' * 64}"
ZONE_BOUNDS = (3_000, 3_000, 7_000, 7_000)
ISLAND_BOUNDS = (3_000, 6_000, 7_000, 7_000)
ESCAPING_ISLAND_BOUNDS = (3_000, 6_000, 7_500, 7_000)


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
    net_class = NetClass(
        id="class:audio",
        name="Audio",
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
                generator="layered-fill-obstacles-v1",
            ),
            outline=(OutlineContour(id="contour:main", outer=_rectangle(0, 0, 10_000, 10_000)),),
            copper_layers=(
                Layer(id=LAYER_ID, name="F.Cu", index=0, kind="signal"),
                Layer(id=BACK_LAYER_ID, name="B.Cu", index=1, kind="signal"),
            ),
            nets=(Net(id=NET_ID, name="AUDIO"), Net(id=OTHER_NET_ID, name="POWER")),
            constraints=ConstraintSet(
                net_classes=(net_class,),
                assignments=(
                    NetClassAssignment(net_id=NET_ID, net_class_id=net_class.id),
                    NetClassAssignment(net_id=OTHER_NET_ID, net_class_id=net_class.id),
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
                    boundary=_rectangle(*ZONE_BOUNDS),
                    clearance_nm=100,
                    min_thickness_nm=100,
                    thermal_gap_nm=100,
                    thermal_bridge_width_nm=100,
                ),
            )
            if include_foreign_zone
            else (),
            # The back layer is walled off, so the conservative answer must be a front-layer
            # detour around the whole outline rather than a cheap via escape.
            keepouts=(
                Keepout(
                    id="keepout:back-wall",
                    layer_ids=(BACK_LAYER_ID,),
                    boundary=_rectangle(4_000, 0, 6_000, 10_000),
                    prohibit_tracks=True,
                    prohibit_vias=False,
                    prohibit_pads=False,
                    prohibit_zones=False,
                    prohibit_footprints=False,
                ),
            ),
        )
    )


def _island(bounds: tuple[int, int, int, int], *, revision: str = SOURCE_REVISION) -> VerifiedFill:
    min_x, min_y, max_x, max_y = bounds
    return VerifiedFill(
        net_id=OTHER_NET_ID,
        layer_id=LAYER_ID,
        points=(
            PointNM(min_x, min_y),
            PointNM(max_x, min_y),
            PointNM(max_x, max_y),
            PointNM(min_x, max_y),
        ),
        source_revision=revision,
    )


def _request(snapshot: BoardIRSnapshot, fill: tuple[VerifiedFill, ...] = ()) -> LayeredRouteRequest:
    return LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=NET_ID,
        start_pad_id="pad:01",
        end_pad_id="pad:02",
        start_layer_id=LAYER_ID,
        end_layer_id=LAYER_ID,
        seed=7,
        grid_step_nm=1_000,
        settings=LayeredAStarSettings(
            move_cost=1,
            via_cost=2,
            max_expansions=100_000,
            max_nodes=250_000,
            max_obstacles=128,
            max_obstacle_checks=2_000_000,
        ),
        verified_fill=fill,
    )


def _candidate(result: LayeredRouteResult) -> LayeredRouteCandidate:
    if result.candidate is None or result.diagnostic is not None:
        raise RuntimeError("benchmark fixture did not produce a layered route candidate")
    return result.candidate


def _refusal(result: LayeredRouteResult, code: LayeredRouteFailureCode, fragment: str) -> str:
    diagnostic = result.diagnostic
    if result.candidate is not None or diagnostic is None:
        raise RuntimeError(f"expected a {code.value} refusal, got a candidate")
    if diagnostic.code is not code or fragment not in diagnostic.message:
        raise RuntimeError(f"expected a {code.value} refusal mentioning {fragment!r}")
    return diagnostic.code.value


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
    island = _island(ISLAND_BOUNDS)
    router = LayeredBoardRouter()

    conservative = [
        _candidate(router.propose(snapshot, _request(snapshot))) for _ in range(repetitions)
    ]
    fill_aware = [
        _candidate(router.propose(snapshot, _request(snapshot, (island,))))
        for _ in range(repetitions)
    ]
    if len({candidate.candidate_id for candidate in conservative}) != 1:
        raise RuntimeError("conservative layered route is not deterministic")
    if len({candidate.candidate_id for candidate in fill_aware}) != 1:
        raise RuntimeError("fill-aware layered route is not deterministic")

    # Every gate is exercised as a real invocation rather than asserted as metadata, so a future
    # regression that starts trusting stale, orphaned, or unclipped fill fails the benchmark.
    stale_code = _refusal(
        router.propose(
            snapshot, _request(snapshot, (_island(ISLAND_BOUNDS, revision=OTHER_REVISION),))
        ),
        LayeredRouteFailureCode.STALE_REVISION,
        "different board revision",
    )
    orphan_snapshot = _snapshot(include_foreign_zone=False)
    orphan_code = _refusal(
        router.propose(orphan_snapshot, _request(orphan_snapshot, (island,))),
        LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
        "matching Board IR zone",
    )
    escaping_code = _refusal(
        router.propose(snapshot, _request(snapshot, (_island(ESCAPING_ISLAND_BOUNDS),))),
        LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
        "escapes its backing",
    )

    # Metamorphic replay: nested islands inside the same outline must be monotone in cost, and
    # the outline-sized island must not beat the envelope it replaced.
    nested_bounds = (
        ISLAND_BOUNDS,
        (3_000, 5_000, 7_000, 7_000),
        (3_000, 4_000, 7_000, 7_000),
        ZONE_BOUNDS,
    )
    nested_lengths = [
        _candidate(
            router.propose(snapshot, _request(snapshot, (_island(bounds),)))
        ).cost.wire_length_nm
        for bounds in nested_bounds
    ]
    if nested_lengths != sorted(nested_lengths):
        raise RuntimeError("growing a verified island cheapened the layered route")

    conservative_length = conservative[0].cost.wire_length_nm
    fill_length = fill_aware[0].cost.wire_length_nm
    if nested_lengths[-1] != conservative_length:
        raise RuntimeError("an outline-sized island did not reproduce the conservative route")
    if not conservative_length > fill_length or fill_aware[0].cost.via_count != 0:
        raise RuntimeError("fill-aware layered routing did not open the verified corridor")
    return {
        "repetitions": repetitions,
        "deterministic_conservative": True,
        "deterministic_fill_aware": True,
        "conservative_wire_length_nm": conservative_length,
        "fill_aware_wire_length_nm": fill_length,
        "wire_length_reduction_nm": conservative_length - fill_length,
        "conservative_via_count": conservative[0].cost.via_count,
        "fill_aware_via_count": fill_aware[0].cost.via_count,
        "conservative_candidate_id": conservative[0].candidate_id,
        "fill_aware_candidate_id": fill_aware[0].candidate_id,
        "nested_island_wire_lengths_nm": nested_lengths,
        "growth_monotonic": True,
        "verified_fill_source_revision": SOURCE_REVISION,
        "stale_revision_refusal_code": stale_code,
        "orphaned_island_refusal_code": orphan_code,
        "escaping_island_refusal_code": escaping_code,
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
        "schema": "copper-mcp/benchmark/layered-fill-obstacles/v1",
        "date_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture": "synthetic-layered-fill-corridor-v1",
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
