"""A bounded, deterministic baseline search over legal placement candidates.

The solver deliberately has no geometry or candidate-writing authority.  It produces only
ref-anchored :class:`PlacementProposal` values and sends every state through
``evaluate_placement``.  Consequently, its output remains the legalizer's immutable,
revision-bound evidence contract; this module is an objective-driven proposal generator, not a
second legalizer and not a board editor.

The cost is intentionally modest: violated intent rules first, then the sum of all pairwise
same-net Manhattan pad distances.  That distance is a reproducible connectivity proxy, not a
route length, timing result, DRC result, or optimal-placement certificate.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from copper_mcp.board_ir import BoardIRSnapshot, PointNM
from copper_mcp.placement.contracts import (
    FootprintPlacement,
    PlacementCandidate,
    PlacementIntent,
    PlacementProposal,
    PlacementResult,
)
from copper_mcp.placement.geometry import rotate_offset
from copper_mcp.placement.legalizer import evaluate_placement
from copper_mcp.placement.view import FootprintView, PlacementView

_FULL_ROTATION_UDEG = 360_000_000
_DIRECTIONS: tuple[tuple[int, int], ...] = ((-1, 0), (0, -1), (0, 1), (1, 0))
_MAX_EVALUATIONS = 1_000_000
_MAX_ROUNDS = 10_000
_MAX_BEAM_WIDTH = 1_024
_MAX_RANKED = 1_024
_MAX_STEP_NM = 1_000_000_000
_MAX_LEGALIZER_CHECKS = 2_000_000
_MAX_DEADLINE_SECONDS = 60.0


class PlacementSolverError(ValueError):
    """Raised when solver settings are malformed before any search starts."""


@dataclass(frozen=True, slots=True)
class PlacementSolverSettings:
    """Explicit resource ceilings for the baseline local/beam search.

    The search always visits references and directions in lexical order.  A wall-clock deadline
    is a fail-closed operational ceiling; reproducibility tests should use a deadline comfortably
    above the measured run time and rely on ``max_evaluations`` for the deterministic ceiling.
    """

    max_evaluations: int = 64
    max_rounds: int = 4
    beam_width: int = 4
    max_ranked: int = 8
    step_nm: int = 1_000_000
    deadline_seconds: float = 2.0
    legalizer_max_checks: int = 200_000
    legalizer_deadline_seconds: float = 1.0

    def __post_init__(self) -> None:
        limits = {
            "max_evaluations": _MAX_EVALUATIONS,
            "beam_width": _MAX_BEAM_WIDTH,
            "max_ranked": _MAX_RANKED,
            "step_nm": _MAX_STEP_NM,
            "legalizer_max_checks": _MAX_LEGALIZER_CHECKS,
        }
        for name, maximum in limits.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise PlacementSolverError(f"{name} must be an integer between 1 and {maximum}")
        if (
            isinstance(self.max_rounds, bool)
            or not isinstance(self.max_rounds, int)
            or not 0 <= self.max_rounds <= _MAX_ROUNDS
        ):
            raise PlacementSolverError(f"max_rounds must be an integer between 0 and {_MAX_ROUNDS}")
        for name in ("deadline_seconds", "legalizer_deadline_seconds"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or not 0 < value <= _MAX_DEADLINE_SECONDS
            ):
                raise PlacementSolverError(
                    f"{name} must be finite and between 0 and {_MAX_DEADLINE_SECONDS}"
                )


@dataclass(frozen=True, slots=True, order=True)
class PlacementSolverScore:
    """Lexicographic search score; lower is better and all fields are exact integers."""

    violated_rules: int
    connectivity_manhattan_nm: int
    moved_footprints: int


@dataclass(frozen=True, slots=True)
class RankedPlacement:
    """One legalizer-issued candidate and the solver's explicitly limited score."""

    result: PlacementResult
    score: PlacementSolverScore

    def __post_init__(self) -> None:
        if self.result.status != "previewed" or self.result.candidate is None:
            raise PlacementSolverError("ranked placements must be legalizer-issued candidates")

    @property
    def candidate(self) -> PlacementCandidate:
        """The verified candidate, available only because ``result`` was previewed."""

        assert self.result.candidate is not None
        return self.result.candidate


@dataclass(frozen=True, slots=True)
class PlacementSolveResult:
    """The bounded outcome; no entry can contain an unlegalized placement."""

    status: str
    initial: PlacementResult | None
    initial_score: PlacementSolverScore | None
    ranked: tuple[RankedPlacement, ...]
    evaluations: int

    def __post_init__(self) -> None:
        if self.status not in {
            "completed",
            "cancelled",
            "deadline_exhausted",
            "work_exhausted",
            "input_refused",
        }:
            raise PlacementSolverError("solver status is malformed")
        if self.evaluations < 0:
            raise PlacementSolverError("solver evaluations must not be negative")
        if any(item.result.candidate is None for item in self.ranked):
            raise PlacementSolverError("solver output must be candidate-backed")


