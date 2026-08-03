"""Single-use apply tokens.

Authorization for a destructive operation must be enforced by the server, not by the model
asking for it (OWASP LLM06). MCP tool annotations describe intent to a client and are advisory;
they stop nothing. So a route candidate cannot be applied unless the caller presents a token
this process issued for that exact candidate, that exact board, and that exact path.

The token is **self-verifying**: it carries a nonce and a MAC over the binding, and nothing
else. There is no server-side table mapping tokens to grants, which keeps the preview surface
stateless in the way the rest of the server is.

Single use comes from the binding rather than from bookkeeping. A token names the board
revision it was issued against; a successful apply changes the file, so the same token can
never match again and a replay fails the compare-and-swap as a stale candidate. The consumed
nonce set below is defence in depth on top of that - it turns a replay into a precise
``token_already_used`` instead of a staleness error, and it is deliberately process-local and
bounded.

**The signing key lives only in this process.** Restarting the server invalidates every
outstanding token. That is the right default for a short-lived confirmation: persisting the key
would create a secret at rest whose compromise would be far worse than the inconvenience of
re-previewing.
"""

from __future__ import annotations

import base64
import hmac
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

#: Domain separation, so a MAC minted here can never be confused with one from another
#: derivation in this project. Bump the suffix if the payload layout ever changes.
_DOMAIN = b"copper-mcp/apply-token/v1"
_NONCE_BYTES = 16
_MAC_BYTES = 32
APPLY_TOKEN_TTL_SECONDS = 15 * 60
#: Bounded so a caller cannot grow process memory by requesting tokens.
MAX_CONSUMED_TOKENS = 4096


class ApplyTokenError(ValueError):
    """Raised for every invalid, expired, mismatched, or replayed token.

    Deliberately one exception type with distinct codes rather than distinct classes: the
    caller is told precisely what went wrong, but the checks themselves are uniform so a
    failure early in verification cannot be distinguished by timing from one later on.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ApplyBinding:
    """What a token authorizes. Every field participates in the MAC."""

    candidate_id: str
    base_revision: str
    board_revision: str
    relative_path: str

    def payload(self, expires_at: int) -> bytes:
        # Length-prefixed so no two different bindings can serialize to the same bytes by
        # shifting a delimiter into a field value.
        parts = (
            self.candidate_id.encode("utf-8"),
            self.base_revision.encode("utf-8"),
            self.board_revision.encode("utf-8"),
            self.relative_path.encode("utf-8"),
            str(expires_at).encode("ascii"),
        )
        joined = b"".join(len(part).to_bytes(4, "big") + part for part in parts)
        return _DOMAIN + joined


class ApplyTokenAuthority:
    """Issues and verifies apply tokens against a key held only in this process."""

    def __init__(
        self,
        *,
        ttl_seconds: int = APPLY_TOKEN_TTL_SECONDS,
        max_consumed: int = MAX_CONSUMED_TOKENS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise ApplyTokenError("invalid_token", "token lifetime is malformed")
        if not 1 <= ttl_seconds <= APPLY_TOKEN_TTL_SECONDS:
            raise ApplyTokenError(
                "invalid_token", "token lifetime must be positive and tighten-only"
            )
        self._key = secrets.token_bytes(32)
        self._ttl = ttl_seconds
        self._max_consumed = max_consumed
        self._clock: Callable[[], float] = clock if callable(clock) else time.time
        self._consumed: OrderedDict[str, None] = OrderedDict()

    def _now(self) -> int:
        return int(self._clock())

    def issue(self, binding: ApplyBinding) -> str:
        """Mint one token for exactly this binding."""

        if not isinstance(binding, ApplyBinding):
            raise ApplyTokenError("invalid_token", "apply binding is malformed")
        nonce = secrets.token_bytes(_NONCE_BYTES)
        expires_at = self._now() + self._ttl
        mac = hmac.new(self._key, nonce + binding.payload(expires_at), "sha256").digest()
        raw = nonce + expires_at.to_bytes(8, "big") + mac
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def verify(self, token: str, binding: ApplyBinding) -> str:
        """Verify a token against its binding and return its nonce, or raise.

        Verification does not consume. Consumption happens only once the apply has actually
        succeeded, so a refusal for an unrelated reason - a stale board, a present lockfile -
        leaves the token usable for a legitimate retry.
        """

        if not isinstance(token, str) or not 1 <= len(token) <= 512:
            raise ApplyTokenError("invalid_token", "apply token is malformed")
        padding = "=" * (-len(token) % 4)
        try:
            raw = base64.urlsafe_b64decode(token + padding)
        except (ValueError, TypeError) as error:
            raise ApplyTokenError("invalid_token", "apply token is malformed") from error
        if len(raw) != _NONCE_BYTES + 8 + _MAC_BYTES:
            raise ApplyTokenError("invalid_token", "apply token is malformed")
        nonce = raw[:_NONCE_BYTES]
        expires_at = int.from_bytes(raw[_NONCE_BYTES : _NONCE_BYTES + 8], "big")
        presented = raw[_NONCE_BYTES + 8 :]

        expected = hmac.new(self._key, nonce + binding.payload(expires_at), "sha256").digest()
        # Constant-time: a byte-by-byte comparison would leak how much of a forged MAC was
        # correct, which is enough to forge one a byte at a time.
        if not hmac.compare_digest(expected, presented):
            raise ApplyTokenError(
                "invalid_token", "apply token does not authorize this candidate and board"
            )
        # Expiry is checked after the MAC so an attacker cannot learn anything from an
        # unauthenticated field, and the expiry itself is inside the MAC so editing it fails.
        if self._now() >= expires_at:
            raise ApplyTokenError("token_expired", "apply token has expired")
        identifier = base64.urlsafe_b64encode(nonce).decode("ascii")
        if identifier in self._consumed:
            raise ApplyTokenError("token_already_used", "apply token has already been used")
        return identifier

    def consume(self, identifier: str) -> None:
        """Record a token as spent. Bounded, and oldest-first when the bound is reached."""

        self._consumed[identifier] = None
        self._consumed.move_to_end(identifier)
        while len(self._consumed) > self._max_consumed:
            self._consumed.popitem(last=False)


__all__ = [
    "APPLY_TOKEN_TTL_SECONDS",
    "MAX_CONSUMED_TOKENS",
    "ApplyBinding",
    "ApplyTokenAuthority",
    "ApplyTokenError",
]
