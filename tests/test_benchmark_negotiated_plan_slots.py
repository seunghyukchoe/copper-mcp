"""Bind the committed B-087 artifact to the harness that produced it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import benchmark_negotiated_plan_slots as benchmark

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "benchmarks" / "results" / "routing" / "2026-08-06-negotiated-plan-slots.json"
TEST_EVIDENCE_HARNESS_COMMIT = "f" * 40
BASELINE = "no-plan-baseline"


def _artifact() -> dict[str, object]:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_the_committed_artifact_is_bound_to_its_own_script_and_run_identity() -> None:
    report = _artifact()
    script = ROOT / str(report["script"])

    assert report["schema"] == "copper-mcp/benchmark/negotiated-plan-slots/v1"
    assert report["script_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()
    recomputed = {key: value for key, value in report.items() if key != "run_id"}
    assert report["run_id"] == benchmark._digest(recomputed)


def test_the_harness_reproduces_the_committed_measurements() -> None:
    committed = _artifact()
    fresh = benchmark.build_report(evidence_harness_commit=TEST_EVIDENCE_HARNESS_COMMIT)

    assert [case["fixture"] for case in fresh["cases"]] == [
        "crossing-neutral-control",
        "congested-channel-negotiating",
        "congested-channel-first-pass",
    ]
    assert fresh["cases"] == committed["cases"]
    assert fresh["observations"] == committed["observations"]


def test_every_declared_plan_replays_deterministically_and_publishes_slot_digests() -> None:
    report = _artifact()

    declared = {item["name"] for item in report["declared_plans"]}
    for case in report["cases"]:
        assert case["replay_count"] >= benchmark.REPLAY_MINIMUM
        assert case["replay_deterministic"] is True
        assert set(case["runs"]) == declared
        baseline = case["runs"][BASELINE]
        assert baseline["plan_digest"] is None
        assert baseline["net_order_slot_digest"] is None
        for name, run in case["runs"].items():
            if name == BASELINE:
                continue
            slots = (
                run["net_order_slot_digest"],
                run["cost_update_slot_digest"],
                run["rip_up_slot_digest"],
            )
            assert all(isinstance(digest, str) for digest in slots)
            assert len(set(slots)) == 3
            assert run["plan_digest"] != run["plan_composite_digest"]
            # The coordinator is single-layer by contract, so this is structurally zero.
            assert run["total_vias"] == 0


def test_the_artifact_records_a_no_quality_claim_and_its_non_claims() -> None:
    report = _artifact()

    assert report["claim"]["quality_claim"] is False
    assert report["claim"]["classification"] == "exploratory sweep / no quality claim"
    assert report["kicad_drc"] == "not_run"
    assert report["apply"] == "not_invoked"
    assert (
        "via counts are structurally zero because the coordinator is single-layer"
        in (report["non_claims"])
    )


def test_the_recorded_congested_fixture_shows_both_a_gain_and_a_regression() -> None:
    """The ledger reports negative results, so the artifact must actually contain them."""

    report = _artifact()
    congested = next(
        case for case in report["cases"] if case["fixture"] == "congested-channel-negotiating"
    )
    baseline = congested["runs"][BASELINE]

    assert baseline["status"] == "completed"
    assert (baseline["iterations"], baseline["router_call_count"]) == (5, 30)
    assert baseline["total_wire_length_nm"] == 56_000_000

    faster = congested["runs"]["net-order/demand-ascending"]
    assert faster["status"] == "completed"
    assert faster["iterations"] == 1
    assert faster["router_call_count"] == 6
    assert faster["total_wire_length_nm"] == 54_000_000

    # Partial rip-up does not converge on this fixture; the ledger records that, not a win.
    assert congested["runs"]["rip-up/conflicted-only"]["status"] == "no_path"
    assert congested["runs"]["cost-update/saturating-decay-half"]["status"] == "no_path"
    # A slot can also converge and still cost more copper.
    scaled = congested["runs"]["cost-update/scaled-accumulation-4"]
    assert scaled["status"] == "completed"
    assert scaled["total_wire_length_nm"] > baseline["total_wire_length_nm"]
