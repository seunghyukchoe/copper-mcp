"""Semantic and budget validation for Circuit Intent IR v0.1."""

from __future__ import annotations

from collections.abc import Iterable

from copper_mcp.circuit_ir.limits import CircuitParseLimits
from copper_mcp.circuit_ir.types import CircuitIntentContent


class CircuitIntentValidationError(ValueError):
    """Typed, non-echoing validation failure at the circuit trust boundary."""

    def __init__(self, code: str, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


def _require_unique(values: Iterable[str], *, kind: str) -> set[str]:
    items = tuple(values)
    unique = set(items)
    if len(items) != len(unique):
        raise CircuitIntentValidationError("identity.duplicate", f"duplicate {kind}")
    return unique


def validate_content(
    content: CircuitIntentContent,
    limits: CircuitParseLimits | None = None,
) -> None:
    """Validate budgets, references, complete pin assignment, and port topology."""

    limits = limits or CircuitParseLimits()
    if len(content.components) > min(limits.max_components, 64):
        raise CircuitIntentValidationError("budget.exceeded", "component budget exceeded")
    if len(content.nets) > min(limits.max_nets, 128):
        raise CircuitIntentValidationError("budget.exceeded", "net budget exceeded")
    if len(content.ports) > min(limits.max_ports, 32):
        raise CircuitIntentValidationError("budget.exceeded", "port budget exceeded")

    component_ids = _require_unique(
        (component.id for component in content.components), kind="component ID"
    )
    _require_unique(
        (component.reference for component in content.components), kind="component reference"
    )
    net_ids = _require_unique((net.id for net in content.nets), kind="net ID")
    _require_unique((net.name for net in content.nets), kind="net name")
    _require_unique((port.id for port in content.ports), kind="port ID")
    port_nets = _require_unique((port.net_id for port in content.ports), kind="port net assignment")
    if not port_nets <= net_ids:
        raise CircuitIntentValidationError("reference.unknown", "port references an unknown net")

    total_connections = sum(len(net.connections) for net in content.nets)
    if total_connections > min(limits.max_connections, 128):
        raise CircuitIntentValidationError("budget.exceeded", "connection budget exceeded")

    observed_pins: list[tuple[str, str]] = []
    for net in content.nets:
        net_pins = [(connection.component_id, connection.pin) for connection in net.connections]
        if len(net_pins) != len(set(net_pins)):
            raise CircuitIntentValidationError(
                "topology.duplicate_pin", "net contains a duplicate component pin"
            )
        if any(component_id not in component_ids for component_id, _ in net_pins):
            raise CircuitIntentValidationError(
                "reference.unknown", "connection references an unknown component"
            )
        if len({component_id for component_id, _ in net_pins}) != len(net_pins):
            raise CircuitIntentValidationError(
                "topology.self_short", "one component cannot connect both pins to the same net"
            )
        if len(net_pins) == 1 and net.id not in port_nets:
            raise CircuitIntentValidationError(
                "topology.dangling", "single-pin net requires an external port"
            )
        observed_pins.extend(net_pins)

    if len(observed_pins) != len(set(observed_pins)):
        raise CircuitIntentValidationError(
            "topology.multiple_nets", "component pin belongs to more than one net"
        )
    expected_pins = {(component.id, pin) for component in content.components for pin in ("1", "2")}
    if set(observed_pins) != expected_pins:
        raise CircuitIntentValidationError(
            "topology.incomplete", "every component pin must belong to exactly one net"
        )
