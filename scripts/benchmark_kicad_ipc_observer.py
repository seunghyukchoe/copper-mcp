#!/usr/bin/env python3
"""Measure the deterministic, redacted contract of the optional KiCad IPC observer."""

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
from copper_mcp.kicad_ipc import (
    KicadIpcConfigurationError,
    KicadIpcVersionError,
    LiveBoardObservation,
    inspect_live_board,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path("scripts/benchmark_kicad_ipc_observer.py")
BENCHMARK_NAME = "kicad-ipc-observer-v1"


class BenchmarkError(RuntimeError):
    """The observer did not satisfy its declared deterministic oracle."""


class _Version:
    def __init__(self, major: int, minor: int, patch: int) -> None:
        self.major = major
        self.minor = minor
        self.patch = patch


class FutureVersionError(Exception):
    """Fake official-binding version error used by the fail-closed oracle."""


_PRIVATE_OBJECT_MARKERS = (
    "PRIVATE_IPC_NET_OBJECT",
    "PRIVATE_IPC_FOOTPRINT_OBJECT",
    "PRIVATE_IPC_PAD_OBJECT",
    "PRIVATE_IPC_TRACK_OBJECT",
    "PRIVATE_IPC_VIA_OBJECT",
    "PRIVATE_IPC_ZONE_OBJECT",
    "PRIVATE_IPC_SHAPE_OBJECT",
    "PRIVATE_IPC_TEXT_OBJECT",
)


class _PrivateObject:
    """Identifiable fake binding object used to detect accidental raw-object leakage."""

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def __repr__(self) -> str:
        return self.marker


class _FakeBoard:
    source = (
        '(kicad_pcb (net 1 "BOARD_AUTHOR_TEXT") '
        '(footprint "F" (pad "1" (net 1 "BOARD_AUTHOR_TEXT"))) (gr_circle))'
    )

    def get_as_string(self) -> str:
        return self.source

    def get_nets(self) -> list[object]:
        return [_PrivateObject(_PRIVATE_OBJECT_MARKERS[0]) for _ in range(3)]

    def get_footprints(self) -> list[object]:
        return [_PrivateObject(_PRIVATE_OBJECT_MARKERS[1]) for _ in range(2)]

    def get_pads(self) -> list[object]:
        return [_PrivateObject(_PRIVATE_OBJECT_MARKERS[2]) for _ in range(6)]

    def get_tracks(self) -> list[object]:
        return [_PrivateObject("PRIVATE_IPC_TRACK_OBJECT") for _ in range(4)]

    def get_vias(self) -> list[object]:
        return [_PrivateObject("PRIVATE_IPC_VIA_OBJECT")]

    def get_zones(self) -> list[object]:
        return [_PrivateObject("PRIVATE_IPC_ZONE_OBJECT")]

    def get_shapes(self) -> list[object]:
        return [_PrivateObject("PRIVATE_IPC_SHAPE_OBJECT") for _ in range(3)]

    def get_text(self) -> list[object]:
        return [_PrivateObject("PRIVATE_IPC_TEXT_OBJECT")]

    def get_dimensions(self) -> list[object]:
        return []

    def get_groups(self) -> list[object]:
        return []


class _FakeKiCad:
    def __init__(self, *, future: bool = False) -> None:
        self.future = future
        self.board = _FakeBoard()

    def get_version(self) -> _Version:
        return _Version(10, 0, 5)

    def get_api_version(self) -> _Version:
        return _Version(10, 0, 5)

    def check_version(self) -> bool:
        if self.future:
            raise FutureVersionError()
        return True

    def get_board(self) -> _FakeBoard:
        return self.board


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


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
    # The benchmark drives a deterministic fake client, but it still goes through the real
    # capture path, which is operator-gated. Enable it explicitly here rather than depending
    # on the ambient environment.
    settings = Settings(workspace=ROOT, allow_live_ipc=True)
    factory = lambda **_: _FakeKiCad()  # noqa: E731 - test factory is intentionally tiny
    observations: list[LiveBoardObservation] = []
    latencies: list[int] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        observations.append(inspect_live_board(settings, client_factory=factory))
        latencies.append(time.perf_counter_ns() - started)
    documents = [observation.to_dict() for observation in observations]
    if len({_digest(document) for document in documents}) != 1:
        raise BenchmarkError("fake-client observations are not deterministic")
    document = documents[0]
    serialized_response = json.dumps(document, sort_keys=True, separators=(",", ":"))
    if "BOARD_AUTHOR_TEXT" in serialized_response or "get_as_string" in serialized_response:
        raise BenchmarkError("live observer leaked board content")
    raw_object_content_returned = any(
        marker in serialized_response for marker in _PRIVATE_OBJECT_MARKERS
    )
    if raw_object_content_returned:
        raise BenchmarkError("live observer leaked a raw KiCad object")
    if document["read_only"] is not True or document["source"] != "kicad-ipc-live":
        raise BenchmarkError("live observer did not advertise its read-only source")
    if document["object_counts"]["nets"] != 1 or document["object_counts"]["shapes"] != 1:
        raise BenchmarkError("serialized top-level object counts are incorrect")

    def future_factory(**_: object) -> _FakeKiCad:
        return _FakeKiCad(future=True)

    try:
        inspect_live_board(settings, client_factory=future_factory)
    except KicadIpcVersionError:
        future_refusal = True
    else:
        future_refusal = False
    if not future_refusal:
        raise BenchmarkError("future API was not refused by default")

    class FalseVersionKiCad(_FakeKiCad):
        def check_version(self) -> bool:
            return False

    try:
        inspect_live_board(settings, client_factory=lambda **_: FalseVersionKiCad())
    except KicadIpcVersionError:
        false_version_refusal = True
    else:
        false_version_refusal = False
    if not false_version_refusal:
        raise BenchmarkError("false version check was not refused")

    with _TemporarySocketEnv("tcp://127.0.0.1:1"):
        try:
            inspect_live_board(settings, client_factory=factory)
        except KicadIpcConfigurationError:
            tcp_refusal = True
        else:
            tcp_refusal = False
    if not tcp_refusal:
        raise BenchmarkError("TCP IPC endpoint was not refused")

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
            "max_board_bytes": settings.max_board_bytes,
        },
        "metrics": {
            "deterministic_replays": repetitions,
            "median_latency_ns": statistics.median(latencies),
            "board_digest": document["board_digest"],
            "board_bytes": document["board_bytes"],
            "object_counts": document["object_counts"],
            "future_api_default_refusals": int(future_refusal),
            "false_version_default_refusals": int(false_version_refusal),
            "tcp_endpoint_refusals": int(tcp_refusal),
            "raw_board_content_returned": False,
            "raw_object_content_returned": raw_object_content_returned,
            "counts_derived_from_serialized_source": True,
        },
        "not_claimed": [
            "live-kicad-session-success",
            "live-circuit-scene-binding",
            "placement",
            "routing",
            "kicad-drc",
            "erc",
            "electrical-validation",
            "fabrication-readiness",
        ],
    }


class _TemporarySocketEnv:
    def __init__(self, value: str) -> None:
        self.value = value
        self.previous: str | None = None

    def __enter__(self) -> None:
        self.previous = os.environ.get("KICAD_API_SOCKET")
        os.environ["KICAD_API_SOCKET"] = self.value

    def __exit__(self, *_: object) -> None:
        if self.previous is None:
            os.environ.pop("KICAD_API_SOCKET", None)
        else:
            os.environ["KICAD_API_SOCKET"] = self.previous


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
    run_id = _digest(document)
    document = {"run_id": run_id, **document}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical_bytes(document) + b"\n")
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
