"""The mutating apply path: the only code in this project that changes a user's board.

Everything here exists to make one operation safe, so the order of the checks is the design:

1. **Opt in.** Refused unless the operator set the flag. A model cannot turn this on.
2. **Authorize before the heavy work.** A single-use token this process issued for this exact
   candidate, board revision and path is verified before the board is read or parsed, so an
   unauthorized caller cannot make the tool do expensive work.
3. **KiCad must be closed.** A ``~name.lck`` sibling is a hard refusal, never removed - checked
   up front and **again under the lock** immediately before the write, because a GUI opened in
   between would otherwise silently overwrite the applied board.
4. **Compare and swap under an exclusive lock.** The board's bytes and its Board IR digest are
   checked, and the final check plus the rename happen while an exclusive ``flock`` is held on
   the board, so two applies from the same base serialise and the loser refuses instead of
   clobbering the winner. A mismatch is **never auto-refreshed**.
5. **Keep a way back.** A timestamped, content-addressed copy is written into a backups
   subdirectory before anything is replaced, and its path is returned. KiCad's own ``-bak``
   files are never touched.
6. **Publish atomically**, then report truthfully. A failure before the rename leaves the board
   untouched; a failure after it says so - the board is changed and is never reported otherwise.
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.kicad_placement_patch import KiCadPlacementPatchError
from copper_mcp.adapters.kicad_route_patch import KiCadRoutePatchError
from copper_mcp.apply.contracts import (
    ApplyDiagnostic,
    ApplyFailureCode,
    ApplyRequest,
    ApplyResult,
    PlacementApplyRequest,
    PlacementApplyResult,
    parse_apply_request,
    parse_placement_apply_request,
)
from copper_mcp.apply.engine import ApplyEngineError, apply_route_candidate
from copper_mcp.apply.placement_engine import apply_placement_candidate as apply_placement_bytes
from copper_mcp.apply.placement_engine import verify_published_placement_board
from copper_mcp.apply.tokens import (
    ApplyBinding,
    ApplyTokenAuthority,
    ApplyTokenError,
)
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.placement.contracts import (
    ORDERING_POLICY,
    PLACEMENT_VERSION,
    FootprintPlacement,
    PlacementCandidate,
    PlacementEvidence,
    PlacementLegality,
    RuleResult,
)
from copper_mcp.request_boundary import NET_CLASS_ID, NET_CLASS_NAME
from copper_mcp.routing.astar import ROUTER_VERSION
from copper_mcp.routing.contracts import RouteCandidate
from copper_mcp.security import (
    WorkspacePostRenameError,
    WorkspaceStaleError,
    WorkspaceViolationError,
    create_workspace_file,
    read_workspace_file,
    replace_workspace_file,
    resolve_workspace_relative_path,
)

#: Filesystems where `os.rename` and `fsync` do not carry their usual guarantees. Detected
#: where the platform makes it cheap; where it does not, the limitation is stated rather than
#: guessed at.
_UNSAFE_FILESYSTEMS = frozenset({"nfs", "smbfs", "afpfs", "webdav", "fusefs", "cifs", "ftp"})
_BACKUPS_DIRNAME = ".copper-mcp-backups"
#: Pre-apply copies kept per board before the oldest is pruned. Bounds a preview→apply loop
#: that would otherwise fill the disk one backup at a time.
MAX_BACKUPS_PER_BOARD = 16


class ApplyServiceError(RuntimeError):
    """Raised only for conditions that cannot be expressed as a typed refusal."""


class _LockfileAppearedError(RuntimeError):
    """Raised by the under-lock precheck when the KiCad lockfile appeared mid-apply."""


@dataclass(frozen=True, slots=True)
class _Board:
    relative_path: str
    absolute_path: Path
    content: bytes
    revision: str
    mode: int


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
    the geometry; this function only performs the structural decode. The geometry it decodes
    is already bounded by ``parse_apply_request``.
    """

    from copper_mcp.board_ir import PointNM
    from copper_mcp.routing.contracts import (
        RouteCost,
        RouteMetrics,
        RoutePatch,
        RoutePath,
    )

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


