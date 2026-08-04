#!/usr/bin/env python3
"""Benchmark restart-safe layered routing requests, results, and explicit export."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.routing import RoutingJobRepository, RoutingJobRequestUnavailableError
from copper_mcp.routing_job_service import (
    RoutingJobServiceError,
    execute_routing_job,
    export_routing_candidate,
    get_routing_job,
    start_routing_job,
)

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


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _request(board_name: str, source: bytes) -> dict[str, object]:
    constraints = NetClass(
        id="class:request",
        name="Request",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    conversion = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(constraints,), default_net_class_id=constraints.id),
    )
    if conversion.diagnostics or conversion.snapshot is None:
        raise RuntimeError("benchmark fixture did not parse into Board IR")
    pads = conversion.snapshot.content.pads
    if len(pads) != 2 or pads[0].net_id is None or pads[0].net_id != pads[1].net_id:
        raise RuntimeError("benchmark fixture does not contain two pads on one net")
    return {
        "board": board_name,
        "start_pad_id": pads[0].id,
        "end_pad_id": pads[1].id,
        "expect_board_revision": _digest(source),
        "expect_snapshot_digest": conversion.snapshot.snapshot_digest,
        "constraints": {
            "clearance_nm": constraints.clearance_nm,
            "track_width_nm": constraints.track_width_nm,
            "via_diameter_nm": constraints.via_diameter_nm,
            "via_drill_nm": constraints.via_drill_nm,
        },
        "grid_step_nm": 250_000,
        "seed": 0,
        "settings": {
            "move_cost": 1,
            "via_cost": 10,
            "max_expansions": 100_000,
            "max_nodes": 250_000,
            "max_obstacles": 256,
            "max_obstacle_checks": 2_000_000,
        },
    }


def _run() -> dict[str, object]:
    source = FIXTURE.read_bytes()
    authorization = _digest(b"benchmark-caller-context")
    with tempfile.TemporaryDirectory(prefix="copper-mcp-routing-job-benchmark-") as directory:
        root = Path(directory)
        board = root / FIXTURE.name
        board.write_bytes(source)
        request = _request(board.name, source)
        settings = Settings(workspace=root)
        path = root / "routing.sqlite3"

        with RoutingJobRepository(path, ttl_ms=60_000) as repository:
            started = start_routing_job(
                {"request": request, "authorization_digest": authorization},
                settings,
                repository,
            )
            job_id = str(started["job_id"])
            queued = started["status"] == "queued"
            deterministic_repeat = (
                start_routing_job(
                    {"request": request, "authorization_digest": authorization},
                    settings,
                    repository,
                )["job_id"]
                == job_id
            )
            try:
                get_routing_job(
                    {"job_id": job_id, "authorization_digest": _digest(b"wrong")},
                    repository,
                )
            except RoutingJobRequestUnavailableError:
                wrong_context_refused = True
            else:
                wrong_context_refused = False
            completed = execute_routing_job(job_id, authorization, settings, repository)
            completed_ok = completed.status.value == "completed"
            candidate_id = completed.candidate_id
            if candidate_id is None:
                raise RuntimeError("benchmark route did not produce a candidate")
            exported = export_routing_candidate(
                {
                    "job_id": job_id,
                    "candidate_id": candidate_id,
                    "authorization_digest": authorization,
                },
                repository,
            )
            explicit_export = exported.get("candidate_id") == candidate_id
            raw = (
                sqlite3.connect(path)
                .execute("SELECT group_concat(candidate_json, '') FROM routing_candidate_exports")
                .fetchone()
            )
            export_bytes = b"" if raw is None or raw[0] is None else str(raw[0]).encode()
            redacted_request = (
                b"board_bytes" not in path.read_bytes() and source not in export_bytes
            )
            get_samples: list[int] = []
            export_samples: list[int] = []
            for _ in range(5):
                start = time.perf_counter_ns()
                get_routing_job(
                    {"job_id": job_id, "authorization_digest": authorization}, repository
                )
                get_samples.append(time.perf_counter_ns() - start)
                start = time.perf_counter_ns()
                export_routing_candidate(
                    {
                        "job_id": job_id,
                        "candidate_id": candidate_id,
                        "authorization_digest": authorization,
                    },
                    repository,
                )
                export_samples.append(time.perf_counter_ns() - start)

        with RoutingJobRepository(path, ttl_ms=60_000) as reopened:
            restart_recovery = (
                get_routing_job(
                    {"job_id": job_id, "authorization_digest": authorization}, reopened
                )["status"]
                == "completed"
            )
            request_persisted = (
                get_routing_job(
                    {"job_id": job_id, "authorization_digest": authorization}, reopened
                )["request"]
                == request
            )
            try:
                start_routing_job(
                    {
                        "request": {**request, "board": "live"},
                        "authorization_digest": authorization,
                    },
                    settings,
                    reopened,
                )
            except RoutingJobServiceError:
                live_refused = True
            else:
                live_refused = False

        return {
            "queued_before_worker": queued,
            "deterministic_job_id_idempotency": deterministic_repeat,
            "completed_candidate": completed_ok,
            "candidate_geometry_exported_only_explicitly": explicit_export,
            "wrong_context_refused": wrong_context_refused,
            "restart_recovered_terminal_job": restart_recovery,
            "restart_recovered_normalized_request": request_persisted,
            "request_and_export_redacted": redacted_request,
            "live_request_refused": live_refused,
            "get_median_ns": int(median(get_samples)),
            "export_median_ns": int(median(export_samples)),
            "request_result_repository": True,
            "mcp_tasks": False,
            "board_mutation": False,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/routing-job-request-result-export/v1",
        "date_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "script": str(SCRIPT_PATH),
        "metrics": _run(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    payload["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
