#!/usr/bin/env python3
"""Measure the public layered DRC evidence contract without spawning KiCad.

The service is exercised through its file-backed preview entry point.  A deterministic fake
authority stands in for KiCad so this benchmark measures schema/binding closure, opt-in behavior,
and source preservation without making a machine-specific DRC performance claim.  The real
blocked-pad KiCad path is covered by the integration test and is recorded separately in B-032.
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
from unittest.mock import patch

import copper_mcp.layered_route_preview as layered_preview
from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import LayeredRouteCandidateDrcEvidence
from copper_mcp.models import DrcSummary

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
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


def _run() -> dict[str, Any]:
    source = FIXTURE.read_bytes()
    source_digest = f"sha256:{hashlib.sha256(source).hexdigest()}"
    constraints = NetClass(
        id="class:request",
        name="Request",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    profile = KiCadConstraintProfile(
        net_classes=(constraints,),
        default_net_class_id=constraints.id,
    )
    conversion = parse_kicad_bytes(source, profile)
    if conversion.snapshot is None:
        raise RuntimeError("two-pad fixture did not convert")
    pads = conversion.snapshot.content.pads
    with tempfile.TemporaryDirectory(prefix="copper-mcp-layered-drc-benchmark-") as directory:
        workspace = Path(directory)
        board = workspace / FIXTURE.name
        board.write_bytes(source)
        settings = Settings(workspace=workspace, max_route_preview_seconds=10)
        revision = source_digest
        request: dict[str, Any] = {
            "board": board.name,
            "start_pad_id": pads[0].id,
            "end_pad_id": pads[1].id,
            "expect_board_revision": revision,
            "expect_snapshot_digest": conversion.snapshot.snapshot_digest,
            "constraints": {
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            },
        }
        before = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
        calls = 0

        def fake_drc(*args: Any, **kwargs: Any) -> LayeredRouteCandidateDrcEvidence:
            nonlocal calls
            calls += 1
            candidate = args[1]
            patched = "sha256:" + "b" * 64
            context = "sha256:" + "c" * 64
            return LayeredRouteCandidateDrcEvidence(
                candidate_id=candidate.candidate_id,
                candidate_base_revision=candidate.base_revision,
                source_revision=revision,
                patched_board_revision=patched,
                patched_drc_context_revision=context,
                summary=DrcSummary(
                    base_revision=patched,
                    drc_context_revision=context,
                    kicad_version="10.0.5",
                    drc_schema="https://schemas.kicad.org/drc.v1.json",
                    coordinate_units="mm",
                    error_count=0,
                    warning_count=0,
                    exclusion_count=0,
                    ignored_check_count=0,
                    unconnected_count=0,
                    violation_type_counts={},
                    passed=True,
                ),
            )

        with patch.object(layered_preview, "run_layered_route_candidate_drc", fake_drc):
            omitted = layered_preview.preview_layered_route(request, settings)
            omitted_calls = calls
            requested = layered_preview.preview_layered_route(
                {**request, "include_drc": True}, settings
            )
        evidence = requested.get("drc_evidence")
        after = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
        if omitted["status"] != "routed" or requested["status"] != "routed":
            raise RuntimeError("layered fixture did not route")
        if omitted.get("drc_evidence") is not None or omitted_calls != 0:
            raise RuntimeError("DRC was not opt-in")
        if not isinstance(evidence, dict):
            raise RuntimeError("requested DRC evidence was not serialized")
        if evidence["candidate_id"] != requested["candidate"]["candidate_id"]:
            raise RuntimeError("DRC evidence was not candidate-bound")
        if evidence["source_revision"] != revision or calls != 1:
            raise RuntimeError("DRC evidence binding or call count is incorrect")
        if before != after:
            raise RuntimeError("public preview mutated its source board")
        return {
            "fixture_sha256": source_digest,
            "omitted_drc_calls": omitted_calls,
            "requested_drc_calls": calls,
            "candidate_evidence_binding": True,
            "source_unchanged": True,
            "workspace_mutations": 0,
            "kicad_invoked": False,
            "schema_or_authority_quality_claim": False,
            "whole_board_drc_claim": False,
            "freerouting_parity_claim": False,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    metrics = _run()
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/layered-drc-preview/v1",
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
