#!/usr/bin/env python3
"""Benchmark the bounded layered-candidate topology gate without exposing board geometry."""

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

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.routing import (
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteRequest,
    canonical_layered_candidate_bytes,
    verify_layered_candidate,
)
from copper_mcp.routing.layered_candidate_verifier import (
    LayeredCandidateVerificationCode,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
SCRIPT_PATH = Path(__file__).relative_to(ROOT)


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
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _restamp(candidate: Any, *, patch: Any) -> Any:
    via_count = len(patch.vias)
    wire_length = patch.wire_length_nm
    changed = replace(
        candidate,
        patch=patch,
        cost=replace(
            candidate.cost,
            wire_length_nm=wire_length,
            via_count=via_count,
            via_cost_units=via_count * candidate.settings.via_cost,
        ),
        metrics=replace(
            candidate.metrics,
            wire_length_nm=wire_length,
            vias=via_count,
            bend_count=patch.bend_count,
        ),
    )
    provisional = replace(changed, candidate_id=f"sha256:{'0' * 64}")
    digest = f"sha256:{hashlib.sha256(canonical_layered_candidate_bytes(provisional)).hexdigest()}"
    return replace(provisional, candidate_id=digest)


def _run(repetitions: int) -> dict[str, Any]:
    conversion = parse_kicad_bytes(FIXTURE.read_bytes(), _profile())
    if conversion.snapshot is None or conversion.diagnostics:
        raise RuntimeError("fixture did not parse into Board IR")
    snapshot = conversion.snapshot
    pads = tuple(pad for pad in snapshot.content.pads if pad.net_id is not None)
    if len(pads) < 2 or pads[0].net_id != pads[1].net_id:
        raise RuntimeError("fixture does not contain two same-net endpoints")
    request = LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=pads[0].net_id,
        start_pad_id=pads[0].id,
        end_pad_id=pads[1].id,
        start_layer_id="layer:F.Cu",
        end_layer_id="layer:F.Cu",
        grid_step_nm=1_000,
        settings=LayeredAStarSettings(via_cost=2),
    )
    router = LayeredBoardRouter()
    first = router.propose(snapshot, request)
    if first.candidate is None:
        raise RuntimeError("fixture route did not produce a candidate")
    candidate = first.candidate

    successes = 0
    candidate_ids: set[str] = set()
    for _ in range(repetitions):
        result = verify_layered_candidate(
            candidate,
            snapshot,
            expected_board_revision=snapshot.snapshot_digest,
            expected_start_pad_id=request.start_pad_id,
            expected_end_pad_id=request.end_pad_id,
        )
        if not result.ok:
            raise RuntimeError(f"valid candidate refused: {result.diagnostic.code.value}")
        successes += 1
        candidate_ids.add(candidate.candidate_id)

    via = candidate.patch.vias[0]
    disconnected_path = replace(
        candidate.patch.paths[1],
        vertices=(
            type(via.center)(via.center.x + 1, via.center.y),
            candidate.patch.paths[1].vertices[-1],
        ),
    )
    disconnected = _restamp(
        candidate,
        patch=replace(
            candidate.patch,
            paths=(candidate.patch.paths[0], disconnected_path, *candidate.patch.paths[2:]),
        ),
    )
    disconnected_result = verify_layered_candidate(disconnected, snapshot)

    endpoint = _restamp(
        candidate,
        patch=replace(
            candidate.patch,
            vias=(replace(candidate.patch.vias[-1], center=pads[1].center),),
        )
        if len(candidate.patch.vias) == 1
        else replace(
            candidate.patch,
            vias=(
                *candidate.patch.vias[:-1],
                replace(candidate.patch.vias[-1], center=pads[1].center),
            ),
        ),
    )
    endpoint_result = verify_layered_candidate(endpoint, snapshot)
    stale_result = verify_layered_candidate(
        candidate, snapshot, expected_board_revision=f"sha256:{'0' * 64}"
    )
    return {
        "repetitions": repetitions,
        "verified_replays": successes,
        "deterministic_candidate_ids": len(candidate_ids) == 1,
        "path_count": len(candidate.patch.paths),
        "via_count": len(candidate.patch.vias),
        "endpoint_via_refused": endpoint_result.diagnostic.code
        is LayeredCandidateVerificationCode.UNSUPPORTED_ENDPOINT_VIA,
        "disconnected_geometry_refused": disconnected_result.diagnostic.code
        is LayeredCandidateVerificationCode.VIA_DISCONTINUITY,
        "stale_revision_refused": stale_result.diagnostic.code
        is LayeredCandidateVerificationCode.STALE_REVISION,
        "physical_validation": "not_modelled",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.repetitions <= 100:
        raise SystemExit("--repetitions must be between 2 and 100")
    metrics = _run(args.repetitions)
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/layered-candidate-verifier/v1",
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
