#!/usr/bin/env python3
"""Measure early cooperative refusal for an expired live-IPC board parse."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

from copper_mcp.kicad_ipc import KicadIpcDeadlineError, _count_serialized_items

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "benchmarks/results/routing/2026-08-05-ipc-deadline-parse.json"


def main() -> int:
    source = b"(kicad_pcb " + b"(net 1 N)" * 100_000
    calls = 0

    def expired() -> None:
        nonlocal calls
        calls += 1
        raise KicadIpcDeadlineError("live IPC capture deadline expired")

    started = time.perf_counter_ns()
    refused = False
    try:
        _count_serialized_items(source, 16 * 1024 * 1024, check_deadline=expired)
    except KicadIpcDeadlineError:
        refused = True
    elapsed = time.perf_counter_ns() - started
    payload = {
        "schema": "copper-mcp/benchmark/ipc-deadline-parse/v1",
        "date_utc": "2026-08-05",
        "source_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - repository-local metadata
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "metrics": {
            "payload_bytes": len(source),
            "payload_sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
            "deadline_refused": refused,
            "deadline_callback_calls": calls,
            "elapsed_ns": elapsed,
            "parser_invocation_completed": False,
        },
        "not_claimed": [
            "hard process preemption",
            "UTF-8 decode interruption",
            "blocking KiCad IPC call interruption",
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
