"""Closed, deterministic policy contracts for *advising* the routing core.

This module is deliberately incapable of constructing a route.  A policy can select the order in
which nets are offered to a future coordinator and select from coordinator-provided rectangular
corridor or repair windows.  It cannot provide vertices, widths, layers, costs, Board IR, a route
candidate, or an apply operation.  Integration must treat every result as an untrusted search hint
and still invoke the deterministic router and validator for every emitted candidate.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol, TypeAlias, TypeVar, cast

POLICY_INPUT_SCHEMA: Final = "copper-mcp.routing-policy-input.v1"
POLICY_DECISION_SCHEMA: Final = "copper-mcp.routing-policy-decision.v1"
POLICY_TRACE_SCHEMA: Final = "copper-mcp.routing-policy-trace.v1"
REFERENCE_POLICY_ID: Final = "deterministic-routing-policy-v1"

_MAX_SAFE_INTEGER: Final = (1 << 53) - 1
_MAX_JSON_BYTES: Final = 64_000
_MAX_JSON_DEPTH: Final = 12
_MAX_JSON_VALUES: Final = 4_096
_MAX_NETS: Final = 64
_MAX_WINDOWS: Final = 256
_MAX_WINDOWS_PER_NET: Final = 8
_MAX_ACTIONS_PER_NET: Final = 2
_MAX_COORDINATE_CELL: Final = 1_000_000
_DIGEST_RE: Final = re.compile(r"^sha256:[a-f0-9]{64}$")
_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,160}$")
_POLICY_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")

JsonObject: TypeAlias = dict[str, Any]


def _integer(
    name: str,
    value: object,
    *,
    minimum: int = 0,
    maximum: int = _MAX_SAFE_INTEGER,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported integer range")
    return value


def _digest(name: str, value: object) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be content-addressed with sha256")
    return value


def _identifier(name: str, value: object, *, prefix: str = "") -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(prefix)
        or _ID_RE.fullmatch(value) is None
    ):
        raise ValueError(f"{name} must be a stable identifier")
    return value


def _policy_id(value: object) -> str:
    if not isinstance(value, str) or _POLICY_RE.fullmatch(value) is None:
        raise ValueError("policy ID must be a stable lowercase identifier")
    return value


def _canonical_bytes(value: object) -> bytes:
    serialized = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return serialized.encode("utf-8", errors="strict") + b"\n"


def _sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


@dataclass(frozen=True, slots=True)
class PolicyBounds:
    """Inclusive cell bounds of a coordinator-provided, non-copper search window."""

    min_x: int
    min_y: int
    max_x: int
    max_y: int

    def __post_init__(self) -> None:
        for name, value in (
            ("minimum x", self.min_x),
            ("minimum y", self.min_y),
            ("maximum x", self.max_x),
            ("maximum y", self.max_y),
        ):
            _integer(name, value, minimum=-_MAX_COORDINATE_CELL, maximum=_MAX_COORDINATE_CELL)
        if self.min_x > self.max_x or self.min_y > self.max_y:
            raise ValueError("policy bounds must be ordered")

    def contains(self, other: PolicyBounds) -> bool:
        """Return whether another supplied search window remains inside this one."""

        return (
            self.min_x <= other.min_x <= other.max_x <= self.max_x
            and self.min_y <= other.min_y <= other.max_y <= self.max_y
        )

    def as_json(self) -> JsonObject:
        return {"max_x": self.max_x, "max_y": self.max_y, "min_x": self.min_x, "min_y": self.min_y}


@dataclass(frozen=True, slots=True)
class PolicyNet:
    """Redaction-sensitive scalar features for one net; no pad, geometry, or copper data."""

    net_id: str
    criticality: int
    demand_cells: int
    congestion_score: int

    def __post_init__(self) -> None:
        _identifier("net ID", self.net_id, prefix="net:")
        _integer("criticality", self.criticality, maximum=1_000_000)
        _integer("demand cells", self.demand_cells, minimum=1, maximum=1_000_000)
        _integer("congestion score", self.congestion_score, maximum=1_000_000)

    def as_json(self) -> JsonObject:
        return {
            "congestion_score": self.congestion_score,
            "criticality": self.criticality,
            "demand_cells": self.demand_cells,
            "net_id": self.net_id,
        }


@dataclass(frozen=True, slots=True)
class CorridorCandidate:
    """One coordinator-owned bounded corridor that a policy may select but never draw into."""

    net_id: str
    bounds: PolicyBounds
    congestion_score: int
    detour_cells: int

    def __post_init__(self) -> None:
        _identifier("corridor net ID", self.net_id, prefix="net:")
        if not isinstance(self.bounds, PolicyBounds):
            raise ValueError("corridor bounds must be PolicyBounds")
        _integer("corridor congestion score", self.congestion_score, maximum=1_000_000)
        _integer("corridor detour cells", self.detour_cells, maximum=1_000_000)

    def as_json(self) -> JsonObject:
        return {
            "bounds": self.bounds.as_json(),
            "congestion_score": self.congestion_score,
            "detour_cells": self.detour_cells,
            "net_id": self.net_id,
        }


@dataclass(frozen=True, slots=True)
class RepairWindowCandidate:
    """One coordinator-owned bounded repair region, not a permission to rip up or mutate copper."""

    net_id: str
    bounds: PolicyBounds
    conflict_score: int

    def __post_init__(self) -> None:
        _identifier("repair net ID", self.net_id, prefix="net:")
        if not isinstance(self.bounds, PolicyBounds):
            raise ValueError("repair bounds must be PolicyBounds")
        _integer("repair conflict score", self.conflict_score, maximum=1_000_000)

    def as_json(self) -> JsonObject:
        return {
            "bounds": self.bounds.as_json(),
            "conflict_score": self.conflict_score,
            "net_id": self.net_id,
        }


_Window = TypeVar("_Window", CorridorCandidate, RepairWindowCandidate)


@dataclass(frozen=True, slots=True)
class RoutingPolicyInput:
    """Closed, immutable feature view for an advisory routing policy."""

    board_revision: str
    bounds: PolicyBounds
    nets: tuple[PolicyNet, ...]
    corridor_candidates: tuple[CorridorCandidate, ...] = ()
    repair_candidates: tuple[RepairWindowCandidate, ...] = ()
    schema: str = POLICY_INPUT_SCHEMA

    def __post_init__(self) -> None:
        _digest("board revision", self.board_revision)
        if self.schema != POLICY_INPUT_SCHEMA:
            raise ValueError("policy input schema is unsupported")
        if not isinstance(self.bounds, PolicyBounds):
            raise ValueError("policy bounds must be PolicyBounds")
        if (
            not isinstance(self.nets, tuple)
            or not 1 <= len(self.nets) <= _MAX_NETS
            or not all(isinstance(net, PolicyNet) for net in self.nets)
        ):
            raise ValueError("policy input must contain a bounded immutable net tuple")
        net_ids = tuple(net.net_id for net in self.nets)
        if net_ids != tuple(sorted(net_ids)) or len(set(net_ids)) != len(net_ids):
            raise ValueError("policy nets must be unique and canonical")
        for name, values, expected_type in (
            ("corridor candidates", self.corridor_candidates, CorridorCandidate),
            ("repair candidates", self.repair_candidates, RepairWindowCandidate),
        ):
            if (
                not isinstance(values, tuple)
                or len(values) > _MAX_WINDOWS
                or not all(isinstance(value, expected_type) for value in values)
            ):
                raise ValueError(f"policy {name} must be bounded immutable tuples")
            seen_per_net: dict[str, int] = {}
            encoded = tuple(_window_sort_key(value) for value in values)
            if encoded != tuple(sorted(encoded)) or len(set(encoded)) != len(encoded):
                raise ValueError(f"policy {name} must be unique and canonical")
            for value in values:
                if value.net_id not in net_ids or not self.bounds.contains(value.bounds):
                    raise ValueError(f"policy {name} must refer to a net and bounds in the input")
                seen_per_net[value.net_id] = seen_per_net.get(value.net_id, 0) + 1
            if any(count > _MAX_WINDOWS_PER_NET for count in seen_per_net.values()):
                raise ValueError(f"policy {name} exceed the per-net budget")

    def as_json(self) -> JsonObject:
        return {
            "board_revision": self.board_revision,
            "bounds": self.bounds.as_json(),
            "corridor_candidates": [candidate.as_json() for candidate in self.corridor_candidates],
            "nets": [net.as_json() for net in self.nets],
            "repair_candidates": [candidate.as_json() for candidate in self.repair_candidates],
            "schema": self.schema,
        }


def _window_sort_key(window: CorridorCandidate | RepairWindowCandidate) -> tuple[object, ...]:
    return (
        window.net_id,
        window.bounds.min_x,
        window.bounds.min_y,
        window.bounds.max_x,
        window.bounds.max_y,
    )


def canonical_policy_input_bytes(policy_input: RoutingPolicyInput) -> bytes:
    """Return the closed canonical bytes that bind a policy feature view."""

    if not isinstance(policy_input, RoutingPolicyInput):
        raise ValueError("policy input must be a RoutingPolicyInput")
    return _canonical_bytes(policy_input.as_json())


def policy_input_digest(policy_input: RoutingPolicyInput) -> str:
    """Return the content address of a closed policy feature view."""

    return _sha256(canonical_policy_input_bytes(policy_input))


@dataclass(frozen=True, slots=True)
class RoutingPolicyDecision:
    """A closed policy action set containing only an ordering and supplied windows."""

    policy_id: str
    input_digest: str
    net_order: tuple[str, ...]
    corridor_hints: tuple[CorridorCandidate, ...] = ()
    repair_windows: tuple[RepairWindowCandidate, ...] = ()
    schema: str = POLICY_DECISION_SCHEMA

    def __post_init__(self) -> None:
        _policy_id(self.policy_id)
        _digest("policy input digest", self.input_digest)
        if self.schema != POLICY_DECISION_SCHEMA:
            raise ValueError("policy decision schema is unsupported")
        if (
            not isinstance(self.net_order, tuple)
            or not self.net_order
            or not all(isinstance(net_id, str) for net_id in self.net_order)
        ):
            raise ValueError("policy net order must be a non-empty immutable tuple")
        for net_id in self.net_order:
            _identifier("ordered net ID", net_id, prefix="net:")
        if len(set(self.net_order)) != len(self.net_order):
            raise ValueError("policy net order must not repeat a net")
        for name, values, expected_type in (
            ("corridor hints", self.corridor_hints, CorridorCandidate),
            ("repair windows", self.repair_windows, RepairWindowCandidate),
        ):
            if (
                not isinstance(values, tuple)
                or len(values) > _MAX_NETS * _MAX_ACTIONS_PER_NET
                or not all(isinstance(value, expected_type) for value in values)
            ):
                raise ValueError(f"policy {name} must be a bounded immutable tuple")
            keys = tuple(_window_sort_key(value) for value in values)
            if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
                raise ValueError(f"policy {name} must be unique and canonical")
            counts: dict[str, int] = {}
            for value in values:
                if value.net_id not in self.net_order:
                    raise ValueError(f"policy {name} refer to unordered nets")
                counts[value.net_id] = counts.get(value.net_id, 0) + 1
            if any(count > _MAX_ACTIONS_PER_NET for count in counts.values()):
                raise ValueError(f"policy {name} exceed the per-net action budget")

    def as_json(self) -> JsonObject:
        return {
            "corridor_hints": [hint.as_json() for hint in self.corridor_hints],
            "input_digest": self.input_digest,
            "net_order": list(self.net_order),
            "policy_id": self.policy_id,
            "repair_windows": [window.as_json() for window in self.repair_windows],
            "schema": self.schema,
        }


def canonical_policy_decision_bytes(decision: RoutingPolicyDecision) -> bytes:
    """Return closed canonical bytes for a policy action set; no copper is representable."""

    if not isinstance(decision, RoutingPolicyDecision):
        raise ValueError("policy decision must be a RoutingPolicyDecision")
    return _canonical_bytes(decision.as_json())


def policy_decision_digest(decision: RoutingPolicyDecision) -> str:
    """Return the content address of a policy decision."""

    return _sha256(canonical_policy_decision_bytes(decision))


class RoutingPolicy(Protocol):
    """A pure advisory policy; the router remains responsible for all geometry and validation."""

    @property
    def policy_id(self) -> str:
        """Return a stable identity for recorded policy behavior."""

    def propose(self, policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
        """Return a closed selection from the feature view's bounded candidate windows."""


