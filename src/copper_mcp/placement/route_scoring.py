"""Read-only routing evidence for ranking already-legal placement candidates.

This module deliberately has no proposal, candidate, serializer, or apply authority.  It projects
the legalizer's immutable pose evidence into a fresh in-memory Board IR snapshot, then asks the
existing bounded router to independently probe a small closed set of nets.  The projection is
never written to a board file and is useful only as a ranking signal.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from copper_mcp.board_ir import (
    BoardIRSnapshot,
    Footprint,
    FootprintSide,
    Pad,
    PointNM,
    Ring,
    make_snapshot,
)
from copper_mcp.board_ir.types import FULL_ROTATION_UDEG
from copper_mcp.placement.contracts import (
    FootprintPlacement,
    PlacementCandidate,
    PlacementError,
    verify_placement_id,
)
from copper_mcp.placement.geometry import rotate_offset
from copper_mcp.placement.view import PlacementView
from copper_mcp.routing.astar import AStarRouter
from copper_mcp.routing.contracts import AStarSettings, RouteFailureCode, RouteRequest

ROUTE_AWARE_SCORING_POLICY = "route-aware-astar-v1"
#: Identifies the exact estimator that produced a piece of route-aware evidence.  ADR-0024 records
#: `ordering_policy` for the same reason: a later estimator must never be mistaken for this one.
ROUTE_AWARE_ESTIMATOR_ID = "route-aware-astar-probe-v1"
_MAX_PROBES = 32

#: Diagnostics that mean the bounded search never finished, as opposed to ``NO_PATH``, which is the
#: only code that reports a completed search over the reachable space.  Collapsing the two would let
#: a caller read "this pose cannot be routed" out of "this router ran out of budget".
_REFUSAL_CODES: frozenset[RouteFailureCode] = frozenset(
    code for code in RouteFailureCode if code is not RouteFailureCode.NO_PATH
)


class RouteScoringError(ValueError):
    """Raised when a closed route-aware scoring policy is malformed."""


class _CandidateBindingError(RouteScoringError):
    """A fail-closed candidate/snapshot/view binding refusal."""


@dataclass(frozen=True, slots=True)
class RouteProbeSettings:
    """Closed, integer-only resource limits for independent routing probes.

    A probe is an independent candidate-only route on the virtual snapshot.  Probes are never
    merged, so this is deliberately not a congestion, DRC, or whole-board completion metric.
    """

    max_probes: int = 8
    #: Operation-wide cap shared by every candidate scored in one placement solve.
    max_total_probes: int = 512
    seed: int = 0
    astar_settings: AStarSettings = field(
        default_factory=lambda: AStarSettings(
            grid_step_nm=250_000,
            max_grid_nodes=100_000,
            max_expansions=50_000,
            max_obstacles=256,
            max_obstacle_checks=500_000,
        )
    )

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_probes, bool)
            or not isinstance(self.max_probes, int)
            or not 1 <= self.max_probes <= _MAX_PROBES
        ):
            raise RouteScoringError(f"max_probes must be an integer between 1 and {_MAX_PROBES}")
        if (
            isinstance(self.max_total_probes, bool)
            or not isinstance(self.max_total_probes, int)
            or not self.max_probes <= self.max_total_probes <= _MAX_PROBES * 1_000_000
        ):
            raise RouteScoringError(
                "max_total_probes must be an integer no smaller than max_probes"
            )
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise RouteScoringError("seed must be a non-negative integer")
        if not isinstance(self.astar_settings, AStarSettings):
            raise RouteScoringError("astar_settings must be an AStarSettings value")

    def digest(self) -> str:
        """Return a stable digest of every setting that can change a probe observation.

        Evidence recorded under different probe or A* budgets is not comparable.  Carrying this
        digest on the evidence is what stops a one-probe observation being read as an eleven-probe
        one, in the same spirit as ADR-0024's `ordering_policy`.
        """

        payload = {
            "max_probes": self.max_probes,
            "max_total_probes": self.max_total_probes,
            "seed": self.seed,
            "astar": {
                "grid_step_nm": self.astar_settings.grid_step_nm,
                "bend_penalty_nm": self.astar_settings.bend_penalty_nm,
                "proximity_penalty_nm": self.astar_settings.proximity_penalty_nm,
                "max_grid_nodes": self.astar_settings.max_grid_nodes,
                "max_expansions": self.astar_settings.max_expansions,
                "max_obstacles": self.astar_settings.max_obstacles,
                "max_obstacle_checks": self.astar_settings.max_obstacle_checks,
            },
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class RouteAwareEvidence:
    """Deterministic bounded-router observations used only for ranking.

    ``estimator_id`` and ``settings_digest`` are provenance, not measurements: they name the exact
    estimator and the exact probe/A* budgets under which the counts below were observed, so
    evidence taken under one configuration can never be silently compared with another.

    ``unrouted_probes`` is the superset tier: it counts every probe that produced no route.
    ``refused_probes`` is the subset of those where the bounded router never finished - a budget,
    grid, support, or cancellation refusal - as distinct from a completed search that proved no
    path exists in its model.
    """

    estimator_id: str
    settings_digest: str
    attempted_probes: int
    completed_probes: int
    unrouted_probes: int
    refused_probes: int
    hard_internal_violations: int
    wire_length_nm: int
    total_cost_nm: int
    operation_probes_before: int
    operation_probes_after: int
    operation_probe_limit: int

    def __post_init__(self) -> None:
        if self.estimator_id != ROUTE_AWARE_ESTIMATOR_ID:
            raise RouteScoringError("route-aware evidence must name this estimator")
        if (
            not isinstance(self.settings_digest, str)
            or not self.settings_digest.startswith("sha256:")
            or len(self.settings_digest) != 71
        ):
            raise RouteScoringError("route-aware evidence settings digest is malformed")
        values = (
            self.attempted_probes,
            self.completed_probes,
            self.unrouted_probes,
            self.refused_probes,
            self.hard_internal_violations,
            self.wire_length_nm,
            self.total_cost_nm,
            self.operation_probes_before,
            self.operation_probes_after,
            self.operation_probe_limit,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values
        ):
            raise RouteScoringError("route-aware evidence must use non-negative integers")
        if self.completed_probes + self.unrouted_probes != self.attempted_probes:
            raise RouteScoringError("route-aware probe counts are inconsistent")
        if self.refused_probes > self.unrouted_probes:
            raise RouteScoringError("refused probes cannot exceed unrouted probes")
        if (
            self.operation_probes_before > self.operation_probes_after
            or self.operation_probes_after > self.operation_probe_limit
            or self.operation_probes_after - self.operation_probes_before != self.attempted_probes
        ):
            raise RouteScoringError("route-aware operation probe accounting is inconsistent")


@dataclass(slots=True)
class RouteProbeBudget:
    """One deterministic operation-wide meter for route-aware scoring work."""

    limit: int
    used: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1:
            raise RouteScoringError("route probe budget limit must be positive")
        if isinstance(self.used, bool) or not isinstance(self.used, int) or self.used < 0:
            raise RouteScoringError("route probe budget use must be non-negative")

    def charge(self) -> bool:
        """Charge one probe before router work, refusing once the operation cap is reached."""

        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def score_route_aware_candidate(
    candidate: PlacementCandidate,
    snapshot: BoardIRSnapshot,
    view: PlacementView,
    *,
    settings: RouteProbeSettings,
    stopped: Callable[[], str | None],
    operation_budget: RouteProbeBudget | None = None,
) -> tuple[RouteAwareEvidence | None, str | None]:
    """Return independent bounded-router evidence for one legalizer-issued candidate.

    The candidate is first rechecked structurally by projecting its derived poses into a new,
    self-verifying Board IR snapshot.  Any stop is propagated as a withheld score rather than a
    partial ranking.  A router refusal counts as one unrouted probe; it never manufactures route
    geometry or changes the placement candidate.
    """

    if not isinstance(candidate, PlacementCandidate):
        raise RouteScoringError("candidate must be a PlacementCandidate")
    if not isinstance(snapshot, BoardIRSnapshot) or not isinstance(view, PlacementView):
        raise RouteScoringError("snapshot and view are required")
    if not isinstance(settings, RouteProbeSettings):
        raise RouteScoringError("settings must be RouteProbeSettings")
    if operation_budget is None:
        operation_budget = RouteProbeBudget(settings.max_total_probes)
    if not isinstance(operation_budget, RouteProbeBudget):
        raise RouteScoringError("operation_budget must be a RouteProbeBudget")
    if operation_budget.limit != settings.max_total_probes:
        raise RouteScoringError("operation_budget limit must match route probe settings")
    if (status := stopped()) is not None:
        return None, status
    digest = settings.digest()
    try:
        projected = project_legal_candidate_snapshot(candidate, snapshot, view)
    except _CandidateBindingError:
        raise
    except (ValueError, RouteScoringError):
        # A legalizer candidate that cannot be represented by this intentionally narrow virtual
        # projection receives no invented route completion.  Every probe it would have attempted is
        # recorded as refused.
        #
        # Reporting a single failed probe here would be a lexicographic inversion whenever
        # ``max_probes`` exceeds one: ``wire_length_nm`` is a minimize tier, so the zero emitted by
        # an unrepresentable candidate is the best possible value, and it would outrank a candidate
        # that was actually probed and tied on unrouted probes.  The probe set is a function of net
        # membership and layer stackup only - both invariant under a pose projection - so it can be
        # taken from the source snapshot without projecting anything.
        return _unscorable_evidence(snapshot, settings, digest, operation_budget)

    router = AStarRouter()
    completed = 0
    unrouted = 0
    refused = 0
    hard_violations = 0
    wire_length = 0
    total_cost = 0
    probes = _probes(projected, settings.max_probes)
    before = operation_budget.used
    for net_id, layer_id in probes:
        if (status := stopped()) is not None:
            return None, status
        if not operation_budget.charge():
            return None, "route_probe_exhausted"
        result = router.propose(
            projected,
            RouteRequest(
                board_revision=projected.snapshot_digest,
                net_id=net_id,
                layer_id=layer_id,
                seed=settings.seed,
                settings=settings.astar_settings,
            ),
            cancelled=lambda: stopped() is not None,
        )
        if (status := stopped()) is not None:
            return None, status
        if result.candidate is not None:
            completed += 1
            hard_violations += result.candidate.metrics.hard_internal_violations
            wire_length += result.candidate.metrics.wire_length_nm
            total_cost += result.candidate.cost.total_cost_nm
        elif result.connected is not None:
            completed += 1
        else:
            unrouted += 1
            if result.diagnostic is not None and result.diagnostic.code in _REFUSAL_CODES:
                refused += 1
    return RouteAwareEvidence(
        estimator_id=ROUTE_AWARE_ESTIMATOR_ID,
        settings_digest=digest,
        attempted_probes=len(probes),
        completed_probes=completed,
        unrouted_probes=unrouted,
        refused_probes=refused,
        hard_internal_violations=hard_violations,
        wire_length_nm=wire_length,
        total_cost_nm=total_cost,
        operation_probes_before=before,
        operation_probes_after=operation_budget.used,
        operation_probe_limit=operation_budget.limit,
    ), None


def _unscorable_evidence(
    snapshot: BoardIRSnapshot,
    settings: RouteProbeSettings,
    digest: str,
    operation_budget: RouteProbeBudget,
) -> tuple[RouteAwareEvidence | None, str | None]:
    """Record a candidate this projection cannot represent as wholly refused, never as cheap."""

    attempted = len(_probes(snapshot, settings.max_probes))
    before = operation_budget.used
    for _ in range(attempted):
        if not operation_budget.charge():
            return None, "route_probe_exhausted"
    return (
        RouteAwareEvidence(
            estimator_id=ROUTE_AWARE_ESTIMATOR_ID,
            settings_digest=digest,
            attempted_probes=attempted,
            completed_probes=0,
            unrouted_probes=attempted,
            refused_probes=attempted,
            hard_internal_violations=0,
            wire_length_nm=0,
            total_cost_nm=0,
            operation_probes_before=before,
            operation_probes_after=operation_budget.used,
            operation_probe_limit=operation_budget.limit,
        ),
        None,
    )


def _verify_candidate_binding(
    candidate: PlacementCandidate, snapshot: BoardIRSnapshot, view: PlacementView
) -> None:
    """Fail closed before projection unless candidate, snapshot, and view are exactly bound."""

    try:
        verify_placement_id(candidate)
    except PlacementError as error:
        raise _CandidateBindingError("placement candidate identity is invalid") from error
    if candidate.base_revision != snapshot.snapshot_digest:
        raise _CandidateBindingError("placement candidate is stale for the Board IR snapshot")
    if candidate.view_revision != view.board_revision:
        raise _CandidateBindingError("placement candidate is stale for the placement view")
    if view.snapshot_digest != snapshot.snapshot_digest:
        raise _CandidateBindingError("placement view is not bound to the Board IR snapshot")


def project_legal_candidate_snapshot(
    candidate: PlacementCandidate, snapshot: BoardIRSnapshot, view: PlacementView
) -> BoardIRSnapshot:
    """Project legalizer-derived footprint poses into a new immutable in-memory snapshot.

    This is a read-only Board IR adapter, not a KiCad renderer.  It preserves every non-placement
    object and transforms only owned pad centres/angles plus courtyard vertices under the exact
    orthogonal pose the legalizer emitted.  Side flips remain outside its conservative support.
    """

    _verify_candidate_binding(candidate, snapshot, view)
    # These three are structural-integrity violations, not narrow-projection limits.  They are
    # raised as binding refusals so they escape the caller's broad handler instead of being
    # downgraded to "this candidate happened to fail its probes".
    placements = {item.ref_id: item for item in candidate.placements}
    if len(placements) != len(candidate.placements):
        raise _CandidateBindingError("candidate footprint placements are not unique")
    if set(placements) != set(view.footprints):
        raise _CandidateBindingError("candidate does not cover the view footprint set")

    footprints_by_id = {item.id: item for item in snapshot.content.footprints}
    if set(footprints_by_id) != set(view.footprints):
        raise _CandidateBindingError("snapshot and placement view footprint sets disagree")
    # A side flip, by contrast, is a legal legalizer output that this deliberately narrow adapter
    # declines to represent.  It stays a plain scoring error so it is scored as refused probes.
    for ref_id, placement in placements.items():
        if FootprintSide(placement.side) is not footprints_by_id[ref_id].side:
            raise RouteScoringError("route-aware scoring does not project side flips")

    projected_footprints = tuple(
        _project_footprint(footprint, placements[footprint.id])
        for footprint in snapshot.content.footprints
    )
    projected_pads = tuple(
        _project_pad(
            pad,
            footprints_by_id[view.owner_by_pad[pad.id]],
            placements[view.owner_by_pad[pad.id]],
        )
        for pad in snapshot.content.pads
    )
    return make_snapshot(
        replace(
            snapshot.content,
            footprints=projected_footprints,
            pads=projected_pads,
        )
    )


def _project_footprint(footprint: Footprint, placement: FootprintPlacement) -> Footprint:
    return replace(
        footprint,
        origin=PointNM(placement.origin_x_nm, placement.origin_y_nm),
        rotation_udeg=placement.orientation_udeg,
        courtyards=tuple(
            Ring(tuple(_project_point(point, footprint, placement) for point in courtyard.points))
            for courtyard in footprint.courtyards
        ),
    )


def _project_pad(pad: Pad, footprint: Footprint, placement: FootprintPlacement) -> Pad:
    return replace(
        pad,
        center=_project_point(pad.center, footprint, placement),
        rotation_udeg=(pad.rotation_udeg - footprint.rotation_udeg + placement.orientation_udeg)
        % FULL_ROTATION_UDEG,
    )


def _project_point(point: PointNM, footprint: Footprint, placement: FootprintPlacement) -> PointNM:
    local = rotate_offset(
        PointNM(point.x - footprint.origin.x, point.y - footprint.origin.y),
        (-footprint.rotation_udeg) % FULL_ROTATION_UDEG,
    )
    turned = rotate_offset(local, placement.orientation_udeg)
    return PointNM(placement.origin_x_nm + turned.x, placement.origin_y_nm + turned.y)


def _probes(snapshot: BoardIRSnapshot, maximum: int) -> tuple[tuple[str, str], ...]:
    """Choose a stable, supported subset of multi- or two-pad single-layer net probes."""

    layer_order = {layer.id: layer.index for layer in snapshot.content.copper_layers}
    by_net: dict[str, list[Pad]] = {}
    for pad in snapshot.content.pads:
        if pad.net_id is not None:
            by_net.setdefault(pad.net_id, []).append(pad)
    probes: list[tuple[str, str]] = []
    for net_id in sorted(by_net):
        pads = by_net[net_id]
        if not 2 <= len(pads) <= 9:
            continue
        common_layers = set(pads[0].layer_ids)
        for pad in pads[1:]:
            common_layers.intersection_update(pad.layer_ids)
        supported = sorted(common_layers, key=lambda layer_id: (layer_order[layer_id], layer_id))
        if supported:
            probes.append((net_id, supported[0]))
        if len(probes) >= maximum:
            break
    return tuple(probes)


__all__ = [
    "ROUTE_AWARE_ESTIMATOR_ID",
    "ROUTE_AWARE_SCORING_POLICY",
    "RouteAwareEvidence",
    "RouteProbeBudget",
    "RouteProbeSettings",
    "RouteScoringError",
    "project_legal_candidate_snapshot",
    "score_route_aware_candidate",
]
