#!/usr/bin/env python3
"""Measure a candidate becoming new copper in a disposable audio-board derivative.

The fixture is CopperMCP-authored, Apache-2.0 synthetic low-voltage RC test data.  This
benchmark uses the public non-mutating route-preview service, then verifies that its exact
candidate serializes into one additional KiCad segment in an in-memory derivative.  It does
not write or apply the source board, invoke KiCad, or establish real-board coverage.
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

from copper_mcp.adapters import parse_kicad_bytes, render_kicad_candidate_board
from copper_mcp.config import Settings
from copper_mcp.route_preview import RoutePreviewStatus, preview_route

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "benchmarks" / "audio" / "catalog.json"
FIXTURE_ID = "rc-low-pass-routing-v1"
FIXTURE = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-routing-v1.kicad_pcb"
SCRIPT_PATH = Path("scripts/benchmark_audio_routing_gap.py")
CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}


def _sha256(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


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


def _fixture_metadata() -> dict[str, Any]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    fixtures = catalog.get("fixtures")
    if not isinstance(fixtures, list):
        raise RuntimeError("audio benchmark catalog has no fixture list")
    metadata = next(
        (item for item in fixtures if isinstance(item, dict) and item.get("id") == FIXTURE_ID),
        None,
    )
    if metadata is None:
        raise RuntimeError(f"audio benchmark catalog does not declare {FIXTURE_ID}")
    if metadata.get("origin") != "coppermcp-original":
        raise RuntimeError("fixture is not declared CopperMCP-original")
    if metadata.get("license_spdx") != "Apache-2.0":
        raise RuntimeError("fixture is not declared Apache-2.0")
    if metadata.get("third_party_content_included") is not False:
        raise RuntimeError("fixture must not include third-party content")
    if metadata.get("artifact_path") != str(FIXTURE.relative_to(ROOT)):
        raise RuntimeError("fixture path disagrees with the reviewed audio catalog")
    return metadata


def _request(board: str) -> dict[str, Any]:
    return {
        "board": board,
        "net": "AUDIO_IN",
        "layer": "F.Cu",
        "seed": 23,
        "constraints": dict(CONSTRAINTS),
    }


def run_benchmark(repetitions: int) -> dict[str, Any]:
    """Run repeatable preview-and-serialization checks over the reviewed microcase."""

    if not 1 <= repetitions <= 100:
        raise ValueError("repetitions must be between 1 and 100")
    metadata = _fixture_metadata()
    source = FIXTURE.read_bytes()
    source_sha256 = _sha256(source)
    if metadata.get("artifact_sha256") != source_sha256:
        raise RuntimeError("fixture bytes do not match the reviewed catalog digest")

    with tempfile.TemporaryDirectory(prefix="copper-mcp-audio-routing-gap-") as directory:
        workspace = Path(directory)
        board = workspace / FIXTURE.name
        board.write_bytes(source)
        settings = Settings(workspace=workspace)
        before = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)

        previews = [preview_route(_request(board.name), settings) for _ in range(repetitions)]
        if any(preview.status is not RoutePreviewStatus.ROUTED for preview in previews):
            raise RuntimeError("audio microcase did not produce a route candidate")
        if any(preview.candidate is None for preview in previews):
            raise RuntimeError("routed audio microcase did not return a candidate")
        candidates = [preview.candidate for preview in previews]
        assert all(candidate is not None for candidate in candidates)
        candidate_ids = {candidate.candidate_id for candidate in candidates}
        if len(candidate_ids) != 1:
            raise RuntimeError("audio route candidate was not deterministic")
        candidate = candidates[0]
        assert candidate is not None
        if candidate.pad_count != 2 or candidate.ordering_policy != "single-path":
            raise RuntimeError("audio microcase no longer represents the declared two-pad route")

        profile = previews[0].request.profile()
        conversion = parse_kicad_bytes(source, profile)
        if conversion.snapshot is None or conversion.diagnostics:
            raise RuntimeError("audio fixture did not cleanly convert to Board IR")
        source_snapshot = conversion.snapshot
        rendered = [
            render_kicad_candidate_board(source, source_snapshot, item, profile)
            for item in candidates
        ]
        if len(set(rendered)) != 1:
            raise RuntimeError("candidate serialization was not deterministic")
        derivative = rendered[0]
        patched = parse_kicad_bytes(derivative, profile)
        if patched.snapshot is None or patched.diagnostics:
            raise RuntimeError("candidate derivative did not round-trip through Board IR")

        original_segments = source_snapshot.content.segments
        rendered_segments = patched.snapshot.content.segments
        original_segment_ids = {item.id for item in original_segments}
        added_segments = tuple(
            segment for segment in rendered_segments if segment.id not in original_segment_ids
        )
        expected_edges = sum(len(path.vertices) - 1 for path in candidate.patch.paths)
        if len(added_segments) != expected_edges or expected_edges < 1:
            raise RuntimeError(
                "candidate derivative did not contain every newly proposed copper edge"
            )
        if any(
            segment.net_id != candidate.patch.net_id
            or segment.layer_id != candidate.patch.layer_id
            or segment.width_nm != candidate.patch.width_nm
            for segment in added_segments
        ):
            raise RuntimeError("candidate derivative changed the proposed copper attributes")
        added_length_nm = sum(
            abs(segment.end.x - segment.start.x) + abs(segment.end.y - segment.start.y)
            for segment in added_segments
        )
        if added_length_nm != candidate.cost.length_nm:
            raise RuntimeError("candidate derivative length does not match candidate cost")

        after = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
        if before != after:
            raise RuntimeError("benchmark mutated its private workspace board")

    return {
        "fixture_id": FIXTURE_ID,
        "fixture_origin": metadata["origin"],
        "fixture_license_spdx": metadata["license_spdx"],
        "fixture_source_sha256": source_sha256,
        "fixture_license_sha256": metadata["license_sha256"],
        "fixture_derivation_statement": metadata["derivation_statement"],
        "repetitions": repetitions,
        "deterministic_candidate_id": True,
        "deterministic_derivative_bytes": True,
        "candidate_id": candidate.candidate_id,
        "candidate_pad_count": candidate.pad_count,
        "candidate_path_count": len(candidate.patch.paths),
        "candidate_edge_count": expected_edges,
        "candidate_wire_length_nm": candidate.metrics.wire_length_nm,
        "candidate_total_cost_nm": candidate.cost.total_cost_nm,
        "original_segment_count": len(original_segments),
        "rendered_segment_count": len(rendered_segments),
        "new_copper_segment_count": len(added_segments),
        "new_copper_length_nm": added_length_nm,
        "source_unchanged": True,
        "derivative_written": False,
        "candidate_applied": False,
        "kicad_invoked": False,
        "authoritative_drc_performed": False,
        "not_claimed": [
            "external-or-production-board-coverage",
            "candidate-drc",
            "apply",
            "manufacturability",
            "fabrication-readiness",
            "hardware-measurement",
        ],
    }


def build_report(repetitions: int, *, timestamp: datetime | None = None) -> dict[str, Any]:
    """Return a content-addressed benchmark report for one local execution."""

    report: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/audio-routing-gap/v1",
        "date_utc": (timestamp or datetime.now(UTC)).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "script": str(SCRIPT_PATH),
        "metrics": run_benchmark(repetitions),
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    report["run_id"] = "sha256:" + _sha256(canonical)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        report = build_report(args.repetitions)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"Audio routing-gap benchmark failed: {error}") from error
    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