def _assert_selected_candidates(
    policy_input: RoutingPolicyInput,
    decision: RoutingPolicyDecision,
) -> None:
    if decision.input_digest != policy_input_digest(policy_input):
        raise ValueError("policy decision is not bound to this policy input")
    if set(decision.net_order) != {net.net_id for net in policy_input.nets}:
        raise ValueError("policy decision must order every input net exactly once")
    available_corridors = set(policy_input.corridor_candidates)
    available_repairs = set(policy_input.repair_candidates)
    if not set(decision.corridor_hints).issubset(available_corridors):
        raise ValueError("policy corridor hint was not supplied by the coordinator")
    if not set(decision.repair_windows).issubset(available_repairs):
        raise ValueError("policy repair window was not supplied by the coordinator")


def evaluate_policy(
    policy: RoutingPolicy,
    policy_input: RoutingPolicyInput,
) -> RoutingPolicyDecision:
    """Run one policy and fail closed unless it returns a valid input-bound decision.

    This is the intended model boundary: a future local model may implement ``RoutingPolicy``,
    but it can only select coordinator-provided options.  Exceptions, malformed values, or a
    changed policy identity are rejected before any routing operation is attempted.
    """

    if not isinstance(policy_input, RoutingPolicyInput):
        raise ValueError("policy input must be a RoutingPolicyInput")
    try:
        declared_id = _policy_id(policy.policy_id)
        decision = policy.propose(policy_input)
    except Exception as error:
        raise ValueError("routing policy failed to provide a closed decision") from error
    if not isinstance(decision, RoutingPolicyDecision) or decision.policy_id != declared_id:
        raise ValueError("routing policy returned an invalid decision")
    _assert_selected_candidates(policy_input, decision)
    return decision


