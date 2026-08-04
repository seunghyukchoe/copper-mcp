#!/usr/bin/env python3
"""Measure the public routed-preview contract for freshness-bound fill evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import copper_mcp.route_preview as route_preview_module
from copper_mcp.adapters import net_id_for_name
from copper_mcp.board_ir import PointNM
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import ZoneFillAuthority
from copper_mcp.mcp_contracts import RoutePreviewToolResponse
from copper_mcp.route_preview import preview_route
from copper_mcp.zone_fill import FillIsland

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path("scripts/benchmark_fill_preview_provenance.py")
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "blocked-zone.kicad_pcb"


def _request(board: str) -> dict[str, Any]:
    return {
        "board": board,
        "net": "AUDIO",
        "layer": "F.Cu",
        "seed": 23,
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
        "include_fill_authority": True,
    }


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
    with tempfile.TemporaryDirectory(prefix="copper-mcp-fill-preview-") as temporary_directory:
        workspace = Path(temporary_directory)
        board = workspace / FIXTURE.name
        board.write_bytes(FIXTURE.read_bytes())
        source_revision = "sha256:" + hashlib.sha256(board.read_bytes()).hexdigest()
        authority = ZoneFillAuthority(
            source_revision=source_revision,
            context_revision=f"sha256:{'a' * 64}",
            source_fill_digest=f"sha256:{'b' * 64}",
            refilled_fill_digest=f"sha256:{'b' * 64}",
            kicad_version="10.0.5",
            fill_polygon_count=1,
            fill_vertex_count=4,
        )
        island = FillIsland(
            net_id=net_id_for_name("POWER"),
            layer_id="layer:F.Cu",
            points=(
                PointNM(18_000_000, 11_000_000),
                PointNM(22_000_000, 11_000_000),
                PointNM(22_000_000, 14_000_000),
                PointNM(18_000_000, 14_000_000),
            ),
        )
        settings = Settings(workspace=workspace, max_drc_report_bytes=4096)
        original = route_preview_module.run_zone_fill_authority
        route_preview_module.run_zone_fill_authority = lambda *_: (authority, (island,))
        try:
            before = board.read_bytes()
            previews = [preview_route(_request(board.name), settings) for _ in range(repetitions)]
        finally:
            route_preview_module.run_zone_fill_authority = original

        documents = [
            RoutePreviewToolResponse.model_validate(preview.to_dict()) for preview in previews
        ]
        if any(document.root.status != "routed" for document in documents):
            raise RuntimeError("fill-aware preview did not produce routed outcomes")
        effects = [document.root.fill_authority for document in documents]
        if any(
            effect is None or effect.routing_effect != "foreign_zone_obstacles"
            for effect in effects
        ):
            raise RuntimeError("routed output did not expose foreign-zone provenance")
        candidates = [preview.candidate for preview in previews]
        if any(candidate is None for candidate in candidates):
            raise RuntimeError("routed preview omitted its candidate")
        candidate_ids = {
            candidate.candidate_id for candidate in candidates if candidate is not None
        }
        if len(candidate_ids) != 1:
            raise RuntimeError("fill-aware preview was not deterministic")
        if board.read_bytes() != before:
            raise RuntimeError("preview changed the workspace board")
        return {
            "repetitions": repetitions,
            "schema_valid": True,
            "routed_outcomes": repetitions,
            "foreign_zone_provenance_outcomes": repetitions,
            "deterministic_candidate_ids": len(candidate_ids) == 1,
            "workspace_unchanged": True,
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
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/fill-preview-provenance/v1",
        "date_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture": "blocked-zone-foreign-fill-contract-v1",
        "script": str(SCRIPT_PATH),
        "metrics": _run(args.repetitions),
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
