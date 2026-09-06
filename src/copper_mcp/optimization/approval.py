"""Process-local, single-use review consent. Not registered in MCP and not apply authority.

The host owns the human channel: it may call issue_from_human_channel only after a genuine
human confirmation. This object cannot distinguish a human from privileged same-process code.
No caller-provided boolean, claimed actor name, or model tool call is accepted as consent.
"""

from __future__ import annotations

import math
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from copper_mcp.optimization.contracts import OptimizationError, digest_document
from copper_mcp.optimization.lifecycle import OptimizationJobRecord
from copper_mcp.optimization.package import OptimizationPackage

_TTL_SECONDS = 600
_CAPACITY = 128


@dataclass(frozen=True, slots=True)
class _Ticket:
    job_id: str
    revision: int
    owner_binding: str
    request_digest: str
    package_digest: str
    judge_digest: str


def _ticket(record: OptimizationJobRecord, package: OptimizationPackage, owner: str) -> _Ticket:
    if (
        record.status != "awaiting_approval"
        or record.owner_binding != owner
        or record.package_digest != package.digest
        or record.judge_digest != package.judge.digest
        or not package.judge.reviewable
    ):
        raise OptimizationError("optimization confirmation is unavailable")
    return _Ticket(
        record.job_id,
        record.revision,
        owner,
        record.request_digest,
        package.digest,
        package.judge.digest,
    )


class HumanApprovalAuthority:
    """Bounded ephemeral capabilities; restarting invalidates every unconsumed confirmation."""

    def __init__(
        self, *, enabled: bool = False, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if type(enabled) is not bool:
            raise OptimizationError("optimization confirmation is unavailable")
        self._enabled = enabled
        self._clock = clock
        self._tokens: dict[str, tuple[float, _Ticket]] = {}
        self._lock = threading.RLock()

    def _now(self) -> float:
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, int | float) or not math.isfinite(now):
            raise OptimizationError("optimization confirmation is unavailable")
        expired = [token for token, (deadline, _) in self._tokens.items() if now >= deadline]
        for token in expired:
            del self._tokens[token]
        return now

    def issue_from_human_channel(
        self,
        record: OptimizationJobRecord,
        package: OptimizationPackage,
        *,
        owner_binding: str,
    ) -> str:
        with self._lock:
            now = self._now()
            if not self._enabled or len(self._tokens) >= _CAPACITY:
                raise OptimizationError("optimization confirmation is unavailable")
            ticket = _ticket(record, package, owner_binding)
            token = secrets.token_hex(32)
            if token in self._tokens:
                raise OptimizationError("optimization confirmation is unavailable")
            self._tokens[token] = (now + _TTL_SECONDS, ticket)
            return token

    def consume(
        self,
        record: OptimizationJobRecord,
        package: OptimizationPackage,
        capability: str,
        *,
        owner_binding: str,
    ) -> str:
        with self._lock:
            self._now()
            if not self._enabled or type(capability) is not str or len(capability) != 64:
                raise OptimizationError("optimization confirmation is unavailable")
            ticket = _ticket(record, package, owner_binding)
            stored = self._tokens.get(capability)
            if stored is None or stored[1] != ticket:
                raise OptimizationError("optimization confirmation is unavailable")
            del self._tokens[capability]
            return digest_document(
                "copper-mcp/optimization/v1/human-review-receipt",
                {
                    "job_id": ticket.job_id,
                    "revision": ticket.revision,
                    "owner_binding": ticket.owner_binding,
                    "request_digest": ticket.request_digest,
                    "package_digest": ticket.package_digest,
                    "judge_digest": ticket.judge_digest,
                },
            )