@dataclass(frozen=True, slots=True)
class _SearchState:
    """Internal proposal state.  Its candidate always comes from the legalizer."""

    proposals: tuple[PlacementProposal, ...]
    ranked: RankedPlacement


def solve_placement(
    intent: PlacementIntent,
    snapshot: BoardIRSnapshot,
    view: PlacementView,
    *,
    settings: PlacementSolverSettings | None = None,
    cancelled: Callable[[], bool] | None = None,
    board_path: str = "",
) -> PlacementSolveResult:
    """Search bounded grid-adjacent moves and rank only legalizer-issued candidates.

    ``intent`` is never changed.  For the search only, pad subjects are canonicalized to their
    owning footprint while rule and anchor references retain their original meaning. Locked and
    padless footprints are never synthesized as moved proposals.  A stale/digest mismatch is
    surfaced by the initial legalizer evaluation, before the solver examines any footprint
    geometry.
    """

    if not isinstance(intent, PlacementIntent):
        raise PlacementSolverError("intent must be a PlacementIntent")
    if not isinstance(snapshot, BoardIRSnapshot):
        raise PlacementSolverError("snapshot must be a BoardIRSnapshot")
    if not isinstance(view, PlacementView):
        raise PlacementSolverError("view must be a PlacementView")
    if settings is None:
        settings = PlacementSolverSettings()
    if not isinstance(settings, PlacementSolverSettings):
        raise PlacementSolverError("settings must be a PlacementSolverSettings")
    if cancelled is not None and not callable(cancelled):
        raise PlacementSolverError("cancelled must be callable")

    search_intent = _canonicalise_search_intent(intent, view)
    if search_intent is None:
        return PlacementSolveResult("input_refused", None, None, (), 0)

    started = time.monotonic()

    def stopped() -> str | None:
        if cancelled is not None:
            try:
                if cancelled():
                    return "cancelled"
            except Exception:
                return "cancelled"
        if time.monotonic() - started >= settings.deadline_seconds:
            return "deadline_exhausted"
        return None

    def remaining_legalizer_deadline() -> tuple[str | None, float | None]:
        """Return an evaluator sub-deadline within the one solver operation deadline."""

        stop = stopped()
        if stop is not None:
            return stop, None
        remaining = settings.deadline_seconds - (time.monotonic() - started)
        if remaining <= 0:
            return "deadline_exhausted", None
        return None, min(settings.legalizer_deadline_seconds, remaining)

    initial_status, initial_deadline = remaining_legalizer_deadline()
    if initial_status is not None:
        return PlacementSolveResult(initial_status, None, None, (), 0)
    assert initial_deadline is not None

    initial = evaluate_placement(
        search_intent,
        snapshot,
        view,
        max_checks=settings.legalizer_max_checks,
        deadline_seconds=initial_deadline,
        board_path=board_path,
    )
    evaluations = 1
    if initial.candidate is None:
        return PlacementSolveResult("input_refused", initial, None, (), evaluations)

    initial_score, score_status = _score(initial.candidate, snapshot, view, stopped=stopped)
    if score_status is not None:
        return PlacementSolveResult(score_status, initial, None, (), evaluations)
    assert initial_score is not None
    initial_ranked = RankedPlacement(initial, initial_score)
    known: dict[str, RankedPlacement] = {initial_ranked.candidate.candidate_id: initial_ranked}
    movable = _movable_refs(search_intent, view)
    initial_state = _SearchState(
        _normalise_proposals(search_intent, initial_ranked.candidate, view, movable), initial_ranked
    )
    frontier: tuple[_SearchState, ...] = (initial_state,)
    status = "completed"

    for _round in range(settings.max_rounds):
        if not frontier or not movable:
            break
        next_states: list[_SearchState] = []
        for state in frontier:
            for ref_id in movable:
                for dx, dy in _DIRECTIONS:
                    if evaluations >= settings.max_evaluations:
                        status = "work_exhausted"
                        break
                    call_status, call_deadline = remaining_legalizer_deadline()
                    if call_status is not None:
                        status = call_status
                        break
                    assert call_deadline is not None
                    successor = _with_step(state, ref_id, dx, dy, settings.step_nm, view)
                    proposal_intent = replace(search_intent, proposals=successor)
                    result = evaluate_placement(
                        proposal_intent,
                        snapshot,
                        view,
                        max_checks=settings.legalizer_max_checks,
                        deadline_seconds=call_deadline,
                        board_path=board_path,
                    )
                    evaluations += 1
                    if result.candidate is None:
                        continue
                    score, score_status = _score(result.candidate, snapshot, view, stopped=stopped)
                    if score_status is not None:
                        status = score_status
                        break
                    assert score is not None
                    proposed_ranked = RankedPlacement(result, score)
                    candidate_id = proposed_ranked.candidate.candidate_id
                    if candidate_id in known:
                        continue
                    known[candidate_id] = proposed_ranked
                    next_states.append(_SearchState(successor, proposed_ranked))
                if status != "completed":
                    break
            if status != "completed":
                break
        if status != "completed":
            break
        frontier = tuple(sorted(next_states, key=_state_key)[: settings.beam_width])

    retained: tuple[RankedPlacement, ...] = tuple(
        sorted(known.values(), key=_rank_key)[: settings.max_ranked]
    )
    return PlacementSolveResult(status, initial, initial_ranked.score, retained, evaluations)


