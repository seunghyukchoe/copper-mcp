"""Fresh-process native job entrypoint. Run by absolute path under Python -I only."""

from __future__ import annotations

import base64
import hashlib
import importlib.abc
import importlib.machinery
import json
import os
import stat
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import ModuleType


class _SourceOnlyFinder(importlib.abc.MetaPathFinder):
    """The native job may import inventoried Python source, never extension/sourceless code."""

    def __init__(self, origins: frozenset[str]) -> None:
        self.origins = origins

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != "copper_mcp" and not fullname.startswith("copper_mcp."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if (
            spec is None
            or not isinstance(spec.loader, importlib.machinery.SourceFileLoader)
            or spec.origin not in self.origins
        ):
            raise ImportError("native module is not inventoried Python source")
        return spec


def _inventory(root: Path) -> tuple[tuple[str, str], ...]:
    # Inspect every directory entry, not just *.py matches: glob can silently omit
    # a symlinked package directory that Python would nevertheless import.
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError("source inventory is unavailable")
    pending = [root]
    paths: list[Path] = []
    nodes = 0
    while pending:
        with os.scandir(pending.pop()) as children:
            for child in children:
                nodes += 1
                if nodes > 16_384:
                    raise ValueError("source inventory exceeds its node budget")
                mode = child.stat(follow_symlinks=False).st_mode
                path = Path(child.path)
                if stat.S_ISDIR(mode):
                    pending.append(path)
                elif not stat.S_ISREG(mode):
                    raise ValueError("source inventory is unavailable")
                elif path.suffix in {".so", ".pyd", ".dll"} or (
                    path.suffix == ".pyc" and "__pycache__" not in path.relative_to(root).parts
                ):
                    raise ValueError("source inventory contains non-source executable code")
                elif path.suffix == ".py":
                    paths.append(path)
                    if len(paths) > 4096:
                        raise ValueError("source inventory exceeds its file budget")
    if not paths:
        raise ValueError("source inventory is unavailable")
    entries = []
    total = 0
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise ValueError("source inventory is unavailable")
        with path.open("rb") as source:
            content = source.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            raise ValueError("source inventory exceeds its byte budget")
        total += len(content)
        if total > 256 * 1024 * 1024:
            raise ValueError("source inventory exceeds its byte budget")
        entries.append((path.relative_to(root).as_posix(), hashlib.sha256(content).hexdigest()))
    return tuple(entries)


def main() -> None:
    if not sys.flags.isolated or any(
        name == "copper_mcp" or name.startswith("copper_mcp.") for name in sys.modules
    ):
        raise ValueError("native execution requires fresh isolated imports")
    root = Path(__file__).resolve().parents[1]
    before = _inventory(root)
    raw = sys.stdin.buffer.read(262_145)
    if len(raw) > 262_144:
        raise ValueError("native job input exceeds its byte budget")
    document = json.loads(raw)
    # An empty private pycache prefix prevents timestamp/size-valid stale .pyc files from
    # substituting old code for the source bytes inventoried above. -B alone does not do this.
    with tempfile.TemporaryDirectory(prefix="copper-native-imports-") as cache:
        sys.pycache_prefix = cache
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(root.parent))
        sys.meta_path.insert(
            0, _SourceOnlyFinder(frozenset(str(root / path) for path, _ in before))
        )
        from copper_mcp.config import Settings
        from copper_mcp.optimization.contracts import OptimizationRequest
        from copper_mcp.optimization.coordinator import coordinate_optimization
        from copper_mcp.optimization.inputs import prepare_optimization
        from copper_mcp.optimization.judge import JudgeReport
        from copper_mcp.optimization.package import OptimizationPackage
        from copper_mcp.optimization.repository import OptimizationJobRepository
        from copper_mcp.optimization.worker import (
            OptimizationExecutionError,
            OptimizationExecutionProbe,
            execute_optimization_job,
        )

        if _inventory(root) != before:
            raise ValueError("native sources changed during import")
        values = document["settings"]
        values["workspace"] = Path(values["workspace"])
        if values["kicad_cli"] is not None:
            values["kicad_cli"] = Path(values["kicad_cli"])
        values.update(
            allow_apply=False,
            allow_live_apply=False,
            allow_live_ipc=False,
            optimization_host_confirmation=False,
        )
        settings = Settings(**values)
        request = OptimizationRequest.model_validate_json(json.dumps(document["request"]))
        retained: list[tuple[OptimizationPackage, bytes]] = []
        reports: list[JudgeReport] = []

        def execute(probe: OptimizationExecutionProbe) -> OptimizationPackage:
            prepared = prepare_optimization(document["launch"], settings)
            if prepared.request != request:
                raise OptimizationExecutionError("stale_revision")
            prepared = replace(prepared, started_at=document["started_at"])
            result = coordinate_optimization(
                prepared,
                settings,
                probe,
                retain_private_result=lambda package, source: retained.append((package, source)),
                observe_judge=reports.append,
            )
            if _inventory(root) != before:
                raise OptimizationExecutionError("invalid_candidate")
            return result

        with OptimizationJobRepository(document["repository"]) as repository:
            record = execute_optimization_job(
                repository,
                document["job_id"],
                request,
                document["owner"],
                execute,
                absolute_deadline_ms=document["deadline_ms"],
            )
        result = {
            "record": record.document(),
            "judges": [report.model_dump(mode="json") for report in reports],
            "source": base64.b64encode(retained[0][1]).decode("ascii")
            if retained and record.status == "awaiting_approval"
            else None,
        }
        encoded = json.dumps(result, allow_nan=False, ensure_ascii=True).encode("ascii")
        if len(encoded) > 64 * 1024 * 1024:
            raise ValueError("native job output exceeds its byte budget")
        sys.stdout.buffer.write(encoded)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Neither Python tracebacks nor private input/output enter parent diagnostics.
        raise SystemExit(1) from None