def _placement_candidate_from_manifest(payload: Any) -> PlacementCandidate:
    """Decode a bounded placement manifest without trusting its identity or verdict."""

    if not isinstance(payload, Mapping):
        raise ApplyEngineError("placement candidate manifest is malformed")

    def strict_int(value: Any, message: str) -> int:
        if type(value) is not int:  # bool is intentionally not an integer here
            raise ApplyEngineError(message)
        return value

    def strict_text(value: Any, message: str) -> str:
        if not isinstance(value, str) or len(value) > 256:
            raise ApplyEngineError(message)
        return value

    def strict_bool(value: Any, message: str) -> bool:
        if type(value) is not bool:
            raise ApplyEngineError(message)
        return value

    try:
        raw_placements = payload["placements"]
        if not isinstance(raw_placements, list | tuple):
            raise ApplyEngineError("placement candidate placements are malformed")
        placements: list[FootprintPlacement] = []
        for item in raw_placements:
            if not isinstance(item, Mapping):
                raise ApplyEngineError("placement entry is malformed")
            origin = item["origin_nm"]
            if not isinstance(origin, list | tuple) or len(origin) != 2:
                raise ApplyEngineError("placement origin is malformed")
            placements.append(
                FootprintPlacement(
                    ref_id=strict_text(item["ref_id"], "placement reference is malformed"),
                    origin_x_nm=strict_int(origin[0], "placement origin is malformed"),
                    origin_y_nm=strict_int(origin[1], "placement origin is malformed"),
                    orientation_udeg=strict_int(
                        item["orientation_udeg"], "placement rotation is malformed"
                    ),
                    side=strict_text(item["side"], "placement side is malformed"),
                    moved=strict_bool(item["moved"], "placement moved flag is malformed"),
                )
            )
        evidence_payload = payload["evidence"]
        if not isinstance(evidence_payload, Mapping):
            raise ApplyEngineError("placement evidence is malformed")
        raw_rules = evidence_payload["rule_results"]
        if not isinstance(raw_rules, list | tuple):
            raise ApplyEngineError("placement rule evidence is malformed")
        rules = tuple(
            RuleResult(
                rule_index=strict_int(item["rule_index"], "placement rule evidence is malformed"),
                kind=strict_text(item["kind"], "placement rule evidence is malformed"),
                status=strict_text(item["status"], "placement rule evidence is malformed"),
                residual_nm=strict_int(item["residual_nm"], "placement rule evidence is malformed"),
            )
            for item in raw_rules
            if isinstance(item, Mapping)
        )
        if len(rules) != len(raw_rules):
            raise ApplyEngineError("placement rule evidence is malformed")
        legality_payload = evidence_payload["legality"]
        if not isinstance(legality_payload, Mapping):
            raise ApplyEngineError("placement legality evidence is malformed")
        legality = PlacementLegality(
            pad_overlap=strict_text(
                legality_payload["pad_overlap"], "placement legality evidence is malformed"
            ),
            outline_containment=strict_text(
                legality_payload["outline_containment"],
                "placement legality evidence is malformed",
            ),
            keepout_respect=strict_text(
                legality_payload["keepout_respect"],
                "placement legality evidence is malformed",
            ),
            courtyard_overlap=strict_text(
                legality_payload.get("courtyard_overlap", "proven_clear"),
                "placement legality evidence is malformed",
            ),
        )
        candidate_id = strict_text(
            payload["candidate_id"], "placement candidate identity is malformed"
        )
        base_revision = strict_text(
            payload["base_revision"], "placement base revision is malformed"
        )
        view_revision = strict_text(
            payload["view_revision"], "placement view revision is malformed"
        )
        ordering_policy = strict_text(
            payload["ordering_policy"], "placement ordering policy is malformed"
        )
        placement_version = strict_text(
            payload["placement_version"], "placement version is malformed"
        )
        if ordering_policy != ORDERING_POLICY or placement_version != PLACEMENT_VERSION:
            raise ApplyEngineError("placement candidate version or ordering policy is malformed")
        return PlacementCandidate(
            candidate_id=candidate_id,
            base_revision=base_revision,
            view_revision=view_revision,
            placements=tuple(placements),
            evidence=PlacementEvidence(
                rule_results=rules,
                legality=legality,
                checks_used=strict_int(
                    evidence_payload["checks_used"], "placement evidence is malformed"
                ),
                inconclusive_pairs=strict_int(
                    evidence_payload["inconclusive_pairs"], "placement evidence is malformed"
                ),
            ),
            placement_grid_nm=strict_int(
                payload["placement_grid_nm"], "placement candidate grid is malformed"
            ),
            ordering_policy=ordering_policy,
            placement_version=placement_version,
        )
    except ApplyEngineError:
        raise
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise ApplyEngineError("placement candidate manifest is malformed") from error


