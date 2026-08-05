#!/usr/bin/env python3
"""Replay the predeclared gate for an opt-in exact-local-repair transaction.

This harness intentionally does *not* admit a repair profile. The predeclared fixture's ordinary
coordinator already publishes a complete zero-overflow allocation in its first iteration, so there
is no complete rejected allocation from which a coordinator-owned repair transaction may derive
its immutable window or occupancy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from copper_mcp.routing import (
    NegotiatedRoutingRequest,
    NegotiatedRoutingStatus,
    RouteRequest,
    negotiate_routes,
)
from copper_mcp.routing.physical_clearance import verify_negotiated_physical_clearance

try:
    from scripts.exact_local_repair_gate_fixture import (
        EXPECTED_SNAPSHOT_DIGEST,
        PREDECLARED_SOURCE_COMMIT,
        build_requests,
        build_snapshot,
        settings,
    )
except ModuleNotFoundError:  # Script execution adds its own directory, not the repository root.
    from exact_local_repair_gate_fixture import (
        EXPECTED_SNAPSHOT_DIGEST,
        PREDECLARED_SOURCE_COMMIT,
        build_requests,
        build_snapshot,
        settings,
    )

SCRIPT_FILE = Path(__file__).resolve()
ROOT = SCRIPT_FILE.parents[1]
SCRIPT_PATH = SCRIPT_FILE.relative_to(ROOT)
FIXTURE_HELPER = ROOT / "scripts" / "exact_local_repair_gate_fixture.py"
OUTPUT = (
    ROOT
    / "benchmarks"
    / "results"
    / "routing"
    / "2026-08-05-exact-local-repair-gate-correction-v2.json"
)
BENCHMARK = "exact-local-repair-negotiated-gate-v2"
REPLAY_COUNT = 10


def _load() -> tuple[Any, tuple[RouteRequest, ...]]:
    snapshot = build_snapshot()
    return snapshot, build_requests(snapshot)


def _replay(snapshot: Any, requests: tuple[RouteRequest, ...]) -> dict[str, Any]:
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=requests,
        max_iterations=4,
        max_total_expansions=2_000_000,
        max_total_obstacle_checks=10_000_000,
        max_total_physical_checks=2_000_000,
    )
    result = negotiate_routes(snapshot, envelope)
    physical = verify_negotiated_physical_clearance(
        snapshot,
        result.candidates,
        layer_id=envelope.layer_id,
        max_pair_checks=envelope.max_total_physical_checks,
    )
    return {
        "candidate_ids": [candidate.candidate_id for candidate in result.candidates],
        "candidate_revisions_exact": all(
            candidate.base_revision == snapshot.snapshot_digest for candidate in result.candidates
        ),
        "iterations": result.iterations,
        "overflow_units": result.overflow_units,
        "physical_gate": {
            "accepted": physical.accepted,
            "failure": None if physical.failure is None else physical.failure.value,
            "pair_checks": physical.pair_checks,
        },
        "ripups": result.ripups,
        "router_expansions": sum(
            candidate.metrics.expanded_states for candidate in result.candidates
        ),
        "router_obstacle_checks": sum(
            candidate.metrics.obstacle_checks for candidate in result.candidates
        ),
        "status": result.status.value,
        "unrouted_nets": list(result.unrouted_nets),
    }


def build_report() -> dict[str, Any]:
    """Return one deterministic no-admission gate report for the fixed held-out fixture."""

    snapshot, requests = _load()
    replays = [_replay(snapshot, requests) for _ in range(REPLAY_COUNT)]
    if any(replay != replays[0] for replay in replays[1:]):
        raise RuntimeError("the predeclared coordinator replay was not deterministic")
    baseline = replays[0]
    completed = baseline["status"] == NegotiatedRoutingStatus.COMPLETED.value
    gates_hold = (
        completed
        and baseline["overflow_units"] == 0
        and baseline["candidate_revisions_exact"]
        and baseline["physical_gate"]["accepted"]
    )
    report: dict[str, Any] = {
        "benchmark": BENCHMARK,
        "candidate_identity": {
            "no_profile_candidate_identity": "negotiated-congestion-v2 (unchanged)",
            "no_profile_result_shape": "NegotiatedRoutingResult (unchanged)",
        },
        "configuration": {
            "envelope": {
                "max_iterations": 4,
                "max_total_expansions": 2_000_000,
                "max_total_obstacle_checks": 10_000_000,
                "max_total_physical_checks": 2_000_000,
            },
            "settings": asdict(settings()),
        },
        "dataset": {
            "expected_snapshot_digest": EXPECTED_SNAPSHOT_DIGEST,
            "fixture_helper": FIXTURE_HELPER.relative_to(ROOT).as_posix(),
            "fixture_helper_sha256": hashlib.sha256(FIXTURE_HELPER.read_bytes()).hexdigest(),
            "pinned_source_commit": PREDECLARED_SOURCE_COMMIT,
            "predeclared_in": "docs/research/exact-local-repair-negotiated-integration-gate.md",
            "provenance": (
                "Committed semantic helper equivalence-tested against the original builder."
            ),
        },
        "gate": {
            "baseline_gates_hold": gates_hold,
            "minimum_improvement_percent": 10,
            "named_coordinator_metric": "iterations",
            "result": "declined",
            "transaction_admitted": False,
            "why": (
                "The no-profile coordinator completed with zero overflow in one iteration, so no "
                "complete rejected allocation exists. A repair window, occupancy projection, local "
                "repair search, validator invocation, and candidate-v3 binding are not admissible."
            ),
        },
        "metering": {
            "candidate_path_edge_checks": 0,
            "candidate_path_obstacle_checks": 0,
            "local_repair_expanded_states": 0,
            "reason": "No rejected allocation exists; no repair or validator work was performed.",
        },
        "replay_count": REPLAY_COUNT,
        "replay_deterministic": True,
        "replays": replays,
        "schema": "copper-mcp/benchmark/exact-local-repair-gate/v2",
        "script": SCRIPT_PATH.as_posix(),
        "script_sha256": hashlib.sha256(SCRIPT_FILE.read_bytes()).hexdigest(),
        "snapshot_digest": snapshot.snapshot_digest,
    }
    report["run_id"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(report, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the committed report path")
    args = parser.parse_args()
    rendered = json.dumps(build_report(), indent=2, sort_keys=True) + "\n"
    if args.write:
        OUTPUT.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
