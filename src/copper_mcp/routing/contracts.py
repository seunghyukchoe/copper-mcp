"""Backend-neutral routing interfaces for classical and learned policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from copper_mcp.models import CandidateSummary


@dataclass(frozen=True, slots=True)
class RouteRequest:
    """A deterministic routing request against one immutable board revision."""

    board_revision: str
    net_names: tuple[str, ...]
    seed: int
    policy: str = "heuristic-v1"
    max_runtime_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.board_revision.startswith("sha256:"):
            raise ValueError("board_revision must be content-addressed with sha256")
        if not self.net_names or len(self.net_names) > 10_000:
            raise ValueError("net_names must contain between 1 and 10,000 entries")
        if len(set(self.net_names)) != len(self.net_names):
            raise ValueError("net_names must not contain duplicates")
        if isinstance(self.seed, bool) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")
        if not 1 <= self.max_runtime_seconds <= 86_400:
            raise ValueError("max_runtime_seconds must be between 1 and 86,400")


class RoutingBackend(Protocol):
    """Contract implemented by CPU, GPU, research, and remote backends."""

    @property
    def name(self) -> str:
        """Return a stable backend identifier."""

    def propose(self, request: RouteRequest) -> CandidateSummary:
        """Produce an immutable, unapplied candidate."""
