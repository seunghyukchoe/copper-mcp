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
import os
import re
import shutil
import subprocess
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
ARCHIVED_ARTIFACT = (
    benchmark.ROOT / "benchmarks/results/routing/archive/"
    "2026-08-30-negotiated-multipin-branch-repair-v1-b7c71d4d.json"
)
ARCHIVED_COMMITMENT = (
    benchmark.ROOT / "benchmarks/results/routing/archive/"
    "2026-08-30-negotiated-multipin-branch-repair-v1-b7c71d4d.commitment.json"
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


def _boundary_json(size: int) -> bytes:
    payload = b'{"boundary":true}'
    assert len(payload) < size
    return payload + (b" " * (size - len(payload)))


def _corpus_manifest() -> dict[str, Any]:
    document = json.loads((benchmark.b140.CORPUS / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _reference_authority() -> dict[str, benchmark.b140.ReferenceBoardAuthority]:
    return benchmark._reference_authority(benchmark.load_reference_artifact())


def _copy_exact_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    samples = corpus / "samples"
    samples.mkdir(parents=True)
    manifest = _corpus_manifest()
    shutil.copyfile(benchmark.b140.CORPUS / "manifest.json", corpus / "manifest.json")
    shutil.copyfile(benchmark.b140.CORPUS / "LICENSE", corpus / "LICENSE")
    for entry in manifest["files"]:
        if entry["committed"]:
            shutil.copyfile(
                benchmark.b140.CORPUS / "samples" / entry["name"], samples / entry["name"]
            )
    return corpus


def test_b141_loader_does_not_call_historical_corpus_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = benchmark._reference_authority(benchmark.load_reference_artifact())

    def forbidden(*_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("historical B-140 loader was called")

    monkeypatch.setattr(benchmark.b140.reference, "load_corpus", forbidden)
    manifest, samples, manifest_sha256 = benchmark._load_exact_corpus(
        benchmark.b140.CORPUS, authority
    )
    assert len(manifest["files"]) == 36
    assert len(samples) == 20
    assert manifest_sha256 == benchmark._current_corpus_binding()["corpus_manifest_sha256"]


def test_b141_loader_rejects_oversized_manifest_before_json_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.json").write_bytes(b"{" + b" " * benchmark.MAX_CORPUS_DESCRIPTOR_BYTES)

    def forbidden_json(*_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("oversized manifest reached JSON parsing")

    monkeypatch.setattr(benchmark.json, "loads", forbidden_json)
    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark.CORPUS_MANIFEST_ERROR),
    ):
        benchmark._load_exact_corpus(corpus, {})


def test_b141_loader_preflights_declared_sample_size_before_license_or_sample_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    manifest = _corpus_manifest()
    manifest["files"][0]["bytes"] = benchmark.MAX_CORPUS_SAMPLE_BYTES + 1
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    original_read = benchmark._read_bounded_at
    reads: list[str] = []

    def recording_read(directory_fd: int, name: str, *, max_bytes: int) -> bytes:
        reads.append(name)
        return original_read(directory_fd, name, max_bytes=max_bytes)

    monkeypatch.setattr(benchmark, "_read_bounded_at", recording_read)
    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark.CORPUS_MANIFEST_ERROR),
    ):
        benchmark._load_exact_corpus(corpus, {})
    assert reads == ["manifest.json"]


def test_b141_corpus_reader_accepts_exact_sample_limit_and_refuses_plus_one(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "bounded"
    directory.mkdir()
    target = directory / "sample.json"
    target.write_bytes(b"x" * benchmark.MAX_CORPUS_SAMPLE_BYTES)
    descriptor = benchmark._open_corpus_directory(directory)
    try:
        assert (
            len(
                benchmark._read_bounded_at(
                    descriptor, "sample.json", max_bytes=benchmark.MAX_CORPUS_SAMPLE_BYTES
                )
            )
            == benchmark.MAX_CORPUS_SAMPLE_BYTES
        )
        target.write_bytes(b"x" * (benchmark.MAX_CORPUS_SAMPLE_BYTES + 1))
        with pytest.raises(
            benchmark.NegotiatedDifferentialError,
            match=re.escape(benchmark.CORPUS_MANIFEST_ERROR),
        ):
            benchmark._read_bounded_at(
                descriptor, "sample.json", max_bytes=benchmark.MAX_CORPUS_SAMPLE_BYTES
            )
    finally:
        os.close(descriptor)


def test_b141_loader_passes_the_sample_ceiling_to_every_sample_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _reference_authority()
    original_read = benchmark._read_bounded_at
    sample_limits: list[int] = []

    def recording_read(directory_fd: int, name: str, *, max_bytes: int) -> bytes:
        if name.endswith(".json") and name != "manifest.json":
            sample_limits.append(max_bytes)
        return original_read(directory_fd, name, max_bytes=max_bytes)

    monkeypatch.setattr(benchmark, "_read_bounded_at", recording_read)
    benchmark._load_exact_corpus(benchmark.b140.CORPUS, authority)
    assert sample_limits == [benchmark.MAX_CORPUS_SAMPLE_BYTES] * 20


@pytest.mark.parametrize(
    "mutation",
    (
        "traversal",
        "backslash_traversal",
        "duplicate",
        "extra_entry_key",
        "missing_entry_key",
        "extra_manifest_key",
        "wrong_committed_count",
        "too_few_entries",
        "too_many_entries",
    ),
)
def test_b141_manifest_preflight_rejects_noncanonical_entry_sets(mutation: str) -> None:
    manifest = _corpus_manifest()
    files = manifest["files"]
    if mutation == "traversal":
        files[0]["name"] = "../private.json"
    elif mutation == "backslash_traversal":
        files[0]["name"] = "..\\private.json"
    elif mutation == "duplicate":
        files[1]["name"] = files[0]["name"]
    elif mutation == "extra_entry_key":
        files[0]["private"] = True
    elif mutation == "missing_entry_key":
        files[0].pop("sha256")
    elif mutation == "extra_manifest_key":
        manifest["private"] = True
    elif mutation == "wrong_committed_count":
        next(entry for entry in files if entry["committed"])["committed"] = False
    elif mutation == "too_few_entries":
        files.pop()
    elif mutation == "too_many_entries":
        files.append(dict(files[-1], name="ts37_extra.json"))
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(f"unhandled mutation {mutation}")

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark.CORPUS_MANIFEST_ERROR),
    ):
        benchmark._assert_manifest_metadata(manifest)


def test_b141_loader_returns_the_digest_of_the_exact_manifest_bytes_read(
    tmp_path: Path,
) -> None:
    corpus = _copy_exact_corpus(tmp_path)
    raw_manifest = (corpus / "manifest.json").read_bytes()
    manifest, samples, manifest_sha256 = benchmark._load_exact_corpus(
        corpus, _reference_authority()
    )

    assert len(manifest["files"]) == 36
    assert len(samples) == 20
    assert manifest_sha256 == "sha256:" + hashlib.sha256(raw_manifest).hexdigest()


def test_b141_loader_rejects_license_digest_drift_before_sample_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _copy_exact_corpus(tmp_path)
    (corpus / "LICENSE").write_bytes(b"drifted licence")
    original_open_samples = benchmark._open_corpus_child_directory

    def forbidden_samples(directory_fd: int, name: str) -> int:
        if name == "samples":
            raise AssertionError("license drift reached sample access")
        return original_open_samples(directory_fd, name)

    monkeypatch.setattr(benchmark, "_open_corpus_child_directory", forbidden_samples)
    with pytest.raises(benchmark.NegotiatedDifferentialError) as captured:
        benchmark._load_exact_corpus(corpus, {})
    assert str(captured.value) == benchmark.CORPUS_MANIFEST_ERROR


@pytest.mark.parametrize("directory_kind", ("corpus", "samples"))
def test_b141_loader_rejects_symlinked_directory_components(
    tmp_path: Path, directory_kind: str
) -> None:
    authority = _reference_authority()
    if directory_kind == "corpus":
        corpus = tmp_path / "linked-corpus"
        corpus.symlink_to(benchmark.b140.CORPUS, target_is_directory=True)
    else:
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        shutil.copyfile(benchmark.b140.CORPUS / "manifest.json", corpus / "manifest.json")
        shutil.copyfile(benchmark.b140.CORPUS / "LICENSE", corpus / "LICENSE")
        (corpus / "samples").symlink_to(benchmark.b140.CORPUS / "samples", target_is_directory=True)

    with pytest.raises(benchmark.NegotiatedDifferentialError) as captured:
        benchmark._load_exact_corpus(corpus, authority)
    assert str(captured.value) == benchmark.CORPUS_MANIFEST_ERROR
    assert "linked-corpus" not in str(captured.value)


def test_b141_loader_rejects_a_symlinked_sample_file(tmp_path: Path) -> None:
    corpus = _copy_exact_corpus(tmp_path)
    first_name = next(entry["name"] for entry in _corpus_manifest()["files"] if entry["committed"])
    sample = corpus / "samples" / first_name
    sample.unlink()
    sample.symlink_to(benchmark.b140.CORPUS / "samples" / first_name)

    with pytest.raises(benchmark.NegotiatedDifferentialError) as captured:
        benchmark._load_exact_corpus(corpus, _reference_authority())
    assert str(captured.value) == benchmark.CORPUS_MANIFEST_ERROR


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_b141_loader_rejects_a_fifo_sample_without_blocking(tmp_path: Path) -> None:
    corpus = _copy_exact_corpus(tmp_path)
    first_name = next(entry["name"] for entry in _corpus_manifest()["files"] if entry["committed"])
    sample = corpus / "samples" / first_name
    sample.unlink()
    os.mkfifo(sample)

    with pytest.raises(benchmark.NegotiatedDifferentialError) as captured:
        benchmark._load_exact_corpus(corpus, _reference_authority())
    assert str(captured.value) == benchmark.CORPUS_MANIFEST_ERROR


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_b141_corpus_reader_rejects_fifo_before_reading(tmp_path: Path) -> None:
    directory = tmp_path / "fifo"
    directory.mkdir()
    os.mkfifo(directory / "sample.json")
    descriptor = benchmark._open_corpus_directory(directory)
    try:
        with pytest.raises(benchmark.NegotiatedDifferentialError) as captured:
            benchmark._read_bounded_at(
                descriptor, "sample.json", max_bytes=benchmark.MAX_CORPUS_SAMPLE_BYTES
            )
    finally:
        os.close(descriptor)
    assert str(captured.value) == benchmark.CORPUS_MANIFEST_ERROR


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
@pytest.mark.parametrize("label", ("B-141", "B-141 commitment"))
def test_caller_selected_artifact_fifo_refuses_promptly_without_blocking(
    tmp_path: Path, label: str
) -> None:
    """Report and commitment readers must reject a FIFO before any blocking read."""

    path = tmp_path / "selected.json"
    os.mkfifo(path)
    code = (
        "from pathlib import Path; "
        "from scripts import benchmark_negotiated_multipin_branch_repair as b; "
        f"p=Path({str(path)!r}); "
        f"\ntry: b._read_bounded_bytes(p, label={label!r})\n"
        "except Exception as error: print(type(error).__name__); raise SystemExit(3)"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    assert completed.returncode != 0
    assert str(path) not in completed.stderr
    assert path.name not in completed.stderr


@pytest.mark.parametrize("label", ("B-141", "B-141 commitment"))
def test_caller_selected_artifact_rejects_a_symlinked_parent_without_echoing_path(
    tmp_path: Path, label: str
) -> None:
    real_parent = tmp_path / "real-parent"
    nested_parent = real_parent / "nested"
    nested_parent.mkdir(parents=True)
    target = nested_parent / "selected.json"
    target.write_text("{}", encoding="utf-8")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    selected = linked_parent / nested_parent.name / target.name

    with pytest.raises(benchmark.NegotiatedDifferentialError) as captured:
        benchmark._read_bounded_bytes(selected, label=label)
    assert str(selected) not in str(captured.value)
    assert selected.name not in str(captured.value)


@pytest.mark.parametrize("kind", ("symlink", "fifo"))
@pytest.mark.parametrize("label", ("B-141", "B-141 commitment"))
def test_caller_selected_artifact_rejects_final_nonregular_without_echoing_path(
    tmp_path: Path, kind: str, label: str
) -> None:
    path = tmp_path / f"selected-{kind}.json"
    if kind == "symlink":
        target = tmp_path / "regular.json"
        target.write_text("{}", encoding="utf-8")
        path.symlink_to(target)
    else:
        os.mkfifo(path)

    with pytest.raises(benchmark.NegotiatedDifferentialError) as captured:
        benchmark._read_bounded_bytes(path, label=label)
    assert str(captured.value) == f"the {label} artifact is unreadable"
    assert str(path) not in str(captured.value)
    assert path.name not in str(captured.value)


@pytest.mark.parametrize("mismatch", ("length", "digest"))
def test_b141_loader_rejects_declared_sample_mismatch_at_the_read_boundary(
    tmp_path: Path, mismatch: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _copy_exact_corpus(tmp_path)
    manifest = _corpus_manifest()
    first = next(entry for entry in manifest["files"] if entry["committed"])
    sample = corpus / "samples" / first["name"]
    payload = sample.read_bytes()
    if mismatch == "length":
        sample.write_bytes(payload + b"x")
    else:
        sample.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    monkeypatch.setattr(benchmark, "_assert_manifest_matches_samples", lambda *_args: None)
    monkeypatch.setattr(benchmark, "_assert_exact_corpus_membership", lambda *_args: None)

    with pytest.raises(benchmark.NegotiatedDifferentialError) as captured:
        benchmark._load_exact_corpus(corpus, {})
    assert str(captured.value) == benchmark.CORPUS_MANIFEST_ERROR


@pytest.mark.parametrize("shortfall", [15, 0])
def test_b141_iteration_floor_accepts_exact_and_refuses_shortfall(shortfall: int) -> None:
    population = dict(EXPECTED_POPULATION)
    aggregate = _synthetic_public_report()["metrics"]["control"]
    bounds = benchmark._upper_bounds()
    benchmark._validate_aggregate(aggregate, treatment=False, population=population, bounds=bounds)
    aggregate["total_iterations"] = shortfall
    with pytest.raises(benchmark.NegotiatedDifferentialError, match="iteration floor"):
        benchmark._validate_aggregate(
            aggregate, treatment=False, population=population, bounds=bounds
        )


def test_b141_measurement_rejects_zero_iteration_on_one_of_two_admitted_boards(
    prepared_population: tuple[tuple[benchmark.PreparedBoard, ...], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, _population = prepared_population
    boards = tuple(board for board in prepared if board.envelope is not None)[:2]
    results = iter(
        (
            NegotiatedRoutingResult(
                status=NegotiatedRoutingStatus.NO_PATH,
                board_revision=boards[0].problem.snapshot.snapshot_digest,
                iterations=0,
            ),
            NegotiatedRoutingResult(
                status=NegotiatedRoutingStatus.NO_PATH,
                board_revision=boards[1].problem.snapshot.snapshot_digest,
                iterations=2,
            ),
        )
    )
    monkeypatch.setattr(benchmark, "negotiate_routes", lambda *_args, **_kwargs: next(results))
    with pytest.raises(benchmark.NegotiatedDifferentialError, match="zero iterations"):
        benchmark._measure_configuration(boards, treatment=False)


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
        "total_iterations": population["boards_admitted_by_the_coordinator"],
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
        "date_utc": "2026-08-31",
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
        # The shape layer no longer pins a date literal -- the value is bound to the recorded
        # commit by `_validate_evidence_date_binding`, which
        # `test_evidence_date_must_be_the_recorded_commits_utc_date` exercises.  What the shape
        # layer still owes is that the field is a real calendar day.
        (("date_utc",), "2026-13-45"),
        (("date_utc",), "31-08-2026"),
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


def test_authoritative_load_rejects_a_self_resigned_source_without_its_pinned_runner_blob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generic self-digests cannot make an arbitrary source revision authoritative."""

    tampered = _synthetic_public_report()
    tampered["source_commit"] = "f" * 40
    _retag_document(tampered)
    commitment = _commitment()
    commitment["source_commit"] = tampered["source_commit"]
    _retag_commitment(commitment)

    # Both documents remain independently well-formed and self-consistent.  The authoritative
    # check must still reject: a revision nobody can resolve cannot make anything authoritative.
    # The refusal names absence rather than alleging a runner mismatch nobody looked for -- this
    # revision names no object, so no runner blob was ever compared against it.
    benchmark.validate_report(tampered)
    benchmark.validate_commitment(commitment)
    monkeypatch.setattr(benchmark, "load_b140_artifact", lambda: {})
    monkeypatch.setattr(benchmark, "load_reference_artifact", lambda: {})
    monkeypatch.setattr(benchmark, "_reference_authority", lambda _document: {})
    monkeypatch.setattr(
        benchmark,
        "_load_exact_corpus",
        lambda _corpus, _authority: (
            {},
            tuple(range(EXPECTED_POPULATION["boards_offered"])),
            tampered["population_binding"]["corpus_manifest_sha256"],
        ),
    )
    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=re.escape(benchmark._COMMIT_ABSENT_ERROR)
    ):
        benchmark._validate_authoritative_bindings(tampered)


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


@pytest.mark.parametrize("total_ripups, accepted", ((490, True), (491, False)))
def test_total_ripups_uses_the_exact_490_transaction_boundary(
    total_ripups: int, accepted: bool
) -> None:
    """The closed rip-up ceiling is 70 submitted nets times seven retry gaps."""

    report = _synthetic_public_report()
    report["metrics"]["treatment"]["total_ripups"] = total_ripups
    _retag_document(report)

    if accepted:
        benchmark.validate_report(report)
    else:
        with pytest.raises(benchmark.NegotiatedDifferentialError, match="closed bound"):
            benchmark.validate_report(report)


@pytest.mark.parametrize(
    "total_wire_length_nm, accepted",
    ((4_375_000_000_000, True), (4_375_000_000_001, False)),
)
def test_total_wire_length_uses_the_exact_submitted_net_boundary(
    total_wire_length_nm: int, accepted: bool
) -> None:
    """The wire-length ceiling is one maximum grid path per submitted net."""

    report = _synthetic_public_report()
    treatment = report["metrics"]["treatment"]
    treatment["total_wire_length_nm"] = total_wire_length_nm
    report["metrics"]["differential"]["total_wire_length_nm_delta"] = total_wire_length_nm
    _retag_document(report)

    if accepted:
        benchmark.validate_report(report)
    else:
        with pytest.raises(benchmark.NegotiatedDifferentialError, match="closed bound"):
            benchmark.validate_report(report)


def test_json_artifact_reader_accepts_exact_64_kib_with_trailing_whitespace(
    tmp_path: Path,
) -> None:
    path = tmp_path / "exact.json"
    path.write_bytes(_boundary_json(benchmark.MAX_JSON_ARTIFACT_BYTES))

    assert benchmark._load_object(path, label="B-141") == {"boundary": True}


@pytest.mark.parametrize("label", ("B-141", "B-141 commitment"))
@pytest.mark.parametrize("relative", (False, True))
def test_caller_selected_regular_json_paths_keep_exact_64_kib_boundary(
    tmp_path: Path, label: str, relative: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / f"selected-{label.replace(' ', '-')}.json"
    path.write_bytes(_boundary_json(benchmark.MAX_JSON_ARTIFACT_BYTES))
    selected = path
    if relative:
        monkeypatch.chdir(tmp_path)
        selected = Path(path.name)

    assert benchmark._read_bounded_bytes(selected, label=label) == path.read_bytes()

    path.write_bytes(_boundary_json(benchmark.MAX_JSON_ARTIFACT_BYTES + 1))
    with pytest.raises(benchmark.NegotiatedDifferentialError) as captured:
        benchmark._read_bounded_bytes(selected, label=label)
    assert str(captured.value) == f"the {label} artifact exceeds 64 KiB"
    assert str(selected) not in str(captured.value)
    assert path.name not in str(captured.value)


@pytest.mark.parametrize("loader", ("report", "commitment"))
def test_public_loaders_reject_oversized_valid_json_before_parsing(
    loader: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / f"oversized-{loader}.json"
    path.write_bytes(_boundary_json(benchmark.MAX_JSON_ARTIFACT_BYTES + 1))

    def parsing_must_not_start(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("oversized input must be refused before JSON parsing")

    monkeypatch.setattr(benchmark.json, "loads", parsing_must_not_start)
    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=(
            r"^the B-141 artifact exceeds 64 KiB$"
            if loader == "report"
            else r"^the B-141 commitment artifact exceeds 64 KiB$"
        ),
    ) as error:
        if loader == "report":
            benchmark.load_artifact(path)
        else:
            benchmark.load_commitment(path)
    assert str(path) not in str(error.value)
    assert path.name not in str(error.value)
    assert "boundary" not in str(error.value)


def test_bounded_reader_requests_only_one_byte_beyond_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "reader.json"
    path.write_bytes(b"ignored")
    requested: list[int] = []
    opened_descriptor = -1

    class FakeReader:
        def __enter__(self) -> FakeReader:
            return self

        def __exit__(self, *_args: Any) -> None:
            os.close(opened_descriptor)

        def read(self, count: int = -1) -> bytes:
            requested.append(count)
            return b"{}"

    def fake_fdopen(descriptor: int, *_args: Any, **_kwargs: Any) -> FakeReader:
        nonlocal opened_descriptor
        opened_descriptor = descriptor
        return FakeReader()

    monkeypatch.setattr(benchmark.os, "fdopen", fake_fdopen)
    assert benchmark._read_bounded_bytes(path, label="B-141") == b"{}"
    assert requested == [benchmark.MAX_JSON_ARTIFACT_BYTES + 1]


def test_commitment_builder_rejects_direct_oversized_bytes_before_json_work() -> None:
    oversized = _boundary_json(benchmark.MAX_JSON_ARTIFACT_BYTES + 1)
    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=r"^the B-141 artifact exceeds 64 KiB$",
    ):
        benchmark._build_commitment_from_bytes(
            _synthetic_public_report(), benchmark.DEFAULT_OUTPUT, oversized
        )


def test_json_recursion_error_maps_to_fixed_unreadable_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "recursive.json"
    path.write_bytes(b"{}")
    monkeypatch.setattr(
        benchmark.json, "loads", lambda *_args, **_kwargs: (_ for _ in ()).throw(RecursionError)
    )

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=r"^the B-141 artifact is unreadable$",
    ):
        benchmark._load_object(path, label="B-141")


def test_numeric_recursion_error_maps_to_fixed_unsafe_numbers_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "recursive-number.json"
    path.write_bytes(b"{}")
    monkeypatch.setattr(
        benchmark,
        "_assert_finite_json_numbers",
        lambda _value: (_ for _ in ()).throw(RecursionError),
    )

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=r"^the B-141 artifact contains unsafe numbers$",
    ):
        benchmark._load_object(path, label="B-141")


def test_self_resigned_same_total_refusal_reason_swap_is_rejected() -> None:
    tampered = _synthetic_public_report()
    refusals = tampered["metrics"]["treatment"]["refusal_breakdown"]
    refusals["no_path_physical_clearance"] = 14
    refusals["no_path_search"] = 1
    _resign_public_report(tampered)

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="refusal"):
        benchmark.validate_report(tampered)


def test_self_resigned_envelope_outcome_must_match_fixed_population() -> None:
    tampered = _synthetic_public_report()
    control = tampered["metrics"]["control"]
    outcomes = control["outcome_breakdown"]
    refusals = control["refusal_breakdown"]
    repairs = control["repair_outcome_breakdown"]
    statuses = control["status_breakdown"]

    # Move one refusal unit while preserving every aggregate total and closed taxonomy.  The
    # fixed population still contains four non-constructible envelopes, so only the population
    # binding can reject this self-consistent arm rewrite.
    outcomes["envelope_construction"] -= 1
    outcomes["no_path_physical_clearance"] += 1
    refusals["envelope_construction"] -= 1
    refusals["no_path_physical_clearance"] += 1
    repairs["not_applicable_envelope_refused"] -= 1
    repairs["repair_not_published"] += 1
    statuses["not_run"] -= 1
    statuses["no_path"] += 1
    _resign_public_report(tampered)

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match="envelope refusal count drifted from the fixed population",
    ):
        benchmark.validate_report(tampered)


def test_self_resigned_control_cannot_claim_repair_or_repair_work() -> None:
    """A control arm cannot manufacture repair evidence by re-signing every linked counter."""

    tampered = _synthetic_public_report()
    control = tampered["metrics"]["control"]
    outcomes = control["outcome_breakdown"]
    refusals = control["refusal_breakdown"]
    repairs = control["repair_outcome_breakdown"]
    statuses = control["status_breakdown"]
    work = control["repair_work"]

    # Move one admitted refusal to a completed-with-repair result and update every generic
    # reconciliation field.  The report remains self-digested and its control boundary remains
    # explicitly disabled, so only the control-only repair guard may reject this claim.
    outcomes["no_path_physical_clearance"] -= 1
    outcomes["completed_with_repair"] = 1
    refusals["no_path_physical_clearance"] -= 1
    repairs["repair_not_published"] -= 1
    repairs["repair_published"] = 1
    statuses["no_path"] -= 1
    statuses["completed"] = 1
    control["boards_completed"] = 1
    control["negotiated_nets_completed"] = 2
    control["total_physical_checks"] = 1
    work["published_repairs"] = 1
    work["repair_local_expanded_states"] = 1
    _reconcile_differential(tampered)

    assert control["repair_enabled"] is False
    assert control["repair_settings"] is None
    assert outcomes["completed_with_repair"] > 0
    assert repairs["repair_published"] > 0
    assert any(work.values())
    assert sum(outcomes.values()) == control["boards_offered"]
    assert sum(refusals.values()) == sum(outcomes[key] for key in benchmark.REFUSAL_TAXONOMY)
    assert sum(repairs.values()) == control["boards_offered"]
    assert statuses["completed"] == control["boards_completed"]
    assert {key: control[key] for key in EXPECTED_POPULATION} == EXPECTED_POPULATION
    assert tampered["metrics"]["differential"] == {
        "boards_completed_delta": 0,
        "negotiated_nets_completed_delta": 0,
        "total_wire_length_nm_delta": 0,
        "total_overflow_units_delta": 0,
        "total_physical_checks_delta": 0,
        "positive_completion_delta": False,
        "verdict": "zero_or_negative_completion_delta",
    }
    assert tampered["run_id"] == _canonical_digest(
        {key: value for key, value in tampered.items() if key != "run_id"}
    )

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark._CONTROL_REPAIR_CLAIMS_ERROR),
    ):
        benchmark.validate_report(tampered)


def test_self_resigned_status_categories_must_match_outcome_categories() -> None:
    """Equal totals do not make a treatment status/category contradiction valid."""

    tampered = _synthetic_public_report()
    treatment = tampered["metrics"]["treatment"]
    outcomes = treatment["outcome_breakdown"]
    refusals = treatment["refusal_breakdown"]
    repairs = treatment["repair_outcome_breakdown"]
    statuses = treatment["status_breakdown"]

    # Keep refusal, repair, population, and differential totals unchanged while moving one
    # refusal category.  Retain the old no_path status and explicitly add the zero partial status;
    # only category-to-status reconciliation can reject this self-resigned report.
    outcomes["no_path_physical_clearance"] -= 1
    outcomes["partial_budget"] += 1
    refusals["no_path_physical_clearance"] -= 1
    refusals["partial_budget"] += 1
    statuses["partial"] = 0
    _reconcile_differential(tampered)

    assert outcomes["no_path_physical_clearance"] == 14
    assert outcomes["partial_budget"] == 1
    assert refusals["no_path_physical_clearance"] == 14
    assert refusals["partial_budget"] == 1
    assert statuses["no_path"] == 15
    assert statuses["partial"] == 0
    assert sum(outcomes.values()) == treatment["boards_offered"]
    assert sum(refusals.values()) == sum(outcomes[key] for key in benchmark.REFUSAL_TAXONOMY)
    assert repairs["repair_published"] == outcomes["completed_with_repair"]
    assert repairs["repair_not_published"] == 15
    assert repairs["not_applicable_envelope_refused"] == outcomes["envelope_construction"]
    assert sum(repairs.values()) == treatment["boards_offered"]
    assert {key: treatment[key] for key in EXPECTED_POPULATION} == EXPECTED_POPULATION
    assert sum(statuses.values()) == treatment["boards_offered"]
    assert tampered["metrics"]["differential"] == {
        "boards_completed_delta": 1,
        "negotiated_nets_completed_delta": 2,
        "total_wire_length_nm_delta": 0,
        "total_overflow_units_delta": 0,
        "total_physical_checks_delta": 1,
        "positive_completion_delta": True,
        "verdict": "positive_completion_delta",
    }

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark._STATUS_OUTCOME_RECONCILIATION_ERROR),
    ):
        benchmark.validate_report(tampered)


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
    tmp_path: Path,
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
    corpus = _copy_exact_corpus(tmp_path)
    (corpus / "manifest.json").write_text(json.dumps(mutated_manifest), encoding="utf-8")
    (corpus / "samples" / omitted[0]).write_bytes(duplicate[1])
    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark.CORPUS_MANIFEST_ERROR),
    ):
        benchmark._load_exact_corpus(corpus, _reference_authority())


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
        iterations=1,
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
    aggregate["outcome_breakdown"]["envelope_construction"] = 4
    aggregate["outcome_breakdown"]["no_path_physical_clearance"] = 16
    aggregate["refusal_breakdown"]["envelope_construction"] = 4
    aggregate["refusal_breakdown"]["no_path_physical_clearance"] = 16
    aggregate["repair_outcome_breakdown"]["not_applicable_envelope_refused"] = 4
    aggregate["repair_outcome_breakdown"]["repair_not_published"] = 16
    aggregate["status_breakdown"] = {"no_path": 16, "not_run": 4}
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
    # The evidence date is derived from the recorded commit, so the explicit revision must be one
    # this repository actually has.  `B140_SOURCE_COMMIT` is a real commit, but it is reachable
    # only from an unrelated side branch: a CI checkout fetches the PR ref and its base, so even at
    # `fetch-depth: 0` that object is absent and `git show` exits 128.  HEAD is an ancestor of
    # whatever ref was checked out, so it resolves anywhere.  Preservation is still pinned, because
    # the Git probe is patched to report a *different* revision: a `build_report` that ignored its
    # argument would record that one instead.
    source_commit = benchmark._git_state()[0]
    monkeypatch.setattr(benchmark, "_git_state", lambda: ("b" * 40, ()))
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
    assert report["source_commit"] != "b" * 40
    assert report["date_utc"] == benchmark._commit_utc_date(source_commit)
    assert report["run_id"] == _canonical_digest(
        {key: value for key, value in report.items() if key != "run_id"}
    )

    # A revision this repository does not have is refused as absent, not as a date disagreement:
    # no date was ever read, so no disagreement was ever observed.
    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=re.escape(benchmark._COMMIT_ABSENT_ERROR)
    ):
        benchmark.build_report(repetitions=2, source_commit="a" * 40)


def test_load_artifact_rejects_a_payload_tamper_before_accepting_it(
    tmp_path: Path,
) -> None:
    tampered = _artifact()
    tampered["metrics"]["differential"]["total_physical_checks_delta"] += 1
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


def test_exact_pins_reject_self_resigned_treatment_total_with_patched_metrics_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tampered arm total cannot become authoritative by re-signing the metrics projection."""

    tampered = _artifact()
    tampered["metrics"]["treatment"]["total_iterations"] += 1
    _retag_document(tampered)

    # The arm-total mutation remains within the generic semantic bounds.  Patch the source-owned
    # digest authority so this isolated assertion reaches the exact treatment arm pin.
    benchmark.validate_report(tampered)
    monkeypatch.setattr(benchmark, "B141_METRICS_SHA256", _canonical_digest(tampered["metrics"]))

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="commitment arm"):
        benchmark._validate_exact_measurement_pins(tampered)


def test_exact_pins_reject_repair_work_detail_by_name_and_by_metrics_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repair-work detail is a claim, so it is pinned by name -- and by the whole-metrics digest."""

    tampered = _artifact()
    tampered["metrics"]["treatment"]["repair_work"]["repair_local_expanded_states"] += 1
    _retag_document(tampered)

    # The report is still semantically valid and the differential is untouched: only a repair-work
    # counter moved.  It used to sit outside the enumerated pins and be caught by the digest alone.
    benchmark.validate_report(tampered)
    assert tampered["metrics"]["differential"] == benchmark._COMMITMENT_DIFFERENTIAL_EXPECTED
    assert (
        benchmark._measurement_arm_pin(tampered["metrics"]["treatment"])
        != benchmark._COMMITMENT_TREATMENT_EXPECTED
    )

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="commitment arm"):
        benchmark._validate_exact_measurement_pins(tampered)

    # The whole-metrics digest remains an independent second guard: even with the named arm pin
    # re-pinned to the tampered value, the source-owned digest still refuses it.
    monkeypatch.setattr(
        benchmark,
        "_COMMITMENT_TREATMENT_EXPECTED",
        benchmark._measurement_arm_pin(tampered["metrics"]["treatment"]),
    )
    with pytest.raises(benchmark.NegotiatedDifferentialError, match="metrics digest"):
        benchmark._validate_exact_measurement_pins(tampered)


def test_exact_pins_reject_self_resigned_differential_with_patched_metrics_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The differential pin remains exact even if a caller re-signs the enclosing metrics."""

    tampered = _artifact()
    tampered["metrics"]["differential"]["total_physical_checks_delta"] += 1
    _retag_document(tampered)
    monkeypatch.setattr(benchmark, "B141_METRICS_SHA256", _canonical_digest(tampered["metrics"]))

    # This deliberately changes the differential's measured claim, so use the narrow helper to
    # isolate the commitment contract rather than the generic semantic reconciliation guard.
    with pytest.raises(benchmark.NegotiatedDifferentialError, match="differential pin"):
        benchmark._validate_exact_measurement_pins(tampered)


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


# --- Family A: partitions are declared, and every count/breakdown pair reconciles ---------------


def test_declared_partitions_are_total_and_disjoint_over_the_run_outcome_taxonomy() -> None:
    """The declaration itself is checked, so a new outcome code cannot land in no bucket."""

    benchmark._assert_declared_partitions()

    for name, partition in benchmark._ARM_BREAKDOWN_PARTITIONS.items():
        buckets = [bucket for bucket, _codes in partition.buckets]
        assert set(buckets) == benchmark._PARTITION_TAXONOMIES[name]
        assigned = [code for _bucket, codes in partition.buckets for code in codes]
        assert len(assigned) == len(set(assigned))
        assert set(assigned) <= set(benchmark.RUN_OUTCOME_TAXONOMY)
        if partition.total:
            assert set(assigned) == set(benchmark.RUN_OUTCOME_TAXONOMY)
    assert set(benchmark._PARTITION_RECONCILIATION_ERRORS) == set(
        benchmark._ARM_BREAKDOWN_PARTITIONS
    )


@pytest.mark.parametrize("breakdown", ("status_breakdown", "repair_outcome_breakdown"))
def test_a_partition_that_stops_covering_its_taxonomy_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, breakdown: str
) -> None:
    """Dropping one outcome code from a total partition must fail, not publish a quiet zero."""

    partition = benchmark._ARM_BREAKDOWN_PARTITIONS[breakdown]
    bucket, codes = next(item for item in partition.buckets if item[1])
    crippled = tuple(
        (name, () if name == bucket else assigned) for name, assigned in partition.buckets
    )
    monkeypatch.setitem(
        benchmark._ARM_BREAKDOWN_PARTITIONS,
        breakdown,
        benchmark._DeclaredPartition(buckets=crippled, total=partition.total),
    )
    assert codes

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark._PARTITION_DECLARATION_ERROR),
    ):
        benchmark._assert_declared_partitions()
    with pytest.raises(benchmark.NegotiatedDifferentialError):
        benchmark.validate_report(_synthetic_public_report())


def test_self_resigned_repair_outcome_bucket_swap_is_rejected() -> None:
    """One repair outcome moved between buckets, every total preserved, still refused."""

    tampered = _synthetic_public_report()
    treatment = tampered["metrics"]["treatment"]
    repairs = treatment["repair_outcome_breakdown"]
    repairs["not_applicable_envelope_refused"] += 1
    repairs["repair_not_published"] -= 1
    _resign_public_report(tampered)

    assert sum(repairs.values()) == treatment["boards_offered"]
    assert sum(treatment["outcome_breakdown"].values()) == treatment["boards_offered"]

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark._PARTITION_RECONCILIATION_ERRORS["repair_outcome_breakdown"]),
    ):
        benchmark.validate_report(tampered)


