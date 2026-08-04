#!/usr/bin/env python3
"""Replay the bounded, separately authorized placement-apply contract.

The benchmark uses a committed KiCad fixture, mints an explicit placement-scoped capability,
applies one legal front-side pose, and reparses the result. It also proves that a route-scoped
capability cannot cross the operation boundary. This is file-level CAS/backup evidence only; it
does not claim general placement fidelity, KiCad DRC, live IPC mutation, or fabrication readiness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.apply import (
    ApplyBinding,
    ApplyTokenAuthority,
    apply_placement_candidate,
)
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.config import Settings
from copper_mcp.placement import build_placement_view
from copper_mcp.placement_preview import preview_placement

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/placement-v0.1/placement-legal.kicad_pcb"
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


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
    net_class = NetClass(id="class:benchmark", name="Benchmark", **CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _preview(workspace: Path, authority: ApplyTokenAuthority):
    board = workspace / FIXTURE.name
    source = FIXTURE.read_bytes()
    board.write_bytes(source)
    conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
    if conversion.snapshot is None or conversion.diagnostics:
        raise RuntimeError("benchmark fixture is outside the supported Board IR subset")
    refs = sorted(build_placement_view(source, conversion.snapshot).footprints)
    settings = Settings(workspace=workspace.resolve(), allow_apply=True)
    preview = preview_placement(
        {
            "board": board.name,
            "constraints": CONSTRAINTS,
            "subjects": refs,
            "proposals": [{"subject": refs[0], "offset_x_nm": 1_000_000}],
            "include_apply_token": True,
        },
        settings,
        authority,
    )
    if preview.candidate is None or preview.apply_token is None:
        raise RuntimeError("authorized placement preview did not mint a capability")
    request = {
        "board": board.name,
        "candidate": preview.candidate.to_dict(),
        "apply_token": preview.apply_token,
        "expect_board_revision": preview.board_revision,
        "constraints": CONSTRAINTS,
    }
    return board, source, settings, preview, request


def _run() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="copper-mcp-placement-bench-") as directory:
        workspace = Path(directory)
        authority = ApplyTokenAuthority()
        board, source, settings, preview, request = _preview(workspace, authority)
        before_files = {
            path.relative_to(workspace).as_posix()
            for path in workspace.rglob("*")
            if path.is_file()
        }
        applied = apply_placement_candidate(request, settings, authority)
        after_files = {
            path.relative_to(workspace).as_posix()
            for path in workspace.rglob("*")
            if path.is_file()
        }
        if applied.status != "applied" or applied.backup_path is None:
            raise RuntimeError(f"placement apply did not complete: {applied.status}")
        backup = workspace / applied.backup_path
        result_bytes = board.read_bytes()
        parsed = parse_kicad_bytes(result_bytes, _profile(), ParseLimits())
        backup_exact = backup.read_bytes() == source
        result_reparsed_clean = parsed.snapshot is not None and parsed.diagnostics == ()
        result_revision_matches = (
            parsed.snapshot is not None
            and parsed.snapshot.content.source.revision == applied.board_revision_after
        )

        route_domain_refused = False
        with tempfile.TemporaryDirectory(prefix="copper-mcp-placement-domain-") as route_directory:
            route_workspace = Path(route_directory)
            route_authority = ApplyTokenAuthority()
            route_board, _, route_settings, route_preview, route_request = _preview(
                route_workspace, route_authority
            )
            assert route_preview.candidate is not None
            route_token = route_authority.issue(
                ApplyBinding(
                    candidate_id=route_preview.candidate.candidate_id,
                    base_revision=route_preview.candidate.base_revision,
                    board_revision=route_preview.board_revision,
                    relative_path=route_board.name,
                    operation="route",
                )
            )
            route_request["apply_token"] = route_token
            refused = apply_placement_candidate(route_request, route_settings, route_authority)
            route_domain_refused = (
                refused.status == "refused"
                and refused.diagnostic is not None
                and refused.diagnostic.code == "invalid_token"
            )

        return {
            "status": applied.status,
            "footprints_moved": applied.footprints_moved,
            "bytes_changed": applied.bytes_changed,
            "backup_exact": backup_exact,
            "result_reparsed_clean": result_reparsed_clean,
            "result_revision_matches": result_revision_matches,
            "explicit_token_requested": True,
            "placement_token_issued": preview.apply_token is not None,
            "route_token_cross_domain_refused": route_domain_refused,
            "workspace_files_before": len(before_files),
            "workspace_files_after": len(after_files),
            "workspace_mutations": len(after_files - before_files)
            + int(board.read_bytes() != source),
            "kicad_invoked": False,
            "live_ipc_invoked": False,
            "general_placement_claim": False,
            "post_apply_drc_claim": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "benchmarks/results/placement/2026-08-05-placement-apply.json",
    )
    args = parser.parse_args()
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/placement-apply/v1",
        "date_utc": "2026-08-05",
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "metrics": _run(),
        "not_claimed": [
            "general KiCad footprint geometry or side flipping",
            "post-placement KiCad DRC or connectivity",
            "live IPC mutation or undo integration",
            "fabrication, electrical, or FreeRouting parity",
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
