"""Corpus licensing, harness determinism, and artifact self-digest tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import benchmark_simple_route_json_corpus as benchmark
from scripts import fetch_simple_route_json_corpus as fetcher

ARTIFACT = benchmark.DEFAULT_OUTPUT
CORPUS = benchmark.CORPUS


def _artifact() -> dict[str, Any]:
    document = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def test_committed_corpus_records_a_permissive_licence_and_its_own_digest() -> None:
    manifest = benchmark.load_manifest()

    assert manifest["license_spdx"] == "MIT"
    assert manifest["redistribution_allowed"] is True
    assert manifest["attribution_required"] is True
    assert manifest["upstream_commit"] == "be36518b5bf51755dae92c230061ab3cf4e3e063"
    licence = (CORPUS / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in licence
    assert "Zach Dwiel" in licence
    # Attribution must travel with the files, not live only in a research note.
    attribution = (CORPUS / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "Zach Dwiel" in attribution
    assert manifest["upstream_repository"] in attribution


def test_every_committed_sample_matches_its_recorded_digest() -> None:
    manifest = benchmark.load_manifest()
    files = manifest["files"]
    assert isinstance(files, list)

    committed = 0
    for entry in files:
        assert isinstance(entry, dict)
        path = CORPUS / "samples" / str(entry["name"])
        if not entry["committed"]:
            # A digest is recorded for every upstream file, committed or not, so a fetched
            # remainder is verifiable against the same manifest.
            assert not path.exists()
            continue
        committed += 1
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    assert committed == manifest["committed_subset_size"] == 20
    assert manifest["upstream_sample_count"] == 36
    assert len(list((CORPUS / "samples").glob("*.json"))) == committed


def test_the_committed_subset_is_a_stated_prefix_rather_than_a_selection_on_results() -> None:
    manifest = benchmark.load_manifest()
    files = manifest["files"]
    assert isinstance(files, list)
    names = [str(entry["name"]) for entry in files if isinstance(entry, dict)]

    assert names == sorted(names)
    committed = [
        str(entry["name"]) for entry in files if isinstance(entry, dict) and entry["committed"]
    ]
    assert committed == names[:20]
    assert manifest["committed_subset_rule"] == (
        "the first 20 sample filenames in upstream lexical order"
    )


def test_the_fetch_script_refuses_to_write_into_the_committed_corpus() -> None:
    with pytest.raises(fetcher.CorpusFetchError):
        fetcher.fetch(CORPUS / "samples")


def test_one_configuration_replays_identically(tmp_path: Path) -> None:
    _manifest, samples = benchmark.load_corpus()
    subset = samples[:4]

    first = benchmark.run_configuration(subset, benchmark.FIXED_POLICY)
    second = benchmark.run_configuration(subset, benchmark.FIXED_POLICY)

    assert first == second
    assert first["boards_imported"] == 4
    assert first["import_refusals"] == {}


def test_the_grid_policy_is_decided_from_geometry_before_any_routing() -> None:
    _manifest, samples = benchmark.load_corpus()
    two_pin = None
    for _name, payload in samples:
        problem = benchmark.import_simple_route_json("probe", payload)
        two_pin = next((net for net in problem.routable_nets if net.pad_count == 2), None)
        if two_pin is not None:
            break
    assert two_pin is not None

    fixed = benchmark.FIXED_POLICY.step_for(problem, two_pin.pad_ids)
    aligned = benchmark.DIVISOR_POLICY.step_for(problem, two_pin.pad_ids)

    assert fixed == benchmark.FIXED_GRID_STEP_NM
    assert aligned in benchmark.GRID_STEP_LADDER_NM
    # Calling it twice must not move: the step is a property of the net, never of a retry.
    assert aligned == benchmark.DIVISOR_POLICY.step_for(problem, two_pin.pad_ids)


def test_the_artifact_validates_against_its_own_self_digest() -> None:
    report = _artifact()
    recorded = report.pop("run_id")

    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    assert recorded == "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_the_artifact_records_every_refusal_and_claims_no_baseline() -> None:
    report = _artifact()
    metrics = report["metrics"]

    for name in ("fixed", "divisor-aligned"):
        configuration = metrics["configurations"][name]
        breakdown = configuration["outcome_breakdown"]
        attempted = sum(count for code, count in breakdown.items() if code != "no_routing_work")
        # The refusal breakdown is the result: every attempted net is accounted for by exactly one
        # outcome, so a success rate can never be computed over a quietly shortened denominator.
        assert attempted == configuration["nets_attempted"]
        assert breakdown["routed"] == configuration["nets_routed"]
        assert any(code.startswith("refused:") for code in breakdown)
        assert (
            configuration["routed_wire_length_nm"]
            >= (configuration["routed_pad_gap_lower_bound_nm"])
        )
    for baseline in metrics["baselines"].values():
        assert baseline["status"] == "not_run"
        assert "reason" in baseline
    assert metrics["deterministic_replays"] is True
    assert any("not_run" in claim for claim in report["not_claimed"])


def test_the_artifact_matches_a_fresh_run_of_the_committed_corpus() -> None:
    fresh, _timing = benchmark.run_benchmark(1)

    assert fresh == _artifact()["metrics"]
