"""Historical and successor guards for the negotiated whole-corpus census.

B-124 is immutable evidence for the former two-pin, shared-world-origin contract.  It is verified
as history and is never compared with current code.  The successor artifact is separately pinned
evidence, while one module-scoped in-memory report checks that its metrics still match the current
2-to-32-pad, request-local-origin contract without comparing machine timing.

So the file is organised around six questions.

1. Does B-124 retain its exact self-digest, source commit, and historical 0-of-20 result?
2. Does the successor artifact retain its exact identity, source, predecessor, runner, measured
   headline, aggregate reconciliation, and redaction contract?
3. Does a fresh successor run freeze and enforce 16 admitted / four envelope-ineligible boards
   before measurement, without predicting a routing outcome, and reproduce the artifact metrics?
4. Does the pass-through recorder actually record, and actually change nothing?  Answered on the
   committed two-net KiCad crossing fixture, where the coordinator genuinely reaches its
   physical-clearance gate without relying on a successor corpus outcome.
5. Does every rung of the blocking-stage ladder distinguish the complete-allocation physical
   trigger from the presence of a selectable two-pin violating target?  Answered with constructed
   gate observations rather than predeclaring either corpus outcome.
6. Do the harness's own refusals fire?  A drifted B-088 baseline, a disagreeing admission
   predicate, and an observer that perturbs the result must each raise rather than be recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from copper_mcp.benchmarks.simple_route_json import import_simple_route_json
from copper_mcp.routing import (
    AStarRouter,
    NegotiatedRoutingStatus,
    RouteDiagnostic,
    RouteFailureCode,
    RouteResult,
)
from copper_mcp.routing import congestion as coordinator
from copper_mcp.routing.congestion import NegotiatedRoutingRequest, negotiate_routes
from copper_mcp.routing.physical_clearance import PhysicalClearanceFailure
from scripts import benchmark_negotiated_congestion as crossing
from scripts import benchmark_negotiated_corpus_census as census
from scripts import benchmark_simple_route_json_corpus as reference
from scripts.replay_source_binding import (
    _MAX_OUTPUT_BYTES,
    SourceBinding,
    capture_source_binding,
    verify_source_binding,
)
from tests.test_routing_congestion import _multipin_requests, _multipin_shifted_snapshot

LEGACY_ARTIFACT = census.LEGACY_ARTIFACT
SUCCESSOR_ARTIFACT = census.DEFAULT_OUTPUT
SUCCESSOR_SOURCE_COMMIT = "30692df496e0dc250d3b09bae5ad9b7b11a3d827"
SUCCESSOR_RUN_ID = "sha256:ef3724e6a58ba94df8a7e392a4e407029fb2720844fc5adcc4654cac8bbc3a31"
SUCCESSOR_RUNNER_SHA256 = "sha256:eb4339e5e2264c62a1971958af6a6d5d037d5e5703a3609561c7f5f607279774"
REFERENCE_RUNNER_SHA256 = "sha256:8fb5d05fb60a75b66e4720b3aa3ba9e0b28dbd8c3377ac159a239adbc4795fed"
CURRENT_CENSUS_SCHEMA = "copper-mcp/current-census-replay/v1"
CURRENT_CENSUS_KEYS = frozenset(
    {
        "python_version",
        "receipt_digest",
        "repetitions",
        "report",
        "schema",
        "source_inventory_digest",
        "source_inventory_files",
        "status",
    }
)
FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "_negotiated_result",
        "board",
        "board_revision",
        "boards",
        "candidate",
        "candidate_id",
        "candidate_ids",
        "coordinates",
        "document_sha256",
        "geometry",
        "negotiated",
        "paths",
        "segments",
        "submitted_net_ids",
        "two_pin_repair_eligible_violating_targets",
        "unrouted_nets",
        "vertices",
    }
)


def _legacy_artifact() -> dict[str, Any]:
    document = json.loads(LEGACY_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _successor_artifact() -> dict[str, Any]:
    document = json.loads(SUCCESSOR_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {item for child in value.values() for item in _nested_keys(child)}
    if isinstance(value, list):
        return {item for child in value for item in _nested_keys(child)}
    return set()


def _canonical_digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _render_test_envelope(binding: SourceBinding, report: object) -> bytes:
    receipt = {
        "schema": CURRENT_CENSUS_SCHEMA,
        "source_inventory_digest": binding.digest,
        "source_inventory_files": len(binding.entries),
        "python_version": platform.python_version(),
        "repetitions": 1,
        "status": "measured",
        "report": report,
    }
    return json.dumps(
        {**receipt, "receipt_digest": _canonical_digest(receipt)}, sort_keys=True
    ).encode()


def _closed_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_nonfinite_json_number(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def _validate_census_envelope(payload: bytes, binding: SourceBinding) -> dict[str, Any]:
    if len(payload) > _MAX_OUTPUT_BYTES:
        raise AssertionError("current census replay output exceeds its byte budget")
    try:
        envelope = json.loads(
            payload,
            object_pairs_hook=_closed_json_object,
            parse_constant=_reject_nonfinite_json_number,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise AssertionError("current census replay output is not one JSON document") from error
    if not isinstance(envelope, dict) or set(envelope) != CURRENT_CENSUS_KEYS:
        raise AssertionError("current census replay envelope is not closed")
    body = {key: value for key, value in envelope.items() if key != "receipt_digest"}
    if envelope["receipt_digest"] != _canonical_digest(body):
        raise AssertionError("current census replay self-digest does not match")
    if envelope["schema"] != CURRENT_CENSUS_SCHEMA or envelope["status"] != "measured":
        raise AssertionError("current census replay identity does not match")
    if (
        type(envelope["repetitions"]) is not int
        or envelope["repetitions"] != 1
        or envelope["python_version"] != platform.python_version()
    ):
        raise AssertionError("current census replay execution does not match")
    if (
        envelope["source_inventory_digest"] != binding.digest
        or type(envelope["source_inventory_files"]) is not int
        or envelope["source_inventory_files"] != len(binding.entries)
    ):
        raise AssertionError("current census replay source binding does not match")
    report = envelope["report"]
    if not isinstance(report, dict):
        raise AssertionError("current census replay report is not an object")
    return report


def _current_census_report() -> dict[str, Any]:
    before = capture_source_binding()
    script = census.ROOT / "scripts" / "replay_source_binding.py"
    environment = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"}
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and repository-owned script
        [sys.executable, "-I", str(script), "--census"],
        check=True,
        capture_output=True,
        timeout=3_600,
        env=environment,
    )
    verify_source_binding(before)
    return _validate_census_envelope(completed.stdout, before)


@pytest.fixture(scope="module")
def successor_report() -> dict[str, Any]:
    """Measure once in an isolated process; every current-contract assertion reuses it."""

    return _current_census_report()


def test_current_census_subprocess_is_fixed_isolated_and_has_a_minimal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = capture_source_binding()
    payload = _render_test_envelope(binding, {"fresh": True})
    captured: dict[str, Any] = {}

    def completed(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout=payload, stderr=b"")

    monkeypatch.setenv("COV_CORE_SOURCE", "must-not-reach-child")
    monkeypatch.setenv("PYTEST_ADDOPTS", "must-not-reach-child")
    monkeypatch.setenv("COPPER_MCP_ALLOW_APPLY", "must-not-reach-child")
    monkeypatch.setattr(subprocess, "run", completed)

    assert _current_census_report() == {"fresh": True}
    assert captured["command"] == [
        sys.executable,
        "-I",
        str(census.ROOT / "scripts" / "replay_source_binding.py"),
        "--census",
    ]
    assert captured["kwargs"] == {
        "check": True,
        "capture_output": True,
        "timeout": 3_600,
        "env": {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"},
    }


def test_parent_refuses_malformed_oversized_tampered_or_unbound_census_envelopes() -> None:
    binding = capture_source_binding()
    valid = _render_test_envelope(binding, {"fresh": True})

    with pytest.raises(AssertionError, match="not one JSON document"):
        _validate_census_envelope(b"not-json", binding)
    with pytest.raises(AssertionError, match="exceeds its byte budget"):
        _validate_census_envelope(b"x" * (_MAX_OUTPUT_BYTES + 1), binding)

    tampered = json.loads(valid)
    tampered["report"]["fresh"] = False
    with pytest.raises(AssertionError, match="self-digest"):
        _validate_census_envelope(json.dumps(tampered).encode(), binding)

    unbound = json.loads(valid)
    unbound["source_inventory_digest"] = "sha256:" + "0" * 64
    unbound_body = {key: value for key, value in unbound.items() if key != "receipt_digest"}
    unbound["receipt_digest"] = _canonical_digest(unbound_body)
    with pytest.raises(AssertionError, match="source binding"):
        _validate_census_envelope(json.dumps(unbound).encode(), binding)

    open_envelope = json.loads(valid)
    open_envelope["unexpected"] = True
    with pytest.raises(AssertionError, match="not closed"):
        _validate_census_envelope(json.dumps(open_envelope).encode(), binding)

    wrong_report = json.loads(valid)
    wrong_report["report"] = []
    wrong_body = {key: value for key, value in wrong_report.items() if key != "receipt_digest"}
    wrong_report["receipt_digest"] = _canonical_digest(wrong_body)
    with pytest.raises(AssertionError, match="report is not an object"):
        _validate_census_envelope(json.dumps(wrong_report).encode(), binding)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("repetitions", "execution does not match"),
        ("source_inventory_files", "source binding does not match"),
    ],
)
def test_parent_refuses_resigned_booleans_for_exact_integer_fields(
    field: str, message: str
) -> None:
    binding = SourceBinding((("synthetic.py", "0" * 64),))
    envelope = json.loads(_render_test_envelope(binding, {"fresh": True}))
    envelope[field] = True
    body = {key: value for key, value in envelope.items() if key != "receipt_digest"}
    envelope["receipt_digest"] = _canonical_digest(body)

    with pytest.raises(AssertionError, match=message):
        _validate_census_envelope(json.dumps(envelope).encode(), binding)


def test_parent_refuses_duplicate_json_members_before_digest_validation() -> None:
    binding = capture_source_binding()
    valid = _render_test_envelope(binding, {"fresh": True})
    duplicated = valid.replace(
        b'"status": "measured"',
        b'"status": "measured", "status": "measured"',
        1,
    )
    assert duplicated != valid

    with pytest.raises(AssertionError, match="not one JSON document"):
        _validate_census_envelope(duplicated, binding)


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_parent_refuses_nonfinite_json_numbers_before_digest_validation(constant: bytes) -> None:
    binding = capture_source_binding()
    valid = _render_test_envelope(binding, {"fresh": True})
    nonfinite = valid.replace(b'"fresh": true', b'"fresh": ' + constant, 1)
    assert nonfinite != valid

    with pytest.raises(AssertionError, match="not one JSON document"):
        _validate_census_envelope(nonfinite, binding)


def _observation(
    *,
    candidates: int,
    failure: str | None,
    violating_nets: int,
    two_pin_targets: int = 0,
) -> census.GateObservation:
    return census.GateObservation(
        candidates=candidates,
        failure=failure,
        violating_nets=violating_nets,
        two_pin_repair_eligible_violating_targets=two_pin_targets,
        pair_checks=1,
    )


# --------------------------------------------------------------------------------------------
# 1. Immutable B-124 history and the in-memory successor
# --------------------------------------------------------------------------------------------


def test_legacy_b124_keeps_its_exact_identity() -> None:
    report = _legacy_artifact()
    recorded = report.pop("run_id")

    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    assert recorded == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert recorded == "sha256:c72cf0a6061b90efb15cf7e61701e4caebd0e0baeb2311e6017c7ef43d6b5df2"
    assert report["schema"] == "copper-mcp/benchmark/negotiated-corpus-census/v1"
    assert report["source_commit"] == "7b6d7aa1cf40623d6d2e85fb75b615a6af46192c"


def test_legacy_b124_records_the_exact_historical_zero_of_twenty() -> None:
    report = _legacy_artifact()
    metrics = report["metrics"]
    primary = metrics["configurations"]["b088-routable"]

    assert metrics["headline"]["boards_offered"] == 20
    assert metrics["headline"]["boards_reaching_the_repair_precondition"] == 0
    assert primary["boards_admitted_by_the_coordinator"] == 0
    assert primary["negotiated_nets_completed"] == 0
    assert primary["first_unmet_conjunct_breakdown"] == {
        "at_least_two_requests": 4,
        "exactly_two_selected_layer_pads_per_net": 16,
        "none": 0,
        "one_selected_layer_and_grid_step": 0,
        "one_shared_world_grid": 0,
    }
    assert report["metrics"]["repair_settings_enabled"] is False
    assert report["configuration"]["repair_settings"] is None


def test_successor_paths_schema_and_prediction_are_disjoint_from_b124() -> None:
    assert census.LEGACY_ARTIFACT.name == "2026-08-20-negotiated-corpus-census-v1.json"
    assert census.DEFAULT_OUTPUT.name == "2026-08-29-negotiated-multipin-corpus-census-v1.json"
    assert census.DEFAULT_OUTPUT != census.LEGACY_ARTIFACT
    assert census.REPORT_SCHEMA == "copper-mcp/benchmark/negotiated-multipin-corpus-census/v1"
    assert census.REFERENCE_RUN_ID == (
        "sha256:facf95ee9770ffab8c1bc403a32a403e55ca79f2c56d1eabc6679eb6ec4dfca3"
    )
    assert census.PREDECLARED_PRIMARY_ADMISSION == {
        "configuration": "b088-routable",
        "population": "the per-board net sets routed by B-088's fixed-policy configuration",
        "boards_offered": 20,
        "boards_admitted_by_the_coordinator": 16,
        "boards_unable_to_form_a_two_request_envelope": 4,
        "routing_outcomes": "not_predicted",
    }
    assert census.BLOCKING_STAGES == (
        "envelope_construction",
        "coordinator_admission",
        "no_physical_gate_call",
        "no_clearance_violation",
        "clearance_violation_on_incomplete_allocation",
        "complete_allocation_clearance_violation_with_fewer_than_two_violating_nets",
        "complete_allocation_physical_clearance_trigger_without_two_pin_repair_eligible_target",
        "complete_allocation_physical_clearance_trigger_with_two_pin_repair_eligible_target",
    )


def test_successor_artifact_has_exact_identity_and_authorities() -> None:
    report = _successor_artifact()
    body = {key: value for key, value in report.items() if key != "run_id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    assert SUCCESSOR_ARTIFACT == (
        census.ROOT
        / "benchmarks/results/routing/2026-08-29-negotiated-multipin-corpus-census-v1.json"
    )
    assert report["schema"] == census.REPORT_SCHEMA
    assert report["source_commit"] == SUCCESSOR_SOURCE_COMMIT
    assert report["run_id"] == SUCCESSOR_RUN_ID
    assert report["run_id"] == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert report["historical_predecessor"] == {
        "benchmark": "B-124",
        "artifact": "benchmarks/results/routing/2026-08-20-negotiated-corpus-census-v1.json",
        "relationship": (
            "immutable evidence for the former exactly-two-pad, shared-world-origin contract; "
            "never replayed as current behavior"
        ),
    }
    assert report["metrics"]["reference_baseline"] == {
        "benchmark": "B-088",
        "artifact": "benchmarks/results/routing/2026-08-06-simple-route-json-corpus-v1.json",
        "artifact_run_id": census.REFERENCE_RUN_ID,
        "grid_policy": "fixed",
        "nets_routed": 70,
        "nets_attempted": 117,
    }
    assert report["predeclared_prediction"] == census.PREDECLARED_PRIMARY_ADMISSION
    assert report["configuration"]["runner_sha256"] == SUCCESSOR_RUNNER_SHA256
    assert report["configuration"]["reference_runner_sha256"] == REFERENCE_RUNNER_SHA256
    assert SUCCESSOR_RUNNER_SHA256 == _file_sha256(census.ROOT / census.SCRIPT_PATH)
    assert REFERENCE_RUNNER_SHA256 == _file_sha256(census.ROOT / census.REFERENCE_RUNNER_PATH)


def test_successor_artifact_has_exact_reconciled_redacted_measurement() -> None:
    report = _successor_artifact()
    metrics = report["metrics"]
    headline = metrics["headline"]
    configurations = metrics["configurations"]
    primary = configurations["b088-routable"]
    control = configurations["two-pad-control"]
    aggregate_keys = {
        "blocking_stage_breakdown",
        "boards_admitted_by_the_coordinator",
        "boards_imported",
        "boards_offered",
        "boards_reaching_complete_allocation_physical_clearance_trigger",
        "boards_with_a_constructible_envelope",
        "boards_with_a_two_pin_repair_eligible_violating_target",
        "configuration",
        "first_unmet_conjunct_breakdown",
        "negotiated_nets_completed",
        "nets_submitted",
        "physical_gate_calls",
        "reference_per_net_nets_routed",
        "submitted_nets_the_reference_routed",
        "terminal_status_breakdown",
    }

    assert headline == {
        "boards_offered": 20,
        "boards_admitted_by_the_coordinator": 16,
        "boards_unable_to_form_a_two_request_envelope": 4,
        "boards_reaching_complete_allocation_physical_clearance_trigger": 16,
        "boards_with_a_two_pin_repair_eligible_violating_target": 0,
        "negotiated_nets_completed": 0,
        "reference_per_net_nets_routed": 70,
        "physical_gate_calls": 128,
        "two_pad_nets_offered": 36,
        "two_pad_nets_the_reference_routed": 0,
    }
    assert metrics["premeasurement_admission_check"] == {
        "boards_offered": 20,
        "boards_admitted_by_the_coordinator": 16,
        "boards_unable_to_form_a_two_request_envelope": 4,
    }
    for measurement in configurations.values():
        assert set(measurement) == aggregate_keys
        assert sum(measurement["blocking_stage_breakdown"].values()) == 20
        assert sum(measurement["first_unmet_conjunct_breakdown"].values()) == 20
        assert sum(measurement["terminal_status_breakdown"].values()) == 20
        assert measurement["boards_reaching_complete_allocation_physical_clearance_trigger"] == sum(
            measurement["blocking_stage_breakdown"][stage]
            for stage in census.PHYSICAL_TRIGGER_STAGES
        )
        assert (
            measurement["boards_with_a_two_pin_repair_eligible_violating_target"]
            == (
                measurement["blocking_stage_breakdown"][census.PHYSICAL_TRIGGER_WITH_TWO_PIN_TARGET]
            )
        )
    for field in (
        "boards_offered",
        "boards_admitted_by_the_coordinator",
        "boards_reaching_complete_allocation_physical_clearance_trigger",
        "boards_with_a_two_pin_repair_eligible_violating_target",
        "negotiated_nets_completed",
        "reference_per_net_nets_routed",
        "physical_gate_calls",
    ):
        assert headline[field] == primary[field]
    assert (
        headline["boards_unable_to_form_a_two_request_envelope"]
        == primary["first_unmet_conjunct_breakdown"]["at_least_two_requests"]
    )
    assert headline["two_pad_nets_offered"] == control["nets_submitted"]
    assert (
        headline["two_pad_nets_the_reference_routed"]
        == control["submitted_nets_the_reference_routed"]
    )
    assert _nested_keys(report).isdisjoint(FORBIDDEN_PUBLIC_KEYS)


def test_successor_artifact_metrics_match_one_fresh_report(
    successor_report: dict[str, Any],
) -> None:
    # The isolated child returns the complete freshly built B-140 report. Timing, environment,
    # source commit, and the outer self-digest are run-specific; deterministic metrics are the
    # historical compatibility surface.
    artifact = _successor_artifact()

    assert set(successor_report) == set(artifact)
    assert artifact["metrics"] == successor_report["metrics"]


def test_current_admission_vocabulary_pins_2_and_32_without_a_shared_origin() -> None:
    names = tuple(name for name, _stage, _description in census.ADMISSION_CONJUNCTS)
    assert names == (
        "at_least_two_requests",
        "one_selected_layer_and_grid_step",
        "selected_layer_pad_count_between_2_and_32",
    )
    assert "one_shared_world_grid" not in names
    assert set(census.COORDINATOR_DIAGNOSTICS) == {"selected_layer_pad_count_between_2_and_32"}

    def submitted(count: int, *, layer: str = "layer:F.Cu") -> census.SubmittedNet:
        return census.SubmittedNet("net:test", layer, "routed", count)

    for count in (2, 32):
        held = census._admission(None, (submitted(count), submitted(count)))  # type: ignore[arg-type]
        assert held["selected_layer_pad_count_between_2_and_32"] is True
    for count in (1, 33):
        held = census._admission(None, (submitted(count), submitted(count)))  # type: ignore[arg-type]
        assert held["selected_layer_pad_count_between_2_and_32"] is False


@pytest.mark.parametrize(
    ("pad_count", "admitted"), ((2, True), (32, True), (1, False), (33, False))
)
def test_real_coordinator_enforces_the_pad_census_before_router_work(
    pad_count: int,
    admitted: bool,
) -> None:
    snapshot = _multipin_shifted_snapshot(pad_count)
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_multipin_requests(snapshot),
        max_iterations=1,
    )

    class RouterSpy:
        def __init__(self) -> None:
            self.calls = 0

        def propose(self, *_args: object, **_kwargs: object) -> RouteResult:
            self.calls += 1
            return RouteResult(
                diagnostic=RouteDiagnostic(RouteFailureCode.NO_PATH, "synthetic bounded refusal")
            )

    router = RouterSpy()
    result = negotiate_routes(snapshot, envelope, router=router)

    if admitted:
        assert router.calls > 0
        assert result.diagnostic != (
            "each negotiated net must expose 2 to 32 pads on the selected layer"
        )
    else:
        assert router.calls == 0
        assert result.status is NegotiatedRoutingStatus.INVALID_REQUEST
        assert result.diagnostic == (
            "each negotiated net must expose 2 to 32 pads on the selected layer"
        )


def test_successor_report_matches_only_the_predeclared_admission(
    successor_report: dict[str, Any],
) -> None:
    report = dict(successor_report)
    recorded = report.pop("run_id")
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    metrics = report["metrics"]
    primary = metrics["configurations"]["b088-routable"]

    assert recorded == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert report["schema"] == census.REPORT_SCHEMA
    assert report["predeclared_prediction"] == census.PREDECLARED_PRIMARY_ADMISSION
    assert metrics["premeasurement_admission_check"] == {
        "boards_offered": 20,
        "boards_admitted_by_the_coordinator": 16,
        "boards_unable_to_form_a_two_request_envelope": 4,
    }
    assert primary["boards_admitted_by_the_coordinator"] == 16
    assert primary["first_unmet_conjunct_breakdown"]["at_least_two_requests"] == 4
    assert (
        primary["first_unmet_conjunct_breakdown"]["selected_layer_pad_count_between_2_and_32"] == 0
    )
    assert "one_shared_world_grid" not in primary["first_unmet_conjunct_breakdown"]
    assert metrics["repair_settings_enabled"] is False
    assert report["configuration"]["repair_settings"] is None
    assert any("any predeclared routing outcome" in claim for claim in report["not_claimed"])
    # Actual routing outcomes are present because they were measured, but no value is asserted here.
    assert isinstance(metrics["headline"]["negotiated_nets_completed"], int)
    assert isinstance(
        metrics["headline"]["boards_reaching_complete_allocation_physical_clearance_trigger"],
        int,
    )
    assert isinstance(
        metrics["headline"]["boards_with_a_two_pin_repair_eligible_violating_target"],
        int,
    )
    assert "boards_reaching_the_repair_precondition" not in metrics["headline"]


def test_successor_report_contains_no_candidate_or_geometry_payload(
    successor_report: dict[str, Any],
) -> None:
    assert _nested_keys(successor_report).isdisjoint(FORBIDDEN_PUBLIC_KEYS)


def test_hostile_per_board_identifiers_and_values_remain_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_board = "board:HOSTILE_DO_NOT_PUBLISH"
    secret_net = "net:HOSTILE_DO_NOT_PUBLISH"
    secret_document = "document:HOSTILE_DO_NOT_PUBLISH"
    secret_revision = "revision:HOSTILE_DO_NOT_PUBLISH"
    secret_geometry = "geometry:HOSTILE_DO_NOT_PUBLISH"
    problem = SimpleNamespace(name=secret_board)
    submitted = (
        census.SubmittedNet(secret_net, "layer:F.Cu", "routed", 2),
        census.SubmittedNet(f"{secret_net}:second", "layer:F.Cu", "routed", 2),
    )
    private_record = {
        "board": secret_board,
        "document_sha256": secret_document,
        "board_revision": secret_revision,
        "submitted_net_ids": [secret_net],
        "geometry": secret_geometry,
        "blocking_stage": "no_physical_gate_call",
        "first_unmet_conjunct": None,
        "terminal_status": "partial",
        "physical_gate_calls": 0,
        "envelope_constructed": True,
        "negotiated": {"status": "partial", "unrouted_nets": [secret_net]},
        "_negotiated_result": SimpleNamespace(secret=secret_geometry),
    }

    monkeypatch.setattr(census, "import_simple_route_json", lambda *_args: problem)
    monkeypatch.setattr(census, "_solo_reference", lambda *_args: submitted)
    monkeypatch.setattr(census, "_assert_reference_authority", lambda *_args: 2)
    monkeypatch.setattr(census, "census_board", lambda *_args: dict(private_record))

    measurement = census.run_configuration(
        ((f"{secret_board}.json", secret_geometry.encode()),),
        census.PRIMARY,
        {},
    )
    private = json.dumps(measurement.replay_evidence, default=str)
    public = json.dumps(measurement.aggregate, sort_keys=True)

    assert all(
        value in private for value in (secret_board, secret_net, secret_document, secret_geometry)
    )
    assert all(
        value not in public
        for value in (secret_board, secret_net, secret_document, secret_revision, secret_geometry)
    )
    assert "boards" not in measurement.aggregate


# --------------------------------------------------------------------------------------------
# 2. The recorder is a real instrument
# --------------------------------------------------------------------------------------------


def _crossing_census_inputs() -> tuple[Any, tuple[census.SubmittedNet, ...], AStarRouter]:
    """Derive census inputs from the committed KiCad crossing fixture."""

    snapshot, requests, source = crossing._load_fixture()
    problem = SimpleNamespace(
        name="negotiated-crossing-v1",
        document_sha256=hashlib.sha256(source).hexdigest(),
        snapshot=snapshot,
        nets=tuple(
            SimpleNamespace(
                net_id=request.net_id,
                layer_id=request.layer_id,
                pad_count=sum(
                    pad.net_id == request.net_id and request.layer_id in pad.layer_ids
                    for pad in snapshot.content.pads
                ),
            )
            for request in requests
        ),
    )
    router = AStarRouter()
    return problem, census.PRIMARY.select(census._solo_reference(problem, router)), router


def test_the_recorder_observes_a_real_gate_call_and_changes_no_published_field() -> None:
    # A corpus result must not be the recorder's only positive control. The committed two-net
    # crossing fixture reaches the gate independently of whatever the successor measurement finds.
    snapshot, requests, _source = crossing._load_fixture()
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest, requests=requests, max_iterations=8
    )

    control = negotiate_routes(snapshot, envelope)
    with census.observed_physical_gate() as observations:
        instrumented = negotiate_routes(snapshot, envelope)

    assert observations, "the recorder saw no gate call on a fixture that reaches the gate"
    assert instrumented == control
    assert all(item.candidates == len(requests) for item in observations)
    assert all(item.pair_checks >= 0 for item in observations)


def test_crossing_fixture_is_admitted_and_preserves_unperturbed_census_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This only establishes the fixture's local census precondition and two-run parity; it does
    # not make a corpus or routing-outcome claim.
    problem, submitted, router = _crossing_census_inputs()
    calls = {"n": 0}
    honest = census.negotiate_routes

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return honest(*args, **kwargs)

    monkeypatch.setattr(census, "negotiate_routes", counting)
    record = census.census_board(problem, submitted, router)

    assert record["envelope_constructed"] is True
    assert record["terminal_status"] != "invalid_request"
    assert calls["n"] == 2


def test_the_recorder_counts_only_two_pin_candidates_named_by_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    two_pin_violating = SimpleNamespace(
        patch=SimpleNamespace(net_id="net:two-pin-violating"), pad_count=2
    )
    multipin_violating = SimpleNamespace(
        patch=SimpleNamespace(net_id="net:multipin-violating"), pad_count=3
    )
    two_pin_not_violating = SimpleNamespace(
        patch=SimpleNamespace(net_id="net:two-pin-clear"), pad_count=2
    )
    candidates = (two_pin_violating, multipin_violating, two_pin_not_violating)
    physical = SimpleNamespace(
        failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION,
        violating_nets=("net:two-pin-violating", "net:multipin-violating"),
        pair_checks=3,
    )
    calls = 0

    def physical_gate(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        assert args == ("snapshot", candidates)
        assert kwargs == {
            "layer_id": "layer:F.Cu",
            "max_pair_checks": 10,
            "cancelled": None,
        }
        return physical

    monkeypatch.setattr(coordinator, "verify_negotiated_physical_clearance", physical_gate)
    with census.observed_physical_gate() as observations:
        returned = coordinator.verify_negotiated_physical_clearance(
            "snapshot", candidates, layer_id="layer:F.Cu", max_pair_checks=10
        )

    assert returned is physical
    assert calls == 1
    assert observations == [
        census.GateObservation(
            candidates=3,
            failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value,
            violating_nets=2,
            two_pin_repair_eligible_violating_targets=1,
            pair_checks=3,
        )
    ]


def test_the_recorder_restores_the_coordinator_symbol_even_when_the_body_raises() -> None:
    original = coordinator.verify_negotiated_physical_clearance

    with pytest.raises(RuntimeError, match="deliberate"):
        with census.observed_physical_gate():
            assert coordinator.verify_negotiated_physical_clearance is not original
            raise RuntimeError("deliberate")

    assert coordinator.verify_negotiated_physical_clearance is original


def test_parity_refuses_a_change_to_a_semantic_field_omitted_from_the_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # policy_digest is deliberately absent from the redacted aggregate projection. Complete-object
    # parity must still catch it, or the recorder could alter provenance without changing a count.
    problem, submitted, router = _crossing_census_inputs()
    calls = {"n": 0}
    honest = census.negotiate_routes

    def perturbing(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        result = honest(*args, **kwargs)
        if calls["n"] == 1:
            return result
        digest = f"sha256:{'0' * 64}"
        if result.policy_digest == digest:
            digest = f"sha256:{'1' * 64}"
        changed = replace(result, policy_digest=digest)
        assert census._projection(changed) == census._projection(result)
        return changed

    monkeypatch.setattr(census, "negotiate_routes", perturbing)
    with pytest.raises(census.NegotiatedCensusError, match="complete immutable"):
        census.census_board(problem, submitted, router)
    assert calls["n"] == 2


# --------------------------------------------------------------------------------------------
# 3. Every rung of the ladder responds to input
# --------------------------------------------------------------------------------------------


def test_complete_allocation_physical_trigger_without_two_pin_target_is_distinct() -> None:
    reached = _observation(
        candidates=4,
        failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value,
        violating_nets=2,
        two_pin_targets=0,
    )

    assert (
        census._stage_from_observations((reached,), submitted=4, connectable=0)
        == census.PHYSICAL_TRIGGER_WITHOUT_TWO_PIN_TARGET
    )


def test_complete_allocation_physical_trigger_with_two_pin_target_is_distinct() -> None:
    reached = _observation(
        candidates=4,
        failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value,
        violating_nets=2,
        two_pin_targets=1,
    )

    assert (
        census._stage_from_observations((reached,), submitted=4, connectable=0)
        == census.PHYSICAL_TRIGGER_WITH_TWO_PIN_TARGET
    )


@pytest.mark.parametrize(
    ("observations", "submitted", "expected"),
    [
        ((), 4, "no_physical_gate_call"),
        (
            (_observation(candidates=4, failure=None, violating_nets=0),),
            4,
            "no_clearance_violation",
        ),
        (
            (
                _observation(
                    candidates=4,
                    failure=PhysicalClearanceFailure.BUDGET_EXHAUSTED.value,
                    violating_nets=0,
                ),
            ),
            4,
            "no_clearance_violation",
        ),
        (
            (
                _observation(
                    candidates=3,
                    failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value,
                    violating_nets=2,
                ),
            ),
            4,
            "clearance_violation_on_incomplete_allocation",
        ),
        (
            (
                _observation(
                    candidates=4,
                    failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value,
                    violating_nets=1,
                ),
            ),
            4,
            "complete_allocation_clearance_violation_with_fewer_than_two_violating_nets",
        ),
    ],
)
def test_each_earlier_rung_is_reported_for_the_input_that_stops_there(
    observations: tuple[census.GateObservation, ...], submitted: int, expected: str
) -> None:
    assert (
        census._stage_from_observations(observations, submitted=submitted, connectable=0)
        == expected
    )


def test_the_latest_rung_any_iteration_reached_is_the_one_recorded() -> None:
    # A run can fail the gate several times. The census must record the furthest a run got, not the
    # first or last thing that happened to it.
    early = _observation(
        candidates=2, failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value, violating_nets=2
    )
    late = _observation(
        candidates=4,
        failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value,
        violating_nets=2,
        two_pin_targets=1,
    )

    assert (
        census._stage_from_observations((early, late), submitted=4, connectable=0)
        == census.PHYSICAL_TRIGGER_WITH_TWO_PIN_TARGET
    )
    assert (
        census._stage_from_observations((late, early), submitted=4, connectable=0)
        == census.PHYSICAL_TRIGGER_WITH_TWO_PIN_TARGET
    )


def test_an_already_connected_net_counts_toward_a_complete_allocation() -> None:
    # The recorder sees candidates but not connections, so the census bounds connections with the
    # solo reference's already_connected count. The bound must be able to *complete* an allocation,
    # or the completeness conjunct could never hold on a board carrying pre-existing copper.
    partial = _observation(
        candidates=3, failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION.value, violating_nets=2
    )

    assert (
        census._stage_from_observations((partial,), submitted=4, connectable=0)
        == "clearance_violation_on_incomplete_allocation"
    )
    assert (
        census._stage_from_observations((partial,), submitted=4, connectable=1)
        == census.PHYSICAL_TRIGGER_WITHOUT_TWO_PIN_TARGET
    )


# --------------------------------------------------------------------------------------------
# 4. The harness's own refusals
# --------------------------------------------------------------------------------------------


def test_a_tampered_reference_artifact_is_refused(tmp_path: Path) -> None:
    recorded = json.loads(census.REFERENCE_ARTIFACT.read_text(encoding="utf-8"))
    recorded["metrics"]["configurations"]["fixed"]["nets_routed"] += 1
    tampered = tmp_path / "b088.json"
    tampered.write_text(json.dumps(recorded), encoding="utf-8")

    with pytest.raises(census.NegotiatedCensusError, match="self-digest"):
        census.load_reference_artifact(tampered)


def test_a_self_digest_valid_rewritten_root_stops_before_import_or_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = json.loads(census.REFERENCE_ARTIFACT.read_text(encoding="utf-8"))
    document["source_commit"] = "f" * 40
    body = {key: value for key, value in document.items() if key != "run_id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    document["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    rewritten = tmp_path / "rewritten-b088-root.json"
    rewritten.write_text(json.dumps(document), encoding="utf-8")
    original_loader = census.load_reference_artifact
    calls = {"imports": 0, "routes": 0}

    def forbidden_import(*_args: Any, **_kwargs: Any) -> Any:
        calls["imports"] += 1
        raise AssertionError("corpus import must not start")

    def forbidden_route(*_args: Any, **_kwargs: Any) -> Any:
        calls["routes"] += 1
        raise AssertionError("routing must not start")

    assert document["run_id"] != census.REFERENCE_RUN_ID
    monkeypatch.setattr(census, "load_reference_artifact", lambda: original_loader(rewritten))
    monkeypatch.setattr(census, "import_simple_route_json", forbidden_import)
    monkeypatch.setattr(census, "_solo_reference", forbidden_route)

    with pytest.raises(census.NegotiatedCensusError, match="pinned B-088 root"):
        census.run_census(repetitions=1)
    assert calls == {"imports": 0, "routes": 0}


def test_internal_per_board_authority_refuses_a_same_count_candidate_membership_mutation() -> None:
    # Bypass only the pinned-root loader to unit-test its secondary per-board defence directly:
    # changing the candidate commitment while preserving routed counts must still fail authority.
    document = json.loads(census.REFERENCE_ARTIFACT.read_text(encoding="utf-8"))
    boards = document["metrics"]["configurations"]["fixed"]["boards"]
    board = next(item for item in boards if item["outcomes"].get("routed") == 1)
    routed_count = board["outcomes"]["routed"]
    board["candidate_digest"] = "0" * 64
    body = {key: value for key, value in document.items() if key != "run_id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    document["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()

    assert document["run_id"] != census.REFERENCE_RUN_ID
    authority = census._reference_authority_by_board(document)
    _manifest, samples = reference.load_corpus()
    selected = tuple(item for item in samples if Path(item[0]).stem == board["board"])
    problem = import_simple_route_json(Path(selected[0][0]).stem, selected[0][1])
    submitted = census._solo_reference(problem, AStarRouter())

    assert board["outcomes"]["routed"] == routed_count
    assert len(selected) == 1
    with pytest.raises(census.NegotiatedCensusError, match="committed B-088 authority"):
        census._assert_reference_authority(problem, submitted, authority.get(problem.name))


def test_a_coordinator_refusal_without_an_unmet_conjunct_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _manifest, samples = reference.load_corpus()
    name, payload = next(item for item in samples if item[0].startswith("ts18"))
    problem = import_simple_route_json(Path(name).stem, payload)
    router = AStarRouter()
    submitted = census.PRIMARY.select(census._solo_reference(problem, router))
    false_refusal = coordinator._invalid_result(
        census.COORDINATOR_DIAGNOSTICS["selected_layer_pad_count_between_2_and_32"],
        board_revision=problem.snapshot.snapshot_digest,
    )
    monkeypatch.setattr(census, "negotiate_routes", lambda *_args, **_kwargs: false_refusal)

    with pytest.raises(census.NegotiatedCensusError, match="does not match"):
        census.census_board(problem, submitted, router)


def test_a_coordinator_refusal_with_the_wrong_diagnostic_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _manifest, samples = reference.load_corpus()
    name, payload = next(item for item in samples if item[0].startswith("ts18"))
    problem = import_simple_route_json(Path(name).stem, payload)
    router = AStarRouter()
    submitted = census.PRIMARY.select(census._solo_reference(problem, router))
    false_refusal = {name: True for name, _stage, _text in census.ADMISSION_CONJUNCTS}
    false_refusal["selected_layer_pad_count_between_2_and_32"] = False
    wrong_diagnostic = coordinator._invalid_result(
        "synthetic wrong admission diagnostic",
        board_revision=problem.snapshot.snapshot_digest,
    )
    monkeypatch.setattr(census, "_admission", lambda *_args, **_kwargs: false_refusal)
    monkeypatch.setattr(census, "negotiate_routes", lambda *_args, **_kwargs: wrong_diagnostic)

    with pytest.raises(census.NegotiatedCensusError, match="does not match"):
        census.census_board(problem, submitted, router)


def test_an_admission_predicate_that_disagrees_with_the_coordinator_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The independent predicate is only a cross-check if a disagreement is fatal. Here it falsely
    # refuses a board that the current coordinator admits.
    problem, submitted, router = _crossing_census_inputs()
    false_refusal = {name: True for name, _s, _d in census.ADMISSION_CONJUNCTS}
    false_refusal["selected_layer_pad_count_between_2_and_32"] = False
    monkeypatch.setattr(census, "_admission", lambda *_args, **_kwargs: false_refusal)

    with pytest.raises(census.NegotiatedCensusError, match="computed unmet but the coordinator"):
        census.census_board(problem, submitted, router)


def test_a_drifted_predeclared_partition_stops_before_negotiated_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drifted = {**census.PREDECLARED_PRIMARY_ADMISSION, "boards_admitted_by_the_coordinator": 15}
    measurement_calls = 0

    def forbidden_measurement(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal measurement_calls
        measurement_calls += 1
        raise AssertionError("negotiated measurement started before the prediction was checked")

    monkeypatch.setattr(census, "PREDECLARED_PRIMARY_ADMISSION", drifted)
    monkeypatch.setattr(census, "run_configuration", forbidden_measurement)

    with pytest.raises(census.NegotiatedCensusError, match="predeclared prediction"):
        census.run_census(repetitions=1)
    assert measurement_calls == 0


def test_successor_configuration_is_closed_reconciled_aggregate_only(
    successor_report: dict[str, Any],
) -> None:
    primary = successor_report["metrics"]["configurations"]["b088-routable"]

    assert "boards" not in primary
    assert sum(primary["blocking_stage_breakdown"].values()) == primary["boards_imported"]
    assert sum(primary["first_unmet_conjunct_breakdown"].values()) == primary["boards_imported"]
    assert sum(primary["terminal_status_breakdown"].values()) == primary["boards_imported"]
    assert primary["boards_with_a_constructible_envelope"] == 16
    assert primary["boards_admitted_by_the_coordinator"] == 16
    physical_trigger_stages = census.PHYSICAL_TRIGGER_STAGES
    assert primary["boards_reaching_complete_allocation_physical_clearance_trigger"] == sum(
        primary["blocking_stage_breakdown"][stage] for stage in physical_trigger_stages
    )
    assert (
        primary["boards_with_a_two_pin_repair_eligible_violating_target"]
        == primary["blocking_stage_breakdown"][census.PHYSICAL_TRIGGER_WITH_TWO_PIN_TARGET]
    )
    assert (
        primary["boards_with_a_two_pin_repair_eligible_violating_target"]
        <= primary["boards_reaching_complete_allocation_physical_clearance_trigger"]
    )


def test_a_nondeterministic_configuration_replay_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def drifting(*args: Any, **kwargs: Any) -> census.ConfigurationMeasurement:
        calls["n"] += 1
        return census.ConfigurationMeasurement(
            aggregate={"replay": calls["n"]},
            replay_evidence=(),
        )

    monkeypatch.setattr(
        census,
        "_preflight_primary_admission",
        lambda *_args, **_kwargs: {
            "boards_offered": 20,
            "boards_admitted_by_the_coordinator": 16,
            "boards_unable_to_form_a_two_request_envelope": 4,
        },
    )
    monkeypatch.setattr(census, "run_configuration", drifting)

    with pytest.raises(census.NegotiatedCensusError, match="replay diverged"):
        census.run_census(repetitions=2)


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (("unknown", False), "known Git revision"),
        (("a" * 40, True), "clean Git worktree"),
    ],
)
def test_write_refuses_unknown_or_dirty_git_before_measurement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: tuple[str, bool],
    message: str,
) -> None:
    output = tmp_path / "new.json"
    measurement_calls = 0

    def forbidden_measurement(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal measurement_calls
        measurement_calls += 1
        raise AssertionError("measurement must not start")

    monkeypatch.setattr(
        sys, "argv", [str(Path(census.__file__)), "--write", "--output", str(output)]
    )
    monkeypatch.setattr(census, "_git_state", lambda: state)
    monkeypatch.setattr(census, "build_report", forbidden_measurement)

    with pytest.raises(census.NegotiatedCensusError, match=message):
        census.main()
    assert measurement_calls == 0
    assert not output.exists()


@pytest.mark.parametrize("protected", (census.LEGACY_ARTIFACT, census.REFERENCE_ARTIFACT))
def test_write_protects_historical_authority_targets_before_measurement(
    protected: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    measurement_calls = 0

    def forbidden_measurement(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal measurement_calls
        measurement_calls += 1
        raise AssertionError("measurement must not start")

    monkeypatch.setattr(
        sys,
        "argv",
        [str(Path(census.__file__)), "--write", "--output", str(protected)],
    )
    monkeypatch.setattr(census, "build_report", forbidden_measurement)

    with pytest.raises(census.NegotiatedCensusError, match="protected historical authority"):
        census.main()
    assert measurement_calls == 0


def test_write_refuses_an_existing_target_without_touching_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "existing.json"
    output.write_text("sentinel", encoding="utf-8")
    measurement_calls = 0

    def forbidden_measurement(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal measurement_calls
        measurement_calls += 1
        raise AssertionError("measurement must not start")

    monkeypatch.setattr(
        sys, "argv", [str(Path(census.__file__)), "--write", "--output", str(output)]
    )
    monkeypatch.setattr(census, "build_report", forbidden_measurement)

    with pytest.raises(census.NegotiatedCensusError, match="new path"):
        census.main()
    assert measurement_calls == 0
    assert output.read_text(encoding="utf-8") == "sentinel"


@pytest.mark.parametrize(
    ("final_state", "message"),
    [
        (("a" * 40, True), "clean Git worktree"),
        (("unknown", False), "known Git revision"),
        (("b" * 40, False), "changed during benchmark"),
    ],
)
def test_write_rechecks_git_immediately_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    final_state: tuple[str, bool],
    message: str,
) -> None:
    output = tmp_path / "new.json"
    captured = "a" * 40
    states = iter(((captured, False), final_state))

    def cheap_report(
        _repetitions: int,
        _corpus: Path,
        *,
        source_commit: str | None,
    ) -> dict[str, Any]:
        assert source_commit == captured
        return {"schema": census.REPORT_SCHEMA, "source_commit": source_commit}

    monkeypatch.setattr(
        sys, "argv", [str(Path(census.__file__)), "--write", "--output", str(output)]
    )
    monkeypatch.setattr(census, "_git_state", lambda: next(states))
    monkeypatch.setattr(census, "build_report", cheap_report)

    with pytest.raises(census.NegotiatedCensusError, match=message):
        census.main()
    assert not output.exists()


def test_write_records_the_premeasurement_commit_and_creates_exclusively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "new.json"
    captured = "c" * 40
    seen: list[str | None] = []

    def cheap_report(
        _repetitions: int,
        _corpus: Path,
        *,
        source_commit: str | None,
    ) -> dict[str, Any]:
        seen.append(source_commit)
        return {"schema": census.REPORT_SCHEMA, "source_commit": source_commit}

    monkeypatch.setattr(
        sys, "argv", [str(Path(census.__file__)), "--write", "--output", str(output)]
    )
    monkeypatch.setattr(census, "_git_state", lambda: (captured, False))
    monkeypatch.setattr(census, "build_report", cheap_report)

    assert census.main() == 0
    assert seen == [captured]
    assert json.loads(output.read_text(encoding="utf-8"))["source_commit"] == captured

    with pytest.raises(census.NegotiatedCensusError, match="new path"):
        census.main()