@dataclass(frozen=True, slots=True)
class DeterministicReferencePolicy:
    """Stable reference heuristic for trace and future-model regression baselines."""

    policy_id: str = REFERENCE_POLICY_ID

    def __post_init__(self) -> None:
        _policy_id(self.policy_id)

    def propose(self, policy_input: RoutingPolicyInput) -> RoutingPolicyDecision:
        if not isinstance(policy_input, RoutingPolicyInput):
            raise ValueError("policy input must be a RoutingPolicyInput")
        net_order = tuple(
            net.net_id
            for net in sorted(
                policy_input.nets,
                key=lambda net: (
                    -net.criticality,
                    -net.congestion_score,
                    -net.demand_cells,
                    net.net_id,
                ),
            )
        )
        corridors = tuple(
            sorted(
                policy_input.corridor_candidates,
                key=lambda candidate: (
                    candidate.net_id,
                    candidate.congestion_score,
                    candidate.detour_cells,
                    _window_sort_key(candidate),
                ),
            )
        )
        repairs = tuple(
            sorted(
                policy_input.repair_candidates,
                key=lambda candidate: (
                    candidate.net_id,
                    -candidate.conflict_score,
                    _window_sort_key(candidate),
                ),
            )
        )
        return RoutingPolicyDecision(
            policy_id=self.policy_id,
            input_digest=policy_input_digest(policy_input),
            net_order=net_order,
            corridor_hints=_take_per_net(corridors),
            repair_windows=_take_per_net(repairs),
        )


