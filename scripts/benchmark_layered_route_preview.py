#!/usr/bin/env python3
"""Measure the public, file-backed layered route preview contract.

The benchmark uses the independently authored two-layer KiCad fixture with a front-layer blocker.
It exercises the same service used by MCP, validates the closed structured-output union, and
records source immutability plus stale-CAS refusals.  It does not write a board, invoke KiCad DRC,
or claim general multilayer/FreeRouting parity.
"""

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

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.layered_route_preview import preview_layered_route
from copper_mcp.mcp_contracts import LayeredRoutePreviewToolResponse

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
SCRIPT_PATH = Path("scripts/benchmark_layered_route_preview.py")


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


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:request",
        name="Request",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _request(
    board: Path, source: bytes, snapshot_digest: str, start: str, end: str
) -> dict[str, Any]:
    return {
        "board": board.name,
        "start_pad_id": start,
        "end_pad_id": end,
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
        "expect_board_revision": "sha256:" + hashlib.sha256(source).hexdigest(),
        "expect_snapshot_digest": snapshot_digest,
    }


def _run(repetitions: int) -> dict[str, Any]:
    import tempfile

    source = FIXTURE.read_bytes()
    conversion = parse_kicad_bytes(source, _profile())
    if conversion.snapshot is None:
        raise RuntimeError("fixture did not convert to Board IR")
    pads = tuple(pad for pad in conversion.snapshot.content.pads if pad.net_id)
    if len(pads) < 2:
        raise RuntimeError("fixture lacks two same-net pads")
    same_net = tuple(pad for pad in pads if pad.net_id == pads[0].net_id)
    if len(same_net) != 2:
        raise RuntimeError("fixture does not have exactly two selected-net pads")

    with tempfile.TemporaryDirectory(prefix="copper-mcp-layered-preview-") as directory:
        board = Path(directory) / FIXTURE.name
        board.write_bytes(source)
        settings = Settings(workspace=board.parent)
        request = _request(
            board,
            source,
            conversion.snapshot.snapshot_digest,
            same_net[0].id,
            same_net[1].id,
        )
        before = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
        responses = [
            LayeredRoutePreviewToolResponse.model_validate(
                preview_layered_route(request, settings)
            ).root
            for _ in range(repetitions)
        ]
        if any(response.status != "routed" for response in responses):
            raise RuntimeError("public preview did not route the via-required fixture")
        candidate_ids = [response.candidate.candidate_id for response in responses]
        if len(set(candidate_ids)) != 1:
            raise RuntimeError("public layered preview was not deterministic")
        vias = len(responses[0].candidate.patch.vias)

        stale_board = dict(request)
        stale_board["expect_board_revision"] = "sha256:" + "0" * 64
        stale_result = LayeredRoutePreviewToolResponse.model_validate(
            preview_layered_route(stale_board, settings)
        ).root
        if stale_result.status != "not_routed" or stale_result.diagnostic.code != "stale_revision":
            raise RuntimeError("stale board CAS was not refused")

        stale_snapshot = dict(request)
        stale_snapshot["expect_snapshot_digest"] = "sha256:" + "0" * 64
        snapshot_result = LayeredRoutePreviewToolResponse.model_validate(
            preview_layered_route(stale_snapshot, settings)
        ).root
        if (
            snapshot_result.status != "not_routed"
            or snapshot_result.diagnostic.code != "stale_revision"
        ):
            raise RuntimeError("stale snapshot CAS was not refused")

        after = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
        if before != after:
            raise RuntimeError("preview mutated the workspace board")
        return {
            "repetitions": repetitions,
            "deterministic_candidate_ids": True,
            "candidate_id": candidate_ids[0],
            "via_count": vias,
            "schema_valid_replays": repetitions,
            "stale_board_refused": True,
            "stale_snapshot_refused": True,
            "source_unchanged": True,
            "kicad_invoked": False,
            "drc_performed": False,
            "apply_authority": False,
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
        "schema": "copper-mcp/benchmark/layered-route-preview/v1",
        "date_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture": str(FIXTURE.relative_to(ROOT)),
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