def test_companion_arm_breakdowns_are_reconciled_against_their_own_outcome_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pin is not the only guard: a re-pinned companion must still partition its outcomes."""

    lying = json.loads(json.dumps(benchmark._COMMITMENT_TREATMENT_EXPECTED))
    lying["status_breakdown"]["no_path"] -= 1
    lying["status_breakdown"]["partial"] = 1
    # Re-pin the constant as a republication would, so only the partition guard can object.
    monkeypatch.setattr(benchmark, "_COMMITMENT_TREATMENT_EXPECTED", lying)

    assert sum(lying["status_breakdown"].values()) == 20

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark._PARTITION_RECONCILIATION_ERRORS["status_breakdown"]),
    ):
        benchmark._validate_commitment_arm(lying, lying)


# --- Family B: every ceiling is derived from the denominator that applies to it -----------------


def test_total_overflow_units_uses_the_submitted_net_grid_boundary() -> None:
    """Overflow is a per-board single-iteration snapshot; boards and iterations do not multiply."""

    bounds = benchmark._upper_bounds()
    submitted = EXPECTED_POPULATION["nets_submitted"]
    admitted = EXPECTED_POPULATION["boards_admitted_by_the_coordinator"]
    iterations = benchmark.b140.ENVELOPE_BUDGETS["max_iterations"]
    expected = submitted * benchmark.b140.ROUTER_LIMITS["max_grid_nodes"]

    assert bounds["completion_totals"]["total_overflow_units"] == expected
    # The retired ceiling multiplied two population-wide aggregates by a per-board repeat count.
    assert expected != admitted * iterations * submitted * submitted

    accepted = _synthetic_public_report()
    accepted["metrics"]["treatment"]["total_overflow_units"] = expected
    _reconcile_differential(accepted)
    benchmark.validate_report(accepted)

    refused = _synthetic_public_report()
    refused["metrics"]["treatment"]["total_overflow_units"] = expected + 1
    _reconcile_differential(refused)
    with pytest.raises(benchmark.NegotiatedDifferentialError, match="closed bound"):
        benchmark.validate_report(refused)


def test_every_completion_ceiling_states_a_denominator_that_applies_to_it() -> None:
    """No completion ceiling may be a population aggregate multiplied by the board count."""

    bounds = benchmark._upper_bounds()["completion_totals"]
    submitted = EXPECTED_POPULATION["nets_submitted"]
    admitted = EXPECTED_POPULATION["boards_admitted_by_the_coordinator"]
    iterations = benchmark.b140.ENVELOPE_BUDGETS["max_iterations"]
    grid_nodes = benchmark.b140.ROUTER_LIMITS["max_grid_nodes"]

    assert bounds == {
        "boards_completed": admitted,
        "negotiated_nets_completed": submitted,
        "total_iterations": admitted * iterations,
        "total_ripups": submitted * (iterations - 1),
        "total_wire_length_nm": submitted * grid_nodes * benchmark.b140.FIXED_GRID_STEP_NM,
        "total_overflow_units": submitted * grid_nodes,
        "total_physical_checks": (
            admitted * benchmark.b140.ENVELOPE_BUDGETS["max_total_physical_checks"]
        ),
    }
    # `nets_submitted * boards_admitted` is 16 copies of a population that exists once.
    forbidden = submitted * admitted
    assert all(value != forbidden for value in bounds.values())


# --- Family C: provenance and commitment coverage ----------------------------------------------


def test_evidence_date_must_be_the_recorded_commits_utc_date() -> None:
    """A hand-written label can name a day the run did not happen on; a derived one cannot."""

    head = benchmark._git_state()[0]
    document = {"source_commit": head, "date_utc": benchmark._commit_utc_date(head)}
    benchmark._validate_evidence_date_binding(document)

    for wrong in ("2026-01-01", "1999-12-31"):
        if wrong == document["date_utc"]:
            continue
        with pytest.raises(
            benchmark.NegotiatedDifferentialError,
            match=re.escape(benchmark._EVIDENCE_DATE_ERROR),
        ):
            benchmark._validate_evidence_date_binding({**document, "date_utc": wrong})

    # A well-formed hex that names no commit cannot supply a date at all -- and is reported as
    # absent, not as a disagreement, because no date was ever read to disagree with.
    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=re.escape(benchmark._COMMIT_ABSENT_ERROR)
    ):
        benchmark._validate_evidence_date_binding({**document, "source_commit": "0" * 40})


def test_published_artifact_date_is_its_own_recorded_commits_utc_date() -> None:
    """The published audit record's chronology is checked against Git, not asserted in prose."""

    document = _artifact()

    assert document["date_utc"] == benchmark._commit_utc_date(document["source_commit"])