def _take_per_net(
    candidates: tuple[_Window, ...],
) -> tuple[_Window, ...]:
    selected: list[_Window] = []
    counts: dict[str, int] = {}
    for candidate in candidates:
        count = counts.get(candidate.net_id, 0)
        if count < _MAX_ACTIONS_PER_NET:
            selected.append(candidate)
            counts[candidate.net_id] = count + 1
    return tuple(selected)


def _token(input_digest: str, value: str) -> str:
    """Return a stable opaque trace token without retaining the source identifier."""

    return hashlib.sha256(f"{input_digest}\x00{value}".encode()).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class RoutingPolicyTrace:
    """Redacted deterministic action record for offline local policy-learning experiments."""

    input_digest: str
    decision_digest: str
    policy_id: str
    net_count: int
    corridor_hint_count: int
    repair_window_count: int
    ordered_net_tokens: tuple[str, ...]
    corridor_hint_tokens: tuple[str, ...]
    repair_window_tokens: tuple[str, ...]
    schema: str = POLICY_TRACE_SCHEMA

    def __post_init__(self) -> None:
        _digest("trace input digest", self.input_digest)
        _digest("trace decision digest", self.decision_digest)
        _policy_id(self.policy_id)
        if self.schema != POLICY_TRACE_SCHEMA:
            raise ValueError("policy trace schema is unsupported")
        _integer("trace net count", self.net_count, minimum=1, maximum=_MAX_NETS)
        _integer(
            "trace corridor count",
            self.corridor_hint_count,
            maximum=_MAX_NETS * _MAX_ACTIONS_PER_NET,
        )
        _integer(
            "trace repair count",
            self.repair_window_count,
            maximum=_MAX_NETS * _MAX_ACTIONS_PER_NET,
        )
        expected = (
            ("ordered net", self.ordered_net_tokens, self.net_count),
            ("corridor hint", self.corridor_hint_tokens, self.corridor_hint_count),
            ("repair window", self.repair_window_tokens, self.repair_window_count),
        )
        for name, tokens, count in expected:
            if (
                not isinstance(tokens, tuple)
                or len(tokens) != count
                or not all(
                    isinstance(token, str) and re.fullmatch(r"[a-f0-9]{24}", token)
                    for token in tokens
                )
                or len(set(tokens)) != len(tokens)
            ):
                raise ValueError(f"trace {name} tokens are malformed")

    def as_json(self) -> JsonObject:
        return {
            "corridor_hint_count": self.corridor_hint_count,
            "corridor_hint_tokens": list(self.corridor_hint_tokens),
            "decision_digest": self.decision_digest,
            "input_digest": self.input_digest,
            "net_count": self.net_count,
            "ordered_net_tokens": list(self.ordered_net_tokens),
            "policy_id": self.policy_id,
            "repair_window_count": self.repair_window_count,
            "repair_window_tokens": list(self.repair_window_tokens),
            "schema": self.schema,
        }


