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

from copper_mcp.board_ir import BoardIRSnapshot, CourtyardCircle, Keepout, Pad, PointNM, Ring
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
    courtyard_region_overlap,
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


def _reject_padless_preflight_refs(view: PlacementView, intent: PlacementIntent) -> None:
    """Reject unsupported placement references before syntactic analysis.

    Padless footprints are deliberately unavailable as placement subjects, anchors, and rule
    references. Resolve subjects in request order before the rule-reference and proposal-anchor
    scans. Each scan stops at its first unknown or padless reference, so a later unsupported
    reference cannot overwrite an earlier ``unresolved_ref`` outcome. This keeps a
    contradictory rule set from hiding an unsupported placement reference behind an
    ``infeasible_constraints`` diagnostic.
    """

    for ref in intent.subject_refs:
        if view.resolve(ref) is None:
            _reject_padless(view, ref)
            raise _UnresolvedError("a placement subject does not exist on this board")

    rule_refs: list[str] = []
    for rule in intent.rules:
        if isinstance(rule, ProximityRule):
            rule_refs.extend((rule.subject, rule.target))
        elif isinstance(rule, EdgeRule | RegionRule | OrientationRule | SideRule):
            rule_refs.append(rule.subject)
        elif isinstance(rule, AlignmentRule):
            rule_refs.extend(rule.members)
        elif isinstance(rule, SymmetryRule):
            rule_refs.append(rule.about)
            rule_refs.extend(ref for pair in rule.pairs for ref in pair)
    for ref in rule_refs:
        # Rules may name an individual pad, which ``view.resolve`` intentionally does not
        # resolve because it only returns placeable footprints.
        if view.resolve(ref) is not None or ref in view.owner_by_pad:
            continue
        _reject_padless(view, ref)
        raise _UnresolvedError("a rule names an object that does not exist on this board")

    for proposal in intent.proposals:
        if proposal.anchor is None or view.resolve(proposal.anchor) is not None:
            continue
        _reject_padless(view, proposal.anchor)
        raise _UnresolvedError("a proposal anchors to an object that does not exist")


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
    #: Courtyard geometry on the layer matching ``side``.
    courtyards: tuple[Ring, ...]
    courtyard_circles: tuple[CourtyardCircle, ...]
    #: Courtyard geometry on the layer opposite ``side`` (ADR-0097).
    far_side_courtyards: tuple[Ring, ...] = ()
    far_side_courtyard_circles: tuple[CourtyardCircle, ...] = ()

    def on_layer(self, front: bool) -> tuple[tuple[Ring, ...], tuple[CourtyardCircle, ...]]:
        """Return the rings and circles this footprint draws on ``F.CrtYd`` or ``B.CrtYd``.

        The footprint's own side selects which of the two stored sets is the named layer; it
        never decides *whether* the footprint occupies that layer. That is KiCad's model:
        ``FOOTPRINT::BuildCourtyardCaches`` files each shape by the shape's own layer and the
        courtyard DRC provider compares front against front and back against back, consulting
        neither footprint's side (``pcbnew/footprint.cpp``,
        ``pcbnew/drc/drc_test_provider_courtyard_clearance.cpp``, KiCad 10.0.5).
        """

        if (self.side == "front") == front:
            return self.courtyards, self.courtyard_circles
        return self.far_side_courtyards, self.far_side_courtyard_circles


