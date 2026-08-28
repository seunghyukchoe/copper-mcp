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
from copper_mcp.apply_token_reasons import apply_token_withheld_reason
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError
from copper_mcp.kicad_ipc import capture_live_board
from copper_mcp.parse_budgets import parse_limits_for
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

#: The live placement seam shares the pipeline below but mints no capability under any setting:
#: its parser refuses ``include_apply_token`` outright. Read out of the shared order rather than
#: typed in beside this surface, so the set stays closed.
_LIVE_WITHHELD_REASON = apply_token_withheld_reason(
    surface_mints_tokens=False,
    requested=False,
    apply_enabled=False,
    has_candidate=False,
)
assert _LIVE_WITHHELD_REASON is not None


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
    deadline: float | None = None,
    mints_apply_tokens: bool = True,
) -> PlacementResult:
    """Run the deterministic placement pipeline over one already-bound source.

    ``mints_apply_tokens`` is false for the live surface, which shares this pipeline but can
    never issue a capability: its parser refuses ``include_apply_token`` outright. Saying so
    with ``unsupported_surface`` is not the same statement as ``not_requested``, and a caller
    deciding whether to ask again needs the difference.
    """

    if deadline is None:
        deadline = time.monotonic() + float(settings.max_placement_seconds)
    apply_enabled = settings.allow_apply and isinstance(token_authority, ApplyTokenAuthority)
    # Every return below that carries no candidate shares one reason, decided by the shared
    # order in `copper_mcp.apply_token_reasons` rather than restated per branch.
    withheld_without_candidate = apply_token_withheld_reason(
        surface_mints_tokens=mints_apply_tokens,
        requested=intent.include_apply_token,
        apply_enabled=apply_enabled,
        has_candidate=False,
    )
    assert withheld_without_candidate is not None

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
            apply_token_withheld_reason=withheld_without_candidate,
        )

    limits = parse_limits_for(settings)
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
            apply_token_withheld_reason=withheld_without_candidate,
        )

    if conversion.outline_inward_deviation_nm:
        # The board outline was inscribed rather than drawn: an ``Edge.Cuts`` arc has no equal
        # polygon, so the modelled boundary runs up to this many nanometres *inside* the drawn one
        # (D-229, ADR-0124).  Every other consumer of the outline is safe with that, because less
        # room is never a false claim about where copper may go.  Placement is not.  Its legality
        # contract publishes ``outline_containment`` in **both** directions -- ``proven_inside``
        # from an over-approximating pad box, and ``violated`` from an under-approximating pad
        # core crossing the boundary -- and only the first survives a boundary that is itself
        # under-approximated: copper sitting in the sliver between the inscribed polygon and the
        # true arc is inside the fabricated board and would be reported as crossing its edge.
        # The rule rules also measure *against* the boundary: an edge rule's residual is a
        # distance from the board's own bounding box, and a region rule keyed to this contour
        # decides inside-or-out by it.  Reporting a shrunken answer for either is a false claim
        # and not a conservative one, so the whole request is refused by name rather than three
        # verdicts being quietly degraded.  See ADR-0124 for the exit condition.
        return PlacementResult(
            status="refused",
            board_revision=board_revision,
            board_path=relative_path,
            request=intent,
            snapshot_digest=conversion.snapshot.snapshot_digest,
            diagnostic=PlacementDiagnostic(
                code=PlacementFailureCode.UNSUPPORTED_GEOMETRY,
                message=("placement needs an exact board outline and this board's is approximated"),
            ),
            apply_token_withheld_reason=withheld_without_candidate,
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
            apply_token_withheld_reason=withheld_without_candidate,
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
            apply_token_withheld_reason=withheld_without_candidate,
        )

    remaining_seconds = deadline - time.monotonic()
    if remaining_seconds <= 0:
        return PlacementResult(
            status="refused",
            board_revision=board_revision,
            board_path=relative_path,
            request=intent,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=PlacementDiagnostic(
                code=PlacementFailureCode.BUDGET_EXHAUSTED,
                message="placement preview deadline expired before legalization",
            ),
            apply_token_withheld_reason=withheld_without_candidate,
        )

    result = evaluate_placement(
        intent,
        snapshot,
        view,
        max_checks=settings.max_placement_checks,
        deadline_seconds=remaining_seconds,
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
    if result.status != "previewed" or result.candidate is None:
        # The legalizer builds its refusals before this surface knows what the operator permits,
        # so the reason is stamped here rather than there.
        return replace(result, apply_token_withheld_reason=withheld_without_candidate)

    moves = any(item.moved for item in result.candidate.placements)
    replay_accepted = True
    if mints_apply_tokens and intent.include_apply_token and apply_enabled and moves:
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
            # R-149 lived exactly here, as `pass`. The refusal was swallowed, the caller got
            # `apply_token: null`, and nothing distinguished it from five other causes. The
            # exception object is deliberately *not* carried into the reason: it names the
            # board construct that refused, and a withheld reason discloses no board content.
            replay_accepted = False
    withheld = apply_token_withheld_reason(
        surface_mints_tokens=mints_apply_tokens,
        requested=intent.include_apply_token,
        apply_enabled=apply_enabled,
        has_candidate=True,
        candidate_moves=moves,
        replay_accepted=replay_accepted,
    )
    if withheld is not None:
        return replace(result, apply_token_withheld_reason=withheld)
    assert isinstance(token_authority, ApplyTokenAuthority)
    token = token_authority.issue(
        ApplyBinding(
            candidate_id=result.candidate.candidate_id,
            base_revision=result.candidate.base_revision,
            board_revision=board_revision,
            relative_path=relative_path,
            operation="placement",
        )
    )
    return replace(result, apply_token=token)


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
            apply_token_withheld_reason=_LIVE_WITHHELD_REASON,
        )

    result = _preview_placement_source(
        intent,
        captured.source,
        "live",
        board_revision,
        settings,
        token_authority=None,
        deadline=deadline,
        mints_apply_tokens=False,
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
            apply_token_withheld_reason=_LIVE_WITHHELD_REASON,
        )
    return result


__all__ = ["PLACEMENT_VERSION", "preview_live_placement", "preview_placement"]