def _canonicalise_search_intent(
    intent: PlacementIntent, view: PlacementView
) -> PlacementIntent | None:
    """Map supported pad subjects to owning footprints without broadening move authority.

    The placement contract permits a subject reference to name either a footprint or one of its
    pads. The legalizer resolves pad references for rules and anchors, but a physical move always
    belongs to one footprint. Canonicalizing the private solver request preserves that meaning and
    keeps generated proposals inside the intent's declared subject scope. If two user proposals
    collapse onto one footprint, the search refuses rather than guessing which offset should win.
    Unknown and padless references stay untouched so the legalizer can return its established
    typed diagnostic.
    """

    resolved_subjects: dict[str, str] = {}
    for subject in intent.subject_refs:
        footprint = view.resolve(subject)
        if footprint is None:
            return intent
        resolved_subjects[subject] = footprint.ref_id

    proposals_by_subject: dict[str, PlacementProposal] = {}
    for proposal in intent.proposals:
        footprint = view.resolve(proposal.subject)
        if footprint is None:
            return intent
        canonical = replace(proposal, subject=footprint.ref_id)
        existing = proposals_by_subject.get(canonical.subject)
        if existing is not None and existing != canonical:
            return None
        proposals_by_subject[canonical.subject] = canonical

    canonical_subjects = tuple(sorted(set(resolved_subjects.values())))
    canonical_proposals = tuple(
        proposals_by_subject[subject] for subject in sorted(proposals_by_subject)
    )
    if canonical_subjects == intent.subject_refs and canonical_proposals == intent.proposals:
        return intent
    return replace(intent, subject_refs=canonical_subjects, proposals=canonical_proposals)


def _movable_refs(intent: PlacementIntent, view: PlacementView) -> tuple[str, ...]:
    """Resolve intent subjects to unique, placeable, unlocked footprint identifiers."""

    refs: set[str] = set()
    for subject in intent.subject_refs:
        footprint = view.resolve(subject)
        if footprint is not None and not footprint.locked:
            refs.add(footprint.ref_id)
    return tuple(sorted(refs))


def _normalise_proposals(
    intent: PlacementIntent,
    candidate: PlacementCandidate,
    view: PlacementView,
    movable: tuple[str, ...],
) -> tuple[PlacementProposal, ...]:
    """Keep supplied proposals unless the legalizer has resolved a movable pose exactly."""

    proposals = {item.subject: item for item in intent.proposals}
    movable_set = frozenset(movable)
    for placement in candidate.placements:
        if placement.ref_id in movable_set and placement.moved:
            footprint = view.footprints[placement.ref_id]
            proposals[placement.ref_id] = _proposal_for_pose(footprint, placement)
    return tuple(proposals[subject] for subject in sorted(proposals))


def _with_step(
    state: _SearchState,
    ref_id: str,
    dx: int,
    dy: int,
    step_nm: int,
    view: PlacementView,
) -> tuple[PlacementProposal, ...]:
    candidate_placement = next(
        item for item in state.ranked.candidate.placements if item.ref_id == ref_id
    )
    shifted = replace(
        candidate_placement,
        origin_x_nm=candidate_placement.origin_x_nm + dx * step_nm,
        origin_y_nm=candidate_placement.origin_y_nm + dy * step_nm,
    )
    proposals = {item.subject: item for item in state.proposals}
    proposals[ref_id] = _proposal_for_pose(view.footprints[ref_id], shifted)
    return tuple(proposals[subject] for subject in sorted(proposals))