def snap(value: int, grid_nm: int) -> int:
    """Snap to the placement grid deterministically, including for negatives.

    Floor division rounds half away from negative infinity rather than toward zero, so the
    result is a pure function of the input with no sign-dependent special case.
    """

    return ((value + grid_nm // 2) // grid_nm) * grid_nm


def _inverse_rotate(offset: PointNM, orientation_udeg: int) -> PointNM:
    return rotate_offset(offset, (-orientation_udeg) % 360_000_000)


def _place_ring(
    ring: Ring,
    original_origin: PointNM,
    original_orientation_udeg: int,
    new_origin: PointNM,
    new_orientation_udeg: int,
) -> Ring:
    """Move one Board IR courtyard from its saved pose to a proposed orthogonal pose."""

    points = []
    for point in ring.points:
        saved_local = _inverse_rotate(
            PointNM(point.x - original_origin.x, point.y - original_origin.y),
            original_orientation_udeg,
        )
        turned = rotate_offset(saved_local, new_orientation_udeg)
        points.append(PointNM(new_origin.x + turned.x, new_origin.y + turned.y))
    return Ring(tuple(points))


def _place_circle(
    circle: CourtyardCircle,
    original_origin: PointNM,
    original_orientation_udeg: int,
    new_origin: PointNM,
    new_orientation_udeg: int,
) -> CourtyardCircle:
    """Move one circular courtyard between orthogonal poses; the radius is invariant."""

    saved_local = _inverse_rotate(
        PointNM(circle.center.x - original_origin.x, circle.center.y - original_origin.y),
        original_orientation_udeg,
    )
    turned = rotate_offset(saved_local, new_orientation_udeg)
    return CourtyardCircle(
        center=PointNM(new_origin.x + turned.x, new_origin.y + turned.y),
        radius_nm=circle.radius_nm,
    )


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
        if footprint.orientation_udeg % QUARTER_UDEG != 0:
            # The saved pose is the frame every pad offset and courtyard point is un-rotated out
            # of below, so an orthogonal *proposal* does not make a non-orthogonal *source*
            # placeable. Checking only the resulting orientation let an orthogonal proposal mask
            # the stored angle, and the un-rotation then escaped this boundary as a bare
            # ValueError from ``rotate_offset`` instead of a typed refusal.
            raise _UnsupportedError("a placement subject's saved pose is not orthogonal")

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
                copper_envelope=pad.copper_envelope,
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
        courtyards = tuple(
            _place_ring(
                ring,
                footprint.origin,
                footprint.orientation_udeg,
                origin,
                orientation,
            )
            for ring in footprint.courtyards
        )
        courtyard_circles = tuple(
            _place_circle(
                circle,
                footprint.origin,
                footprint.orientation_udeg,
                origin,
                orientation,
            )
            for circle in footprint.courtyard_circles
        )
        # The far-side courtyard is part of the same rigid body and moves with it. A quarter turn
        # about the footprint origin is the same map on either courtyard layer, because the pose
        # change this version admits is a translation plus an orthogonal rotation and never a
        # flip -- a proposal naming a different `side` is refused above as unmodelled, so no
        # mirror is ever required here and the two layers never trade contents.
        far_side_courtyards = tuple(
            _place_ring(
                ring,
                footprint.origin,
                footprint.orientation_udeg,
                origin,
                orientation,
            )
            for ring in footprint.far_side_courtyards
        )
        far_side_courtyard_circles = tuple(
            _place_circle(
                circle,
                footprint.origin,
                footprint.orientation_udeg,
                origin,
                orientation,
            )
            for circle in footprint.far_side_courtyard_circles
        )
        placed.append(
            _PlacedFootprint(
                ref_id=ref_id,
                origin=origin,
                orientation_udeg=orientation,
                side=side,
                moved=moved,
                pads=tuple(pads),
                hull=hull,
                courtyards=courtyards,
                courtyard_circles=courtyard_circles,
                far_side_courtyards=far_side_courtyards,
                far_side_courtyard_circles=far_side_courtyard_circles,
            )
        )
    # Graphics-only footprints cannot be moved or used as rule references, but a rectangular
    # courtyard on one is still physical board geometry. Include it as a fixed collision envelope
    # while keeping it out of the candidate and rule-resolution maps below.
    for ref_id in sorted(view.stationary):
        budget.charge()
        stationary_footprint = view.stationary[ref_id]
        stationary_hull: Rect | None = None
        for ring in (
            *stationary_footprint.courtyards,
            *stationary_footprint.far_side_courtyards,
        ):
            bounds = ring_bounds(ring)
            stationary_hull = bounds if stationary_hull is None else union(stationary_hull, bounds)
        for circle in (
            *stationary_footprint.courtyard_circles,
            *stationary_footprint.far_side_courtyard_circles,
        ):
            bounds = (
                circle.center.x - circle.radius_nm,
                circle.center.y - circle.radius_nm,
                circle.center.x + circle.radius_nm,
                circle.center.y + circle.radius_nm,
            )
            stationary_hull = bounds if stationary_hull is None else union(stationary_hull, bounds)
        assert stationary_hull is not None
        placed.append(
            _PlacedFootprint(
                ref_id=ref_id,
                origin=stationary_footprint.origin,
                orientation_udeg=stationary_footprint.orientation_udeg,
                side=stationary_footprint.side,
                moved=False,
                pads=(),
                hull=stationary_hull,
                courtyards=stationary_footprint.courtyards,
                courtyard_circles=stationary_footprint.courtyard_circles,
                far_side_courtyards=stationary_footprint.far_side_courtyards,
                far_side_courtyard_circles=stationary_footprint.far_side_courtyard_circles,
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
    """Bracket KiCad edge collisions without inventing global containment semantics.

    Bounds entirely inside an outer ring prove the pad is inside.  An under-approximating core
    crossing an outer or hole boundary proves real copper crosses an edge.  The gap includes both
    rounded copper whose box clips an edge and copper wholly remote from every edge; KiCad 10.0.5's
    edge-clearance provider reports neither as a global containment failure, so both are disclosed
    as ``inconclusive`` rather than collapsed into a parity claim.
    """

    contours = snapshot.content.outline
    if not contours:
        raise _UnsupportedError("a board with no outline cannot bound a placement")
    inconclusive = False
    for footprint in placed:
        for pad in footprint.pads:
            budget.charge()
            bounds_inside = any(rect_inside_ring(pad.bounds, contour.outer) for contour in contours)
            core_inside = pad.core is not None and any(
                rect_inside_ring(pad.core, contour.outer) for contour in contours
            )
            if not bounds_inside:
                core_crosses_edge = (
                    pad.core is not None
                    and not core_inside
                    and any(rect_touches_ring(pad.core, contour.outer) for contour in contours)
                )
                if core_crosses_edge:
                    return "violated"
                inconclusive = True
            for contour in contours:
                for hole in contour.holes:
                    budget.charge()
                    if pad.core is not None and rect_touches_ring(pad.core, hole):
                        if not rect_inside_ring(pad.core, hole):
                            return "violated"
                    if rect_touches_ring(pad.bounds, hole):
                        inconclusive = True
    return "inconclusive" if inconclusive else "proven_inside"


def _keepout_respect(
    placed: tuple[_PlacedFootprint, ...], snapshot: BoardIRSnapshot, budget: _Budget
) -> str:
    """Bracket footprint-keepout contact with direction-typed pad geometry.

    Disjoint over-approximating bounds prove clearance; contact by an under-approximating core
    proves intrusion.  Bounds-only contact is neither proof and remains ``inconclusive``.
    """

    keepouts: tuple[Keepout, ...] = tuple(
        item for item in snapshot.content.keepouts if item.prohibit_footprints
    )
    if not keepouts:
        return "proven_clear"
    inconclusive = False
    for footprint in placed:
        for pad in footprint.pads:
            for keepout in keepouts:
                budget.charge()
                if not set(pad.pad.layer_ids) & set(keepout.layer_ids):
                    continue
                if not rect_touches_ring(pad.bounds, keepout.boundary):
                    continue
                if pad.core is not None and rect_touches_ring(pad.core, keepout.boundary):
                    return "violated"
                inconclusive = True
    return "inconclusive" if inconclusive else "proven_clear"


def _courtyard_overlap(placed: tuple[_PlacedFootprint, ...], budget: _Budget) -> str:
    """Check per-layer courtyards - rings, chamfers, and circles - against KiCad 10.0.5's model.

    Board IR v0.2 accepts simple closed octilinear courtyard rings and exact circular
    courtyards.  A footprint's contours are one even-odd region rather than a set of
    independent solids, matching the contour hierarchy KiCad builds, and the region is
    contracted by the cached-courtyard inset before the collision test.
    See :func:`courtyard_region_overlap` for why the answer is three-valued, which bound is
    allowed to prove which claim, and why anything else is ``inconclusive``.

    **The pairing is by courtyard layer, not by footprint side** (ADR-0097).  ``F.CrtYd`` and
    ``B.CrtYd`` are independent physical layers, so cross-layer contact is still not a
    collision - but a footprint may draw on the layer opposite its own side, and when it does,
    that geometry keeps out on the layer it is drawn on.  Real ``kicad-cli`` 10.0.5 reports
    ``courtyards_overlap`` for two coincident ``B.CrtYd`` rectangles whether their footprints
    sit on the front, the back, or one of each, and reports nothing for a ``B.CrtYd`` against
    an ``F.CrtYd``.  Gating on the footprint's side instead would have published
    ``proven_clear`` for the first case - a keep-out silently dropped, in the one direction an
    obstacle may never err.

    For a board on which every courtyard matches its footprint's side, this enumerates exactly
    the pairs the previous same-side gate enumerated: the opposite-layer set of every footprint
    is empty, so its comparison is skipped, and the matching-layer comparison happens only
    between two footprints on the same side.
    """

    verdict = "proven_clear"
    for first_index, first in enumerate(placed):
        if not (
            first.courtyards
            or first.courtyard_circles
            or first.far_side_courtyards
            or first.far_side_courtyard_circles
        ):
            # Unchanged from the same-side gate: a footprint with no courtyard on either layer
            # is skipped before it charges the pair budget, so a board of courtyard-less
            # footprints consumes exactly the checks it consumed before.
            continue
        for second in placed[first_index + 1 :]:
            budget.charge()
            for front in (True, False):
                first_rings, first_circles = first.on_layer(front)
                if not first_rings and not first_circles:
                    continue
                second_rings, second_circles = second.on_layer(front)
                if not second_rings and not second_circles:
                    continue
                outcome = courtyard_region_overlap(
                    first_rings,
                    second_rings,
                    first_circles=first_circles,
                    second_circles=second_circles,
                    charge=budget.charge,
                )
                if outcome == "violated":
                    return "violated"
                if outcome == "inconclusive":
                    verdict = "inconclusive"
    return verdict


# --- rules ------------------------------------------------------------------------------


def _resolve_bounds(
    ref: str, placed_by_ref: dict[str, _PlacedFootprint], view: PlacementView
) -> Rect:
    """Resolve the over-approximating region used for clearance and touch requirements."""

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


def _resolve_core(
    ref: str, placed_by_ref: dict[str, _PlacedFootprint], view: PlacementView
) -> Rect:
    """Resolve the under-approximating region used for pad containment requirements.

    Footprint rules retain their existing hull semantics: a footprint is the rule subject, not a
    copper primitive with a separately modelled attachment core.  Pad rules require a real core
    and refuse when this Board IR version cannot supply one.
    """

    footprint = placed_by_ref.get(ref)
    if footprint is not None:
        return footprint.hull
    owner = view.owner_by_pad.get(ref)
    if owner is not None:
        parent = placed_by_ref.get(owner)
        if parent is not None:
            for pad in parent.pads:
                if pad.pad.id == ref:
                    if pad.core is None:
                        raise _UnsupportedError(
                            "a rule needs pad attachment geometry this version cannot prove"
                        )
                    return pad.core
    _reject_padless(view, ref)
    raise _UnresolvedError("a rule names an object that does not exist on this board")


def _resolve_centre(
    ref: str, placed_by_ref: dict[str, _PlacedFootprint], view: PlacementView
) -> PointNM:
    """Resolve an exact pose centre; an envelope midpoint is not an identity-bearing position."""

    footprint = placed_by_ref.get(ref)
    if footprint is not None:
        return footprint.origin
    owner = view.owner_by_pad.get(ref)
    if owner is not None:
        parent = placed_by_ref.get(owner)
        if parent is not None:
            for pad in parent.pads:
                if pad.pad.id == ref:
                    return pad.centre
    _reject_padless(view, ref)
    raise _UnresolvedError("a rule names an object that does not exist on this board")


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
        centres = [_resolve_centre(member, placed_by_ref, view) for member in rule.members]
        values = [centre.x if axis_index == 0 else centre.y for centre in centres]
        residual = max(values) - min(values)
    elif isinstance(rule, SymmetryRule):
        axis_index = 0 if rule.axis == "x" else 1
        mirror_centre = _resolve_centre(rule.about, placed_by_ref, view)
        mirror = mirror_centre.x if axis_index == 0 else mirror_centre.y
        worst = 0
        for left, right in rule.pairs:
            budget.charge()
            left_centre = _resolve_centre(left, placed_by_ref, view)
            right_centre = _resolve_centre(right, placed_by_ref, view)
            left_value = left_centre.x if axis_index == 0 else left_centre.y
            right_value = right_centre.x if axis_index == 0 else right_centre.y
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
        if rule.mode == "keep_in":
            core = _resolve_core(rule.subject, placed_by_ref, view)
            residual = 0 if rect_inside_ring(core, boundary) else 1
        else:
            bounds = _resolve_bounds(rule.subject, placed_by_ref, view)
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
        _reject_padless_preflight_refs(view, intent)
        _check_infeasible(intent)
        placed = _place(view, snapshot, intent, budget)
        # Stationary padless envelopes participate in physical legality, but remain unavailable
        # as subjects, anchors, and rule references as promised by the padless contract.
        placed_by_ref = {item.ref_id: item for item in placed if item.ref_id in view.footprints}
        rule_results = tuple(
            _evaluate_rule(index, rule, placed_by_ref, view, snapshot, budget)
            for index, rule in enumerate(intent.rules)
        )
        overlap, inconclusive = _pad_overlap(placed, budget)
        legality = PlacementLegality(
            pad_overlap=overlap,
            outline_containment=_outline_containment(placed, snapshot, budget),
            keepout_respect=_keepout_respect(placed, snapshot, budget),
            courtyard_overlap=_courtyard_overlap(placed, budget),
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
                if item.ref_id in view.footprints
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
