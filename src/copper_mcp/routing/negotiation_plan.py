"""Three separately declared, digest-bound negotiation policy slots.

ADR-0055 gave the negotiated coordinator one fused strategy: a hardcoded net order, a hardcoded
additive history-cost update, and a hardcoded rip-up-everything discipline.  ADR-0064 then bound a
closed policy decision to the *initial* net order only, and reserved every other negotiation choice
for a later decision with its own evidence.

This module is that later contract's data half.  It declares the three PathFinder negotiation
choices as three separate closed slots -- net order, per-iteration cost update, and rip-up
selection -- each a fixed enumeration of literals plus bounded integer weights, each carrying its
own content digest, composed into one plan digest.

Two properties are load-bearing:

* **Nothing here touches the path search.**  A slot chooses which nets are handed to the router,
  in what order, how the coordinator's integer congestion counters move between iterations, and
  which nets are re-routed.  The A* expansion, its cost function, its obstacle predicate, and the
  geometry it emits are untouched.  A future learned selector may pick among these literals; it
  can never author a rule body, because a rule body is a closed enumeration member.
* **No inert parameter may vary a digest.**  A weight that a rule does not read is pinned to its
  neutral value by validation, so two plans have different digests only when they can produce
  different behavior.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

NEGOTIATION_PLAN_SCHEMA = "copper-mcp.negotiation-plan.v1"
NET_ORDER_SLOT_SCHEMA = "copper-mcp.negotiation-slot.net-order.v1"
COST_UPDATE_SLOT_SCHEMA = "copper-mcp.negotiation-slot.cost-update.v1"
RIP_UP_SLOT_SCHEMA = "copper-mcp.negotiation-slot.rip-up.v1"

MAX_NEGOTIATED_NETS = 32
MAX_SLOT_WEIGHT = 1024
NEUTRAL_WEIGHT = 1
MAX_RIPUP_WINDOW_CELLS = 64
NEUTRAL_WINDOW_CELLS = 0


class NetOrderRule(StrEnum):
    """Closed rules for the order in which nets are handed to the router each iteration."""

    STABLE_IDENTIFIER = "stable-identifier-v1"
    CONFLICT_DESCENDING = "conflict-descending-v1"
    DEMAND_DESCENDING = "demand-descending-v1"
    DEMAND_ASCENDING = "demand-ascending-v1"


class CostUpdateRule(StrEnum):
    """Closed rules for how congestion counters move between iterations.

    The names are deliberately not "PathFinder's rule".  McMurchie and Ebeling specify the history
    term only qualitatively -- each iteration a shared node is used, its cost "is increased
    slightly" -- and publish no closed form for it or for the present-sharing factor.  The
    additive `h += max(0, occupancy - capacity) * acc_fac` form is VPR's `acc_cost`, and the
    fading variants come from the detailed-routing line (Dr. CU, TritonRoute).
    """

    ACCUMULATED_OVERUSE = "accumulated-overuse-v1"
    SCALED_ACCUMULATION = "scaled-accumulation-v1"
    SATURATING_DECAY = "saturating-decay-v1"


class RipUpRule(StrEnum):
    """Closed rules for which nets are ripped up before the next iteration.

    `ALL_NETS` is what classic PathFinder does and what ADR-0055 already did.  `CONFLICTED_ONLY`
    is the enhancement PathFinder's own section 3.5 proposes and what VPR ships.
    `TOP_CONFLICT_ONLY` is CopperMCP-original: no published router selects a bounded top-k by
    conflict score, so it is offered as a work-bounding knob and claims no literature pedigree.

    `CONFLICT_WINDOW` is the bounded rip-up window of issue #64.  It re-routes every conflicted
    net plus every retained net whose copper lies within a fixed number of lattice cells of one,
    which is the shape TritonRoute's search-and-repair uses: a *constant* worker box that is
    re-offset each pass, never a window that widens until it is full rip-up again.  See
    `docs/research/incremental-spatial-index-v1.md` §1.2 for the schedule that argument reads.
    """

    ALL_NETS = "all-nets-v1"
    CONFLICTED_ONLY = "conflicted-only-v1"
    TOP_CONFLICT_ONLY = "top-conflict-only-v1"
    CONFLICT_WINDOW = "conflict-window-v1"


def _bounded(name: str, value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the declared negotiation slot range")
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


@dataclass(frozen=True, slots=True)
class NetOrderSlot:
    """Declared rule for the per-iteration net order.

    The default reproduces ADR-0055 exactly: stable `(net_id, seed)` on the first pass, and the
    coordinator's own exact conflict scores descending on every later pass.
    """

    rule: NetOrderRule = NetOrderRule.CONFLICT_DESCENDING
    schema: str = NET_ORDER_SLOT_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.rule, NetOrderRule):
            raise ValueError("net-order rule is not a declared literal")
        if self.schema != NET_ORDER_SLOT_SCHEMA:
            raise ValueError("net-order slot schema is unsupported")

    def as_json(self) -> dict[str, object]:
        """Return the canonical serializable view of this slot."""

        return {"rule": self.rule.value, "schema": self.schema}

    @property
    def slot_digest(self) -> str:
        """Return this slot's own content address, independent of the other two slots."""

        return _sha256(_canonical_bytes(self.as_json()))


