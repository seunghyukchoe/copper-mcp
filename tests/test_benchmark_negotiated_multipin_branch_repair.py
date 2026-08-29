"""Focused contract tests for the B-141 negotiated multi-pin differential.

The B-141 runner deliberately keeps board-level evidence private.  These tests therefore use two
levels of evidence: a fresh report/artifact comparison for the published contract, and small
in-memory seams for the control/treatment reachability and replay guards.  In particular, the
zero-repair corpus outcome is not treated as a positive repair result; a treatment sentinel proves
that the repair entry point is reachable independently of whether the committed corpus publishes
repair evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.routing import NegotiatedRoutingStatus
from copper_mcp.routing import congestion as coordinator
from copper_mcp.routing.congestion import NegotiatedRoutingResult
from copper_mcp.routing.repair import RepairTransactionSettings
from scripts import benchmark_negotiated_multipin_branch_repair as benchmark

EXPECTED_ARTIFACT = (
    benchmark.ROOT
    / "benchmarks/results/routing/2026-08-30-negotiated-multipin-branch-repair-v1.json"
)
EXPECTED_POPULATION = {
    "boards_offered": 20,
    "boards_imported": 20,
    "boards_with_a_constructible_envelope": 16,
    "boards_admitted_by_the_coordinator": 16,
    "boards_unable_to_form_a_two_request_envelope": 4,
    "nets_submitted": 70,
    "submitted_nets_the_reference_routed": 70,
    "reference_per_net_nets_routed": 70,
}
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
EXPECTED_REPAIR_WORK_DEFINITION = (
    "Inherited search and proximity fields are read from a published repaired candidate's "
    "unchanged candidate metrics and cost. Local expansions, Board-IR projection checks, "
    "validator checks, and repair responsibility/final physical work are separate. "
    "Refused transactions publish no repair evidence; their consumed physical work remains "
    "in total_physical_checks and is not fabricated into success-only fields."
)
EXPECTED_DIFFERENTIAL_DEFINITION = (
    "Treatment minus control over the same immutable B-140 snapshots and request tuples. "
    "A positive completion delta is a measured differential only; it is not a "
    "routing-quality, electrical, DRC, fabrication, or generalisation claim."
)
EXPECTED_NOT_CLAIMED = [
    "that repair was successful when repair evidence was not published",
    "that a zero or negative differential is evidence that the repair contract is ineffective",
    "that control and treatment answer a like-for-like quality question against B-088's "
    "independent per-net routes",
    "KiCad DRC, electrical correctness, signal integrity, thermal behaviour, DFM, fabrication, "
    "apply, editor, hardware, or network behaviour",
    "any board, net, revision, candidate, path, geometry, or private corpus identity",
    "generalisation beyond the exact committed 20-board B-088 subset",
]


def _canonical_digest(value: object) -> str:
    rendered = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(rendered).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in _nested_keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in _nested_keys(child)}
    return set()


def _artifact() -> dict[str, Any]:
    document = json.loads(EXPECTED_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _commitment() -> dict[str, Any]:
    document = json.loads(benchmark.COMMITMENT_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _retag_document(document: dict[str, Any]) -> dict[str, Any]:
    """Return a self-consistent rewrite so semantic checks, not the outer digest, are exercised."""

    body = {key: value for key, value in document.items() if key != "run_id"}
    document["run_id"] = _canonical_digest(body)
    return document


def _retag_commitment(document: dict[str, Any]) -> dict[str, Any]:
    """Re-sign a sidecar mutation so the external binding, not JSON syntax, is exercised."""

    body = {key: value for key, value in document.items() if key != "run_id"}
    document["run_id"] = _canonical_digest(body)
    return document


def _reconcile_differential(document: dict[str, Any]) -> dict[str, Any]:
    """Compute the public deltas independently of the runner's validator implementation."""

    control = document["metrics"]["control"]
    treatment = document["metrics"]["treatment"]
    control_nets = control["negotiated_nets_completed"]
    treatment_nets = treatment["negotiated_nets_completed"]
    document["metrics"]["differential"] = {
        "boards_completed_delta": treatment["boards_completed"] - control["boards_completed"],
        "negotiated_nets_completed_delta": treatment_nets - control_nets,
        "total_wire_length_nm_delta": (
            treatment["total_wire_length_nm"] - control["total_wire_length_nm"]
        ),
        "total_overflow_units_delta": (
            treatment["total_overflow_units"] - control["total_overflow_units"]
        ),
        "total_physical_checks_delta": (
            treatment["total_physical_checks"] - control["total_physical_checks"]
        ),
        "positive_completion_delta": treatment_nets > control_nets,
        "verdict": (
            "positive_completion_delta"
            if treatment_nets > control_nets
            else "zero_or_negative_completion_delta"
        ),
    }
    return _retag_document(document)


def _zero_completed_treatment(document: dict[str, Any]) -> dict[str, Any]:
    """Turn treatment into a coherent zero-completion report without changing its population."""

    treatment = document["metrics"]["treatment"]
    outcomes = treatment["outcome_breakdown"]
    refusals = treatment["refusal_breakdown"]
    repairs = treatment["repair_outcome_breakdown"]
    statuses = treatment["status_breakdown"]

    outcomes["completed_with_repair"] = 0
    outcomes["completed_without_repair"] = 0
    outcomes["no_path_physical_clearance"] += 1
    refusals["no_path_physical_clearance"] += 1
    repairs["repair_published"] = 0
    repairs["repair_not_published"] += 1
    statuses["completed"] = 0
    statuses["no_path"] = statuses.get("no_path", 0) + 1
    treatment["boards_completed"] = 0
    treatment["negotiated_nets_completed"] = 0
    for key in treatment["repair_work"]:
        treatment["repair_work"][key] = 0
    return _reconcile_differential(document)


@pytest.fixture(scope="module")
def prepared_population() -> tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]]:
    """Freeze the exact B-140 population once for the seam tests."""

    return benchmark.prepare_population()


@pytest.fixture(scope="module")
def fresh_report() -> dict[str, Any]:
    """Run two repetitions; timing is intentionally not part of parity assertions."""

    return benchmark.build_report(repetitions=2)


def _admitted_board(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
) -> benchmark.PreparedBoard:
    prepared, _population = prepared_population
    return next(board for board in prepared if board.envelope is not None)


def _ts18_board(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
) -> benchmark.PreparedBoard:
    prepared, _population = prepared_population
    return next(
        board
        for board in prepared
        if board.envelope is not None and board.problem.name.startswith("ts18")
    )


def _minimal_aggregate(population: dict[str, Any]) -> dict[str, Any]:
    """Build the smallest aggregate accepted after a mocked differential measurement."""

    zero_counts = dict.fromkeys(benchmark.RUN_OUTCOME_TAXONOMY, 0)
    zero_refusals = dict.fromkeys(benchmark.REFUSAL_TAXONOMY, 0)
    zero_repairs = dict.fromkeys(benchmark.REPAIR_OUTCOME_TAXONOMY, 0)
    return {
        **population,
        "repair_enabled": False,
        "repair_settings": None,
        "boards_completed": 0,
        "negotiated_nets_completed": 0,
        "total_wire_length_nm": 0,
        "total_overflow_units": 0,
        "total_physical_checks": 0,
        "total_iterations": 0,
        "total_ripups": 0,
        "outcome_breakdown": zero_counts,
        "refusal_breakdown": zero_refusals,
        "repair_outcome_breakdown": zero_repairs,
        "status_breakdown": {"not_run": population["boards_offered"]},
        "repair_work": {
            "published_repairs": 0,
            "inherited_search_expansions": 0,
            "inherited_search_obstacle_checks": 0,
            "inherited_proximity_steps": 0,
            "inherited_proximity_cost_nm": 0,
            "repair_local_expanded_states": 0,
            "repair_projection_obstacle_checks": 0,
            "repair_validator_edge_checks": 0,
            "repair_validator_obstacle_checks": 0,
        },
        "repair_work_accounting": {
            "refusal_work_in_total_physical_checks": True,
            "successful_repair_evidence_only": True,
            "unpublished_local_projection_and_validator_work": (
                "not exposed by the closed result on refusal"
            ),
        },
    }


