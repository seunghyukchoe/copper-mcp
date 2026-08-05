#!/usr/bin/env python3
"""Measure stationary padless-courtyard collision evidence without mutating a board."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.placement import build_placement_view, evaluate_placement, parse_placement_intent

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "tests/fixtures/board-ir-v0.2/padless-footprint.kicad_pcb"
OUTPUT = ROOT / "benchmarks/results/placement/2026-08-05-padless-courtyard.json"
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


def _intent(view, *, proposals: list[dict[str, object]] | None = None):
    return parse_placement_intent(
        {
            "board": BOARD.name,
            "constraints": CONSTRAINTS,
            "subjects": sorted(view.footprints),
            **({"proposals": proposals} if proposals is not None else {}),
        }
    )


def main() -> int:
    source = BOARD.read_bytes()
    profile = KiCadConstraintProfile(
        net_classes=(NetClass(id="class:request", name="Request", **CONSTRAINTS),),
        default_net_class_id="class:request",
    )
    parsed = parse_kicad_bytes(source, profile, ParseLimits())
    if parsed.snapshot is None:
        raise RuntimeError("padless courtyard fixture must parse")
    view = build_placement_view(source, parsed.snapshot)
    subject = sorted(view.footprints)[0]
    baseline = evaluate_placement(_intent(view), parsed.snapshot, view)
    collision = evaluate_placement(
        _intent(view, proposals=[{"subject": subject, "offset_x_nm": 30_000_000}]),
        parsed.snapshot,
        view,
    )
    legality = collision.diagnostic.legality if collision.diagnostic is not None else None
    payload = {
        "schema": "copper-mcp/benchmark/padless-courtyard/v1",
        "date_utc": "2026-08-05",
        "source_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - repository-local metadata
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "fixture": "tests/fixtures/board-ir-v0.2/padless-footprint.kicad_pcb",
        "metrics": {
            "baseline_status": baseline.status,
            "collision_status": collision.status,
            "stationary_padless_courtyards": len(view.stationary),
            "collision_courtyard_overlap": None if legality is None else legality.courtyard_overlap,
            "source_sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
            "workspace_mutations": 0,
        },
        "not_claimed": [
            "custom courtyard-clearance values",
            "non-rectangular courtyard topology",
            "KiCad DRC or fabrication readiness",
            "apply or FreeRouting parity",
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