@dataclass(frozen=True, slots=True)
class CostUpdateSlot:
    """Declared rule for the per-iteration congestion cost update.

    A negotiation iteration moves two terms: the accumulated history of an overused resource, and
    the present-sharing factor applied to every resource.  Both live in this one slot because they
    are one update rule; splitting them would let two digests describe one behavior.

    The default reproduces ADR-0055 exactly: history accumulates the integer overuse count with no
    decay, and the present penalty is constant across iterations.  A growth ratio above 1 gives
    the shape of VPR's `pres_fac_mult` schedule; the coordinator's existing history and penalty
    ceilings are what stop an unbounded history term from eventually dominating the present term.
    """

    rule: CostUpdateRule = CostUpdateRule.ACCUMULATED_OVERUSE
    accumulation_weight: int = NEUTRAL_WEIGHT
    decay_numerator: int = NEUTRAL_WEIGHT
    decay_denominator: int = NEUTRAL_WEIGHT
    present_growth_numerator: int = NEUTRAL_WEIGHT
    present_growth_denominator: int = NEUTRAL_WEIGHT
    schema: str = COST_UPDATE_SLOT_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.rule, CostUpdateRule):
            raise ValueError("cost-update rule is not a declared literal")
        if self.schema != COST_UPDATE_SLOT_SCHEMA:
            raise ValueError("cost-update slot schema is unsupported")
        _bounded(
            "accumulation weight", self.accumulation_weight, minimum=1, maximum=MAX_SLOT_WEIGHT
        )
        _bounded(
            "history decay numerator", self.decay_numerator, minimum=0, maximum=MAX_SLOT_WEIGHT
        )
        _bounded(
            "history decay denominator", self.decay_denominator, minimum=1, maximum=MAX_SLOT_WEIGHT
        )
        _bounded(
            "present growth numerator",
            self.present_growth_numerator,
            minimum=1,
            maximum=MAX_SLOT_WEIGHT,
        )
        _bounded(
            "present growth denominator",
            self.present_growth_denominator,
            minimum=1,
            maximum=MAX_SLOT_WEIGHT,
        )
        if self.decay_numerator > self.decay_denominator:
            raise ValueError("history decay must not amplify accumulated congestion")
        if self.present_growth_numerator < self.present_growth_denominator:
            raise ValueError("present congestion pressure must not weaken between iterations")
        # An inert parameter must not be able to vary this slot's digest: a rule that never reads
        # a weight pins that weight to its neutral value, so one digest names one behavior.
        if (
            self.rule is CostUpdateRule.ACCUMULATED_OVERUSE
            and self.accumulation_weight != NEUTRAL_WEIGHT
        ):
            raise ValueError("the additive rule does not read an accumulation weight")
        if self.rule is not CostUpdateRule.SATURATING_DECAY and (
            self.decay_numerator != NEUTRAL_WEIGHT or self.decay_denominator != NEUTRAL_WEIGHT
        ):
            raise ValueError("only the saturating-decay rule reads a decay ratio")

    @property
    def decays_unused_resources(self) -> bool:
        """Return true when the rule must visit resources that were not overused this pass."""

        return self.rule is CostUpdateRule.SATURATING_DECAY

    def as_json(self) -> dict[str, object]:
        """Return the canonical serializable view of this slot."""

        return {
            "accumulation_weight": self.accumulation_weight,
            "decay_denominator": self.decay_denominator,
            "decay_numerator": self.decay_numerator,
            "present_growth_denominator": self.present_growth_denominator,
            "present_growth_numerator": self.present_growth_numerator,
            "rule": self.rule.value,
            "schema": self.schema,
        }

    @property
    def slot_digest(self) -> str:
        """Return this slot's own content address, independent of the other two slots."""

        return _sha256(_canonical_bytes(self.as_json()))


