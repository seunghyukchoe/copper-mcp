"""Filesystem boundary checks used by every board-facing entry point.

This module owns the question "may this path be touched at all?" and the descriptor-anchored
reads, create-only writes, and atomic replacements that answer it. Every board-facing entry
point passes through it, so a caller cannot reach the filesystem by a route that skips the
workspace check.

It refuses rather than repairs: a path that escapes the configured workspace by traversal or
symlink, a read that exceeds its byte ceiling, a create whose target already exists, and a
replacement whose pre-rename snapshot no longer matches are all `WorkspaceViolationError`
outcomes, never silently corrected ones. Detection of an unsafe filesystem is best effort, so a
negative result means *not detected* and never *known safe*.

Nothing here interprets board contents, applies a candidate, or decides whether an operation is
authorized. Those are the callers' concerns; this module only decides where they may stand.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import secrets
import stat
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path


class WorkspaceViolationError(ValueError):
    """Raised when a caller attempts to access data outside the configured workspace."""


class WorkspaceStaleError(WorkspaceViolationError):
    """Raised when the target's bytes no longer match the digest a replacement expected.

    This is a *pre-rename* refusal: the target has not been touched, so a caller may report
    it as "nothing changed" truthfully.
    """


class WorkspacePostRenameError(RuntimeError):
    """Raised when a replacement fails *after* the rename has already happened.

    The board has been changed at this point, so a caller must never report it as untouched.
    Carries the digest of the bytes the rename published so the caller can decide whether to
    roll back.
    """

    def __init__(self, message: str, published_revision: str) -> None:
        super().__init__(message)
        self.published_revision = published_revision


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
    expect_digest: str | None = None,
    precheck: Callable[[], None] | None = None,
) -> Path:
    """Atomically replace one existing file beneath ``workspace``, under an exclusive lock.

    This is the only clobbering primitive in the project, so it inherits every check
    ``create_workspace_file`` performs and adds the ones a replacement needs.

    The target is opened, an exclusive ``flock`` is taken on that descriptor, and the lock is
    **held across the compare-and-swap and the rename**. Two replacements of the same file
    therefore serialise: the loser blocks until the winner's rename completes, then sees the
    winner's bytes under the swap and refuses rather than clobbering them.

    * ``expect_digest`` - if given, the target's current bytes are re-read *under the lock,
      immediately before the rename*, and the replacement is refused with ``WorkspaceStaleError``
      unless they still hash to this value. This closes the window between an earlier read and
      the rename.
    * ``precheck`` - an optional callback run under the lock just before the rename. It may
      raise to abort while the target is still untouched (the apply path uses it to re-check the
      KiCad lockfile).

    Failures are split by whether the rename has happened. Anything before it leaves the target
    untouched and raises ``WorkspaceViolationError``/``WorkspaceStaleError``; anything after it
    raises ``WorkspacePostRenameError``, because the board has already changed and must never be
    reported as untouched. The replacement also copies the target's permission bits onto the new
    file, so an applied board keeps the mode its author gave it rather than collapsing to 0600.
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
    lock_fd: int | None = None
    renamed = False
    published_revision = f"sha256:{hashlib.sha256(content).hexdigest()}"
    try:
        # The target must already be a regular file. Opening it with O_NOFOLLOW first is what
        # stops a symlink planted at the name from redirecting the replacement elsewhere. The
        # descriptor is then held, and flocked, for the whole critical section.
        try:
            lock_fd = os.open(candidate.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        except OSError as error:
            raise WorkspaceViolationError(
                "replacement target must be an existing regular file"
            ) from error
        existing = os.fstat(lock_fd)
        if not stat.S_ISREG(existing.st_mode):
            raise WorkspaceViolationError("replacement target must be a regular file")
        target_mode = stat.S_IMODE(existing.st_mode)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # Compare-and-swap under the lock. Re-read the *name* (not the held descriptor), so a
        # concurrent rename that repointed the name at different bytes is caught here.
        if expect_digest is not None:
            current_fd = os.open(candidate.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                current = _read_all(current_fd, max_bytes)
            finally:
                os.close(current_fd)
            if f"sha256:{hashlib.sha256(current).hexdigest()}" != expect_digest:
                raise WorkspaceStaleError("the target changed since it was read for replacement")

        if precheck is not None:
            # Runs while the target is still untouched, so raising here refuses cleanly.
            precheck()

        temporary_fd = os.open(temporary_name, file_flags, 0o600, dir_fd=directory_fd)
        written = 0
        while written < len(content):
            count = os.write(temporary_fd, content[written:])
            if count <= 0:
                raise OSError("short output write")
            written += count
        os.fchmod(temporary_fd, target_mode)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None

        os.rename(temporary_name, candidate.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        renamed = True

        # From here the board is changed; any failure is a post-rename condition.
        try:
            os.fsync(directory_fd)
            final_fd = os.open(candidate.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                observed = _read_all(final_fd, max_bytes)
            finally:
                os.close(final_fd)
            if observed != content:
                raise WorkspacePostRenameError(
                    "replaced output verification failed", published_revision
                )
            stable_parent = root.joinpath(*relative_parent.parts)
            resolved_parent = stable_parent.resolve(strict=True)
            resolved_parent.relative_to(root)
            visible_stat = stable_parent.stat(follow_symlinks=False)
            held_stat = os.fstat(directory_fd)
            if (
                resolved_parent != stable_parent
                or visible_stat.st_dev != held_stat.st_dev
                or visible_stat.st_ino != held_stat.st_ino
            ):
                raise WorkspacePostRenameError(
                    "output parent changed during replacement", published_revision
                )
            return stable_parent / candidate.name
        except WorkspacePostRenameError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise WorkspacePostRenameError(
                "replacement could not be verified after the rename", published_revision
            ) from error
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if not renamed:
            # A temporary that was never renamed must not survive a failure.
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(directory_fd)


def _read_all(descriptor: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def resolve_workspace_relative_path(
    workspace: Path,
    requested_path: str,
    *,
    allowed_suffixes: Collection[str] = (),
) -> str:
    """Resolve one confined path to its workspace-relative POSIX form without reading it.

    A cheap counterpart to ``read_workspace_file`` for the cases that need the canonical path
    but not the bytes - the apply path verifies a token before it reads a 64 MiB board, and it
    needs the same relative path the preview bound the token to.
    """

    if (
        not requested_path
        or "\x00" in requested_path
        or len(requested_path) > 4096
        or requested_path.startswith("~")
    ):
        raise WorkspaceViolationError("path is malformed")
    root = workspace.resolve(strict=True)
    untrusted = Path(requested_path)
    try:
        relative = untrusted.relative_to(root) if untrusted.is_absolute() else untrusted
    except ValueError as error:
        raise WorkspaceViolationError("path must stay inside the configured workspace") from error
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise WorkspaceViolationError("path must stay inside the configured workspace")
    allowed = {suffix.lower() for suffix in allowed_suffixes}
    if allowed and relative.suffix.lower() not in allowed:
        raise WorkspaceViolationError("file type is not allowed")
    resolved = (root / relative).resolve(strict=True)
    try:
        confined = resolved.relative_to(root)
    except ValueError as error:
        raise WorkspaceViolationError("path must stay inside the configured workspace") from error
    return confined.as_posix()
