from __future__ import annotations

from types import MappingProxyType

import pytest

import copper_mcp.routing.congestion as congestion_module
from copper_mcp.routing.congestion import (
    ISOLATED_REFERENCE_POLICY_PROFILE,
    NegotiatedRoutingRequest,
    NegotiatedRoutingStatus,
    RepairNegotiatedRoutingResult,
    negotiate_routes,
)
from copper_mcp.routing.physical_clearance import (
    PhysicalClearanceFailure,
    PhysicalClearanceVerificationResult,
)
from copper_mcp.routing.policy import (
    REFERENCE_POLICY_ID,
    RoutingPolicyDecision,
    policy_input_digest,
)
from copper_mcp.routing.repair import RepairTransactionSettings
from scripts import exact_local_repair_gate_fixture as fixture


def _run_request() -> tuple[object, NegotiatedRoutingRequest]:
    snapshot = fixture.build_snapshot()
    return snapshot, NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=fixture.build_requests(snapshot),
        max_iterations=1,
    )


def _reject_then_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    original = congestion_module.verify_negotiated_physical_clearance
    calls = 0

    def rejection_then_recheck(
        *args: object, **kwargs: object
    ) -> PhysicalClearanceVerificationResult:
        nonlocal calls
        calls += 1
        if calls % 2 == 1:
            return PhysicalClearanceVerificationResult(
                pair_checks=1,
                failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION,
                violating_nets=("net:horizontal", "net:vertical"),
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(
        congestion_module, "verify_negotiated_physical_clearance", rejection_then_recheck
    )


def test_repair_transaction_only_publishes_after_complete_rejection_and_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, envelope = _run_request()
    _reject_then_delegate(monkeypatch)

    result = negotiate_routes(
        snapshot,
        envelope,
        repair_settings=RepairTransactionSettings(max_projection_cells=256),
    )
    assert isinstance(result, RepairNegotiatedRoutingResult)
    assert result.status is NegotiatedRoutingStatus.COMPLETED
    assert result.repair_evidence is not None
    assert result.repair_evidence.projection_obstacle_checks > 0
    assert result.repair_evidence.local_expanded_states > 0
    assert result.repair_evidence.validator_edge_checks > 0
    assert result.repair_evidence.validator_obstacle_checks > 0
    assert all(
        candidate.policy.startswith("negotiated-congestion-repair-v1-")
        for candidate in result.candidates
    )


def test_repair_policy_can_only_select_a_coordinator_supplied_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, envelope = _run_request()
    _reject_then_delegate(monkeypatch)

    class SelectLastSuppliedWindow:
        policy_id = REFERENCE_POLICY_ID

        def propose(self, policy_input: object) -> RoutingPolicyDecision:
            assert hasattr(policy_input, "nets")
            assert hasattr(policy_input, "repair_candidates")
            return RoutingPolicyDecision(
                policy_id=self.policy_id,
                input_digest=policy_input_digest(policy_input),  # type: ignore[arg-type]
                net_order=tuple(net.net_id for net in policy_input.nets),  # type: ignore[union-attr]
                repair_windows=(policy_input.repair_candidates[-1],),  # type: ignore[union-attr]
            )

    monkeypatch.setattr(
        congestion_module,
        "_POLICY_PROFILE_REGISTRY",
        MappingProxyType({"repair-select-test": SelectLastSuppliedWindow}),
    )
    monkeypatch.setattr(
        congestion_module,
        "_EXPECTED_POLICY_IDS",
        MappingProxyType({"repair-select-test": REFERENCE_POLICY_ID}),
    )
    result = negotiate_routes(
        snapshot,
        envelope,
        repair_settings=RepairTransactionSettings(max_projection_cells=256),
        repair_policy_profile="repair-select-test",
    )

    assert isinstance(result, RepairNegotiatedRoutingResult)
    assert result.repair_evidence is not None
    assert result.repair_evidence.repair_policy_decision_digest is not None


def test_repair_refusals_keep_the_rejected_allocation_and_evidence_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, envelope = _run_request()
    _reject_then_delegate(monkeypatch)

    def malformed_provenance(*_args: object, **_kwargs: object) -> object:
        raise ValueError("untrusted provenance")

    monkeypatch.setattr(congestion_module, "derive_repair_provenance", malformed_provenance)
    result = negotiate_routes(
        snapshot,
        envelope,
        repair_settings=RepairTransactionSettings(max_projection_cells=256),
    )

    assert result.status is not NegotiatedRoutingStatus.COMPLETED
    assert result.candidates == ()
    assert result.connections == ()
    assert not hasattr(result, "repair_evidence")

    isolated = negotiate_routes(
        snapshot,
        envelope,
        repair_settings=RepairTransactionSettings(max_projection_cells=256),
        repair_policy_profile=ISOLATED_REFERENCE_POLICY_PROFILE,
    )
    assert isolated.status is NegotiatedRoutingStatus.INVALID_REQUEST
    assert isolated.candidates == ()


def test_local_request_budget_refusal_is_atomic_after_successful_provenance_derivation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, envelope = _run_request()
    _reject_then_delegate(monkeypatch)

    # The 256-cell projection budget admits coordinator provenance, while the one-cell local
    # request ceiling refuses its derived window. This internal conversion must never escape as
    # an exception or publish a repaired/partial allocation.
    result = negotiate_routes(
        snapshot,
        envelope,
        repair_settings=RepairTransactionSettings(max_projection_cells=256, max_window_cells=1),
    )

    assert result.status is not NegotiatedRoutingStatus.COMPLETED
    assert result.candidates == ()
    assert result.connections == ()
    assert not hasattr(result, "repair_evidence")
