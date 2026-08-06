"""Bounded exact pairwise clearance checks for negotiated single-layer candidates.

This is intentionally a narrow acceptance gate, not a replacement for a board DRC.  It models
each orthogonal candidate segment as its centreline swept by a closed disc of its half-width.
Twice-nanometre coordinates keep half-widths and squared Euclidean separations integral even when
a track width is odd.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations, pairwise
from typing import Protocol

from copper_mcp.board_ir import BoardIRSnapshot, PointNM
from copper_mcp.routing.contracts import RouteCandidate

_CANCELLATION_CADENCE = 64
_MAX_PAIR_CHECKS = 10_000_000


class CancellationCheck(Protocol):
    """Cooperative cancellation hook shared with the routing boundary."""

    def __call__(self) -> bool: ...


class PhysicalClearanceFailure(StrEnum):
    """Fixed, non-echoing reasons a candidate set cannot be accepted."""

    INVALID_CANDIDATE = "invalid_candidate"
    CLEARANCE_VIOLATION = "clearance_violation"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class PhysicalClearanceVerificationResult:
    """Redacted result of a bounded physical-clearance pass.

    ``violating_nets`` names the first offending pair, and only for a clearance violation.  Net
    IDs are already part of this coordinator's published `unrouted_nets`, so attribution discloses
    nothing new; it exists so a caller can re-route the pair that failed rather than the whole
    allocation.  It is deliberately not geometry: no coordinate, width, or clearance value leaves
    this gate.
    """

    pair_checks: int
    failure: PhysicalClearanceFailure | None = None
    violating_nets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.pair_checks, bool)
            or not isinstance(self.pair_checks, int)
            or not 0 <= self.pair_checks <= _MAX_PAIR_CHECKS
        ):
            raise ValueError("physical-clearance pair checks are outside the supported range")
        if self.failure is not None and not isinstance(self.failure, PhysicalClearanceFailure):
            raise ValueError("physical-clearance failure is malformed")
        if not isinstance(self.violating_nets, tuple) or not all(
            isinstance(item, str) for item in self.violating_nets
        ):
            raise ValueError("physical-clearance attribution is malformed")
        if self.violating_nets and self.failure is not PhysicalClearanceFailure.CLEARANCE_VIOLATION:
            raise ValueError("only a clearance violation attributes a net pair")
        if self.violating_nets and (
            len(self.violating_nets) != 2
            or tuple(sorted(self.violating_nets)) != self.violating_nets
            or len(set(self.violating_nets)) != 2
        ):
            raise ValueError("a clearance violation attributes exactly two sorted distinct nets")

    @property
    def accepted(self) -> bool:
        """Return true when the complete candidate set passed this gate."""

        return self.failure is None

    @property
    def diagnostic(self) -> str | None:
        """Return a stable diagnostic that does not echo board geometry or identifiers."""

        messages = {
            PhysicalClearanceFailure.INVALID_CANDIDATE: (
                "negotiated candidates failed physical-clearance validation"
            ),
            PhysicalClearanceFailure.CLEARANCE_VIOLATION: (
                "negotiated candidates violate pairwise physical clearance"
            ),
            PhysicalClearanceFailure.BUDGET_EXHAUSTED: (
                "the negotiated physical-clearance budget was exhausted"
            ),
            PhysicalClearanceFailure.CANCELLED: (
                "negotiated physical-clearance verification was cancelled"
            ),
        }
        return None if self.failure is None else messages[self.failure]


@dataclass(frozen=True, slots=True)
class _TrackSegment:
    """One axis-aligned centreline segment expressed in doubled nanometres."""

    minimum_x2: int
    maximum_x2: int
    minimum_y2: int
    maximum_y2: int


def _cancelled(cancelled: CancellationCheck | None) -> bool:
    if cancelled is None:
        return False
    try:
        return bool(cancelled())
    except Exception:  # pragma: no cover - untrusted cancellation boundaries fail closed
        return True


def _track_segment(start: PointNM, end: PointNM) -> _TrackSegment:
    return _TrackSegment(
        minimum_x2=2 * min(start.x, end.x),
        maximum_x2=2 * max(start.x, end.x),
        minimum_y2=2 * min(start.y, end.y),
        maximum_y2=2 * max(start.y, end.y),
    )


def _squared_centreline_distance(left: _TrackSegment, right: _TrackSegment) -> int:
    horizontal_gap = max(
        0,
        left.minimum_x2 - right.maximum_x2,
        right.minimum_x2 - left.maximum_x2,
    )
    vertical_gap = max(
        0,
        left.minimum_y2 - right.maximum_y2,
        right.minimum_y2 - left.maximum_y2,
    )
    return horizontal_gap * horizontal_gap + vertical_gap * vertical_gap


def _net_classes(snapshot: BoardIRSnapshot) -> dict[str, tuple[int, int]] | None:
    classes = {item.id: item for item in snapshot.content.constraints.net_classes}
    resolved: dict[str, tuple[int, int]] = {}
    for assignment in snapshot.content.constraints.assignments:
        net_class = classes.get(assignment.net_class_id)
        if net_class is None or assignment.net_id in resolved:
            return None
        resolved[assignment.net_id] = (net_class.clearance_nm, net_class.track_width_nm)
    return resolved


def _candidate_segments(candidate: RouteCandidate) -> tuple[_TrackSegment, ...]:
    return tuple(
        _track_segment(start, end)
        for path in candidate.patch.paths
        for start, end in pairwise(path.vertices)
    )


def verify_negotiated_physical_clearance(
    snapshot: object,
    candidates: object,
    *,
    layer_id: object,
    max_pair_checks: object,
    cancelled: CancellationCheck | None = None,
) -> PhysicalClearanceVerificationResult:
    """Verify pairwise copper spacing for the negotiated one-layer candidate set.

    The required centreline separation is both half widths plus the stricter assigned net-class
    clearance.  Equality is legal; only a strictly smaller Euclidean separation fails.  Vias,
    arcs, zones, pads, custom KiCad rules, multilayer interactions, and fabrication constraints
    are outside this deliberately bounded gate.
    """

    if (
        not isinstance(snapshot, BoardIRSnapshot)
        or not isinstance(candidates, tuple)
        or not all(isinstance(candidate, RouteCandidate) for candidate in candidates)
        or not isinstance(layer_id, str)
        or isinstance(max_pair_checks, bool)
        or not isinstance(max_pair_checks, int)
        or not 0 <= max_pair_checks <= _MAX_PAIR_CHECKS
    ):
        return PhysicalClearanceVerificationResult(
            pair_checks=0, failure=PhysicalClearanceFailure.INVALID_CANDIDATE
        )
    if _cancelled(cancelled):
        return PhysicalClearanceVerificationResult(
            pair_checks=0, failure=PhysicalClearanceFailure.CANCELLED
        )

    net_classes = _net_classes(snapshot)
    if net_classes is None:
        return PhysicalClearanceVerificationResult(
            pair_checks=0, failure=PhysicalClearanceFailure.INVALID_CANDIDATE
        )
    ordered = tuple(sorted(candidates, key=lambda candidate: candidate.patch.net_id))
    if ordered != candidates or len({candidate.patch.net_id for candidate in candidates}) != len(
        candidates
    ):
        return PhysicalClearanceVerificationResult(
            pair_checks=0, failure=PhysicalClearanceFailure.INVALID_CANDIDATE
        )

    segments: dict[str, tuple[_TrackSegment, ...]] = {}
    for candidate in candidates:
        class_dimensions = net_classes.get(candidate.patch.net_id)
        if (
            class_dimensions is None
            or candidate.patch.layer_id != layer_id
            or candidate.patch.width_nm != class_dimensions[1]
        ):
            return PhysicalClearanceVerificationResult(
                pair_checks=0, failure=PhysicalClearanceFailure.INVALID_CANDIDATE
            )
        segments[candidate.patch.net_id] = _candidate_segments(candidate)

    checks = 0
    for left, right in combinations(candidates, 2):
        left_clearance, left_width = net_classes[left.patch.net_id]
        right_clearance, right_width = net_classes[right.patch.net_id]
        required2 = 2 * max(left_clearance, right_clearance) + left_width + right_width
        required_squared = required2 * required2
        for left_segment in segments[left.patch.net_id]:
            for right_segment in segments[right.patch.net_id]:
                if checks >= max_pair_checks:
                    return PhysicalClearanceVerificationResult(
                        pair_checks=checks, failure=PhysicalClearanceFailure.BUDGET_EXHAUSTED
                    )
                if checks % _CANCELLATION_CADENCE == 0 and _cancelled(cancelled):
                    return PhysicalClearanceVerificationResult(
                        pair_checks=checks, failure=PhysicalClearanceFailure.CANCELLED
                    )
                checks += 1
                if _squared_centreline_distance(left_segment, right_segment) < required_squared:
                    return PhysicalClearanceVerificationResult(
                        pair_checks=checks,
                        failure=PhysicalClearanceFailure.CLEARANCE_VIOLATION,
                        violating_nets=tuple(sorted((left.patch.net_id, right.patch.net_id))),
                    )
    return PhysicalClearanceVerificationResult(pair_checks=checks)


__all__ = [
    "PhysicalClearanceFailure",
    "PhysicalClearanceVerificationResult",
    "verify_negotiated_physical_clearance",
]
