"""Deterministic candidate-first routing contracts and reference backends."""

from copper_mcp.routing.astar import (
    ROUTER_VERSION,
    ROUTING_POLICY,
    AStarRouter,
    canonical_candidate_bytes,
    verify_candidate_id,
)
from copper_mcp.routing.contracts import (
    AStarSettings,
    CancellationCheck,
    RouteCandidate,
    RouteCost,
    RouteDiagnostic,
    RouteFailureCode,
    RouteMetrics,
    RoutePatch,
    RouteRequest,
    RouteResult,
    RoutingBackend,
)

__all__ = [
    "ROUTER_VERSION",
    "ROUTING_POLICY",
    "AStarRouter",
    "AStarSettings",
    "CancellationCheck",
    "RouteCandidate",
    "RouteCost",
    "RouteDiagnostic",
    "RouteFailureCode",
    "RouteMetrics",
    "RoutePatch",
    "RouteRequest",
    "RouteResult",
    "RoutingBackend",
    "canonical_candidate_bytes",
    "verify_candidate_id",
]
