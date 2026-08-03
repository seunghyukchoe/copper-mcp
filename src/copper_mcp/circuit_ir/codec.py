"""Strict JSON decoding for untrusted Circuit Intent IR v0.1 input."""

from __future__ import annotations

import json
from typing import Any

from copper_mcp.circuit_ir.canonical import (
    canonical_content_bytes,
    make_snapshot,
    normalize_content,
    verify_snapshot,
)
from copper_mcp.circuit_ir.limits import CircuitParseLimits
from copper_mcp.circuit_ir.types import (
    CIRCUIT_INTENT_SCHEMA,
    CIRCUIT_INTENT_SCHEMA_VERSION,
    CircuitIntentContent,
    CircuitIntentSnapshot,
    Component,
    ComponentKind,
    Connection,
    Net,
    Port,
    PortDirection,
)
from copper_mcp.circuit_ir.validation import CircuitIntentValidationError, validate_content


def _reject_number(value: str) -> Any:
    del value
    raise ValueError("numeric JSON values are unsupported")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _preflight_json(text: str, limits: CircuitParseLimits) -> None:
    depth = 0
    values = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            values += 1
            if depth > limits.max_json_depth:
                raise CircuitIntentValidationError(
                    "budget.exceeded", "JSON nesting exceeds the decoder budget"
                )
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced JSON container")
        elif character == ",":
            values += 1
        if values > limits.max_json_values:
            raise CircuitIntentValidationError(
                "budget.exceeded", "JSON value count exceeds the decoder budget"
            )
    if in_string or depth != 0:
        raise ValueError("incomplete JSON input")


def _validate_tree(value: Any, limits: CircuitParseLimits) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > limits.max_json_values or depth > limits.max_json_depth:
            raise CircuitIntentValidationError(
                "budget.exceeded", "decoded JSON exceeds the structure budget"
            )
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif not isinstance(item, (str, bool, type(None))):
            raise ValueError("unsupported JSON scalar")


def _validate_text_budget(value: Any, limits: CircuitParseLimits) -> None:
    """Bound decoded text before canonical encoding duplicates it in memory."""

    pending = [value]
    encoded_bytes = 0
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str):
            remaining = limits.max_input_bytes - encoded_bytes
            if len(item) > remaining:
                raise CircuitIntentValidationError(
                    "budget.exceeded", "Circuit Intent input byte budget exceeded"
                )
            encoded_bytes += len(item.encode("utf-8", errors="strict"))
            if encoded_bytes > limits.max_input_bytes:
                raise CircuitIntentValidationError(
                    "budget.exceeded", "Circuit Intent input byte budget exceeded"
                )


def _object(value: Any, *, required: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"{path} has unknown or missing fields")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    return value


def _decode_component(value: Any) -> Component:
    document = _object(
        value,
        required={"id", "kind", "reference", "value"},
        path="component",
    )
    return Component(
        id=_string(document["id"], "component.id"),
        kind=ComponentKind(_string(document["kind"], "component.kind")),
        reference=_string(document["reference"], "component.reference"),
        value=_string(document["value"], "component.value"),
    )


def _decode_connection(value: Any) -> Connection:
    document = _object(
        value,
        required={"component_id", "pin"},
        path="connection",
    )
    return Connection(
        component_id=_string(document["component_id"], "connection.component_id"),
        pin=_string(document["pin"], "connection.pin"),
    )


def _decode_net(value: Any) -> Net:
    document = _object(value, required={"id", "name", "connections"}, path="net")
    return Net(
        id=_string(document["id"], "net.id"),
        name=_string(document["name"], "net.name"),
        connections=tuple(
            _decode_connection(connection)
            for connection in _array(document["connections"], "net.connections")
        ),
    )


def _decode_port(value: Any) -> Port:
    document = _object(value, required={"id", "net_id", "direction"}, path="port")
    return Port(
        id=_string(document["id"], "port.id"),
        net_id=_string(document["net_id"], "port.net_id"),
        direction=PortDirection(_string(document["direction"], "port.direction")),
    )


