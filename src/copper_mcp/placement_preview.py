"""Workspace-facing placement preview.

Mirrors ``route_preview``: read one confined board, convert it, evaluate, and return a typed
result. Nothing is written, no job is created, and no KiCad process is started.

The board is loaded through ``read_workspace_file`` so a caller cannot name a path outside the
configured workspace, and the request is validated before any file is touched.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from dataclasses import replace
from typing import Any

from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.adapters.kicad_placement_patch import (
    KiCadPlacementPatchError,
    render_kicad_placement_candidate_board,
)
from copper_mcp.apply.tokens import ApplyBinding, ApplyTokenAuthority
from copper_mcp.board_ir import ParseLimits
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError
from copper_mcp.kicad_ipc import capture_live_board
from copper_mcp.placement import build_placement_view, evaluate_placement
from copper_mcp.placement.contracts import (
    PLACEMENT_VERSION,
    PlacementDiagnostic,
    PlacementError,
    PlacementFailureCode,
    PlacementPreviewError,
    PlacementResult,
    parse_placement_intent,
)
from copper_mcp.placement.view import PlacementViewError
from copper_mcp.placement_drc import PlacementCandidateDrcEvidence, run_placement_candidate_drc
from copper_mcp.security import read_workspace_file


def preview_placement(
    payload: Any,
    settings: Settings,
    token_authority: ApplyTokenAuthority | None = None,
) -> PlacementResult:
    """Validate one placement proposal against a workspace board without mutating it."""

    if not isinstance(settings, Settings):
        raise PlacementError("placement settings are malformed")
    intent = parse_placement_intent(
        payload,
        max_subjects=settings.max_placement_subjects,
        max_rules=settings.max_placement_rules,
    )

    workspace_root = settings.workspace.resolve(strict=True)
    board = read_workspace_file(
        settings.workspace,
        intent.board,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    relative_path = board.path.relative_to(workspace_root).as_posix()
    source = board.content
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"

    return _preview_placement_source(
        intent,
        source,
        relative_path,
        board_revision,
        settings,
        token_authority=token_authority,
    )


def _preview_placement_source(
    intent: Any,
    source: bytes,
    relative_path: str,
    board_revision: str,
    settings: Settings,
    *,
    token_authority: ApplyTokenAuthority | None = None,
) -> PlacementResult:
    """Run the deterministic placement pipeline over one already-bound source."""

    deadline = time.monotonic() + float(settings.max_placement_seconds)

    # A caller may bind a file-backed request to a previously observed revision as well as a
    # live request.  Honor that precondition before parsing so a stale request cannot echo its
    # expected digest as if it had been checked.
    if intent.expect_board_revision is not None and intent.expect_board_revision != board_revision:
        return PlacementResult(
            status="refused",
            board_revision=board_revision,
            board_path=relative_path,
            request=intent,
            diagnostic=PlacementDiagnostic(
                code=PlacementFailureCode.STALE_REVISION,
                message="board revision is stale",
            ),
        )

    default_limits = ParseLimits()
    limits = replace(
        default_limits,
        max_input_bytes=min(default_limits.max_input_bytes, settings.max_board_bytes),
    )
    conversion = parse_kicad_bytes(source, intent.profile(), limits)
    if conversion.snapshot is None or conversion.diagnostics:
        counts = Counter(diagnostic.code for diagnostic in conversion.diagnostics)
        return PlacementResult(
            status="unsupported_board",
            board_revision=board_revision,
            board_path=relative_path,
            request=intent,
            diagnostic=PlacementDiagnostic(
                code=PlacementFailureCode.UNSUPPORTED_BOARD,
                message="this board is outside the supported Board IR subset",
            ),
            conversion_diagnostic_counts=dict(counts),
        )

    snapshot = conversion.snapshot
    # Snapshot CAS stops immediately after conversion and before placement-view construction or
    # legalizer work.  This keeps stale requests bounded even when a board is expensive to
    # evaluate.
    if (
        intent.expect_snapshot_digest is not None
        and intent.expect_snapshot_digest != snapshot.snapshot_digest
    ):
        return PlacementResult(
            status="refused",
            board_revision=board_revision,
            board_path=relative_path,
            request=intent,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=PlacementDiagnostic(
                code=PlacementFailureCode.STALE_REVISION,
                message="Board IR snapshot revision is stale",
            ),
        )
    try:
        view = build_placement_view(source, snapshot, limits=limits)
    except PlacementViewError as error:
        # Footprint identity could not be recovered, so nothing here can be placed. Typed and
        # non-echoing: the message describes the failure, never the board's contents.
        return PlacementResult(
            status="refused",
            board_revision=board_revision,
            board_path=relative_path,
            request=intent,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=PlacementDiagnostic(
                code=PlacementFailureCode.UNSUPPORTED_GEOMETRY,
                message=str(error),
            ),
        )

    result = evaluate_placement(
        intent,
        snapshot,
        view,
        max_checks=settings.max_placement_checks,
        deadline_seconds=float(settings.max_placement_seconds),
        board_path=relative_path,
    )
    if result.status == "previewed" and result.candidate is not None and intent.include_drc:
        try:
            evidence = run_placement_candidate_drc(
                relative_path,
                result.candidate,
                intent.profile(),
                _placement_drc_settings(settings, deadline),
            )
        except KiCadCliError as error:
            raise PlacementPreviewError(
                "authoritative placement DRC evidence is unavailable"
            ) from error
        if not isinstance(evidence, PlacementCandidateDrcEvidence):
            raise PlacementPreviewError("authoritative placement DRC evidence is malformed")
        if (
            evidence.candidate_id != result.candidate.candidate_id
            or evidence.candidate_base_revision != result.candidate.base_revision
            or evidence.source_revision != board_revision
        ):
            raise PlacementPreviewError(
                "authoritative placement DRC evidence is not bound to this candidate"
            )
        result = replace(result, drc_evidence=evidence)
    if (
        result.status == "previewed"
        and result.candidate is not None
        and intent.include_apply_token
        and settings.allow_apply
        and isinstance(token_authority, ApplyTokenAuthority)
        and any(item.moved for item in result.candidate.placements)
    ):
        # The capability is minted only after the same pure replay used by placement DRC accepts
        # the source. A legalizer candidate outside the current source-preserving subset remains
        # previewable, but cannot accidentally receive a token that the apply path must refuse.
        try:
            render_kicad_placement_candidate_board(
                source,
                snapshot,
                result.candidate,
                intent.profile(),
                limits=limits,
            )
        except KiCadPlacementPatchError:
            pass
        else:
            token = token_authority.issue(
                ApplyBinding(
                    candidate_id=result.candidate.candidate_id,
                    base_revision=result.candidate.base_revision,
                    board_revision=board_revision,
                    relative_path=relative_path,
                    operation="placement",
                )
            )
            result = replace(result, apply_token=token)
    return result


def _placement_drc_settings(settings: Settings, deadline: float) -> Settings:
    """Clamp disposable KiCad DRC to the placement preview's remaining deadline."""

    remaining = int(deadline - time.monotonic())
    if remaining < 1:
        raise PlacementPreviewError("the placement preview deadline expired before DRC could run")
    return replace(settings, kicad_timeout_seconds=min(settings.kicad_timeout_seconds, remaining))


