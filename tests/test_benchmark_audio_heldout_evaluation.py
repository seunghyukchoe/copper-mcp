"""Regression tests for the licence-safe held-out audio family evaluator."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import benchmark_audio_heldout_evaluation as benchmark

ARTIFACT = benchmark.ROOT / benchmark.ARTIFACT_PATH


def _replay_artifact_in_clean_detached_worktree(
    artifact: dict[str, object], tmp_path: Path
) -> dict[str, object]:
    """Run the strict artifact CLI outside the caller's potentially dirty checkout."""

    evidence = artifact["evidence"]
    assert isinstance(evidence, dict)
    source_commit = evidence["evidence_source_commit"]
    assert isinstance(source_commit, str)
    evaluation = evidence["evaluation"]
    assert isinstance(evaluation, dict)
    repetitions = evaluation["repetitions"]
    assert isinstance(repetitions, int)
    git = shutil.which("git")
    assert git is not None
    reachable = subprocess.run(  # noqa: S603 - fixed local Git argv; does not contact a remote
        [git, "merge-base", "--is-ancestor", source_commit, "HEAD"],
        cwd=benchmark.ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert reachable.returncode == 0, (
        "artifact evidence source must be reachable from the local checkout before replay; "
        f"source={source_commit} stderr={reachable.stderr}"
    )
    detached = tmp_path / "clean-evidence-clone"
    output = tmp_path / "replayed-artifact.json"
    cloned = subprocess.run(  # noqa: S603 - fixed local Git argv; writes only below tmp_path
        [git, "clone", "--no-local", "--no-checkout", str(benchmark.ROOT), str(detached)],
        cwd=benchmark.ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert cloned.returncode == 0, cloned.stderr
    checked_out = subprocess.run(  # noqa: S603 - fixed local Git argv
        [git, "checkout", "--detach", source_commit],
        cwd=detached,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert checked_out.returncode == 0, checked_out.stderr
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(detached / "src")
    replay = subprocess.run(  # noqa: S603 - fixed local interpreter and repository script
        [
            sys.executable,
            str(detached / benchmark.SCRIPT_PATH),
            "--repetitions",
            str(repetitions),
            "--reproducible-artifact",
            "--evidence-source-commit",
            source_commit,
            "--output",
            str(output),
        ],
        cwd=detached,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert replay.returncode == 0, replay.stderr
    value = json.loads(output.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_protocol_binds_original_fixture_license_and_exclusive_heldout_split() -> None:
    protocol = benchmark.load_protocol()

    assert protocol.fixture_sha256 == hashlib.sha256(benchmark.FIXTURE.read_bytes()).hexdigest()
    assert (
        protocol.license_sha256
        == hashlib.sha256((benchmark.FIXTURE_DIRECTORY / "LICENSE").read_bytes()).hexdigest()
    )
    assert protocol.route_nets == tuple(sorted(protocol.route_nets))


def test_heldout_evaluation_replays_exactly_with_placement_route_and_inspection_metrics() -> None:
    first = benchmark.run_evaluation(2)
    second = benchmark.run_evaluation(2)

    assert first == second
    assert first["deterministic_replays"] is True
    assert first["split"] == {
        "training_family_ids": ["passive-rc-low-pass"],
        "tuning_family_ids": [],
        "heldout_family_ids": ["ac-coupled-signal-chain"],
        "training_or_tuning_fixture_read": False,
    }
    metrics = first["metrics"]
    assert metrics["inspection"]["object_counts"]["footprints"] == 6
    assert metrics["inspection"]["object_counts"]["pads"] == 12
    assert metrics["placement"]["legal_candidate_count"] > 0
    assert metrics["routing"]["completion_fraction"] == 1.0
    assert metrics["routing"]["hard_internal_violations"] == 0
    assert metrics["routing"]["routed_nets"] == 6
    assert metrics["source_unchanged"] is True
    assert metrics["candidate_applied"] is False
    assert metrics["kicad_invoked"] is False


def test_evaluator_never_reads_the_declared_training_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    training_fixture = (
        benchmark.ROOT / "benchmarks/audio/fixtures/rc-low-pass-routing-v1.kicad_pcb"
    ).resolve()
    original_read_bytes = Path.read_bytes

    def reject_training_read(path: Path) -> bytes:
        if path.resolve() == training_fixture:
            raise AssertionError(
                "the training fixture must not be opened during held-out evaluation"
            )
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_training_read)

    assert benchmark.run_evaluation(2)["split"]["training_or_tuning_fixture_read"] is False


def test_duplicate_fixture_hash_across_split_partitions_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    split = json.loads(benchmark.SPLIT.read_text(encoding="utf-8"))
    heldout_hash = split["family_definitions"]["heldout"][0]["fixture_sha256"]
    split["family_definitions"]["training"][0]["fixture_sha256"] = heldout_hash
    tampered = tmp_path / "split.json"
    tampered.write_text(json.dumps(split), encoding="utf-8")
    monkeypatch.setattr(benchmark, "SPLIT", tampered)

    with pytest.raises(benchmark.HeldoutEvaluationError, match="multiple split partitions"):
        benchmark.load_protocol()


@pytest.mark.parametrize("value", (None, "not-a-hash", "f" * 63, "g" * 64))
def test_nonempty_split_entry_requires_a_valid_fixture_hash(
    value: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    split = json.loads(benchmark.SPLIT.read_text(encoding="utf-8"))
    split["family_definitions"]["training"][0]["fixture_sha256"] = value
    tampered = tmp_path / "split.json"
    tampered.write_text(json.dumps(split), encoding="utf-8")
    monkeypatch.setattr(benchmark, "SPLIT", tampered)

    with pytest.raises(benchmark.HeldoutEvaluationError, match="required and must be SHA-256"):
        benchmark.load_protocol()


def test_report_separates_host_observations_from_content_addressed_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_git_commit", lambda: "test-commit")
    timestamp = datetime(2026, 8, 5, tzinfo=UTC)

    first = benchmark.build_report(2, timestamp=timestamp)
    monkeypatch.setattr(benchmark.platform, "platform", lambda: "different-host")
    monkeypatch.setattr(benchmark.platform, "python_version", lambda: "different-python")
    second = benchmark.build_report(2, timestamp=timestamp)

    assert first["evidence"] == second["evidence"]
    assert first["evidence_run_id"] == second["evidence_run_id"]
    assert first["observations"] != second["observations"]
    expected = hashlib.sha256(
        json.dumps(
            first["evidence"], sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    assert first["evidence_run_id"] == f"sha256:{expected}"
    assert (
        "placement or routing quality improvement" in first["evidence"]["evaluation"]["not_claimed"]
    )


def test_reproducible_artifact_requires_a_clean_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_git_output", lambda _arguments: b" M fixture")

    with pytest.raises(benchmark.HeldoutEvaluationError, match="clean Git tree"):
        benchmark.build_reproducible_artifact(2, evidence_source_commit="a" * 40)


def test_reproducible_artifact_binds_all_inputs_without_host_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "a" * 40
    monkeypatch.setattr(benchmark, "_require_clean_git_tree", lambda: None)
    monkeypatch.setattr(benchmark, "_evidence_source_commit", lambda value: value)
    monkeypatch.setattr(
        benchmark,
        "_tracked_sha256",
        lambda _commit, path: benchmark._input_hashes()[path.as_posix()],
    )

    artifact = benchmark.build_reproducible_artifact(2, evidence_source_commit=commit)

    assert artifact["evidence"]["evidence_source_commit"] == commit
    assert set(artifact["evidence"]["inputs"]) == {
        "scripts/benchmark_audio_heldout_evaluation.py",
        "tests/fixtures/benchmarks/heldout-audio/LICENSE",
        "tests/fixtures/benchmarks/heldout-audio/ac-coupled-signal-chain-v1.kicad_pcb",
        "tests/fixtures/benchmarks/heldout-audio/provenance.json",
        "tests/fixtures/benchmarks/heldout-audio/split.json",
    }
    assert "observations" not in artifact
    canonical = dict(artifact)
    run_id = canonical.pop("run_id")
    assert (
        run_id
        == "sha256:"
        + hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )


def test_committed_artifact_replays_from_its_clean_evidence_source(tmp_path: Path) -> None:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(artifact, dict)

    assert _replay_artifact_in_clean_detached_worktree(artifact, tmp_path) == artifact
