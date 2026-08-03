"""Exact, backend-neutral contracts for immutable route candidates."""

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
_MAX_OBSTACLES = 4_096
_MAX_OBSTACLE_CHECKS = 10_000_000


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
    """Integer-only policy and resource limits for the reference A* router."""

    grid_step_nm: int = 250_000
    bend_penalty_nm: int = 500_000
    proximity_penalty_nm: int = 50_000
    max_grid_nodes: int = 250_000
    max_expansions: int = 100_000
    max_obstacles: int = 256
    max_obstacle_checks: int = 2_000_000

    def __post_init__(self) -> None:
        _integer("grid step", self.grid_step_nm, minimum=1, maximum=_MAX_COST_TERM_NM)
        _integer("bend penalty", self.bend_penalty_nm, maximum=_MAX_COST_TERM_NM)
        _integer("proximity penalty", self.proximity_penalty_nm, maximum=_MAX_COST_TERM_NM)
        _integer("grid-node budget", self.max_grid_nodes, minimum=1, maximum=_MAX_GRID_NODES)
        _integer("expansion budget", self.max_expansions, minimum=1, maximum=_MAX_EXPANSIONS)
        _integer("obstacle budget", self.max_obstacles, minimum=1, maximum=_MAX_OBSTACLES)
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
class RoutePatch:
    """One unapplied, single-layer, orthogonal route geometry patch."""

    net_id: str
    layer_id: str
    width_nm: int
    vertices: tuple[PointNM, ...]

    def __post_init__(self) -> None:
        _typed_id("net ID", self.net_id, "net:")
        _typed_id("layer ID", self.layer_id, "layer:")
        _integer("route width", self.width_nm, minimum=1)
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
        _integer("seed", self.seed)

        length_nm = sum(
            abs(start.x - end.x) + abs(start.y - end.y)
            for start, end in pairwise(self.patch.vertices)
        )
        bend_count = len(self.patch.vertices) - 2
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
    """Exact evidence that both pads already share one selected-layer copper component."""

    base_revision: str
    start_pad_id: str
    end_pad_id: str
    attachment_segments: int
    component_objects: int
    obstacle_checks: int = 0

    def __post_init__(self) -> None:
        _digest("base revision", self.base_revision)
        _typed_id("start pad ID", self.start_pad_id, "pad:")
        _typed_id("end pad ID", self.end_pad_id, "pad:")
        if self.start_pad_id == self.end_pad_id:
            raise ValueError("route endpoints must be distinct pads")
        _integer("attachment segments", self.attachment_segments, minimum=1)
        _integer("component objects", self.component_objects, minimum=3)
        _integer("connection obstacle checks", self.obstacle_checks)
        if self.component_objects != self.attachment_segments + 2:
            raise ValueError("a connected component must account for both pads and every segment")


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
    SEARCH_BUDGET_EXCEEDED = "search_budget_exceeded"
    CANCELLED = "cancelled"
    NO_PATH = "no_path"


@dataclass(frozen=True, slots=True)
class RouteDiagnostic:
    """Non-echoing diagnostic for one expected routing failure."""

    code: RouteFailureCode
    message: str
    expanded_states: int = 0
    obstacle_checks: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.code, RouteFailureCode):
            raise ValueError("diagnostic code must use RouteFailureCode")
        if not isinstance(self.message, str) or not 1 <= len(self.message) <= 256:
            raise ValueError("diagnostic message is malformed")
        _integer("diagnostic expanded states", self.expanded_states)
        _integer("diagnostic obstacle checks", self.obstacle_checks)


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
