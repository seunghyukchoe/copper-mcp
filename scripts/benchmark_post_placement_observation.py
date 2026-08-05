#!/usr/bin/env python3
"""Measure deterministic read-only post-placement scene/DRC observation."""

from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.apply import ApplyTokenAuthority, apply_placement_candidate
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.config import Settings
from copper_mcp.placement import build_placement_view
from copper_mcp.placement_preview import preview_placement
from copper_mcp.post_placement_observation import observe_post_placement

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/placement-v0.1/placement-legal.kicad_pcb"
OUTPUT = ROOT / (
    "benchmarks/results/placement/2026-08-05-post-placement-observation-replay-b056.json"
)
KICAD = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


def _workspace_state(root: Path) -> dict[str, object]:
    """Return a digest of every visible workspace entry, not just board bytes."""

    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            stat = path.lstat()
            entries.append(
                {
                    "kind": "symlink",
                    "inode": stat.st_ino,
                    "mode": stat.st_mode & 0o7777,
                    "mtime_ns": stat.st_mtime_ns,
                    "path": relative,
                    "target": str(path.readlink()),
                }
            )
            continue
        if not path.is_file():
            continue
        content = path.read_bytes()
        stat = path.stat()
        entries.append(
            {
                "kind": "file",
                "inode": stat.st_ino,
                "mode": stat.st_mode & 0o7777,
                "mtime_ns": stat.st_mtime_ns,
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "digest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "entries": len(entries),
    }


def main() -> int:
    if not KICAD.is_file():
        raise RuntimeError("KiCad CLI is not installed at the reference path")
    source = FIXTURE.read_bytes()
    samples: list[int] = []
    bindings: set[tuple[str, str, str]] = set()
    summaries: set[tuple[bool, bool, int, int, int, int, int]] = set()
    with tempfile.TemporaryDirectory(prefix="copper-mcp-post-placement-") as directory:
        workspace = Path(directory)
        board = workspace / FIXTURE.name
        board.write_bytes(source)
        settings = Settings(workspace=workspace, kicad_cli=KICAD, allow_apply=True)
        net_class = NetClass(id="class:request", name="Request", **CONSTRAINTS)
        profile = KiCadConstraintProfile(
            net_classes=(net_class,), default_net_class_id=net_class.id
        )
        parsed = parse_kicad_bytes(source, profile, ParseLimits())
        assert parsed.snapshot is not None
        subject = sorted(build_placement_view(source, parsed.snapshot).footprints)[0]
        authority = ApplyTokenAuthority()
        preview = preview_placement(
            {
                "board": board.name,
                "constraints": CONSTRAINTS,
                "subjects": [subject],
                "proposals": [{"subject": subject, "offset_x_nm": 1_000_000}],
                "include_apply_token": True,
            },
            settings,
            authority,
        )
        assert preview.candidate is not None and preview.apply_token is not None
        applied = apply_placement_candidate(
            {
                "board": board.name,
                "candidate": preview.candidate.to_dict(),
                "apply_token": preview.apply_token,
                "expect_board_revision": preview.board_revision,
                "constraints": CONSTRAINTS,
            },
            settings,
            authority,
        )
        assert applied.status == "applied" and applied.board_revision_after is not None
        before = board.read_bytes()
        request = {
            "board": board.name,
            "expect_board_revision": applied.board_revision_after,
            "constraints": CONSTRAINTS,
            "region": {
                "min_x_nm": -10_000_000,
                "min_y_nm": -10_000_000,
                "max_x_nm": 100_000_000,
                "max_y_nm": 100_000_000,
            },
        }
        workspace_before = _workspace_state(workspace)
        for _ in range(3):
            started = time.perf_counter_ns()
            result = observe_post_placement(request, settings)
            samples.append(time.perf_counter_ns() - started)
            bindings.add(
                (
                    result.board_revision,
                    result.snapshot_digest,
                    result.drc_summary.drc_context_revision,
                )
            )
            summary = result.drc_summary
            summaries.add(
                (
                    summary.passed,
                    summary.clean,
                    summary.error_count,
                    summary.warning_count,
                    summary.exclusion_count,
                    summary.ignored_check_count,
                    summary.unconnected_count,
                )
            )
        preserved = board.read_bytes() == before
        workspace_after = _workspace_state(workspace)
    payload = {
        "schema": "copper-mcp/benchmark/post-placement-observation/v1",
        "date_utc": "2026-08-05",
        "source_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - repository-local benchmark metadata
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "environment": {"python": __import__("sys").version.split()[0], "kicad_cli": str(KICAD)},
        "fixture": "placement-v0.1/placement-legal.kicad_pcb",
        "metrics": {
            "repetitions": 3,
            "same_revision_scene_drc_binding": len(bindings) == 1,
            "binding_signatures": len(bindings),
            "post_apply_board_bytes_preserved": preserved,
            "workspace_mutations": workspace_before != workspace_after,
            "workspace_state_before": workspace_before,
            "workspace_state_after": workspace_after,
            "median_observation_ns": statistics.median(samples),
            "drc_summary_signatures": len(summaries),
            "drc_summary": next(iter(summaries)),
        },
        "not_claimed": [
            "apply provenance beyond the returned revision",
            "live editor CAS",
            "ERC/electrical/fabrication signoff",
            "FreeRouting parity",
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
