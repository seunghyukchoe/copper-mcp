from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from copper_mcp.routing.policy import (
    CorridorCandidate,
    DeterministicReferencePolicy,
    PolicyBounds,
    RepairWindowCandidate,
    RoutingPolicyDecision,
    RoutingPolicyInput,
    canonical_policy_decision_bytes,
    canonical_policy_input_bytes,
    canonical_policy_trace_bytes,
    decode_policy_input_json,
    evaluate_policy,
    policy_decision_digest,
    policy_input_digest,
    redacted_policy_trace,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "routing-policy" / "reference-input.json"


def _input() -> RoutingPolicyInput:
    return decode_policy_input_json(_FIXTURE.read_bytes())


def test_reference_policy_is_repeatable_and_content_addressed() -> None:
    policy_input = _input()
    first = evaluate_policy(DeterministicReferencePolicy(), policy_input)
    second = evaluate_policy(DeterministicReferencePolicy(), policy_input)

    assert first == second
    assert canonical_policy_input_bytes(policy_input) == canonical_policy_input_bytes(policy_input)
    assert policy_input_digest(policy_input) == policy_input_digest(policy_input)
    assert canonical_policy_decision_bytes(first) == canonical_policy_decision_bytes(second)
    assert policy_decision_digest(first) == policy_decision_digest(second)
    assert first.net_order == ("net:clock", "net:data")
    assert len(first.corridor_hints) == 3


def test_redacted_trace_is_repeatable_and_excludes_source_sensitive_values() -> None:
    policy_input = _input()
    decision = evaluate_policy(DeterministicReferencePolicy(), policy_input)
    first = redacted_policy_trace(policy_input, decision)
    second = redacted_policy_trace(policy_input, decision)
    serialized = canonical_policy_trace_bytes(first).decode("ascii")

    assert first == second
    assert canonical_policy_trace_bytes(first) == canonical_policy_trace_bytes(second)
    assert "net:clock" not in serialized
    assert "net:data" not in serialized
    assert "min_x" not in serialized
    assert "criticality" not in serialized
    assert "board_revision" not in serialized
    assert "vertices" not in serialized
    assert first.net_count == 2


def test_trace_tokens_cannot_be_reproduced_from_raw_names_or_window_json() -> None:
    policy_input = _input()
    decision = evaluate_policy(DeterministicReferencePolicy(), policy_input)
    trace = redacted_policy_trace(policy_input, decision)
    input_digest = policy_input_digest(policy_input)
    guessed_net_token = hashlib.sha256(f"{input_digest}\x00net:clock".encode()).hexdigest()[:24]
    guessed_window = (
        json.dumps(
            decision.corridor_hints[0].as_json(),
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    guessed_window_token = hashlib.sha256(
        f"{input_digest}\x00{guessed_window}".encode()
    ).hexdigest()[:24]

    assert guessed_net_token not in trace.ordered_net_tokens
    assert guessed_window_token not in trace.corridor_hint_tokens
    assert (
        trace.ordered_net_tokens == redacted_policy_trace(policy_input, decision).ordered_net_tokens
    )


def test_contracts_are_frozen_and_windows_are_only_coordinator_supplied_options() -> None:
    policy_input = _input()
    decision = evaluate_policy(DeterministicReferencePolicy(), policy_input)

    with pytest.raises(FrozenInstanceError):
        policy_input.board_revision = "sha256:" + "b" * 64  # type: ignore[misc]
    with pytest.raises(ValueError, match="not supplied"):
        redacted_policy_trace(
            policy_input,
            replace(
                decision,
                corridor_hints=(CorridorCandidate("net:clock", PolicyBounds(1, 1, 2, 2), 0, 0),),
            ),
        )
    with pytest.raises(ValueError, match="not supplied"):
        redacted_policy_trace(
            policy_input,
            replace(
                decision,
                repair_windows=(RepairWindowCandidate("net:clock", PolicyBounds(1, 1, 2, 2), 1),),
            ),
        )


@pytest.mark.parametrize(
    "payload",
    (
        json.dumps(
            {
                "schema": "copper-mcp.routing-policy-input.v1",
                "board_revision": "sha256:" + "a" * 64,
                "bounds": {"min_x": 0, "min_y": 0, "max_x": 1, "max_y": 1},
                "nets": [],
                "corridor_candidates": [],
                "repair_candidates": [],
                "copper": "no",
            }
        ),
        (
            '{"schema":"copper-mcp.routing-policy-input.v1",'
            '"board_revision":"sha256:'
            + "a"
            * 64
            + '","bounds":{"min_x":0,"min_y":0,"max_x":1,"max_y":1},'
            '"nets":[],"nets":[],"corridor_candidates":[],"repair_candidates":[]}'
        ),
        json.dumps(
            {
                "schema": "copper-mcp.routing-policy-input.v1",
                "board_revision": "sha256:" + "a" * 64,
                "bounds": {"min_x": 0, "min_y": 0, "max_x": 1, "max_y": 1},
                "nets": [
                    {
                        "net_id": "net:bad\x00",
                        "criticality": 1,
                        "demand_cells": 1,
                        "congestion_score": 1,
                    }
                ],
                "corridor_candidates": [],
                "repair_candidates": [],
            }
        ),
        json.dumps(
            {
                "schema": "copper-mcp.routing-policy-input.v1",
                "board_revision": "sha256:" + "a" * 64,
                "bounds": {"min_x": 0, "min_y": 0, "max_x": 1, "max_y": 1},
                "nets": [
                    {
                        "net_id": "net:ok",
                        "criticality": True,
                        "demand_cells": 1,
                        "congestion_score": 1,
                    }
                ],
                "corridor_candidates": [],
                "repair_candidates": [],
            }
        ),
    ),
)
def test_hostile_json_is_rejected_without_becoming_policy_input(payload: str) -> None:
    with pytest.raises(ValueError):
        decode_policy_input_json(payload)


def test_oversize_json_and_out_of_bounds_windows_fail_closed() -> None:
    with pytest.raises(ValueError, match="byte budget"):
        decode_policy_input_json(" " * 64_001)

    policy_input = _input()
    decision = evaluate_policy(DeterministicReferencePolicy(), policy_input)
    escaped = CorridorCandidate("net:clock", PolicyBounds(-1, 0, 1, 1), 1, 1)
    with pytest.raises(ValueError, match="not supplied"):
        redacted_policy_trace(policy_input, replace(decision, corridor_hints=(escaped,)))


def test_evaluate_policy_rejects_unbound_or_incomplete_advisory_output() -> None:
    policy_input = _input()

    class HostilePolicy:
        policy_id = "hostile-policy-v1"

        def propose(self, _policy_input: object) -> RoutingPolicyDecision:
            return RoutingPolicyDecision(
                policy_id=self.policy_id,
                input_digest="sha256:" + "0" * 64,
                net_order=("net:clock",),
            )

    with pytest.raises(ValueError, match="not bound"):
        evaluate_policy(HostilePolicy(), policy_input)


def test_evaluate_policy_fails_closed_when_a_policy_raises() -> None:
    policy_input = _input()

    class FailingPolicy:
        policy_id = "failing-policy-v1"

        def propose(self, _policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
            raise RuntimeError("untrusted policy failure")

    with pytest.raises(ValueError, match="failed to provide"):
        evaluate_policy(FailingPolicy(), policy_input)
