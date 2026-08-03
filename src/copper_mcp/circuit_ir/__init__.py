"""Canonical, MCP-independent Circuit Intent IR v0.1 contracts."""

from copper_mcp.circuit_ir.canonical import (
    canonical_content_bytes,
    encode_snapshot,
    make_content,
    make_snapshot,
    normalize_content,
    verify_snapshot,
)
from copper_mcp.circuit_ir.codec import decode_snapshot_json, snapshot_from_content
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

__all__ = [
    "CIRCUIT_INTENT_SCHEMA",
    "CIRCUIT_INTENT_SCHEMA_VERSION",
    "CircuitIntentContent",
    "CircuitIntentSnapshot",
    "CircuitIntentValidationError",
    "CircuitParseLimits",
    "Component",
    "ComponentKind",
    "Connection",
    "Net",
    "Port",
    "PortDirection",
    "canonical_content_bytes",
    "decode_snapshot_json",
    "encode_snapshot",
    "make_content",
    "make_snapshot",
    "normalize_content",
    "snapshot_from_content",
    "validate_content",
    "verify_snapshot",
]
