from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "performance_profile_v1.py"
COMMITTED_REPORT = (
    ROOT / "benchmarks" / "results" / "performance" / "2026-08-05-performance-profile-v1.json"
)


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _run_digest(report: dict[str, object]) -> str:
    payload = dict(report)
    payload.pop("run_id")
    return _canonical_digest(payload)


def _git() -> str:
    git = shutil.which("git")
    assert git is not None
    return git


def _git_head(root: Path) -> str:
    return subprocess.run(  # noqa: S603 - locally resolved Git reads one local revision
        [_git(), "rev-parse", "HEAD"],
        check=True,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


@contextmanager
def _isolated_worktree(tmp_path: Path, *, source_root: Path = ROOT) -> Iterator[Path]:
    worktree = tmp_path / "isolated-worktree"
    source_head = _git_head(source_root)
    subprocess.run(  # noqa: S603 - local non-shared clone into pytest-owned storage
        [
            _git(),
            "clone",
            "--no-local",
            "--no-checkout",
            "--quiet",
            str(source_root),
            str(worktree),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    subprocess.run(  # noqa: S603 - checks out the exact caller commit in isolated storage
        [_git(), "checkout", "--detach", "--quiet", source_head],
        check=True,
        cwd=worktree,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert _git_head(worktree) == source_head
    try:
        yield worktree
    finally:
        shutil.rmtree(worktree)


def _run_profile(output: Path, *, root: Path) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}
    return subprocess.run(  # noqa: S603 - fixed script plus pytest-owned output path
        [
            sys.executable,
            str(root / "scripts" / "performance_profile_v1.py"),
            "--output",
            str(output),
            "--samples",
            "2",
            "--warmups",
            "1",
        ],
        check=False,
        capture_output=True,
        cwd=root,
        env=environment,
        text=True,
        timeout=120,
    )


def _report(tmp_path: Path, *, parent_root: Path = ROOT) -> dict[str, object]:
    parent_head = _git_head(parent_root)
    with _isolated_worktree(tmp_path, source_root=parent_root) as worktree:
        output = worktree / "profile.json"
        completed = _run_profile(output, root=worktree)
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout == output.read_text(encoding="utf-8")
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["provenance"]["git_head"] == parent_head
        return report


def test_performance_profile_has_fixed_identity_and_three_replayable_scenarios(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)

    assert report["schema"] == "copper-mcp/performance-profile/v1"
    identity = report["identity"]
    assert isinstance(identity, dict)
    assert identity["fixed_seed"] == 23
    assert identity["measurement_configuration"] == {
        "hotspot_limit": 8,
        "samples": 2,
        "warmups": 1,
    }
    assert report["identity_digest"] == _canonical_digest(identity)
    assert report["run_id"] == _run_digest(report)
    assert identity["source_provenance"]["clean_worktree"] is True
    assert report["provenance"]["clean_worktree"] is True
    assert identity["source_provenance"]["git_head"] == report["provenance"]["git_head"]
    scenarios = report["scenarios"]
    assert isinstance(scenarios, dict)
    assert set(scenarios) == {"placement", "routing", "scene"}
    for scenario in scenarios.values():
        assert isinstance(scenario, dict)
        assert len(scenario["timing_perf_counter_ns"]["samples_ns"]) == 2
        hotspots = scenario["hotspots_cumulative"]
        assert 1 <= len(hotspots) <= 8
        assert all(item["cumulative_time_ns"] >= 0 for item in hotspots)


def test_performance_profile_redacts_paths_and_orders_hotspots_by_cumulative_time(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    rendered = json.dumps(report, sort_keys=True)

    assert str(ROOT) not in rendered
    assert str(tmp_path) not in rendered
    scenarios = report["scenarios"]
    assert isinstance(scenarios, dict)
    for scenario in scenarios.values():
        assert isinstance(scenario, dict)
        hotspots = scenario["hotspots_cumulative"]
        assert isinstance(hotspots, list)
        ranking = [
            (-item["cumulative_time_ns"], -item["self_time_ns"], item["function"])
            for item in hotspots
        ]
        assert ranking == sorted(ranking)
        assert all(
            "/" not in item["function"] and "\\" not in item["function"] for item in hotspots
        )


def test_committed_performance_profile_keeps_provenance_outside_deterministic_identity() -> None:
    report = json.loads(COMMITTED_REPORT.read_text(encoding="utf-8"))

    assert report["identity_digest"] == _canonical_digest(report["identity"])
    assert report["run_id"] == _run_digest(report)
    assert report["identity"]["measurement_configuration"] == {
        "hotspot_limit": 8,
        "samples": 5,
        "warmups": 2,
    }
    assert len(report["provenance"]["git_head"]) == 40
    assert report["provenance"]["clean_worktree"] is True
    assert report["identity"]["source_provenance"] == {
        "clean_worktree": True,
        "git_head": report["provenance"]["git_head"],
    }
    rendered = json.dumps(report, sort_keys=True)
    assert str(ROOT) not in rendered
    for scenario in report["scenarios"].values():
        assert all("/" not in item["function"] for item in scenario["hotspots_cumulative"])


def test_performance_profile_refuses_dirty_tracked_source(tmp_path: Path) -> None:
    with _isolated_worktree(tmp_path) as worktree:
        script = worktree / "scripts" / "performance_profile_v1.py"
        original = script.read_bytes()
        output = worktree / "profile.json"
        try:
            script.write_bytes(original + b"\n")
            completed = _run_profile(output, root=worktree)
        finally:
            script.write_bytes(original)

    assert completed.returncode != 0
    assert "requires a clean tracked and untracked tree" in completed.stderr
    assert not output.exists()


def test_performance_profile_refuses_untracked_helper(tmp_path: Path) -> None:
    with _isolated_worktree(tmp_path) as worktree:
        helper = worktree / "performance-profile-untracked-helper.py"
        output = worktree / "profile.json"
        try:
            helper.write_text("# untracked file\n", encoding="utf-8")
            completed = _run_profile(output, root=worktree)
        finally:
            helper.unlink(missing_ok=True)

    assert completed.returncode != 0
    assert "requires a clean tracked and untracked tree" in completed.stderr
    assert not output.exists()


def test_performance_profile_replays_from_isolation_when_parent_worktree_is_dirty(
    tmp_path: Path,
) -> None:
    with _isolated_worktree(tmp_path / "dirty-parent") as dirty_parent:
        marker = dirty_parent / "untracked-parent-helper.py"
        try:
            marker.write_text("# parent-only untracked helper\n", encoding="utf-8")
            report = _report(tmp_path / "profile", parent_root=dirty_parent)
        finally:
            marker.unlink(missing_ok=True)

    assert report["identity"]["source_provenance"]["clean_worktree"] is True