@dataclass(frozen=True, slots=True)
class RipUpSlot:
    """Declared rule for which nets are ripped up before the next iteration.

    The default reproduces ADR-0055 exactly: every net is ripped up and re-routed on every pass,
    which is the discipline classic PathFinder describes.
    """

    rule: RipUpRule = RipUpRule.ALL_NETS
    max_ripup_nets: int = MAX_NEGOTIATED_NETS
    ripup_window_cells: int = NEUTRAL_WINDOW_CELLS
    schema: str = RIP_UP_SLOT_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.rule, RipUpRule):
            raise ValueError("rip-up rule is not a declared literal")
        if self.schema != RIP_UP_SLOT_SCHEMA:
            raise ValueError("rip-up slot schema is unsupported")
        _bounded("rip-up ceiling", self.max_ripup_nets, minimum=1, maximum=MAX_NEGOTIATED_NETS)
        _bounded(
            "rip-up window",
            self.ripup_window_cells,
            minimum=NEUTRAL_WINDOW_CELLS,
            maximum=MAX_RIPUP_WINDOW_CELLS,
        )
        if self.rule is not RipUpRule.TOP_CONFLICT_ONLY and self.max_ripup_nets != (
            MAX_NEGOTIATED_NETS
        ):
            raise ValueError("only the top-conflict rule reads a rip-up ceiling")
        if self.rule is not RipUpRule.CONFLICT_WINDOW and self.ripup_window_cells != (
            NEUTRAL_WINDOW_CELLS
        ):
            raise ValueError("only the conflict-window rule reads a rip-up window")
        if self.rule is RipUpRule.CONFLICT_WINDOW and self.ripup_window_cells < 1:
            raise ValueError("the conflict-window rule requires a positive rip-up window")

    def as_json(self) -> dict[str, object]:
        """Return the canonical serializable view of this slot.

        The window is present **only** for the rule that reads it.  Every other literal keeps the
        exact canonical bytes it published before the window existed, so no already-issued rip-up
        slot digest, plan digest, or plan-bound candidate identity moves because a fourth literal
        was added.  A caller who stored `all-nets-v1`'s digest can still re-derive it.  This is
        the narrow reading of ADR-0073's no-inert-parameter rule: a weight a rule does not read
        may not vary the digest, and the cheapest way to guarantee that for a *new* weight is for
        it not to appear at all.
        """

        payload: dict[str, object] = {
            "max_ripup_nets": self.max_ripup_nets,
            "rule": self.rule.value,
            "schema": self.schema,
        }
        if self.rule is RipUpRule.CONFLICT_WINDOW:
            payload["ripup_window_cells"] = self.ripup_window_cells
        return payload

    @property
    def slot_digest(self) -> str:
        """Return this slot's own content address, independent of the other two slots."""

        return _sha256(_canonical_bytes(self.as_json()))


