#!/usr/bin/env python3
"""Measure the read-only, revision-bound KiCad editor-context contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import statistics
import subprocess
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from copper_mcp.config import Settings
from copper_mcp.kicad_ipc import capture_live_editor_context
from copper_mcp.live_editor_context import LiveEditorContextError, inspect_live_editor_context_raw
from copper_mcp.mcp_contracts import LiveEditorContextToolResponse

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path("scripts/benchmark_kicad_live_editor_context.py")
BENCHMARK_NAME = "kicad-ipc-live-editor-context-v1"
SOURCE = "(kicad_pcb (version 20260206) (layers))"
PAD_ID = "11111111-1111-1111-1111-111111111111"
TRACK_ID = "22222222-2222-2222-2222-222222222222"


class Pad:
    def __init__(self, value: str) -> None:
        self.id = SimpleNamespace(value=value)


class Track:
    def __init__(self, value: str) -> None:
        self.id = SimpleNamespace(value=value)


class _Board:
    def __init__(self) -> None:
        self.active_layer = 31
        self.layer_name = "F.Cu"
        self.selection: list[object] = [Track(TRACK_ID), Pad(PAD_ID)]
        self.mutating_calls = 0

    def get_as_string(self) -> str:
        return SOURCE

    def get_active_layer(self) -> int:
        return self.active_layer

    def get_layer_name(self, layer: int) -> str:
        if layer != self.active_layer:
            raise AssertionError("unexpected layer lookup")
        return self.layer_name

    def get_selection(self) -> list[object]:
        return list(self.selection)

    def get_selection_as_string(self) -> str:
        raise AssertionError("raw selection text must never be read")

    def __getattr__(self, name: str) -> Any:
        if name in {"save", "update_items", "begin_commit", "push_commit", "remove_items"}:
            self.mutating_calls += 1
            raise AssertionError(f"editor context called forbidden mutator: {name}")
        raise AttributeError(name)


class _KiCad:
    def __init__(self, board: _Board) -> None:
        self.board = board

    def get_version(self) -> SimpleNamespace:
        return SimpleNamespace(major=10, minor=0, patch=5)

    def get_api_version(self) -> SimpleNamespace:
        return SimpleNamespace(major=10, minor=0, patch=5)

    def check_version(self) -> bool:
        return True

    def get_board(self) -> _Board:
        return self.board


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - executable resolved from PATH
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _repetitions(value: str) -> int:
    count = int(value)
    if not 2 <= count <= 20:
        raise argparse.ArgumentTypeError("repetitions must be between 2 and 20")
    return count


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _run(repetitions: int) -> dict[str, Any]:
    settings = Settings(workspace=ROOT)
    board = _Board()

    def factory(**_: object) -> _KiCad:
        return _KiCad(board)

    captured = capture_live_editor_context(settings, client_factory=factory)
    request = {
        "board": "live",
        "expect_board_revision": captured.board_digest,
        "expect_snapshot_digest": captured.board_digest,
    }
    documents: list[dict[str, Any]] = []
    latencies: list[int] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        result = inspect_live_editor_context_raw(request, settings, client_factory=factory)
        latencies.append(time.perf_counter_ns() - started)
        document = result.to_dict()
        LiveEditorContextToolResponse.model_validate(document)
        documents.append(document)
    canonical = [json.dumps(value, sort_keys=True, separators=(",", ":")) for value in documents]
    if len(set(canonical)) != 1:
        raise RuntimeError("editor-context replay was not deterministic")
    if board.mutating_calls != 0:
        raise RuntimeError("a mutating IPC method was called")
    if "F.Cu" not in documents[0]["active_layer"]["name"]:
        raise RuntimeError("active layer was not captured")
    if "selection_as_string" in canonical[0] or "kicad_pcb" in canonical[0]:
        raise RuntimeError("raw editor content leaked")

    stale_board = dict(request, expect_board_revision="sha256:" + "0" * 64)
    try:
        inspect_live_editor_context_raw(stale_board, settings, client_factory=factory)
    except LiveEditorContextError:
        stale_board_refusal = 1
    else:
        stale_board_refusal = 0
    first = inspect_live_editor_context_raw(request, settings, client_factory=factory)
    stale_context = dict(
        request,
        expect_context_digest=first.context_digest[:-1]
        + ("0" if first.context_digest[-1] != "0" else "1"),
    )
    try:
        inspect_live_editor_context_raw(stale_context, settings, client_factory=factory)
    except LiveEditorContextError:
        stale_context_refusal = 1
    else:
        stale_context_refusal = 0

    changed = _Board()
    changed.active_layer = 32
    changed.layer_name = "B.Cu"
    changed_result = inspect_live_editor_context_raw(
        request, settings, client_factory=lambda **_: _KiCad(changed)
    )
    context_changed = int(changed_result.context_digest != first.context_digest)
    return {
        "benchmark": BENCHMARK_NAME,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_commit": _git_commit(),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "dependencies": {
                "mcp": _package_version("mcp"),
                "pydantic": _package_version("pydantic"),
            },
            "kicad_invoked": False,
            "live_ipc_probe": "not_run_kicad_api_server_disabled",
        },
        "configuration": {
            "script_sha256": hashlib.sha256((ROOT / SCRIPT_PATH).read_bytes()).hexdigest(),
            "repetitions": repetitions,
            "transport_path": "fake_kicad_python_client",
            "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
        },
        "metrics": {
            "deterministic_replays": repetitions,
            "unique_response_digests": 1,
            "median_capture_latency_ns": int(statistics.median(latencies)),
            "active_layer": documents[0]["active_layer"],
            "selection_count": documents[0]["selection_count"],
            "context_digest": documents[0]["context_digest"],
            "stale_board_refusals": stale_board_refusal,
            "stale_context_refusals": stale_context_refusal,
            "active_layer_change_updates_context": context_changed,
            "mutating_ipc_calls": board.mutating_calls,
            "raw_editor_content_returned": False,
        },
        "interpretation": (
            "Fake-client contract evidence only; no live KiCad GUI, mutation, DRC, routing, "
            "placement, electrical, or fabrication claim."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=_repetitions, default=10)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    result = _run(args.repetitions)
    document = {
        "run_id": "sha256:" + hashlib.sha256(_canonical_bytes(result)).hexdigest(),
        **result,
    }
    encoded = _canonical_bytes(document) + b"\n"
    if args.output is None:
        print(encoded.decode(), end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(encoded)


if __name__ == "__main__":
    main()