def _synthetic_public_report() -> dict[str, Any]:
    """Build a complete, self-resigned report without reading the committed B-141 artifact."""

    population = dict(EXPECTED_POPULATION)
    control = _minimal_aggregate(population)
    control["outcome_breakdown"]["envelope_construction"] = 4
    control["outcome_breakdown"]["no_path_physical_clearance"] = 16
    control["refusal_breakdown"]["envelope_construction"] = 4
    control["refusal_breakdown"]["no_path_physical_clearance"] = 16
    control["repair_outcome_breakdown"]["not_applicable_envelope_refused"] = 4
    control["repair_outcome_breakdown"]["repair_not_published"] = 16
    control["status_breakdown"] = {"no_path": 16, "not_run": 4}

    treatment = json.loads(json.dumps(control))
    treatment["repair_enabled"] = True
    treatment["repair_settings"] = asdict(RepairTransactionSettings())
    treatment["outcome_breakdown"]["no_path_physical_clearance"] = 15
    treatment["outcome_breakdown"]["completed_with_repair"] = 1
    treatment["refusal_breakdown"]["no_path_physical_clearance"] = 15
    treatment["repair_outcome_breakdown"]["repair_not_published"] = 15
    treatment["repair_outcome_breakdown"]["repair_published"] = 1
    treatment["status_breakdown"] = {"completed": 1, "no_path": 15, "not_run": 4}
    treatment["boards_completed"] = 1
    treatment["negotiated_nets_completed"] = 2
    treatment["total_physical_checks"] = 1
    treatment["repair_work"]["published_repairs"] = 1
    treatment["repair_work"]["repair_local_expanded_states"] = 1

    binding = {
        "benchmark": "B-140",
        "artifact": benchmark.B140_ARTIFACT.relative_to(benchmark.ROOT).as_posix(),
        "artifact_run_id": benchmark.B140_RUN_ID,
        "configuration": "b088-routable",
        "boards_offered": 20,
        "nets_submitted": 70,
        **benchmark._current_corpus_binding(),
        "admission_partition": {
            "boards_admitted_by_the_coordinator": 16,
            "boards_unable_to_form_a_two_request_envelope": 4,
        },
    }
    report: dict[str, Any] = {
        "schema": benchmark.REPORT_SCHEMA,
        "benchmark": "B-141",
        "date_utc": "2026-08-30",
        "source_commit": "a" * 40,
        "environment": benchmark._environment_projection(),
        "population_binding": binding,
        "configuration": benchmark._configuration(),
        "metrics": {
            "population": population,
            "deterministic_replays": True,
            "control": control,
            "treatment": treatment,
            "differential": {},
            "reference_baseline": {
                "benchmark": "B-088",
                "artifact": benchmark.REFERENCE_ARTIFACT.relative_to(benchmark.ROOT).as_posix(),
                "artifact_run_id": benchmark.B088_RUN_ID,
                "grid_policy": "fixed",
                "nets_routed": 70,
                "nets_attempted": 117,
            },
        },
        "timing": {
            "repetitions": benchmark.BENCHMARK_REPETITIONS,
            "mean_wall_seconds": {"control": 0.0, "treatment": 0.0},
        },
        "repair_work_definition": EXPECTED_REPAIR_WORK_DEFINITION,
        "differential_definition": EXPECTED_DIFFERENTIAL_DEFINITION,
        "not_claimed": list(EXPECTED_NOT_CLAIMED),
    }
    return _reconcile_differential(report)


def _dict_paths(value: Any, path: tuple[str, ...] = ()) -> tuple[tuple[str, ...], ...]:
    """Return every public JSON-object path, including the root."""

    paths: list[tuple[str, ...]] = []
    if isinstance(value, dict):
        paths.append(path)
        for key, child in value.items():
            paths.extend(_dict_paths(child, (*path, key)))
    elif isinstance(value, list):
        for child in value:
            paths.extend(_dict_paths(child, path))
    return tuple(paths)


