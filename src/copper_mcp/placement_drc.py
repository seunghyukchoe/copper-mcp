"""Private, candidate-bound KiCad DRC for the supported placement subset.

This module deliberately stays below the public MCP surface.  Placement DRC evidence is only
authoritative when the exact source snapshot, replay-verified disposable placement board, and
captured KiCad rule/library context are all bound by content digests.  The source workspace is
never written, and raw KiCad findings are reduced to the redacted :class:`DrcSummary` contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

import copper_mcp.kicad_cli as kicad_cli
from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.kicad_placement_patch import (
    KiCadPlacementPatchError,
    render_kicad_placement_candidate_board,
)
from copper_mcp.board_ir import ParseLimits
from copper_mcp.config import Settings
from copper_mcp.models import DrcSummary
from copper_mcp.placement.contracts import PlacementCandidate
from copper_mcp.security import read_workspace_file

_SHA256_ID = re.compile(r"^sha256:[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class PlacementCandidateDrcEvidence:
    """Immutable, redacted KiCad DRC evidence bound to one placement candidate."""

    candidate_id: str
    candidate_base_revision: str
    source_revision: str
    patched_board_revision: str
    patched_drc_context_revision: str
    summary: DrcSummary

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "candidate_base_revision",
            "source_revision",
            "patched_board_revision",
            "patched_drc_context_revision",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256_ID.fullmatch(value):
                raise ValueError(f"{name} must be content-addressed with sha256")
        if not isinstance(self.summary, DrcSummary):
            raise ValueError("summary must be strict KiCad DRC evidence")
        if self.summary.base_revision != self.patched_board_revision:
            raise ValueError("DRC summary is not bound to the patched board revision")
        if self.summary.drc_context_revision != self.patched_drc_context_revision:
            raise ValueError("DRC summary is not bound to the patched context revision")

    def to_dict(self) -> dict[str, Any]:
        """Return only digest bindings and redacted aggregate findings."""

        return {
            "candidate_id": self.candidate_id,
            "candidate_base_revision": self.candidate_base_revision,
            "source_revision": self.source_revision,
            "patched_board_revision": self.patched_board_revision,
            "patched_drc_context_revision": self.patched_drc_context_revision,
            "summary": self.summary.to_dict(),
        }


def run_placement_candidate_drc(
    requested_path: str,
    candidate: PlacementCandidate,
    profile: KiCadConstraintProfile,
    settings: Settings,
) -> PlacementCandidateDrcEvidence:
    """Bind a replay-verified placement candidate to private authoritative KiCad DRC.

    Only the placement serializer's supported subset is admitted.  KiCad runs against a private
    snapshot containing the candidate board and the captured project/rule/library context.  The
    original context is recaptured after DRC and any change discards the evidence.
    """

    if not isinstance(candidate, PlacementCandidate):
        raise kicad_cli.KiCadCliError("placement candidate is malformed")
    if not isinstance(profile, KiCadConstraintProfile):
        raise kicad_cli.KiCadCliError("KiCad constraint profile is malformed")

    board = read_workspace_file(
        settings.workspace,
        requested_path,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    board_path = board.path
    captured_context = kicad_cli._drc_context(board_path, settings, board)
    board_relative = board_path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    original_context_revision = kicad_cli._context_revision(captured_context)
    source = captured_context[board_relative]
    source_revision = kicad_cli._revision(source)

    default_limits = ParseLimits()
    parse_limits = replace(
        default_limits,
        max_input_bytes=min(default_limits.max_input_bytes, settings.max_board_bytes),
    )
    conversion = parse_kicad_bytes(source, profile, parse_limits)
    if conversion.snapshot is None or conversion.diagnostics:
        raise kicad_cli.KiCadCliError(
            "captured KiCad board cannot be represented by the supported Board IR"
        )
    snapshot = conversion.snapshot
    if snapshot.content.source.revision != source_revision:
        raise kicad_cli.KiCadCliError("captured KiCad source revision is inconsistent")
    if candidate.base_revision != snapshot.snapshot_digest:
        raise kicad_cli.KiCadCliError(
            "placement candidate is stale for the captured Board IR snapshot"
        )
    try:
        patched_board = render_kicad_placement_candidate_board(
            source,
            snapshot,
            candidate,
            profile,
            limits=parse_limits,
        )
    except KiCadPlacementPatchError as error:
        raise kicad_cli.KiCadCliError(
            "placement candidate failed replay-verified KiCad serialization"
        ) from error

    patched_context = kicad_cli._candidate_drc_context(
        captured_context,
        board_relative=board_relative,
        patched_board=patched_board,
        settings=settings,
    )
    patched_board_revision = kicad_cli._revision(patched_board)
    patched_drc_context_revision = kicad_cli._context_revision(patched_context)
    del captured_context, conversion, patched_board, snapshot, source

    summary = kicad_cli._run_captured_drc(
        patched_context,
        board_relative=board_relative,
        settings=settings,
    )
    if (
        summary.base_revision != patched_board_revision
        or summary.drc_context_revision != patched_drc_context_revision
    ):
        raise kicad_cli.KiCadCliError("KiCad DRC summary revision binding is inconsistent")
    try:
        recaptured_context_revision = kicad_cli._context_revision(
            kicad_cli._drc_context(board_path, settings)
        )
    except (kicad_cli.KiCadCliError, OSError) as error:
        raise kicad_cli.KiCadCliError(
            "board or DRC rules changed while placement candidate DRC was running; result discarded"
        ) from error
    if recaptured_context_revision != original_context_revision:
        raise kicad_cli.KiCadCliError(
            "board or DRC rules changed while placement candidate DRC was running; result discarded"
        )
    return PlacementCandidateDrcEvidence(
        candidate_id=candidate.candidate_id,
        candidate_base_revision=candidate.base_revision,
        source_revision=source_revision,
        patched_board_revision=patched_board_revision,
        patched_drc_context_revision=patched_drc_context_revision,
        summary=summary,
    )


__all__ = ["PlacementCandidateDrcEvidence", "run_placement_candidate_drc"]
