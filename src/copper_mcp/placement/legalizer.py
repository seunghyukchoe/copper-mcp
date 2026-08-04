"""Deterministic placement legalization: AI proposes, this code disposes.

v0.1 **validates and snaps** model proposals rather than solving for positions. The reasons are
recorded in ADR-0024, but the short one is that a legalizer is needed under either
architecture, and a solver written before a trustworthy legalizer has nothing to check itself
against.

Nothing here mutates a board. A candidate is a proposal bound to the exact board it was derived
from, by both digests, and applying one is a separate operation that does not yet exist.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from copper_mcp.board_ir import BoardIRSnapshot, Keepout, Pad, PointNM, Ring
from copper_mcp.placement.contracts import (
    EMPTY_DIGEST,
    AlignmentRule,
    EdgeRule,
    FootprintPlacement,
    OrientationRule,
    PlacementCandidate,
    PlacementDiagnostic,
    PlacementError,
    PlacementEvidence,
    PlacementFailureCode,
    PlacementIntent,
    PlacementLegality,
    PlacementResult,
    PlacementRule,
    ProximityRule,
    RegionRule,
    RuleResult,
    SideRule,
    SymmetryRule,
    finalise_candidate,
)
from copper_mcp.placement.geometry import (
    QUARTER_UDEG,
    Rect,
    pad_bounds,
    pad_core,
    rect_gap,
    rect_inside_ring,
    rect_touches_ring,
    rects_overlap,
    ring_bounds,
    rotate_offset,
    union,
)
from copper_mcp.placement.view import PlacementView


class _BudgetExhaustedError(RuntimeError):
    """Raised when the work ceiling is reached before a verdict."""


class _InfeasibleError(RuntimeError):
    """Raised when the rules are provably unsatisfiable as written."""


class _UnresolvedError(RuntimeError):
    """Raised when a rule names something the board does not contain."""


def _reject_padless(view: PlacementView, ref: str) -> None:
    """Refuse a reference that names a real footprint carrying no copper pad.

    Board IR v0.2 keeps graphics-only footprints and the scene reports them, so telling a
    caller such a reference "does not exist" would be a false statement about their board. The
    honest answer is that it exists and this version cannot place it.
    """

    if view.is_padless(ref):
        raise _UnsupportedError(
            "a placement subject owns no copper pad, so it cannot be placed in v0.1"
        )


class _UnsupportedError(RuntimeError):
    """Raised when the geometry is outside what this version models."""


@dataclass(slots=True)
class _Budget:
    max_checks: int
    deadline_seconds: float
    used: int = 0
    _started: float = field(default_factory=time.monotonic)

    def charge(self, amount: int = 1) -> None:
        self.used += amount
        if self.used > self.max_checks:
            raise _BudgetExhaustedError("placement check ceiling reached")
        if time.monotonic() - self._started > self.deadline_seconds:
            raise _BudgetExhaustedError("placement deadline reached")


@dataclass(frozen=True, slots=True)
class _PlacedPad:
    pad: Pad
    centre: PointNM
    rotation_udeg: int
    bounds: Rect
    core: Rect | None


@dataclass(frozen=True, slots=True)
class _PlacedFootprint:
    ref_id: str
    origin: PointNM
    orientation_udeg: int
    side: str
    moved: bool
    pads: tuple[_PlacedPad, ...]
    hull: Rect


def snap(value: int, grid_nm: int) -> int:
    """Snap to the placement grid deterministically, including for negatives.

    Floor division rounds half away from negative infinity rather than toward zero, so the
    result is a pure function of the input with no sign-dependent special case.
    """

    return ((value + grid_nm // 2) // grid_nm) * grid_nm


def _inverse_rotate(offset: PointNM, orientation_udeg: int) -> PointNM:
    return rotate_offset(offset, (-orientation_udeg) % 360_000_000)


def _place(
    view: PlacementView,
    snapshot: BoardIRSnapshot,
    intent: PlacementIntent,
    budget: _Budget,
) -> tuple[_PlacedFootprint, ...]:
    """Resolve every proposal into an absolute pose, in reference order.

    Reference order is the same deterministic convention the router uses for component
    merging: sort by identifier, lowest wins. It is recorded as ``validate-snap-v1`` so a
    later solver cannot be mistaken for this resolution.
    """

    pads_by_id = {pad.id: pad for pad in snapshot.content.pads}
    proposals = {proposal.subject: proposal for proposal in intent.proposals}
    for ref in (*intent.subject_refs, *proposals):
        if view.resolve(ref) is None:
            _reject_padless(view, ref)
            raise _UnresolvedError("a placement subject does not exist on this board")

    placed: list[_PlacedFootprint] = []
    for ref_id in sorted(view.footprints):
        budget.charge()
        footprint = view.footprints[ref_id]
        proposal = proposals.get(ref_id)
        origin, orientation, side = footprint.origin, footprint.orientation_udeg, footprint.side
        moved = False
        if proposal is not None:
            if footprint.locked:
                raise _UnsupportedError("moving a locked footprint is not authorized")
            anchor = footprint if proposal.anchor is None else view.resolve(proposal.anchor)
            if anchor is None:
                _reject_padless(view, proposal.anchor or "")
                raise _UnresolvedError("a proposal anchors to an object that does not exist")
            base = _anchor_point(anchor.hull, proposal.anchor_point)
            origin = PointNM(
                snap(base.x + proposal.offset_x_nm, intent.placement_grid_nm),
                snap(base.y + proposal.offset_y_nm, intent.placement_grid_nm),
            )
            if proposal.orientation_udeg is not None:
                orientation = proposal.orientation_udeg
            if proposal.side is not None and proposal.side != side:
                # A side change mirrors the footprint, which is a different transform from the
                # rigid moves this version models. Refusing is honest; a half-correct mirror
                # would silently produce copper on the wrong side of the board.
                raise _UnsupportedError("changing a footprint's side is not modelled in v0.1")
            moved = True

        if orientation % QUARTER_UDEG != 0:
            raise _UnsupportedError("placement supports orthogonal orientations only")

        pads: list[_PlacedPad] = []
        hull: Rect | None = None
        for pad_id in footprint.pad_ids:
            budget.charge()
            pad = pads_by_id[pad_id]
            local = _inverse_rotate(
                PointNM(
                    pad.center.x - footprint.origin.x,
                    pad.center.y - footprint.origin.y,
                ),
                footprint.orientation_udeg,
            )
            turned = rotate_offset(local, orientation)
            centre = PointNM(origin.x + turned.x, origin.y + turned.y)
            delta = (orientation - footprint.orientation_udeg) % 360_000_000
            rotation = (pad.rotation_udeg + delta) % 360_000_000
            # The pad keeps its shape; only which axis it spans can change, so re-derive its
            # box from the turned angle rather than transforming the old box.
            spun = Pad(
                id=pad.id,
                net_id=pad.net_id,
                center=centre,
                rotation_udeg=rotation,
                shape=pad.shape,
                kind=pad.kind,
                size_x_nm=pad.size_x_nm,
                size_y_nm=pad.size_y_nm,
                roundrect_radius_nm=pad.roundrect_radius_nm,
                drill_x_nm=pad.drill_x_nm,
                drill_y_nm=pad.drill_y_nm,
                layer_ids=pad.layer_ids,
                locked=pad.locked,
            )
            bounds = pad_bounds(spun)
            pads.append(
                _PlacedPad(
                    pad=spun,
                    centre=centre,
                    rotation_udeg=rotation,
                    bounds=bounds,
                    core=pad_core(spun),
                )
            )
            hull = bounds if hull is None else union(hull, bounds)
        assert hull is not None  # a view never keeps a footprint without pads
        placed.append(
            _PlacedFootprint(
                ref_id=ref_id,
                origin=origin,
                orientation_udeg=orientation,
                side=side,
                moved=moved,
                pads=tuple(pads),
                hull=hull,
            )
        )
    return tuple(placed)


def _anchor_point(hull: Rect, anchor_point: str) -> PointNM:
    centre_x = (hull[0] + hull[2]) // 2
    centre_y = (hull[1] + hull[3]) // 2
    return {
        "center": PointNM(centre_x, centre_y),
        "north": PointNM(centre_x, hull[1]),
        "south": PointNM(centre_x, hull[3]),
        "west": PointNM(hull[0], centre_y),
        "east": PointNM(hull[2], centre_y),
    }[anchor_point]


# --- legality ---------------------------------------------------------------------------


def _pad_overlap(placed: tuple[_PlacedFootprint, ...], budget: _Budget) -> tuple[str, int]:
    """Three-valued overlap over every different-net pad pair on a shared layer.

    Bounds over-approximate and cores under-approximate, so:

    * disjoint bounds prove the pads are clear;
    * overlapping cores prove the pads collide;
    * anything between is **inconclusive**, and says so rather than guessing.

    Reporting inconclusive as a violation would reject legal boards - measured on this
    repository's own board, axis-aligned boxes clip on pads KiCad calls clean - and reporting
    it as clear would claim a proof nobody has.
    """

    verdict = "proven_clear"
    inconclusive = 0
    for first_index, first in enumerate(placed):
        for second in placed[first_index + 1 :]:
            budget.charge()
            if not rects_overlap(first.hull, second.hull):
                continue
            for left in first.pads:
                for right in second.pads:
                    budget.charge()
                    if not set(left.pad.layer_ids) & set(right.pad.layer_ids):
                        continue
                    if left.pad.net_id is not None and left.pad.net_id == right.pad.net_id:
                        continue
                    if not rects_overlap(left.bounds, right.bounds):
                        continue
                    if (
                        left.core is not None
                        and right.core is not None
                        and rects_overlap(left.core, right.core)
                    ):
                        return "violated", inconclusive
                    inconclusive += 1
                    verdict = "inconclusive"
    return verdict, inconclusive


def _outline_containment(
    placed: tuple[_PlacedFootprint, ...], snapshot: BoardIRSnapshot, budget: _Budget
) -> str:
    contours = snapshot.content.outline
    if not contours:
        raise _UnsupportedError("a board with no outline cannot bound a placement")
    for footprint in placed:
        for pad in footprint.pads:
            budget.charge()
            if not any(rect_inside_ring(pad.bounds, contour.outer) for contour in contours):
                return "violated"
            for contour in contours:
                for hole in contour.holes:
                    budget.charge()
                    if rect_touches_ring(pad.bounds, hole):
                        return "violated"
    return "proven_inside"


def _keepout_respect(
    placed: tuple[_PlacedFootprint, ...], snapshot: BoardIRSnapshot, budget: _Budget
) -> str:
    keepouts: tuple[Keepout, ...] = tuple(
        item for item in snapshot.content.keepouts if item.prohibit_footprints
    )
    if not keepouts:
        return "proven_clear"
    for footprint in placed:
        for pad in footprint.pads:
            for keepout in keepouts:
                budget.charge()
                if not set(pad.pad.layer_ids) & set(keepout.layer_ids):
                    continue
                if rect_touches_ring(pad.bounds, keepout.boundary):
                    return "violated"
    return "proven_clear"


# --- rules ------------------------------------------------------------------------------


def _resolve_bounds(
    ref: str, placed_by_ref: dict[str, _PlacedFootprint], view: PlacementView
) -> Rect:
    footprint = placed_by_ref.get(ref)
    if footprint is not None:
        return footprint.hull
    owner = view.owner_by_pad.get(ref)
    if owner is not None:
        parent = placed_by_ref.get(owner)
        if parent is not None:
            for pad in parent.pads:
                if pad.pad.id == ref:
                    return pad.bounds
    _reject_padless(view, ref)
    raise _UnresolvedError("a rule names an object that does not exist on this board")


def _centre(rect: Rect) -> tuple[int, int]:
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


def _status(residual: int, tolerance: int | None) -> str:
    if residual == 0:
        return "satisfied_exactly"
    if tolerance is not None and residual <= tolerance:
        return "satisfied_within_tolerance"
    return "violated"


def _evaluate_rule(
    index: int,
    rule: PlacementRule,
    placed_by_ref: dict[str, _PlacedFootprint],
    view: PlacementView,
    snapshot: BoardIRSnapshot,
    budget: _Budget,
) -> RuleResult:
    budget.charge()
    axis_index = 0
    residual = 0

    if isinstance(rule, ProximityRule):
        gap = rect_gap(
            _resolve_bounds(rule.subject, placed_by_ref, view),
            _resolve_bounds(rule.target, placed_by_ref, view),
        )
        residual = max(0, gap - rule.max_distance_nm)
    elif isinstance(rule, AlignmentRule):
        axis_index = 0 if rule.axis == "x" else 1
        values = [
            _centre(_resolve_bounds(member, placed_by_ref, view))[axis_index]
            for member in rule.members
        ]
        residual = max(values) - min(values)
    elif isinstance(rule, SymmetryRule):
        axis_index = 0 if rule.axis == "x" else 1
        mirror = _centre(_resolve_bounds(rule.about, placed_by_ref, view))[axis_index]
        worst = 0
        for left, right in rule.pairs:
            budget.charge()
            left_value = _centre(_resolve_bounds(left, placed_by_ref, view))[axis_index]
            right_value = _centre(_resolve_bounds(right, placed_by_ref, view))[axis_index]
            worst = max(worst, abs((left_value + right_value) - 2 * mirror))
        # Doubling the mirror keeps this exact: comparing midpoints directly would need a
        # division that is not always integral.
        residual = worst
    elif isinstance(rule, EdgeRule):
        board = _board_bounds(snapshot)
        bounds = _resolve_bounds(rule.subject, placed_by_ref, view)
        actual = {
            "west": bounds[0] - board[0],
            "north": bounds[1] - board[1],
            "east": board[2] - bounds[2],
            "south": board[3] - bounds[3],
        }[rule.edge]
        residual = abs(actual - rule.offset_nm)
    elif isinstance(rule, RegionRule):
        boundary = _boundary_ring(rule.boundary_ref, snapshot)
        bounds = _resolve_bounds(rule.subject, placed_by_ref, view)
        if rule.mode == "keep_in":
            residual = 0 if rect_inside_ring(bounds, boundary) else 1
        else:
            residual = 1 if rect_touches_ring(bounds, boundary) else 0
    elif isinstance(rule, OrientationRule):
        footprint = placed_by_ref.get(rule.subject)
        if footprint is None:
            _reject_padless(view, rule.subject)
            raise _UnresolvedError("an orientation rule names an object that is not a footprint")
        residual = 0 if footprint.orientation_udeg in rule.allowed else 1
    elif isinstance(rule, SideRule):
        footprint = placed_by_ref.get(rule.subject)
        if footprint is None:
            _reject_padless(view, rule.subject)
            raise _UnresolvedError("a side rule names an object that is not a footprint")
        residual = 0 if footprint.side == rule.side else 1
    else:  # pragma: no cover - the union is exhaustive
        raise PlacementError("unsupported placement rule")

    return RuleResult(
        rule_index=index,
        kind=rule.kind,
        status=_status(residual, rule.tolerance_nm),
        residual_nm=residual,
    )


def _board_bounds(snapshot: BoardIRSnapshot) -> Rect:
    contours = snapshot.content.outline
    if not contours:
        raise _UnsupportedError("a board with no outline has no edges to measure from")
    bounds = ring_bounds(contours[0].outer)
    for contour in contours[1:]:
        bounds = union(bounds, ring_bounds(contour.outer))
    return bounds


def _boundary_ring(ref: str, snapshot: BoardIRSnapshot) -> Ring:
    for keepout in snapshot.content.keepouts:
        if keepout.id == ref:
            return keepout.boundary
    for contour in snapshot.content.outline:
        if contour.id == ref:
            return contour.outer
    raise _UnresolvedError("a region rule names a boundary that does not exist on this board")


def _check_infeasible(intent: PlacementIntent) -> None:
    """Reject rule sets that provably contradict themselves, before any geometry runs.

    Only *syntactic* contradictions are claimed here - two rules that cannot both hold no
    matter where anything is placed. Anything requiring search stays out, because reporting
    "no solution" when the truth is "I did not look" is exactly the confusion between
    infeasibility and budget exhaustion this contract refuses to make.
    """

    sides: dict[str, str] = {}
    orientations: dict[str, set[int]] = {}
    edges: dict[str, set[str]] = {}
    for rule in intent.rules:
        if isinstance(rule, SideRule):
            previous = sides.setdefault(rule.subject, rule.side)
            if previous != rule.side:
                raise _InfeasibleError("two side rules require the same object on both sides")
        elif isinstance(rule, OrientationRule):
            allowed = orientations.setdefault(rule.subject, set(rule.allowed))
            allowed &= set(rule.allowed)
            orientations[rule.subject] = allowed
            if not allowed:
                raise _InfeasibleError("orientation rules leave no permitted orientation")
        elif isinstance(rule, EdgeRule):
            seen = edges.setdefault(rule.subject, set())
            opposite = {"north": "south", "south": "north", "east": "west", "west": "east"}
            if opposite[rule.edge] in seen:
                # Two opposing edge offsets fix the object's size, which a placement may not
                # change, so no placement can satisfy both.
                raise _InfeasibleError("edge rules pin one object against two opposite edges")
            seen.add(rule.edge)


def evaluate_placement(
    intent: PlacementIntent,
    snapshot: BoardIRSnapshot,
    view: PlacementView,
    *,
    max_checks: int = 2_000_000,
    deadline_seconds: float = 10.0,
    board_path: str = "",
) -> PlacementResult:
    """Validate one placement proposal and return a candidate or a typed refusal."""

    board_revision = view.board_revision
    if view.snapshot_digest != snapshot.snapshot_digest:
        return _refuse(
            board_revision,
            snapshot,
            PlacementFailureCode.STALE_REVISION,
            "the placement view and the board snapshot describe different boards",
            intent=intent,
            board_path=board_path,
        )
    budget = _Budget(max_checks=max_checks, deadline_seconds=deadline_seconds)
    try:
        _check_infeasible(intent)
        placed = _place(view, snapshot, intent, budget)
        placed_by_ref = {item.ref_id: item for item in placed}
        rule_results = tuple(
            _evaluate_rule(index, rule, placed_by_ref, view, snapshot, budget)
            for index, rule in enumerate(intent.rules)
        )
        overlap, inconclusive = _pad_overlap(placed, budget)
        legality = PlacementLegality(
            pad_overlap=overlap,
            outline_containment=_outline_containment(placed, snapshot, budget),
            keepout_respect=_keepout_respect(placed, snapshot, budget),
        )
    except _BudgetExhaustedError as error:
        return _refuse(
            board_revision,
            snapshot,
            PlacementFailureCode.BUDGET_EXHAUSTED,
            str(error),
            budget,
            intent=intent,
            board_path=board_path,
        )
    except _InfeasibleError as error:
        return _refuse(
            board_revision,
            snapshot,
            PlacementFailureCode.INFEASIBLE_CONSTRAINTS,
            str(error),
            budget,
            intent=intent,
            board_path=board_path,
        )
    except _UnresolvedError as error:
        return _refuse(
            board_revision,
            snapshot,
            PlacementFailureCode.UNRESOLVED_REF,
            str(error),
            budget,
            intent=intent,
            board_path=board_path,
        )
    except _UnsupportedError as error:
        return _refuse(
            board_revision,
            snapshot,
            PlacementFailureCode.UNSUPPORTED_GEOMETRY,
            str(error),
            budget,
            intent=intent,
            board_path=board_path,
        )

    if not legality.legal:
        return _refuse(
            board_revision,
            snapshot,
            PlacementFailureCode.ILLEGAL_PLACEMENT,
            "the proposed placement is provably illegal",
            budget,
            legality=legality,
            rule_results=rule_results,
            intent=intent,
            board_path=board_path,
        )

    candidate = finalise_candidate(
        PlacementCandidate(
            candidate_id=EMPTY_DIGEST,
            base_revision=snapshot.snapshot_digest,
            view_revision=board_revision,
            placements=tuple(
                FootprintPlacement(
                    ref_id=item.ref_id,
                    origin_x_nm=item.origin.x,
                    origin_y_nm=item.origin.y,
                    orientation_udeg=item.orientation_udeg,
                    side=item.side,
                    moved=item.moved,
                )
                for item in placed
            ),
            evidence=PlacementEvidence(
                rule_results=rule_results,
                legality=legality,
                checks_used=budget.used,
                inconclusive_pairs=inconclusive,
            ),
            placement_grid_nm=intent.placement_grid_nm,
        )
    )
    return PlacementResult(
        status="previewed",
        board_revision=board_revision,
        board_path=board_path,
        request=intent,
        snapshot_digest=snapshot.snapshot_digest,
        candidate=candidate,
    )


def _refuse(
    board_revision: str,
    snapshot: BoardIRSnapshot,
    code: PlacementFailureCode,
    message: str,
    budget: _Budget | None = None,
    *,
    legality: PlacementLegality | None = None,
    rule_results: tuple[RuleResult, ...] = (),
    intent: PlacementIntent | None = None,
    board_path: str = "",
) -> PlacementResult:
    return PlacementResult(
        status="refused",
        board_revision=board_revision,
        board_path=board_path,
        request=intent,
        snapshot_digest=snapshot.snapshot_digest,
        diagnostic=PlacementDiagnostic(
            code=code,
            message=message,
            checks_used=0 if budget is None else budget.used,
            legality=legality,
            rule_results=rule_results,
        ),
    )


__all__ = ["evaluate_placement", "snap"]