def _decode_content(value: Any) -> CircuitIntentContent:
    document = _object(
        value,
        required={"circuit_id", "project_name", "title", "components", "nets", "ports"},
        path="content",
    )
    return CircuitIntentContent(
        circuit_id=_string(document["circuit_id"], "content.circuit_id"),
        project_name=_string(document["project_name"], "content.project_name"),
        title=_string(document["title"], "content.title"),
        components=tuple(
            _decode_component(component)
            for component in _array(document["components"], "content.components")
        ),
        nets=tuple(_decode_net(net) for net in _array(document["nets"], "content.nets")),
        ports=tuple(_decode_port(port) for port in _array(document["ports"], "content.ports")),
    )


def decode_snapshot_json(
    payload: bytes,
    limits: CircuitParseLimits | None = None,
) -> CircuitIntentSnapshot:
    """Decode, validate, normalize, and verify one untrusted JSON envelope."""

    limits = limits or CircuitParseLimits()
    if not isinstance(payload, bytes) or len(payload) > limits.max_input_bytes:
        raise CircuitIntentValidationError(
            "budget.exceeded", "Circuit Intent input byte budget exceeded"
        )
    try:
        text = payload.decode("utf-8", errors="strict")
        _preflight_json(text, limits)
        decoded = json.loads(
            text,
            parse_int=_reject_number,
            parse_float=_reject_number,
            parse_constant=_reject_number,
            object_pairs_hook=_object_pairs,
        )
        _validate_tree(decoded, limits)
        envelope = _object(
            decoded,
            required={"schema", "schema_version", "snapshot_digest", "content"},
            path="snapshot",
        )
        if _string(envelope["schema"], "snapshot.schema") != CIRCUIT_INTENT_SCHEMA:
            raise ValueError("unsupported schema discriminator")
        if (
            _string(envelope["schema_version"], "snapshot.schema_version")
            != CIRCUIT_INTENT_SCHEMA_VERSION
        ):
            raise ValueError("unsupported schema version")
        content = _decode_content(envelope["content"])
        validate_content(content, limits)
        content = normalize_content(content)
        validate_content(content, limits)
        snapshot = CircuitIntentSnapshot(
            snapshot_digest=_string(envelope["snapshot_digest"], "snapshot.snapshot_digest"),
            content=content,
        )
        verify_snapshot(snapshot)
        return snapshot
    except CircuitIntentValidationError:
        raise
    except RecursionError as error:
        raise CircuitIntentValidationError(
            "budget.exceeded", "JSON nesting exceeds the decoder budget"
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise CircuitIntentValidationError(
            "schema.invalid", "JSON does not conform to Circuit Intent IR v0.1"
        ) from error


def snapshot_from_content(
    value: Any,
    limits: CircuitParseLimits | None = None,
) -> CircuitIntentSnapshot:
    """Validate structured Circuit Intent content and create its canonical snapshot.

    This boundary is intended for already-decoded protocol objects such as MCP tool
    arguments. It applies the same structural and semantic ceilings as the strict
    JSON decoder without asking a caller to calculate ``snapshot_digest``.
    """

    limits = limits or CircuitParseLimits()
    try:
        _validate_tree(value, limits)
        _validate_text_budget(value, limits)
        content = _decode_content(value)
        validate_content(content, limits)
        content = normalize_content(content)
        validate_content(content, limits)
        if len(canonical_content_bytes(content)) > limits.max_input_bytes:
            raise CircuitIntentValidationError(
                "budget.exceeded", "Circuit Intent input byte budget exceeded"
            )
        return make_snapshot(content)
    except CircuitIntentValidationError:
        raise
    except RecursionError as error:
        raise CircuitIntentValidationError(
            "budget.exceeded", "Circuit Intent structure exceeds the decoder budget"
        ) from error
    except (TypeError, UnicodeEncodeError, ValueError) as error:
        raise CircuitIntentValidationError(
            "schema.invalid", "content does not conform to Circuit Intent IR v0.1"
        ) from error
