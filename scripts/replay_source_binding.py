#!/usr/bin/env python3
"""Bind a fresh B-141 recomputation to its complete local Python source inventory.

Historical artifact loading authenticates the published companion and its declared inputs.
It does not demonstrate that today's production code still reproduces the measurement. This
separate fresh-process command records that stronger, current-source claim without rewriting
the historical report. Only aggregate digests and counts leave the process.
"""

from __future__ import annotations

import hashlib
import json
import platform
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_ROOTS = ("src/copper_mcp", "scripts", "benchmarks/corpora/tscircuit-benchmark")
_ARTIFACTS = (
    "benchmarks/results/routing/2026-08-06-simple-route-json-corpus-v1.json",
    "benchmarks/results/routing/2026-08-29-negotiated-multipin-corpus-census-v1.json",
    "benchmarks/results/routing/2026-08-30-negotiated-multipin-branch-repair-v1.json",
    "benchmarks/results/routing/2026-08-30-negotiated-multipin-branch-repair-v1.commitment.json",
)


class ReplayBindingError(ValueError):
    """The current source/input inventory cannot support a reproducible measurement."""


def _digest(document: object) -> str:
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class SourceBinding:
    entries: tuple[tuple[str, str], ...]

    @property
    def digest(self) -> str:
        return _digest(self.entries)


def capture_source_binding(root: Path = ROOT) -> SourceBinding:
    """Hash a conservative executable-source superset and all declared replay inputs."""

    paths: set[Path] = {root / name for name in _ARTIFACTS}
    for name in _ROOTS:
        directory = root / name
        if directory.is_symlink() or not directory.is_dir():
            raise ReplayBindingError("replay source inventory is unavailable")
        for path in directory.rglob("*"):
            if "__pycache__" in path.parts:
                continue
            if path.is_symlink():
                raise ReplayBindingError("replay source inventory contains a symlink")
            metadata = path.lstat()
            if not stat.S_ISDIR(metadata.st_mode) and not stat.S_ISREG(metadata.st_mode):
                raise ReplayBindingError("replay source inventory contains a special file")
            if stat.S_ISREG(metadata.st_mode) and (
                name.startswith("benchmarks/") or path.suffix == ".py"
            ):
                paths.add(path)
            if len(paths) > 4096:
                raise ReplayBindingError("replay source inventory exceeds its file budget")
    entries: list[tuple[str, str]] = []
    total_bytes = 0
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise ReplayBindingError("replay input is not an available regular file")
        with path.open("rb") as stream:
            content = stream.read(8 * 1024 * 1024 + 1)
        total_bytes += len(content)
        if len(content) > 8 * 1024 * 1024 or total_bytes > 256 * 1024 * 1024:
            raise ReplayBindingError("replay source inventory exceeds its byte budget")
        entries.append((path.relative_to(root).as_posix(), hashlib.sha256(content).hexdigest()))
    return SourceBinding(tuple(entries))


def verify_source_binding(binding: SourceBinding, root: Path = ROOT) -> None:
    if capture_source_binding(root) != binding:
        raise ReplayBindingError("replay sources or inputs changed during measurement")


def main() -> int:
    # Invoke with `python -I`: production modules are imported only after the before-inventory.
    if not sys.flags.isolated:
        raise ReplayBindingError("replay requires an isolated interpreter (-I)")
    before = capture_source_binding()
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from scripts import benchmark_negotiated_multipin_branch_repair as benchmark

    published = benchmark.load_artifact()
    measured = benchmark.build_report(repetitions=2)
    if any(measured[key] != published[key] for key in ("metrics", "configuration")):
        raise ReplayBindingError("current implementation does not reproduce published evidence")
    verify_source_binding(before)
    receipt = {
        "schema": "copper-mcp/current-evidence-replay/v1",
        "source_inventory_digest": before.digest,
        "source_inventory_files": len(before.entries),
        "python_version": platform.python_version(),
        "published_run_id": published["run_id"],
        "metrics_digest": _digest(measured["metrics"]),
        "configuration_digest": _digest(measured["configuration"]),
        "repetitions": 2,
        "status": "reproduced",
    }
    print(json.dumps({**receipt, "receipt_digest": _digest(receipt)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