def _refuse(
    request: ApplyRequest | None,
    board_path: str,
    board_revision: str | None,
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


def _placement_refuse(
    request: PlacementApplyRequest | None,
    board_path: str,
    board_revision: str | None,
    code: ApplyFailureCode,
    message: str,
    **extra: Any,
) -> PlacementApplyResult:
    return PlacementApplyResult(
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

    # Resolve the canonical path without reading the board, so a refusal names the same path
    # every other refusal would and no digest is synthesised for a board we never read.
    try:
        relative_path = resolve_workspace_relative_path(
            settings.workspace, request.board, allowed_suffixes={".kicad_pcb"}
        )
    except WorkspaceViolationError as error:
        return _refuse(request, request.board, None, ApplyFailureCode.INVALID_REQUEST, str(error))

    if not settings.allow_apply:
        # The tool stays listed so a client can explain it; the capability is simply off.
        return _refuse(
            request,
            relative_path,
            None,
            ApplyFailureCode.APPLY_DISABLED,
            "applying candidates is disabled; set COPPER_MCP_ALLOW_APPLY=1 to enable it",
        )

    # Authorize before the heavy work. The token binds identity strings that are already
    # bounded and the claimed board revision, none of which needs the board to be read.
    binding = ApplyBinding(
        candidate_id=str(request.candidate.get("candidate_id", "")),
        base_revision=str(request.candidate.get("base_revision", "")),
        board_revision=request.expect_board_revision,
        relative_path=relative_path,
    )
    try:
        verified = token_authority.verify(request.apply_token, binding)
    except ApplyTokenError as error:
        return _refuse(request, relative_path, None, ApplyFailureCode(error.code), str(error))

    board = _read_board(settings, request.board)
    if lockfile_for(board.absolute_path).exists():
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.KICAD_OPEN,
            _lockfile_message(board.absolute_path),
        )

    # First compare-and-swap, before any parsing: the caller must have previewed these bytes.
    if board.revision != request.expect_board_revision:
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.STALE_CANDIDATE,
            "the board changed since the preview; re-run the preview and review the result",
        )

    constraints = NetClass(id=NET_CLASS_ID, name=NET_CLASS_NAME, **request.constraints_payload())
    profile = _profile(constraints)
    limits = parse_limits_for(settings)

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
    except (ApplyEngineError, KiCadRoutePatchError) as error:
        # Both are the pure engine refusing to produce bytes. A KiCadRoutePatchError escaping
        # here previously crashed the destructive tool on a legal board whose outline carried
        # a derived rather than native identity.
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
        # rather than proceed without one.
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.BACKUP_FAILED,
            f"the pre-apply copy could not be written, so nothing was changed: {error}",
        )

    def _recheck_lockfile() -> None:
        # Runs under the exclusive lock, immediately before the rename. A GUI opened between
        # the first check and here would otherwise silently overwrite the applied board.
        if lockfile_for(board.absolute_path).exists():
            raise _LockfileAppearedError(board.absolute_path.name)

    try:
        replace_workspace_file(
            settings.workspace,
            board.relative_path,
            applied.content,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
            expect_digest=board.revision,
            precheck=_recheck_lockfile,
        )
    except _LockfileAppearedError:
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.KICAD_OPEN,
            _lockfile_message(board.absolute_path),
            backup_path=backup_path,
        )
    except WorkspaceStaleError:
        # Caught under the lock, before the rename: the board is untouched.
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.STALE_CANDIDATE,
            "the board changed while the candidate was being applied",
            backup_path=backup_path,
        )
    except (WorkspaceViolationError, OSError) as error:
        # A pre-rename replacement failure: everything after the rename is turned into a
        # WorkspacePostRenameError by replace_workspace_file, so any plain OSError or workspace
        # violation reaching here happened before the board was touched.
        return _refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.APPLY_VERIFICATION_FAILED,
            f"the board could not be replaced safely: {error}",
            backup_path=backup_path,
        )
    except WorkspacePostRenameError as error:
        # The rename happened, so the board IS changed. Report that truthfully and attempt a
        # guarded rollback that only touches the file if it still holds exactly our bytes.
        # A post-rename condition spends the capability even when the guarded rollback succeeds:
        # otherwise the same token could replay a second write against the restored revision.
        token_authority.consume(verified)
        restored = _guarded_restore(settings, board, error.published_revision)
        final_revision = _final_observed_revision(settings, board)
        return ApplyResult(
            status="applied_but_unverified",
            board_path=board.relative_path,
            board_revision_before=board.revision,
            board_revision_after=final_revision,
            snapshot_digest_before=snapshot.snapshot_digest,
            base_revision=candidate.base_revision,
            candidate_id=candidate.candidate_id,
            request=request,
            backup_path=backup_path,
            bytes_added=applied.bytes_added,
            segments_added=applied.segments_added,
            diagnostic=ApplyDiagnostic(
                code=ApplyFailureCode.APPLY_VERIFICATION_FAILED,
                message=(
                    "the board was written but could not be verified afterwards; "
                    + (
                        "the original was rolled back"
                        if restored
                        else "it now holds bytes we did not write, so it was left in place; "
                        "restore the pre-apply copy if needed"
                    )
                ),
            ),
        )

    # Take one final observation immediately before reporting success.  The publication lock is
    # released by replace_workspace_file before this point, so a writer can still win the tiny
    # interval after the atomic rename.  We cannot eliminate that final nanosecond without a
    # longer transaction, but a visible rewrite must not be reported as a verified apply.
    final_revision = _final_observed_revision(settings, board)
    if final_revision != applied.result_revision:
        token_authority.consume(verified)
        return ApplyResult(
            status="applied_but_unverified",
            board_path=board.relative_path,
            board_revision_before=board.revision,
            board_revision_after=final_revision,
            snapshot_digest_before=snapshot.snapshot_digest,
            base_revision=candidate.base_revision,
            candidate_id=candidate.candidate_id,
            request=request,
            backup_path=backup_path,
            bytes_added=applied.bytes_added,
            segments_added=applied.segments_added,
            diagnostic=ApplyDiagnostic(
                code=ApplyFailureCode.APPLY_VERIFICATION_FAILED,
                message=(
                    "the authorized route was verified once, but "
                    + (
                        "the final board revision could not be observed; the board may be "
                        "missing or unreadable; restore the pre-apply copy if needed"
                        if final_revision is None
                        else "the final observed board revision changed before return; "
                        "concurrent bytes were left in place; restore the pre-apply copy if needed"
                    )
                ),
            ),
        )

    token_authority.consume(verified)
    return ApplyResult(
        status="applied",
        board_path=board.relative_path,
        board_revision_before=board.revision,
        board_revision_after=final_revision,
        snapshot_digest_before=snapshot.snapshot_digest,
        base_revision=candidate.base_revision,
        candidate_id=candidate.candidate_id,
        request=request,
        backup_path=backup_path,
        bytes_added=applied.bytes_added,
        segments_added=applied.segments_added,
        verification=applied.verification,
    )