def _live_capture_timeout_ms(deadline: float) -> int:
    """Keep KiCad IPC connection setup within the placement operation's remaining budget."""

    remaining_ms = int((deadline - time.monotonic()) * 1_000)
    if remaining_ms < 1:
        raise PlacementPreviewError("the placement preview deadline expired before live capture")
    # Match the bounded capture adapter's normal two-second connection cap while never allowing
    # a live IPC request to consume more than this placement preview has left.
    return min(2_000, remaining_ms)


def preview_live_placement(
    payload: Any,
    settings: Settings,
    *,
    client_factory: Any = None,
) -> PlacementResult:
    """Preview a placement against one exact, read-only KiCad IPC snapshot.

    The IPC adapter is the only live boundary. Once its byte-confirmed snapshot is captured,
    the same Board IR and legalizer used by the file-backed tool produce the candidate. No
    editor mutation, DRC, fill, apply token, or raw board source is exposed.
    """

    if not isinstance(settings, Settings):
        raise PlacementError("placement settings are malformed")
    intent = parse_placement_intent(
        payload,
        max_subjects=settings.max_placement_subjects,
        max_rules=settings.max_placement_rules,
        allow_live=True,
        require_revisions=True,
    )
    if intent.board != "live":
        raise PlacementError("live placement requests must set board to 'live'")

    deadline = time.monotonic() + float(settings.max_placement_seconds)
    captured = capture_live_board(
        settings,
        client_factory=client_factory,
        timeout_ms=_live_capture_timeout_ms(deadline),
        deadline=deadline,
    )
    board_revision = captured.observation.board_digest
    if intent.expect_board_revision != board_revision:
        return PlacementResult(
            status="refused",
            board_revision=board_revision,
            board_path="live",
            request=intent,
            diagnostic=PlacementDiagnostic(
                code=PlacementFailureCode.STALE_REVISION,
                message="live board revision is stale",
            ),
        )

    result = _preview_placement_source(
        intent,
        captured.source,
        "live",
        board_revision,
        settings,
        token_authority=None,
    )
    if (
        intent.expect_snapshot_digest is not None
        and result.snapshot_digest is not None
        and intent.expect_snapshot_digest != result.snapshot_digest
    ):
        return PlacementResult(
            status="refused",
            board_revision=board_revision,
            board_path="live",
            request=intent,
            snapshot_digest=result.snapshot_digest,
            diagnostic=PlacementDiagnostic(
                code=PlacementFailureCode.STALE_REVISION,
                message="live Board IR snapshot is stale",
            ),
        )
    return result


__all__ = ["PLACEMENT_VERSION", "preview_live_placement", "preview_placement"]
