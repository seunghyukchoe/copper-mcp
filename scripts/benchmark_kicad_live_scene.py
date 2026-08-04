#!/usr/bin/env python3
"""Measure the deterministic IPC-snapshot-to-Circuit-Scene binding contract."""

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

from copper_mcp.circuit_scene import CircuitSceneError, observe_live_board_scene
from copper_mcp.config import Settings

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path("scripts/benchmark_kicad_live_scene.py")
FIXTURE = ROOT / "tests" / "fixtures" / "circuit-scene-v0.1" / "scene-region.kicad_pcb"
BENCHMARK_NAME = "kicad-ipc-live-scene-v1"
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}
REGION = {
    "min_x_nm": -1_000_000_000,
    "min_y_nm": -1_000_000_000,
    "max_x_nm": 1_000_000_000,
    "max_y_nm": 1_000_000_000,
}


class BenchmarkError(RuntimeError):
    """The fake-client scene oracle was not deterministic or bounded."""


class _Version:
    major = 10
    minor = 0
    patch = 5


class _FakeBoard:
    def __init__(self, source: str) -> None:
        self.source = source

    def get_as_string(self) -> str:
        return self.source

    def get_nets(self) -> list[object]:
        return [object() for _ in range(3)]

    def get_footprints(self) -> list[object]:
        return [object() for _ in range(2)]

    def get_pads(self) -> list[object]:
        return [object() for _ in range(2)]

    def get_tracks(self) -> list[object]:
        return [object() for _ in range(2)]

    def get_vias(self) -> list[object]:
        return [object()]

    def get_zones(self) -> list[object]:
        return [object()]

    def get_shapes(self) -> list[object]:
        return [object()]

    def get_text(self) -> list[object]:
        return []

    def get_dimensions(self) -> list[object]:
        return []

    def get_groups(self) -> list[object]:
        return []


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
    factory_calls = 0

    def factory(**_: object) -> _FakeKiCad:
        nonlocal factory_calls
        factory_calls += 1
        return _FakeKiCad(source)

    request = {"board": "live", "constraints": CONSTRAINTS, "region": REGION}
    documents: list[dict[str, Any]] = []
    latencies: list[int] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        scene = observe_live_board_scene(request, settings, client_factory=factory)
        latencies.append(time.perf_counter_ns() - started)
        documents.append(scene.to_dict())
    if len({_canonical_bytes(document) for document in documents}) != 1:
        raise BenchmarkError("live scene replay was not deterministic")
    document = documents[0]
    encoded = json.dumps(document, sort_keys=True)
    if "CopperMCP_ScenePad" in encoded or "scene-region" in encoded:
        raise BenchmarkError("live scene leaked a source path or footprint name")
    board_revision = document["board_revision"]
    snapshot_digest = document["snapshot_digest"]
    if document["board_path"] != "live" or document["supported"] is not True:
        raise BenchmarkError("live scene did not advertise its active source")

    stale_board_refusals = 0
    try:
        observe_live_board_scene(
            {**request, "expect_board_revision": "sha256:" + "0" * 64},
            settings,
            client_factory=factory,
        )
    except CircuitSceneError:
        stale_board_refusals = 1
    stale_snapshot_refusals = 0
    try:
        observe_live_board_scene(
            {
                **request,
                "expect_board_revision": board_revision,
                "expect_snapshot_digest": "sha256:" + "1" * 64,
            },
            settings,
            client_factory=factory,
        )
    except CircuitSceneError:
        stale_snapshot_refusals = 1
    if stale_board_refusals != 1 or stale_snapshot_refusals != 1:
        raise BenchmarkError("stale live scene revisions were not refused")

    calls_before_malformed = factory_calls
    malformed_request_refusals = 0
    try:
        observe_live_board_scene(
            {
                **request,
                "constraints": {
                    "clearance_nm": "not-an-integer",
                    "track_width_nm": CONSTRAINTS["track_width_nm"],
                    "via_diameter_nm": CONSTRAINTS["via_diameter_nm"],
                    "via_drill_nm": CONSTRAINTS["via_drill_nm"],
                },
            },
            settings,
            client_factory=factory,
        )
    except CircuitSceneError:
        malformed_request_refusals = 1
    if malformed_request_refusals != 1:
        raise BenchmarkError("malformed live-scene request was not refused")
    malformed_request_ipc_calls = factory_calls - calls_before_malformed
    if malformed_request_ipc_calls != 0:
        raise BenchmarkError("malformed request opened a KiCad IPC client")

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
        },
        "metrics": {
            "deterministic_replays": repetitions,
            "median_latency_ns": statistics.median(latencies),
            "board_revision": board_revision,
            "snapshot_digest": snapshot_digest,
            "scene_version": document["scene_version"],
            "board_path": document["board_path"],
            "objects_returned": document["truncation"]["objects_returned"],
            "annotations_returned": document["truncation"]["annotations_returned"],
            "stale_board_revision_refusals": stale_board_refusals,
            "stale_snapshot_digest_refusals": stale_snapshot_refusals,
            "malformed_request_refusals": malformed_request_refusals,
            "malformed_request_ipc_calls": malformed_request_ipc_calls,
            "raw_source_returned": False,
        },
        "not_claimed": [
            "live-kicad-session-success",
            "live-placement-authority",
            "live-routing-authority",
            "kicad-drc",
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
    parser.add_argument("--repetitions", type=_repetitions, default=7)
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