def _at_path(document: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    value: Any = document
    for key in path:
        value = value[key]
    assert isinstance(value, dict)
    return value


def _resign_public_report(document: dict[str, Any]) -> dict[str, Any]:
    """Recompute nested configuration and outer report digests after a deliberate mutation."""

    configuration = document.get("configuration")
    if isinstance(configuration, dict):
        body = {key: value for key, value in configuration.items() if key != "configuration_sha256"}
        configuration["configuration_sha256"] = _canonical_digest(body)
    return _retag_document(document)


def test_population_is_exactly_twenty_boards_with_a_16_4_admission_partition(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
) -> None:
    prepared, population = prepared_population

    assert len(prepared) == 20
    assert population == EXPECTED_POPULATION
    assert sum(board.envelope is not None for board in prepared) == 16
    assert sum(board.envelope is None for board in prepared) == 4
    assert sum(len(board.submitted) for board in prepared) == 70
    assert sum(board.reference_nets_routed for board in prepared) == 70


def test_published_artifact_uses_the_exact_b141_basename_and_self_digest(
    fresh_report: dict[str, Any],
) -> None:
    report = _artifact()
    body = {key: value for key, value in report.items() if key != "run_id"}

    assert benchmark.DEFAULT_OUTPUT == EXPECTED_ARTIFACT
    assert report["schema"] == benchmark.REPORT_SCHEMA
    assert isinstance(report["run_id"], str) and SHA256.fullmatch(report["run_id"])
    assert report["run_id"] == _canonical_digest(body)
    assert report["source_commit"] == fresh_report["source_commit"]
    assert report["metrics"] == fresh_report["metrics"]
    assert report["configuration"] == fresh_report["configuration"]
    assert benchmark.load_artifact(EXPECTED_ARTIFACT) == report


def test_committed_artifact_records_the_exact_zero_control_and_one_repaired_treatment() -> None:
    report = _artifact()
    control = report["metrics"]["control"]
    treatment = report["metrics"]["treatment"]

    assert control["boards_completed"] == 0
    assert control["negotiated_nets_completed"] == 0
    assert control["repair_outcome_breakdown"]["repair_published"] == 0
    assert control["outcome_breakdown"]["completed_with_repair"] == 0
    assert treatment["boards_completed"] == 1
    assert treatment["negotiated_nets_completed"] == 2
    assert treatment["repair_outcome_breakdown"]["repair_published"] == 1
    assert treatment["outcome_breakdown"]["completed_with_repair"] == 1
    assert report["metrics"]["differential"]["boards_completed_delta"] == 1
    assert report["metrics"]["differential"]["negotiated_nets_completed_delta"] == 2


def test_artifact_binds_the_reference_runner_b140_and_current_runner_bytes(
    fresh_report: dict[str, Any],
) -> None:
    report = _artifact()
    configuration = report["configuration"]
    fresh_configuration = fresh_report["configuration"]
    without_digest = {
        key: value for key, value in configuration.items() if key != "configuration_sha256"
    }

    assert configuration["configuration_sha256"] == _canonical_digest(without_digest)
    assert configuration["runner_sha256"] == _file_digest(benchmark.ROOT / benchmark.SCRIPT_PATH)
    assert configuration["b140_runner_sha256"] == _file_digest(benchmark.B140_RUNNER_PATH)
    assert configuration["b140_artifact_sha256"] == _file_digest(benchmark.B140_ARTIFACT)
    assert configuration["b140_source_commit"] == benchmark.B140_SOURCE_COMMIT
    assert configuration["reference_runner_sha256"] == _file_digest(benchmark.REFERENCE_RUNNER_PATH)
    assert configuration["reference_adapter_sha256"] == _file_digest(benchmark.B088_ADAPTER_PATH)
    assert configuration["reference_artifact_sha256"] == _file_digest(benchmark.REFERENCE_ARTIFACT)
    assert configuration["reference_source_commit"] == benchmark.B088_SOURCE_COMMIT
    assert configuration["b140_artifact_run_id"] == benchmark.B140_RUN_ID
    assert configuration["reference_artifact_run_id"] == benchmark.REFERENCE_RUN_ID
    assert configuration == fresh_configuration
    assert report["population_binding"] == {
        "benchmark": "B-140",
        "artifact": benchmark.B140_ARTIFACT.relative_to(benchmark.ROOT).as_posix(),
        "artifact_run_id": benchmark.B140_RUN_ID,
        "configuration": "b088-routable",
        "boards_offered": 20,
        "nets_submitted": 70,
        "corpus_manifest_count": 20,
        "corpus_manifest_sha256": benchmark._current_corpus_binding()["corpus_manifest_sha256"],
        "admission_partition": {
            "boards_admitted_by_the_coordinator": 16,
            "boards_unable_to_form_a_two_request_envelope": 4,
        },
    }
    assert report["metrics"]["reference_baseline"] == {
        "benchmark": "B-088",
        "artifact": benchmark.REFERENCE_ARTIFACT.relative_to(benchmark.ROOT).as_posix(),
        "artifact_run_id": benchmark.REFERENCE_RUN_ID,
        "grid_policy": "fixed",
        "nets_routed": 70,
        "nets_attempted": 117,
    }


def test_current_configuration_digest_binds_the_current_runner_bytes() -> None:
    configuration = benchmark._configuration()
    without_digest = {
        key: value for key, value in configuration.items() if key != "configuration_sha256"
    }

    assert configuration["runner_sha256"] == _file_digest(benchmark.ROOT / benchmark.SCRIPT_PATH)
    assert configuration["configuration_sha256"] == _canonical_digest(without_digest)


@pytest.mark.parametrize("repetitions", (1, 3))
def test_invalid_repetition_counts_fail_before_measurement_or_publication(
    repetitions: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reached: list[str] = []

    def measurement_reached(*_args: Any, **_kwargs: Any) -> Any:
        reached.append("measurement")
        raise AssertionError("measurement must not start")

    monkeypatch.setattr(benchmark, "prepare_population", measurement_reached)
    with pytest.raises(ValueError, match="exactly two"):
        benchmark.run_differential(repetitions=repetitions)
    assert reached == []

    monkeypatch.setattr(benchmark, "run_differential", measurement_reached)
    with pytest.raises(ValueError, match="exactly two"):
        benchmark.build_report(repetitions=repetitions, source_commit="a" * 40)
    assert reached == []


@pytest.mark.parametrize(
    "timing",
    (
        {"repetitions": 1, "mean_wall_seconds": {"control": 1.0, "treatment": 1.0}},
        {"repetitions": 3, "mean_wall_seconds": {"control": 1.0, "treatment": 1.0}},
        {"repetitions": True, "mean_wall_seconds": {"control": 1.0, "treatment": 1.0}},
        {"repetitions": 2, "mean_wall_seconds": {"control": float("nan"), "treatment": 1.0}},
        {"repetitions": 2, "mean_wall_seconds": {"control": float("inf"), "treatment": 1.0}},
        {"repetitions": 2, "mean_wall_seconds": {"control": True, "treatment": 1.0}},
        {"repetitions": 2, "mean_wall_seconds": {"control": -1.0, "treatment": 1.0}},
        {"repetitions": 2, "mean_wall_seconds": {"control": 1.0}},
    ),
)
def test_timing_validator_rejects_drift_malformed_nonfinite_and_boolean_values(
    timing: dict[str, Any],
) -> None:
    with pytest.raises(benchmark.NegotiatedDifferentialError, match=r"timing|repetition"):
        benchmark._validate_timing(timing, require_exact_repetitions=True)


def test_current_public_shape_and_static_contract_are_accepted_without_the_artifact() -> None:
    report = _synthetic_public_report()

    assert report["repair_work_definition"] == EXPECTED_REPAIR_WORK_DEFINITION
    assert report["differential_definition"] == EXPECTED_DIFFERENTIAL_DEFINITION
    assert report["not_claimed"] == EXPECTED_NOT_CLAIMED
    benchmark.validate_report(report)


def test_self_resigned_extra_and_missing_keys_are_rejected_in_every_public_object() -> None:
    original = _synthetic_public_report()

    for path in _dict_paths(original):
        extra = json.loads(json.dumps(original))
        _at_path(extra, path)["unexpected"] = "private payload"
        _resign_public_report(extra)
        with pytest.raises(benchmark.NegotiatedDifferentialError):
            benchmark.validate_report(extra, require_semantics=False)

        missing = json.loads(json.dumps(original))
        target = _at_path(missing, path)
        removable = next(
            key
            for key in target
            if key != "run_id" and (path != ("configuration",) or key != "configuration_sha256")
        )
        target.pop(removable)
        _resign_public_report(missing)
        with pytest.raises(benchmark.NegotiatedDifferentialError):
            benchmark.validate_report(missing, require_semantics=False)


def test_private_payload_is_rejected_in_every_public_object() -> None:
    original = _synthetic_public_report()

    for path in _dict_paths(original):
        tampered = json.loads(json.dumps(original))
        _at_path(tampered, path)["private_payload"] = {"board_id": "secret"}
        _resign_public_report(tampered)
        with pytest.raises(benchmark.NegotiatedDifferentialError):
            benchmark.validate_report(tampered, require_semantics=False)


def test_private_payload_is_rejected_by_commitment_construction_and_authoritative_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tampered = _synthetic_public_report()
    tampered["private_payload"] = {"board_id": "secret"}
    _resign_public_report(tampered)
    rendered = json.dumps(tampered, allow_nan=False, sort_keys=True).encode()

    with pytest.raises(benchmark.NegotiatedDifferentialError):
        benchmark.validate_report(tampered)
    with pytest.raises(benchmark.NegotiatedDifferentialError):
        benchmark._build_commitment_from_bytes(tampered, benchmark.DEFAULT_OUTPUT, rendered)

    original_load_object = benchmark._load_object

    def tampered_loader(path: Path, *, label: str) -> dict[str, Any]:
        if Path(path).resolve() == benchmark.DEFAULT_OUTPUT.resolve():
            return tampered
        return original_load_object(path, label=label)

    monkeypatch.setattr(benchmark, "_load_object", tampered_loader)
    with pytest.raises(benchmark.NegotiatedDifferentialError):
        benchmark.load_artifact(benchmark.DEFAULT_OUTPUT)


def _replace_path(document: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    if not path:
        raise AssertionError("a root replacement needs a field path")
    parent = document
    for key in path[:-1]:
        child = parent[key]
        assert isinstance(child, dict)
        parent = child
    parent[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("schema",), "copper-mcp/benchmark/other/v1"),
        (("benchmark",), "B-140"),
        (("date_utc",), "2026-08-31"),
        (("repair_work_definition",), "arbitrary claim"),
        (("differential_definition",), "arbitrary differential"),
        (("not_claimed",), ["private_payload"]),
        (("not_claimed",), {"private_payload": "secret"}),
        (("environment",), []),
        (("environment", "python_version"), []),
        (("population_binding", "configuration"), "arbitrary-config"),
        (("metrics", "reference_baseline", "grid_policy"), "arbitrary-grid"),
    ),
)
def test_public_static_definitions_and_allowed_string_containers_cannot_be_rewritten(
    path: tuple[str, ...], value: Any
) -> None:
    tampered = _synthetic_public_report()
    _replace_path(tampered, path, value)
    _resign_public_report(tampered)

    with pytest.raises(benchmark.NegotiatedDifferentialError):
        benchmark.validate_report(tampered, require_semantics=False)


@pytest.mark.parametrize("field", ("os_family", "architecture"))
def test_self_resigned_environment_identifiers_reject_board_identity(field: str) -> None:
    tampered = _synthetic_public_report()
    tampered["environment"][field] = "ts18_dual_reg"
    _resign_public_report(tampered)

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="environment"):
        benchmark.validate_report(tampered, require_semantics=False)


