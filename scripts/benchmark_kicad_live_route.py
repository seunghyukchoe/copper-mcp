#!/usr/bin/env python3
"""Measure revision-bound, read-only routing over one captured KiCad IPC snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from copper_mcp.config import Settings
from copper_mcp.route_preview import (
    RoutePreviewError,
    RoutePreviewStatus,
    preview_live_route,
    preview_route,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path("scripts/benchmark_kicad_live_route.py")
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
BENCHMARK_NAME = "kicad-ipc-live-route-v1"
CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}


class BenchmarkError(RuntimeError):
    """The fake-client live-route oracle was not deterministic or bounded."""


class _Version:
    major = 10
    minor = 0
    patch = 5


class _FakeBoard:
    def __init__(self, source: str) -> None:
        self.source = source

    def get_as_string(self) -> str:
        return self.source


class _FakeKiCad:
    def __init__(self, source: str) -> None:
        self.board = _FakeBoard(source)

    def get_version(self) -> _Version:
        return _Version()

    def get_api_version(self) -> _Version:
        return _Version()

    def check_version(self) -> bool:
        return True

    def get_board(self) -> _FakeBoard:
        return self.board


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _git_metadata() -> tuple[str, bool | None, int | None]:
    git = shutil.which("git")
    if git is None:
        return "unknown", None, None
    try:
        commit = subprocess.run(  # noqa: S603 - fixed local executable and argv
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(  # noqa: S603 - fixed local executable and argv
                [git, "status", "--porcelain", "--untracked-files=no"],
                cwd=ROOT,
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            ).stdout
        )
        untracked = subprocess.run(  # noqa: S603 - fixed local executable and argv
            [git, "ls-files", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return "unknown", None, None
    return commit, dirty, len(untracked)


def _repetitions(value: str) -> int:
    count = int(value)
    if not 2 <= count <= 20:
        raise argparse.ArgumentTypeError("repetitions must be between 2 and 20")
    return count


def _run(repetitions: int) -> dict[str, Any]:
    source = FIXTURE.read_text(encoding="utf-8")
    settings = Settings(workspace=ROOT)
    named = preview_route(
        {
            "board": str(FIXTURE.relative_to(ROOT)),
            "net": "AUDIO",
            "layer": "F.Cu",
            "seed": 23,
            "constraints": CONSTRAINTS,
        },
        settings,
    )
    if named.status is not RoutePreviewStatus.ROUTED or named.candidate is None:
        raise BenchmarkError("the file-backed route oracle did not produce a candidate")
    if named.snapshot_digest is None:
        raise BenchmarkError("the file-backed route oracle omitted its snapshot digest")

    factory_calls = 0

    def factory(**_: object) -> _FakeKiCad:
        nonlocal factory_calls
        factory_calls += 1
        return _FakeKiCad(source)

    request = {
        "board": "live",
        "net_ref_id": named.candidate.patch.net_id,
        "layer": "F.Cu",
        "seed": 23,
        "constraints": CONSTRAINTS,
        "expect_board_revision": named.board_revision,
        "expect_snapshot_digest": named.snapshot_digest,
    }
    documents: list[dict[str, Any]] = []
    latencies: list[int] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        preview = preview_live_route(request, settings, client_factory=factory)
        latencies.append(time.perf_counter_ns() - started)
        if preview.status is not RoutePreviewStatus.ROUTED or preview.candidate is None:
            raise BenchmarkError("live route did not produce a candidate")
        if preview.candidate != named.candidate:
            raise BenchmarkError("live route diverged from the same-byte file oracle")
        documents.append(preview.to_dict())

    if len({_canonical_bytes(document) for document in documents}) != 1:
        raise BenchmarkError("live route replay was not deterministic")
    document = documents[0]
    encoded = json.dumps(document, sort_keys=True)
    if "two-pad.kicad_pcb" in encoded or "AUDIO" in encoded:
        raise BenchmarkError("live route leaked a source path or private net name")
    if document["board_path"] != "live" or document["apply_token"] is not None:
        raise BenchmarkError("live route returned an invalid mutation boundary")

    stale_board_refusals = 0
    stale = {**request, "expect_board_revision": "sha256:" + "0" * 64}
    stale_preview = preview_live_route(stale, settings, client_factory=factory)
    if (
        stale_preview.status is RoutePreviewStatus.NOT_ROUTED
        and stale_preview.diagnostic is not None
        and str(stale_preview.diagnostic.code) == "stale_revision"
    ):
        stale_board_refusals = 1

    stale_snapshot_refusals = 0
    stale = {**request, "expect_snapshot_digest": "sha256:" + "1" * 64}
    stale_preview = preview_live_route(stale, settings, client_factory=factory)
    if (
        stale_preview.status is RoutePreviewStatus.NOT_ROUTED
        and stale_preview.snapshot_digest is not None
        and stale_preview.diagnostic is not None
        and str(stale_preview.diagnostic.code) == "stale_revision"
    ):
        stale_snapshot_refusals = 1

    calls_before_forbidden = factory_calls
    forbidden_action_refusals = 0
    try:
        preview_live_route({**request, "include_drc": True}, settings, client_factory=factory)
    except RoutePreviewError:
        forbidden_action_refusals += 1
    forbidden_action_ipc_calls = factory_calls - calls_before_forbidden
    if forbidden_action_refusals != 1 or forbidden_action_ipc_calls != 0:
        raise BenchmarkError("live route action authority crossed the IPC preflight")
    if stale_board_refusals != 1 or stale_snapshot_refusals != 1:
        raise BenchmarkError("stale live route revisions were not refused")

    commit, tracked_dirty, untracked = _git_metadata()
    return {
        "benchmark": BENCHMARK_NAME,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_commit": commit,
        "tracked_worktree_dirty": tracked_dirty,
        "untracked_file_count": untracked,
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "physical_memory_bytes": _physical_memory_bytes(),
            "dependencies": {
                "mcp": _package_version("mcp"),
                "pydantic": _package_version("pydantic"),
                "kicad-python": _package_version("kicad-python"),
            },
            "kicad_invoked": False,
            "live_ipc_probe": "not_run_kicad_api_server_disabled",
        },
        "configuration": {
            "script_sha256": hashlib.sha256((ROOT / SCRIPT_PATH).read_bytes()).hexdigest(),
            "repetitions": repetitions,
            "transport_path": "fake_kicad_python_client",
            "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            "router_oracle": "file-backed-preview-route-v1",
        },
        "metrics": {
            "deterministic_replays": repetitions,
            "median_latency_ns": statistics.median(latencies),
            "board_revision": document["board_revision"],
            "snapshot_digest": document["snapshot_digest"],
            "status": document["status"],
            "candidate_id": document["candidate"]["candidate_id"],
            "candidate_base_revision": document["candidate"]["base_revision"],
            "board_path": document["board_path"],
            "stale_board_revision_refusals": stale_board_refusals,
            "stale_snapshot_digest_refusals": stale_snapshot_refusals,
            "forbidden_action_refusals": forbidden_action_refusals,
            "forbidden_action_ipc_calls": forbidden_action_ipc_calls,
            "raw_source_returned": False,
            "drc_evidence_returned": document["drc_evidence"] is not None,
            "fill_authority_returned": document["fill_authority"] is not None,
            "apply_token_returned": document["apply_token"] is not None,
        },
        "not_claimed": [
            "live-kicad-session-success",
            "live-placement-authority",
            "live-route-application",
            "kicad-drc",
            "zone-fill-authority",
            "erc",
            "electrical-validation",
            "fabrication-readiness",
        ],
    }


def _physical_memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=_repetitions, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = _run(args.repetitions)
    run_id = "sha256:" + hashlib.sha256(_canonical_bytes(document)).hexdigest()
    document = {"run_id": run_id, **document}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical_bytes(document) + b"\n")
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
