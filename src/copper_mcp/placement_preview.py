"""Workspace-facing placement preview.

Mirrors ``route_preview``: read one confined board, convert it, evaluate, and return a typed
result. Nothing is written, no job is created, and no KiCad process is started.

The board is loaded through ``read_workspace_file`` so a caller cannot name a path outside the
configured workspace, and the request is validated before any file is touched.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import replace
from typing import Any

from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.board_ir import ParseLimits
from copper_mcp.config import Settings
from copper_mcp.placement import build_placement_view, evaluate_placement
from copper_mcp.placement.contracts import (
    PLACEMENT_VERSION,
    PlacementDiagnostic,
    PlacementError,
    PlacementFailureCode,
    PlacementResult,
    parse_placement_intent,
)
from copper_mcp.placement.view import PlacementViewError
from copper_mcp.security import read_workspace_file


def preview_placement(payload: Any, settings: Settings) -> PlacementResult:
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

    return evaluate_placement(
        intent,
        snapshot,
        view,
        max_checks=settings.max_placement_checks,
        deadline_seconds=float(settings.max_placement_seconds),
        board_path=relative_path,
    )


__all__ = ["PLACEMENT_VERSION", "preview_placement"]
