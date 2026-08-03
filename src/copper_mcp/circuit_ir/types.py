"""Frozen Circuit Intent IR v0.1 domain types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

CIRCUIT_INTENT_SCHEMA = "copper.circuit-intent"
CIRCUIT_INTENT_SCHEMA_VERSION = "0.1.0"

_SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
_TYPED_ID = re.compile(r"^(?:circuit|component|net|port):[a-z0-9][a-z0-9._-]{0,63}$")
_PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_NET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.+/:-]{0,63}$")
_REFERENCE = re.compile(r"^[RC][1-9][0-9]{0,5}$")


def _utf8_text(name: str, value: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name} is malformed")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError(f"{name} contains control characters")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} contains an invalid Unicode surrogate") from error


def _typed_id(name: str, value: str, prefix: str) -> None:
    if not isinstance(value, str) or not value.startswith(prefix) or not _TYPED_ID.fullmatch(value):
        raise ValueError(f"{name} must be a stable {prefix.rstrip(':')} ID")


def _tuple_of(name: str, value: object, item_type: type[object]) -> None:
    if not isinstance(value, tuple) or not all(isinstance(item, item_type) for item in value):
        raise ValueError(f"{name} must be an immutable tuple of {item_type.__name__}")


def _sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be content-addressed with sha256")


class ComponentKind(StrEnum):
    """Component primitives accepted by Circuit Intent IR v0.1."""

    RESISTOR = "resistor"
    CAPACITOR_UNPOLARIZED = "capacitor_unpolarized"


class PortDirection(StrEnum):
    """Logical direction metadata for an externally visible net."""

    INPUT = "input"
    OUTPUT = "output"
    BIDIRECTIONAL = "bidirectional"
    PASSIVE = "passive"


@dataclass(frozen=True, slots=True)
class Component:
    """One independently identified two-pin passive component."""

    id: str
    kind: ComponentKind
    reference: str
    value: str

    def __post_init__(self) -> None:
        _typed_id("component ID", self.id, "component:")
        if not isinstance(self.kind, ComponentKind):
            raise ValueError("component kind is unsupported")
        if not isinstance(self.reference, str) or not _REFERENCE.fullmatch(self.reference):
            raise ValueError("component reference is malformed")
        expected_prefix = "R" if self.kind is ComponentKind.RESISTOR else "C"
        if not self.reference.startswith(expected_prefix):
            raise ValueError("component reference is incompatible with its kind")
        _utf8_text("component value", self.value, maximum=64)


@dataclass(frozen=True, slots=True, order=True)
class Connection:
    """One component-pin membership in a logical net."""

    component_id: str
    pin: str

    def __post_init__(self) -> None:
        _typed_id("connection component ID", self.component_id, "component:")
        if self.pin not in {"1", "2"}:
            raise ValueError("Circuit Intent IR v0.1 supports only pins 1 and 2")


@dataclass(frozen=True, slots=True)
class Net:
    """One named logical connection set."""

    id: str
    name: str
    connections: tuple[Connection, ...]

    def __post_init__(self) -> None:
        _typed_id("net ID", self.id, "net:")
        if not isinstance(self.name, str) or not _NET_NAME.fullmatch(self.name):
            raise ValueError("net name is malformed")
        _tuple_of("net connections", self.connections, Connection)
        if not self.connections:
            raise ValueError("net must contain at least one connection")


@dataclass(frozen=True, slots=True)
class Port:
    """One external interface attached to a logical net."""

    id: str
    net_id: str
    direction: PortDirection

    def __post_init__(self) -> None:
        _typed_id("port ID", self.id, "port:")
        _typed_id("port net ID", self.net_id, "net:")
        if not isinstance(self.direction, PortDirection):
            raise ValueError("port direction is unsupported")


@dataclass(frozen=True, slots=True)
class CircuitIntentContent:
    """Canonical logical topology hashed by the snapshot envelope."""

    circuit_id: str
    project_name: str
    title: str
    components: tuple[Component, ...]
    nets: tuple[Net, ...]
    ports: tuple[Port, ...] = ()

    def __post_init__(self) -> None:
        _typed_id("circuit ID", self.circuit_id, "circuit:")
        if not isinstance(self.project_name, str) or not _PROJECT_NAME.fullmatch(self.project_name):
            raise ValueError("project name is malformed")
        _utf8_text("circuit title", self.title, maximum=120)
        _tuple_of("components", self.components, Component)
        _tuple_of("nets", self.nets, Net)
        _tuple_of("ports", self.ports, Port)
        if not self.components or not self.nets:
            raise ValueError("circuit must contain components and nets")


@dataclass(frozen=True, slots=True)
class CircuitIntentSnapshot:
    """Self-verifying content-addressed Circuit Intent envelope."""

    snapshot_digest: str
    content: CircuitIntentContent
    schema: str = CIRCUIT_INTENT_SCHEMA
    schema_version: str = CIRCUIT_INTENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.content, CircuitIntentContent):
            raise ValueError("snapshot content must be CircuitIntentContent")
        if self.schema != CIRCUIT_INTENT_SCHEMA:
            raise ValueError("Circuit Intent schema discriminator is unsupported")
        if self.schema_version != CIRCUIT_INTENT_SCHEMA_VERSION:
            raise ValueError("Circuit Intent schema version is unsupported")
        _sha256("snapshot digest", self.snapshot_digest)
