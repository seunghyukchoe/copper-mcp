"""Shared validation primitives for untrusted request payloads.

Every public service that accepts a JSON-shaped request parses it here first, so
field, type, range, and character rules cannot drift between services. These
helpers never echo unvalidated input back to the caller.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from copper_mcp.board_ir import NetClass

NET_CLASS_ID = "class:request"
NET_CLASS_NAME = "Request"

CONSTRAINT_FIELDS = (
    "clearance_nm",
    "track_width_nm",
    "via_diameter_nm",
    "via_drill_nm",
)
COPPER_LAYER = re.compile(r"^(?:F\.Cu|B\.Cu|In(?:[1-9]|[12][0-9]|3[0-2])\.Cu)$")
MAX_BOARD_PATH_CHARACTERS = 4096
MAX_DIMENSION_NM = 1_000_000_000
MAX_JSON_SAFE_INTEGER = (1 << 53) - 1

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_FIELDS = 64


class RequestError(ValueError):
    """Raised when an untrusted request payload violates its declared contract."""


def mapping(name: str, value: Any) -> dict[str, Any]:
    """Accept only a bounded object whose field names are strings."""

    if not isinstance(value, Mapping):
        raise RequestError(f"{name} must be an object")
    if len(value) > _MAX_FIELDS:
        raise RequestError(f"{name} has too many fields")
    for key in value:
        if not isinstance(key, str):
            raise RequestError(f"{name} field names must be strings")
    return dict(value)


def known_fields(name: str, payload: Mapping[str, Any], allowed: frozenset[str]) -> None:
    """Reject unsupported fields by count, never by echoing caller-controlled names."""

    unknown = len(set(payload) - allowed)
    if unknown:
        raise RequestError(
            f"{name} has {unknown} unsupported field(s); supported fields are: "
            f"{', '.join(sorted(allowed))}"
        )


def required_fields(name: str, payload: Mapping[str, Any], required: tuple[str, ...]) -> None:
    """Reject a payload that omits any mandatory field."""

    missing = sorted(set(required) - set(payload))
    if missing:
        raise RequestError(f"{name} is missing required fields: {', '.join(missing)}")


def integer(name: str, value: Any, *, minimum: int, maximum: int) -> int:
    """Accept only a bounded integer; booleans are never integers at this boundary."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise RequestError(f"{name} must be between {minimum} and {maximum}")
    return int(value)


def text(name: str, value: Any, *, maximum: int) -> str:
    """Accept only a bounded, control-character-free string."""

    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise RequestError(f"{name} must be a non-empty string of at most {maximum} characters")
    if _CONTROL_CHARACTERS.search(value):
        raise RequestError(f"{name} must not contain control characters")
    return value


def boolean(name: str, value: Any) -> bool:
    """Accept only a real boolean, never an integer or string spelling."""

    if not isinstance(value, bool):
        raise RequestError(f"{name} must be a boolean")
    return value


def board_path(value: Any) -> str:
    """Validate the textual shape of a board path before any filesystem access."""

    return text("board", value, maximum=MAX_BOARD_PATH_CHARACTERS)


def copper_layer(name: str, value: Any) -> str:
    """Accept only a documented KiCad copper layer name."""

    layer = text(name, value, maximum=64)
    if not COPPER_LAYER.fullmatch(layer):
        raise RequestError(f"{name} must be a documented KiCad copper layer name")
    return layer


def net_class_constraints(payload: Any) -> NetClass:
    """Build the typed net class applied to a converted board from caller values only."""

    fields = mapping("constraints", payload)
    known_fields("constraints", fields, frozenset(CONSTRAINT_FIELDS))
    required_fields("constraints", fields, CONSTRAINT_FIELDS)
    values = {
        field: integer(
            f"constraints.{field}",
            fields[field],
            minimum=0 if field == "clearance_nm" else 1,
            maximum=MAX_DIMENSION_NM,
        )
        for field in CONSTRAINT_FIELDS
    }
    try:
        return NetClass(id=NET_CLASS_ID, name=NET_CLASS_NAME, **values)
    except ValueError as error:
        raise RequestError(f"constraints are invalid: {error}") from error
