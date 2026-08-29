"""Bounded, candidate-only negotiated-congestion routing.

This module is a deliberately small PathFinder-inspired coordinator around the deterministic
single-net A* backend.  It does not mutate a board, inject model-generated copper, or claim KiCad
DRC validity.  Present occupancy and historical overflow only influence the next A* search order;
every returned path remains an ordinary immutable route candidate that must pass the existing
verification/apply gates.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import Any, TypeAlias, cast

from copper_mcp.board_ir import (
    BoardIRSnapshot,
    BoardIRValidationError,
    Pad,
    PointNM,
    verify_snapshot,
)
from copper_mcp.routing.astar import AStarRouter, canonical_candidate_bytes, verify_candidate_id
from copper_mcp.routing.candidate_path_validator import (
    CandidatePathValidationFailure,
    validate_candidate_path,
)
from copper_mcp.routing.contracts import (
    SINGLE_PATH_ORDERING,
    CancellationCheck,
    RouteCandidate,
    RouteConnection,
    RouteCost,
    RouteDiagnostic,
    RouteFailureCode,
    RouteMetrics,
    RoutePatch,
    RoutePath,
    RouteRequest,
    RouteResult,
)
from copper_mcp.routing.negotiation_plan import (
    MAX_NEGOTIATED_NETS,
    NEGOTIATION_PLAN_SCHEMA,
    CostUpdateSlot,
    NegotiationPlan,
    RipUpRule,
    next_history_value,
    next_present_penalty,
    ordered_net_ids,
    ripup_net_ids,
)
from copper_mcp.routing.physical_clearance import (
    PhysicalClearanceFailure,
    verify_negotiated_physical_clearance,
)
from copper_mcp.routing.policy import (
    REFERENCE_POLICY_ID,
    DeterministicReferencePolicy,
    PolicyBounds,
    PolicyFactory,
    PolicyNet,
    RoutingPolicyDecision,
    RoutingPolicyInput,
    evaluate_policy,
    policy_decision_digest,
    policy_input_digest,
)
from copper_mcp.routing.policy_worker import evaluate_reference_policy_in_worker
from copper_mcp.routing.repair import (
    LocalRepairStatus,
    RepairTransactionSettings,
    exact_local_repair,
    verify_local_repair_result,
)
from copper_mcp.routing.repair_provenance import (
    CoordinatorRepairProvenance,
    derive_repair_provenance,
)
from copper_mcp.routing.spatial_index import (
    IncrementalSpatialIndex,
    bounds_intersect,
    inflate_bounds,
)

_EMPTY_DIGEST = f"sha256:{'0' * 64}"
_MAX_NETS = MAX_NEGOTIATED_NETS
_MIN_PADS_PER_NET = 2
_MAX_PADS_PER_NET = 32
_MAX_ITERATIONS = 32
_MAX_PENALTY_NM = 1_000_000_000
_MAX_HISTORY = 1_000_000
_MAX_DIAGNOSTIC = 256
_MAX_UNIT_RESOURCES = 2_000_000
# How many lattice steps one incremental-index cell spans.  Larger cells mean fewer buckets and
# more candidates per bucket; the exact predicate decides either way, so this trades work for
# memory and can never change which nets a window names.
_LEDGER_INDEX_CELL_STEPS = 4
_MAX_TOTAL_EXPANSIONS = 10_000_000
_MAX_TOTAL_OBSTACLE_CHECKS = 50_000_000
_MAX_TOTAL_PHYSICAL_CHECKS = 10_000_000
_SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
NEGOTIATED_ROUTER_VERSION = "negotiated-grid-0.2.0"
NEGOTIATED_ROUTING_POLICY = "negotiated-congestion-v2"
POLICY_NEGOTIATED_ROUTING_POLICY = "negotiated-congestion-policy-order-v3"
REFERENCE_POLICY_PROFILE = "deterministic-reference-v1"
ISOLATED_REFERENCE_POLICY_PROFILE = "deterministic-reference-worker-v1"
NEGOTIATED_POLICY_BINDING_SCHEMA = "copper-mcp.negotiated-policy-binding.v1"
NEGOTIATED_POLICY_EVIDENCE_SCHEMA = "copper-mcp.negotiated-policy-evidence.v1"
PLAN_NEGOTIATED_ROUTING_POLICY = "negotiated-congestion-plan-v4"
NEGOTIATION_PLAN_BINDING_SCHEMA = "copper-mcp.negotiation-plan-binding.v1"
NEGOTIATION_PLAN_EVIDENCE_SCHEMA = "copper-mcp.negotiation-plan-evidence.v1"
REPAIR_NEGOTIATED_ROUTING_POLICY = "negotiated-congestion-repair-v1"
NEGOTIATED_REPAIR_BINDING_SCHEMA = "copper-mcp.negotiated-repair-binding.v1"
NEGOTIATED_REPAIR_EVIDENCE_SCHEMA = "copper-mcp.negotiated-repair-evidence.v1"
_POLICY_ID = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_ISOLATED_POLICY_TIMEOUT_SECONDS = 1.0

# This private immutable registry admits only in-process profiles.  Together with the fixed
# isolated-worker branch in ``_evaluate_policy_profile``, it is the closed policy-admission
# boundary: callers cannot supply objects, callables, model adapters, remote evaluators, or worker
# commands through ``negotiate_routes``.
_POLICY_PROFILE_REGISTRY: Mapping[str, PolicyFactory] = MappingProxyType(
    {REFERENCE_POLICY_PROFILE: DeterministicReferencePolicy}
)
_EXPECTED_POLICY_IDS: Mapping[str, str] = MappingProxyType(
    {
        REFERENCE_POLICY_PROFILE: REFERENCE_POLICY_ID,
        ISOLATED_REFERENCE_POLICY_PROFILE: REFERENCE_POLICY_ID,
    }
)

ResourceKey: TypeAlias = tuple[str, PointNM, PointNM]


def _integer(name: str, value: int, *, minimum: int = 0, maximum: int = (1 << 53) - 1) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported integer range")


class NegotiatedRoutingStatus(StrEnum):
    """Terminal status for one bounded multi-net proposal run."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    NO_PATH = "no_path"
    INVALID_REQUEST = "invalid_request"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class NegotiatedRoutingRequest:
    """Immutable policy and request envelope for one multi-net routing run.

    The coordinator admits two to thirty-two selected-layer pads per net on one signal layer and
    one common grid step.  Each request keeps the private A* router's deterministic local lattice
    origin; absolute-world resources and the mandatory whole-set physical gate reconcile the
    requests without giving the coordinator a new geometry authority.
    """

    board_revision: str
    requests: tuple[RouteRequest, ...]
    max_iterations: int = 8
    present_penalty_nm: int = 20_000_000
    history_penalty_nm: int = 5_000_000
    max_total_expansions: int = 2_000_000
    max_total_obstacle_checks: int = 10_000_000
    max_total_physical_checks: int = 2_000_000

    def __post_init__(self) -> None:
        if not isinstance(self.board_revision, str) or not _SHA256.fullmatch(self.board_revision):
            raise ValueError("board revision must be a sha256 digest")
        if (
            not isinstance(self.requests, tuple)
            or not 2 <= len(self.requests) <= _MAX_NETS
            or not all(isinstance(item, RouteRequest) for item in self.requests)
        ):
            raise ValueError("negotiated routing requires an immutable bounded request tuple")
        _integer(
            "negotiated iteration budget", self.max_iterations, minimum=1, maximum=_MAX_ITERATIONS
        )
        _integer("present congestion penalty", self.present_penalty_nm, maximum=_MAX_PENALTY_NM)
        _integer("history congestion penalty", self.history_penalty_nm, maximum=_MAX_PENALTY_NM)
        _integer(
            "total expansion budget",
            self.max_total_expansions,
            minimum=1,
            maximum=_MAX_TOTAL_EXPANSIONS,
        )
        _integer(
            "total obstacle-check budget",
            self.max_total_obstacle_checks,
            minimum=1,
            maximum=_MAX_TOTAL_OBSTACLE_CHECKS,
        )
        _integer(
            "total physical-clearance budget",
            self.max_total_physical_checks,
            minimum=1,
            maximum=_MAX_TOTAL_PHYSICAL_CHECKS,
        )
        net_ids = [item.net_id for item in self.requests]
        if len(set(net_ids)) != len(net_ids):
            raise ValueError("negotiated requests must target distinct nets")
        if any(item.board_revision != self.board_revision for item in self.requests):
            raise ValueError("every negotiated request must bind the envelope board revision")
        layers = {item.layer_id for item in self.requests}
        grid_steps = {item.settings.grid_step_nm for item in self.requests}
        if len(layers) != 1 or len(grid_steps) != 1:
            raise ValueError("the first negotiated slice requires one layer and one grid step")

    @property
    def layer_id(self) -> str:
        """Return the common selected layer after constructor validation."""

        return self.requests[0].layer_id

    @property
    def grid_step_nm(self) -> int:
        """Return the common lattice step after constructor validation."""

        return self.requests[0].settings.grid_step_nm

    @property
    def policy_digest(self) -> str:
        """Return a content digest for the policy envelope, including all request limits."""

        payload = {
            "board_revision": self.board_revision,
            "history_penalty_nm": self.history_penalty_nm,
            "max_iterations": self.max_iterations,
            "max_total_expansions": self.max_total_expansions,
            "max_total_obstacle_checks": self.max_total_obstacle_checks,
            "max_total_physical_checks": self.max_total_physical_checks,
            "present_penalty_nm": self.present_penalty_nm,
            "requests": [
                {
                    "board_revision": request.board_revision,
                    "layer_id": request.layer_id,
                    "net_id": request.net_id,
                    "seed": request.seed,
                    "settings": {
                        "bend_penalty_nm": request.settings.bend_penalty_nm,
                        "grid_step_nm": request.settings.grid_step_nm,
                        "max_expansions": request.settings.max_expansions,
                        "max_grid_nodes": request.settings.max_grid_nodes,
                        "max_obstacle_checks": request.settings.max_obstacle_checks,
                        "max_obstacles": request.settings.max_obstacles,
                        "proximity_penalty_nm": request.settings.proximity_penalty_nm,
                    },
                }
                for request in sorted(self.requests, key=lambda item: (item.net_id, item.seed))
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True, order=True)
class CongestionResource:
    """One exact unit edge or lattice vertex with its present usage count."""

    kind: str
    start: PointNM
    end: PointNM
    usage: int

    def __post_init__(self) -> None:
        if self.kind not in {"edge", "vertex"}:
            raise ValueError("congestion resource kind is unsupported")
        if self.kind == "vertex" and self.start != self.end:
            raise ValueError("a vertex resource must have identical endpoints")
        if self.kind == "edge" and self.start == self.end:
            raise ValueError("an edge resource must have distinct endpoints")
        _integer("congestion resource usage", self.usage, minimum=2)


def _policy_binding_digest(envelope_digest: str, decision_digest: str) -> str:
    """Return the versioned candidate binding for one accepted policy decision."""

    payload = {
        "candidate_identity_policy": POLICY_NEGOTIATED_ROUTING_POLICY,
        "negotiated_envelope_digest": envelope_digest,
        "policy_decision_digest": decision_digest,
        "schema": NEGOTIATED_POLICY_BINDING_SCHEMA,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class NegotiatedPolicyEvidence:
    """Redacted binding evidence for one accepted, pre-routing policy decision."""

    envelope_digest: str
    input_digest: str
    decision_digest: str
    composite_digest: str
    policy_id: str
    policy_profile: str
    schema: str = NEGOTIATED_POLICY_EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        for name, value in (
            ("negotiated envelope digest", self.envelope_digest),
            ("policy input digest", self.input_digest),
            ("policy decision digest", self.decision_digest),
            ("policy composite digest", self.composite_digest),
        ):
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"{name} is malformed")
        if not isinstance(self.policy_id, str) or not _POLICY_ID.fullmatch(self.policy_id):
            raise ValueError("negotiated policy ID is malformed")
        if not isinstance(self.policy_profile, str) or not _POLICY_ID.fullmatch(
            self.policy_profile
        ):
            raise ValueError("negotiated policy profile is malformed")
        if self.schema != NEGOTIATED_POLICY_EVIDENCE_SCHEMA:
            raise ValueError("negotiated policy evidence schema is unsupported")
        if self.composite_digest != _policy_binding_digest(
            self.envelope_digest, self.decision_digest
        ):
            raise ValueError("negotiated policy composite digest is not bound to its evidence")


def _plan_binding_digest(envelope_digest: str, plan_digest: str) -> str:
    """Return the versioned candidate binding for one declared negotiation plan."""

    payload = {
        "candidate_identity_policy": PLAN_NEGOTIATED_ROUTING_POLICY,
        "negotiated_envelope_digest": envelope_digest,
        "negotiation_plan_digest": plan_digest,
        "schema": NEGOTIATION_PLAN_BINDING_SCHEMA,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _repair_binding_digest(
    envelope_digest: str,
    provenance_digest: str,
    local_route_digest: str,
    decision_digest: str | None,
) -> str:
    """Bind an accepted repair transaction without exposing its window or geometry."""

    payload = {
        "candidate_identity_policy": REPAIR_NEGOTIATED_ROUTING_POLICY,
        "local_route_digest": local_route_digest,
        "negotiated_envelope_digest": envelope_digest,
        "provenance_digest": provenance_digest,
        "repair_policy_decision_digest": decision_digest,
        "schema": NEGOTIATED_REPAIR_BINDING_SCHEMA,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class NegotiatedRepairEvidence:
    """Redacted, success-only evidence for one accepted local repair transaction."""

    envelope_digest: str
    provenance_digest: str
    local_request_digest: str
    local_route_digest: str
    composite_digest: str
    projection_obstacle_checks: int
    local_expanded_states: int
    validator_edge_checks: int
    validator_obstacle_checks: int
    repair_policy_decision_digest: str | None = None
    schema: str = NEGOTIATED_REPAIR_EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        for name, value in (
            ("negotiated envelope digest", self.envelope_digest),
            ("repair provenance digest", self.provenance_digest),
            ("local repair request digest", self.local_request_digest),
            ("local repair route digest", self.local_route_digest),
            ("repair composite digest", self.composite_digest),
        ):
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"{name} is malformed")
        if self.repair_policy_decision_digest is not None and (
            not isinstance(self.repair_policy_decision_digest, str)
            or not _SHA256.fullmatch(self.repair_policy_decision_digest)
        ):
            raise ValueError("repair policy decision digest is malformed")
        _integer("local repair expansions", self.local_expanded_states, maximum=4_096)
        _integer(
            "repair projection obstacle checks",
            self.projection_obstacle_checks,
            maximum=_MAX_TOTAL_OBSTACLE_CHECKS,
        )
        _integer("repair validator edge checks", self.validator_edge_checks, maximum=4_096)
        _integer(
            "repair validator obstacle checks",
            self.validator_obstacle_checks,
            maximum=_MAX_TOTAL_OBSTACLE_CHECKS,
        )
        if self.schema != NEGOTIATED_REPAIR_EVIDENCE_SCHEMA:
            raise ValueError("negotiated repair evidence schema is unsupported")
        if self.composite_digest != _repair_binding_digest(
            self.envelope_digest,
            self.provenance_digest,
            self.local_route_digest,
            self.repair_policy_decision_digest,
        ):
            raise ValueError("negotiated repair composite digest is not bound to its evidence")


def _composed_plan_digest(
    net_order_slot_digest: str, cost_update_slot_digest: str, rip_up_slot_digest: str
) -> str:
    """Recompose a plan digest from exactly its three published slot digests."""

    payload = {
        "cost_update_slot_digest": cost_update_slot_digest,
        "net_order_slot_digest": net_order_slot_digest,
        "rip_up_slot_digest": rip_up_slot_digest,
        "schema": NEGOTIATION_PLAN_SCHEMA,
    }
    encoded = json.dumps(
        payload, allow_nan=False, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class NegotiationPlanEvidence:
    """Redacted binding evidence for one accepted, declared negotiation plan.

    The three slot digests are published individually so a reader can see which slot changed
    between two runs.  The evidence re-derives the plan digest from exactly those three values and
    refuses itself when they do not compose to it, so a published plan digest can never name a
    slot combination other than the one it reports.
    """

    envelope_digest: str
    plan_digest: str
    net_order_slot_digest: str
    cost_update_slot_digest: str
    rip_up_slot_digest: str
    composite_digest: str
    schema: str = NEGOTIATION_PLAN_EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        for name, value in (
            ("negotiated envelope digest", self.envelope_digest),
            ("negotiation plan digest", self.plan_digest),
            ("net-order slot digest", self.net_order_slot_digest),
            ("cost-update slot digest", self.cost_update_slot_digest),
            ("rip-up slot digest", self.rip_up_slot_digest),
            ("negotiation plan composite digest", self.composite_digest),
        ):
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"{name} is malformed")
        if self.schema != NEGOTIATION_PLAN_EVIDENCE_SCHEMA:
            raise ValueError("negotiation plan evidence schema is unsupported")
        if self.plan_digest != _composed_plan_digest(
            self.net_order_slot_digest, self.cost_update_slot_digest, self.rip_up_slot_digest
        ):
            raise ValueError("the negotiation plan digest is not composed of its own slot digests")
        if self.composite_digest != _plan_binding_digest(self.envelope_digest, self.plan_digest):
            raise ValueError("negotiation plan composite digest is not bound to its evidence")


@dataclass(frozen=True, slots=True)
class NegotiatedRoutingResult:
    """Immutable evidence from one bounded negotiated-routing replay."""

    status: NegotiatedRoutingStatus
    board_revision: str
    candidates: tuple[RouteCandidate, ...] = ()
    connections: tuple[RouteConnection, ...] = ()
    unrouted_nets: tuple[str, ...] = ()
    iterations: int = 0
    ripups: int = 0
    overflow_resources: tuple[CongestionResource, ...] = ()
    overflow_units: int = 0
    total_wire_length_nm: int = 0
    total_physical_checks: int = 0
    diagnostic: str | None = None
    policy_digest: str = _EMPTY_DIGEST

    def __post_init__(self) -> None:
        if not isinstance(self.status, NegotiatedRoutingStatus):
            raise ValueError("negotiated status is malformed")
        if not isinstance(self.board_revision, str) or not _SHA256.fullmatch(self.board_revision):
            raise ValueError("negotiated result revision is malformed")
        if (
            not isinstance(self.candidates, tuple)
            or not all(isinstance(item, RouteCandidate) for item in self.candidates)
            or tuple(sorted(self.candidates, key=lambda item: item.patch.net_id)) != self.candidates
        ):
            raise ValueError("negotiated candidates must be sorted immutable route candidates")
        if len({item.patch.net_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("negotiated candidates must target distinct nets")
        if not isinstance(self.connections, tuple) or not all(
            isinstance(item, RouteConnection) for item in self.connections
        ):
            raise ValueError("negotiated connections are malformed")
        if (
            not isinstance(self.unrouted_nets, tuple)
            or not all(isinstance(item, str) for item in self.unrouted_nets)
            or tuple(sorted(self.unrouted_nets)) != self.unrouted_nets
            or len(set(self.unrouted_nets)) != len(self.unrouted_nets)
        ):
            raise ValueError("unrouted net IDs must be sorted")
        _integer("negotiated iterations", self.iterations, maximum=_MAX_ITERATIONS)
        _integer("negotiated ripups", self.ripups)
        if (
            not isinstance(self.overflow_resources, tuple)
            or not all(isinstance(item, CongestionResource) for item in self.overflow_resources)
            or tuple(sorted(self.overflow_resources)) != self.overflow_resources
        ):
            raise ValueError("overflow resources must be sorted immutable evidence")
        _integer("negotiated overflow units", self.overflow_units)
        _integer("negotiated wire length", self.total_wire_length_nm)
        _integer("negotiated physical-clearance checks", self.total_physical_checks)
        if self.diagnostic is not None and (
            not isinstance(self.diagnostic, str) or not 1 <= len(self.diagnostic) <= _MAX_DIAGNOSTIC
        ):
            raise ValueError("negotiated diagnostic is malformed")
        if not isinstance(self.policy_digest, str) or not _SHA256.fullmatch(self.policy_digest):
            raise ValueError("negotiated policy digest is malformed")
        if self.overflow_units != sum(item.usage - 1 for item in self.overflow_resources):
            raise ValueError("overflow units must equal the exact resource excess")
        if self.total_wire_length_nm != sum(item.patch.length_nm for item in self.candidates):
            raise ValueError("negotiated wire length must match candidate geometry")

    @property
    def ok(self) -> bool:
        """Return true only for a fully routed, structurally conflict-free proposal."""

        return self.status is NegotiatedRoutingStatus.COMPLETED and not self.overflow_resources


@dataclass(frozen=True, slots=True)
class PolicyNegotiatedRoutingResult(NegotiatedRoutingResult):
    """A negotiated result with accepted closed-policy provenance.

    The legacy base result deliberately has no optional evidence field so its dataclass fields,
    repr, and wire representation remain byte-for-byte compatible when no profile is selected.
    """

    policy_evidence: NegotiatedPolicyEvidence | None = None

    def __post_init__(self) -> None:
        super(PolicyNegotiatedRoutingResult, self).__post_init__()
        if not isinstance(self.policy_evidence, NegotiatedPolicyEvidence):
            raise ValueError("negotiated policy evidence is malformed")
        if self.policy_evidence.envelope_digest != self.policy_digest:
            raise ValueError("negotiated policy evidence has a different envelope digest")


@dataclass(frozen=True, slots=True)
class PlanNegotiatedRoutingResult(NegotiatedRoutingResult):
    """A negotiated result produced under one accepted, declared negotiation plan.

    Like the policy-enabled subtype, this is a separate shape rather than an optional field on the
    base result, so a no-plan run's serialization stays byte-for-byte what it always was.
    """

    plan_evidence: NegotiationPlanEvidence | None = None

    def __post_init__(self) -> None:
        super(PlanNegotiatedRoutingResult, self).__post_init__()
        if not isinstance(self.plan_evidence, NegotiationPlanEvidence):
            raise ValueError("negotiation plan evidence is malformed")
        if self.plan_evidence.envelope_digest != self.policy_digest:
            raise ValueError("negotiation plan evidence has a different envelope digest")


@dataclass(frozen=True, slots=True)
class RepairNegotiatedRoutingResult(NegotiatedRoutingResult):
    """A completed allocation whose replacement path passed the repair transaction gates."""

    repair_evidence: NegotiatedRepairEvidence | None = None

    def __post_init__(self) -> None:
        super(RepairNegotiatedRoutingResult, self).__post_init__()
        if self.status is not NegotiatedRoutingStatus.COMPLETED:
            raise ValueError("repair evidence is published only with a completed allocation")
        if not isinstance(self.repair_evidence, NegotiatedRepairEvidence):
            raise ValueError("negotiated repair evidence is malformed")
        if self.repair_evidence.envelope_digest != self.policy_digest:
            raise ValueError("negotiated repair evidence has a different envelope digest")


def _published_result(
    policy_evidence: NegotiatedPolicyEvidence | None,
    plan_evidence: NegotiationPlanEvidence | None = None,
    repair_evidence: NegotiatedRepairEvidence | None = None,
    **values: object,
) -> NegotiatedRoutingResult:
    """Construct the legacy, policy-enabled, or plan-enabled immutable result shape."""

    if sum(item is not None for item in (policy_evidence, plan_evidence, repair_evidence)) > 1:
        raise ValueError("a negotiated result carries at most one provenance shape")
    if repair_evidence is not None:
        return RepairNegotiatedRoutingResult(**cast(Any, values), repair_evidence=repair_evidence)
    if plan_evidence is not None:
        return PlanNegotiatedRoutingResult(**cast(Any, values), plan_evidence=plan_evidence)
    if policy_evidence is None:
        return NegotiatedRoutingResult(**cast(Any, values))
    return PolicyNegotiatedRoutingResult(**cast(Any, values), policy_evidence=policy_evidence)


class CongestionLedger:
    """Mutable per-replay occupancy overlay with bounded integer penalties.

    Occupancy is an *additive per-net integer count* over exact lattice resources, so removing one
    net's contribution is exact subtraction rather than an approximation.  That is what lets
    :meth:`retain_only` replace the historic "clear everything, then re-add every retained
    candidate" reconstruction without moving a single published byte: the two paths reach the same
    counters, and the counters are the only thing the router ever reads.  ADR-0081 records the
    argument; ``docs/research/incremental-spatial-index-v1.md`` §1.3 records why an integer
    lattice makes the exact answer and the indexed answer the same answer.

    A conservative :class:`IncrementalSpatialIndex` over each added candidate's copper envelope is
    maintained alongside the counters.  It exists for the bounded rip-up window and for nothing
    else: it never decides a penalty, an overflow, or a conflict score.
    """

    def __init__(
        self,
        *,
        grid_step_nm: int,
        present_penalty_nm: int,
        history_penalty_nm: int,
    ) -> None:
        _integer("grid step", grid_step_nm, minimum=1)
        _integer("present penalty", present_penalty_nm, maximum=_MAX_PENALTY_NM)
        _integer("history penalty", history_penalty_nm, maximum=_MAX_PENALTY_NM)
        self.grid_step_nm = grid_step_nm
        self.present_penalty_nm = present_penalty_nm
        self.history_penalty_nm = history_penalty_nm
        self._present: Counter[ResourceKey] = Counter()
        self._history: Counter[ResourceKey] = Counter()
        self._nets: dict[ResourceKey, Counter[str]] = defaultdict(Counter)
        self._resources: dict[str, frozenset[ResourceKey]] = {}
        self._index: IncrementalSpatialIndex = IncrementalSpatialIndex(
            cell_size_nm=grid_step_nm * _LEDGER_INDEX_CELL_STEPS,
            max_entries=_MAX_NETS,
        )
        # Monotonic counters for differential benchmarks.  Nothing in the routing result, the
        # candidate identity, or any published response reads them.
        self.resource_insertions = 0
        self.resource_removals = 0
        self.reconstruction_operations = 0

    @property
    def added_nets(self) -> frozenset[str]:
        """Return the nets whose candidates currently occupy the ledger."""

        return frozenset(self._resources)

    @property
    def live_resource_count(self) -> int:
        """Return how many distinct resources the present overlay currently tracks.

        This exists so bounded memory is testable.  A subtractive reconstruction that left a
        resource behind at a count of zero would be invisible in every published output — every
        reader of the present overlay filters on ``usage > 1`` or reads a ``Counter`` whose default
        is already zero — while the overlay grew without bound across passes.  An incrementally
        retained ledger must track exactly as many resources as a rebuilt one.
        """

        return len(self._present)

    def clear_present(self) -> None:
        """Rip up the current iteration while retaining historical overflow pressure."""

        self._present.clear()
        self._nets.clear()
        self._resources.clear()
        self._index.clear()

    def add_candidate(self, candidate: RouteCandidate) -> None:
        """Add one candidate's distinct unit resources to present occupancy."""

        keys = _candidate_resources(candidate, self.grid_step_nm)
        if len(keys) > _MAX_UNIT_RESOURCES:
            raise ValueError("candidate resource expansion exceeds the bounded ledger budget")
        net_id = candidate.patch.net_id
        if net_id in self._resources:
            raise ValueError("the congestion ledger accepts one candidate per net per iteration")
        self._resources[net_id] = frozenset(keys)
        for key in keys:
            self._present[key] += 1
            self._nets[key][net_id] += 1
        self.resource_insertions += len(keys)
        self._index.insert(net_id, _candidate_bounds(candidate))

    def remove_net(self, net_id: str) -> None:
        """Remove exactly one net's occupancy, leaving every other net's counters untouched.

        The inverse of :meth:`add_candidate` down to the emptied keys: a resource whose count
        reaches zero is deleted rather than left at zero.  B-095's mutation check established what
        that guard is and is not for.  It is *not* an output guard — every reader of the present
        overlay filters on ``usage > 1`` or reads a ``Counter`` whose default is already zero, so
        a lingering zero changes nothing observable.  It is a **memory** guard: without it the
        overlay accumulates dead keys pass after pass and a long negotiation grows without bound.
        :attr:`live_resource_count` is what makes that testable.
        """

        if net_id not in self._resources:
            raise ValueError("the congestion ledger cannot remove a net it does not hold")
        keys = self._resources.pop(net_id)
        for key in keys:
            remaining = self._present[key] - 1
            if remaining:
                self._present[key] = remaining
            else:
                del self._present[key]
            occupants = self._nets[key]
            net_remaining = occupants[net_id] - 1
            if net_remaining:
                occupants[net_id] = net_remaining
            else:
                del occupants[net_id]
            if not occupants:
                del self._nets[key]
        self.resource_removals += len(keys)
        self._index.remove(net_id)

    def retain_only(self, net_ids: frozenset[str]) -> None:
        """Rip up every held net outside ``net_ids``, keeping the rest in place.

        This replaces ``clear_present()`` followed by re-adding every retained candidate, and it
        deliberately does **not** always subtract.  Measurement (B-095) showed that subtracting
        the departures is cheaper only when there are fewer of them than there are survivors; a
        pass that rips up almost everything pays more to subtract than to re-count.  So the
        reconstruction costs ``min(ripped-up units, retained units)`` — never more unit work than
        either single strategy, and never more than the path it replaces, which always paid the
        retained units *and* re-derived each retained candidate's resources from geometry because
        the coordinator handed back candidates rather than cached resource sets.

        One honest exception, measured rather than assumed: retaining *nothing* does no unit work
        either way, and the bookkeeping to decide that costs a few microseconds the bare
        ``clear_present()`` did not.  B-095 records up to 22% slower there, on an operation whose
        absolute cost is single-digit microseconds.

        All three branches reach the same counters.  The choice is arithmetic, not semantics.
        """

        held = frozenset(self._resources)
        dropped = sorted(held - net_ids)
        if not dropped:
            return
        kept = sorted(held & net_ids)
        if not kept:
            # Retaining nothing is a full rip-up, and a bare clear is already the cheapest correct
            # reconstruction for it.  Measured, not assumed: without this branch, walking the held
            # nets to drop them individually was 60-130% *slower* than the path it replaces.
            self.clear_present()
            return
        dropped_units = sum(len(self._resources[net_id]) for net_id in dropped)
        kept_units = sum(len(self._resources[net_id]) for net_id in kept)
        self.reconstruction_operations += min(dropped_units, kept_units)
        if dropped_units <= kept_units:
            for net_id in dropped:
                self.remove_net(net_id)
            return
        for net_id in dropped:
            del self._resources[net_id]
            self._index.remove(net_id)
        self._present.clear()
        self._nets.clear()
        for net_id in kept:
            for key in self._resources[net_id]:
                self._present[key] += 1
                self._nets[key][net_id] += 1

    def nets_within_window(self, seeds: frozenset[str], *, window_nm: int) -> frozenset[str]:
        """Return every held net other than a seed whose copper lies within ``window_nm`` of one.

        The index narrows the candidates; the exact integer rectangle predicate decides
        membership.  That ordering is deliberate — it makes the returned set a function of the
        stored envelopes alone, so changing the index's cell size, its capacity, or its oversize
        fallback can change how fast this runs and can never change which nets it names.
        """

        _integer("rip-up window", window_nm, minimum=0)
        selected: set[str] = set()
        for seed in sorted(seeds & frozenset(self._resources)):
            window = inflate_bounds(self._index.bounds_of(seed), window_nm)
            for net_id in self._index.query(window):
                if net_id != seed and bounds_intersect(self._index.bounds_of(net_id), window):
                    selected.add(net_id)
        return frozenset(selected) - seeds

    def penalty(self, start: PointNM, end: PointNM) -> int:
        """Return a bounded non-negative search-ordering penalty for one proposed lattice edge."""

        edge = _edge_key(start, end)
        destination = _vertex_key(end)
        value = (
            self._present[edge] * self.present_penalty_nm
            + self._history[edge] * self.history_penalty_nm
            + self._present[destination] * self.present_penalty_nm
            + self._history[destination] * self.history_penalty_nm
        )
        return min(value, _MAX_PENALTY_NM)

    def update_history(self, slot: CostUpdateSlot | None = None) -> None:
        """Move history counters by the declared cost-update rule, capped and bounded.

        The default reproduces the ADR-0055 rule exactly: accumulate only exact overuse units.  A
        rule that decays must also visit resources that were *not* overused this pass, so those
        keys are enumerated from the accumulated history rather than from present occupancy.
        """

        if slot is None:
            for key, usage in sorted(self._present.items()):
                if usage > 1:
                    self._history[key] = min(_MAX_HISTORY, self._history[key] + usage - 1)
            return
        keys = set(self._present)
        if slot.decays_unused_resources:
            keys |= set(self._history)
        for key in sorted(keys):
            overuse = max(0, self._present[key] - 1)
            value = next_history_value(
                slot, previous=self._history[key], overuse=overuse, cap=_MAX_HISTORY
            )
            if value:
                self._history[key] = value
            else:
                self._history.pop(key, None)

    def apply_present_growth(self, slot: CostUpdateSlot) -> None:
        """Advance the present-congestion penalty by the declared growth schedule."""

        self.present_penalty_nm = next_present_penalty(
            slot, previous=self.present_penalty_nm, cap=_MAX_PENALTY_NM
        )

    def overflow_resources(self) -> tuple[CongestionResource, ...]:
        """Return sorted exact resources used by more than one distinct route."""

        return tuple(
            CongestionResource(kind=key[0], start=key[1], end=key[2], usage=usage)
            for key, usage in sorted(self._present.items())
            if usage > 1
        )

    def conflict_scores(self) -> dict[str, int]:
        """Return deterministic per-net conflict scores for the next rip-up order."""

        scores: Counter[str] = Counter()
        # Sorted rather than in mapping order: an incrementally maintained ledger and a rebuilt
        # one hold the same keys in different insertion orders, and this is the one accumulation
        # whose result is a mapping rather than a sorted sequence.  Sorting makes the returned
        # dict's own order identical too, so the equivalence holds byte for byte and not merely
        # under `==`.
        for key in sorted(self._present):
            usage = self._present[key]
            if usage <= 1:
                continue
            for net_id in sorted(self._nets[key]):
                scores[net_id] += (usage - 1) * self._nets[key][net_id]
        return dict(scores)


def _vertex_key(point: PointNM) -> ResourceKey:
    return ("vertex", point, point)


def _edge_key(start: PointNM, end: PointNM) -> ResourceKey:
    if start == end or (start.x != end.x and start.y != end.y):
        raise ValueError("congestion resources require non-zero orthogonal edges")
    if (end.x, end.y) < (start.x, start.y):
        start, end = end, start
    return ("edge", start, end)


def _path_resources(path_vertices: tuple[PointNM, ...], grid_step_nm: int) -> set[ResourceKey]:
    keys: set[ResourceKey] = set()
    for start, end in pairwise(path_vertices):
        if start.x != end.x and start.y != end.y:
            raise ValueError("negotiated routing only accepts orthogonal paths")
        delta = abs(end.x - start.x) + abs(end.y - start.y)
        if delta == 0 or delta % grid_step_nm != 0:
            raise ValueError("route geometry is not aligned to the negotiated grid")
        steps = delta // grid_step_nm
        dx = 0 if start.x == end.x else (1 if end.x > start.x else -1)
        dy = 0 if start.y == end.y else (1 if end.y > start.y else -1)
        for index in range(steps):
            left = PointNM(start.x + index * grid_step_nm * dx, start.y + index * grid_step_nm * dy)
            right = PointNM(
                start.x + (index + 1) * grid_step_nm * dx,
                start.y + (index + 1) * grid_step_nm * dy,
            )
            keys.add(_edge_key(left, right))
            keys.add(_vertex_key(left))
            keys.add(_vertex_key(right))
    return keys


def _candidate_resources(candidate: RouteCandidate, grid_step_nm: int) -> set[ResourceKey]:
    resources: set[ResourceKey] = set()
    for path in candidate.patch.paths:
        resources.update(_path_resources(path.vertices, grid_step_nm))
    return resources


def _candidate_bounds(candidate: RouteCandidate) -> tuple[int, int, int, int]:
    """Return the exact integer copper envelope of one candidate's whole patch.

    Track centre lines are inflated by the rounded-up half width, so the rectangle covers the
    copper rather than the centre line.  Rounding *up* keeps the envelope an over-approximation,
    which is the direction every obstacle bound in this repository is required to round.
    """

    vertices = [point for path in candidate.patch.paths for point in path.vertices]
    if not vertices:
        raise ValueError("a route candidate patch carries no path vertices")
    half_width_nm = (candidate.patch.width_nm + 1) // 2
    return (
        min(point.x for point in vertices) - half_width_nm,
        min(point.y for point in vertices) - half_width_nm,
        max(point.x for point in vertices) + half_width_nm,
        max(point.y for point in vertices) + half_width_nm,
    )


def _invalid_result(
    message: str, *, board_revision: str = _EMPTY_DIGEST
) -> NegotiatedRoutingResult:
    return NegotiatedRoutingResult(
        status=NegotiatedRoutingStatus.INVALID_REQUEST,
        board_revision=board_revision,
        diagnostic=message,
    )


def _cancelled(cancelled: CancellationCheck | None) -> bool:
    if cancelled is None:
        return False
    try:
        return bool(cancelled())
    except Exception:  # pragma: no cover - fail closed for an untrusted cancellation boundary
        return True


def _request_pads(snapshot: BoardIRSnapshot, request: RouteRequest) -> tuple[Pad, ...] | None:
    """Return the exact selected-layer pad identities for one negotiated request."""

    pads = tuple(
        sorted(
            (
                pad
                for pad in snapshot.content.pads
                if pad.net_id == request.net_id and request.layer_id in pad.layer_ids
            ),
            key=lambda pad: pad.id,
        )
    )
    if not _MIN_PADS_PER_NET <= len(pads) <= _MAX_PADS_PER_NET:
        return None
    return pads


def _request_pad_centres(
    snapshot: BoardIRSnapshot, request: RouteRequest
) -> tuple[PointNM, ...] | None:
    pads = _request_pads(snapshot, request)
    return None if pads is None else tuple(pad.center for pad in pads)


def _requested_track_width(snapshot: BoardIRSnapshot, request: RouteRequest) -> int | None:
    """Resolve the one immutable net-class width that the request is allowed to emit."""

    classes = {item.id: item.track_width_nm for item in snapshot.content.constraints.net_classes}
    assignment = next(
        (
            item
            for item in snapshot.content.constraints.assignments
            if item.net_id == request.net_id
        ),
        None,
    )
    return None if assignment is None else classes.get(assignment.net_class_id)


def _candidate_is_bound(
    candidate: object,
    snapshot: BoardIRSnapshot,
    request: RouteRequest,
) -> bool:
    """Prove a pluggable candidate still belongs to this exact bounded invocation.

    The generic router seam is an untrusted boundary.  A candidate ID binds every canonical
    geometry byte and metadata field, while this check binds those bytes to the current immutable
    snapshot, the *bounded* request settings, and the selected net's exact pad identities.
    It deliberately runs before accounting, re-identification, or ledger mutation.
    """

    try:
        if not isinstance(candidate, RouteCandidate):
            return False
        pads = _request_pads(snapshot, request)
        expected_width = _requested_track_width(snapshot, request)
        if pads is None or expected_width is None:
            return False
        if (
            candidate.base_revision != snapshot.snapshot_digest
            or candidate.base_revision != request.board_revision
            or candidate.patch.net_id != request.net_id
            or candidate.patch.layer_id != request.layer_id
            or candidate.patch.width_nm != expected_width
            or candidate.fill_binding is not None
            or candidate.seed != request.seed
            or candidate.settings != request.settings
            or candidate.start_pad_id != pads[0].id
            or candidate.end_pad_id != pads[-1].id
            or candidate.pad_count != len(pads)
            or (
                len(pads) == 2
                and (
                    candidate.ordering_policy != SINGLE_PATH_ORDERING
                    or len(candidate.patch.paths) != 1
                )
            )
        ):
            return False
        # ``candidate_id`` covers the unmodified patch geometry, cost, metrics, route settings,
        # and endpoint IDs.  Re-identifying first would otherwise let a backend smuggle a forged
        # or stale payload behind a newly computed negotiated ID.
        verify_candidate_id(candidate)
    except Exception:  # pragma: no cover - an in-process router is an untrusted boundary
        return False
    return True


def _connection_is_bound(
    connection: object,
    snapshot: BoardIRSnapshot,
    request: RouteRequest,
) -> bool:
    """Verify the limited identity and work evidence available for an already-connected net."""

    try:
        if not isinstance(connection, RouteConnection):
            return False
        pads = _request_pads(snapshot, request)
        if pads is None:
            return False
        return (
            connection.base_revision == snapshot.snapshot_digest
            and connection.base_revision == request.board_revision
            and connection.start_pad_id == pads[0].id
            and connection.end_pad_id == pads[-1].id
            and connection.pad_count == len(pads)
            and not isinstance(connection.obstacle_checks, bool)
            and 0 <= connection.obstacle_checks <= request.settings.max_obstacle_checks
        )
    except Exception:  # pragma: no cover - an in-process router is an untrusted boundary
        return False


def _diagnostic_is_bounded(diagnostic: object, request: RouteRequest) -> bool:
    """Accept only typed failure accounting that fits this clipped request budget."""

    try:
        if not isinstance(diagnostic, RouteDiagnostic):
            return False
        return (
            not isinstance(diagnostic.expanded_states, bool)
            and not isinstance(diagnostic.obstacle_checks, bool)
            and 0 <= diagnostic.expanded_states <= request.settings.max_expansions
            and 0 <= diagnostic.obstacle_checks <= request.settings.max_obstacle_checks
        )
    except Exception:  # pragma: no cover - an in-process router is an untrusted boundary
        return False


def _router_result_is_bound(
    result: object,
    snapshot: BoardIRSnapshot,
    request: RouteRequest,
) -> bool:
    """Validate one generic-router outcome before accepting any of its work accounting."""

    try:
        if not isinstance(result, RouteResult):
            return False
        present = (result.candidate, result.connected, result.diagnostic)
        if sum(item is not None for item in present) != 1:
            return False
        if result.candidate is not None:
            return _candidate_is_bound(result.candidate, snapshot, request)
        if result.connected is not None:
            return _connection_is_bound(result.connected, snapshot, request)
        return _diagnostic_is_bounded(result.diagnostic, request)
    except Exception:  # pragma: no cover - an in-process router is an untrusted boundary
        return False


def _results_are_semantically_equal(left: RouteResult, right: RouteResult) -> bool:
    """Compare trusted route semantics while allowing backend labels before re-identification."""

    try:
        if left.candidate is not None and right.candidate is not None:
            return (
                left.candidate.base_revision == right.candidate.base_revision
                and left.candidate.start_pad_id == right.candidate.start_pad_id
                and left.candidate.end_pad_id == right.candidate.end_pad_id
                and left.candidate.patch == right.candidate.patch
                and left.candidate.cost == right.candidate.cost
                and left.candidate.metrics == right.candidate.metrics
                and left.candidate.settings == right.candidate.settings
                and left.candidate.seed == right.candidate.seed
                and left.candidate.pad_count == right.candidate.pad_count
                and left.candidate.ordering_policy == right.candidate.ordering_policy
                and left.candidate.fill_binding == right.candidate.fill_binding
            )
        if left.connected is not None and right.connected is not None:
            return left.connected == right.connected
        if left.diagnostic is not None and right.diagnostic is not None:
            return left.diagnostic == right.diagnostic
    except Exception:  # pragma: no cover - an in-process router is an untrusted boundary
        return False
    return False


def _result_work(result: RouteResult) -> tuple[int, int]:
    """Return already-validated deterministic work counts for one router invocation."""

    if result.candidate is not None:
        return result.candidate.metrics.expanded_states, result.candidate.metrics.obstacle_checks
    if result.connected is not None:
        return 0, result.connected.obstacle_checks
    assert result.diagnostic is not None
    return result.diagnostic.expanded_states, result.diagnostic.obstacle_checks


def _is_exact_reference_router(router: object) -> bool:
    """Return true only for an unmodified built-in reference-router method."""

    try:
        return (
            type(router) is AStarRouter
            and getattr(router.propose, "__func__", None) is AStarRouter.propose
        )
    except Exception:  # pragma: no cover - router dispatch is an untrusted boundary
        return False


def _router_boundary_failure(
    envelope: NegotiatedRoutingRequest,
    ordered: tuple[RouteRequest, ...],
    *,
    iterations: int,
    ripups: int,
    total_physical_checks: int,
    policy_evidence: NegotiatedPolicyEvidence | None = None,
    plan_evidence: NegotiationPlanEvidence | None = None,
) -> NegotiatedRoutingResult:
    """Fail closed without preserving a prefix from an unbound router invocation."""

    return _published_result(
        policy_evidence,
        plan_evidence,
        status=NegotiatedRoutingStatus.INVALID_REQUEST,
        board_revision=envelope.board_revision,
        candidates=(),
        connections=(),
        unrouted_nets=tuple(item.net_id for item in ordered),
        iterations=iterations,
        ripups=ripups,
        overflow_resources=(),
        overflow_units=0,
        total_wire_length_nm=0,
        total_physical_checks=total_physical_checks,
        diagnostic="the negotiated router result failed identity validation",
        policy_digest=envelope.policy_digest,
    )


def _validate_snapshot_requests(
    snapshot: BoardIRSnapshot, envelope: NegotiatedRoutingRequest
) -> str | None:
    try:
        verify_snapshot(snapshot)
    except (BoardIRValidationError, TypeError, ValueError):
        return "the Board IR snapshot failed canonical verification"
    if snapshot.snapshot_digest != envelope.board_revision:
        return "the negotiated request is stale against the immutable board revision"
    for request in sorted(envelope.requests, key=lambda item: (item.net_id, item.seed)):
        centres = _request_pad_centres(snapshot, request)
        if centres is None:
            return "each negotiated net must expose 2 to 32 pads on the selected layer"
    return None


def _pad_demand_cells(centres: tuple[PointNM, ...], grid_step_nm: int) -> int:
    """Return a deterministic O(pads) bounding-box demand in whole lattice cells.

    The two-axis span is the former Manhattan endpoint distance for every admitted two-pin
    request.  Rounding up additionally keeps a request-local shifted lattice from understating
    demand without changing any legacy two-pin value, whose endpoints were grid-congruent.
    """

    span_nm = (
        max(point.x for point in centres)
        - min(point.x for point in centres)
        + max(point.y for point in centres)
        - min(point.y for point in centres)
    )
    cells = (span_nm + grid_step_nm - 1) // grid_step_nm
    return max(1, min(cells, 1_000_000))


def _net_demand_cells(
    snapshot: BoardIRSnapshot, envelope: NegotiatedRoutingRequest
) -> dict[str, int]:
    """Return each net's deterministic bounding-box demand in whole lattice cells.

    This is the same bounded, pre-routing feature the closed policy input already carries.  It is
    derived by the coordinator from the verified snapshot, never supplied by a caller.  The
    two-pin result is byte-for-byte identical to the former endpoint-distance calculation.
    """

    demand: dict[str, int] = {}
    for request in envelope.requests:
        centres = _request_pad_centres(snapshot, request)
        if centres is None:
            raise ValueError("validated negotiated pads are unavailable")
        demand[request.net_id] = _pad_demand_cells(centres, envelope.grid_step_nm)
    return demand


def _derive_policy_input(
    snapshot: BoardIRSnapshot, envelope: NegotiatedRoutingRequest
) -> RoutingPolicyInput:
    """Derive the closed, neutral, pre-routing feature view owned by the coordinator."""

    nets: list[PolicyNet] = []
    for request in sorted(envelope.requests, key=lambda item: (item.net_id, item.seed)):
        centres = _request_pad_centres(snapshot, request)
        # The request has already passed ``_validate_snapshot_requests``.  Keep this guard so a
        # future caller cannot turn a stale assumption into an exception at the policy boundary.
        if centres is None:
            raise ValueError("validated negotiated pads are unavailable")
        nets.append(
            PolicyNet(
                net_id=request.net_id,
                criticality=0,
                demand_cells=_pad_demand_cells(centres, envelope.grid_step_nm),
                congestion_score=0,
            )
        )
    return RoutingPolicyInput(
        board_revision=envelope.board_revision,
        bounds=PolicyBounds(0, 0, 0, 0),
        nets=tuple(nets),
    )


def _evaluate_policy_profile(
    profile: str,
    policy_input: RoutingPolicyInput,
    *,
    cancelled: CancellationCheck | None,
) -> RoutingPolicyDecision:
    """Evaluate one private profile without admitting caller-selected worker authority.

    The isolated profile is a separate, fixed profile name rather than a factory supplied by the
    caller.  Its worker receives the same neutral, coordinator-derived order-only input as the
    in-process reference profile.  It cannot carry windows or any geometry/copper capability.
    """

    if profile == ISOLATED_REFERENCE_POLICY_PROFILE:
        return evaluate_reference_policy_in_worker(
            policy_input,
            timeout_seconds=_ISOLATED_POLICY_TIMEOUT_SECONDS,
            cancelled=cancelled,
        )
    policy_factory = _POLICY_PROFILE_REGISTRY.get(profile)
    if policy_factory is None:
        raise ValueError("unknown negotiated policy profile")
    return evaluate_policy(policy_factory(), policy_input)


def _policy_cancelled_result(envelope: NegotiatedRoutingRequest) -> NegotiatedRoutingResult:
    """Return the atomic pre-routing cancellation result without policy provenance."""

    return NegotiatedRoutingResult(
        status=NegotiatedRoutingStatus.CANCELLED,
        board_revision=envelope.board_revision,
        unrouted_nets=tuple(sorted(item.net_id for item in envelope.requests)),
        diagnostic="negotiated routing was cancelled before the next bounded iteration",
        policy_digest=envelope.policy_digest,
    )


def _reidentify_candidate(
    candidate: RouteCandidate,
    binding_digest: str,
    *,
    identity_policy: str = NEGOTIATED_ROUTING_POLICY,
    truncated: bool = True,
) -> RouteCandidate:
    """Bind each accepted path to the negotiated policy envelope before publication."""

    digest_suffix = binding_digest.removeprefix("sha256:")
    if truncated:
        # Keep historic no-policy candidate identities and labels byte-for-byte compatible.
        digest_suffix = digest_suffix[:16]
    policy = f"{identity_policy}-{digest_suffix}"
    marked = replace(
        candidate,
        candidate_id=_EMPTY_DIGEST,
        router_version=NEGOTIATED_ROUTER_VERSION,
        policy=policy,
    )
    digest = f"sha256:{hashlib.sha256(canonical_candidate_bytes(marked)).hexdigest()}"
    marked = replace(marked, candidate_id=digest)
    verify_candidate_id(marked)
    return marked


def _best_key(
    candidates: tuple[RouteCandidate, ...],
    unrouted: tuple[str, ...],
    overflow: tuple[CongestionResource, ...],
) -> tuple[int, int, int, tuple[str, ...]]:
    return (
        sum(item.usage - 1 for item in overflow),
        len(unrouted),
        sum(item.patch.length_nm for item in candidates),
        tuple(item.candidate_id for item in candidates),
    )


def _compressed_points(points: tuple[PointNM, ...]) -> tuple[PointNM, ...]:
    """Remove the local solver's unit-step interior vertices before contract construction."""

    compressed: list[PointNM] = []
    for point in points:
        if len(compressed) >= 2:
            first, middle = compressed[-2:]
            if (first.x == middle.x == point.x) or (first.y == middle.y == point.y):
                compressed[-1] = point
                continue
        compressed.append(point)
    return tuple(compressed)


def _resign_candidate(candidate: RouteCandidate) -> RouteCandidate:
    unsigned = replace(candidate, candidate_id=_EMPTY_DIGEST)
    signed = replace(
        unsigned,
        candidate_id=f"sha256:{hashlib.sha256(canonical_candidate_bytes(unsigned)).hexdigest()}",
    )
    verify_candidate_id(signed)
    return signed


def _candidate_from_local_repair(
    target: RouteCandidate,
    request: RouteRequest,
    provenance: CoordinatorRepairProvenance,
    route: tuple[tuple[int, int], ...],
    expanded_states: int,
) -> RouteCandidate:
    """Rebuild one immutable two-pin candidate from only trusted local-search output."""

    vertices = _compressed_points(
        tuple(
            PointNM(
                provenance.grid_origin.x + cell[0] * provenance.grid_step_nm,
                provenance.grid_origin.y + cell[1] * provenance.grid_step_nm,
            )
            for cell in route
        )
    )
    patch = RoutePatch(
        net_id=request.net_id,
        layer_id=request.layer_id,
        width_nm=target.patch.width_nm,
        paths=(RoutePath(vertices),),
    )
    cost = RouteCost(
        length_nm=patch.length_nm,
        bend_count=patch.bend_count,
        bend_cost_nm=patch.bend_count * request.settings.bend_penalty_nm,
        proximity_steps=0,
        proximity_cost_nm=0,
        via_cost_nm=0,
        total_cost_nm=patch.length_nm + patch.bend_count * request.settings.bend_penalty_nm,
    )
    candidate = RouteCandidate(
        candidate_id=_EMPTY_DIGEST,
        base_revision=request.board_revision,
        start_pad_id=target.start_pad_id,
        end_pad_id=target.end_pad_id,
        patch=patch,
        cost=cost,
        metrics=RouteMetrics(
            hard_internal_violations=0,
            unrouted_connections=0,
            vias=0,
            wire_length_nm=patch.length_nm,
            expanded_states=expanded_states,
            peak_frontier_states=1,
            obstacle_checks=0,
        ),
        settings=request.settings,
        router_version=target.router_version,
        policy=target.policy,
        seed=request.seed,
        fill_binding=None,
    )
    return _resign_candidate(candidate)


def _repair_policy_selection(
    snapshot: BoardIRSnapshot,
    envelope: NegotiatedRoutingRequest,
    provenances: tuple[CoordinatorRepairProvenance, ...],
    *,
    profile: str | None,
) -> tuple[CoordinatorRepairProvenance | None, str | None]:
    """Choose one supplied repair window, never allowing policy-created geometry or windows."""

    if not provenances:
        return None, None
    if profile is None:
        return provenances[0], None
    if profile == ISOLATED_REFERENCE_POLICY_PROFILE:
        return None, None
    windows = tuple(
        sorted(
            (item.window for item in provenances),
            key=lambda item: (
                item.net_id,
                item.bounds.min_x,
                item.bounds.min_y,
                item.bounds.max_x,
                item.bounds.max_y,
            ),
        )
    )
    try:
        neutral = _derive_policy_input(snapshot, envelope)
        bounds = PolicyBounds(
            min(item.bounds.min_x for item in windows),
            min(item.bounds.min_y for item in windows),
            max(item.bounds.max_x for item in windows),
            max(item.bounds.max_y for item in windows),
        )
        policy_input = RoutingPolicyInput(
            board_revision=neutral.board_revision,
            bounds=bounds,
            nets=neutral.nets,
            repair_candidates=windows,
        )
        decision = _evaluate_policy_profile(profile, policy_input, cancelled=None)
        expected_policy_id = _EXPECTED_POLICY_IDS.get(profile)
        expected_nets = tuple(net.net_id for net in policy_input.nets)
        if (
            (expected_policy_id is not None and decision.policy_id != expected_policy_id)
            or decision.input_digest != policy_input_digest(policy_input)
            or len(decision.net_order) != len(expected_nets)
            or set(decision.net_order) != set(expected_nets)
            or decision.corridor_hints
            or len(decision.repair_windows) != 1
            or decision.repair_windows[0] not in windows
        ):
            return None, None
        selected = next(item for item in provenances if item.window == decision.repair_windows[0])
        return selected, policy_decision_digest(decision)
    except Exception:
        return None, None


def _attempt_local_repair(
    snapshot: BoardIRSnapshot,
    envelope: NegotiatedRoutingRequest,
    candidates: tuple[RouteCandidate, ...],
    *,
    iteration: int,
    violating_nets: tuple[str, ...],
    settings: RepairTransactionSettings,
    policy_profile: str | None,
    cancelled: CancellationCheck | None,
    total_expansions: int,
    total_obstacle_checks: int,
) -> tuple[
    tuple[RouteCandidate, ...] | None,
    NegotiatedRepairEvidence | None,
    int,
    int,
    bool,
]:
    """Run the all-or-nothing local repair transaction for one rejected complete allocation.

    ``None`` means that no replacement is admissible and callers retain the ordinary rejection
    path.  The final boolean is reserved for cancellation, whose atomic result must not publish
    either the rejected allocation or repair evidence.
    """

    if _cancelled(cancelled):
        return None, None, total_expansions, total_obstacle_checks, True
    if (
        envelope.max_total_expansions - total_expansions < settings.max_local_expansions
        or envelope.max_total_obstacle_checks - total_obstacle_checks
        < 2 * settings.max_validator_obstacle_checks
    ):
        return None, None, total_expansions, total_obstacle_checks, False
    candidate_by_net = {item.patch.net_id: item for item in candidates}
    requests = {item.net_id: item for item in envelope.requests}
    targets = tuple(
        sorted(
            (
                candidate_by_net[net_id]
                for net_id in violating_nets
                if net_id in candidate_by_net
                and net_id in requests
                and candidate_by_net[net_id].pad_count == 2
            ),
            key=lambda item: item.candidate_id,
        )[: settings.max_attempts]
    )
    provenances: list[CoordinatorRepairProvenance] = []
    for target in targets:
        if _cancelled(cancelled):
            return None, None, total_expansions, total_obstacle_checks, True
        conflicts = tuple(
            item
            for item in candidates
            if item.patch.net_id in violating_nets and item.patch.net_id != target.patch.net_id
        )
        try:
            provenance = derive_repair_provenance(
                snapshot,
                requests[target.patch.net_id],
                target,
                conflicts,
                envelope_digest=envelope.policy_digest,
                iteration=iteration,
                settings=settings,
            )
            total_obstacle_checks += provenance.projection_obstacle_checks
            provenances.append(provenance)
        except ValueError:
            continue
    selected, decision_digest = _repair_policy_selection(
        snapshot,
        envelope,
        tuple(sorted(provenances, key=lambda item: item.target_candidate_id)),
        profile=policy_profile,
    )
    if selected is None:
        return None, None, total_expansions, total_obstacle_checks, False
    if _cancelled(cancelled):
        return None, None, total_expansions, total_obstacle_checks, True
    request = requests[selected.target_net_id]
    target = candidate_by_net[selected.target_net_id]
    try:
        # Provenance is intentionally capable-only, but it still crosses an internal boundary
        # before entering the local solver.  A malformed/tampered or now-too-small local request
        # is an ordinary atomic repair refusal, never an exception or partial publication.
        local = selected.local_request(settings)
    except (TypeError, ValueError):
        return None, None, total_expansions, total_obstacle_checks, False
    local_result = exact_local_repair(local, cancelled=cancelled)
    total_expansions += local_result.expanded_states
    if local_result.status is LocalRepairStatus.CANCELLED:
        return None, None, total_expansions, total_obstacle_checks, True
    if local_result.status is not LocalRepairStatus.COMPLETED or not verify_local_repair_result(
        local, local_result
    ):
        return None, None, total_expansions, total_obstacle_checks, False
    try:
        candidate = _candidate_from_local_repair(
            target, request, selected, local_result.route, local_result.expanded_states
        )
    except (TypeError, ValueError):
        return None, None, total_expansions, total_obstacle_checks, False
    validation = validate_candidate_path(
        snapshot,
        request,
        candidate,
        max_obstacle_checks=min(
            settings.max_validator_obstacle_checks, request.settings.max_obstacle_checks
        ),
        max_path_edges=min(settings.max_validator_path_edges, request.settings.max_grid_nodes),
        cancelled=cancelled,
    )
    total_obstacle_checks += validation.obstacle_checks
    if validation.failure is CandidatePathValidationFailure.CANCELLED:
        return None, None, total_expansions, total_obstacle_checks, True
    if validation.failure is not None:
        return None, None, total_expansions, total_obstacle_checks, False
    try:
        candidate = _resign_candidate(
            replace(
                candidate,
                metrics=replace(candidate.metrics, obstacle_checks=validation.obstacle_checks),
            )
        )
        composite_digest = _repair_binding_digest(
            envelope.policy_digest, selected.digest, local_result.route_digest, decision_digest
        )
        repaired = tuple(
            sorted(
                (
                    _reidentify_candidate(
                        candidate if item.patch.net_id == request.net_id else item,
                        composite_digest,
                        identity_policy=REPAIR_NEGOTIATED_ROUTING_POLICY,
                        truncated=False,
                    )
                    for item in candidates
                ),
                key=lambda item: item.patch.net_id,
            )
        )
        evidence = NegotiatedRepairEvidence(
            envelope_digest=envelope.policy_digest,
            provenance_digest=selected.digest,
            local_request_digest=local.input_digest,
            local_route_digest=local_result.route_digest,
            composite_digest=composite_digest,
            projection_obstacle_checks=selected.projection_obstacle_checks,
            local_expanded_states=local_result.expanded_states,
            validator_edge_checks=validation.edge_checks,
            validator_obstacle_checks=validation.obstacle_checks,
            repair_policy_decision_digest=decision_digest,
        )
    except (TypeError, ValueError):
        return None, None, total_expansions, total_obstacle_checks, False
    return repaired, evidence, total_expansions, total_obstacle_checks, False


def negotiate_routes(
    snapshot: object,
    envelope: object,
    *,
    cancelled: object = None,
    router: AStarRouter | None = None,
    policy_profile: str | None = None,
    plan: object = None,
    repair_settings: object = None,
    repair_policy_profile: object = None,
) -> NegotiatedRoutingResult:
    """Run negotiated routing, optionally attempting one closed local repair after rejection."""

    if not isinstance(snapshot, BoardIRSnapshot) or not isinstance(
        envelope, NegotiatedRoutingRequest
    ):
        return _invalid_result("the negotiated snapshot or request type is invalid")
    checked_snapshot = snapshot
    checked_envelope = envelope
    if repair_settings is not None and type(repair_settings) is not RepairTransactionSettings:
        return _invalid_result(
            "the negotiated local repair transaction was rejected",
            board_revision=checked_envelope.board_revision,
        )
    if repair_policy_profile is not None and type(repair_policy_profile) is not str:
        return _invalid_result(
            "the negotiated local repair transaction was rejected",
            board_revision=checked_envelope.board_revision,
        )
    if repair_settings is None and repair_policy_profile is not None:
        return _invalid_result(
            "the negotiated local repair transaction was rejected",
            board_revision=checked_envelope.board_revision,
        )
    if repair_settings is not None and (policy_profile is not None or plan is not None):
        # Composing optional transaction evidence needs its own reviewed identity binding.
        return _invalid_result(
            "the negotiated local repair transaction was rejected",
            board_revision=checked_envelope.board_revision,
        )
    if repair_policy_profile == ISOLATED_REFERENCE_POLICY_PROFILE:
        return _invalid_result(
            "the negotiated local repair transaction was rejected",
            board_revision=checked_envelope.board_revision,
        )
    checked_repair_settings = repair_settings
    checked_repair_profile = repair_policy_profile
    if cancelled is not None and not callable(cancelled):
        return _invalid_result(
            "the negotiated cancellation hook is invalid",
            board_revision=checked_envelope.board_revision,
        )
    if plan is not None and type(plan) is not NegotiationPlan:
        return _invalid_result(
            "the declared negotiation plan was rejected",
            board_revision=checked_envelope.board_revision,
        )
    if plan is not None and policy_profile is not None:
        # Composing a learned initial order with declared slots needs its own evidence shape and
        # its own measurement.  Refusing is the honest answer until that decision exists.
        return _invalid_result(
            "a negotiated run declares either a policy profile or a negotiation plan",
            board_revision=checked_envelope.board_revision,
        )
    validation_error = _validate_snapshot_requests(checked_snapshot, checked_envelope)
    if validation_error is not None:
        return _invalid_result(validation_error, board_revision=checked_envelope.board_revision)

    cancellation_check = cast(CancellationCheck | None, cancelled)
    policy_requested = policy_profile is not None
    if policy_requested and _cancelled(cancellation_check):
        return _policy_cancelled_result(checked_envelope)
    if policy_profile is not None and type(policy_profile) is not str:
        return _invalid_result(
            "the negotiated routing policy was rejected",
            board_revision=checked_envelope.board_revision,
        )

    policy_evidence: NegotiatedPolicyEvidence | None = None
    initial_order: tuple[RouteRequest, ...] | None = None
    if policy_requested:
        try:
            assert policy_profile is not None
            policy_input = _derive_policy_input(checked_snapshot, checked_envelope)
            decision = _evaluate_policy_profile(
                policy_profile,
                policy_input,
                cancelled=cancellation_check,
            )
            input_digest = policy_input_digest(policy_input)
            expected_net_ids = tuple(net.net_id for net in policy_input.nets)
            expected_policy_id = _EXPECTED_POLICY_IDS.get(policy_profile)
            if (expected_policy_id is not None and decision.policy_id != expected_policy_id) or (
                decision.input_digest != input_digest
                or len(decision.net_order) != len(expected_net_ids)
                or len(set(decision.net_order)) != len(decision.net_order)
                or set(decision.net_order) != set(expected_net_ids)
                or decision.corridor_hints
                or decision.repair_windows
            ):
                raise ValueError("policy decision is outside the negotiated first-pass boundary")
            decision_digest = policy_decision_digest(decision)
            policy_evidence = NegotiatedPolicyEvidence(
                envelope_digest=checked_envelope.policy_digest,
                input_digest=input_digest,
                decision_digest=decision_digest,
                composite_digest=_policy_binding_digest(
                    checked_envelope.policy_digest, decision_digest
                ),
                policy_id=decision.policy_id,
                policy_profile=policy_profile,
            )
            by_net_id = {request.net_id: request for request in checked_envelope.requests}
            initial_order = tuple(by_net_id[net_id] for net_id in decision.net_order)
        except Exception:
            # This is an untrusted policy boundary.  Do not expose exception text, raw policy
            # output, or a partial policy binding before the first router call.
            if _cancelled(cancellation_check):
                return _policy_cancelled_result(checked_envelope)
            return _invalid_result(
                "the negotiated routing policy was rejected",
                board_revision=checked_envelope.board_revision,
            )
        if _cancelled(cancellation_check):
            return _policy_cancelled_result(checked_envelope)

    plan_evidence: NegotiationPlanEvidence | None = None
    declared_plan: NegotiationPlan | None = None
    demand_cells: Mapping[str, int] = {}
    if plan is not None:
        assert type(plan) is NegotiationPlan
        try:
            declared_plan = plan
            demand_cells = _net_demand_cells(checked_snapshot, checked_envelope)
            plan_evidence = NegotiationPlanEvidence(
                envelope_digest=checked_envelope.policy_digest,
                plan_digest=declared_plan.plan_digest,
                net_order_slot_digest=declared_plan.net_order.slot_digest,
                cost_update_slot_digest=declared_plan.cost_update.slot_digest,
                rip_up_slot_digest=declared_plan.rip_up.slot_digest,
                composite_digest=_plan_binding_digest(
                    checked_envelope.policy_digest, declared_plan.plan_digest
                ),
            )
        except Exception:
            return _invalid_result(
                "the declared negotiation plan was rejected",
                board_revision=checked_envelope.board_revision,
            )

    # All policy/factory work precedes router construction.  The default path deliberately keeps
    # the existing construction, ordering, and envelope-bound candidate identity unchanged.
    selected_router = router or AStarRouter()
    replay_custom_router = not _is_exact_reference_router(selected_router)
    reference_router = AStarRouter()
    ordered = tuple(sorted(checked_envelope.requests, key=lambda item: (item.net_id, item.seed)))
    ledger = CongestionLedger(
        grid_step_nm=checked_envelope.grid_step_nm,
        present_penalty_nm=checked_envelope.present_penalty_nm,
        history_penalty_nm=checked_envelope.history_penalty_nm,
    )
    best_candidates: tuple[RouteCandidate, ...] = ()
    best_connections: tuple[RouteConnection, ...] = ()
    best_unrouted = tuple(item.net_id for item in ordered)
    best_overflow: tuple[CongestionResource, ...] = ()
    best_key: tuple[int, int, int, tuple[str, ...]] | None = None
    iterations = 0
    ripups = 0
    total_expansions = 0
    total_obstacle_checks = 0
    total_physical_checks = 0
    current_order = initial_order or ordered
    candidate_binding_digest = checked_envelope.policy_digest
    candidate_identity_policy = NEGOTIATED_ROUTING_POLICY
    candidate_identity_truncated = True
    if policy_evidence is not None:
        candidate_binding_digest = policy_evidence.composite_digest
        candidate_identity_policy = POLICY_NEGOTIATED_ROUTING_POLICY
        candidate_identity_truncated = False
    elif plan_evidence is not None:
        candidate_binding_digest = plan_evidence.composite_digest
        candidate_identity_policy = PLAN_NEGOTIATED_ROUTING_POLICY
        candidate_identity_truncated = False
    final_failure_message: str | None = None
    request_by_net_id = {item.net_id: item for item in ordered}
    net_seeds = tuple((item.net_id, item.seed) for item in ordered)
    retained_candidates: dict[str, RouteCandidate] = {}
    retained_connections: dict[str, RouteConnection] = {}
    ripup_nets = frozenset(request_by_net_id)
    plan_stop_message: str | None = None
    if declared_plan is not None:
        # The net-order slot owns the first pass as well as every retry.  With no conflicts yet
        # recorded, the default conflict-descending rule reproduces the historic stable order.
        current_order = tuple(
            request_by_net_id[net_id]
            for net_id in ordered_net_ids(
                declared_plan.net_order,
                nets=net_seeds,
                iteration=1,
                conflict_scores={},
                demand_cells=demand_cells,
            )
        )

    for iteration in range(1, checked_envelope.max_iterations + 1):
        if _cancelled(cancellation_check):
            return NegotiatedRoutingResult(
                status=NegotiatedRoutingStatus.CANCELLED,
                board_revision=checked_envelope.board_revision,
                candidates=(),
                connections=(),
                unrouted_nets=tuple(item.net_id for item in ordered),
                iterations=iterations,
                ripups=ripups,
                overflow_resources=(),
                overflow_units=0,
                total_wire_length_nm=0,
                total_physical_checks=total_physical_checks,
                diagnostic="negotiated routing was cancelled before the next bounded iteration",
                policy_digest=checked_envelope.policy_digest,
            )
        if declared_plan is None and iteration > 1:
            ripups += len(best_candidates)
        working: dict[str, RouteCandidate] = {}
        connections: dict[str, RouteConnection] = {}
        unrouted: set[str] = set()
        failure_message: str | None = None
        cancelled_during_iteration = False
        iteration_order = current_order
        if declared_plan is None:
            # The no-plan path rips up every net on every pass, so there is nothing to retain and
            # clearing is already the cheapest correct reconstruction.
            ledger.clear_present()
        else:
            # A retained candidate is immutable, already identity-bound, and already accepted by
            # this run's boundary checks, and its occupancy is *already in the ledger* from the
            # pass that produced it.  Retaining it in place leaves exactly the same counters a
            # clear-and-re-add would rebuild, at the cost of the smaller of the two sides rather
            # than always the retained one.  ADR-0081 and B-095 record the argument and the
            # measurement; `tests/test_routing_incremental_spatial_index.py` pins the equivalence.
            retained_now = frozenset(retained_candidates) - ripup_nets
            ledger.retain_only(retained_now)
            for net_id in sorted(retained_now):
                working[net_id] = retained_candidates[net_id]
            for net_id in sorted(frozenset(retained_connections) - ripup_nets):
                connections[net_id] = retained_connections[net_id]
            iteration_order = tuple(item for item in current_order if item.net_id in ripup_nets)
        for request in iteration_order:
            if _cancelled(cancellation_check):
                cancelled_during_iteration = True
                break
            remaining_expansions = checked_envelope.max_total_expansions - total_expansions
            remaining_obstacle_checks = (
                checked_envelope.max_total_obstacle_checks - total_obstacle_checks
            )
            if remaining_expansions <= 0 or remaining_obstacle_checks <= 0:
                failure_message = "the negotiated routing budget was exhausted"
                unrouted.update(
                    item.net_id for item in iteration_order if item.net_id not in working
                )
                break
            # A generic backend receives only half of the remaining allowance.  The same clipped
            # request is replayed through the reference core before publication, so both searches
            # together remain within the caller-authorised coordinator ceiling.  The exact
            # built-in AStarRouter needs no redundant replay and retains the full remainder.
            verification_expansions = (
                remaining_expansions // 2 if replay_custom_router else remaining_expansions
            )
            verification_obstacle_checks = (
                remaining_obstacle_checks // 2
                if replay_custom_router
                else remaining_obstacle_checks
            )
            if verification_expansions <= 0 or verification_obstacle_checks <= 0:
                failure_message = "the negotiated routing budget was exhausted"
                unrouted.update(
                    item.net_id for item in iteration_order if item.net_id not in working
                )
                break
            bounded_settings = replace(
                request.settings,
                max_expansions=min(request.settings.max_expansions, verification_expansions),
                max_obstacle_checks=min(
                    request.settings.max_obstacle_checks, verification_obstacle_checks
                ),
            )
            bounded_request = replace(request, settings=bounded_settings)
            try:
                result = selected_router.propose(
                    checked_snapshot,
                    bounded_request,
                    cancelled=cancellation_check,
                    congestion_penalty=ledger.penalty,
                )
            except Exception:  # pragma: no cover - a pluggable router must never escape this seam
                return _router_boundary_failure(
                    checked_envelope,
                    ordered,
                    iterations=iteration,
                    ripups=ripups,
                    total_physical_checks=total_physical_checks,
                    policy_evidence=policy_evidence,
                    plan_evidence=plan_evidence,
                )
            if not _router_result_is_bound(result, checked_snapshot, bounded_request):
                return _router_boundary_failure(
                    checked_envelope,
                    ordered,
                    iterations=iteration,
                    ripups=ripups,
                    total_physical_checks=total_physical_checks,
                    policy_evidence=policy_evidence,
                    plan_evidence=plan_evidence,
                )
            if (
                result.diagnostic is not None
                and result.diagnostic.code is RouteFailureCode.CANCELLED
            ):
                # Cancellation never publishes proposal data.  It is safe to honour promptly,
                # including from a custom backend, rather than starting a fresh replay after the
                # caller's cancellation state may have changed.
                cancelled_during_iteration = True
                break
            verification_result: RouteResult | None = None
            if replay_custom_router:
                try:
                    verification_result = reference_router.propose(
                        checked_snapshot,
                        bounded_request,
                        cancelled=cancellation_check,
                        congestion_penalty=ledger.penalty,
                    )
                except Exception:  # pragma: no cover - defensive reference-core boundary
                    return _router_boundary_failure(
                        checked_envelope,
                        ordered,
                        iterations=iteration,
                        ripups=ripups,
                        total_physical_checks=total_physical_checks,
                        policy_evidence=policy_evidence,
                        plan_evidence=plan_evidence,
                    )
                if not _router_result_is_bound(
                    verification_result, checked_snapshot, bounded_request
                ):
                    return _router_boundary_failure(
                        checked_envelope,
                        ordered,
                        iterations=iteration,
                        ripups=ripups,
                        total_physical_checks=total_physical_checks,
                        policy_evidence=policy_evidence,
                        plan_evidence=plan_evidence,
                    )
                if (
                    verification_result.diagnostic is not None
                    and verification_result.diagnostic.code is RouteFailureCode.CANCELLED
                ):
                    cancelled_during_iteration = True
                    break
                if not _results_are_semantically_equal(result, verification_result):
                    return _router_boundary_failure(
                        checked_envelope,
                        ordered,
                        iterations=iteration,
                        ripups=ripups,
                        total_physical_checks=total_physical_checks,
                        policy_evidence=policy_evidence,
                        plan_evidence=plan_evidence,
                    )
            result_expansions, result_obstacle_checks = _result_work(result)
            total_expansions += result_expansions
            total_obstacle_checks += result_obstacle_checks
            if verification_result is not None:
                verification_expansions, verification_obstacle_checks = _result_work(
                    verification_result
                )
                total_expansions += verification_expansions
                total_obstacle_checks += verification_obstacle_checks
            if result.candidate is not None:
                try:
                    marked = _reidentify_candidate(
                        result.candidate,
                        candidate_binding_digest,
                        identity_policy=candidate_identity_policy,
                        truncated=candidate_identity_truncated,
                    )
                    ledger.add_candidate(marked)
                except Exception:  # pragma: no cover - reject malformed canonical route geometry
                    return _router_boundary_failure(
                        checked_envelope,
                        ordered,
                        iterations=iteration,
                        ripups=ripups,
                        total_physical_checks=total_physical_checks,
                        policy_evidence=policy_evidence,
                        plan_evidence=plan_evidence,
                    )
                working[request.net_id] = marked
            elif result.connected is not None:
                connections[request.net_id] = result.connected
            else:
                unrouted.add(request.net_id)
                failure_message = "one or more negotiated nets did not produce a candidate"
        if len(working) + len(connections) < len(ordered):
            unrouted.update(
                item.net_id
                for item in ordered
                if item.net_id not in working and item.net_id not in connections
            )
        iterations = iteration
        if cancelled_during_iteration:
            return NegotiatedRoutingResult(
                status=NegotiatedRoutingStatus.CANCELLED,
                board_revision=checked_envelope.board_revision,
                # A cancellation invalidates the in-progress allocation as a whole.  Publishing
                # only the earlier nets would make an incomplete negotiated pass look usable.
                candidates=(),
                connections=(),
                unrouted_nets=tuple(item.net_id for item in ordered),
                iterations=iterations,
                ripups=ripups,
                overflow_resources=(),
                overflow_units=0,
                total_wire_length_nm=0,
                total_physical_checks=total_physical_checks,
                diagnostic="negotiated routing was cancelled during a bounded iteration",
                policy_digest=checked_envelope.policy_digest,
            )
        present_overflow = ledger.overflow_resources()
        candidates = tuple(sorted(working.values(), key=lambda item: item.patch.net_id))
        connected = tuple(sorted(connections.values(), key=lambda item: item.start_pad_id))
        unrouted_tuple = tuple(sorted(unrouted))
        # Retention is internal negotiation state, decided before the publication reset below.
        # Publishing still happens only from a set the physical gate accepted in its own pass.
        iteration_candidates = dict(working)
        iteration_connections = dict(connections)
        forced_ripup: frozenset[str] = frozenset()
        physical = verify_negotiated_physical_clearance(
            checked_snapshot,
            candidates,
            layer_id=checked_envelope.layer_id,
            max_pair_checks=checked_envelope.max_total_physical_checks - total_physical_checks,
            cancelled=cancellation_check,
        )
        total_physical_checks += physical.pair_checks
        if physical.failure is PhysicalClearanceFailure.CANCELLED:
            return NegotiatedRoutingResult(
                status=NegotiatedRoutingStatus.CANCELLED,
                board_revision=checked_envelope.board_revision,
                candidates=(),
                connections=(),
                unrouted_nets=tuple(item.net_id for item in ordered),
                iterations=iterations,
                ripups=ripups,
                overflow_resources=(),
                overflow_units=0,
                total_wire_length_nm=0,
                total_physical_checks=total_physical_checks,
                diagnostic="negotiated physical-clearance verification was cancelled",
                policy_digest=checked_envelope.policy_digest,
            )
        if (
            checked_repair_settings is not None
            and physical.failure is PhysicalClearanceFailure.CLEARANCE_VIOLATION
            and not unrouted_tuple
            and len(candidates) + len(connected) == len(ordered)
            and len(physical.violating_nets) >= 2
        ):
            repaired, repair_evidence, total_expansions, total_obstacle_checks, repair_cancelled = (
                _attempt_local_repair(
                    checked_snapshot,
                    checked_envelope,
                    candidates,
                    iteration=iteration,
                    violating_nets=physical.violating_nets,
                    settings=checked_repair_settings,
                    policy_profile=checked_repair_profile,
                    cancelled=cancellation_check,
                    total_expansions=total_expansions,
                    total_obstacle_checks=total_obstacle_checks,
                )
            )
            if repair_cancelled:
                return NegotiatedRoutingResult(
                    status=NegotiatedRoutingStatus.CANCELLED,
                    board_revision=checked_envelope.board_revision,
                    candidates=(),
                    connections=(),
                    unrouted_nets=tuple(item.net_id for item in ordered),
                    iterations=iterations,
                    ripups=ripups,
                    overflow_resources=(),
                    overflow_units=0,
                    total_wire_length_nm=0,
                    total_physical_checks=total_physical_checks,
                    diagnostic="negotiated local repair was cancelled",
                    policy_digest=checked_envelope.policy_digest,
                )
            if repaired is not None and repair_evidence is not None:
                repaired_physical = verify_negotiated_physical_clearance(
                    checked_snapshot,
                    repaired,
                    layer_id=checked_envelope.layer_id,
                    max_pair_checks=(
                        checked_envelope.max_total_physical_checks - total_physical_checks
                    ),
                    cancelled=cancellation_check,
                )
                total_physical_checks += repaired_physical.pair_checks
                if repaired_physical.failure is PhysicalClearanceFailure.CANCELLED:
                    return NegotiatedRoutingResult(
                        status=NegotiatedRoutingStatus.CANCELLED,
                        board_revision=checked_envelope.board_revision,
                        candidates=(),
                        connections=(),
                        unrouted_nets=tuple(item.net_id for item in ordered),
                        iterations=iterations,
                        ripups=ripups,
                        overflow_resources=(),
                        overflow_units=0,
                        total_wire_length_nm=0,
                        total_physical_checks=total_physical_checks,
                        diagnostic="negotiated local repair was cancelled",
                        policy_digest=checked_envelope.policy_digest,
                    )
                if repaired_physical.failure is None:
                    return _published_result(
                        None,
                        repair_evidence=repair_evidence,
                        status=NegotiatedRoutingStatus.COMPLETED,
                        board_revision=checked_envelope.board_revision,
                        candidates=repaired,
                        connections=connected,
                        iterations=iterations,
                        ripups=ripups,
                        overflow_resources=(),
                        overflow_units=0,
                        total_wire_length_nm=sum(item.patch.length_nm for item in repaired),
                        total_physical_checks=total_physical_checks,
                        policy_digest=checked_envelope.policy_digest,
                    )
        if physical.failure is not None:
            # A lattice-clean allocation can still be physically illegal.  Never let this
            # iteration contribute candidate copper or connection evidence to the best result or
            # published response.  Connections belong to the allocation as a whole: keeping one
            # after its peer copper was discarded would misrepresent an incomplete proposal.
            unrouted.update(item.patch.net_id for item in candidates)
            unrouted.update(connections)
            unrouted_tuple = tuple(sorted(unrouted))
            candidates = ()
            connected = ()
            present_overflow = ()
            final_failure_message = physical.diagnostic
            # A partial rip-up must not carry forward copper the gate just refused.  An attributed
            # clearance violation names the offending pair, so only that pair is forced out; an
            # unattributed refusal blames nothing in particular and therefore blames everything.
            if physical.violating_nets:
                forced_ripup = frozenset(physical.violating_nets)
            else:
                iteration_candidates = {}
                iteration_connections = {}
        score = _best_key(candidates, unrouted_tuple, present_overflow)
        if best_key is None or score < best_key:
            best_key = score
            best_candidates = candidates
            best_connections = connected
            best_unrouted = unrouted_tuple
            best_overflow = present_overflow
        if not unrouted_tuple and not present_overflow:
            return _published_result(
                policy_evidence,
                plan_evidence,
                status=NegotiatedRoutingStatus.COMPLETED,
                board_revision=checked_envelope.board_revision,
                candidates=candidates,
                connections=connected,
                iterations=iterations,
                ripups=ripups,
                overflow_resources=(),
                overflow_units=0,
                total_wire_length_nm=sum(item.patch.length_nm for item in candidates),
                total_physical_checks=total_physical_checks,
                policy_digest=checked_envelope.policy_digest,
            )
        if declared_plan is None:
            ledger.update_history()
            scores = ledger.conflict_scores()
            current_order = tuple(
                sorted(
                    ordered,
                    key=lambda item: (-scores.get(item.net_id, 0), item.net_id, item.seed),
                )
            )
        else:
            ledger.update_history(declared_plan.cost_update)
            ledger.apply_present_growth(declared_plan.cost_update)
            scores = ledger.conflict_scores()
            current_order = tuple(
                request_by_net_id[net_id]
                for net_id in ordered_net_ids(
                    declared_plan.net_order,
                    nets=net_seeds,
                    iteration=iteration + 1,
                    conflict_scores=scores,
                    demand_cells=demand_cells,
                )
            )
            retained_candidates = iteration_candidates
            retained_connections = iteration_connections
            held = (frozenset(retained_candidates) | frozenset(retained_connections)) - forced_ripup
            # The bounded rip-up window is read off the ledger *before* the next pass rips
            # anything up, so the index still holds every candidate this pass produced.  The
            # window is a declared constant, so the selection cannot widen from one iteration to
            # the next.  A large enough declared window still selects every net — B-095 measures a
            # 16-cell window doing exactly that — which is a caller's choice, not a drift.
            window_nets: frozenset[str] = frozenset()
            if declared_plan.rip_up.rule is RipUpRule.CONFLICT_WINDOW:
                conflicted_seeds = (
                    frozenset(
                        net_id for net_id, score in scores.items() if score > 0 and net_id in held
                    )
                    | forced_ripup
                )
                window_nets = ledger.nets_within_window(
                    conflicted_seeds,
                    window_nm=(
                        declared_plan.rip_up.ripup_window_cells * checked_envelope.grid_step_nm
                    ),
                )
            ripup_nets = ripup_net_ids(
                declared_plan.rip_up,
                nets=net_seeds,
                conflict_scores=scores,
                retained=held,
                window_nets=window_nets,
            )
            # A pass that would re-route nothing cannot make progress, and looping to the
            # iteration ceiling would burn the caller's budget to reach the same allocation.
            if not ripup_nets:
                plan_stop_message = "the declared rip-up rule selected no net to re-route"
                break
            ripups += len(ripup_nets & held)
            if failure_message == "the negotiated routing budget was exhausted":
                plan_stop_message = failure_message
                break
        if failure_message is not None and not candidates and not connections:
            return _published_result(
                policy_evidence,
                plan_evidence,
                status=NegotiatedRoutingStatus.NO_PATH,
                board_revision=checked_envelope.board_revision,
                candidates=(),
                connections=(),
                unrouted_nets=tuple(sorted(unrouted)),
                iterations=iterations,
                ripups=ripups,
                overflow_resources=(),
                overflow_units=0,
                total_wire_length_nm=0,
                total_physical_checks=total_physical_checks,
                diagnostic=failure_message,
                policy_digest=checked_envelope.policy_digest,
            )

    status = NegotiatedRoutingStatus.PARTIAL
    diagnostic = "negotiated routing reached its bounded iteration budget"
    if plan_stop_message is not None:
        diagnostic = plan_stop_message
    if best_unrouted and not best_candidates and not best_connections:
        status = NegotiatedRoutingStatus.NO_PATH
        diagnostic = (
            final_failure_message
            or plan_stop_message
            or "no negotiated net produced a candidate within the bounded budget"
        )
    return _published_result(
        policy_evidence,
        plan_evidence,
        status=status,
        board_revision=checked_envelope.board_revision,
        candidates=best_candidates,
        connections=best_connections,
        unrouted_nets=best_unrouted,
        iterations=iterations,
        ripups=ripups,
        overflow_resources=best_overflow,
        overflow_units=sum(item.usage - 1 for item in best_overflow),
        total_wire_length_nm=sum(item.patch.length_nm for item in best_candidates),
        total_physical_checks=total_physical_checks,
        diagnostic=diagnostic,
        policy_digest=checked_envelope.policy_digest,
    )


__all__ = [
    "ISOLATED_REFERENCE_POLICY_PROFILE",
    "NEGOTIATED_POLICY_BINDING_SCHEMA",
    "NEGOTIATION_PLAN_BINDING_SCHEMA",
    "NEGOTIATION_PLAN_EVIDENCE_SCHEMA",
    "PLAN_NEGOTIATED_ROUTING_POLICY",
    "POLICY_NEGOTIATED_ROUTING_POLICY",
    "REFERENCE_POLICY_PROFILE",
    "CongestionLedger",
    "CongestionResource",
    "NegotiatedPolicyEvidence",
    "NegotiatedRoutingRequest",
    "NegotiatedRoutingResult",
    "NegotiatedRoutingStatus",
    "NegotiationPlanEvidence",
    "PlanNegotiatedRoutingResult",
    "PolicyNegotiatedRoutingResult",
    "negotiate_routes",
]
