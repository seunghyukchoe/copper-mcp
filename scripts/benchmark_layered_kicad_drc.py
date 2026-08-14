#!/usr/bin/env python3
"""Measure authoritative KiCad DRC evidence for the layered proposal.

Each repetition uses a fresh private workspace copy of the independently authored blocked-pad
fixture. The service itself writes only its private temporary DRC snapshot; the fixture copy is
checked for byte, inode, and mtime preservation. No candidate file is published to the repository
or caller workspace.
"""

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

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.benchmarks.drc_comparability import (
    LITERAL_KEY,
    comparability_of,
    require_qualified,
)
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import discover_kicad_cli, run_layered_route_candidate_drc
from copper_mcp.routing import (
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteCandidate,
    LayeredRouteRequest,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
SCRIPT_PATH = Path("scripts/benchmark_layered_kicad_drc.py")
F_LAYER = "layer:F.Cu"


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


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


def _workspace_entries(root: Path) -> tuple[str, ...]:
    return tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob("*")))


def _candidate() -> tuple[
    bytes,
    KiCadConstraintProfile,
    LayeredRouteRequest,
    LayeredRouteCandidate,
]:
    source = FIXTURE.read_bytes()
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    if conversion.snapshot is None or conversion.diagnostics:
        raise RuntimeError("fixture does not convert cleanly")
    snapshot = conversion.snapshot
    pads = tuple(
        pad for pad in snapshot.content.pads if pad.net_id == snapshot.content.pads[0].net_id
    )
    if len(pads) != 2 or not isinstance(pads[0].net_id, str):
        raise RuntimeError("fixture does not contain exactly two same-net pads")
    request = LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=pads[0].net_id,
        start_pad_id=pads[0].id,
        end_pad_id=pads[1].id,
        start_layer_id=F_LAYER,
        end_layer_id=F_LAYER,
        grid_step_nm=1_000,
        settings=LayeredAStarSettings(via_cost=2),
    )
    proposed = LayeredBoardRouter().propose(snapshot, request)
    if proposed.candidate is None or proposed.diagnostic is not None:
        raise RuntimeError("fixture did not produce a layered candidate")
    return source, profile, request, proposed.candidate


def _run(repetitions: int, kicad_cli: Path) -> dict[str, Any]:
    _source, profile, request, candidate = _candidate()
    evidence: list[dict[str, Any]] = []
    preserved: list[bool] = []
    workspace_clean: list[bool] = []
    for _ in range(repetitions):
        with tempfile.TemporaryDirectory(prefix="copper-mcp-layered-benchmark-") as directory:
            workspace = Path(directory)
            board = workspace / FIXTURE.name
            shutil.copy2(FIXTURE, board)
            before_bytes = board.read_bytes()
            before_stat = board.stat()
            before_entries = _workspace_entries(workspace)
            result = run_layered_route_candidate_drc(
                board.name,
                candidate,
                profile,
                Settings(workspace=workspace, kicad_cli=kicad_cli),
                request=request,
            )
            after_stat = board.stat()
            preserved.append(
                board.read_bytes() == before_bytes
                and after_stat.st_ino == before_stat.st_ino
                and after_stat.st_mtime_ns == before_stat.st_mtime_ns
            )
            workspace_clean.append(_workspace_entries(workspace) == before_entries)
            evidence.append(result.to_dict())
    if not all(item["summary"]["passed"] for item in evidence):
        raise RuntimeError("layered candidate DRC did not pass")
    if len({json.dumps(item, sort_keys=True) for item in evidence}) != 1:
        raise RuntimeError("layered candidate DRC evidence is not deterministic")
    return {
        "repetitions": repetitions,
        # Every repetition copies the same fixture into a fresh workspace at one commit, so the
        # `repeated_agreement` precondition holds by construction and the literal reports what
        # the repetitions actually found (ADR-0109). The identity check above already refuses a
        # disagreement outright, so `repeated_disagreement` is unreachable *here* -- but the
        # literal is derived rather than hardcoded, because a runner that stops refusing would
        # otherwise keep publishing the strong claim.
        LITERAL_KEY: comparability_of(
            [
                {
                    name: item["summary"][name]
                    for name in (
                        "error_count",
                        "warning_count",
                        "unconnected_count",
                        "ignored_check_count",
                        "passed",
                    )
                }
                for item in evidence
            ]
        ),
        "deterministic_evidence": True,
        "passed_runs": sum(item["summary"]["passed"] for item in evidence),
        "error_count": evidence[0]["summary"]["error_count"],
        "warning_count": evidence[0]["summary"]["warning_count"],
        "unconnected_count": evidence[0]["summary"]["unconnected_count"],
        "ignored_check_count": evidence[0]["summary"]["ignored_check_count"],
        "kicad_version": evidence[0]["summary"]["kicad_version"],
        "candidate_id": candidate.candidate_id,
        "candidate_base_revision": candidate.base_revision,
        "patched_board_revision": evidence[0]["patched_board_revision"],
        "patched_drc_context_revision": evidence[0]["patched_drc_context_revision"],
        "source_unchanged": all(preserved),
        "workspace_unchanged": all(workspace_clean),
        "kicad_invoked": True,
        "drc": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--kicad-cli", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 50:
        raise SystemExit("--repetitions must be between 1 and 50")
    settings = Settings(workspace=ROOT)
    kicad_cli = args.kicad_cli or discover_kicad_cli(settings)
    metrics = _run(args.repetitions, kicad_cli)
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/layered-kicad-drc/v1",
        "date_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "kicad_cli": str(kicad_cli),
        },
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "script": str(SCRIPT_PATH),
        "metrics": metrics,
    }
    require_qualified(payload, where="copper-mcp/benchmark/layered-kicad-drc/v1")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    payload["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