@dataclass(frozen=True, slots=True)
class NegotiationPlan:
    """One immutable composition of the three separately declared negotiation slots.

    The default plan is behaviorally identical to the ADR-0055 coordinator.  It is not the
    coordinator's *default*: absent a declared plan the coordinator keeps its historic code path
    and its historic candidate identity untouched.
    """

    net_order: NetOrderSlot = NetOrderSlot()
    cost_update: CostUpdateSlot = CostUpdateSlot()
    rip_up: RipUpSlot = RipUpSlot()
    schema: str = NEGOTIATION_PLAN_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.net_order, NetOrderSlot):
            raise ValueError("the net-order slot is malformed")
        if not isinstance(self.cost_update, CostUpdateSlot):
            raise ValueError("the cost-update slot is malformed")
        if not isinstance(self.rip_up, RipUpSlot):
            raise ValueError("the rip-up slot is malformed")
        if self.schema != NEGOTIATION_PLAN_SCHEMA:
            raise ValueError("negotiation plan schema is unsupported")

    def as_json(self) -> dict[str, object]:
        """Return the canonical serializable view of this plan and its three slot digests."""

        return {
            "cost_update_slot_digest": self.cost_update.slot_digest,
            "net_order_slot_digest": self.net_order.slot_digest,
            "rip_up_slot_digest": self.rip_up.slot_digest,
            "schema": self.schema,
        }

    @property
    def plan_digest(self) -> str:
        """Return the content address composed from exactly the three slot digests."""

        return _sha256(_canonical_bytes(self.as_json()))


LEGACY_EQUIVALENT_PLAN = NegotiationPlan()
"""The declared plan whose behavior matches the ADR-0055 coordinator."""


def ordered_net_ids(
    slot: NetOrderSlot,
    *,
    nets: tuple[tuple[str, int], ...],
    iteration: int,
    conflict_scores: Mapping[str, int],
    demand_cells: Mapping[str, int],
) -> tuple[str, ...]:
    """Return the deterministic net permutation for one iteration.

    ``nets`` is the canonical ``(net_id, seed)`` collection.  Every rule tie-breaks on that pair,
    so each returns a total order over distinct nets and never depends on input order.
    """

    if iteration < 1:
        raise ValueError("a negotiation iteration index starts at one")
    if slot.rule is NetOrderRule.STABLE_IDENTIFIER:
        keyed = sorted(nets)
    elif slot.rule is NetOrderRule.CONFLICT_DESCENDING:
        if iteration == 1:
            keyed = sorted(nets)
        else:
            keyed = sorted(nets, key=lambda item: (-conflict_scores.get(item[0], 0), *item))
    elif slot.rule is NetOrderRule.DEMAND_DESCENDING:
        keyed = sorted(nets, key=lambda item: (-demand_cells.get(item[0], 0), *item))
    else:
        keyed = sorted(nets, key=lambda item: (demand_cells.get(item[0], 0), *item))
    return tuple(net_id for net_id, _seed in keyed)


def next_history_value(slot: CostUpdateSlot, *, previous: int, overuse: int, cap: int) -> int:
    """Return the next integer history value for one exact congestion resource."""

    if isinstance(previous, bool) or not isinstance(previous, int) or previous < 0:
        raise ValueError("a history value must be a non-negative integer")
    if isinstance(overuse, bool) or not isinstance(overuse, int) or overuse < 0:
        raise ValueError("an overuse count must be a non-negative integer")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
        raise ValueError("a history cap must be a non-negative integer")
    if slot.rule is CostUpdateRule.ACCUMULATED_OVERUSE:
        value = previous + overuse
    elif slot.rule is CostUpdateRule.SCALED_ACCUMULATION:
        value = previous + slot.accumulation_weight * overuse
    else:
        value = (
            previous * slot.decay_numerator
        ) // slot.decay_denominator + slot.accumulation_weight * overuse
    return max(0, min(cap, value))


