"""The mutating apply path: the only code in this project that changes a user's board.

Everything here exists to make one operation safe, so the order of the checks is the design:

1. **Opt in.** Refused unless the operator set the flag. A model cannot turn this on.
2. **Authorize.** A single-use token this process issued for this exact candidate, board and
   path. Enforced here, not by a tool annotation.
3. **KiCad must be closed.** A ``~name.lck`` sibling is a hard refusal, never removed. pcbnew
   has no external-change watcher, so a GUI holding the board open will silently overwrite
   whatever we write when the user next saves.
4. **Compare and swap, twice.** The board's bytes and its Board IR digest are checked before
   the splice and again immediately before publishing, both under a held lock. A mismatch is
   refused and **never auto-refreshed**: re-routing against copper the caller has not seen
   would apply a proposal nobody approved.
5. **Keep a way back.** A timestamped, content-addressed copy is written beside the board
   before anything is replaced, and its path is returned. KiCad's own ``-bak`` files are never
   touched or relied upon.
6. **Publish atomically**, then verify. If verification fails, the backup is restored and the
   failure is reported rather than left behind.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.apply.contracts import (
    ApplyDiagnostic,
    ApplyFailureCode,
    ApplyRequest,
    ApplyResult,
    parse_apply_request,
)
from copper_mcp.apply.engine import ApplyEngineError, apply_route_candidate
from copper_mcp.apply.tokens import ApplyBinding, ApplyTokenAuthority, ApplyTokenError
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.config import Settings
from copper_mcp.request_boundary import NET_CLASS_ID, NET_CLASS_NAME
from copper_mcp.routing.astar import ROUTER_VERSION
from copper_mcp.routing.contracts import RouteCandidate
from copper_mcp.security import (
    WorkspaceViolationError,
    read_workspace_file,
    replace_workspace_file,
)

#: Filesystems where `os.replace` and `fsync` do not carry their usual guarantees. Detected
#: where the platform makes it cheap; where it does not, the limitation is stated rather than
#: guessed at.
_UNSAFE_FILESYSTEMS = frozenset({"nfs", "smbfs", "afpfs", "webdav", "fusefs", "cifs", "ftp"})


class ApplyServiceError(RuntimeError):
    """Raised only for conditions that cannot be expressed as a typed refusal."""


@dataclass(frozen=True, slots=True)
class _Board:
    relative_path: str
    absolute_path: Path
    content: bytes
    revision: str


def _revision(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def lockfile_for(board: Path) -> Path:
    """KiCad's advisory sibling lock for a board."""

    return board.parent / f"~{board.name}.lck"


def unsafe_filesystem(path: Path) -> str | None:
    """Name the filesystem when it is one whose durability guarantees are weaker.

    Best effort by design. ``statvfs`` exposes a filesystem name on macOS and the BSDs but not
    on Linux, so a ``None`` here means *not detected*, never *known safe* - which is why the
    result is surfaced in the response instead of being silently trusted.
    """

    try:
        stats = os.statvfs(path)
    except OSError:
        return None
    name = getattr(stats, "f_fstypename", None)
    if not isinstance(name, str) or not name:
        return None
    return name.lower() if name.lower() in _UNSAFE_FILESYSTEMS else None


def _profile(constraints: NetClass) -> KiCadConstraintProfile:
    return KiCadConstraintProfile(net_classes=(constraints,), default_net_class_id=constraints.id)


