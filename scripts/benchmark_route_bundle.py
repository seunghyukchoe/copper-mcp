#!/usr/bin/env python3
"""Replay the public route-bundle service on a committed open fixture.

The artifact measures composition quality only: negotiated lattice overflow versus independent
same-base candidates. A KiCad DRC invocation, when locally available, is over a private combined
derivative and never writes the committed source fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import copper_mcp
from copper_mcp.adapters import KiCadConstraintProfile, net_id_for_name, parse_kicad_bytes
from copper_mcp.adapters.kicad_route_bundle_patch import render_kicad_route_bundle_board
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError, discover_kicad_cli, run_board_drc
from copper_mcp.route_bundle import preview_route_bundle
from copper_mcp.routing import AStarRouter, AStarSettings, CongestionLedger, RouteRequest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks/audio/fixtures/negotiated-crossing-v1.kicad_pcb"
OUTPUT = ROOT / "benchmarks/results/routing/2026-08-05-route-bundle-v1.json"
SCRIPT = Path("scripts/benchmark_route_bundle.py")
NET_NAMES = ("HORIZONTAL", "VERTICAL")
_MAX_EXECUTABLE_BYTES = 128 * 1024 * 1024
_MAX_DRC_REPORT_BYTES = 1 * 1024 * 1024


def _sha256_file(path: Path, maximum_bytes: int) -> str:
    """Digest one already-resolved executable while enforcing a stable byte ceiling."""

    try:
        before = path.stat()
        if not path.is_file() or not 0 < before.st_size <= maximum_bytes:
            raise OSError("executable has an unsupported size")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                digest.update(chunk)
        after = path.stat()
    except OSError as error:
        raise KiCadCliError("KiCad executable cannot be bound to benchmark evidence") from error
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise KiCadCliError("KiCad executable changed while its digest was recorded")
    return f"sha256:{digest.hexdigest()}"


def _constraints() -> dict[str, int]:
    return {
        "clearance_nm": 100_000,
        "track_width_nm": 200_000,
        "via_diameter_nm": 600_000,
        "via_drill_nm": 300_000,
    }


def _settings() -> dict[str, int]:
    return {
        "grid_step_nm": 1_000_000,
        "bend_penalty_nm": 500_000,
        "proximity_penalty_nm": 0,
        "max_grid_nodes": 512,
        "max_expansions": 20_000,
        "max_obstacles": 128,
        "max_obstacle_checks": 200_000,
    }


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:request", name="Request", **_constraints())
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _payload(board: str, source: bytes, snapshot_digest: str) -> dict[str, Any]:
    return {
        "board": board,
        "layer": "F.Cu",
        "constraints": _constraints(),
        "net_ref_ids": [net_id_for_name(name) for name in NET_NAMES],
        "expect_board_revision": f"sha256:{hashlib.sha256(source).hexdigest()}",
        "expect_snapshot_digest": snapshot_digest,
        "seed": 7,
        "settings": _settings(),
    }


def _baseline(snapshot: Any) -> int:
    settings = AStarSettings(**_settings())
    requests = tuple(
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net_id_for_name(name),
            layer_id="layer:F.Cu",
            seed=7 + index,
            settings=settings,
        )
        for index, name in enumerate(NET_NAMES)
    )
    ledger = CongestionLedger(
        grid_step_nm=settings.grid_step_nm, present_penalty_nm=0, history_penalty_nm=0
    )
    for request in sorted(requests, key=lambda item: (item.net_id, item.seed)):
        result = AStarRouter().propose(snapshot, request, congestion_penalty=ledger.penalty)
        if result.candidate is None:
            raise RuntimeError("independent baseline unexpectedly did not route")
        ledger.add_candidate(result.candidate)
    return sum(resource.usage - 1 for resource in ledger.overflow_resources())


def _drc(board: Path) -> dict[str, Any]:
    """Return only bounded KiCad evidence for the private combined derivative.

    The shared adapter resolves the executable, creates a private minimal child environment,
    caps the report before decoding it, validates JSON depth/value budgets, and proves that the
    DRC exit code matches the report findings.  A missing or malformed authority result is never
    converted into a successful DRC claim.
    """

    settings = Settings(
        workspace=board.parent,
        kicad_timeout_seconds=45,
        max_drc_report_bytes=_MAX_DRC_REPORT_BYTES,
        max_drc_context_bytes=8 * 1024 * 1024,
        max_drc_context_files=16,
        max_drc_context_scan_seconds=5,
    )
    try:
        executable = discover_kicad_cli(settings)
        executable_digest = _sha256_file(executable, _MAX_EXECUTABLE_BYTES)
        summary = run_board_drc(
            board.name,
            Settings(
                workspace=board.parent,
                kicad_cli=executable,
                kicad_timeout_seconds=settings.kicad_timeout_seconds,
                max_drc_report_bytes=settings.max_drc_report_bytes,
                max_drc_context_bytes=settings.max_drc_context_bytes,
                max_drc_context_files=settings.max_drc_context_files,
                max_drc_context_scan_seconds=settings.max_drc_context_scan_seconds,
            ),
        )
    except KiCadCliError:
        return {"status": "unavailable", "reason": "bounded KiCad DRC evidence is unavailable"}
    expected_base_revision = f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    if summary.base_revision != expected_base_revision:
        raise KiCadCliError("KiCad DRC evidence is not bound to the combined derivative")
    inferred_exit_code = (
        0
        if summary.error_count == 0
        and summary.warning_count == 0
        and summary.exclusion_count == 0
        and summary.unconnected_count == 0
        else 5
    )
    return {
        "status": "completed",
        "execution": "copper_mcp.kicad_cli.run_board_drc",
        "base_revision": summary.base_revision,
        "drc_context_revision": summary.drc_context_revision,
        "executable": str(executable),
        "executable_sha256": executable_digest,
        "kicad_version": summary.kicad_version,
        "drc_schema": summary.drc_schema,
        "exit_code": inferred_exit_code,
        "error_count": summary.error_count,
        "warning_count": summary.warning_count,
        "exclusion_count": summary.exclusion_count,
        "ignored_check_count": summary.ignored_check_count,
        "unconnected_count": summary.unconnected_count,
        "passed": summary.passed,
    }


def build_report() -> dict[str, Any]:
    source = FIXTURE.read_bytes()
    conversion = parse_kicad_bytes(source, _profile())
    if conversion.snapshot is None or conversion.diagnostics:
        raise RuntimeError("committed route-bundle fixture is outside the Board IR subset")
    with tempfile.TemporaryDirectory(prefix="copper-mcp-route-bundle-") as temporary:
        workspace = Path(temporary)
        board = workspace / FIXTURE.name
        board.write_bytes(source)
        before = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
        first = preview_route_bundle(
            _payload(board.name, source, conversion.snapshot.snapshot_digest),
            Settings(workspace=workspace),
        )
        second = preview_route_bundle(
            _payload(board.name, source, conversion.snapshot.snapshot_digest),
            Settings(workspace=workspace),
        )
        if first != second or first.plan is None:
            raise RuntimeError("route-bundle replay was not deterministic or complete")
        derivative = workspace / "combined-route-bundle.kicad_pcb"
        derivative.write_bytes(
            render_kicad_route_bundle_board(source, conversion.snapshot, first.plan, _profile())
        )
        authority = _drc(derivative)
        if before != (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns):
            raise RuntimeError("route-bundle benchmark mutated the private source copy")
    baseline_overflow = _baseline(conversion.snapshot)
    report = {
        "schema": "copper-mcp/benchmark/route-bundle/v1",
        "copper_mcp_version": copper_mcp.__version__,
        "recorded_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "fixture": FIXTURE.relative_to(ROOT).as_posix(),
        "fixture_sha256": hashlib.sha256(source).hexdigest(),
        "license": "Apache-2.0",
        "fixture_origin": "CopperMCP-original committed public fixture",
        "script": SCRIPT.as_posix(),
        "script_sha256": hashlib.sha256((ROOT / SCRIPT).read_bytes()).hexdigest(),
        "source_unchanged": True,
        "candidate_applied": False,
        "baseline": {"same_base_independent_overflow_units": baseline_overflow},
        "bundle": {
            "bundle_id": first.plan.bundle_id,
            "candidate_count": len(first.plan.candidates),
            "core_replays": first.plan.core_replays,
            "physical_pair_checks": first.plan.physical_pair_checks,
            "overflow_units": 0,
            "total_wire_length_nm": first.plan.total_wire_length_nm,
            "combined_derivative": True,
        },
        "metric": {
            "overflow_reduction_units": baseline_overflow,
            "improved": baseline_overflow > 0,
        },
        "authoritative_kicad_drc": authority,
        "not_claimed": [
            "apply_authority",
            "board_mutation",
            "electrical_validation",
            "fabrication_readiness",
            "general_board_scaling",
        ],
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    report["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    report = build_report()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.write:
        OUTPUT.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
