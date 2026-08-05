#!/usr/bin/env python3
"""Measure revision-bound, read-only placement over one captured KiCad IPC snapshot."""

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
from copper_mcp.mcp_contracts import PlacementPreviewToolResponse
from copper_mcp.placement.contracts import PlacementError
from copper_mcp.placement_preview import preview_live_placement, preview_placement

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path("scripts/benchmark_kicad_live_placement.py")
FIXTURE = ROOT / "tests" / "fixtures" / "placement-v0.1" / "placement-legal.kicad_pcb"
BENCHMARK_NAME = "kicad-ipc-live-placement-v1"
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}
SUBJECTS = [
    "footprint:kicad:90000000-0000-0000-0000-000000000001",
    "footprint:kicad:90000000-0000-0000-0000-000000000003",
]
PROPOSALS = [{"subject": SUBJECTS[0], "offset_x_nm": 1_234_567, "offset_y_nm": 0}]


class BenchmarkError(RuntimeError):
    """The fake-client live-placement oracle was not deterministic or bounded."""


class _Version:
    major = 10
    minor = 0
    patch = 5


class _FakeBoard:
    def __init__(self, source: str) -> None:
        self.source = source
        self.mutating_calls = 0

    def get_as_string(self) -> str:
        return self.source

    def __getattr__(self, name: str) -> Any:
        if name in {
            "save",
            "save_as",
            "move",
            "set_position",
            "update_items",
            "begin_commit",
            "push_commit",
            "refill_zones",
        }:
            self.mutating_calls += 1
            raise AssertionError(f"live placement called forbidden mutator: {name}")
        raise AttributeError(name)


class _FakeKiCad:
    def __init__(self, board: _FakeBoard) -> None:
        self.board = board

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
    # The benchmark drives a deterministic fake client, but it still goes through the real
    # capture path, which is operator-gated. Enable it explicitly here rather than depending
    # on the ambient environment.
    settings = Settings(workspace=ROOT, allow_live_ipc=True)
    named = preview_placement(
        {
            "board": str(FIXTURE.relative_to(ROOT)),
            "constraints": CONSTRAINTS,
            "subjects": SUBJECTS,
            "proposals": PROPOSALS,
        },
        settings,
    )
    named_document = named.to_dict()
    PlacementPreviewToolResponse.model_validate(named_document)
    if named_document["status"] != "previewed" or named_document["candidate"] is None:
        raise BenchmarkError("the file-backed placement oracle did not produce a candidate")
    if named_document["snapshot_digest"] is None:
        raise BenchmarkError("the file-backed placement oracle omitted its snapshot digest")

    board = _FakeBoard(source)
    factory_calls = 0

    def factory(**_: object) -> _FakeKiCad:
        nonlocal factory_calls
        factory_calls += 1
        return _FakeKiCad(board)

    request = {
        "board": "live",
        "constraints": CONSTRAINTS,
        "subjects": SUBJECTS,
        "proposals": PROPOSALS,
        "expect_board_revision": named_document["board_revision"],
        "expect_snapshot_digest": named_document["snapshot_digest"],
    }
    documents: list[dict[str, Any]] = []
    latencies: list[int] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        preview = preview_live_placement(request, settings, client_factory=factory)
        latencies.append(time.perf_counter_ns() - started)
        document = preview.to_dict()
        PlacementPreviewToolResponse.model_validate(document)
        if document["status"] != "previewed" or document["candidate"] is None:
            raise BenchmarkError("live placement did not produce a candidate")
        if document["candidate"] != named_document["candidate"]:
            raise BenchmarkError("live placement diverged from the same-byte file oracle")
        documents.append(document)

    if len({_canonical_bytes(document) for document in documents}) != 1:
        raise BenchmarkError("live placement replay was not deterministic")
    document = documents[0]
    encoded = json.dumps(document, sort_keys=True)
    if "placement-legal.kicad_pcb" in encoded:
        raise BenchmarkError("live placement leaked the source path")
    if document["board_path"] != "live":
        raise BenchmarkError("live placement returned a non-live board label")

    stale_board = {**request, "expect_board_revision": "sha256:" + "0" * 64}
    stale_board_result = preview_live_placement(stale_board, settings, client_factory=factory)
    stale_board_refusal = int(
        stale_board_result.diagnostic is not None
        and str(stale_board_result.diagnostic.code) == "stale_revision"
        and stale_board_result.snapshot_digest is None
    )
    stale_snapshot = {**request, "expect_snapshot_digest": "sha256:" + "1" * 64}
    stale_snapshot_result = preview_live_placement(stale_snapshot, settings, client_factory=factory)
    stale_snapshot_refusal = int(
        stale_snapshot_result.diagnostic is not None
        and str(stale_snapshot_result.diagnostic.code) == "stale_revision"
        and stale_snapshot_result.snapshot_digest is not None
    )
    calls_before = factory_calls
    try:
        preview_live_placement(
            {**request, "include_apply_token": True}, settings, client_factory=factory
        )
    except PlacementError:
        forbidden_action_refusal = 1
    else:
        forbidden_action_refusal = 0
    forbidden_action_ipc_calls = factory_calls - calls_before
    if stale_board_refusal != 1 or stale_snapshot_refusal != 1:
        raise BenchmarkError("stale live placement revisions were not refused")
    if forbidden_action_refusal != 1 or forbidden_action_ipc_calls != 0:
        raise BenchmarkError("live placement action authority crossed the IPC preflight")
    if board.mutating_calls != 0:
        raise BenchmarkError("a mutating IPC method was called")

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
            "placement_oracle": "file-backed-preview-placement-v1",
        },
        "metrics": {
            "deterministic_replays": repetitions,
            "median_latency_ns": statistics.median(latencies),
            "board_revision": document["board_revision"],
            "snapshot_digest": document["snapshot_digest"],
            "status": document["status"],
            "candidate_id": document["candidate"]["candidate_id"],
            "candidate_base_revision": document["candidate"]["base_revision"],
            "candidate_view_revision": document["candidate"]["view_revision"],
            "candidate_equal_to_file_oracle": True,
            "candidate_canonical_bytes_equal": True,
            "candidate_reference_closure": True,
            "board_path": document["board_path"],
            "stale_board_revision_refusals": stale_board_refusal,
            "stale_snapshot_digest_refusals": stale_snapshot_refusal,
            "forbidden_action_refusals": forbidden_action_refusal,
            "forbidden_action_ipc_calls": forbidden_action_ipc_calls,
            "mutating_ipc_calls": board.mutating_calls,
            "raw_source_returned": False,
            "drc_evidence_returned": False,
            "fill_authority_returned": False,
            "apply_authority_returned": False,
        },
        "not_claimed": [
            "live-kicad-session-success",
            "live-placement-authority",
            "kicad-drc",
            "zone-fill-authority",
            "single-undo-transaction",
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