def test_commitment_pins_every_claimed_arm_aggregate() -> None:
    """If an aggregate is claimed, it is signed -- enumerated from the report's own arm schema."""

    benchmark._assert_commitment_covers_claims()

    assert not (benchmark._ARM_CONFIGURATION_KEYS & benchmark._COMMITMENT_ARM_KEYS)
    assert (
        benchmark._ARM_CONFIGURATION_KEYS | benchmark._COMMITMENT_ARM_KEYS
    ) == benchmark._AGGREGATE_KEYS
    assert set(benchmark._COMMITMENT_CONTROL_EXPECTED) == benchmark._COMMITMENT_ARM_KEYS
    assert set(benchmark._COMMITMENT_TREATMENT_EXPECTED) == benchmark._COMMITMENT_ARM_KEYS
    # The two headline measurements this benchmark exists to publish are among the pins.
    assert {"total_physical_checks", "total_wire_length_nm"} <= benchmark._COMMITMENT_ARM_KEYS
    # So is every breakdown and the whole repair-work accounting.
    assert set(benchmark._ARM_BREAKDOWN_PARTITIONS) <= benchmark._COMMITMENT_ARM_KEYS
    assert "repair_work" in benchmark._COMMITMENT_ARM_KEYS


def test_a_claimed_aggregate_added_without_a_pin_fails_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future arm field cannot be published unsigned: the guard is derived, not hand-listed."""

    monkeypatch.setattr(
        benchmark, "_AGGREGATE_KEYS", benchmark._AGGREGATE_KEYS | {"total_unsigned_claim"}
    )

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark._COMMITMENT_COVERAGE_ERROR),
    ):
        benchmark._assert_commitment_covers_claims()


@pytest.mark.parametrize(
    ("field", "delta"),
    (
        ("total_physical_checks", 1000),
        ("total_wire_length_nm", 1),
        ("total_iterations", -1),
        ("total_ripups", 1),
        ("total_overflow_units", 1),
    ),
)
def test_re_signed_headline_arm_totals_are_refused_by_the_named_commitment_pin(
    field: str, delta: int
) -> None:
    """Re-signing both files and rebuilding the companion still cannot move a claimed total."""

    commitment = _commitment()
    # The untampered companion validates, so the refusal below is caused by the tamper and not by
    # a companion that was already unacceptable.
    benchmark.validate_commitment(commitment)
    commitment["treatment"] = {
        **commitment["treatment"],
        field: commitment["treatment"][field] + delta,
    }
    _retag_commitment(commitment)

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="arm pin"):
        benchmark.validate_commitment(commitment)


# --- Family D: no byte is read from an unvalidated path ------------------------------------------


@pytest.mark.parametrize("loader", ("load_artifact", "load_commitment"))
def test_untrusted_artifact_bytes_are_capped_before_any_parse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, loader: str
) -> None:
    """The ceiling is applied to the read, not to a value the parser already constructed."""

    oversized = tmp_path / "oversized.json"
    oversized.write_text(
        json.dumps({"pad": "a" * (benchmark.MAX_JSON_ARTIFACT_BYTES * 2)}), encoding="utf-8"
    )
    assert oversized.stat().st_size > benchmark.MAX_JSON_ARTIFACT_BYTES

    def refuse_to_parse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an over-limit payload reached the JSON parser")

    monkeypatch.setattr(json, "loads", refuse_to_parse)

    with pytest.raises(benchmark.NegotiatedDifferentialError, match="64 KiB"):
        getattr(benchmark, loader)(oversized)


def test_companion_breakdown_may_not_carry_a_bucket_its_declaration_does_not_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The companion's breakdowns are not key-closed elsewhere; the sum guard closes them."""

    lying = json.loads(json.dumps(benchmark._COMMITMENT_TREATMENT_EXPECTED))
    lying["status_breakdown"]["undeclared_bucket"] = 1
    monkeypatch.setattr(benchmark, "_COMMITMENT_TREATMENT_EXPECTED", lying)

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark._PARTITION_RECONCILIATION_ERRORS["status_breakdown"]),
    ):
        benchmark._validate_commitment_arm(lying, lying)


