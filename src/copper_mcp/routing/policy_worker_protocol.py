"""Closed, single-frame protocol for the isolated reference routing-policy worker.

The protocol deliberately carries only the neutral, order-only subset of
``RoutingPolicyInput`` accepted by ADR-0064.  It has no representation for
board bytes, geometry, copper, candidates, apply tokens, network endpoints,
or provider credentials.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, TypeAlias, cast

from copper_mcp.routing.policy import (
    POLICY_DECISION_SCHEMA,
    REFERENCE_POLICY_ID,
    PolicyBounds,
    RoutingPolicyDecision,
    RoutingPolicyInput,
    decode_policy_input_json,
    policy_decision_digest,
    policy_input_digest,
)

POLICY_WORKER_REQUEST_SCHEMA: Final = "copper-mcp.policy-worker-request.v1"
POLICY_WORKER_RESPONSE_SCHEMA: Final = "copper-mcp.policy-worker-response.v1"
REFERENCE_POLICY_WORKER_BACKEND: Final = "deterministic-reference-v1"
POLICY_WORKER_REJECTED: Final = "POLICY_WORKER_REJECTED"
MAX_POLICY_WORKER_FRAME_BYTES: Final = 32_768

_NONCE_RE: Final = re.compile(r"^[a-f0-9]{64}$")
_DIGEST_RE: Final = re.compile(r"^sha256:[a-f0-9]{64}$")
_ZERO_NONCE: Final = "0" * 64
_ZERO_DIGEST: Final = "sha256:" + "0" * 64
_NEUTRAL_BOUNDS: Final = PolicyBounds(0, 0, 0, 0)
JsonObject: TypeAlias = dict[str, Any]


class PolicyWorkerProtocolError(ValueError):
    """A fixed, redacted rejection at the policy-worker boundary."""

    def __init__(self) -> None:
        super().__init__(POLICY_WORKER_REJECTED)


def _reject_duplicates(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise PolicyWorkerProtocolError()
        result[key] = value
    return result


def _walk_json(value: object, *, depth: int = 0, count: list[int] | None = None) -> None:
    if count is None:
        count = [0]
    if depth > 12:
        raise PolicyWorkerProtocolError()
    count[0] += 1
    if count[0] > 4_096:
        raise PolicyWorkerProtocolError()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 80 or any(ord(char) < 0x20 for char in key):
                raise PolicyWorkerProtocolError()
            _walk_json(child, depth=depth + 1, count=count)
    elif isinstance(value, list):
        for child in value:
            _walk_json(child, depth=depth + 1, count=count)
    elif value is None or isinstance(value, bool | int | str):
        return
    else:
        raise PolicyWorkerProtocolError()


def _decode_frame(frame: bytes) -> JsonObject:
    if not isinstance(frame, bytes) or not 0 < len(frame) <= MAX_POLICY_WORKER_FRAME_BYTES:
        raise PolicyWorkerProtocolError()
    try:
        text = frame.decode("utf-8", errors="strict")
        decoded = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(PolicyWorkerProtocolError()),
        )
    except (TypeError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, PolicyWorkerProtocolError):
            raise
        raise PolicyWorkerProtocolError() from error
    _walk_json(decoded)
    if not isinstance(decoded, dict):
        raise PolicyWorkerProtocolError()
    return cast(JsonObject, decoded)


def _canonical_bytes(value: object) -> bytes:
    try:
        frame = (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8", errors="strict")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError) as error:
        raise PolicyWorkerProtocolError() from error
    if not 0 < len(frame) <= MAX_POLICY_WORKER_FRAME_BYTES:
        raise PolicyWorkerProtocolError()
    return frame


def _digest(frame: bytes) -> str:
    return f"sha256:{hashlib.sha256(frame).hexdigest()}"


def _closed_object(value: object, expected: frozenset[str]) -> JsonObject:
    if not isinstance(value, dict) or set(value) != expected:
        raise PolicyWorkerProtocolError()
    return cast(JsonObject, value)


def _nonce(value: object) -> str:
    if not isinstance(value, str) or _NONCE_RE.fullmatch(value) is None:
        raise PolicyWorkerProtocolError()
    return value


def _digest_text(value: object) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise PolicyWorkerProtocolError()
    return value


def _reference_safe_input(policy_input: RoutingPolicyInput) -> None:
    """Require the order-only input subset with no geometry-bearing windows."""

    if (
        not isinstance(policy_input, RoutingPolicyInput)
        or policy_input.bounds != _NEUTRAL_BOUNDS
        or policy_input.corridor_candidates
        or policy_input.repair_candidates
    ):
        raise PolicyWorkerProtocolError()


@dataclass(frozen=True, slots=True)
class PolicyWorkerRequest:
    """One reference-backend request, bound to a fresh 256-bit nonce."""

    nonce: str
    policy_input: RoutingPolicyInput
    backend: str = REFERENCE_POLICY_WORKER_BACKEND
    schema: str = POLICY_WORKER_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if (
            self.schema != POLICY_WORKER_REQUEST_SCHEMA
            or self.backend != REFERENCE_POLICY_WORKER_BACKEND
        ):
            raise PolicyWorkerProtocolError()
        _nonce(self.nonce)
        _reference_safe_input(self.policy_input)

    def as_json(self) -> JsonObject:
        return {
            "backend": self.backend,
            "nonce": self.nonce,
            "policy_input": self.policy_input.as_json(),
            "schema": self.schema,
        }


def canonical_policy_worker_request_bytes(request: PolicyWorkerRequest) -> bytes:
    if not isinstance(request, PolicyWorkerRequest):
        raise PolicyWorkerProtocolError()
    return _canonical_bytes(request.as_json())


def policy_worker_request_digest(request: PolicyWorkerRequest) -> str:
    return _digest(canonical_policy_worker_request_bytes(request))


def decode_policy_worker_request(frame: bytes) -> PolicyWorkerRequest:
    root = _closed_object(
        _decode_frame(frame),
        frozenset({"backend", "nonce", "policy_input", "schema"}),
    )
    if (
        root["schema"] != POLICY_WORKER_REQUEST_SCHEMA
        or root["backend"] != REFERENCE_POLICY_WORKER_BACKEND
    ):
        raise PolicyWorkerProtocolError()
    try:
        policy_input = decode_policy_input_json(_canonical_bytes(root["policy_input"]))
        return PolicyWorkerRequest(nonce=_nonce(root["nonce"]), policy_input=policy_input)
    except (TypeError, ValueError, PolicyWorkerProtocolError) as error:
        if isinstance(error, PolicyWorkerProtocolError):
            raise
        raise PolicyWorkerProtocolError() from error


@dataclass(frozen=True, slots=True)
class PolicyWorkerResponse:
    """A nonce- and request-digest-bound worker response with no error detail."""

    nonce: str
    request_digest: str
    status: str
    decision: RoutingPolicyDecision | None = None
    decision_digest: str | None = None
    error: str | None = None
    schema: str = POLICY_WORKER_RESPONSE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != POLICY_WORKER_RESPONSE_SCHEMA:
            raise PolicyWorkerProtocolError()
        _nonce(self.nonce)
        _digest_text(self.request_digest)
        if self.status == "ok":
            if (
                not isinstance(self.decision, RoutingPolicyDecision)
                or self.error is not None
                or self.decision_digest != policy_decision_digest(self.decision)
            ):
                raise PolicyWorkerProtocolError()
            _reference_safe_decision(self.decision)
        elif self.status == "rejected":
            if (
                self.decision is not None
                or self.decision_digest is not None
                or self.error != POLICY_WORKER_REJECTED
            ):
                raise PolicyWorkerProtocolError()
        else:
            raise PolicyWorkerProtocolError()

    def as_json(self) -> JsonObject:
        common: JsonObject = {
            "nonce": self.nonce,
            "request_digest": self.request_digest,
            "schema": self.schema,
            "status": self.status,
        }
        if self.status == "ok":
            assert self.decision is not None
            assert self.decision_digest is not None
            return {
                **common,
                "decision": self.decision.as_json(),
                "decision_digest": self.decision_digest,
            }
        return {**common, "error": POLICY_WORKER_REJECTED}


def _reference_safe_decision(decision: RoutingPolicyDecision) -> None:
    if (
        decision.policy_id != REFERENCE_POLICY_ID
        or decision.corridor_hints
        or decision.repair_windows
    ):
        raise PolicyWorkerProtocolError()


def canonical_policy_worker_response_bytes(response: PolicyWorkerResponse) -> bytes:
    if not isinstance(response, PolicyWorkerResponse):
        raise PolicyWorkerProtocolError()
    return _canonical_bytes(response.as_json())


def rejected_policy_worker_response(
    *, nonce: str = _ZERO_NONCE, request_digest: str = _ZERO_DIGEST
) -> PolicyWorkerResponse:
    """Return the only wire-visible error, without exceptions or worker diagnostics."""

    return PolicyWorkerResponse(
        nonce=nonce,
        request_digest=request_digest,
        status="rejected",
        error=POLICY_WORKER_REJECTED,
    )


def _decode_reference_decision(value: object) -> RoutingPolicyDecision:
    item = _closed_object(
        value,
        frozenset(
            {
                "corridor_hints",
                "input_digest",
                "net_order",
                "policy_id",
                "repair_windows",
                "schema",
            }
        ),
    )
    if (
        item["schema"] != POLICY_DECISION_SCHEMA
        or item["policy_id"] != REFERENCE_POLICY_ID
        or not isinstance(item["net_order"], list)
        or not all(isinstance(net_id, str) for net_id in item["net_order"])
        or item["corridor_hints"] != []
        or item["repair_windows"] != []
    ):
        raise PolicyWorkerProtocolError()
    try:
        return RoutingPolicyDecision(
            policy_id=item["policy_id"],
            input_digest=item["input_digest"],
            net_order=tuple(item["net_order"]),
        )
    except (TypeError, ValueError) as error:
        raise PolicyWorkerProtocolError() from error


def decode_policy_worker_response(frame: bytes) -> PolicyWorkerResponse:
    """Decode only the one canonical response representation.

    Requests intentionally accept any bounded, closed JSON representation before
    being canonicalized for their digest.  Responses are different: the parent
    receives child bytes, so accepting whitespace or member-order variants would
    make a signed/bound receipt ambiguous.  Re-encode the fully validated object
    and require exact byte equality before it crosses the process boundary.
    """

    root = _decode_frame(frame)
    status = root.get("status")
    if status == "rejected":
        item = _closed_object(
            root,
            frozenset({"error", "nonce", "request_digest", "schema", "status"}),
        )
        response = PolicyWorkerResponse(
            nonce=_nonce(item["nonce"]),
            request_digest=_digest_text(item["request_digest"]),
            status="rejected",
            error=item["error"],
            schema=item["schema"],
        )
    elif status == "ok":
        item = _closed_object(
            root,
            frozenset(
                {"decision", "decision_digest", "nonce", "request_digest", "schema", "status"}
            ),
        )
        decision = _decode_reference_decision(item["decision"])
        response = PolicyWorkerResponse(
            nonce=_nonce(item["nonce"]),
            request_digest=_digest_text(item["request_digest"]),
            status="ok",
            decision=decision,
            decision_digest=_digest_text(item["decision_digest"]),
            schema=item["schema"],
        )
    else:
        raise PolicyWorkerProtocolError()
    if frame != canonical_policy_worker_response_bytes(response):
        raise PolicyWorkerProtocolError()
    return response


def validate_policy_worker_response(
    request: PolicyWorkerRequest,
    response: PolicyWorkerResponse,
) -> RoutingPolicyDecision:
    """Fail closed unless a response binds this exact request and reference input."""

    if not isinstance(request, PolicyWorkerRequest) or not isinstance(
        response, PolicyWorkerResponse
    ):
        raise PolicyWorkerProtocolError()
    if (
        response.status != "ok"
        or response.nonce != request.nonce
        or response.request_digest != policy_worker_request_digest(request)
        or response.decision is None
        or response.decision.input_digest != policy_input_digest(request.policy_input)
    ):
        raise PolicyWorkerProtocolError()
    _reference_safe_decision(response.decision)
    if set(response.decision.net_order) != {net.net_id for net in request.policy_input.nets}:
        raise PolicyWorkerProtocolError()
    return response.decision


__all__ = [
    "MAX_POLICY_WORKER_FRAME_BYTES",
    "POLICY_WORKER_REJECTED",
    "POLICY_WORKER_REQUEST_SCHEMA",
    "POLICY_WORKER_RESPONSE_SCHEMA",
    "REFERENCE_POLICY_WORKER_BACKEND",
    "PolicyWorkerProtocolError",
    "PolicyWorkerRequest",
    "PolicyWorkerResponse",
    "canonical_policy_worker_request_bytes",
    "canonical_policy_worker_response_bytes",
    "decode_policy_worker_request",
    "decode_policy_worker_response",
    "policy_worker_request_digest",
    "rejected_policy_worker_response",
    "validate_policy_worker_response",
]
