#!/usr/bin/env python3
"""Benchmark the bounded routing-job worker without persisting board content."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sqlite3
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.routing import (
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteCandidate,
    LayeredRouteRequest,
    RoutingJobAlreadyClaimedError,
    RoutingJobKind,
    RoutingJobLease,
    RoutingJobLimits,
    RoutingJobSpec,
    RoutingJobStatus,
    RoutingJobStore,
    RoutingJobWorker,
    WorkerLimits,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
SCRIPT_PATH = Path(__file__).relative_to(ROOT)
F_CU = "layer:F.Cu"


class _Clock:
    def __init__(self, now_ms: int = 10) -> None:
        self.now_ms = now_ms

    def now(self) -> int:
        return self.now_ms


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


def _candidate_and_spec() -> tuple[LayeredRouteCandidate, RoutingJobSpec]:
    conversion = parse_kicad_bytes(FIXTURE.read_bytes(), _profile())
    if conversion.diagnostics or conversion.snapshot is None:
        raise RuntimeError("benchmark fixture did not parse into Board IR")
    snapshot = conversion.snapshot
    pads = tuple(
        pad for pad in snapshot.content.pads if pad.net_id == snapshot.content.pads[0].net_id
    )
    if len(pads) < 2 or pads[0].net_id is None:
        raise RuntimeError("benchmark fixture lacks two same-net pads")
    request = LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=pads[0].net_id,
        start_pad_id=pads[0].id,
        end_pad_id=pads[1].id,
        start_layer_id=F_CU,
        end_layer_id=F_CU,
        grid_step_nm=1_000,
        settings=LayeredAStarSettings(via_cost=2),
    )
    result = LayeredBoardRouter().propose(snapshot, request)
    if result.candidate is None:
        raise RuntimeError("benchmark fixture did not produce a candidate")
    candidate = result.candidate
    spec = RoutingJobSpec.create(
        board_revision=f"sha256:{'b' * 64}",
        snapshot_digest=snapshot.snapshot_digest,
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        request_digest=f"sha256:{'c' * 64}",
        request_kind=RoutingJobKind.LAYERED,
        backend="board-layered-a-star-v1",
        router_version=candidate.router_version,
        policy=candidate.policy,
        seed=candidate.seed,
        limits=RoutingJobLimits(max_runtime_ms=60_000, max_attempts=2),
    )
    return candidate, spec


def _run() -> dict[str, Any]:
    candidate, spec = _candidate_and_spec()
    clock = _Clock()
    with tempfile.TemporaryDirectory(prefix="copper-mcp-worker-benchmark-") as directory:
        root = Path(directory)
        # Each probe uses a separate store so terminal-state assertions cannot mask one another.
        with RoutingJobStore(root / "success.sqlite3", ttl_ms=60_000) as store:
            store.create(spec, now_ms=clock.now())
            worker = RoutingJobWorker(store, clock=clock.now)
            completed = worker.execute(spec.job_id, lambda _probe: candidate)
            success = completed.status is RoutingJobStatus.COMPLETED
            completed_id = completed.candidate_id

        with RoutingJobStore(root / "race.sqlite3", ttl_ms=60_000) as store:
            store.create(spec, now_ms=clock.now())
            workers = [RoutingJobWorker(store, clock=clock.now) for _ in range(2)]

            def claim(worker: RoutingJobWorker) -> bool:
                try:
                    return isinstance(worker.claim(spec.job_id), RoutingJobLease)
                except (RoutingJobAlreadyClaimedError, ValueError):
                    return False

            with ThreadPoolExecutor(max_workers=2) as executor:
                race_results = list(executor.map(claim, workers))

        with RoutingJobStore(root / "cancel.sqlite3", ttl_ms=60_000) as store:
            store.create(spec, now_ms=clock.now())
            worker = RoutingJobWorker(store, clock=clock.now)

            def cancel_executor(_probe: object) -> LayeredRouteCandidate:
                lease = worker.active_lease
                if lease is None:
                    raise RuntimeError("worker did not expose its lease")
                store.request_cancel(
                    spec.job_id, expected_revision=lease.revision, now_ms=clock.now()
                )
                return candidate

            cancelled = worker.execute(spec.job_id, cancel_executor)

        recovery_clock = _Clock()
        with RoutingJobStore(root / "recovery.sqlite3", ttl_ms=60_000) as store:
            store.create(spec, now_ms=recovery_clock.now())
            RoutingJobWorker(
                store, limits=WorkerLimits(lease_ms=10), clock=recovery_clock.now
            ).claim(spec.job_id)
            recovery_clock.now_ms = 20
            recovered = RoutingJobWorker(
                store, limits=WorkerLimits(lease_ms=10), clock=recovery_clock.now
            ).recover_expired(spec.job_id)

        invalid_path = root / "invalid.sqlite3"
        with RoutingJobStore(invalid_path, ttl_ms=60_000) as store:
            store.create(spec, now_ms=clock.now())
            invalid = RoutingJobWorker(store, clock=clock.now).execute(
                spec.job_id, lambda _probe: cast(LayeredRouteCandidate, object())
            )
        with sqlite3.connect(invalid_path) as connection:
            row = connection.execute("SELECT record_json FROM routing_jobs").fetchone()
        if row is None or not isinstance(row[0], bytes):
            raise RuntimeError("worker benchmark record was not persisted as bytes")
        redacted = b"vertices" not in row[0] and FIXTURE.read_bytes() not in row[0]

    return {
        "success_completed": success,
        "deterministic_candidate_id": completed_id == candidate.candidate_id,
        "claim_race_one_winner": sum(race_results) == 1,
        "cancellation_terminal": cancelled.status is RoutingJobStatus.CANCELLED,
        "expired_lease_recovered": recovered.status is RoutingJobStatus.FAILED,
        "invalid_candidate_terminal": (
            invalid.status is RoutingJobStatus.FAILED
            and invalid.diagnostic_code.value == "invalid_request"
        ),
        "redacted_storage": redacted,
        "candidate_persistence": False,
        "mcp_tasks": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metrics = _run()
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/routing-job-worker/v1",
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
