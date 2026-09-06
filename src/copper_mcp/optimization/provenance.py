"""Bound executable implementation bytes independently of package version strings."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from copper_mcp.optimization.contracts import OptimizationError, digest_document
from copper_mcp.optimization.isolated_entry import _inventory


def bounded_file_digest(path: Path, *, maximum: int = 512 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > maximum:
                raise OptimizationError("optimization executable exceeds its byte budget")
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def native_implementation_digest() -> str:
    root = Path(__file__).resolve().parents[1]
    try:
        inventory = tuple((path, "sha256:" + digest) for path, digest in _inventory(root))
    except (OSError, ValueError):
        raise OptimizationError("optimization source inventory is unavailable") from None
    return digest_document(
        "optimization-native-implementation/v1",
        {
            "python": sys.version,
            "executable": bounded_file_digest(Path(sys.executable).resolve()),
            "sources": inventory,
        },
    )
