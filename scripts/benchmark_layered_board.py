#!/usr/bin/env python3
"""Measure the Board IR-bound, candidate-only layered routing seam.

The benchmark deliberately uses synthetic, self-contained Board IR fixtures.  It measures
deterministic proposal behavior and fail-closed boundaries; it does not invoke KiCad or claim DRC.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
from dataclasses import replace
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
    make_content,
    make_snapshot,
)
from copper_mcp.routing import (
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteFailureCode,
    LayeredRouteRequest,
    verify_layered_candidate_id,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path("scripts/benchmark_layered_board.py")
F_LAYER = "layer:F.Cu"
B_LAYER = "layer:B.Cu"
NET_ID = "net:audio"
SOURCE_REVISION = f"sha256:{'a' * 64}"
OTHER_REVISION = f"sha256:{'b' * 64}"


class BenchmarkError(RuntimeError):
    """The Board IR proposal seam was non-deterministic or violated its contract."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


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


def _rectangle(min_x: int, min_y: int, max_x: int, max_y: int) -> Ring:
    return Ring(
        (
            PointNM(min_x, min_y),
            PointNM(max_x, min_y),
            PointNM(max_x, max_y),
            PointNM(min_x, max_y),
        )
    )


def _keepout(
    identifier: str,
    layers: tuple[str, ...],
    bounds: tuple[int, int, int, int],
    *,
    tracks: bool,
    vias: bool,
) -> Keepout:
    return Keepout(
        id=identifier,
        layer_ids=layers,
        boundary=_rectangle(*bounds),
        prohibit_tracks=tracks,
        prohibit_vias=vias,
        prohibit_pads=False,
        prohibit_zones=False,
        prohibit_footprints=False,
    )


def _board(
    *,
    start: tuple[int, int] = (1_000, 5_000),
    end: tuple[int, int] = (9_000, 5_000),
    keepouts: tuple[Keepout, ...] = (),
) -> BoardIRSnapshot:
    net_class = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=100,
        track_width_nm=200,
        via_diameter_nm=600,
        via_drill_nm=300,
    )
    pads = tuple(
        Pad(
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
            layer_ids=(F_LAYER,),
        )
        for identifier, center in (("pad:01", start), ("pad:02", end))
    )
    content = make_content(
        source=SourceInfo(
            format="test",
            revision=SOURCE_REVISION,
            format_version="1",
            generator="layered-board-benchmark",
        ),
        outline=(OutlineContour(id="contour:main", outer=_rectangle(0, 0, 10_000, 10_000)),),
        copper_layers=(
            Layer(id=F_LAYER, name="F.Cu", index=0),
            Layer(id=B_LAYER, name="B.Cu", index=1),
        ),
        nets=(Net(id=NET_ID, name="AUDIO"),),
        constraints=ConstraintSet(
            net_classes=(net_class,),
            assignments=(NetClassAssignment(net_id=NET_ID, net_class_id=net_class.id),),
        ),
        footprints=(
            Footprint(
                id="footprint:routing-fixture",
                origin=PointNM(*start),
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=("pad:01", "pad:02"),
            ),
        ),
        pads=pads,
        keepouts=keepouts,
    )
    return make_snapshot(content)


def _request(snapshot: BoardIRSnapshot, **changes: Any) -> LayeredRouteRequest:
    values: dict[str, Any] = {
        "board_revision": snapshot.snapshot_digest,
        "net_id": NET_ID,
        "start_pad_id": "pad:01",
        "end_pad_id": "pad:02",
        "start_layer_id": F_LAYER,
        "end_layer_id": F_LAYER,
        "grid_step_nm": 1_000,
        "settings": LayeredAStarSettings(via_cost=2),
    }
    values.update(changes)
    return LayeredRouteRequest(**values)


def _case_inputs() -> tuple[tuple[str, BoardIRSnapshot, LayeredRouteRequest], ...]:
    front_wall = _keepout(
        "keepout:front-wall",
        (F_LAYER,),
        (4_000, 0, 6_000, 10_000),
        tracks=True,
        vias=False,
    )
    both_walls = (
        front_wall,
        _keepout(
            "keepout:back-wall",
            (B_LAYER,),
            (4_000, 0, 6_000, 10_000),
            tracks=True,
            vias=False,
        ),
    )
    no_vias = _keepout(
        "keepout:no-vias",
        (F_LAYER, B_LAYER),
        (0, 0, 10_000, 10_000),
        tracks=False,
        vias=True,
    )
    same = _board()
    via = _board(keepouts=(front_wall,))
    blocked = _board(keepouts=both_walls)
    via_blocked = _board(keepouts=(front_wall, no_vias))
    off_grid = _board(end=(9_500, 5_000))
    return (
        ("same_layer", same, _request(same)),
        ("via_required", via, _request(via)),
        ("both_layers_blocked", blocked, _request(blocked)),
        ("via_keepout_blocked", via_blocked, _request(via_blocked)),
        ("stale_revision", same, _request(same, board_revision=OTHER_REVISION)),
        ("off_grid", off_grid, _request(off_grid)),
    )


