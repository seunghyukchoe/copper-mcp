"""Canonical bytes and content digests for Circuit Intent IR v0.1."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from copper_mcp.circuit_ir.limits import CircuitParseLimits
from copper_mcp.circuit_ir.types import (
    CIRCUIT_INTENT_SCHEMA,
    CIRCUIT_INTENT_SCHEMA_VERSION,
    CircuitIntentContent,
    CircuitIntentSnapshot,
    Component,
    Connection,
    Net,
    Port,
)
from copper_mcp.circuit_ir.validation import CircuitIntentValidationError, validate_content


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _component(component: Component) -> dict[str, object]:
    return {
        "id": component.id,
        "kind": component.kind.value,
        "reference": component.reference,
        "value": component.value,
    }


def _connection(connection: Connection) -> dict[str, object]:
    return {"component_id": connection.component_id, "pin": connection.pin}


def _net(net: Net) -> dict[str, object]:
    return {
        "id": net.id,
        "name": net.name,
        "connections": [_connection(connection) for connection in net.connections],
    }


def _port(port: Port) -> dict[str, object]:
    return {"id": port.id, "net_id": port.net_id, "direction": port.direction.value}


def _content_payload(content: CircuitIntentContent) -> dict[str, object]:
    return {
        "circuit_id": content.circuit_id,
        "project_name": content.project_name,
        "title": content.title,
        "components": [_component(component) for component in content.components],
        "nets": [_net(net) for net in content.nets],
        "ports": [_port(port) for port in content.ports],
    }


def normalize_content(content: CircuitIntentContent) -> CircuitIntentContent:
    """Return the identity-sorted representation used by the digest contract."""

    components = tuple(sorted(content.components, key=lambda item: item.id))
    nets = tuple(
        Net(
            id=net.id,
            name=net.name,
            connections=tuple(sorted(net.connections)),
        )
        for net in sorted(content.nets, key=lambda item: item.id)
    )
    ports = tuple(sorted(content.ports, key=lambda item: item.id))
    return CircuitIntentContent(
        circuit_id=content.circuit_id,
        project_name=content.project_name,
        title=content.title,
        components=components,
        nets=nets,
        ports=ports,
    )


def make_content(
    *,
    circuit_id: str,
    project_name: str,
    title: str,
    components: Iterable[Component],
    nets: Iterable[Net],
    ports: Iterable[Port] = (),
    limits: CircuitParseLimits | None = None,
) -> CircuitIntentContent:
    """Construct, normalize, and validate one logical circuit body."""

    content = normalize_content(
        CircuitIntentContent(
            circuit_id=circuit_id,
            project_name=project_name,
            title=title,
            components=tuple(components),
            nets=tuple(nets),
            ports=tuple(ports),
        )
    )
    validate_content(content, limits)
    return content


def canonical_content_bytes(content: CircuitIntentContent) -> bytes:
    """Encode normalized content without the self-referential envelope."""

    normalized = normalize_content(content)
    validate_content(normalized)
    return _canonical_json(_content_payload(normalized))


def _envelope_payload(snapshot: CircuitIntentSnapshot) -> dict[str, Any]:
    return {
        "schema": CIRCUIT_INTENT_SCHEMA,
        "schema_version": CIRCUIT_INTENT_SCHEMA_VERSION,
        "snapshot_digest": snapshot.snapshot_digest,
        "content": _content_payload(snapshot.content),
    }


def make_snapshot(content: CircuitIntentContent) -> CircuitIntentSnapshot:
    """Create a self-verifying snapshot envelope."""

    normalized = normalize_content(content)
    validate_content(normalized)
    snapshot = CircuitIntentSnapshot(
        snapshot_digest=_digest(_canonical_json(_content_payload(normalized))),
        content=normalized,
    )
    encode_snapshot(snapshot)
    return snapshot


def verify_snapshot(snapshot: CircuitIntentSnapshot) -> None:
    """Require canonical ordering, valid topology, and a matching content digest."""

    validate_content(snapshot.content)
    normalized = normalize_content(snapshot.content)
    if normalized != snapshot.content:
        raise CircuitIntentValidationError(
            "canonical.order", "Circuit Intent content is not canonically ordered"
        )
    expected = _digest(_canonical_json(_content_payload(normalized)))
    if snapshot.snapshot_digest != expected:
        raise CircuitIntentValidationError(
            "digest.mismatch", "Circuit Intent snapshot digest does not match content"
        )


def encode_snapshot(snapshot: CircuitIntentSnapshot) -> bytes:
    """Encode a verified snapshot as byte-stable canonical JSON."""

    verify_snapshot(snapshot)
    payload = _canonical_json(_envelope_payload(snapshot))
    if len(payload) > CircuitParseLimits().max_input_bytes:
        raise CircuitIntentValidationError(
            "budget.exceeded", "encoded Circuit Intent exceeds the input byte ceiling"
        )
    return payload