def next_present_penalty(slot: CostUpdateSlot, *, previous: int, cap: int) -> int:
    """Return the next present-congestion penalty after one iteration's growth step.

    Integer floor division means a zero penalty stays zero and a small penalty can stall below
    the growth ratio's resolution.  That is deliberate: the coordinator's costs are exact
    integers in nanometres, and a declared schedule must not invent fractional pressure.
    """

    if isinstance(previous, bool) or not isinstance(previous, int) or previous < 0:
        raise ValueError("a present penalty must be a non-negative integer")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
        raise ValueError("a present penalty cap must be a non-negative integer")
    grown = (previous * slot.present_growth_numerator) // slot.present_growth_denominator
    return max(0, min(cap, grown))


def ripup_net_ids(
    slot: RipUpSlot,
    *,
    nets: tuple[tuple[str, int], ...],
    conflict_scores: Mapping[str, int],
    retained: frozenset[str],
    window_nets: frozenset[str] = frozenset(),
) -> frozenset[str]:
    """Return the deterministic set of nets to re-route on the next iteration.

    ``retained`` names the nets that currently hold a usable candidate or connection.  Every net
    outside it is always ripped up: a net with nothing to keep must be retried under every rule,
    or the coordinator would silently stop trying to route it.

    ``window_nets`` names the nets the coordinator found inside the declared rip-up window of a
    conflicted net.  It is supplied rather than computed here because computing it needs board
    geometry, and this module is deliberately geometry-free — a slot decides *which* nets, never
    *where* they are.  Only :data:`RipUpRule.CONFLICT_WINDOW` reads it; supplying a non-empty
    window to any other rule is refused rather than ignored, so a caller cannot believe a window
    took effect when it did not.
    """

    seeds = dict(nets)
    if len(seeds) != len(nets):
        raise ValueError("negotiated rip-up selection requires distinct nets")
    if not retained <= seeds.keys():
        raise ValueError("a retained net is not part of this negotiated run")
    if not window_nets <= seeds.keys():
        raise ValueError("a windowed net is not part of this negotiated run")
    if window_nets and slot.rule is not RipUpRule.CONFLICT_WINDOW:
        raise ValueError("only the conflict-window rule reads a rip-up window")
    missing = frozenset(seeds) - retained
    if slot.rule is RipUpRule.ALL_NETS:
        return frozenset(seeds)
    conflicted = sorted(
        (
            (net_id, seed)
            for net_id, seed in nets
            if net_id in retained and conflict_scores.get(net_id, 0) > 0
        ),
        key=lambda item: (-conflict_scores.get(item[0], 0), *item),
    )
    if slot.rule is RipUpRule.TOP_CONFLICT_ONLY:
        conflicted = conflicted[: slot.max_ripup_nets]
    selected = missing | frozenset(net_id for net_id, _seed in conflicted)
    if slot.rule is RipUpRule.CONFLICT_WINDOW:
        # A windowed net is only worth ripping up if there is something to rip up; a net with no
        # candidate is already in `missing`.  Intersecting with `retained` keeps the returned set
        # a subset of the run's nets under every input.
        selected |= window_nets & retained
    return selected


__all__ = [
    "COST_UPDATE_SLOT_SCHEMA",
    "LEGACY_EQUIVALENT_PLAN",
    "MAX_NEGOTIATED_NETS",
    "MAX_RIPUP_WINDOW_CELLS",
    "MAX_SLOT_WEIGHT",
    "NEGOTIATION_PLAN_SCHEMA",
    "NET_ORDER_SLOT_SCHEMA",
    "NEUTRAL_WEIGHT",
    "NEUTRAL_WINDOW_CELLS",
    "RIP_UP_SLOT_SCHEMA",
    "CostUpdateRule",
    "CostUpdateSlot",
    "NegotiationPlan",
    "NetOrderRule",
    "NetOrderSlot",
    "RipUpRule",
    "RipUpSlot",
    "next_history_value",
    "next_present_penalty",
    "ordered_net_ids",
    "ripup_net_ids",
]