def _run(repetitions: int) -> dict[str, Any]:
    router = LayeredBoardRouter()
    cases: list[dict[str, Any]] = []
    deterministic_replays = 0
    source_unchanged = True
    for name, snapshot, request in _case_inputs():
        before = snapshot
        first = router.propose(snapshot, request)
        for _ in range(repetitions):
            replay = router.propose(snapshot, request)
            if replay != first:
                raise BenchmarkError(f"non-deterministic replay for {name}")
            deterministic_replays += 1
        source_unchanged = source_unchanged and snapshot == before
        if first.ok:
            if first.candidate is None or first.diagnostic is not None:
                raise BenchmarkError(f"invalid success union for {name}")
            verify_layered_candidate_id(first.candidate)
            case = {
                "name": name,
                "candidate_id": first.candidate.candidate_id,
                "ok": True,
                "path_count": len(first.candidate.patch.paths),
                "via_count": len(first.candidate.patch.vias),
                "wire_length_nm": first.candidate.patch.wire_length_nm,
                "expanded_states": first.candidate.metrics.expanded_states,
                "obstacle_checks": first.candidate.metrics.obstacle_checks,
                "diagnostic": None,
            }
        else:
            if first.diagnostic is None or first.candidate is not None:
                raise BenchmarkError(f"invalid failure union for {name}")
            case = {
                "name": name,
                "candidate_id": None,
                "ok": False,
                "path_count": 0,
                "via_count": 0,
                "wire_length_nm": 0,
                "expanded_states": first.diagnostic.expanded_states,
                "obstacle_checks": first.diagnostic.obstacle_checks,
                "diagnostic": first.diagnostic.code.value,
            }
        cases.append(case)

    valid_snapshot, valid_request = _case_inputs()[0][1:]
    candidate_result = router.propose(valid_snapshot, valid_request)
    if not candidate_result.ok or candidate_result.candidate is None:
        raise BenchmarkError("valid candidate disappeared before tamper test")
    tampered = replace(
        candidate_result.candidate,
        patch=replace(candidate_result.candidate.patch, width_nm=201),
    )
    try:
        verify_layered_candidate_id(tampered)
    except ValueError:
        tamper_rejected = True
    else:
        tamper_rejected = False
    if not tamper_rejected:
        raise BenchmarkError("candidate tamper was not rejected")

    expected = {
        "same_layer": None,
        "via_required": None,
        "both_layers_blocked": LayeredRouteFailureCode.NO_PATH.value,
        "via_keepout_blocked": LayeredRouteFailureCode.NO_PATH.value,
        "stale_revision": LayeredRouteFailureCode.STALE_REVISION.value,
        "off_grid": LayeredRouteFailureCode.OFF_GRID.value,
    }
    for case in cases:
        case_name = case["name"]
        if not isinstance(case_name, str) or case["diagnostic"] != expected[case_name]:
            raise BenchmarkError(f"unexpected diagnostic for {case['name']}")
    via_case = next(case for case in cases if case["name"] == "via_required")
    if via_case["via_count"] != 2:
        raise BenchmarkError("via-required fixture did not emit two through-vias")

    return {
        "benchmark": "layered-board-ir-adapter-v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "dataset": {
            "fixture_set": "synthetic-two-layer-board-ir-v1",
            "license": "not_applicable_independently_authored",
            "external_content": False,
        },
        "configuration": {
            "script_sha256": hashlib.sha256((ROOT / SCRIPT_PATH).read_bytes()).hexdigest(),
            "repetitions_per_case": repetitions,
            "case_count": len(cases),
            "router": "board-layered-a-star-v1",
            "kiCad_invoked": False,
        },
        "metrics": {
            "case_results": cases,
            "deterministic_replays": deterministic_replays,
            "source_unchanged": source_unchanged,
            "tamper_rejected": tamper_rejected,
            "via_required_success": via_case["ok"] and via_case["via_count"] == 2,
        },
        "not_claimed": [
            "source-preserving KiCad serialization",
            "KiCad DRC or refill authority",
            "whole-board completion",
            "multi-net congestion, rip-up, or FreeRouting parity",
            "electrical, SI/PI, thermal, DFM, or fabrication readiness",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.repetitions <= 50:
        raise SystemExit("repetitions must be between 2 and 50")
    document = _run(args.repetitions)
    document = {
        "run_id": "sha256:" + hashlib.sha256(_canonical_bytes(document)).hexdigest(),
        **document,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical_bytes(document) + b"\n")
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
