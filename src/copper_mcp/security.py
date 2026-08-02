"""Filesystem boundary checks used by every board-facing entry point."""

from __future__ import annotations

import stat
from collections.abc import Collection
from pathlib import Path


class WorkspaceViolationError(ValueError):
    """Raised when a caller attempts to access data outside the configured workspace."""


def resolve_workspace_file(
    workspace: Path,
    requested_path: str,
    *,
    allowed_suffixes: Collection[str],
    max_bytes: int,
) -> Path:
    """Resolve and validate a user-supplied path beneath ``workspace``.

    Resolution is performed before the containment check, so symlinks cannot be
    used to escape the configured workspace.
    """

    if not requested_path or "\x00" in requested_path or len(requested_path) > 4096:
        raise WorkspaceViolationError("path is empty or malformed")

    root = workspace.resolve(strict=True)
    untrusted = Path(requested_path).expanduser()
    candidate = untrusted if untrusted.is_absolute() else root / untrusted
    try:
        candidate = candidate.resolve(strict=True)
        candidate.relative_to(root)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        raise WorkspaceViolationError(
            "path must resolve to a file inside the configured workspace"
        ) from error

    file_stat = candidate.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise WorkspaceViolationError("path must refer to a regular file")
    if candidate.suffix.lower() not in {suffix.lower() for suffix in allowed_suffixes}:
        raise WorkspaceViolationError("file type is not allowed")
    if file_stat.st_size > max_bytes:
        raise WorkspaceViolationError(f"file exceeds the {max_bytes}-byte limit")
    return candidate


def read_bounded_file(path: Path, *, max_bytes: int) -> bytes:
    """Read at most ``max_bytes`` and reject files that grow during the read."""

    with path.open("rb") as stream:
        payload = stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise WorkspaceViolationError(f"file exceeds the {max_bytes}-byte limit")
    return payload
