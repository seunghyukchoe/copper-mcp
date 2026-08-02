"""Routing contracts.

The first repository milestone intentionally ships contracts, not a pretend
autorouter. Implementations must produce immutable candidates and pass exact
validation before they can be applied.
"""

from copper_mcp.routing.contracts import RouteRequest, RoutingBackend

__all__ = ["RouteRequest", "RoutingBackend"]
