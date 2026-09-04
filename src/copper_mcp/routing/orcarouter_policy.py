"""Optional direct-import-only OrcaRouter advisory routing policy.

OrcaRouter exposes an OpenAI-compatible gateway at ``https://api.orcarouter.ai/v1``.  This
adapter uses one non-streaming tool call to select from coordinator-owned policy options.  It
never sends Board IR, coordinates, raw net identities, candidate geometry, caller-supplied prompt
content, or apply authority to the provider, and it never constructs a route.  The deterministic
routing core remains the only place that validates or applies copper.

The module is intentionally not registered as an MCP tool or a negotiated-policy profile.  A
caller must import it explicitly and pass its result through :func:`evaluate_policy`.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .policy import (
    CorridorCandidate,
    PolicyNet,
    RepairWindowCandidate,
    RoutingPolicyDecision,
    RoutingPolicyInput,
    policy_input_digest,
)

ORCAROUTER_POLICY_ID: Final = "orcarouter-advisory-policy-v1"
ORCAROUTER_API_BASE_URL: Final = "https://api.orcarouter.ai/v1"
ORCAROUTER_DEFAULT_MODEL: Final = "orcarouter/auto"
ORCAROUTER_API_KEY_ENV: Final = "ORCA_KEY"
ORCAROUTER_MODEL_ENV: Final = "ORCAROUTER_MODEL"

_ORCAROUTER_HOST: Final = "api.orcarouter.ai"
_SELECT_TOOL_NAME: Final = "select_routing_policy"
_INPUT_SCHEMA: Final = "copper-mcp.orcarouter-policy-input.v1"
_MAX_API_KEY_LENGTH: Final = 512
_MAX_MODEL_LENGTH: Final = 128
_MAX_REQUEST_BYTES: Final = 64_000
_MAX_RESPONSE_BYTES: Final = 64_000
_MAX_ARGUMENT_BYTES: Final = 16_000
_MAX_JSON_DEPTH: Final = 12
_MAX_JSON_VALUES: Final = 4_096
_MAX_JSON_STRING_LENGTH: Final = _MAX_ARGUMENT_BYTES
_MAX_COMPLETION_TOKENS: Final = 512
_MIN_TIMEOUT_SECONDS: Final = 0.1
_MAX_TIMEOUT_SECONDS: Final = 60.0
_MAX_ACTIONS_PER_NET: Final = 2
_MODEL_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


class OrcaRouterPolicyError(ValueError):
    """Raised when the remote policy response cannot become a closed local decision."""

    def __init__(self) -> None:
        # Never include a provider response, HTTP status, URL, or credential in this message.
        super().__init__("orcarouter_policy_rejected")


def _reject() -> OrcaRouterPolicyError:
    return OrcaRouterPolicyError()


def _validate_api_key(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sk-orca-")
        or not 12 <= len(value) <= _MAX_API_KEY_LENGTH
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        raise ValueError("OrcaRouter API key is invalid")
    return value


def _validate_model(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_MODEL_LENGTH
        or _MODEL_RE.fullmatch(value) is None
    ):
        raise ValueError("OrcaRouter model ID is invalid")
    return value


def _validate_base_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("OrcaRouter base URL is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("OrcaRouter base URL is invalid") from error
    if (
        parsed.scheme != "https"
        or parsed.netloc != _ORCAROUTER_HOST
        or parsed.hostname != _ORCAROUTER_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path not in {"/v1", "/v1/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("OrcaRouter base URL is invalid")
    return ORCAROUTER_API_BASE_URL


def _validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("OrcaRouter timeout must be a finite number")
    timeout = float(value)
    if not math.isfinite(timeout) or not _MIN_TIMEOUT_SECONDS <= timeout <= _MAX_TIMEOUT_SECONDS:
        raise ValueError("OrcaRouter timeout must be between 0.1 and 60 seconds")
    return timeout


@dataclass(frozen=True, slots=True)
class OrcaRouterPolicy:
    """A bounded, redacting :class:`RoutingPolicy` backed by OrcaRouter tool calling.

    The API key is accepted explicitly or through :meth:`from_env`; importing this module never
    reads the environment.  ``ORCAROUTER_MODEL`` is optional and defaults to OrcaRouter's
    ``orcarouter/auto`` router.  Explicit provider/model IDs are preferable when repeatability
    and cost predictability matter.
    """

    api_key: str = field(repr=False, compare=False)
    model: str = ORCAROUTER_DEFAULT_MODEL
    base_url: str = ORCAROUTER_API_BASE_URL
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "api_key", _validate_api_key(self.api_key))
        object.__setattr__(self, "model", _validate_model(self.model))
        object.__setattr__(self, "base_url", _validate_base_url(self.base_url))
        object.__setattr__(self, "timeout_seconds", _validate_timeout(self.timeout_seconds))

    @property
    def policy_id(self) -> str:
        """Return the stable adapter identity recorded in the local policy decision."""

        return ORCAROUTER_POLICY_ID

    @classmethod
    def from_env(cls) -> OrcaRouterPolicy:
        """Build an adapter from the documented ``ORCA_KEY`` environment variable."""

        api_key = os.environ.get(ORCAROUTER_API_KEY_ENV)
        if api_key is None:
            raise ValueError("ORCA_KEY is not set")
        return cls(
            api_key=api_key,
            model=os.environ.get(ORCAROUTER_MODEL_ENV, ORCAROUTER_DEFAULT_MODEL),
        )

    def propose(self, policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
        """Ask OrcaRouter for a closed selection and bind it back to local immutable objects."""

        if not isinstance(policy_input, RoutingPolicyInput):
            raise _reject()
        try:
            aliases = _alias_context(policy_input)
            request_body = _request_body(policy_input, aliases, model=self.model)
            request_bytes = _canonical_json_bytes(request_body)
            if len(request_bytes) > _MAX_REQUEST_BYTES:
                raise _reject()
            response_bytes = _post_json(
                f"{self.base_url}/chat/completions",
                {
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                request_bytes,
                self.timeout_seconds,
            )
            return _decision_from_response(response_bytes, policy_input, aliases)
        except OrcaRouterPolicyError:
            raise
        except Exception as error:
            # The public refusal is deliberately fixed even when a library or provider changes
            # its exception text.  The chained cause is available to local debugging only.
            raise _reject() from error


@dataclass(frozen=True, slots=True)
class _AliasContext:
    net_aliases: tuple[str, ...]
    net_ids_by_alias: Mapping[str, str]
    net_alias_by_id: Mapping[str, str]
    corridor_aliases: tuple[str, ...]
    corridors_by_alias: Mapping[str, CorridorCandidate]
    repair_aliases: tuple[str, ...]
    repairs_by_alias: Mapping[str, RepairWindowCandidate]


def _alias_context(policy_input: RoutingPolicyInput) -> _AliasContext:
    net_aliases = tuple(f"n{ordinal}" for ordinal in range(len(policy_input.nets)))
    net_ids_by_alias = {
        alias: net.net_id for alias, net in zip(net_aliases, policy_input.nets, strict=True)
    }
    net_alias_by_id = {net_id: alias for alias, net_id in net_ids_by_alias.items()}
    corridor_aliases = tuple(
        f"c{ordinal}" for ordinal in range(len(policy_input.corridor_candidates))
    )
    corridors_by_alias = dict(zip(corridor_aliases, policy_input.corridor_candidates, strict=True))
    repair_aliases = tuple(f"r{ordinal}" for ordinal in range(len(policy_input.repair_candidates)))
    repairs_by_alias = dict(zip(repair_aliases, policy_input.repair_candidates, strict=True))
    return _AliasContext(
        net_aliases=net_aliases,
        net_ids_by_alias=net_ids_by_alias,
        net_alias_by_id=net_alias_by_id,
        corridor_aliases=corridor_aliases,
        corridors_by_alias=corridors_by_alias,
        repair_aliases=repair_aliases,
        repairs_by_alias=repairs_by_alias,
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")


def _net_features(
    nets: tuple[PolicyNet, ...],
    aliases: _AliasContext,
) -> list[dict[str, Any]]:
    return [
        {
            "id": alias,
            "criticality": net.criticality,
            "demand_cells": net.demand_cells,
            "congestion_score": net.congestion_score,
        }
        for alias, net in zip(aliases.net_aliases, nets, strict=True)
    ]


def _corridor_features(
    candidates: tuple[CorridorCandidate, ...],
    aliases: _AliasContext,
) -> list[dict[str, Any]]:
    return [
        {
            "id": alias,
            "net": aliases.net_alias_by_id[candidate.net_id],
            "congestion_score": candidate.congestion_score,
            "detour_cells": candidate.detour_cells,
        }
        for alias, candidate in zip(aliases.corridor_aliases, candidates, strict=True)
    ]


def _repair_features(
    candidates: tuple[RepairWindowCandidate, ...],
    aliases: _AliasContext,
) -> list[dict[str, Any]]:
    return [
        {
            "id": alias,
            "net": aliases.net_alias_by_id[candidate.net_id],
            "conflict_score": candidate.conflict_score,
        }
        for alias, candidate in zip(aliases.repair_aliases, candidates, strict=True)
    ]


def _array_schema(
    aliases: tuple[str, ...], *, max_items: int, min_items: int = 0
) -> dict[str, Any]:
    item_schema: dict[str, Any] = {"type": "string"}
    if aliases:
        item_schema["enum"] = list(aliases)
    return {
        "items": item_schema,
        "maxItems": max_items,
        "minItems": min_items,
        "type": "array",
    }


def _tool_parameters(aliases: _AliasContext) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": {
            "corridor_hints": _array_schema(
                aliases.corridor_aliases,
                max_items=min(
                    len(aliases.corridor_aliases),
                    len(aliases.net_aliases) * _MAX_ACTIONS_PER_NET,
                ),
            ),
            "net_order": _array_schema(
                aliases.net_aliases,
                max_items=len(aliases.net_aliases),
                min_items=len(aliases.net_aliases),
            ),
            "repair_windows": _array_schema(
                aliases.repair_aliases,
                max_items=min(
                    len(aliases.repair_aliases),
                    len(aliases.net_aliases) * _MAX_ACTIONS_PER_NET,
                ),
            ),
        },
        "required": ["net_order", "corridor_hints", "repair_windows"],
        "type": "object",
    }


def _request_body(
    policy_input: RoutingPolicyInput,
    aliases: _AliasContext,
    *,
    model: str,
) -> dict[str, Any]:
    redacted_features = {
        "schema": _INPUT_SCHEMA,
        "nets": _net_features(policy_input.nets, aliases),
        "corridor_candidates": _corridor_features(policy_input.corridor_candidates, aliases),
        "repair_candidates": _repair_features(policy_input.repair_candidates, aliases),
    }
    return {
        "max_tokens": _MAX_COMPLETION_TOKENS,
        "messages": [
            {
                "content": (
                    "You are an advisory routing-policy selector. Use exactly one tool call. "
                    "Select only supplied aliases; never invent geometry, coordinates, copper, "
                    "files, credentials, or apply actions. The local deterministic core will "
                    "validate your selection."
                ),
                "role": "system",
            },
            {
                "content": _canonical_json_bytes(redacted_features).decode("ascii"),
                "role": "user",
            },
        ],
        "model": model,
        "stream": False,
        "tool_choice": {"function": {"name": _SELECT_TOOL_NAME}, "type": "function"},
        "tools": [
            {
                "function": {
                    "description": (
                        "Choose a complete net order and subsets of coordinator-provided "
                        "corridor and repair aliases. This is not a route or an apply operation."
                    ),
                    "name": _SELECT_TOOL_NAME,
                    "parameters": _tool_parameters(aliases),
                },
                "type": "function",
            }
        ],
    }


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise _reject()


def _post_json(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> bytes:
    if url != f"{ORCAROUTER_API_BASE_URL}/chat/completions":
        raise _reject()
    if not isinstance(body, bytes) or len(body) > _MAX_REQUEST_BYTES:
        raise _reject()
    timeout = _validate_timeout(timeout)
    request = Request(  # noqa: S310 - URL is validated against the fixed HTTPS endpoint.
        url,
        data=body,
        headers=dict(headers),
        method="POST",
    )
    try:
        opener = build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            if getattr(response, "status", None) != 200:
                raise _reject()
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
    except OrcaRouterPolicyError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise _reject() from error
    if not isinstance(payload, bytes) or len(payload) > _MAX_RESPONSE_BYTES:
        raise _reject()
    return payload


def _reject_constant(value: str) -> None:
    del value
    raise ValueError("non-finite JSON number")


def _reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _walk_json(value: object, *, depth: int = 0, counter: list[int] | None = None) -> None:
    if counter is None:
        counter = [0]
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("JSON nesting exceeds the budget")
    counter[0] += 1
    if counter[0] > _MAX_JSON_VALUES:
        raise ValueError("JSON value count exceeds the budget")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 80 or any(ord(char) < 0x20 for char in key):
                raise ValueError("JSON field name is malformed")
            _walk_json(child, depth=depth + 1, counter=counter)
    elif isinstance(value, list):
        for child in value:
            _walk_json(child, depth=depth + 1, counter=counter)
    elif isinstance(value, str):
        if len(value) > _MAX_JSON_STRING_LENGTH:
            raise ValueError("JSON string exceeds the budget")
    elif value is None or isinstance(value, bool | int):
        return
    elif isinstance(value, float) and math.isfinite(value):
        return
    else:
        raise ValueError("JSON value is unsupported")


def _decode_json_object(payload: bytes | str, *, max_bytes: int) -> dict[str, Any]:
    if isinstance(payload, bytes):
        if len(payload) > max_bytes:
            raise _reject()
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise _reject() from error
    elif isinstance(payload, str):
        try:
            encoded = payload.encode("utf-8", errors="strict")
        except UnicodeError as error:
            raise _reject() from error
        if len(encoded) > max_bytes:
            raise _reject()
        text = payload
    else:
        raise _reject()
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_reject_duplicate,
            parse_constant=_reject_constant,
        )
        _walk_json(decoded)
    except (RecursionError, TypeError, ValueError) as error:
        raise _reject() from error
    if not isinstance(decoded, dict):
        raise _reject()
    return cast(dict[str, Any], decoded)


def _closed_object(value: object, expected: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise _reject()
    return cast(dict[str, Any], value)


def _aliases_from_value(
    value: object,
    allowed: tuple[str, ...],
    *,
    max_items: int,
    exact_items: int | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > max_items:
        raise _reject()
    allowed_set = set(allowed)
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in allowed_set or item in result:
            raise _reject()
        result.append(item)
    if exact_items is not None and len(result) != exact_items:
        raise _reject()
    return tuple(result)


def _candidate_sort_key(
    candidate: CorridorCandidate | RepairWindowCandidate,
) -> tuple[str, int, int, int, int]:
    bounds = candidate.bounds
    return (candidate.net_id, bounds.min_x, bounds.min_y, bounds.max_x, bounds.max_y)


def _decision_from_response(
    response: bytes,
    policy_input: RoutingPolicyInput,
    aliases: _AliasContext,
) -> RoutingPolicyDecision:
    root = _decode_json_object(response, max_bytes=_MAX_RESPONSE_BYTES)
    choices = root.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise _reject()
    message = choices[0].get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise _reject()
    tool_calls = message.get("tool_calls")
    if (
        not isinstance(tool_calls, list)
        or len(tool_calls) != 1
        or not isinstance(tool_calls[0], dict)
    ):
        raise _reject()
    tool_call = tool_calls[0]
    if tool_call.get("type") != "function":
        raise _reject()
    function = _closed_object(tool_call.get("function"), frozenset({"name", "arguments"}))
    if function["name"] != _SELECT_TOOL_NAME or not isinstance(function["arguments"], str):
        raise _reject()
    arguments = function["arguments"]
    if len(arguments.encode("utf-8", errors="strict")) > _MAX_ARGUMENT_BYTES:
        raise _reject()
    selected = _closed_object(
        _decode_json_object(arguments, max_bytes=_MAX_ARGUMENT_BYTES),
        frozenset({"net_order", "corridor_hints", "repair_windows"}),
    )
    net_order_aliases = _aliases_from_value(
        selected["net_order"],
        aliases.net_aliases,
        max_items=len(aliases.net_aliases),
        exact_items=len(aliases.net_aliases),
    )
    if set(net_order_aliases) != set(aliases.net_aliases):
        raise _reject()
    corridor_aliases = _aliases_from_value(
        selected["corridor_hints"],
        aliases.corridor_aliases,
        max_items=min(
            len(aliases.corridor_aliases),
            len(aliases.net_aliases) * _MAX_ACTIONS_PER_NET,
        ),
    )
    repair_aliases = _aliases_from_value(
        selected["repair_windows"],
        aliases.repair_aliases,
        max_items=min(
            len(aliases.repair_aliases),
            len(aliases.net_aliases) * _MAX_ACTIONS_PER_NET,
        ),
    )
    corridors = tuple(
        sorted(
            (aliases.corridors_by_alias[alias] for alias in corridor_aliases),
            key=_candidate_sort_key,
        )
    )
    repairs = tuple(
        sorted(
            (aliases.repairs_by_alias[alias] for alias in repair_aliases),
            key=_candidate_sort_key,
        )
    )
    return RoutingPolicyDecision(
        policy_id=ORCAROUTER_POLICY_ID,
        input_digest=policy_input_digest(policy_input),
        net_order=tuple(aliases.net_ids_by_alias[alias] for alias in net_order_aliases),
        corridor_hints=corridors,
        repair_windows=repairs,
    )


__all__ = [
    "ORCAROUTER_API_BASE_URL",
    "ORCAROUTER_API_KEY_ENV",
    "ORCAROUTER_DEFAULT_MODEL",
    "ORCAROUTER_MODEL_ENV",
    "ORCAROUTER_POLICY_ID",
    "OrcaRouterPolicy",
    "OrcaRouterPolicyError",
]
