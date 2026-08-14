"""Immutable contracts for the Board-IR-bound ordered-layer routing proposal.

This is deliberately separate from :mod:`copper_mcp.routing.contracts`.  The legacy route
candidate and KiCad serializer are single-layer contracts; changing them would make old candidate
IDs and apply guards ambiguous.  The layered contract remains proposal-only even though it now has
source-preserving serialization and a separate authoritative DRC evidence gate; MCP exposure and
apply authority are still not part of this contract.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

from copper_mcp.board_ir import PointNM
from copper_mcp.routing.layered_astar import LayeredAStarSettings

_SHA256 = "sha256:"
_MAX_INT = (1 << 53) - 1


def _integer(name: str, value: int, *, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= _MAX_INT:
        raise ValueError(f"{name} is outside the supported integer range")


def _typed_id(name: str, value: str, prefix: str) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith(prefix)
        or not 1 <= len(value.removeprefix(prefix)) <= 160
        or not all(
            character.isascii() and (character.isalnum() or character in "_.:-")
            for character in value
        )
    ):
        raise ValueError(f"{name} must be a stable {prefix.rstrip(':')} ID")


def _digest(name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith(_SHA256)
        or len(value) != 71
        or not all(character in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{name} must be content-addressed with sha256")
    try:
        int(value.removeprefix(_SHA256), 16)
    except ValueError as error:
        raise ValueError(f"{name} must be content-addressed with sha256") from error


@dataclass(frozen=True, slots=True)
class LayeredRoutePath:
    """One compressed, orthogonal path on one physical signal layer."""

    layer_id: str
    vertices: tuple[PointNM, ...]

    def __post_init__(self) -> None:
        _typed_id("layer ID", self.layer_id, "layer:")
        if (
            not isinstance(self.vertices, tuple)
            or len(self.vertices) < 2
            or not all(isinstance(point, PointNM) for point in self.vertices)
        ):
            raise ValueError(
                "layered path vertices must be an immutable tuple of at least two points"
            )
        for start, end in pairwise(self.vertices):
            if start == end or (start.x != end.x and start.y != end.y):
                raise ValueError("layered path edges must be non-zero and orthogonal")
        for first, middle, last in zip(
            self.vertices, self.vertices[1:], self.vertices[2:], strict=False
        ):
            if (first.x == middle.x == last.x) or (first.y == middle.y == last.y):
                raise ValueError("layered path vertices must omit collinear interior points")

    @property
    def length_nm(self) -> int:
        return sum(
            abs(start.x - end.x) + abs(start.y - end.y) for start, end in pairwise(self.vertices)
        )

    @property
    def bend_count(self) -> int:
        return len(self.vertices) - 2


@dataclass(frozen=True, slots=True)
class LayeredRouteVia:
    """One explicit full-stack through-via proposed by a layered route."""

    id: str
    center: PointNM
    diameter_nm: int
    drill_nm: int
    start_layer_id: str
    end_layer_id: str

    def __post_init__(self) -> None:
        _typed_id("via ID", self.id, "via:")
        if not isinstance(self.center, PointNM):
            raise ValueError("via center must be a PointNM")
        _integer("via diameter", self.diameter_nm, minimum=1)
        _integer("via drill", self.drill_nm, minimum=1)
        if self.drill_nm >= self.diameter_nm:
            raise ValueError("via drill must be smaller than via diameter")
        _typed_id("via start layer ID", self.start_layer_id, "layer:")
        _typed_id("via end layer ID", self.end_layer_id, "layer:")
        if self.start_layer_id == self.end_layer_id:
            raise ValueError("via layer span must contain two distinct layers")


@dataclass(frozen=True, slots=True)
class LayeredRoutePatch:
    """Unapplied, immutable layered geometry with explicit paths and vias."""

    net_id: str
    width_nm: int
    via_diameter_nm: int
    via_drill_nm: int
    paths: tuple[LayeredRoutePath, ...]
    vias: tuple[LayeredRouteVia, ...] = ()

    def __post_init__(self) -> None:
        _typed_id("net ID", self.net_id, "net:")
        _integer("route width", self.width_nm, minimum=1)
        _integer("via diameter", self.via_diameter_nm, minimum=1)
        _integer("via drill", self.via_drill_nm, minimum=1)
        if self.via_drill_nm >= self.via_diameter_nm:
            raise ValueError("route via drill must be smaller than its diameter")
        if (
            not isinstance(self.paths, tuple)
            or not self.paths
            or not all(isinstance(path, LayeredRoutePath) for path in self.paths)
        ):
            raise ValueError("layered route patch must carry at least one path")
        if not isinstance(self.vias, tuple) or not all(
            isinstance(via, LayeredRouteVia) for via in self.vias
        ):
            raise ValueError("layered route vias must be an immutable tuple")
        via_ids = tuple(via.id for via in self.vias)
        if len(set(via_ids)) != len(via_ids):
            raise ValueError("layered route via IDs must be unique")
        via_centers = tuple(via.center for via in self.vias)
        if len(set(via_centers)) != len(via_centers):
            raise ValueError("layered route cannot place duplicate vias")
        if any(
            via.diameter_nm != self.via_diameter_nm or via.drill_nm != self.via_drill_nm
            for via in self.vias
        ):
            raise ValueError("every proposed via must use the resolved net-class dimensions")

    @property
    def wire_length_nm(self) -> int:
        return sum(path.length_nm for path in self.paths)

    @property
    def bend_count(self) -> int:
        return sum(path.bend_count for path in self.paths)


@dataclass(frozen=True, slots=True)
class LayeredRouteCost:
    """Exact physical length plus the layered search cost decomposition."""

    wire_length_nm: int
    via_count: int
    via_cost_units: int
    total_search_cost_units: int

    def __post_init__(self) -> None:
        _integer("layered wire length", self.wire_length_nm)
        _integer("layered via count", self.via_count)
        _integer("layered via cost", self.via_cost_units)
        _integer("layered total search cost", self.total_search_cost_units)
        if self.via_count == 0 and self.via_cost_units != 0:
            raise ValueError("a route without vias cannot carry via cost")


@dataclass(frozen=True, slots=True)
class LayeredRouteMetrics:
    """Deterministic search and geometry metrics for one layered candidate."""

    expanded_states: int
    discovered_states: int
    peak_frontier_states: int
    obstacle_checks: int
    move_steps: int
    vias: int
    wire_length_nm: int
    bend_count: int

    def __post_init__(self) -> None:
        for name, value in (
            ("expanded states", self.expanded_states),
            ("discovered states", self.discovered_states),
            ("peak frontier states", self.peak_frontier_states),
            ("obstacle checks", self.obstacle_checks),
            ("move steps", self.move_steps),
            ("via count", self.vias),
            ("wire length", self.wire_length_nm),
            ("bend count", self.bend_count),
        ):
            _integer(name, value)


class LayeredRouteFailureCode(StrEnum):
    """Stable expected outcomes for the Board-IR-bound layered proposal."""

    INVALID_REQUEST = "invalid_request"
    INVALID_SNAPSHOT = "invalid_snapshot"
    STALE_REVISION = "stale_revision"
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"
    UNSUPPORTED_CONSTRAINT = "unsupported_constraint"
    OFF_GRID = "off_grid"
    GRID_BUDGET_EXCEEDED = "grid_budget_exceeded"
    OBSTACLE_BUDGET_EXCEEDED = "obstacle_budget_exceeded"
    SEARCH_BUDGET_EXCEEDED = "search_budget_exceeded"
    CANCELLED = "cancelled"
    STALE_FILL = "stale_fill"
    #: Only :meth:`LayeredBoardRouter.replay` produces this, so it cannot reach a preview
    #: response and is deliberately absent from ``LayeredRouteDiagnosticContract`` -- exactly as
    #: ``fill_evidence_mismatch`` is absent from the single-layer ``RouteDiagnosticContract``.
    FILL_EVIDENCE_MISMATCH = "fill_evidence_mismatch"
    NO_PATH = "no_path"


@dataclass(frozen=True, slots=True)
class LayeredRouteDiagnostic:
    code: LayeredRouteFailureCode
    message: str
    expanded_states: int = 0
    obstacle_checks: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.code, LayeredRouteFailureCode):
            raise ValueError("layered diagnostic code is unsupported")
        if not isinstance(self.message, str) or not 1 <= len(self.message) <= 256:
            raise ValueError("layered diagnostic message is malformed")
        _integer("diagnostic expanded states", self.expanded_states)
        _integer("diagnostic obstacle checks", self.obstacle_checks)


@dataclass(frozen=True, slots=True)
class LayeredRouteCandidate:
    """Content-addressed, read-only proposal for a Board IR layered route."""

    candidate_id: str
    base_revision: str
    start_pad_id: str
    end_pad_id: str
    patch: LayeredRoutePatch
    cost: LayeredRouteCost
    metrics: LayeredRouteMetrics
    settings: LayeredAStarSettings
    router_version: str
    policy: str
    seed: int
    fill_binding: str | None = None
    """The obstacle model that produced this candidate, as a binding rather than as evidence.

    The sha256 content address of the freshness-verified zone fill the ordered-layer router was
    handed, or ``None`` when it was handed none and searched against the conservative zone
    envelope (ADR-0103, ADR-0106).  It is computed by the *same* ``fill_binding_for`` the
    single-layer path uses, over the same ``VerifiedFill`` values
    ``LayeredRouteRequest.verified_fill`` already carries, so the two paths cannot disagree about
    what "the same fill" is.
    """

    def __post_init__(self) -> None:
        _digest("layered candidate ID", self.candidate_id)
        _digest("layered base revision", self.base_revision)
        _typed_id("start pad ID", self.start_pad_id, "pad:")
        _typed_id("end pad ID", self.end_pad_id, "pad:")
        if self.start_pad_id == self.end_pad_id:
            raise ValueError("layered route endpoints must be distinct")
        if not isinstance(self.patch, LayeredRoutePatch):
            raise ValueError("layered candidate patch is malformed")
        if not isinstance(self.cost, LayeredRouteCost) or not isinstance(
            self.metrics, LayeredRouteMetrics
        ):
            raise ValueError("layered candidate cost or metrics is malformed")
        if not isinstance(self.settings, LayeredAStarSettings):
            raise ValueError("layered candidate settings are malformed")
        if not isinstance(self.router_version, str) or not self.router_version:
            raise ValueError("layered router version is malformed")
        if not isinstance(self.policy, str) or not self.policy:
            raise ValueError("layered routing policy is malformed")
        _integer("layered seed", self.seed)
        if self.fill_binding is not None:
            _digest("layered fill binding", self.fill_binding)
        if self.cost.wire_length_nm != self.patch.wire_length_nm:
            raise ValueError("layered candidate length must match patch geometry")
        if self.cost.via_count != len(self.patch.vias) or self.metrics.vias != len(self.patch.vias):
            raise ValueError("layered candidate via accounting is inconsistent")
        if self.cost.via_cost_units != self.cost.via_count * self.settings.via_cost:
            raise ValueError("layered candidate via cost accounting is inconsistent")
        if self.metrics.wire_length_nm != self.patch.wire_length_nm:
            raise ValueError("layered candidate metric length must match patch geometry")
        if self.metrics.bend_count != self.patch.bend_count:
            raise ValueError("layered candidate bend accounting is inconsistent")
        if self.metrics.expanded_states > self.settings.max_expansions:
            raise ValueError("layered candidate exceeds its expansion budget")
        if self.metrics.discovered_states > self.settings.max_nodes:
            raise ValueError("layered candidate exceeds its node budget")
        if self.metrics.obstacle_checks > self.settings.max_obstacle_checks:
            raise ValueError("layered candidate exceeds its obstacle-check budget")


def canonical_layered_candidate_bytes(candidate: LayeredRouteCandidate) -> bytes:
    """Return canonical identity bytes, excluding the circular ``candidate_id`` field."""

    settings_payload: dict[str, int] = {
        "max_expansions": candidate.settings.max_expansions,
        "max_nodes": candidate.settings.max_nodes,
        "max_obstacle_checks": candidate.settings.max_obstacle_checks,
        "max_obstacles": candidate.settings.max_obstacles,
        "move_cost": candidate.settings.move_cost,
        "via_cost": candidate.settings.via_cost,
    }
    # Preserve the exact historic two-layer candidate bytes when no cap is supplied.  Only the
    # *stated* cap enters identity, never the derived effective cap: the effective cap is a pure
    # function of ``settings.max_vias`` and the stack width, and the stack width is already pinned
    # by ``base_revision``, which is in these bytes.  Two candidates with equal bytes therefore
    # always ran under the same effective cap, so folding the derived value in would add no
    # discrimination while breaking every two-layer identity ever issued.
    if candidate.settings.max_vias is not None:
        settings_payload["max_vias"] = candidate.settings.max_vias
    # `fill_binding` is present only when there is one, for the reason ADR-0103 gives for the
    # single-layer candidate and which holds identically here.  A candidate routed under the
    # conservative envelope is the same proposal it has always been, so its published content
    # address must not move; emitting `"fill_binding":null` would move *every* layered identity
    # at once to record an absence, including the two-, three- and four-layer values pinned in
    # `tests/test_layered_board_adapter.py` and the durable export pinned in
    # `tests/test_golden_identities.py`, and would break every persisted candidate from every
    # earlier router version, because `verify_layered_candidate_id` recomputes the address from a
    # rehydrated candidate's own fields.  `LAYERED_ROUTER_VERSION` therefore does not move.
    fill_binding = (
        {"fill_binding": candidate.fill_binding} if candidate.fill_binding is not None else {}
    )
    payload = {
        **fill_binding,
        "base_revision": candidate.base_revision,
        "cost": {
            "total_search_cost_units": candidate.cost.total_search_cost_units,
            "via_cost_units": candidate.cost.via_cost_units,
            "via_count": candidate.cost.via_count,
            "wire_length_nm": candidate.cost.wire_length_nm,
        },
        "end_pad_id": candidate.end_pad_id,
        "metrics": {
            "bend_count": candidate.metrics.bend_count,
            "discovered_states": candidate.metrics.discovered_states,
            "expanded_states": candidate.metrics.expanded_states,
            "move_steps": candidate.metrics.move_steps,
            "obstacle_checks": candidate.metrics.obstacle_checks,
            "peak_frontier_states": candidate.metrics.peak_frontier_states,
            "vias": candidate.metrics.vias,
            "wire_length_nm": candidate.metrics.wire_length_nm,
        },
        "patch": {
            "net_id": candidate.patch.net_id,
            "paths": [
                {
                    "layer_id": path.layer_id,
                    "vertices": [{"x_nm": point.x, "y_nm": point.y} for point in path.vertices],
                }
                for path in candidate.patch.paths
            ],
            "via_diameter_nm": candidate.patch.via_diameter_nm,
            "via_drill_nm": candidate.patch.via_drill_nm,
            "vias": [
                {
                    "center": {"x_nm": via.center.x, "y_nm": via.center.y},
                    "diameter_nm": via.diameter_nm,
                    "drill_nm": via.drill_nm,
                    "end_layer_id": via.end_layer_id,
                    "id": via.id,
                    "start_layer_id": via.start_layer_id,
                }
                for via in candidate.patch.vias
            ],
            "width_nm": candidate.patch.width_nm,
        },
        "policy": candidate.policy,
        "router_version": candidate.router_version,
        "seed": candidate.seed,
        "settings": settings_payload,
        "start_pad_id": candidate.start_pad_id,
    }
    rendered = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    return rendered.encode("utf-8", errors="strict") + b"\n"


def verify_layered_candidate_id(candidate: LayeredRouteCandidate) -> bool:
    expected = (
        f"{_SHA256}{hashlib.sha256(canonical_layered_candidate_bytes(candidate)).hexdigest()}"
    )
    if candidate.candidate_id != expected:
        raise ValueError("layered candidate ID does not match canonical route content")
    return True


@dataclass(frozen=True, slots=True)
class LayeredRouteResult:
    candidate: LayeredRouteCandidate | None = None
    diagnostic: LayeredRouteDiagnostic | None = None

    def __post_init__(self) -> None:
        if (self.candidate is None) == (self.diagnostic is None):
            raise ValueError("layered route result must contain exactly one outcome")
        if self.candidate is not None and not isinstance(self.candidate, LayeredRouteCandidate):
            raise ValueError("layered route result candidate is malformed")
        if self.diagnostic is not None and not isinstance(self.diagnostic, LayeredRouteDiagnostic):
            raise ValueError("layered route result diagnostic is malformed")

    @property
    def ok(self) -> bool:
        return self.candidate is not None
