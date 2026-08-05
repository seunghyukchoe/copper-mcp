#!/usr/bin/env python3
"""Measure the physical-clearance acceptance delta for a lattice-clean candidate set."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from copper_mcp.board_ir import (
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
    make_content,
    make_snapshot,
)
from copper_mcp.routing import (
    AStarSettings,
    CongestionLedger,
    RouteCandidate,
    RouteCost,
    RouteMetrics,
    RoutePatch,
    RoutePath,
    canonical_candidate_bytes,
)
from copper_mcp.routing.physical_clearance import verify_negotiated_physical_clearance

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).relative_to(ROOT)
OUTPUT = ROOT / "benchmarks/results/routing/2026-08-05-negotiated-physical-clearance.json"
SOURCE_COMMIT = "62a267e01c4cdbcafb0985d876bb7b54e7b9691b"
SOURCE_REVISION = f"sha256:{'c' * 64}"
LAYER = "layer:F.Cu"
HORIZONTAL = "net:horizontal"
VERTICAL = "net:vertical"


def _settings() -> AStarSettings:
    return AStarSettings(
        grid_step_nm=100_000,
        bend_penalty_nm=0,
        proximity_penalty_nm=0,
        max_grid_nodes=256,
        max_expansions=100,
        max_obstacles=16,
        max_obstacle_checks=100,
    )


def _snapshot() -> Any:
    def pad(identifier: str, net_id: str, y_nm: int) -> Pad:
        return Pad(
            id=identifier,
            net_id=net_id,
            center=PointNM(1_000_000 if identifier.endswith("1") else 9_000_000, y_nm),
            rotation_udeg=0,
            shape=PadShape.RECT,
            kind=PadKind.SMD,
            size_x_nm=100_000,
            size_y_nm=100_000,
            roundrect_radius_nm=None,
            drill_x_nm=None,
            drill_y_nm=None,
            layer_ids=(LAYER,),
        )

    pads = (
        pad("pad:h1", HORIZONTAL, 3_000_000),
        pad("pad:h2", HORIZONTAL, 3_000_000),
        pad("pad:v1", VERTICAL, 3_900_000),
        pad("pad:v2", VERTICAL, 3_900_000),
    )
    low = NetClass(
        id="class:low",
        name="Low",
        clearance_nm=400_000,
        track_width_nm=600_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    high = NetClass(
        id="class:high",
        name="High",
        clearance_nm=500_000,
        track_width_nm=600_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    return make_snapshot(
        make_content(
            source=SourceInfo(
                format="benchmark",
                revision=SOURCE_REVISION,
                format_version="1",
                generator="negotiated-physical-clearance",
            ),
            outline=(
                OutlineContour(
                    id="contour:board",
                    outer=Ring(
                        (
                            PointNM(0, 0),
                            PointNM(12_000_000, 0),
                            PointNM(12_000_000, 8_000_000),
                            PointNM(0, 8_000_000),
                        )
                    ),
                ),
            ),
            copper_layers=(Layer(id=LAYER, name="F.Cu", index=0, kind="signal"),),
            nets=(Net(id=HORIZONTAL, name="HORIZONTAL"), Net(id=VERTICAL, name="VERTICAL")),
            constraints=ConstraintSet(
                net_classes=(low, high),
                assignments=(
                    NetClassAssignment(net_id=HORIZONTAL, net_class_id=low.id),
                    NetClassAssignment(net_id=VERTICAL, net_class_id=high.id),
                ),
            ),
            footprints=(
                Footprint(
                    id="footprint:h",
                    origin=pads[0].center,
                    rotation_udeg=0,
                    side=FootprintSide.FRONT,
                    pad_ids=(pads[0].id, pads[1].id),
                ),
                Footprint(
                    id="footprint:v",
                    origin=pads[2].center,
                    rotation_udeg=0,
                    side=FootprintSide.FRONT,
                    pad_ids=(pads[2].id, pads[3].id),
                ),
            ),
            pads=pads,
        )
    )


def _candidate(snapshot: Any, net_id: str, y_nm: int) -> RouteCandidate:
    patch = RoutePatch(
        net_id=net_id,
        layer_id=LAYER,
        width_nm=600_000,
        paths=(RoutePath((PointNM(1_000_000, y_nm), PointNM(9_000_000, y_nm))),),
    )
    candidate = RouteCandidate(
        candidate_id=f"sha256:{'0' * 64}",
        base_revision=snapshot.snapshot_digest,
        start_pad_id="pad:h1" if net_id == HORIZONTAL else "pad:v1",
        end_pad_id="pad:h2" if net_id == HORIZONTAL else "pad:v2",
        patch=patch,
        cost=RouteCost(
            length_nm=patch.length_nm,
            bend_count=0,
            bend_cost_nm=0,
            proximity_steps=0,
            proximity_cost_nm=0,
            via_cost_nm=0,
            total_cost_nm=patch.length_nm,
        ),
        metrics=RouteMetrics(
            hard_internal_violations=0,
            unrouted_connections=0,
            vias=0,
            wire_length_nm=patch.length_nm,
            expanded_states=0,
            peak_frontier_states=1,
            obstacle_checks=0,
        ),
        settings=_settings(),
        router_version="benchmark",
        policy="benchmark",
        seed=1 if net_id == HORIZONTAL else 2,
    )
    return replace(
        candidate,
        candidate_id=("sha256:" + hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()),
    )


def _run(replays: int) -> dict[str, Any]:
    snapshot = _snapshot()
    candidates = (
        _candidate(snapshot, HORIZONTAL, 3_000_000),
        _candidate(snapshot, VERTICAL, 3_900_000),
    )
    ledger = CongestionLedger(
        grid_step_nm=_settings().grid_step_nm,
        present_penalty_nm=0,
        history_penalty_nm=0,
    )
    for candidate in candidates:
        ledger.add_candidate(candidate)
    overflow_units = sum(item.usage - 1 for item in ledger.overflow_resources())

    outcomes = [
        verify_negotiated_physical_clearance(
            snapshot,
            candidates,
            layer_id=LAYER,
            max_pair_checks=1,
        )
        for _ in range(replays)
    ]
    first = outcomes[0]
    summary = {
        "accepted": first.accepted,
        "diagnostic": first.diagnostic,
        "failure": None if first.failure is None else first.failure.value,
        "pair_checks": first.pair_checks,
    }
    if any(
        {
            "accepted": item.accepted,
            "diagnostic": item.diagnostic,
            "failure": None if item.failure is None else item.failure.value,
            "pair_checks": item.pair_checks,
        }
        != summary
        for item in outcomes[1:]
    ):
        raise RuntimeError("physical-clearance benchmark replay was not deterministic")
    if overflow_units != 0 or first.accepted:
        raise RuntimeError("benchmark no longer demonstrates the intended acceptance delta")
    return {
        "available_clearance_nm": 300_000,
        "governing_clearance_nm": 500_000,
        "legacy_lattice_overflow_units": overflow_units,
        "legacy_would_accept_with_lattice_only": overflow_units == 0,
        "physical_gate": summary,
        "replays": replays,
        "replay_deterministic": True,
        "snapshot_digest": snapshot.snapshot_digest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replays", type=int, default=3)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if not 3 <= args.replays <= 20:
        raise SystemExit("--replays must be between 3 and 20")
    payload: dict[str, Any] = {
        "benchmark": "negotiated-physical-clearance-v2",
        "environment": "deterministic CPU-only; no external EDA tool invoked",
        "metrics": _run(args.replays),
        "non_claims": [
            "KiCad DRC or KiCad geometry parity",
            "multilayer, pads, vias, arcs, zones, or board-wide clearance",
            "fabrication clearance or FreeRouting parity",
        ],
        "replays": args.replays,
        "schema": "copper-mcp/benchmark/negotiated-physical-clearance/v2",
        "script": SCRIPT_PATH.as_posix(),
        "script_sha256": hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest(),
        "source_commit": SOURCE_COMMIT,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    payload["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