def redacted_policy_trace(
    policy_input: RoutingPolicyInput,
    decision: RoutingPolicyDecision,
) -> RoutingPolicyTrace:
    """Return an immutable trace without board coordinates, net IDs, copper, or raw features."""

    _assert_selected_candidates(policy_input, decision)
    input_digest = policy_input_digest(policy_input)
    return RoutingPolicyTrace(
        input_digest=input_digest,
        decision_digest=policy_decision_digest(decision),
        policy_id=decision.policy_id,
        net_count=len(decision.net_order),
        corridor_hint_count=len(decision.corridor_hints),
        repair_window_count=len(decision.repair_windows),
        ordered_net_tokens=tuple(_token(input_digest, net_id) for net_id in decision.net_order),
        corridor_hint_tokens=tuple(
            _token(input_digest, _canonical_bytes(candidate.as_json()).decode("ascii"))
            for candidate in decision.corridor_hints
        ),
        repair_window_tokens=tuple(
            _token(input_digest, _canonical_bytes(candidate.as_json()).decode("ascii"))
            for candidate in decision.repair_windows
        ),
    )


def canonical_policy_trace_bytes(trace: RoutingPolicyTrace) -> bytes:
    """Return stable bytes for a trace record that contains only redacted metadata."""

    if not isinstance(trace, RoutingPolicyTrace):
        raise ValueError("policy trace must be a RoutingPolicyTrace")
    return _canonical_bytes(trace.as_json())


def _reject_duplicates(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("policy input contains duplicate fields")
        result[key] = value
    return result


def _walk_json(value: object, *, depth: int = 0, counter: list[int] | None = None) -> None:
    if counter is None:
        counter = [0]
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("policy input nesting exceeds the supported limit")
    counter[0] += 1
    if counter[0] > _MAX_JSON_VALUES:
        raise ValueError("policy input contains too many values")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 80 or any(ord(char) < 0x20 for char in key):
                raise ValueError("policy input field names are malformed")
            _walk_json(child, depth=depth + 1, counter=counter)
    elif isinstance(value, list):
        for child in value:
            _walk_json(child, depth=depth + 1, counter=counter)
    elif value is None or isinstance(value, bool | int | str):
        return
    else:
        raise ValueError("policy input contains unsupported JSON values")


def _closed_object(value: object, expected: frozenset[str], *, name: str) -> JsonObject:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"policy {name} has an unsupported shape")
    return cast(JsonObject, value)