def test_companion_published_repairs_must_match_its_own_repair_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-pinned companion cannot claim repair work it did not also claim a repair for."""

    lying = json.loads(json.dumps(benchmark._COMMITMENT_TREATMENT_EXPECTED))
    lying["repair_work"]["published_repairs"] += 1
    monkeypatch.setattr(benchmark, "_COMMITMENT_TREATMENT_EXPECTED", lying)

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark._COMMITMENT_ARM_PIN_ERROR),
    ):
        benchmark._validate_commitment_arm(lying, lying)


def test_authoritative_load_rejects_a_self_resigned_evidence_date() -> None:
    """The reviewer's tampering, executed: a re-signed artifact with a lying UTC date is refused."""

    document = _artifact()
    published = benchmark._commit_utc_date(document["source_commit"])
    year, month, day = published.split("-")
    document["date_utc"] = f"{int(year) + 1:04d}-{month}-{day}"
    _retag_document(document)
    assert document["date_utc"] != published

    # Self-consistent by its own digest, and still refused.
    benchmark.validate_report(document)

    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=re.escape(benchmark._EVIDENCE_DATE_ERROR)
    ):
        benchmark._validate_authoritative_bindings(document)


def test_declared_bucket_names_must_be_the_published_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bucket the published taxonomy does not contain is a declaration error, not a zero."""

    partition = benchmark._ARM_BREAKDOWN_PARTITIONS["status_breakdown"]
    bucket = partition.buckets[0][0]
    # Every run-outcome code stays assigned exactly once, so the totality check cannot object and
    # only the bucket-name check can: the declaration now names a status the report cannot publish.
    renamed = tuple(
        ("a_status_the_report_cannot_publish" if name == bucket else name, assigned)
        for name, assigned in partition.buckets
    )
    monkeypatch.setitem(
        benchmark._ARM_BREAKDOWN_PARTITIONS,
        "status_breakdown",
        benchmark._DeclaredPartition(buckets=renamed, total=partition.total),
    )
    assert {code for _bucket, codes in renamed for code in codes} == set(
        benchmark.RUN_OUTCOME_TAXONOMY
    )

    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape(benchmark._PARTITION_DECLARATION_ERROR),
    ):
        benchmark._assert_declared_partitions()


# --- The guarantee a validation call actually reached is returned, not left to be inferred -------


def test_guarantee_levels_are_ordered_weakest_to_strongest() -> None:
    """The tuple's order is the strength order, so a caller can compare by index."""

    assert benchmark.GUARANTEE_LEVELS == (
        benchmark.GUARANTEE_SHAPE_ONLY,
        benchmark.GUARANTEE_OFFLINE,
        benchmark.GUARANTEE_REPOSITORY_BOUND,
        benchmark.GUARANTEE_COMPANION_BOUND,
    )
    assert benchmark.LOAD_ARTIFACT_GUARANTEE == benchmark.GUARANTEE_LEVELS[-1]


