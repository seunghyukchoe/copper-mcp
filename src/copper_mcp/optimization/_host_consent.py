"""Bounded expiring server-owned challenges for optimization host consent."""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Literal, NoReturn

from copper_mcp.optimization.contracts import OptimizationError

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_MAX_PENDING = 128
_TTL_SECONDS = 120


class HostConsentError(OptimizationError):
    """Fixed refusal without owner, command, package, judge, or challenge context."""


def _fail() -> NoReturn:
    raise HostConsentError("optimization host consent is unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class _ConsentBinding:
    owner: str
    purpose: Literal["geometry", "approval"]
    job_id: str
    record_revision: int
    package_digest: str
    judge_digest: str

    def __repr__(self) -> str:
        return "<_ConsentBinding redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class _Pending:
    binding: _ConsentBinding
    challenge: str
    expires_at: float

    def __repr__(self) -> str:
        return "<_PendingHostConsent redacted>"


def _validate(binding: object) -> _ConsentBinding:
    if type(binding) is not _ConsentBinding:
        _fail()
    owner: object = binding.owner
    purpose: object = binding.purpose
    job_id: object = binding.job_id
    record_revision: object = binding.record_revision
    package_digest: object = binding.package_digest
    judge_digest: object = binding.judge_digest
    if (
        type(owner) is not str
        or _DIGEST.fullmatch(owner) is None
        or type(purpose) is not str
        or purpose not in ("geometry", "approval")
        or type(job_id) is not str
        or _DIGEST.fullmatch(job_id) is None
        or type(record_revision) is not int
        or not 0 <= record_revision <= (1 << 53) - 1
        or type(package_digest) is not str
        or _DIGEST.fullmatch(package_digest) is None
        or type(judge_digest) is not str
        or _DIGEST.fullmatch(judge_digest) is None
    ):
        _fail()
    return binding


class _PendingHostConsents:
    """One process-local pending set; loss on restart is fail-closed."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: dict[tuple[str, str, str], _Pending] = {}

    def _purge(self, now: float) -> None:
        expired = [key for key, item in self._pending.items() if item.expires_at <= now]
        for key in expired:
            del self._pending[key]

    def issue_challenge(self, binding: _ConsentBinding) -> str:
        admitted = _validate(binding)
        with self._lock:
            now = time.monotonic()
            self._purge(now)
            key = admitted.owner, admitted.purpose, admitted.job_id
            existing = self._pending.get(key)
            if existing is not None and existing.binding == admitted:
                return existing.challenge
            if existing is None and len(self._pending) >= _MAX_PENDING:
                _fail()
            challenge = secrets.token_urlsafe(32)
            if _CHALLENGE.fullmatch(challenge) is None or any(
                item.challenge == challenge for item in self._pending.values()
            ):
                _fail()
            self._pending[key] = _Pending(admitted, challenge, now + _TTL_SECONDS)
            return challenge

    def consume(self, binding: _ConsentBinding, challenge: object) -> None:
        admitted = _validate(binding)
        if type(challenge) is not str or _CHALLENGE.fullmatch(challenge) is None:
            _fail()
        with self._lock:
            self._purge(time.monotonic())
            key = admitted.owner, admitted.purpose, admitted.job_id
            current = self._pending.get(key)
            if current is None or current.binding != admitted or current.challenge != challenge:
                _fail()
            del self._pending[key]


__all__: list[str] = []
