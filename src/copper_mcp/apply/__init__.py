"""Route-candidate application.

Only the pure engine exists today: it computes the bytes an apply *would* write and proves
them correct. The mutating path - authorization tokens, lockfile refusal, compare-and-swap
against the board's digest, and atomic replacement - is designed but not yet implemented, and
there is deliberately no function here that touches the filesystem.
"""

from __future__ import annotations

from copper_mcp.apply.engine import (
    AppliedBoard,
    ApplyEngineError,
    ApplyVerification,
    apply_route_candidate,
)

__all__ = [
    "AppliedBoard",
    "ApplyEngineError",
    "ApplyVerification",
    "apply_route_candidate",
]
