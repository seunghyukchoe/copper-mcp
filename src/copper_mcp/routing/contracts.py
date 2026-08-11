"""Exact, backend-neutral contracts for immutable route candidates.

This module owns the stable seam between the routing core and everything that consumes it: the
request, patch, candidate, and diagnostic shapes, in exact integer units, with candidates
content-addressed and bound to the Board IR revision they were computed against. It is
backend-neutral by design — a future Rust or GPU router implements these same types, which is
why AGENTS.md names this file as the boundary such a backend may appear behind.

It refuses to be an implementation. There is no search, no cost model, no obstacle collection,
no serialization to KiCad bytes, and no apply authority here. A candidate constructed through
these types is a proposal and nothing more: it carries no DRC evidence, and it becomes stale the
moment its base revision changes rather than being refreshed in place.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Protocol

from copper_mcp.board_ir import BoardIRSnapshot, PointNM

_JSON_SAFE_INTEGER = (1 << 53) - 1
_SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
_STABLE_NAME = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_MAX_GRID_NODES = 500_000
_MAX_EXPANSIONS = 1_000_000
_MAX_COST_TERM_NM = 1_000_000_000
_MAX_OBSTACLES = 32_768
_MAX_NET_OBJECTS = 4_096
_MAX_REGION_MARGIN_NM = 1_000_000_000
_MAX_OBSTACLE_CHECKS = 10_000_000

#: Ordering policy recorded by a candidate whose patch is a single path.
SINGLE_PATH_ORDERING = "single-path"
#: Deterministic minimum spanning tree over the net's initial connected components.
COMPONENT_MST_ORDERING = "component-mst-v1"
#: Bounded clean-room one-Steiner ordering heuristic over evolving component envelopes.
#:
#: This is deliberately not named FLUTE: the policy uses a median-point lower-cost signal to
#: choose which components to merge, while the existing exact A* leg search still constructs and
#: validates every emitted path.  The name is part of candidate identity so replay never
#: silently changes topology.
BATCHED_ONE_STEINER_ORDERING = "batched-1-steiner-v1"


def _integer(name: str, value: int, *, minimum: int = 0, maximum: int = _JSON_SAFE_INTEGER) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
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
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be content-addressed with sha256")


def _stable_name(name: str, value: str) -> None:
    if not isinstance(value, str) or not _STABLE_NAME.fullmatch(value):
        raise ValueError(f"{name} must be a stable lowercase identifier")


@dataclass(frozen=True, slots=True)
class AStarSettings:
    """Integer-only policy and resource limits for the reference A* router.

    Three of these budgets meter three structurally different populations, and the
    calibration note ``docs/research/route-obstacle-budget-calibration-v1.md`` records the
    measurement each default comes from:

    - ``max_obstacles`` bounds the **region-scoped** foreign selected-layer obstacle model —
      copper the route has to avoid, restricted to the region the lattice can reach.
    - ``max_net_objects`` bounds the **same-net copper model** — the routed net's own pads,
      tracks, vias, and verified fill islands across every layer, which decide connectivity and
      attachment rather than obstruction. Its ceiling is quadratic work, not board size.
    - ``max_obstacle_checks`` bounds the exact geometric predicates one request may evaluate.

    ``region_margin_nm`` is the width of the corridor added around the routed net's own copper
    to form that region. It is a capability knob and a work bound at once: the lattice cannot
    leave the region, so nothing outside it is modelled, and nothing outside it can be routed
    through either.
    """

    grid_step_nm: int = 250_000
    bend_penalty_nm: int = 500_000
    proximity_penalty_nm: int = 50_000
    max_grid_nodes: int = 250_000
    max_expansions: int = 100_000
    max_obstacles: int = 4_096
    max_net_objects: int = 1_024
    region_margin_nm: int = 10_000_000
    max_obstacle_checks: int = 2_000_000

    def __post_init__(self) -> None:
        _integer("grid step", self.grid_step_nm, minimum=1, maximum=_MAX_COST_TERM_NM)
        _integer("bend penalty", self.bend_penalty_nm, maximum=_MAX_COST_TERM_NM)
        _integer("proximity penalty", self.proximity_penalty_nm, maximum=_MAX_COST_TERM_NM)
        _integer("grid-node budget", self.max_grid_nodes, minimum=1, maximum=_MAX_GRID_NODES)
        _integer("expansion budget", self.max_expansions, minimum=1, maximum=_MAX_EXPANSIONS)
        _integer("obstacle budget", self.max_obstacles, minimum=1, maximum=_MAX_OBSTACLES)
        _integer("net-object budget", self.max_net_objects, minimum=1, maximum=_MAX_NET_OBJECTS)
        _integer(
            "routing-region margin",
            self.region_margin_nm,
            minimum=1,
            maximum=_MAX_REGION_MARGIN_NM,
        )
        _integer(
            "obstacle-check budget",
            self.max_obstacle_checks,
            minimum=1,
            maximum=_MAX_OBSTACLE_CHECKS,
        )


@dataclass(frozen=True, slots=True)
class RouteRequest:
    """A deterministic two-pin routing request against one immutable board revision."""

    board_revision: str
    net_id: str
    layer_id: str
    seed: int
    settings: AStarSettings = AStarSettings()

    def __post_init__(self) -> None:
        _digest("board revision", self.board_revision)
        _typed_id("net ID", self.net_id, "net:")
        _typed_id("layer ID", self.layer_id, "layer:")
        _integer("seed", self.seed)
        if not isinstance(self.settings, AStarSettings):
            raise ValueError("settings must be an AStarSettings value")


@dataclass(frozen=True, slots=True)
class RoutePath:
    """One orthogonal polyline of a route patch."""

    vertices: tuple[PointNM, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.vertices, tuple)
            or len(self.vertices) < 2
            or not all(isinstance(point, PointNM) for point in self.vertices)
        ):
            raise ValueError("route vertices must be an immutable tuple of at least two points")
        for start, end in pairwise(self.vertices):
            if start == end or (start.x != end.x and start.y != end.y):
                raise ValueError("route edges must be non-zero and orthogonal")
        for first, middle, last in zip(
            self.vertices, self.vertices[1:], self.vertices[2:], strict=False
        ):
            if (first.x == middle.x == last.x) or (first.y == middle.y == last.y):
                raise ValueError("route vertices must omit collinear interior points")

    @property
    def length_nm(self) -> int:
        """Return the exact Manhattan length of this polyline."""

        return sum(
            abs(start.x - end.x) + abs(start.y - end.y) for start, end in pairwise(self.vertices)
        )

    @property
    def bend_count(self) -> int:
        """Return the number of direction changes, which is its interior vertex count."""

        return len(self.vertices) - 2


@dataclass(frozen=True, slots=True)
class RoutePatch:
    """One unapplied, single-layer, orthogonal route geometry patch of one or more paths.

    A two-pin proposal carries exactly one path. A multi-pin proposal carries one path per
    merged component, so a patch is a tree over the net rather than a bag of wires.
    """

    net_id: str
    layer_id: str
    width_nm: int
    paths: tuple[RoutePath, ...]

    def __post_init__(self) -> None:
        _typed_id("net ID", self.net_id, "net:")
        _typed_id("layer ID", self.layer_id, "layer:")
        _integer("route width", self.width_nm, minimum=1)
        if (
            not isinstance(self.paths, tuple)
            or not self.paths
            or not all(isinstance(path, RoutePath) for path in self.paths)
        ):
            raise ValueError("a route patch must carry at least one route path")

    @property
    def length_nm(self) -> int:
        """Return the exact Manhattan length of every path in the patch."""

        return sum(path.length_nm for path in self.paths)

    @property
    def bend_count(self) -> int:
        """Return the total direction changes across every path in the patch."""

        return sum(path.bend_count for path in self.paths)


@dataclass(frozen=True, slots=True)
class RouteCost:
    """Exact additive cost decomposition for one route candidate."""

    length_nm: int
    bend_count: int
    bend_cost_nm: int
    proximity_steps: int
    proximity_cost_nm: int
    via_cost_nm: int
    total_cost_nm: int

    def __post_init__(self) -> None:
        for name, value in (
            ("route length", self.length_nm),
            ("bend count", self.bend_count),
            ("bend cost", self.bend_cost_nm),
            ("proximity steps", self.proximity_steps),
            ("proximity cost", self.proximity_cost_nm),
            ("via cost", self.via_cost_nm),
            ("total cost", self.total_cost_nm),
        ):
            _integer(name, value)
        expected = self.length_nm + self.bend_cost_nm + self.proximity_cost_nm + self.via_cost_nm
        if self.total_cost_nm != expected:
            raise ValueError("total route cost must equal its exact additive components")


@dataclass(frozen=True, slots=True)
class RouteMetrics:
    """Deterministic correctness and search metrics; never wall-clock telemetry."""

    hard_internal_violations: int
    unrouted_connections: int
    vias: int
    wire_length_nm: int
    expanded_states: int
    peak_frontier_states: int
    obstacle_checks: int

    def __post_init__(self) -> None:
        for name, value in (
            ("hard internal violations", self.hard_internal_violations),
            ("unrouted connections", self.unrouted_connections),
            ("via count", self.vias),
            ("wire length", self.wire_length_nm),
            ("expanded states", self.expanded_states),
            ("peak frontier states", self.peak_frontier_states),
            ("obstacle checks", self.obstacle_checks),
        ):
            _integer(name, value)


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    """A content-addressed, immutable, unapplied routing proposal."""

    candidate_id: str
    base_revision: str
    start_pad_id: str
    end_pad_id: str
    patch: RoutePatch
    cost: RouteCost
    metrics: RouteMetrics
    settings: AStarSettings
    router_version: str
    policy: str
    seed: int
    pad_count: int = 2
    ordering_policy: str = SINGLE_PATH_ORDERING

    def __post_init__(self) -> None:
        _digest("candidate ID", self.candidate_id)
        _digest("base revision", self.base_revision)
        _typed_id("start pad ID", self.start_pad_id, "pad:")
        _typed_id("end pad ID", self.end_pad_id, "pad:")
        if self.start_pad_id == self.end_pad_id:
            raise ValueError("route endpoints must be distinct pads")
        if not isinstance(self.patch, RoutePatch):
            raise ValueError("candidate patch must be a RoutePatch")
        if not isinstance(self.cost, RouteCost) or not isinstance(self.metrics, RouteMetrics):
            raise ValueError("candidate cost and metrics are malformed")
        if not isinstance(self.settings, AStarSettings):
            raise ValueError("candidate settings must be AStarSettings")
        _stable_name("router version", self.router_version)
        _stable_name("routing policy", self.policy)
        _stable_name("ordering policy", self.ordering_policy)
        _integer("seed", self.seed)
        _integer("pad count", self.pad_count, minimum=2)
        # A tree over N components needs exactly N - 1 merges, and every pad beyond the first
        # two can only be reached by one more path.
        if len(self.patch.paths) > self.pad_count - 1:
            raise ValueError("a candidate cannot carry more paths than its pads can require")
        if self.pad_count == 2 and len(self.patch.paths) != 1:
            raise ValueError("a two-pin candidate must carry exactly one path")
        if self.pad_count == 2 and self.ordering_policy != SINGLE_PATH_ORDERING:
            raise ValueError("a two-pin candidate has no ordering policy to record")

        length_nm = self.patch.length_nm
        bend_count = self.patch.bend_count
        if self.cost.length_nm != length_nm or self.metrics.wire_length_nm != length_nm:
            raise ValueError("candidate length must match its exact patch geometry")
        if self.cost.bend_count != bend_count:
            raise ValueError("candidate bend count must match its compressed patch geometry")
        if self.cost.bend_cost_nm != bend_count * self.settings.bend_penalty_nm:
            raise ValueError("candidate bend cost must match its routing settings")
        if self.cost.proximity_cost_nm != (
            self.cost.proximity_steps * self.settings.proximity_penalty_nm
        ):
            raise ValueError("candidate proximity cost must match its routing settings")
        if (
            self.cost.via_cost_nm != 0
            or self.metrics.hard_internal_violations != 0
            or self.metrics.unrouted_connections != 0
            or self.metrics.vias != 0
        ):
            raise ValueError("a successful single-layer candidate must be internally clean")
        if self.metrics.expanded_states > self.settings.max_expansions:
            raise ValueError("candidate expansions exceed its recorded resource ceiling")
        if self.metrics.obstacle_checks > self.settings.max_obstacle_checks:
            raise ValueError("candidate obstacle checks exceed its recorded resource ceiling")
        if self.metrics.peak_frontier_states < 1:
            raise ValueError("a successful candidate must record a non-empty frontier")


@dataclass(frozen=True, slots=True)
class RouteConnection:
    """Exact evidence that every pad of a net already shares one selected-layer component.

    ``start_pad_id`` and ``end_pad_id`` are the lexicographically first and last of the net's
    pads on the layer. For the two-pin case those are simply its two pads; for a wider net they
    bound the set rather than naming a route, because a connected net has no route to name.
    ``pad_count`` is what tells the two apart. A non-zero ``vias`` means the connection was
    established across copper layers through those vias, so the evidence is multilayer even
    though the request names one layer. A non-zero ``fill_polygons`` means poured zone copper
    carried part of the connection, which is only ever admitted alongside freshness evidence
    that the board's cached fill is what KiCad recomputes from it today.
    """

    base_revision: str
    start_pad_id: str
    end_pad_id: str
    attachment_segments: int
    component_objects: int
    pad_count: int = 2
    vias: int = 0
    fill_polygons: int = 0
    obstacle_checks: int = 0

    def __post_init__(self) -> None:
        _digest("base revision", self.base_revision)
        _typed_id("start pad ID", self.start_pad_id, "pad:")
        _typed_id("end pad ID", self.end_pad_id, "pad:")
        if self.start_pad_id == self.end_pad_id:
            raise ValueError("connected pads must be distinct")
        _integer("attachment segments", self.attachment_segments)
        _integer("pad count", self.pad_count, minimum=2)
        _integer("via count", self.vias)
        _integer("fill polygon count", self.fill_polygons)
        _integer("component objects", self.component_objects, minimum=2)
        _integer("connection obstacle checks", self.obstacle_checks)
        if self.component_objects != (
            self.attachment_segments + self.pad_count + self.vias + self.fill_polygons
        ):
            raise ValueError(
                "a connected component must account for every pad, segment, via and fill island"
            )


class RouteFailureCode(StrEnum):
    """Stable failure taxonomy for expected, fail-closed routing outcomes."""

    INVALID_SNAPSHOT = "invalid_snapshot"
    INVALID_REQUEST = "invalid_request"
    STALE_REVISION = "stale_revision"
    INVALID_TWO_PIN_NET = "invalid_two_pin_net"
    UNSUPPORTED_CONSTRAINT = "unsupported_constraint"
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"
    OFF_GRID = "off_grid"
    GRID_BUDGET_EXCEEDED = "grid_budget_exceeded"
    OBSTACLE_BUDGET_EXCEEDED = "obstacle_budget_exceeded"
    NET_OBJECT_BUDGET_EXCEEDED = "net_object_budget_exceeded"
    OBSTACLE_CHECK_BUDGET_EXCEEDED = "obstacle_check_budget_exceeded"
    SEARCH_BUDGET_EXCEEDED = "search_budget_exceeded"
    CANCELLED = "cancelled"
    STALE_FILL = "stale_fill"
    NO_PATH = "no_path"
    NO_PATH_IN_REGION = "no_path_in_region"


@dataclass(frozen=True, slots=True)
class OffGridEvidence:
    """Exact per-pad geometry for one ``off_grid`` refusal.

    A two-pin route joins pad centres, so the lattice is anchored at ``anchor_pad_id`` and
    ``pad_id`` is the centre that has to land on it. Every field is an exact integer computed
    from the request the caller made against bytes the caller supplied; nothing here is
    estimated, and nothing here counts board objects.

    ``miss_x_nm`` and ``miss_y_nm`` are signed displacements *from the nearest lattice line to
    the pad centre*, so moving the pad by ``(-miss_x_nm, -miss_y_nm)`` puts it on the lattice
    and ``abs(miss) <= grid_step_nm // 2`` on each axis. At least one of them is non-zero,
    because a pad that misses on neither axis is on the lattice.

    ``largest_representable_step_nm`` is the greatest common divisor of the two centre deltas:
    the largest lattice step at which this pad pair is representable at all. It is a statement
    about representability and **not** a prediction that routing succeeds there. B-100 measured
    18 real-board ``off_grid`` refusals, re-previewed every one of them at exactly this step
    with ``max_grid_nodes`` at its ceiling, and routed **none**: five then exceeded the node
    budget and thirteen had a pad centre outside the board outline inset by half the routed
    track width. Ten of the eighteen report 1 or
    3 nm here, because KiCad writes millimetre coordinates one nanometre short of the round
    value the part was placed at -- which is board content, not a conversion defect, and
    collapses the divisor without moving the pad by anything a designer could see.
    """

    pad_id: str
    anchor_pad_id: str
    grid_step_nm: int
    miss_x_nm: int
    miss_y_nm: int
    largest_representable_step_nm: int

    def __post_init__(self) -> None:
        _typed_id("off-grid pad ID", self.pad_id, "pad:")
        _typed_id("off-grid anchor pad ID", self.anchor_pad_id, "pad:")
        if self.pad_id == self.anchor_pad_id:
            raise ValueError("an off-grid pad and its lattice anchor must differ")
        _integer("off-grid grid step", self.grid_step_nm, minimum=1, maximum=_MAX_COST_TERM_NM)
        _integer("off-grid representable step", self.largest_representable_step_nm, minimum=1)
        half_step = self.grid_step_nm // 2
        for axis, miss in (("x", self.miss_x_nm), ("y", self.miss_y_nm)):
            _integer(f"off-grid {axis} miss", miss, minimum=-half_step, maximum=half_step)
        if self.miss_x_nm == 0 and self.miss_y_nm == 0:
            raise ValueError("an off-grid pad must miss the lattice on at least one axis")
        # The requested step represents this centre exactly when it divides the greatest common
        # divisor of the two deltas. A larger divisor is not a contradiction — a centre 8,001 nm
        # away is representable at 8,001 nm and not at 1,000 nm — so the invariant is
        # divisibility, never magnitude.
        if self.largest_representable_step_nm % self.grid_step_nm == 0:
            raise ValueError("an off-grid pad pair must not be representable at the requested step")


@dataclass(frozen=True, slots=True)
class RouteDiagnostic:
    """Non-echoing diagnostic for one expected routing failure."""

    code: RouteFailureCode
    message: str
    expanded_states: int = 0
    obstacle_checks: int = 0
    off_grid: OffGridEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, RouteFailureCode):
            raise ValueError("diagnostic code must use RouteFailureCode")
        if not isinstance(self.message, str) or not 1 <= len(self.message) <= 256:
            raise ValueError("diagnostic message is malformed")
        _integer("diagnostic expanded states", self.expanded_states)
        _integer("diagnostic obstacle checks", self.obstacle_checks)
        # The evidence is carried by exactly the code it explains. Attaching it to another code
        # would let a caller read lattice geometry out of a refusal that never measured any.
        if (self.off_grid is not None) is not (self.code is RouteFailureCode.OFF_GRID):
            raise ValueError("off-grid evidence belongs to the off_grid diagnostic alone")
        if self.off_grid is not None and not isinstance(self.off_grid, OffGridEvidence):
            raise ValueError("off-grid evidence must be typed")


@dataclass(frozen=True, slots=True)
class RouteResult:
    """Exactly one candidate, one already-connected record, or one expected diagnostic."""

    candidate: RouteCandidate | None = None
    connected: RouteConnection | None = None
    diagnostic: RouteDiagnostic | None = None

    def __post_init__(self) -> None:
        present = sum(
            value is not None for value in (self.candidate, self.connected, self.diagnostic)
        )
        if present != 1:
            raise ValueError(
                "route result must contain exactly one candidate, connection, or diagnostic"
            )
        if self.candidate is not None and not isinstance(self.candidate, RouteCandidate):
            raise ValueError("route result candidate is malformed")
        if self.connected is not None and not isinstance(self.connected, RouteConnection):
            raise ValueError("route result connection is malformed")
        if self.diagnostic is not None and not isinstance(self.diagnostic, RouteDiagnostic):
            raise ValueError("route result diagnostic is malformed")

    @property
    def ok(self) -> bool:
        """Return true only when an immutable candidate is present."""

        return self.candidate is not None

    @property
    def terminal(self) -> bool:
        """Return true when routing succeeded or was provably unnecessary."""

        return self.candidate is not None or self.connected is not None


CancellationCheck = Callable[[], bool]

# Internal, candidate-only policy hook used by negotiated multi-net routing.  The callback is
# intentionally expressed in world coordinates rather than lattice indices so the deterministic
# A* core does not expose its private search state to policy code.  A penalty is an integer search
# ordering term; it is never included in a candidate's physical cost or treated as DRC evidence.
CongestionPenalty = Callable[[PointNM, PointNM], int]


class RoutingBackend(Protocol):
    """Contract implemented by deterministic CPU and future accelerated backends."""

    @property
    def name(self) -> str:
        """Return a stable backend identifier."""

    def propose(
        self,
        snapshot: BoardIRSnapshot,
        request: RouteRequest,
        *,
        cancelled: CancellationCheck | None = None,
    ) -> RouteResult:
        """Produce an immutable, unapplied candidate or a bounded failure."""
