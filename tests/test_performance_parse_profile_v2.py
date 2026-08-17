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
COMMITTED_REPORT = (
    ROOT / "benchmarks" / "results" / "performance" / "2026-08-17-performance-parse-profile-v2.json"
)


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _git() -> str:
    git = shutil.which("git")
    assert git is not None
    return git


def _git_head(root: Path) -> str:
    return subprocess.run(  # noqa: S603 - fixed local Git read
        [_git(), "rev-parse", "HEAD"],
        check=True,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


@contextmanager
def _isolated_worktree(tmp_path: Path) -> Iterator[Path]:
    worktree = tmp_path / "isolated-worktree"
    source_head = _git_head(ROOT)
    subprocess.run(  # noqa: S603 - pytest-owned non-shared local clone
        [
            _git(),
            "clone",
            "--no-local",
            "--no-checkout",
            "--quiet",
            str(ROOT),
            str(worktree),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    subprocess.run(  # noqa: S603 - exact caller revision in isolated storage
        [_git(), "checkout", "--detach", "--quiet", source_head],
        check=True,
        cwd=worktree,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        yield worktree
    finally:
        shutil.rmtree(worktree)


def _run_profile(output: Path, *, root: Path) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}
    return subprocess.run(  # noqa: S603 - fixed script and pytest-owned output
        [
            sys.executable,
            str(root / "scripts" / "performance_parse_profile_v2.py"),
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


def _run_digest(report: dict[str, object]) -> str:
    payload = dict(report)
    payload.pop("run_id")
    return _canonical_digest(payload)


def test_parse_profile_attributes_the_complete_read_without_changing_output(
    tmp_path: Path,
) -> None:
    with _isolated_worktree(tmp_path) as worktree:
        output = worktree / "profile.json"
        completed = _run_profile(output, root=worktree)
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout == output.read_text(encoding="utf-8")
        report = json.loads(completed.stdout)

    assert report["schema"] == "copper-mcp/performance-parse-profile/v2"
    assert report["identity_digest"] == _canonical_digest(report["identity"])
    assert report["run_id"] == _run_digest(report)
    assert report["identity"]["source_provenance"] == {
        "clean_worktree": True,
        "git_head": _git_head(ROOT),
    }
    assert report["identity"]["measurement_configuration"] == {
        "hotspot_limit": 12,
        "samples": 2,
        "warmups": 1,
    }
    scenario = report["scenario"]
    assert scenario["fixture"]["bytes"] == 166_070
    assert len(scenario["timing_perf_counter_ns"]["samples_ns"]) == 2
    assert len(scenario["output_digest"]) == 71
    attribution = scenario["stage_attribution_cumulative"]
    assert attribution["values_are_nested_not_additive"] is True
    assert set(attribution["stages"]) == {
        "board_ir_conversion",
        "complete_read",
        "model_conversion",
        "sexpr_parse",
        "tokenization",
    }
    assert attribution["stages"]["complete_read"]["share_of_complete_read_ppm"] == 1_000_000
    assert attribution["stages"]["sexpr_parse"]["share_of_complete_read_ppm"] >= 500_000
    assert (
        attribution["stages"]["sexpr_parse"]["cumulative_time_ns"]
        > attribution["stages"]["model_conversion"]["cumulative_time_ns"]
    )
    assert all(
        0 <= stage["share_of_complete_read_ppm"] <= 1_000_000
        for stage in attribution["stages"].values()
    )


def test_parse_profile_redacts_paths_and_binds_both_scripts(tmp_path: Path) -> None:
    with _isolated_worktree(tmp_path) as worktree:
        output = worktree / "profile.json"
        completed = _run_profile(output, root=worktree)
        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout)

    rendered = json.dumps(report, sort_keys=True)
    assert str(ROOT) not in rendered
    assert str(tmp_path) not in rendered
    identity = report["identity"]
    assert len(identity["script_sha256"]) == 71
    assert len(identity["support_script_sha256"]) == 71
    functions = [row["function"] for row in report["scenario"]["hotspots_cumulative"]]
    functions.extend(
        stage["function"]
        for stage in report["scenario"]["stage_attribution_cumulative"]["stages"].values()
    )
    assert all("/" not in function and "\\" not in function for function in functions)


def test_committed_parse_profile_is_self_digested_and_keeps_timing_out_of_identity() -> None:
    report = json.loads(COMMITTED_REPORT.read_text(encoding="utf-8"))

    assert report["identity_digest"] == _canonical_digest(report["identity"])
    assert report["run_id"] == _run_digest(report)
    assert "timing_perf_counter_ns" not in json.dumps(report["identity"], sort_keys=True)
    assert report["identity"]["measurement_configuration"] == {
        "hotspot_limit": 12,
        "samples": 5,
        "warmups": 2,
    }
    assert report["provenance"]["clean_worktree"] is True
    assert report["scenario"]["output_digest"] == (
        "sha256:ef6219e861f5bcd8d18142d1a3d16a2529e4e857a5f713c62c262eb1685d0148"
    )
    assert report["identity"]["source_provenance"] == {
        "clean_worktree": True,
        "git_head": report["provenance"]["git_head"],
    }
    attribution = report["scenario"]["stage_attribution_cumulative"]
    complete_ns = attribution["stages"]["complete_read"]["cumulative_time_ns"]
    assert complete_ns > 0
    assert all(
        0 <= stage["cumulative_time_ns"] <= complete_ns for stage in attribution["stages"].values()
    )


def test_parse_profile_refuses_a_dirty_support_script(tmp_path: Path) -> None:
    with _isolated_worktree(tmp_path) as worktree:
        support = worktree / "scripts" / "performance_profile_v1.py"
        support.write_bytes(support.read_bytes() + b"\n")
        output = worktree / "profile.json"
        completed = _run_profile(output, root=worktree)

    assert completed.returncode != 0
    assert "requires a clean tracked and untracked tree" in completed.stderr
    assert not output.exists()