def _proposal_for_pose(
    footprint: FootprintView, placement: FootprintPlacement
) -> PlacementProposal:
    """Encode a legalizer-derived absolute pose back into its required ref-relative form."""

    hull_centre_x = (footprint.hull[0] + footprint.hull[2]) // 2
    hull_centre_y = (footprint.hull[1] + footprint.hull[3]) // 2
    return PlacementProposal(
        subject=placement.ref_id,
        offset_x_nm=placement.origin_x_nm - hull_centre_x,
        offset_y_nm=placement.origin_y_nm - hull_centre_y,
        orientation_udeg=placement.orientation_udeg,
        side=placement.side,
    )


def _score(
    candidate: PlacementCandidate,
    snapshot: BoardIRSnapshot,
    view: PlacementView,
    *,
    stopped: Callable[[], str | None],
) -> tuple[PlacementSolverScore | None, str | None]:
    """Score a legal candidate, stopping before an unbounded proxy calculation can escape.

    The exact all-pairs Manhattan total is computed from sorted axes rather than enumerating
    pairs. This keeps the proxy exact while reducing each net from quadratic to ``O(n log n)``.
    Every linear scan checks the solver's single cancellation/deadline gate.
    """

    violated = 0
    for rule_result in candidate.evidence.rule_results:
        try:
            _raise_if_stopped(stopped)
        except _ScoreInterruptedError as error:
            return None, error.status
        violated += rule_result.status == "violated"
    moved = 0
    for placement in candidate.placements:
        try:
            _raise_if_stopped(stopped)
        except _ScoreInterruptedError as error:
            return None, error.status
        moved += placement.moved
    try:
        connectivity = _connectivity_manhattan(candidate, snapshot, view, stopped=stopped)
    except _ScoreInterruptedError as error:
        return None, error.status
    return PlacementSolverScore(
        violated_rules=violated,
        connectivity_manhattan_nm=connectivity,
        moved_footprints=moved,
    ), None


def _connectivity_manhattan(
    candidate: PlacementCandidate,
    snapshot: BoardIRSnapshot,
    view: PlacementView,
    *,
    stopped: Callable[[], str | None],
) -> int:
    """Return the all-pairs same-net Manhattan proxy in integer nanometres.

    This intentionally ignores routing topology, layers, existing tracks, timing, impedance and
    congestion.  Its only role is a stable, visible rank signal for this baseline.
    """

    placement_by_ref: dict[str, FootprintPlacement] = {}
    for placement in candidate.placements:
        _raise_if_stopped(stopped)
        placement_by_ref[placement.ref_id] = placement
    centres_by_net: dict[str, list[PointNM]] = {}
    for pad in snapshot.content.pads:
        _raise_if_stopped(stopped)
        if pad.net_id is None:
            continue
        owner = view.owner_by_pad.get(pad.id)
        if owner is None:
            continue
        footprint = view.footprints[owner]
        placement = placement_by_ref[owner]
        local = rotate_offset(
            PointNM(pad.center.x - footprint.origin.x, pad.center.y - footprint.origin.y),
            (-footprint.orientation_udeg) % _FULL_ROTATION_UDEG,
        )
        turned = rotate_offset(local, placement.orientation_udeg)
        centre = PointNM(
            placement.origin_x_nm + turned.x,
            placement.origin_y_nm + turned.y,
        )
        centres_by_net.setdefault(pad.net_id, []).append(centre)

    total = 0
    for net_id in sorted(centres_by_net):
        _raise_if_stopped(stopped)
        entries = centres_by_net[net_id]
        total += _pairwise_axis_distance((point.x for point in entries), stopped=stopped)
        total += _pairwise_axis_distance((point.y for point in entries), stopped=stopped)
    return total


class _ScoreInterruptedError(RuntimeError):
    """Carry one non-sensitive solver stop status out of a scoring helper."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def _raise_if_stopped(stopped: Callable[[], str | None]) -> None:
    """Stop scoring without manufacturing an incomplete ranking signal."""

    if (status := stopped()) is not None:
        raise _ScoreInterruptedError(status)


def _pairwise_axis_distance(values: Iterable[int], *, stopped: Callable[[], str | None]) -> int:
    """Return ``sum(abs(a - b) for a < b)`` for one axis in ``O(n log n)`` exactly."""

    ordered: list[int] = []
    for value in values:
        _raise_if_stopped(stopped)
        ordered.append(value)
    ordered.sort()
    count = len(ordered)
    total = 0
    for index, value in enumerate(ordered):
        _raise_if_stopped(stopped)
        total += value * (2 * index - count + 1)
    return total


def _rank_key(item: RankedPlacement) -> tuple[PlacementSolverScore, str]:
    return (item.score, item.candidate.candidate_id)


def _state_key(item: _SearchState) -> tuple[PlacementSolverScore, str]:
    return _rank_key(item.ranked)