def test_validate_report_returns_the_guarantee_level_it_reached() -> None:
    """`validate_report` accepting a document is a weaker statement at weaker arguments."""

    document = _artifact()

    assert (
        benchmark.validate_report(document, require_semantics=False)
        == benchmark.GUARANTEE_SHAPE_ONLY
    )
    assert benchmark.validate_report(document) == benchmark.GUARANTEE_OFFLINE
    assert (
        benchmark.validate_report(document, verify_live_bindings=True)
        == benchmark.GUARANTEE_REPOSITORY_BOUND
    )


def _repository_bound_tampers() -> dict[str, dict[str, Any]]:
    """Three re-signed documents whose lie lives in the repository, not in the document."""

    date_tamper = _artifact()
    published = benchmark._commit_utc_date(date_tamper["source_commit"])
    year, month, day = published.split("-")
    date_tamper["date_utc"] = f"{int(year) + 1:04d}-{month}-{day}"
    _retag_document(date_tamper)

    commit_tamper = _artifact()
    commit_tamper["source_commit"] = "0" * 40
    _retag_document(commit_tamper)

    total_tamper = _artifact()
    total_tamper["metrics"]["treatment"]["total_physical_checks"] += 1000
    total_tamper["metrics"]["differential"]["total_physical_checks_delta"] += 1000
    _retag_document(total_tamper)

    return {
        "evidence_date": date_tamper,
        "source_commit": commit_tamper,
        "headline_total": total_tamper,
    }


