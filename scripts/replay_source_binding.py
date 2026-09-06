#!/usr/bin/env python3
"""Bind fresh benchmarks or parser allocation measurements to local Python sources.

Historical artifact loading authenticates the published companion and its declared inputs.
It does not demonstrate that today's production code still reproduces the measurement. This
separate fresh-process command records that stronger, current-source claim without rewriting
the historical report. Only bounded aggregate evidence leaves the process.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.machinery
import json
import os
import platform
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, ModuleType

ROOT = Path(__file__).resolve().parents[1]
_ARGUMENT_ERROR = "replay arguments must be empty, exactly --census, or exactly --parse-memory"
_MAX_OUTPUT_BYTES = 1024 * 1024
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


class _BoundSourceLoader(importlib.machinery.SourceFileLoader):
    """Compile the inventoried bytes directly; timestamp-valid bytecode is not evidence."""

    def __init__(self, name: str, path: str, expected_digest: str) -> None:
        super().__init__(name, path)
        self.expected_digest = expected_digest

    def get_code(self, fullname: str) -> CodeType:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(self.path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ReplayBindingError("replay module is not a regular source file")
            source = stream.read(8 * 1024 * 1024 + 1)
        if (
            len(source) > 8 * 1024 * 1024
            or hashlib.sha256(source).hexdigest() != self.expected_digest
        ):
            raise ReplayBindingError("replay module changed after its source inventory")
        return self.source_to_code(source, self.path)


class _InventoriedImports(importlib.abc.MetaPathFinder):
    """Bootstrap without importing project helpers before their bytes have been verified."""

    def __init__(self, binding: SourceBinding, root: Path) -> None:
        self.origins = {
            str(root / name): digest for name, digest in binding.entries if name.endswith(".py")
        }
        self.namespace_directories = {str(Path(name).parent) for name in self.origins}

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname.split(".", 1)[0] not in {"copper_mcp", "scripts"}:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and spec.loader is None and spec.submodule_search_locations:
            if all(
                str(location) in self.namespace_directories
                for location in spec.submodule_search_locations
            ):
                return spec
        if (
            spec is None
            or not isinstance(spec.loader, importlib.machinery.SourceFileLoader)
            or spec.origin not in self.origins
        ):
            raise ReplayBindingError("replay module is not inventoried Python source")
        assert spec.origin is not None
        spec.loader = _BoundSourceLoader(fullname, spec.origin, self.origins[spec.origin])
        return spec


def capture_source_binding(root: Path = ROOT) -> SourceBinding:
    """Hash a conservative executable-source superset and all declared replay inputs."""

    paths: set[Path] = {root / name for name in _ARTIFACTS}
    nodes = 0
    for name in _ROOTS:
        directory = root / name
        if directory.is_symlink() or not directory.is_dir():
            raise ReplayBindingError("replay source inventory is unavailable")
        for path in directory.rglob("*"):
            nodes += 1
            if nodes > 16_384:
                raise ReplayBindingError("replay source inventory exceeds its node budget")
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


def _render_bounded(document: dict[str, object]) -> bytes:
    rendered = json.dumps(document, sort_keys=True, allow_nan=False).encode() + b"\n"
    if len(rendered) > _MAX_OUTPUT_BYTES:
        raise ReplayBindingError("replay output exceeds its byte budget")
    return rendered


def _b141_receipt(before: SourceBinding) -> dict[str, object]:
    from scripts import benchmark_negotiated_multipin_branch_repair as benchmark

    published = benchmark.load_artifact()
    measured = benchmark.build_report(repetitions=2)
    if any(measured[key] != published[key] for key in ("metrics", "configuration")):
        raise ReplayBindingError("current implementation does not reproduce published evidence")
    verify_source_binding(before)
    return {
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


def _census_receipt(before: SourceBinding) -> dict[str, object]:
    from scripts import benchmark_negotiated_corpus_census as census

    report = census.build_report(repetitions=1)
    verify_source_binding(before)
    return {
        "schema": "copper-mcp/current-census-replay/v1",
        "source_inventory_digest": before.digest,
        "source_inventory_files": len(before.entries),
        "python_version": platform.python_version(),
        "repetitions": 1,
        "status": "measured",
        "report": report,
    }


def _parse_memory_receipt(before: SourceBinding) -> dict[str, object]:
    from scripts import measure_parse_memory

    report = measure_parse_memory.measure_all()
    verify_source_binding(before)
    return {
        "schema": "copper-mcp/parse-memory-replay/v1",
        "source_inventory_digest": before.digest,
        "source_inventory_files": len(before.entries),
        "python_version": platform.python_version(),
        "status": "measured",
        "report": report,
    }


def main() -> int:
    # Invoke with `python -I`: production modules are imported only after the before-inventory.
    if not sys.flags.isolated:
        raise ReplayBindingError("replay requires an isolated interpreter (-I)")
    if any(name.split(".", 1)[0] in {"copper_mcp", "scripts"} for name in sys.modules):
        raise ReplayBindingError("replay requires fresh project imports")
    arguments = tuple(sys.argv[1:])
    if arguments not in {(), ("--census",), ("--parse-memory",)}:
        raise ReplayBindingError(_ARGUMENT_ERROR)
    before = capture_source_binding()
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    sys.meta_path.insert(0, _InventoriedImports(before, ROOT))
    sys.dont_write_bytecode = True
    if not arguments:
        receipt = _b141_receipt(before)
    elif arguments == ("--census",):
        receipt = _census_receipt(before)
    else:
        receipt = _parse_memory_receipt(before)
    sys.stdout.buffer.write(_render_bounded({**receipt, "receipt_digest": _digest(receipt)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
