"""Candidate application.

``engine`` is pure: it computes the bytes an apply would write and proves them correct, and
touches no filesystem. ``service`` is the only code in this project that changes a user's
board, and it does so only behind an operator flag, a single-use token, a lockfile refusal, a
double compare-and-swap, a pre-apply copy, and an atomic replacement that is verified after
publication.
"""

from __future__ import annotations

from copper_mcp.apply.contracts import (
    APPLY_VERSION,
    PLACEMENT_APPLY_VERSION,
    ApplyDiagnostic,
    ApplyFailureCode,
    ApplyRequest,
    ApplyRequestError,
    ApplyResult,
    PlacementApplyRequest,
    PlacementApplyResult,
    parse_apply_request,
    parse_placement_apply_request,
)
from copper_mcp.apply.engine import (
    AppliedBoard,
    ApplyEngineError,
    ApplyVerification,
    apply_route_candidate,
)
from copper_mcp.apply.placement_engine import (
    AppliedPlacementBoard,
)
from copper_mcp.apply.placement_engine import (
    apply_placement_candidate as apply_placement_candidate_bytes,
)
from copper_mcp.apply.service import (
    apply_candidate,
    apply_placement_candidate,
    lockfile_for,
)
from copper_mcp.apply.tokens import (
    ApplyBinding,
    ApplyTokenAuthority,
    ApplyTokenError,
    LiveApplyBinding,
    VerifiedToken,
)

__all__ = [
    "APPLY_VERSION",
    "PLACEMENT_APPLY_VERSION",
    "AppliedBoard",
    "AppliedPlacementBoard",
    "ApplyBinding",
    "ApplyDiagnostic",
    "ApplyEngineError",
    "ApplyFailureCode",
    "ApplyRequest",
    "ApplyRequestError",
    "ApplyResult",
    "ApplyTokenAuthority",
    "ApplyTokenError",
    "ApplyVerification",
    "LiveApplyBinding",
    "PlacementApplyRequest",
    "PlacementApplyResult",
    "VerifiedToken",
    "apply_candidate",
    "apply_placement_candidate",
    "apply_placement_candidate_bytes",
    "apply_route_candidate",
    "lockfile_for",
    "parse_apply_request",
    "parse_placement_apply_request",
]
