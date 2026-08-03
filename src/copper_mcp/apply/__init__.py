"""Route-candidate application.

``engine`` is pure: it computes the bytes an apply would write and proves them correct, and
touches no filesystem. ``service`` is the only code in this project that changes a user's
board, and it does so only behind an operator flag, a single-use token, a lockfile refusal, a
double compare-and-swap, a pre-apply copy, and an atomic replacement that is verified after
publication.
"""

from __future__ import annotations

from copper_mcp.apply.contracts import (
    APPLY_VERSION,
    ApplyDiagnostic,
    ApplyFailureCode,
    ApplyRequest,
    ApplyRequestError,
    ApplyResult,
    parse_apply_request,
)
from copper_mcp.apply.engine import (
    AppliedBoard,
    ApplyEngineError,
    ApplyVerification,
    apply_route_candidate,
)
from copper_mcp.apply.service import apply_candidate, lockfile_for
from copper_mcp.apply.tokens import (
    ApplyBinding,
    ApplyTokenAuthority,
    ApplyTokenError,
    VerifiedToken,
)

__all__ = [
    "APPLY_VERSION",
    "AppliedBoard",
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
    "VerifiedToken",
    "apply_candidate",
    "apply_route_candidate",
    "lockfile_for",
    "parse_apply_request",
]