@pytest.mark.parametrize("case", ("evidence_date", "source_commit", "headline_total"))
def test_the_offline_guarantee_does_not_cover_repository_bound_provenance(case: str) -> None:
    """The docstring's stated gap is real, and `load_artifact` is what closes it.

    This pins the *content* of the weaker guarantee rather than its wording: at the offline level
    each of these re-signed documents is accepted, and each is refused once the repository is
    consulted.  If a future edit silently strengthened or weakened either side, this fails.
    """

    tampered = _repository_bound_tampers()[case]

    # Accepted at the level `validate_report` actually reaches, and it says so.
    assert benchmark.validate_report(tampered) == benchmark.GUARANTEE_OFFLINE

    # Refused once the repository is consulted, which is what `load_artifact` does.
    with pytest.raises(benchmark.NegotiatedDifferentialError):
        benchmark._validate_authoritative_bindings(
            tampered, artifact_path=EXPECTED_ARTIFACT, require_commitment=True
        )


def test_authoritative_bindings_report_whether_the_companion_was_consulted() -> None:
    """`repository_bound` and `companion_bound` are different guarantees and are named apart."""

    document = _artifact()

    assert (
        benchmark._validate_authoritative_bindings(document) == benchmark.GUARANTEE_REPOSITORY_BOUND
    )
    assert (
        benchmark._validate_authoritative_bindings(
            document, artifact_path=EXPECTED_ARTIFACT, require_commitment=True
        )
        == benchmark.LOAD_ARTIFACT_GUARANTEE
    )


