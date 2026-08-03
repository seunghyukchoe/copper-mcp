"""Operational limits for untrusted Circuit Intent input."""

from __future__ import annotations

from dataclasses import dataclass

MAX_CIRCUIT_INPUT_BYTES = 256_000
MAX_CIRCUIT_JSON_DEPTH = 32
MAX_CIRCUIT_JSON_VALUES = 8_192
MAX_CIRCUIT_COMPONENTS = 64
MAX_CIRCUIT_NETS = 128
MAX_CIRCUIT_PORTS = 32
MAX_CIRCUIT_CONNECTIONS = 128


@dataclass(frozen=True, slots=True)
class CircuitParseLimits:
    """Caller-tightenable bounds that cannot exceed the v0.1 schema ceiling."""

    max_input_bytes: int = MAX_CIRCUIT_INPUT_BYTES
    max_json_depth: int = MAX_CIRCUIT_JSON_DEPTH
    max_json_values: int = MAX_CIRCUIT_JSON_VALUES
    max_components: int = MAX_CIRCUIT_COMPONENTS
    max_nets: int = MAX_CIRCUIT_NETS
    max_ports: int = MAX_CIRCUIT_PORTS
    max_connections: int = MAX_CIRCUIT_CONNECTIONS

    def __post_init__(self) -> None:
        ceilings = (
            ("max_input_bytes", self.max_input_bytes, MAX_CIRCUIT_INPUT_BYTES),
            ("max_json_depth", self.max_json_depth, MAX_CIRCUIT_JSON_DEPTH),
            ("max_json_values", self.max_json_values, MAX_CIRCUIT_JSON_VALUES),
            ("max_components", self.max_components, MAX_CIRCUIT_COMPONENTS),
            ("max_nets", self.max_nets, MAX_CIRCUIT_NETS),
            ("max_ports", self.max_ports, MAX_CIRCUIT_PORTS),
            ("max_connections", self.max_connections, MAX_CIRCUIT_CONNECTIONS),
        )
        for name, value, ceiling in ceilings:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("Circuit Intent parse limits must be positive integers")
            if value > ceiling:
                raise ValueError(f"{name} cannot exceed the Circuit Intent v0.1 ceiling")