def apply_placement_candidate(
    payload: Any,
    settings: Settings,
    token_authority: ApplyTokenAuthority,
    *,
    clock: Callable[[], float] | None = None,
) -> PlacementApplyResult:
    """Apply one authorized placement candidate to a workspace board.

    This deliberately mirrors route application at the mutation boundary, but has its own
    parser, token operation domain, pure engine, result vocabulary, and MCP tool.  A route token
    can never authorize this function, and a placement candidate can never enter the additive
    route patch path by accident.
    """

    if not isinstance(settings, Settings):
        raise ApplyServiceError("placement apply settings are malformed")
    if not isinstance(token_authority, ApplyTokenAuthority):
        raise ApplyServiceError("placement apply token authority is malformed")
    now = clock if callable(clock) else time.time

    request = parse_placement_apply_request(payload)
    try:
        relative_path = resolve_workspace_relative_path(
            settings.workspace, request.board, allowed_suffixes={".kicad_pcb"}
        )
    except WorkspaceViolationError as error:
        return _placement_refuse(
            request, request.board, None, ApplyFailureCode.INVALID_REQUEST, str(error)
        )

    if not settings.allow_apply:
        return _placement_refuse(
            request,
            relative_path,
            None,
            ApplyFailureCode.APPLY_DISABLED,
            "applying placement candidates is disabled; set COPPER_MCP_ALLOW_APPLY=1 to enable it",
        )

    binding = ApplyBinding(
        candidate_id=str(request.candidate.get("candidate_id", "")),
        base_revision=str(request.candidate.get("base_revision", "")),
        board_revision=request.expect_board_revision,
        relative_path=relative_path,
        operation="placement",
    )
    try:
        verified = token_authority.verify(request.apply_token, binding)
    except ApplyTokenError as error:
        return _placement_refuse(
            request, relative_path, None, ApplyFailureCode(error.code), str(error)
        )

    board = _read_board(settings, request.board)
    if lockfile_for(board.absolute_path).exists():
        return _placement_refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.KICAD_OPEN,
            _lockfile_message(board.absolute_path),
        )
    if board.revision != request.expect_board_revision:
        return _placement_refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.STALE_CANDIDATE,
            (
                "the board changed since the placement preview; re-run the preview and review "
                "the result"
            ),
        )

    constraints = NetClass(id=NET_CLASS_ID, name=NET_CLASS_NAME, **request.constraints_payload())
    profile = _profile(constraints)
    limits = parse_limits_for(settings)
    conversion = parse_kicad_bytes(board.content, profile, limits)
    if conversion.snapshot is None or conversion.diagnostics:
        return _placement_refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.UNSUPPORTED_BOARD,
            "this board is outside the supported Board IR subset",
        )
    snapshot = conversion.snapshot
    try:
        candidate = _placement_candidate_from_manifest(request.candidate)
    except ApplyEngineError as error:
        return _placement_refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.INVALID_REQUEST,
            str(error),
        )
    if snapshot.snapshot_digest != candidate.base_revision:
        return _placement_refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.STALE_CANDIDATE,
            "the placement candidate was proposed against a different Board IR snapshot",
        )

    try:
        applied = apply_placement_bytes(board.content, snapshot, candidate, profile, limits=limits)
    except (ApplyEngineError, KiCadPlacementPatchError) as error:
        return _placement_refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.SPLICE_ASSERTION_FAILED,
            str(error),
        )

    filesystem = unsafe_filesystem(board.absolute_path.parent)
    if filesystem is not None:
        return _placement_refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.UNSAFE_FILESYSTEM,
            (
                f"the board is on a {filesystem} filesystem, where atomic replacement and fsync "
                "do not carry their usual guarantees; copy it to local storage first"
            ),
        )
    try:
        backup_path = _write_backup(settings, board, now)
    except (WorkspaceViolationError, OSError) as error:
        return _placement_refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.BACKUP_FAILED,
            f"the pre-apply copy could not be written, so nothing was changed: {error}",
        )

    def _recheck_lockfile() -> None:
        if lockfile_for(board.absolute_path).exists():
            raise _LockfileAppearedError(board.absolute_path.name)

    try:
        replace_workspace_file(
            settings.workspace,
            board.relative_path,
            applied.content,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
            expect_digest=board.revision,
            precheck=_recheck_lockfile,
        )
    except _LockfileAppearedError:
        return _placement_refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.KICAD_OPEN,
            _lockfile_message(board.absolute_path),
            backup_path=backup_path,
        )
    except WorkspaceStaleError:
        return _placement_refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.STALE_CANDIDATE,
            "the board changed while the placement candidate was being applied",
            backup_path=backup_path,
        )
    except (WorkspaceViolationError, OSError) as error:
        return _placement_refuse(
            request,
            board.relative_path,
            board.revision,
            ApplyFailureCode.APPLY_VERIFICATION_FAILED,
            f"the board could not be replaced safely: {error}",
            backup_path=backup_path,
        )
    except WorkspacePostRenameError as error:
        # A post-rename condition spends the capability even when the guarded rollback succeeds;
        # otherwise the same token could replay a second write against the restored revision.
        token_authority.consume(verified)
        restored = _guarded_restore(settings, board, error.published_revision)
        final_revision = _observed_revision(
            settings,
            board,
            fallback=board.revision if restored else error.published_revision,
        )
        return PlacementApplyResult(
            status="applied_but_unverified",
            board_path=board.relative_path,
            board_revision_before=board.revision,
            board_revision_after=final_revision,
            snapshot_digest_before=snapshot.snapshot_digest,
            base_revision=candidate.base_revision,
            candidate_id=candidate.candidate_id,
            request=request,
            backup_path=backup_path,
            bytes_changed=applied.bytes_changed,
            footprints_moved=applied.footprints_moved,
            diagnostic=ApplyDiagnostic(
                code=ApplyFailureCode.APPLY_VERIFICATION_FAILED,
                message=(
                    "the board was written but could not be verified afterwards; "
                    + (
                        "the original was rolled back"
                        if restored
                        else "it now holds bytes we did not write, so it was left in place; "
                        "restore the pre-apply copy if needed"
                    )
                ),
            ),
        )

    published_revision = applied.result_revision
    try:
        published = _read_board(settings, board.relative_path)
        published_revision = published.revision
        verify_published_placement_board(
            published.content,
            board.content,
            snapshot,
            candidate,
            profile,
            limits=limits,
        )
    except (ApplyEngineError, WorkspaceViolationError, OSError):
        # Never roll back bytes merely because they were observed after our rename: they may
        # belong to a concurrent writer.  Restoration is allowed only if the board still holds
        # the exact output this operation published.
        # The rename already spent the capability, even when the guarded restore succeeds.
        token_authority.consume(verified)
        restored = _guarded_restore(settings, board, applied.result_revision)
        final_revision = _observed_revision(
            settings,
            board,
            fallback=published_revision,
        )
        return PlacementApplyResult(
            status="applied_but_unverified",
            board_path=board.relative_path,
            board_revision_before=board.revision,
            board_revision_after=final_revision,
            snapshot_digest_before=snapshot.snapshot_digest,
            base_revision=candidate.base_revision,
            candidate_id=candidate.candidate_id,
            request=request,
            backup_path=backup_path,
            bytes_changed=applied.bytes_changed,
            footprints_moved=applied.footprints_moved,
            diagnostic=ApplyDiagnostic(
                code=ApplyFailureCode.APPLY_VERIFICATION_FAILED,
                message=(
                    "the placement board was written but failed post-publication verification; "
                    + (
                        "the original was rolled back"
                        if restored
                        else "it now holds bytes we did not write, so it was left in place; "
                        "restore the pre-apply copy if needed"
                    )
                ),
            ),
        )

    # Take one final observation immediately before reporting success.  The publication lock is
    # released by replace_workspace_file before this post-publication verification, so a writer
    # can still win the tiny interval after verification.  We cannot eliminate that last
    # nanosecond without a longer transaction, but we can refuse to claim success when the
    # reproducible race is visible and preserve the concurrent bytes.
    observed_final_revision = _final_observed_revision(settings, board)
    if observed_final_revision is None or observed_final_revision != published_revision:
        token_authority.consume(verified)
        return PlacementApplyResult(
            status="applied_but_unverified",
            board_path=board.relative_path,
            board_revision_before=board.revision,
            board_revision_after=observed_final_revision,
            snapshot_digest_before=snapshot.snapshot_digest,
            base_revision=candidate.base_revision,
            candidate_id=candidate.candidate_id,
            request=request,
            backup_path=backup_path,
            bytes_changed=applied.bytes_changed,
            footprints_moved=applied.footprints_moved,
            diagnostic=ApplyDiagnostic(
                code=ApplyFailureCode.APPLY_VERIFICATION_FAILED,
                message=(
                    "the authorized placement was verified once, but "
                    + (
                        "the final board revision could not be observed; the board may be "
                        "missing or unreadable; restore the pre-apply copy if needed"
                        if observed_final_revision is None
                        else "the final observed board revision changed before return; "
                        "concurrent bytes were left in place; restore the pre-apply copy if needed"
                    )
                ),
            ),
        )

    token_authority.consume(verified)
    return PlacementApplyResult(
        status="applied",
        board_path=board.relative_path,
        board_revision_before=board.revision,
        board_revision_after=observed_final_revision,
        snapshot_digest_before=snapshot.snapshot_digest,
        base_revision=candidate.base_revision,
        candidate_id=candidate.candidate_id,
        request=request,
        backup_path=backup_path,
        bytes_changed=applied.bytes_changed,
        footprints_moved=applied.footprints_moved,
        verification=applied.verification,
    )