def test_load_artifact_refuses_a_binding_that_did_not_reach_its_documented_guarantee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future edit that drops the companion binding fails the load, not the docstring."""

    monkeypatch.setattr(
        benchmark,
        "_validate_authoritative_bindings",
        lambda *_args, **_kwargs: benchmark.GUARANTEE_REPOSITORY_BOUND,
    )

    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=re.escape(benchmark._GUARANTEE_ERROR)
    ):
        benchmark.load_artifact()


# --- An absent commit and a disagreeing commit are different facts --------------------------------


def test_recorded_commit_resolution_separates_absent_from_present() -> None:
    """`None` means "not here", which is not a verdict about the artifact."""

    head = benchmark._git_state()[0]

    assert benchmark._resolve_recorded_commit(head) == head
    assert benchmark._resolve_recorded_commit("0" * 40) is None
    # A malformed revision is an input failure, not a repository-contents failure.
    with pytest.raises(benchmark.NegotiatedDifferentialError):
        benchmark._resolve_recorded_commit("not-a-sha")


def test_an_absent_commit_is_never_reported_as_a_date_disagreement() -> None:
    """The CI defect: a real artifact in a clone without the object was blamed for tampering."""

    absent = {"date_utc": "2026-09-01", "source_commit": "0" * 40}

    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=re.escape(benchmark._COMMIT_ABSENT_ERROR)
    ):
        benchmark._validate_evidence_date_binding(absent)
    # And specifically NOT the tampering refusal.
    try:
        benchmark._validate_evidence_date_binding(absent)
    except benchmark.NegotiatedDifferentialError as error:
        assert benchmark._EVIDENCE_DATE_ERROR not in str(error)


def test_a_present_commit_with_a_wrong_date_keeps_the_tampering_refusal() -> None:
    """The other branch is unchanged: a resolvable commit that disagrees is tampering."""

    head = benchmark._git_state()[0]
    published = benchmark._commit_utc_date(head)
    year, month, day = published.split("-")
    mismatched = {"date_utc": f"{int(year) + 1:04d}-{month}-{day}", "source_commit": head}

    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=re.escape(benchmark._EVIDENCE_DATE_ERROR)
    ):
        benchmark._validate_evidence_date_binding(mismatched)


def test_an_absent_commit_is_never_reported_as_a_runner_binding_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same conflation lived in the runner binding and is separated there too."""

    document = _artifact()
    document["source_commit"] = "0" * 40

    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=re.escape(benchmark._COMMIT_ABSENT_ERROR)
    ):
        benchmark._validate_source_commit_runner_binding(document)
    assert (
        benchmark._validate_source_commit_runner_binding(document, allow_absent_commit=True)
        is False
    )

    # The other branch, without depending on any commit's reachability: point the binding at a
    # different tracked file that HEAD certainly carries.  The revision resolves, the blob is
    # fetched, and its digest is not the pinned runner's -- a real binding failure, named as one.
    present = _artifact()
    present["source_commit"] = benchmark._git_state()[0]
    monkeypatch.setattr(benchmark, "SCRIPT_PATH", "scripts/mutation_harness.py")
    with pytest.raises(benchmark.NegotiatedDifferentialError, match="source commit/runner"):
        benchmark._validate_source_commit_runner_binding(present)