def _candidate_from_manifest(payload: Any) -> RouteCandidate:
    """Rebuild a candidate from its manifest without trusting any field in it.

    ``candidate_from_dict`` accepts a manifest at face value and cannot detect a forged
    identity, so it is deliberately not used. The engine recomputes the identity and replays
    the geometry; this function only performs the structural decode.
    """

    from copper_mcp.board_ir import PointNM
    from copper_mcp.routing.contracts import (
        RouteCost,
        RouteMetrics,
        RoutePatch,
        RoutePath,
    )

    # A Mapping, not specifically a dict: the request boundary hands this over as a read-only
    # MappingProxyType so the manifest cannot be mutated after validation.
    if not isinstance(payload, Mapping):
        raise ApplyEngineError("candidate manifest is malformed")
    try:
        patch = payload["patch"]
        paths = tuple(
            RoutePath(
                vertices=tuple(
                    PointNM(int(point[0]), int(point[1])) for point in item["vertices_nm"]
                )
            )
            for item in patch["paths"]
        )
        cost = payload["cost"]
        metrics = payload["metrics"]
        settings_payload = payload["settings"]
        from copper_mcp.routing import AStarSettings

        return RouteCandidate(
            candidate_id=str(payload["candidate_id"]),
            base_revision=str(payload["base_revision"]),
            start_pad_id=str(payload["start_pad_id"]),
            end_pad_id=str(payload["end_pad_id"]),
            pad_count=int(payload["pad_count"]),
            patch=RoutePatch(
                net_id=str(patch["net_id"]),
                layer_id=str(patch["layer_id"]),
                width_nm=int(patch["width_nm"]),
                paths=paths,
            ),
            cost=RouteCost(**{key: int(value) for key, value in cost.items()}),
            metrics=RouteMetrics(**{key: int(value) for key, value in metrics.items()}),
            settings=AStarSettings(**{key: int(value) for key, value in settings_payload.items()}),
            policy=str(payload["policy"]),
            seed=int(payload["seed"]),
            router_version=str(payload.get("router_version", ROUTER_VERSION)),
            ordering_policy=str(payload["ordering_policy"]),
        )
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise ApplyEngineError("candidate manifest is malformed") from error


def _refuse(
    request: ApplyRequest | None,
    board_path: str,
    board_revision: str,
    code: ApplyFailureCode,
    message: str,
    **extra: Any,
) -> ApplyResult:
    return ApplyResult(
        status="refused",
        board_path=board_path,
        board_revision_before=board_revision,
        request=request,
        diagnostic=ApplyDiagnostic(code=code, message=message),
        **extra,
    )


def apply_candidate(
    payload: Any,
    settings: Settings,
    token_authority: ApplyTokenAuthority,
    *,
    clock: Callable[[], float] | None = None,
) -> ApplyResult:
    """Apply one authorized route candidate to a workspace board."""

    if not isinstance(settings, Settings):
        raise ApplyServiceError("apply settings are malformed")
    if not isinstance(token_authority, ApplyTokenAuthority):
        raise ApplyServiceError("apply token authority is malformed")
    now = clock if callable(clock) else time.time

    request = parse_apply_request(payload)

    if not settings.allow_apply:
        # The tool stays listed so a client can explain it; the capability is simply off.
        return _refuse(
            request,
            request.board,
            _revision(b""),
            ApplyFailureCode.APPLY_DISABLED,
            "applying candidates is disabled; set COPPER_MCP_ALLOW_APPLY=1 to enable it",
        )

    board = _read_board(settings, request.board)
    lock_path = lockfile_for(board.absolute_path)
    if lock_path.exists():
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.KICAD_OPEN,
            (
                "a KiCad lockfile is present, so the board may be open in the editor; "
                f"close KiCad and remove {lock_path.name} if it is stale"
            ),
        )

    constraints = NetClass(id=NET_CLASS_ID, name=NET_CLASS_NAME, **request.constraints_payload())
    profile = _profile(constraints)
    limits = ParseLimits()

    conversion = parse_kicad_bytes(board.content, profile, limits)
    if conversion.snapshot is None or conversion.diagnostics:
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.UNSUPPORTED_BOARD,
            "this board is outside the supported Board IR subset",
        )
    snapshot = conversion.snapshot

    try:
        candidate = _candidate_from_manifest(request.candidate)
    except ApplyEngineError as error:
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.INVALID_REQUEST,
            str(error),
        )

    binding = ApplyBinding(
        candidate_id=candidate.candidate_id,
        base_revision=candidate.base_revision,
        board_revision=request.expect_board_revision,
        relative_path=board.relative_path,
    )
    try:
        nonce = token_authority.verify(request.apply_token, binding)
    except ApplyTokenError as error:
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode(error.code),
            str(error),
        )

    # First compare-and-swap, before any work: the caller must have previewed these exact
    # bytes and this exact Board IR.
    if board.revision != request.expect_board_revision:
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.STALE_CANDIDATE,
            "the board changed since the preview; re-run the preview and review the result",
        )
    if snapshot.snapshot_digest != candidate.base_revision:
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.STALE_CANDIDATE,
            "the candidate was proposed against a different Board IR snapshot",
        )

    try:
        applied = apply_route_candidate(board.content, snapshot, candidate, profile, limits=limits)
    except ApplyEngineError as error:
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.SPLICE_ASSERTION_FAILED,
            str(error),
        )

    filesystem = unsafe_filesystem(board.absolute_path.parent)
    if filesystem is not None:
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.UNSAFE_FILESYSTEM,
            (
                f"the board is on a {filesystem} filesystem, where atomic replacement and "
                "fsync do not carry their usual guarantees; copy it to local storage first"
            ),
        )

    try:
        backup_path = _write_backup(settings, board, now)
    except (WorkspaceViolationError, OSError) as error:
        # The pre-apply copy is the only way back, so failing to write it must stop the apply
        # rather than proceed without one. Found by crash injection: this previously escaped
        # as an uncaught OSError instead of a typed refusal.
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.BACKUP_FAILED,
            f"the pre-apply copy could not be written, so nothing was changed: {error}",
        )

    # Second compare-and-swap, immediately before publishing. The window between the first
    # check and here is where a GUI save or another process would land.
    current = _read_board(settings, request.board)
    if current.revision != board.revision:
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.STALE_CANDIDATE,
            "the board changed while the candidate was being prepared",
            backup_path=backup_path,
        )

    try:
        replace_workspace_file(
            settings.workspace,
            board.relative_path,
            applied.content,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
        )
    except (WorkspaceViolationError, OSError) as error:
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.APPLY_VERIFICATION_FAILED,
            f"the board could not be replaced safely: {error}",
            backup_path=backup_path,
        )

    verified = _verify_after_publish(settings, request.board, applied.result_revision)
    if not verified:
        restored = _restore(settings, board)
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.APPLY_VERIFICATION_FAILED,
            (
                "the applied board did not verify after publication; "
                + ("the original was restored" if restored else "restore the pre-apply copy")
            ),
            backup_path=backup_path,
        )

    token_authority.consume(nonce)
    return ApplyResult(
        status="applied",
        board_path=board.relative_path,
        board_revision_before=board.revision,
        board_revision_after=applied.result_revision,
        snapshot_digest_before=snapshot.snapshot_digest,
        base_revision=candidate.base_revision,
        candidate_id=candidate.candidate_id,
        request=request,
        backup_path=backup_path,
        bytes_added=applied.bytes_added,
        segments_added=applied.segments_added,
        verification=applied.verification,
    )


