#!/usr/bin/env python3
"""Produce deterministic advisory-policy replay evidence without invoking routing.

The report establishes only contract replay and hostile-decision refusal.  It neither routes a
board nor makes a route-quality, physical-validity, DRC, fabrication, or model-quality claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from copper_mcp.routing.policy import (
    DeterministicReferencePolicy,
    RoutingPolicyDecision,
    RoutingPolicyInput,
    canonical_policy_decision_bytes,
    canonical_policy_trace_bytes,
    decode_policy_input_json,
    evaluate_policy,
    policy_decision_digest,
    redacted_policy_trace,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "routing-policy" / "reference-input.json"
SCRIPT_PATH = Path(__file__).relative_to(ROOT)
# The policy implementation measured by this fixture is public and reachable from this branch.
# Evidence is intentionally recorded separately because the benchmark harness was introduced later.
IMPLEMENTATION_COMMIT = "f1af095bfd6712969b0f9fb29933cd48aedef0dc"
REPLAY_COUNT = 10


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def _input() -> RoutingPolicyInput:
    return decode_policy_input_json(FIXTURE.read_bytes())


def _hostile_decision_is_refused(policy_input: RoutingPolicyInput) -> bool:
    accepted = evaluate_policy(DeterministicReferencePolicy(), policy_input)

    class HostilePolicy:
        policy_id = "benchmark-hostile-policy-v1"

        def propose(self, _policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
            return RoutingPolicyDecision(
                policy_id=self.policy_id,
                input_digest="sha256:" + "0" * 64,
                net_order=accepted.net_order,
            )

    try:
        evaluate_policy(HostilePolicy(), policy_input)
    except ValueError:
        return True
    return False


def _metrics() -> dict[str, Any]:
    policy_input = _input()
    decisions = tuple(
        evaluate_policy(DeterministicReferencePolicy(), policy_input) for _ in range(REPLAY_COUNT)
    )
    traces = tuple(redacted_policy_trace(policy_input, decision) for decision in decisions)
    first_decision = decisions[0]
    first_trace = traces[0]
    if not all(decision == first_decision for decision in decisions):
        raise RuntimeError("reference policy decisions were not deterministic")
    if not all(trace == first_trace for trace in traces):
        raise RuntimeError("policy traces were not deterministic")
    if not _hostile_decision_is_refused(policy_input):
        raise RuntimeError("hostile policy decision was accepted")
    return {
        "replay_count": REPLAY_COUNT,
        "closed_decision_accepted": True,
        "hostile_decision_refused": True,
        "deterministic_decision": True,
        "deterministic_trace": True,
        "decision_digest": policy_decision_digest(first_decision),
        "decision_bytes_sha256": "sha256:"
        + hashlib.sha256(canonical_policy_decision_bytes(first_decision)).hexdigest(),
        "trace_bytes_sha256": "sha256:"
        + hashlib.sha256(canonical_policy_trace_bytes(first_trace)).hexdigest(),
        "routing_invoked": False,
        "copper_emitted": False,
        "routing_quality_claim": False,
        "physical_validity_claim": False,
    }


def _commit(value: str, *, option: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{option} must be a lowercase 40-character Git commit ID")
    return value


def build_report(*, evidence_source_commit: str) -> dict[str, Any]:
    """Build one content-addressed, locally reproducible policy-boundary report."""

    report: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/routing-policy/v2",
        "evidence_source_commit": _commit(evidence_source_commit, option="evidence_source_commit"),
        "implementation_commit": IMPLEMENTATION_COMMIT,
        "script": SCRIPT_PATH.as_posix(),
        "script_sha256": hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest(),
        "fixture": FIXTURE.relative_to(ROOT).as_posix(),
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "metrics": _metrics(),
        "non_claims": [
            "route construction, candidate geometry, or copper emission",
            "route-quality or model-quality improvement",
            "physical validity, KiCad DRC, fabrication, or FreeRouting parity",
            (
                "trace-digest secrecy, unlinkability, or resistance to complete-record "
                "dictionary tests"
            ),
        ],
    }
    report["run_id"] = "sha256:" + hashlib.sha256(_canonical_bytes(report)).hexdigest()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-source-commit", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = build_report(evidence_source_commit=args.evidence_source_commit)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