@pytest.mark.parametrize("repetitions", (1, 3))
def test_report_rejects_timing_repetition_drift(repetitions: int) -> None:
    tampered = _artifact()
    tampered["timing"]["repetitions"] = repetitions
    _retag_document(tampered)

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="exactly two"):
        benchmark.validate_report(tampered)


@pytest.mark.parametrize(
    ("binding", "path_attribute"),
    (
        ("runner_sha256", "SCRIPT_PATH"),
        ("b140_runner_sha256", "B140_RUNNER_PATH"),
        ("b140_artifact_sha256", "B140_ARTIFACT"),
        ("reference_runner_sha256", "REFERENCE_RUNNER_PATH"),
        ("reference_adapter_sha256", "B088_ADAPTER_PATH"),
        ("reference_artifact_sha256", "REFERENCE_ARTIFACT"),
    ),
)
def test_load_artifact_rejects_current_authority_digest_drift(
    binding: str,
    path_attribute: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-consistent report is still stale when any bound authority bytes drift."""

    original_digest = benchmark._file_digest
    expected_path = Path(getattr(benchmark, path_attribute)).resolve()
    zero_digest = "sha256:" + "0" * 64

    def drifted_digest(path: Path) -> str:
        if Path(path).resolve() == expected_path:
            return zero_digest
        return original_digest(path)

    monkeypatch.setattr(benchmark, "_file_digest", drifted_digest)

    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=r"(?:digest|authority|binding)"
    ):
        benchmark.load_artifact(EXPECTED_ARTIFACT)


def test_load_artifact_rejects_a_current_runner_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loader must compare the report to the bytes executing its validation code."""

    original_digest = benchmark._file_digest
    runner_path = (benchmark.ROOT / benchmark.SCRIPT_PATH).resolve()

    def drifted_runner(path: Path) -> str:
        if Path(path).resolve() == runner_path:
            return "sha256:" + "0" * 64
        return original_digest(path)

    monkeypatch.setattr(benchmark, "_file_digest", drifted_runner)

    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=r"(?:digest|authority|binding)"
    ):
        benchmark.load_artifact(EXPECTED_ARTIFACT)