def _decode_bounds(value: object) -> PolicyBounds:
    item = _closed_object(value, frozenset({"min_x", "min_y", "max_x", "max_y"}), name="bounds")
    return PolicyBounds(item["min_x"], item["min_y"], item["max_x"], item["max_y"])


def _decode_net(value: object) -> PolicyNet:
    item = _closed_object(
        value,
        frozenset({"net_id", "criticality", "demand_cells", "congestion_score"}),
        name="net",
    )
    return PolicyNet(
        item["net_id"],
        item["criticality"],
        item["demand_cells"],
        item["congestion_score"],
    )


def _decode_corridor(value: object) -> CorridorCandidate:
    item = _closed_object(
        value,
        frozenset({"net_id", "bounds", "congestion_score", "detour_cells"}),
        name="corridor candidate",
    )
    return CorridorCandidate(
        item["net_id"],
        _decode_bounds(item["bounds"]),
        item["congestion_score"],
        item["detour_cells"],
    )


def _decode_repair(value: object) -> RepairWindowCandidate:
    item = _closed_object(
        value,
        frozenset({"net_id", "bounds", "conflict_score"}),
        name="repair candidate",
    )
    return RepairWindowCandidate(
        item["net_id"],
        _decode_bounds(item["bounds"]),
        item["conflict_score"],
    )


def decode_policy_input_json(payload: str | bytes) -> RoutingPolicyInput:
    """Decode one hostile JSON payload through a bounded closed shape into immutable contracts."""

    if isinstance(payload, bytes):
        if len(payload) > _MAX_JSON_BYTES:
            raise ValueError("policy input exceeds the byte budget")
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("policy input is not valid UTF-8") from error
    elif isinstance(payload, str):
        if len(payload.encode("utf-8", errors="strict")) > _MAX_JSON_BYTES:
            raise ValueError("policy input exceeds the byte budget")
        text = payload
    else:
        raise ValueError("policy input must be JSON text")
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite JSON is unsupported")
            ),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("policy input JSON is malformed") from error
    _walk_json(decoded)
    root = _closed_object(
        decoded,
        frozenset(
            {
                "schema",
                "board_revision",
                "bounds",
                "nets",
                "corridor_candidates",
                "repair_candidates",
            }
        ),
        name="input",
    )
    for name in ("nets", "corridor_candidates", "repair_candidates"):
        if not isinstance(root[name], list):
            raise ValueError("policy input arrays are malformed")
    return RoutingPolicyInput(
        board_revision=root["board_revision"],
        bounds=_decode_bounds(root["bounds"]),
        nets=tuple(_decode_net(value) for value in root["nets"]),
        corridor_candidates=tuple(_decode_corridor(value) for value in root["corridor_candidates"]),
        repair_candidates=tuple(_decode_repair(value) for value in root["repair_candidates"]),
        schema=root["schema"],
    )


PolicyFactory: TypeAlias = Callable[[], RoutingPolicy]

__all__ = [
    "POLICY_DECISION_SCHEMA",
    "POLICY_INPUT_SCHEMA",
    "POLICY_TRACE_SCHEMA",
    "REFERENCE_POLICY_ID",
    "CorridorCandidate",
    "DeterministicReferencePolicy",
    "PolicyBounds",
    "PolicyFactory",
    "PolicyNet",
    "RepairWindowCandidate",
    "RoutingPolicy",
    "RoutingPolicyDecision",
    "RoutingPolicyInput",
    "RoutingPolicyTrace",
    "canonical_policy_decision_bytes",
    "canonical_policy_input_bytes",
    "canonical_policy_trace_bytes",
    "decode_policy_input_json",
    "evaluate_policy",
    "policy_decision_digest",
    "policy_input_digest",
    "redacted_policy_trace",
]
