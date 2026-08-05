#!/usr/bin/env python3
"""Measure the bounded, transport-independent routing-job ledger.

This benchmark exercises SQLite persistence and compare-and-swap transitions without running a
router or retaining a candidate board. It records local ledger latency as operational evidence;
the numbers are not routing-quality, KiCad-DRC, or MCP-Tasks evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.routing.jobs import (
    RoutingJobConflictError,
    RoutingJobKind,
    RoutingJobLimits,
    RoutingJobNotFoundError,
    RoutingJobSpec,
    RoutingJobStatus,
    RoutingJobStore,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
SCRIPT_PATH = Path("scripts/benchmark_routing_jobs.py")


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


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return round(ordered[index], 3)


def _spec(index: int, board_revision: str) -> RoutingJobSpec:
    return RoutingJobSpec.create(
        board_revision=board_revision,
        snapshot_digest=board_revision,
        start_pad_id=f"pad:start-{index}",
        end_pad_id=f"pad:end-{index}",
        request_digest=_digest(f"routing-job-request-{index}".encode()),
        request_kind=RoutingJobKind.LAYERED,
        backend="board-layered-a-star-v1",
        router_version="layered-board-v1",
        policy="layered-board-v1",
        seed=index,
        limits=RoutingJobLimits(max_runtime_ms=60_000, max_attempts=1),
    )


def _run(repetitions: int) -> dict[str, Any]:
    source = FIXTURE.read_bytes()
    source_digest = _digest(source)
    with tempfile.TemporaryDirectory(prefix="copper-mcp-routing-jobs-") as directory:
        database = Path(directory) / "jobs.sqlite3"
        before = (source, FIXTURE.stat().st_ino, FIXTURE.stat().st_mtime_ns)
        create_latencies: list[float] = []
        transition_latencies: list[float] = []
        specs = [_spec(index, source_digest) for index in range(repetitions)]
        with RoutingJobStore(database, max_records=repetitions + 1, ttl_ms=86_400_000) as store:
            for index, spec in enumerate(specs):
                started = time.perf_counter_ns()
                record = store.create(spec, now_ms=1_000 + index)
                create_latencies.append((time.perf_counter_ns() - started) / 1_000)
                started = time.perf_counter_ns()
                if index % 2 == 0:
                    record = store.start(
                        spec.job_id, expected_revision=record.revision, now_ms=2_000 + index
                    )
                    record = store.request_cancel(
                        spec.job_id, expected_revision=record.revision, now_ms=3_000 + index
                    )
                else:
                    record = store.get(spec.job_id, now_ms=2_000 + index)
                transition_latencies.append((time.perf_counter_ns() - started) / 1_000)
                if index % 2 == 0 and record.status is not RoutingJobStatus.CANCEL_REQUESTED:
                    raise RuntimeError("ledger cancellation transition was not persisted")

            duplicate = store.create(specs[0], now_ms=9_000)
            idempotent = duplicate.spec == specs[0] and duplicate.created_at_ms == 1_000
            try:
                store.request_cancel(specs[1].job_id, expected_revision=1, now_ms=9_001)
            except RoutingJobConflictError:
                cas_refusal = True
            else:
                cas_refusal = False

        with RoutingJobStore(database, max_records=repetitions + 1, ttl_ms=86_400_000) as reopened:
            rehydrated = all(
                reopened.get(spec.job_id, now_ms=10_000).spec == spec for spec in specs
            )

        with RoutingJobStore(Path(directory) / "expiry.sqlite3", ttl_ms=5) as expiring:
            expiring.create(specs[0], now_ms=20_000)
            try:
                expiring.get(specs[0].job_id, now_ms=20_005)
            except RoutingJobNotFoundError:
                expiry_refusal = True
            else:
                expiry_refusal = False

        raw_database = database.read_bytes()
        after = (FIXTURE.read_bytes(), FIXTURE.stat().st_ino, FIXTURE.stat().st_mtime_ns)
        return {
            "repetitions": repetitions,
            "records_rehydrated": rehydrated,
            "idempotent_create": idempotent,
            "cas_refusal": cas_refusal,
            "expiry_refusal": expiry_refusal,
            "redacted_storage": b"vertices" not in raw_database and source not in raw_database,
            "source_unchanged": before == after,
            "create_latency_us": {
                "p50": _percentile(create_latencies, 0.50),
                "p95": _percentile(create_latencies, 0.95),
            },
            "transition_latency_us": {
                "p50": _percentile(transition_latencies, 0.50),
                "p95": _percentile(transition_latencies, 0.95),
            },
            "worker_execution": False,
            "candidate_geometry_persistence": False,
            "mcp_tasks": False,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 10 <= args.repetitions <= 256:
        raise SystemExit("--repetitions must be between 10 and 256")
    metrics = _run(args.repetitions)
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/routing-job-ledger/v1",
        "date_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "script": str(SCRIPT_PATH),
        "metrics": metrics,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    payload["run_id"] = _digest(canonical)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