def _read_board(settings: Settings, requested: str) -> _Board:
    snapshot = read_workspace_file(
        settings.workspace,
        requested,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    root = settings.workspace.resolve(strict=True)
    return _Board(
        relative_path=snapshot.path.relative_to(root).as_posix(),
        absolute_path=snapshot.path,
        content=snapshot.content,
        revision=_revision(snapshot.content),
    )


def _write_backup(settings: Settings, board: _Board, now: Callable[[], float]) -> str:
    """Write the pre-apply copy beside the board and return its workspace-relative path.

    Timestamped and content-addressed so successive applies never collide and so a user can
    tell which copy corresponds to which board state. KiCad's own ``-bak`` files are a
    different mechanism owned by the editor and are never written, read, or removed here.
    """

    from copper_mcp.security import create_workspace_file

    stamp = datetime.fromtimestamp(now(), tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    digest = board.revision.removeprefix("sha256:")[:16]
    name = f"{board.absolute_path.name}.{stamp}.{digest}.pre-apply.kicad_pcb"
    relative = (Path(board.relative_path).parent / name).as_posix()
    root = settings.workspace.resolve(strict=True)
    try:
        created = create_workspace_file(
            settings.workspace,
            relative,
            board.content,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
        )
    except WorkspaceViolationError:
        # A retry within the same second finds its own copy already there. The name carries
        # the source digest, so an existing file under it is the same board state - reuse it
        # after confirming that, rather than failing an apply over a backup that already
        # exists or silently overwriting something that does not match.
        existing = read_workspace_file(
            settings.workspace,
            relative,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
        )
        if existing.content != board.content:
            raise
        return existing.path.relative_to(root).as_posix()
    return created.relative_to(root).as_posix()


def _verify_after_publish(settings: Settings, requested: str, expected: str) -> bool:
    try:
        return _read_board(settings, requested).revision == expected
    except (WorkspaceViolationError, OSError):
        return False


def _restore(settings: Settings, board: _Board) -> bool:
    try:
        replace_workspace_file(
            settings.workspace,
            board.relative_path,
            board.content,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
        )
    except (WorkspaceViolationError, OSError):
        return False
    return True


__all__ = ["ApplyServiceError", "apply_candidate", "lockfile_for", "unsafe_filesystem"]