def _lockfile_message(board: Path) -> str:
    return (
        "a KiCad lockfile is present, so the board may be open in the editor; "
        f"close KiCad and remove {lockfile_for(board).name} if it is stale"
    )


def _read_board(settings: Settings, requested: str) -> _Board:
    snapshot = read_workspace_file(
        settings.workspace,
        requested,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    root = settings.workspace.resolve(strict=True)
    try:
        mode = stat.S_IMODE(snapshot.path.stat(follow_symlinks=False).st_mode)
    except OSError:
        mode = 0o644
    return _Board(
        relative_path=snapshot.path.relative_to(root).as_posix(),
        absolute_path=snapshot.path,
        content=snapshot.content,
        revision=_revision(snapshot.content),
        mode=mode,
    )


def _write_backup(settings: Settings, board: _Board, now: Callable[[], float]) -> str:
    """Write the pre-apply copy into a backups subdirectory and return its relative path.

    The copies go in ``.copper-mcp-backups/`` rather than beside the board so that a backup is
    never itself a valid ``.kicad_pcb`` apply target - a cascading ``pre-apply.pre-apply`` name
    otherwise appears. They are timestamped and content-addressed, kept to a bounded count per
    board, and given the board's own permission bits. KiCad's ``-bak`` files are never touched.
    """

    root = settings.workspace.resolve(strict=True)
    backups_relative = (Path(board.relative_path).parent / _BACKUPS_DIRNAME).as_posix()
    _ensure_backups_dir(root, backups_relative)

    stamp = datetime.fromtimestamp(now(), tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    digest = board.revision.removeprefix("sha256:")[:16]
    prefix = f"{board.absolute_path.name}."
    name = f"{prefix}{stamp}.{digest}.pre-apply.kicad_pcb"
    relative = f"{backups_relative}/{name}"
    try:
        created = create_workspace_file(
            settings.workspace,
            relative,
            board.content,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
        )
    except WorkspaceViolationError:
        # A retry within the same second finds its own copy already there. The name carries the
        # source digest, so an existing file under it is the same board state - reuse it after
        # confirming that, rather than failing an apply over a backup that is already correct.
        existing = read_workspace_file(
            settings.workspace,
            relative,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
        )
        if existing.content != board.content:
            raise
        created = existing.path
    try:
        created.chmod(board.mode)
    except OSError:
        pass
    _prune_backups(root / backups_relative, prefix)
    return created.relative_to(root).as_posix()


def _ensure_backups_dir(root: Path, backups_relative: str) -> None:
    directory = root / backups_relative
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A symlink planted at the backups name would redirect copies outside the workspace;
    # create_workspace_file's O_NOFOLLOW walk also rejects it, but refusing here is clearer.
    if directory.is_symlink() or not directory.resolve(strict=True).is_relative_to(root):
        raise WorkspaceViolationError("backups directory is not a real directory in the workspace")


def _prune_backups(directory: Path, prefix: str) -> None:
    try:
        copies = sorted(
            item
            for item in directory.iterdir()
            if item.name.startswith(prefix)
            and item.name.endswith(".pre-apply.kicad_pcb")
            and not item.is_symlink()
            and item.is_file()
        )
    except OSError:
        return
    # Names sort by their UTC timestamp, so the oldest are first. Keep the newest N.
    for stale in copies[:-MAX_BACKUPS_PER_BOARD] if len(copies) > MAX_BACKUPS_PER_BOARD else []:
        try:
            stale.unlink()
        except OSError:
            continue


def _guarded_restore(settings: Settings, board: _Board, published_revision: str) -> bool:
    """Roll a board back to its pre-apply bytes, but only if it still holds *our* write.

    The likeliest cause of a post-publish verification failure is a concurrent writer, so an
    unconditional restore would be the data loss it is meant to prevent. The restore therefore
    only runs when the on-disk digest still equals the bytes this apply published; if anything
    else is there, a third party has written and the file is left alone.
    """

    try:
        current = _read_board(settings, board.relative_path)
    except (WorkspaceViolationError, OSError):
        return False
    if current.revision != published_revision:
        return False
    try:
        replace_workspace_file(
            settings.workspace,
            board.relative_path,
            board.content,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
            expect_digest=published_revision,
        )
    except (WorkspaceViolationError, WorkspacePostRenameError, OSError):
        return False
    return True


def _observed_revision(settings: Settings, board: _Board, *, fallback: str) -> str:
    """Return the digest actually visible after a post-rename recovery attempt.

    A post-rename durability error can be raised after the replacement bytes are visible.  The
    guarded restore has the same property: it may publish the original bytes and then fail while
    syncing the directory.  Re-reading here keeps the response truthful even in that narrow
    window; the fallback is used only when the file cannot be observed.
    """

    try:
        return _read_board(settings, board.relative_path).revision
    except (WorkspaceViolationError, OSError):
        return fallback


def _final_observed_revision(settings: Settings, board: _Board) -> str | None:
    """Best-effort final observation for a successfully published apply.

    Unlike recovery reporting, a success path must not substitute an expected digest after an
    unreadable or missing board. Returning ``None`` makes the published-but-unobserved state
    explicit so callers report it as ``applied_but_unverified`` and spend the token.
    """

    try:
        return _read_board(settings, board.relative_path).revision
    except (WorkspaceViolationError, OSError):
        return None


__all__ = [
    "MAX_BACKUPS_PER_BOARD",
    "ApplyServiceError",
    "apply_candidate",
    "apply_placement_candidate",
    "lockfile_for",
    "unsafe_filesystem",
]
