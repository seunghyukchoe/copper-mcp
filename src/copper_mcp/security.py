"""Filesystem boundary checks used by every board-facing entry point."""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path


class WorkspaceViolationError(ValueError):
    """Raised when a caller attempts to access data outside the configured workspace."""


@dataclass(frozen=True, slots=True)
class WorkspaceFileSnapshot:
    """One path-confined regular file captured through a single held descriptor."""

    path: Path
    content: bytes


def read_workspace_file(
    workspace: Path,
    requested_path: str,
    *,
    allowed_suffixes: Collection[str] = (),
    allowed_names: Collection[str] = (),
    max_bytes: int,
) -> WorkspaceFileSnapshot:
    """Capture one workspace file without a validate-then-reopen race.

    Every path component is opened relative to a held workspace descriptor with
    symlink following disabled. The same final descriptor supplies validation,
    bytes, and before/after mutation checks.
    """

    if (
        not requested_path
        or "\x00" in requested_path
        or len(requested_path) > 4096
        or requested_path.startswith("~")
        or isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes < 1
    ):
        raise WorkspaceViolationError("path or read budget is malformed")
    root = workspace.resolve(strict=True)
    untrusted = Path(requested_path)
    try:
        relative = untrusted.relative_to(root) if untrusted.is_absolute() else untrusted
    except ValueError as error:
        raise WorkspaceViolationError("path must stay inside the configured workspace") from error
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise WorkspaceViolationError("path must stay inside the configured workspace")
    allowed_suffix_set = {suffix.lower() for suffix in allowed_suffixes}
    allowed_name_set = set(allowed_names)
    if relative.name not in allowed_name_set and relative.suffix.lower() not in allowed_suffix_set:
        raise WorkspaceViolationError("file type is not allowed")
    if (
        os.open not in os.supports_dir_fd
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NONBLOCK")
    ):
        raise WorkspaceViolationError("secure workspace reads are unsupported on this platform")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open(root, directory_flags)
    except OSError as error:
        raise WorkspaceViolationError("configured workspace could not be opened safely") from error
    try:
        for part in parts[:-1]:
            next_descriptor = os.open(part, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        file_descriptor = os.open(parts[-1], file_flags, dir_fd=descriptor)
    except OSError as error:
        os.close(descriptor)
        raise WorkspaceViolationError(
            "path must resolve to a regular file inside the configured workspace"
        ) from error
    try:
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise WorkspaceViolationError("path must refer to a regular file")
        if before.st_size > max_bytes:
            raise WorkspaceViolationError(f"file exceeds the {max_bytes}-byte limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(file_descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(file_descriptor)
        before_state = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_state = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if (
            len(payload) > max_bytes
            or len(payload) != before.st_size
            or before_state != after_state
        ):
            raise WorkspaceViolationError("file changed while it was being read")
        return WorkspaceFileSnapshot(path=root.joinpath(*parts), content=payload)
    finally:
        os.close(file_descriptor)
        os.close(descriptor)


def create_workspace_file(
    workspace: Path,
    requested_path: str,
    content: bytes,
    *,
    allowed_suffixes: Collection[str],
    max_bytes: int,
) -> Path:
    """Atomically create one complete new file beneath ``workspace``.

    The final name is linked from a complete private temporary file through a
    held parent-directory descriptor. Existing files and symlinks are never
    replaced, and the exact final bytes are re-read before success.
    """

    if (
        not requested_path
        or "\x00" in requested_path
        or len(requested_path) > 4096
        or requested_path.startswith("~")
        or not isinstance(content, bytes)
        or len(content) > max_bytes
    ):
        raise WorkspaceViolationError("output path or content is malformed")
    root = workspace.resolve(strict=True)
    untrusted = Path(requested_path)
    candidate = untrusted if untrusted.is_absolute() else root / untrusted
    if candidate.suffix not in set(allowed_suffixes):
        raise WorkspaceViolationError("output file type is not allowed")
    try:
        parent = candidate.parent.resolve(strict=True)
        relative_parent = parent.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        raise WorkspaceViolationError(
            "output parent must resolve inside the configured workspace"
        ) from error
    if not parent.is_dir() or candidate.name in {"", ".", ".."}:
        raise WorkspaceViolationError("output parent must be a directory")

    required_dir_fd = {os.open, os.link, os.unlink}
    if (
        not required_dir_fd <= os.supports_dir_fd
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
    ):
        raise WorkspaceViolationError("secure create-only export is unsupported on this platform")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    file_flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(root, directory_flags)
    except OSError as error:
        raise WorkspaceViolationError("configured workspace could not be opened safely") from error
    try:
        for part in relative_parent.parts:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
    except OSError as error:
        os.close(directory_fd)
        raise WorkspaceViolationError("output parent could not be opened safely") from error
    temporary_name = f".copper-mcp-{secrets.token_hex(16)}.tmp"
    temporary_fd: int | None = None
    final_created = False
    try:
        temporary_fd = os.open(temporary_name, file_flags, 0o600, dir_fd=directory_fd)
        written = 0
        while written < len(content):
            count = os.write(temporary_fd, content[written:])
            if count <= 0:
                raise OSError("short output write")
            written += count
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None
        try:
            os.link(
                temporary_name,
                candidate.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise WorkspaceViolationError("output file already exists") from error
        final_created = True
        os.fsync(directory_fd)

        read_flags = os.O_RDONLY | os.O_NOFOLLOW
        final_fd = os.open(candidate.name, read_flags, dir_fd=directory_fd)
        try:
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(final_fd, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            observed = b"".join(chunks)
        finally:
            os.close(final_fd)
        if observed != content:
            raise WorkspaceViolationError("created output verification failed")
        stable_parent = root.joinpath(*relative_parent.parts)
        try:
            resolved_parent = stable_parent.resolve(strict=True)
            resolved_parent.relative_to(root)
            visible_stat = stable_parent.stat(follow_symlinks=False)
            held_stat = os.fstat(directory_fd)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise WorkspaceViolationError("output parent changed during export") from error
        if (
            resolved_parent != stable_parent
            or visible_stat.st_dev != held_stat.st_dev
            or visible_stat.st_ino != held_stat.st_ino
        ):
            raise WorkspaceViolationError("output parent changed during export")
        return stable_parent / candidate.name
    except Exception:
        if final_created:
            try:
                os.unlink(candidate.name, dir_fd=directory_fd)
            except OSError:
                pass
        raise
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def replace_workspace_file(
    workspace: Path,
    requested_path: str,
    content: bytes,
    *,
    allowed_suffixes: Collection[str],
    max_bytes: int,
) -> Path:
    """Atomically replace one existing file beneath ``workspace``.

    This is the only clobbering primitive in the project, so it inherits every check
    ``create_workspace_file`` performs and adds the ones a replacement needs: the target must
    already exist as a regular file, must not be a symlink, and its exact bytes are re-read
    after the rename.

    Durability follows the standard sequence - write a private ``O_EXCL`` temporary in the
    *target's own directory*, ``fsync`` it, rename it over the name, then ``fsync`` the
    directory so the rename itself survives a crash. The rename gives name atomicity: a
    concurrent reader sees either the old file or the new one, never a torn one.
    """

    if (
        not requested_path
        or "\x00" in requested_path
        or len(requested_path) > 4096
        or requested_path.startswith("~")
        or not isinstance(content, bytes)
        or not content
        or len(content) > max_bytes
    ):
        raise WorkspaceViolationError("output path or content is malformed")
    root = workspace.resolve(strict=True)
    untrusted = Path(requested_path)
    candidate = untrusted if untrusted.is_absolute() else root / untrusted
    if candidate.suffix not in set(allowed_suffixes):
        raise WorkspaceViolationError("output file type is not allowed")
    try:
        parent = candidate.parent.resolve(strict=True)
        relative_parent = parent.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        raise WorkspaceViolationError(
            "output parent must resolve inside the configured workspace"
        ) from error
    if not parent.is_dir() or candidate.name in {"", ".", ".."}:
        raise WorkspaceViolationError("output parent must be a directory")

    # ``os.rename`` rather than ``os.replace``: on POSIX both are the same ``renameat``
    # syscall and both already replace an existing destination atomically. ``os.replace``
    # exists to give Windows those POSIX semantics, and it does not accept ``dir_fd`` on macOS,
    # so using it here would forfeit the descriptor anchoring that keeps the operation confined.
    required_dir_fd = {os.open, os.rename, os.unlink}
    if (
        os.name != "posix"
        or not required_dir_fd <= os.supports_dir_fd
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
    ):
        raise WorkspaceViolationError("secure replacement is unsupported on this platform")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        directory_fd = os.open(root, directory_flags)
    except OSError as error:
        raise WorkspaceViolationError("configured workspace could not be opened safely") from error
    try:
        for part in relative_parent.parts:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
    except OSError as error:
        os.close(directory_fd)
        raise WorkspaceViolationError("output parent could not be opened safely") from error

    temporary_name = f".copper-mcp-{secrets.token_hex(16)}.tmp"
    temporary_fd: int | None = None
    try:
        # The target must already be a regular file. Opening it with O_NOFOLLOW first is what
        # stops a symlink planted at the name from redirecting the replacement elsewhere.
        try:
            existing_fd = os.open(candidate.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        except OSError as error:
            raise WorkspaceViolationError(
                "replacement target must be an existing regular file"
            ) from error
        try:
            existing = os.fstat(existing_fd)
            if not stat.S_ISREG(existing.st_mode):
                raise WorkspaceViolationError("replacement target must be a regular file")
        finally:
            os.close(existing_fd)

        temporary_fd = os.open(temporary_name, file_flags, 0o600, dir_fd=directory_fd)
        written = 0
        while written < len(content):
            count = os.write(temporary_fd, content[written:])
            if count <= 0:
                raise OSError("short output write")
            written += count
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None

        os.rename(temporary_name, candidate.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)

        final_fd = os.open(candidate.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        try:
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(final_fd, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            observed = b"".join(chunks)
        finally:
            os.close(final_fd)
        if observed != content:
            raise WorkspaceViolationError("replaced output verification failed")

        stable_parent = root.joinpath(*relative_parent.parts)
        try:
            resolved_parent = stable_parent.resolve(strict=True)
            resolved_parent.relative_to(root)
            visible_stat = stable_parent.stat(follow_symlinks=False)
            held_stat = os.fstat(directory_fd)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise WorkspaceViolationError("output parent changed during replacement") from error
        if (
            resolved_parent != stable_parent
            or visible_stat.st_dev != held_stat.st_dev
            or visible_stat.st_ino != held_stat.st_ino
        ):
            raise WorkspaceViolationError("output parent changed during replacement")
        return stable_parent / candidate.name
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        # A temporary that was never renamed must not survive a failure.
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
