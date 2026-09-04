"""Workspace-facing bounded placement solve.

Mirrors ``placement_preview``: read one confined board, convert it, search it, and return
ranked legalizer-issued candidates. Nothing is written, no job is created, no KiCad process
is started, no apply token is minted under any setting, and no DRC evidence is produced.

Refusal ordering matches the preview surface exactly -- board revision, then conversion,
then snapshot CAS, then the approximated-outline refusal, then the view -- so a caller
holding a stale world-view learns that first rather than some property of a board it was
not looking at.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from typing import Any

from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.apply_token_reasons import apply_token_withheld_reason
from copper_mcp.config import Settings
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.placement import build_placement_view
from copper_mcp.placement.contracts import (
    PlacementDiagnostic,
    PlacementError,
    PlacementFailureCode,
    PlacementSolveResponse,
    parse_placement_solve_request,
)
from copper_mcp.placement.route_scoring import RouteProbeSettings
from copper_mcp.placement.solver import (
    PlacementScoringPolicy,
    PlacementSolverError,
    PlacementSolverSettings,
    solve_placement,
)
from copper_mcp.placement.view import PlacementViewError
from copper_mcp.security import read_workspace_file

#: The solve surface mints no capability under any setting. Decided once through the shared
#: order rather than restated per branch, so the set stays closed.
_SOLVE_WITHHELD_REASON = apply_token_withheld_reason(
    surface_mints_tokens=False,
    requested=False,
    apply_enabled=False,
    has_candidate=False,
)
assert _SOLVE_WITHHELD_REASON is not None


def solve_placement_preview(payload: Any, settings: Settings) -> PlacementSolveResponse:
    """Search bounded grid-adjacent moves and rank only legalizer-issued candidates."""

    if not isinstance(settings, Settings):
        raise PlacementError("placement settings are malformed")
    try:
        solve_request = parse_placement_solve_request(
            payload,
            max_subjects=settings.max_placement_subjects,
            max_rules=settings.max_placement_rules,
        )
    except PlacementError:
        raise
    intent = solve_request.intent

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
    deadline = time.monotonic() + float(settings.max_placement_seconds)

    def refuse(
        code: PlacementFailureCode,
        message: str,
        *,
        status: str = "refused",
        snapshot_digest: str | None = None,
        conversion_diagnostic_counts: dict[str, int] | None = None,
        evaluations: int = 0,
        route_probes_used: int = 0,
        route_probe_limit: int = 0,
    ) -> PlacementSolveResponse:
        return PlacementSolveResponse(
            status=status,
            board_revision=board_revision,
            board_path=relative_path,
            request=solve_request,
            solver=solve_request.solver_settings_dict(),
            snapshot_digest=snapshot_digest,
            diagnostic=PlacementDiagnostic(code=code, message=message),
            evaluations=evaluations,
            route_probes_used=route_probes_used,
            route_probe_limit=route_probe_limit,
            scoring_policy=solve_request.scoring_policy,
            apply_token_withheld_reason=_SOLVE_WITHHELD_REASON,
            conversion_diagnostic_counts=conversion_diagnostic_counts or {},
        )

    def refuse_board(
        counts: Counter[str],
    ) -> PlacementSolveResponse:
        return refuse(
            PlacementFailureCode.UNSUPPORTED_BOARD,
            "this board is outside the supported Board IR subset",
            status="unsupported_board",
            conversion_diagnostic_counts=dict(counts),
        )

    if intent.expect_board_revision is not None and intent.expect_board_revision != board_revision:
        return refuse(
            PlacementFailureCode.STALE_REVISION,
            "board revision is stale",
        )

    limits = parse_limits_for(settings)
    conversion = parse_kicad_bytes(source, intent.profile(), limits)
    if conversion.snapshot is None or conversion.diagnostics:
        counts = Counter(diagnostic.code for diagnostic in conversion.diagnostics)
        return refuse_board(counts)

    snapshot = conversion.snapshot
    if (
        intent.expect_snapshot_digest is not None
        and intent.expect_snapshot_digest != snapshot.snapshot_digest
    ):
        return refuse(
            PlacementFailureCode.STALE_REVISION,
            "Board IR snapshot revision is stale",
            snapshot_digest=snapshot.snapshot_digest,
        )

    if conversion.outline_inward_deviation_nm:
        # Same refusal as the preview surface, for the same reason: placement legality
        # publishes ``outline_containment`` in both directions and only one survives an
        # inscribed boundary (D-229, ADR-0124). A solved pose under an approximated outline
        # would be no more trustworthy than a previewed one.
        return refuse(
            PlacementFailureCode.UNSUPPORTED_GEOMETRY,
            "placement needs an exact board outline and this board's is approximated",
            snapshot_digest=snapshot.snapshot_digest,
        )
    try:
        view = build_placement_view(source, snapshot, limits=limits)
    except PlacementViewError as error:
        return refuse(
            PlacementFailureCode.UNSUPPORTED_GEOMETRY,
            str(error),
            snapshot_digest=snapshot.snapshot_digest,
        )

    remaining_seconds = deadline - time.monotonic()
    if remaining_seconds <= 0:
        return refuse(
            PlacementFailureCode.BUDGET_EXHAUSTED,
            "placement solve deadline expired before search",
            snapshot_digest=snapshot.snapshot_digest,
        )

    try:
        scoring_policy = PlacementScoringPolicy(solve_request.scoring_policy)
    except ValueError as error:
        raise PlacementError(
            "solver scoring_policy must be a supported PlacementScoringPolicy"
        ) from error
    try:
        solver_settings = PlacementSolverSettings(
            max_evaluations=solve_request.max_evaluations,
            max_rounds=solve_request.max_rounds,
            beam_width=solve_request.beam_width,
            max_ranked=solve_request.max_ranked,
            step_nm=solve_request.step_nm,
            deadline_seconds=remaining_seconds,
            legalizer_max_checks=min(200_000, settings.max_placement_checks),
            legalizer_deadline_seconds=min(1.0, remaining_seconds),
            scoring_policy=scoring_policy,
            route_probe_settings=RouteProbeSettings(),
        )
    except PlacementSolverError as error:
        raise PlacementError(f"solver settings are malformed: {error}") from error

    result = solve_placement(
        intent,
        snapshot,
        view,
        settings=solver_settings,
        board_path=relative_path,
    )
    if result.status == "completed":
        return PlacementSolveResponse(
            status="solved",
            board_revision=board_revision,
            board_path=relative_path,
            request=solve_request,
            solver=solve_request.solver_settings_dict(),
            snapshot_digest=snapshot.snapshot_digest,
            candidates=tuple(item.candidate for item in result.ranked),
            evaluations=result.evaluations,
            route_probes_used=result.route_probes_used,
            route_probe_limit=result.route_probe_limit,
            scoring_policy=solve_request.scoring_policy,
            apply_token_withheld_reason=_SOLVE_WITHHELD_REASON,
        )
    if result.status == "input_refused":
        if result.initial is not None:
            initial = result.initial
            return PlacementSolveResponse(
                status="refused",
                board_revision=board_revision,
                board_path=relative_path,
                request=solve_request,
                solver=solve_request.solver_settings_dict(),
                snapshot_digest=snapshot.snapshot_digest,
                diagnostic=initial.diagnostic,
                evaluations=result.evaluations,
                route_probes_used=result.route_probes_used,
                route_probe_limit=result.route_probe_limit,
                scoring_policy=solve_request.scoring_policy,
                apply_token_withheld_reason=_SOLVE_WITHHELD_REASON,
            )
        # Two proposals collapsing onto one footprint with different offsets: a syntactic
        # contradiction, which is what infeasible_constraints names. Fixed text, no refs.
        return refuse(
            PlacementFailureCode.INFEASIBLE_CONSTRAINTS,
            "two proposals move one footprint",
            snapshot_digest=snapshot.snapshot_digest,
            evaluations=result.evaluations,
            route_probes_used=result.route_probes_used,
            route_probe_limit=result.route_probe_limit,
        )
    if result.status in ("work_exhausted", "deadline_exhausted"):
        return refuse(
            PlacementFailureCode.BUDGET_EXHAUSTED,
            "placement solve exhausted its search work before ranking",
            snapshot_digest=snapshot.snapshot_digest,
            evaluations=result.evaluations,
            route_probes_used=result.route_probes_used,
            route_probe_limit=result.route_probe_limit,
        )
    # The service passes no cancellation callback, so `cancelled` is unreachable. An unknown
    # status is an internal invariant violation, which is a defect rather than a refusal.
    raise PlacementError(f"placement solver returned an unknown status: {result.status}")


__all__ = ["solve_placement_preview"]
