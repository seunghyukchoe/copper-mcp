#!/usr/bin/env python3
"""Classify CI evidence work conservatively; uncertainty never becomes a cheap path."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PYTHON = "3.12"
ALL_PYTHONS = ("3.11", "3.12", "3.13")
FAST_MARKER = "not slow_evidence and not external_router and not networked_provider"
FULL_MARKER = "not slow_evidence"
SENSITIVE_FILES = {
    "Makefile",
    "pyproject.toml",
    "scripts/ci_test_plan.py",
    "scripts/offline_mcp_harness.py",
    "scripts/mutation_harness.py",
    "scripts/replay_source_binding.py",
    "scripts/measure_parse_memory.py",
    "tests/test_parse_memory_measurement.py",
    "pytest.ini",
    ".pytest.ini",
    "pytest.toml",
    ".pytest.toml",
    "tox.ini",
    "setup.cfg",
    ".coveragerc",
}
SENSITIVE_PREFIXES = (
    "src/",
    "benchmarks/",
    ".github/",
    "scripts/benchmark_",
    "scripts/evaluate_",
    "tests/test_benchmark_",
    "tests/test_replay_source_binding.py",
)
LOCKFILE_NAMES = {"uv.lock", "poetry.lock", "requirements.txt", "requirements-dev.txt"}
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
GIT_DIFF_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class TestPlan:
    regular_marker: str
    evidence_enabled: bool
    evidence_pythons: tuple[str, ...]
    evidence_policy: str


def _is_sensitive(changed_files: tuple[str, ...]) -> bool:
    return any(
        path in SENSITIVE_FILES
        # Conftest hooks can alter collection/execution, including in nested evidence suites.
        or path.rsplit("/", 1)[-1] == "conftest.py"
        or path in LOCKFILE_NAMES
        or path.endswith(".lock")
        or path.startswith(SENSITIVE_PREFIXES)
        for path in changed_files
    )


def make_plan(*, event_name: str, changed_files: tuple[str, ...] | None) -> TestPlan:
    """Return a closed plan; ``None`` is unknown and therefore fans evidence out."""

    if changed_files is None and event_name in {"schedule", "workflow_dispatch"}:
        changed_files = ()
    unknown = changed_files is None
    sensitive = unknown or _is_sensitive(changed_files)
    if event_name == "pull_request":
        if not sensitive:
            return TestPlan(FAST_MARKER, False, (), "skipped-nonsensitive-pr")
        return TestPlan(FAST_MARKER, True, ALL_PYTHONS, "all-interpreters-sensitive-pr")
    return TestPlan(
        FULL_MARKER,
        True,
        ALL_PYTHONS if sensitive else (CANONICAL_PYTHON,),
        "all-interpreters-sensitive" if sensitive else "canonical-full",
    )


def changed_files(*, base_sha: str, head_sha: str) -> tuple[str, ...] | None:
    if _GIT_SHA.fullmatch(base_sha) is None or _GIT_SHA.fullmatch(head_sha) is None:
        return None
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - checked-out workflow SHAs only.
            [
                git,
                "diff",
                "--no-renames",
                "--name-only",
                "-z",
                "--diff-filter=ACMRD",
                f"{base_sha}..{head_sha}",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_DIFF_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return tuple(path for path in result.stdout.split("\0") if path)


def _write_github_output(path: Path, plan: TestPlan) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(f"regular-marker={plan.regular_marker}\n")
        output.write(f"evidence-enabled={str(plan.evidence_enabled).lower()}\n")
        output.write(f"evidence-python={json.dumps(plan.evidence_pythons)}\n")
        output.write(f"evidence-policy={plan.evidence_policy}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--changed-file", action="append", default=None)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    files = (
        tuple(args.changed_file)
        if args.changed_file is not None
        else changed_files(base_sha=args.base_sha, head_sha=args.head_sha)
    )
    plan = make_plan(event_name=args.event_name, changed_files=files)
    if args.github_output is not None:
        _write_github_output(args.github_output, plan)
    else:
        print(json.dumps(plan.__dict__, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