def test_load_artifact_accepts_a_recorded_source_revision_after_head_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later checkout does not invalidate an otherwise byte-bound report provenance."""

    recorded_source_commit = _artifact()["source_commit"]
    moved_source_commit = "f" * 40
    if moved_source_commit == recorded_source_commit:
        moved_source_commit = "e" * 40

    monkeypatch.setattr(benchmark, "_git_state", lambda: (moved_source_commit, ()))

    loaded = benchmark.load_artifact(EXPECTED_ARTIFACT)

    assert loaded["source_commit"] == recorded_source_commit


def test_fresh_report_reconciles_both_arms_and_keeps_refusal_taxonomies_closed(
    fresh_report: dict[str, Any],
) -> None:
    metrics = fresh_report["metrics"]
    assert metrics["population"] == EXPECTED_POPULATION
    assert metrics["deterministic_replays"] is True
    assert metrics["control"]["repair_enabled"] is False
    assert metrics["treatment"]["repair_enabled"] is True
    assert metrics["control"]["repair_settings"] is None
    assert metrics["treatment"]["repair_settings"] == asdict(RepairTransactionSettings())

    for aggregate in (metrics["control"], metrics["treatment"]):
        assert {key: aggregate[key] for key in EXPECTED_POPULATION} == EXPECTED_POPULATION
        assert set(aggregate["outcome_breakdown"]) == set(benchmark.RUN_OUTCOME_TAXONOMY)
        assert set(aggregate["refusal_breakdown"]) == set(benchmark.REFUSAL_TAXONOMY)
        assert set(aggregate["repair_outcome_breakdown"]) == set(benchmark.REPAIR_OUTCOME_TAXONOMY)
        assert sum(aggregate["outcome_breakdown"].values()) == aggregate["boards_offered"]
        refusal_total = sum(
            aggregate["outcome_breakdown"][code] for code in benchmark.REFUSAL_TAXONOMY
        )
        completed_total = sum(
            aggregate["outcome_breakdown"][code]
            for code in ("completed_without_repair", "completed_with_repair")
        )
        assert refusal_total + completed_total == aggregate["boards_offered"]
        assert sum(aggregate["refusal_breakdown"].values()) == refusal_total
        assert sum(aggregate["repair_outcome_breakdown"].values()) == aggregate["boards_offered"]
        assert aggregate["boards_completed"] == completed_total
        assert (
            aggregate["boards_admitted_by_the_coordinator"]
            == aggregate["boards_offered"]
            - aggregate["boards_unable_to_form_a_two_request_envelope"]
        )
        assert (
            aggregate["repair_outcome_breakdown"]["not_applicable_envelope_refused"]
            == aggregate["outcome_breakdown"]["envelope_construction"]
        )
        assert (
            aggregate["repair_outcome_breakdown"]["repair_published"]
            == aggregate["outcome_breakdown"]["completed_with_repair"]
        )
        assert aggregate["repair_outcome_breakdown"]["repair_not_published"] == aggregate[
            "outcome_breakdown"
        ]["completed_without_repair"] + sum(
            aggregate["outcome_breakdown"][code]
            for code in benchmark.REFUSAL_TAXONOMY
            if code != "envelope_construction"
        )
        assert (
            aggregate["repair_work"]["published_repairs"]
            == aggregate["repair_outcome_breakdown"]["repair_published"]
        )
        assert type(aggregate["total_physical_checks"]) is int
        assert aggregate["total_physical_checks"] >= 0
        work = aggregate["repair_work"]
        assert all(type(value) is int and value >= 0 for value in work.values())
        if work["published_repairs"] == 0:
            assert all(value == 0 for key, value in work.items() if key != "published_repairs")

    # A zero published-repair count is valid evidence of the measured result, not a runner error.
    assert metrics["differential"]["verdict"] in {
        "positive_completion_delta",
        "zero_or_negative_completion_delta",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("boards_completed", EXPECTED_POPULATION["boards_admitted_by_the_coordinator"] + 1),
        ("negotiated_nets_completed", 999),
        ("repair_local_expanded_states", 999999),
    ),
)
def test_report_rejects_completion_and_repair_work_budget_overruns(
    field: str,
    value: int,
) -> None:
    tampered = _artifact()
    treatment = tampered["metrics"]["treatment"]
    if field in treatment:
        treatment[field] = value
        _reconcile_differential(tampered)
    else:
        treatment["repair_work"][field] = value
        _retag_document(tampered)

    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=r"closed bound|per-repair bound"
    ):
        benchmark.validate_report(tampered)


@pytest.mark.parametrize(
    ("field", "per_repair_budget"),
    (
        ("repair_local_expanded_states", "max_local_expansions"),
        ("repair_projection_obstacle_checks", "projection_plus_obstacles"),
        ("repair_validator_edge_checks", "max_validator_path_edges"),
        ("repair_validator_obstacle_checks", "max_validator_obstacle_checks"),
    ),
)
def test_published_repair_work_accepts_exact_transaction_budget_and_rejects_one_more(
    field: str,
    per_repair_budget: str,
) -> None:
    settings = RepairTransactionSettings()
    budgets = {
        "max_local_expansions": settings.max_local_expansions,
        "projection_plus_obstacles": (
            settings.max_projection_cells + settings.max_validator_obstacle_checks
        ),
        "max_validator_path_edges": settings.max_validator_path_edges,
        "max_validator_obstacle_checks": settings.max_validator_obstacle_checks,
    }
    exact = _artifact()
    treatment = exact["metrics"]["treatment"]
    assert treatment["repair_outcome_breakdown"]["repair_published"] == 1
    treatment["repair_work"][field] = budgets[per_repair_budget]
    _retag_document(exact)
    benchmark.validate_report(exact)

    over = _artifact()
    over["metrics"]["treatment"]["repair_work"][field] = budgets[per_repair_budget] + 1
    _retag_document(over)
    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=r"closed bound|per-repair bound"
    ):
        benchmark.validate_report(over)


@pytest.mark.parametrize(
    ("field", "per_candidate_budget"),
    (
        ("inherited_search_expansions", "max_expansions"),
        ("inherited_search_obstacle_checks", "max_obstacle_checks"),
        ("inherited_proximity_steps", "max_grid_nodes"),
        (
            "inherited_proximity_cost_nm",
            "max_grid_path_length_nm",
        ),
    ),
)
def test_inherited_repair_work_is_bounded_by_32_final_candidates_per_repair(
    field: str,
    per_candidate_budget: str,
) -> None:
    b140 = benchmark.b140
    budgets = {
        "max_expansions": b140.ROUTER_LIMITS["max_expansions"],
        "max_obstacle_checks": b140.ROUTER_LIMITS["max_obstacle_checks"],
        "max_grid_nodes": b140.ROUTER_LIMITS["max_grid_nodes"],
        "max_grid_path_length_nm": (b140.ROUTER_LIMITS["max_grid_nodes"] * b140.FIXED_GRID_STEP_NM),
    }
    exact = _artifact()
    treatment = exact["metrics"]["treatment"]
    assert treatment["repair_outcome_breakdown"]["repair_published"] == 1
    treatment["repair_work"][field] = 32 * budgets[per_candidate_budget]
    _retag_document(exact)
    benchmark.validate_report(exact)

    over = _artifact()
    over["metrics"]["treatment"]["repair_work"][field] = 32 * budgets[per_candidate_budget] + 1
    _retag_document(over)
    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=r"closed bound|per-repair bound"
    ):
        benchmark.validate_report(over)


def test_completion_nets_must_be_zero_when_no_board_completes() -> None:
    valid = _zero_completed_treatment(_artifact())
    treatment = valid["metrics"]["treatment"]
    assert treatment["boards_completed"] == 0
    assert treatment["negotiated_nets_completed"] == 0
    benchmark.validate_report(valid)

    invalid = _zero_completed_treatment(_artifact())
    invalid["metrics"]["treatment"]["negotiated_nets_completed"] = 1
    _reconcile_differential(invalid)
    with pytest.raises(benchmark.NegotiatedDifferentialError, match=r"reconcil|range|totals"):
        benchmark.validate_report(invalid)


@pytest.mark.parametrize(
    ("completed_nets", "accepted"),
    ((1, False), (2, True), (32, True), (33, False)),
)
def test_completion_nets_are_between_two_and_32_per_completed_board(
    completed_nets: int,
    accepted: bool,
) -> None:
    tampered = _artifact()
    tampered["metrics"]["treatment"]["negotiated_nets_completed"] = completed_nets
    _reconcile_differential(tampered)
    if accepted:
        benchmark.validate_report(tampered)
    else:
        with pytest.raises(benchmark.NegotiatedDifferentialError, match=r"reconcil|range|bound"):
            benchmark.validate_report(tampered)


@pytest.mark.parametrize(
    "tamper", ("delta", "repair_published", "completed_with_repair", "population")
)
def test_validate_and_load_reject_a_self_consistent_semantic_tamper(
    tamper: str,
    tmp_path: Path,
) -> None:
    """Recomputing ``run_id`` must not turn an impossible differential into evidence."""

    tampered = _artifact()
    if tamper == "delta":
        tampered["metrics"]["differential"]["boards_completed_delta"] = 999
    elif tamper == "repair_published":
        repairs = tampered["metrics"]["treatment"]["repair_outcome_breakdown"]
        repairs["repair_published"] = 2
        repairs["repair_not_published"] -= 1
    elif tamper == "completed_with_repair":
        outcomes = tampered["metrics"]["treatment"]["outcome_breakdown"]
        refusals = tampered["metrics"]["treatment"]["refusal_breakdown"]
        outcomes["completed_with_repair"] += 1
        outcomes["no_path_physical_clearance"] -= 1
        refusals["no_path_physical_clearance"] -= 1
    elif tamper == "population":
        tampered["metrics"]["population"]["nets_submitted"] = 69
    else:  # pragma: no cover - the parametrization is closed above
        raise AssertionError(f"unhandled tamper {tamper}")
    _retag_document(tampered)
    destination = tmp_path / f"tampered-{tamper}.json"
    destination.write_text(json.dumps(tampered), encoding="utf-8")

    semantic_error = r"(?:semantic|reconcil|population|repair|delta|differential)"
    with pytest.raises(benchmark.NegotiatedDifferentialError, match=semantic_error):
        benchmark.validate_report(tampered)
    with pytest.raises(benchmark.NegotiatedDifferentialError, match=semantic_error):
        benchmark.load_artifact(destination)


def test_public_report_contains_no_board_net_candidate_or_geometry_payload(
    fresh_report: dict[str, Any],
) -> None:
    forbidden = benchmark.FORBIDDEN_PUBLIC_KEYS
    assert _nested_keys(fresh_report).isdisjoint(forbidden)
    assert _nested_keys(_artifact()).isdisjoint(forbidden)
    serialized = json.dumps(fresh_report, sort_keys=True)
    assert all(secret not in serialized for secret in ("ts01_led", "ts18_dual_reg", "net:n0"))


def test_control_passes_none_and_never_reaches_the_actual_repair_entry_point(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board = _admitted_board(prepared_population)
    seen_settings: list[object] = []
    original_negotiate = benchmark.negotiate_routes

    def recording_negotiate(*args: Any, **kwargs: Any) -> NegotiatedRoutingResult:
        seen_settings.append(kwargs.get("repair_settings"))
        return original_negotiate(*args, **kwargs)

    def forbidden_repair(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("control reached _attempt_local_repair")

    monkeypatch.setattr(benchmark, "negotiate_routes", recording_negotiate)
    monkeypatch.setattr(coordinator, "_attempt_local_repair", forbidden_repair)

    measurement = benchmark._measure_configuration((board,), treatment=False)

    assert seen_settings == [None]
    assert measurement.aggregate["repair_enabled"] is False
    assert measurement.aggregate["repair_settings"] is None
    assert measurement.aggregate["boards_offered"] == 1


def test_treatment_reaches_actual_repair_entry_point_even_when_no_repair_is_published(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board = _ts18_board(prepared_population)
    calls: list[dict[str, Any]] = []

    def treatment_sentinel(*_args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        raise RuntimeError("treatment repair sentinel reached")

    monkeypatch.setattr(coordinator, "_attempt_local_repair", treatment_sentinel)

    with pytest.raises(RuntimeError, match="treatment repair sentinel reached"):
        benchmark._measure_configuration((board,), treatment=True)

    assert calls
    assert isinstance(calls[0]["settings"], RepairTransactionSettings)


def test_run_differential_passes_one_byte_identical_population_to_both_arms_and_replays(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, population = prepared_population
    passed: list[tuple[bool, tuple[benchmark.PreparedBoard, ...]]] = []

    def fake_measure(
        population_arg: tuple[benchmark.PreparedBoard, ...], *, treatment: bool
    ) -> benchmark.ConfigurationMeasurement:
        passed.append((treatment, population_arg))
        return benchmark.ConfigurationMeasurement(
            aggregate=_minimal_aggregate(population), records=()
        )

    monkeypatch.setattr(
        benchmark, "prepare_population", lambda *_args, **_kwargs: (prepared, population)
    )
    monkeypatch.setattr(benchmark, "_measure_configuration", fake_measure)

    benchmark.run_differential(repetitions=2)

    assert [treatment for treatment, _population_arg in passed] == [False, True, False, True]
    assert all(population_arg is prepared for _treatment, population_arg in passed)
    assert all(population_arg == prepared for _treatment, population_arg in passed)


def test_run_differential_refuses_a_second_run_with_a_changed_immutable_projection(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, population = prepared_population
    calls = 0

    def drifting_measure(
        _population_arg: tuple[benchmark.PreparedBoard, ...], *, treatment: bool
    ) -> benchmark.ConfigurationMeasurement:
        nonlocal calls
        calls += 1
        return benchmark.ConfigurationMeasurement(
            aggregate=_minimal_aggregate(population),
            records=(
                benchmark.RunRecord(
                    result=None,
                    projection={"arm": treatment, "replay": calls},
                ),
            ),
        )

    monkeypatch.setattr(
        benchmark, "prepare_population", lambda *_args, **_kwargs: (prepared, population)
    )
    monkeypatch.setattr(benchmark, "_measure_configuration", drifting_measure)

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="projection diverged"):
        benchmark.run_differential(repetitions=2)
    # Each repetition measures both arms before comparing the complete pair.
    assert calls == 4


def test_run_differential_rejects_same_count_arm_population_drift(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, population = prepared_population

    def mismatched_measure(
        _population_arg: tuple[benchmark.PreparedBoard, ...], *, treatment: bool
    ) -> benchmark.ConfigurationMeasurement:
        aggregate = _minimal_aggregate(population)
        # Keep the shape and all other fields intact while changing only one population member.
        aggregate["nets_submitted"] = int(aggregate["nets_submitted"]) - 1
        return benchmark.ConfigurationMeasurement(aggregate=aggregate, records=())

    monkeypatch.setattr(
        benchmark, "prepare_population", lambda *_args, **_kwargs: (prepared, population)
    )
    monkeypatch.setattr(benchmark, "_measure_configuration", mismatched_measure)

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="population projection"):
        benchmark.run_differential(repetitions=2)


def test_same_count_reference_membership_tamper_is_refused(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
) -> None:
    _prepared, _population = prepared_population
    document = json.loads(benchmark.REFERENCE_ARTIFACT.read_text(encoding="utf-8"))
    boards = document["metrics"]["configurations"]["fixed"]["boards"]
    board = next(item for item in boards if item["outcomes"].get("routed") == 1)
    original_count = board["outcomes"]["routed"]
    board["candidate_digest"] = "0" * 64
    body = {key: value for key, value in document.items() if key != "run_id"}
    document["run_id"] = _canonical_digest(body)

    assert board["outcomes"]["routed"] == original_count
    with pytest.raises(benchmark.NegotiatedDifferentialError, match="re-derived B-088 population"):
        benchmark.prepare_population(b088_document=document)


def test_prepare_population_refuses_a_duplicate_omitted_board_with_same_population_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Roster identity must be checked independently of 20/70/16-4 aggregate arithmetic."""

    manifest, samples = benchmark.b140.reference.load_corpus(benchmark.b140.CORPUS)
    duplicate = next(item for item in samples if Path(item[0]).stem == "ts02_voltage_divider")
    omitted = next(item for item in samples if Path(item[0]).stem == "ts03_rc_filter")
    replaced = list(samples)
    omitted_index = next(
        index for index, item in enumerate(replaced) if Path(item[0]).stem == "ts03_rc_filter"
    )
    # Keep the omitted board's unique filename so the manifest byte-set check passes, but replace
    # its payload with the duplicate board's bytes.  This is an omitted/duplicated board identity
    # substitution, not merely a count mutation.
    replaced[omitted_index] = (omitted[0], duplicate[1])
    names = tuple(Path(item[0]).stem for item in replaced)

    router = benchmark.b140.AStarRouter()

    def primary_signature(item: tuple[str, bytes]) -> tuple[int, int, object]:
        problem = benchmark.b140.import_simple_route_json(Path(item[0]).stem, item[1])
        submitted = benchmark.b140.PRIMARY.select(benchmark.b140._solo_reference(problem, router))
        return (
            len(submitted),
            sum(entry.reference_outcome == "routed" for entry in submitted),
            benchmark.b140._first_unmet(benchmark.b140._admission(problem, submitted)),
        )

    # The replacement is deliberately aggregate-preserving: both boards contribute one routed
    # primary net and the same envelope refusal, so only exact membership can reject it.
    assert primary_signature(duplicate) == primary_signature(replaced[omitted_index])

    assert len(replaced) == 20
    assert len(set(names)) == 20
    assert names.count("ts02_voltage_divider") == 1
    assert names.count("ts03_rc_filter") == 1
    assert duplicate[1] != omitted[1]
    assert (
        hashlib.sha256(replaced[omitted_index][1]).hexdigest()
        == hashlib.sha256(duplicate[1]).hexdigest()
    )
    assert (
        hashlib.sha256(replaced[omitted_index][1]).hexdigest()
        != hashlib.sha256(omitted[1]).hexdigest()
    )
    mutated_manifest = json.loads(json.dumps(manifest))
    omitted_entry = next(
        entry for entry in mutated_manifest["files"] if entry["name"] == omitted[0]
    )
    omitted_entry["bytes"] = len(duplicate[1])
    omitted_entry["sha256"] = hashlib.sha256(duplicate[1]).hexdigest()
    monkeypatch.setattr(
        benchmark.b140.reference,
        "load_corpus",
        lambda _corpus: (mutated_manifest, tuple(replaced)),
    )
    # Isolate the exact-membership seam from the later per-board candidate authority check.  If the
    # new guard is removed, this bypass lets the aggregate-preserving substitution reach the end.
    monkeypatch.setattr(
        benchmark.b140,
        "_assert_reference_authority",
        lambda _problem, submitted, _expected: sum(
            item.reference_outcome == "routed" for item in submitted
        ),
    )

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark.CORPUS_MANIFEST_ERROR),
    ):
        benchmark.prepare_population()


