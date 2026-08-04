"""Placement intent and deterministic legalization."""

from __future__ import annotations

from copper_mcp.placement.contracts import (
    COURTYARD_POLICY,
    ORDERING_POLICY,
    PLACEMENT_VERSION,
    PlacementCandidate,
    PlacementError,
    PlacementFailureCode,
    PlacementIntent,
    PlacementLegality,
    PlacementResult,
    parse_placement_intent,
    verify_placement_id,
)
from copper_mcp.placement.legalizer import evaluate_placement
from copper_mcp.placement.view import (
    FootprintView,
    PlacementView,
    PlacementViewError,
    build_placement_view,
)

__all__ = [
    "COURTYARD_POLICY",
    "ORDERING_POLICY",
    "PLACEMENT_VERSION",
    "FootprintView",
    "PlacementCandidate",
    "PlacementError",
    "PlacementFailureCode",
    "PlacementIntent",
    "PlacementLegality",
    "PlacementResult",
    "PlacementView",
    "PlacementViewError",
    "build_placement_view",
    "evaluate_placement",
    "parse_placement_intent",
    "verify_placement_id",
]
