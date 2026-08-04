"""Pure, source-preserving application of a verified placement candidate.

The serializer in :mod:`copper_mcp.adapters.kicad_placement_patch` already proves that the
supported footprint subset can be replayed without rewriting unrelated KiCad syntax.  This
module gives that proof the same apply-shaped result vocabulary as route application while
remaining completely filesystem-free.  Authorization, locking, compare-and-swap, backups, and
atomic replacement stay in ``apply.service``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from copper_mcp.adapters import KiCadConstraintProfile
from copper_mcp.adapters.kicad_placement_patch import (
    KiCadPlacementPatchError,
    render_kicad_placement_candidate_board,
)
from copper_mcp.apply.engine import ApplyEngineError, ApplyVerification
from copper_mcp.board_ir import BoardIRSnapshot, ParseLimits
from copper_mcp.placement.contracts import PlacementCandidate, verify_placement_id


@dataclass(frozen=True, slots=True)
class AppliedPlacementBoard:
    """Bytes that may be written and the proof attached to those bytes."""

    content: bytes
    source_revision: str
    result_revision: str
    base_revision: str
    candidate_id: str
    bytes_changed: int
    footprints_moved: int
    verification: ApplyVerification

    def to_dict(self) -> dict[str, object]:
        return {
            "source_revision": self.source_revision,
            "result_revision": self.result_revision,
            "base_revision": self.base_revision,
            "candidate_id": self.candidate_id,
            "bytes_changed": self.bytes_changed,
            "footprints_moved": self.footprints_moved,
            "verification": self.verification.to_dict(),
        }


def _revision(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _changed_bytes(source: bytes, result: bytes) -> int:
    shared = sum(left != right for left, right in zip(source, result, strict=False))
    return shared + abs(len(source) - len(result))


def apply_placement_candidate(
    source: bytes,
    snapshot: BoardIRSnapshot,
    candidate: PlacementCandidate,
    profile: KiCadConstraintProfile,
    *,
    limits: ParseLimits | None = None,
) -> AppliedPlacementBoard:
    """Return the exact board bytes a placement apply would publish.

    Only orthogonal front-side footprints in the existing source-preserving serializer's
    bounded subset are admitted.  A candidate with no changed pose is refused rather than
    consuming an apply capability for a no-op.
    """

    if not isinstance(source, bytes):
        raise ApplyEngineError("board source must be immutable bytes")
    if not isinstance(snapshot, BoardIRSnapshot):
        raise ApplyEngineError("board snapshot is malformed")
    if not isinstance(candidate, PlacementCandidate):
        raise ApplyEngineError("placement candidate is malformed")
    if not isinstance(profile, KiCadConstraintProfile):
        raise ApplyEngineError("KiCad constraint profile is malformed")
    if limits is not None and not isinstance(limits, ParseLimits):
        raise ApplyEngineError("parse limits are malformed")
    moved = sum(item.moved for item in candidate.placements)
    if moved < 1:
        raise ApplyEngineError("placement candidate carries no pose changes")
    try:
        verify_placement_id(candidate)
        result = render_kicad_placement_candidate_board(
            source, snapshot, candidate, profile, limits=limits
        )
    except (ValueError, KiCadPlacementPatchError) as error:
        if isinstance(error, ApplyEngineError):
            raise
        raise ApplyEngineError(str(error)) from error
    if result == source:
        raise ApplyEngineError("placement serializer produced no board change")
    source_revision = _revision(source)
    if candidate.view_revision != source_revision:
        raise ApplyEngineError("placement candidate is stale for the supplied board bytes")
    return AppliedPlacementBoard(
        content=result,
        source_revision=source_revision,
        result_revision=_revision(result),
        base_revision=candidate.base_revision,
        candidate_id=candidate.candidate_id,
        bytes_changed=_changed_bytes(source, result),
        footprints_moved=moved,
        verification=ApplyVerification(),
    )


__all__ = ["AppliedPlacementBoard", "apply_placement_candidate"]