def test_an_absent_commit_refuses_by_default_and_downgrades_only_on_request() -> None:
    """Direction of error: never certify what could not be checked, never allege what was not seen.

    This is the shallow-checkout case -- a genuine artifact validated in a clone that lacks its
    recorded commit.  The default refuses rather than silently accepting, because every call site
    in this repository uses validation for its exception.  A caller that opts in is told what was
    actually established: `offline`, which claims no repository binding.
    """

    document = _artifact()
    document["source_commit"] = "0" * 40
    _retag_document(document)

    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=re.escape(benchmark._COMMIT_ABSENT_ERROR)
    ):
        benchmark.validate_report(document, verify_live_bindings=True)

    assert (
        benchmark.validate_report(
            document, verify_live_bindings=True, allow_absent_source_commit=True
        )
        == benchmark.GUARANTEE_OFFLINE
    )


def test_load_artifact_never_downgrades_for_an_absent_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The authoritative path stays fail-closed: it cannot opt in, and its level check backstops."""

    monkeypatch.setattr(benchmark, "_resolve_recorded_commit", lambda _commit: None)

    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=re.escape(benchmark._COMMIT_ABSENT_ERROR)
    ):
        benchmark.load_artifact()


# --- The squash-merge orphan class -----------------------------------------------------------


def test_published_artifact_records_a_default_branch_ancestor() -> None:
    """The durable rule, mechanized: a recorded revision that no clone carries is not provenance.

    A pull request may publish its artifact from a feature-branch commit that squash-merging will
    discard.  The synthetic merge ``HEAD`` still contains that commit, so it cannot prove durable
    provenance.  This guard checks the fetched default branch instead and refuses before merge
    unless the recorded revision is already carried there.
    """

    document = _artifact()
    recorded = document["source_commit"]

    assert benchmark._resolve_recorded_commit(recorded) == recorded, (
        "the recorded B-141 source commit is not in this repository. If this branch was "
        "squash-merged, republish the artifact bound to the squash commit on the default branch; "
        "see the B-141 amendment in docs/ledgers/benchmark-ledger.md."
    )
    git = shutil.which("git")
    assert git is not None
    default_branch = "refs/remotes/origin/main"
    default_branch_probe = subprocess.run(  # noqa: S603 - fixed local Git executable and argv
        [git, "rev-parse", "--verify", f"{default_branch}^{{commit}}"],
        cwd=benchmark.ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert default_branch_probe.returncode == 0, (
        "the fetched default-branch ref is unavailable. Fetch origin/main before validating "
        "B-141 provenance."
    )
    ancestry = subprocess.run(  # noqa: S603 - fixed local Git executable and argv
        [git, "merge-base", "--is-ancestor", recorded, default_branch],
        cwd=benchmark.ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert ancestry.returncode == 0, (
        "the recorded B-141 source commit exists only outside the fetched default-branch "
        "ancestry. "
        "Republish the artifact against a commit carried by the default branch; see the B-141 "
        "amendment in docs/ledgers/benchmark-ledger.md."
    )


def test_orphaned_publication_is_archived_without_becoming_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve the retired bytes for audit without weakening the authoritative loader."""

    report_bytes = ARCHIVED_ARTIFACT.read_bytes()
    commitment_bytes = ARCHIVED_COMMITMENT.read_bytes()
    assert hashlib.sha256(report_bytes).hexdigest() == (
        "ff2bcd77814e3818a896eb2813b66def45997487301ec8954cd7614d7affc81c"
    )
    assert hashlib.sha256(commitment_bytes).hexdigest() == (
        "129be265f95519db1bb7a5856ad1323d0b57ed0fc180a9bbe6161957b83696d9"
    )

    report = json.loads(report_bytes)
    commitment = json.loads(commitment_bytes)
    assert benchmark.validate_report(report) == benchmark.GUARANTEE_OFFLINE
    benchmark.validate_commitment(commitment)
    assert report["source_commit"] == "b7c71d4d643df155c7bdcee5bac25e7d943b7031"
    assert report["run_id"] == (
        "sha256:bb73a925b00506e4c5305bd2fe0136f4d501f7351d1b78d8b8552b010cf06fe3"
    )
    assert report["timing"]["mean_wall_seconds"] == {
        "control": 40.574,
        "treatment": 41.039,
    }
    assert commitment["artifact_sha256"] == "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    assert commitment["artifact_run_id"] == report["run_id"]
    assert commitment["run_id"] == (
        "sha256:3633c0b6a1fa362d30572311968e56539cec455e39f1ddf687547592da79e397"
    )
    assert not _nested_keys(report).intersection(benchmark.FORBIDDEN_PUBLIC_KEYS)

    current = _artifact()
    assert current["source_commit"] == "86634180e5a3f0956cf2ede4168710f1fce8fbcb"
    assert current["run_id"] != report["run_id"]
    monkeypatch.setattr(
        benchmark,
        "_validate_source_commit_runner_binding",
        lambda _document, **_kwargs: True,
    )
    monkeypatch.setattr(
        benchmark,
        "_validate_evidence_date_binding",
        lambda _document, **_kwargs: True,
    )
    with pytest.raises(
        benchmark.NegotiatedDifferentialError,
        match=re.escape("the B-141 commitment artifact path is malformed"),
    ):
        benchmark.load_artifact(ARCHIVED_ARTIFACT)


def test_a_squash_orphaned_source_commit_stays_fail_closed_even_though_the_runner_survives() -> (
    None
):
    """A squash keeps the runner blob and discards the commit; content is not revision provenance.

    This is the situation a squash actually produces, reconstructed: the recorded revision is
    unreachable while the exact runner bytes it bound are still present in this repository.  The
    design deliberately does not treat that as `repository_bound` -- the artifact claims it was
    produced at a named revision, and no clone can check that claim, so certifying it green is the
    failure this benchmark exists to refuse.  It refuses, and downgrades only when asked.
    """

    document = _artifact()
    live_runner = document["configuration"]["runner_sha256"]
    document["source_commit"] = "0" * 40
    _retag_document(document)

    # The runner bytes the orphaned revision bound are still here: a squash preserves file
    # content.  Content survival is what makes this case tempting to wave through.
    assert benchmark._file_digest(benchmark.ROOT / benchmark.SCRIPT_PATH) == live_runner
    assert benchmark._resolve_recorded_commit(document["source_commit"]) is None

    # Offline, the document is entirely self-consistent and says so.
    assert benchmark.validate_report(document) == benchmark.GUARANTEE_OFFLINE

    # Consulting the repository refuses, naming absence rather than alleging a disagreement.
    with pytest.raises(
        benchmark.NegotiatedDifferentialError, match=re.escape(benchmark._COMMIT_ABSENT_ERROR)
    ):
        benchmark.validate_report(document, verify_live_bindings=True)

    # And a caller that knowingly accepts the weaker claim is told exactly what it got.
    assert (
        benchmark.validate_report(
            document, verify_live_bindings=True, allow_absent_source_commit=True
        )
        == benchmark.GUARANTEE_OFFLINE
    )