def test_zero_repair_result_is_a_valid_treatment_measurement(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board = _admitted_board(prepared_population)
    result = NegotiatedRoutingResult(
        status=NegotiatedRoutingStatus.COMPLETED,
        board_revision=board.problem.snapshot.snapshot_digest,
        iterations=2,
        ripups=1,
        total_wire_length_nm=0,
        total_physical_checks=7,
    )
    seen_settings: list[object] = []

    def completed_without_repair(*_args: Any, **kwargs: Any) -> NegotiatedRoutingResult:
        seen_settings.append(kwargs["repair_settings"])
        return result

    monkeypatch.setattr(benchmark, "negotiate_routes", completed_without_repair)
    measurement = benchmark._measure_configuration((board,), treatment=True)
    aggregate = measurement.aggregate

    assert isinstance(seen_settings[0], RepairTransactionSettings)
    assert aggregate["outcome_breakdown"]["completed_without_repair"] == 1
    assert aggregate["repair_outcome_breakdown"]["repair_not_published"] == 1
    assert aggregate["repair_work"]["published_repairs"] == 0
    assert aggregate["total_physical_checks"] == 7


def test_completed_with_repair_requires_a_final_completed_result(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repair evidence on a refused final result cannot promote that result to completion."""

    board = _admitted_board(prepared_population)
    refused = NegotiatedRoutingResult(
        status=NegotiatedRoutingStatus.NO_PATH,
        board_revision=board.problem.snapshot.snapshot_digest,
        diagnostic="no path after repair transaction",
    )
    calls = 0

    def evidence_only_during_classification(_result: NegotiatedRoutingResult) -> object | None:
        nonlocal calls
        calls += 1
        # Simulate an invalid producer that exposes evidence transiently.  The aggregate must still
        # classify from the final terminal status, and the later accounting calls see no evidence.
        return object() if calls == 1 else None

    def refused_route(*_args: Any, **_kwargs: Any) -> NegotiatedRoutingResult:
        return refused

    monkeypatch.setattr(benchmark, "_repair_evidence", evidence_only_during_classification)
    monkeypatch.setattr(benchmark, "negotiate_routes", refused_route)

    aggregate = benchmark._measure_configuration((board,), treatment=True).aggregate

    assert aggregate["outcome_breakdown"]["completed_with_repair"] == 0
    assert aggregate["outcome_breakdown"]["no_path_search"] == 1
    assert aggregate["repair_outcome_breakdown"]["repair_published"] == 0


def test_validate_report_rejects_a_rewritten_configuration_digest(
    fresh_report: dict[str, Any],
) -> None:
    tampered = json.loads(json.dumps(fresh_report))
    tampered["configuration"]["control"]["repair_settings"] = {"unexpected": True}
    body = {key: value for key, value in tampered.items() if key != "run_id"}
    tampered["run_id"] = _canonical_digest(body)

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="configuration"):
        benchmark.validate_report(tampered)


def test_build_report_preserves_an_explicit_source_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _minimal_aggregate(EXPECTED_POPULATION)
    aggregate["outcome_breakdown"]["envelope_construction"] = 20
    aggregate["refusal_breakdown"]["envelope_construction"] = 20
    aggregate["repair_outcome_breakdown"]["not_applicable_envelope_refused"] = 20
    aggregate["repair_enabled"] = False
    aggregate["repair_settings"] = None
    metrics = {
        "population": EXPECTED_POPULATION,
        "deterministic_replays": True,
        "control": aggregate,
        "treatment": {
            **aggregate,
            "repair_enabled": True,
            "repair_settings": asdict(RepairTransactionSettings()),
        },
        "differential": {
            "boards_completed_delta": 0,
            "negotiated_nets_completed_delta": 0,
            "total_wire_length_nm_delta": 0,
            "total_overflow_units_delta": 0,
            "total_physical_checks_delta": 0,
            "positive_completion_delta": False,
            "verdict": "zero_or_negative_completion_delta",
        },
        "reference_baseline": {
            "benchmark": "B-088",
            "artifact": benchmark.REFERENCE_ARTIFACT.relative_to(benchmark.ROOT).as_posix(),
            "artifact_run_id": benchmark.REFERENCE_RUN_ID,
            "grid_policy": "fixed",
            "nets_routed": 70,
            "nets_attempted": 117,
        },
    }
    source_commit = "a" * 40
    monkeypatch.setattr(
        benchmark,
        "run_differential",
        lambda _repetitions, _corpus: (
            metrics,
            {"repetitions": 2, "mean_wall_seconds": {"control": 0.0, "treatment": 0.0}},
        ),
    )
    monkeypatch.setattr(
        benchmark,
        "load_b140_artifact",
        lambda: {"run_id": benchmark.B140_RUN_ID},
    )

    report = benchmark.build_report(repetitions=2, source_commit=source_commit)

    assert report["source_commit"] == source_commit
    assert report["run_id"] == _canonical_digest(
        {key: value for key, value in report.items() if key != "run_id"}
    )


def test_load_artifact_rejects_a_payload_tamper_before_accepting_it(
    tmp_path: Path,
) -> None:
    tampered = _artifact()
    tampered["metrics"]["differential"]["verdict"] = "forged"
    destination = tmp_path / "tampered.json"
    destination.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="self-digest"):
        benchmark.load_artifact(destination)


def test_authoritative_load_rejects_a_self_resigned_zero_repair_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generic semantic validity cannot replace the sidecar's exact measured-result commitment."""

    tampered = _zero_completed_treatment(_artifact())
    benchmark.validate_report(tampered)
    original_load_object = benchmark._load_object

    def substitute_report(path: Path, *, label: str) -> dict[str, Any]:
        if Path(path).resolve() == EXPECTED_ARTIFACT.resolve():
            return tampered
        return original_load_object(path, label=label)

    monkeypatch.setattr(benchmark, "_load_object", substitute_report)
    with pytest.raises(benchmark.NegotiatedDifferentialError, match=r"commitment|binding"):
        benchmark.load_artifact(EXPECTED_ARTIFACT)


@pytest.mark.parametrize(
    "tamper",
    ("artifact_sha256", "artifact_run_id", "outcome", "missing_key", "unknown_key", "run_id"),
)
def test_authoritative_load_rejects_tampered_commitment_fields(
    tamper: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact()
    commitment = _commitment()
    # Applying a source mutant changes the live runner digest.  Freeze the report's already
    # recorded configuration here so this test exercises the commitment field under test, not the
    # earlier current-runner byte binding guard.
    monkeypatch.setattr(benchmark, "_configuration", lambda: artifact["configuration"])
    if tamper == "artifact_sha256":
        commitment["artifact_sha256"] = "sha256:" + "0" * 64
        _retag_commitment(commitment)
    elif tamper == "artifact_run_id":
        commitment["artifact_run_id"] = "sha256:" + "0" * 64
        _retag_commitment(commitment)
    elif tamper == "outcome":
        commitment["treatment"]["completed_with_repair"] = 0
        _retag_commitment(commitment)
    elif tamper == "missing_key":
        commitment.pop("treatment")
    elif tamper == "unknown_key":
        commitment["unexpected"] = True
    elif tamper == "run_id":
        commitment["run_id"] = "sha256:" + "0" * 64
    else:  # pragma: no cover - the parametrization is closed above
        raise AssertionError(f"unhandled tamper {tamper}")

    def tampered_loader() -> dict[str, Any]:
        benchmark.validate_commitment(commitment)
        return commitment

    monkeypatch.setattr(benchmark, "load_commitment", tampered_loader)
    with pytest.raises(benchmark.NegotiatedDifferentialError, match=r"commitment|binding|digest"):
        benchmark.load_artifact(EXPECTED_ARTIFACT)


@pytest.mark.parametrize("mode", ("missing", "malformed"))
def test_authoritative_load_requires_a_present_well_formed_commitment(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar = tmp_path / f"{mode}.commitment.json"
    if mode == "malformed":
        sidecar.write_text("{}", encoding="utf-8")

    original_load_commitment = benchmark.load_commitment

    def sidecar_loader() -> dict[str, Any]:
        return original_load_commitment(sidecar)

    monkeypatch.setattr(benchmark, "load_commitment", sidecar_loader)
    with pytest.raises(benchmark.NegotiatedDifferentialError, match=r"commitment|unreadable"):
        benchmark.load_artifact(EXPECTED_ARTIFACT)


@pytest.mark.parametrize(
    "dirty_path",
    (
        benchmark.SCRIPT_PATH,
        benchmark.DEFAULT_OUTPUT.relative_to(benchmark.ROOT).as_posix(),
        benchmark.COMMITMENT_RELATIVE_PATH,
    ),
)
def test_write_refuses_dirty_runner_or_tracked_authority_before_measurement(
    dirty_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "new-report.json"
    calls = 0

    def unexpected_measurement(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("measurement must not start while a tracked authority is dirty")

    monkeypatch.setattr(benchmark, "_git_state", lambda: ("a" * 40, (dirty_path,)))
    monkeypatch.setattr(benchmark, "build_report", unexpected_measurement)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_negotiated_multipin_branch_repair.py",
            "--write",
            "--output",
            str(output),
        ],
    )

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="unchanged source tree"):
        benchmark.main()
    assert calls == 0
    assert not output.exists()


@pytest.mark.parametrize(
    "allowlisted_path",
    (
        benchmark.DEFAULT_OUTPUT.relative_to(benchmark.ROOT).as_posix(),
        benchmark.COMMITMENT_RELATIVE_PATH,
    ),
)
def test_publishable_source_refuses_a_tracked_allowlisted_output_or_sidecar(
    allowlisted_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allowlisting a publication path does not permit a tracked deletion or modification."""

    seen: list[str] = []
    monkeypatch.setattr(benchmark, "_git_state", lambda: ("a" * 40, (allowlisted_path,)))

    def tracked(path: str) -> bool:
        seen.append(path)
        return True

    monkeypatch.setattr(benchmark, "_git_path_is_tracked", tracked)

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="unchanged source tree"):
        benchmark._require_publishable_source(
            expected_commit="a" * 40,
            allowed_dirty=frozenset({allowlisted_path}),
        )
    assert seen == [allowlisted_path]


def test_clean_source_and_new_exclusive_output_are_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "new-report.json"
    states = iter((("a" * 40, ()), ("a" * 40, ())))
    report = {"schema": "synthetic", "metrics": {"private": False}}
    calls = 0

    def measured(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return report

    monkeypatch.setattr(benchmark, "_git_state", lambda: next(states))
    monkeypatch.setattr(benchmark, "build_report", measured)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_negotiated_multipin_branch_repair.py",
            "--write",
            "--output",
            str(output),
        ],
    )

    assert benchmark.main() == 0
    assert calls == 1
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_post_measurement_runner_drift_is_refused_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "new-report.json"
    states = iter((("a" * 40, ()), ("a" * 40, (benchmark.SCRIPT_PATH,))))
    calls = 0

    def measured(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"schema": "synthetic"}

    monkeypatch.setattr(benchmark, "_git_state", lambda: next(states))
    monkeypatch.setattr(benchmark, "build_report", measured)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_negotiated_multipin_branch_repair.py",
            "--write",
            "--output",
            str(output),
        ],
    )

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="unchanged source tree"):
        benchmark.main()
    assert calls == 1
    assert not output.exists()


def test_new_publication_target_never_overwrites_an_existing_path(tmp_path: Path) -> None:
    destination = tmp_path / "already-published.json"
    original = b"existing authority\n"
    destination.write_bytes(original)

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="new path"):
        benchmark._new_output_target(destination)
    assert destination.read_bytes() == original


def test_second_commitment_write_race_rolls_back_only_the_new_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sidecar race cannot leave a usable uncommitted artifact or overwrite the racer."""

    report = _artifact()
    artifact_path = tmp_path / "artifact.json"
    commitment_path = tmp_path / "commitment.json"
    raced_bytes = b"raced sidecar authority\n"

    # Keep the production path/relative-path arithmetic intact while redirecting only this test's
    # publication transaction into tmp_path.  All report data remains the current committed shape.
    monkeypatch.setattr(benchmark, "DEFAULT_OUTPUT", artifact_path)
    monkeypatch.setattr(benchmark, "COMMITMENT_PATH", commitment_path)
    monkeypatch.setattr(benchmark, "COMMITMENT_RELATIVE_PATH", "commitment.json")
    monkeypatch.setattr(benchmark, "_new_output_target", lambda path: Path(path))
    monkeypatch.setattr(benchmark, "build_report", lambda *_args, **_kwargs: report)
    # The commitment contents are irrelevant to this race test; bypass its semantic preflight so
    # the transaction can reach the two exclusive writes while all actual files stay in tmp_path.
    monkeypatch.setattr(benchmark, "_build_commitment_from_bytes", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        benchmark,
        "_require_publishable_source",
        lambda *_args, **_kwargs: "a" * 40,
    )
    runner_digest = _file_digest(EXPECTED_ARTIFACT)
    monkeypatch.setattr(benchmark, "_file_digest", lambda _path: runner_digest)

    original_write = benchmark._write_exclusive
    calls = 0

    def racing_write(output: Path, rendered: str) -> tuple[int, int]:
        nonlocal calls
        calls += 1
        signature = original_write(output, rendered)
        if calls == 1:
            commitment_path.write_bytes(raced_bytes)
        return signature

    monkeypatch.setattr(benchmark, "_write_exclusive", racing_write)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_negotiated_multipin_branch_repair.py",
            "--write",
            "--output",
            str(artifact_path),
        ],
    )

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="new path"):
        benchmark.main()
    assert calls == 2
    assert not artifact_path.exists()
    assert commitment_path.read_bytes() == raced_bytes
