"""Bounded extraction of KiCad project text variables.

This reads only the ``text_variables`` member needed for schematic hierarchy
resolution.  It does not claim that the rest of a KiCad project document is valid.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, NoReturn

_MAX_VARIABLES = 128
_MAX_PROJECT_BYTES = 32 * 1024 * 1024
_MAX_VARIABLE_KEY_CHARS = 128
_MAX_VARIABLE_VALUE_CHARS = 4096
_VARIABLE_NAME = re.compile(r"[A-Za-z0-9_:]+")


class ProjectSettingsError(ValueError):
    """A fixed, redacted refusal from the project settings boundary."""


class ProjectSettingsDeadlineError(ProjectSettingsError):
    """The shared cooperative deadline expired; this is not malformed JSON."""


def _fail() -> NoReturn:
    raise ProjectSettingsError("KiCad project settings are malformed")


def _validate_text_variables(value: object) -> dict[str, str]:
    if type(value) is not dict or len(value) > _MAX_VARIABLES:
        _fail()
    variables: dict[str, str] = {}
    for key, item in value.items():
        if (
            type(key) is not str
            or not key
            or len(key) > _MAX_VARIABLE_KEY_CHARS
            or not _VARIABLE_NAME.fullmatch(key)
            or type(item) is not str
            or len(item) > _MAX_VARIABLE_VALUE_CHARS
        ):
            _fail()
        variables[key] = item
    return variables


def parse_project_document(payload: bytes, *, deadline: float | None = None) -> dict[str, Any]:
    """Decode bounded project JSON and validate its variable map, not all KiCad semantics."""

    # Keep these imports local so this small private boundary reuses the established
    # KiCad CLI JSON guards without coupling module import to the CLI adapter.
    import json

    from copper_mcp.kicad_cli import (
        _drc_object_pairs,
        _finite_json_float,
        _preflight_drc_json,
        _reject_json_constant,
        _validate_drc_json_tree,
    )

    normalized_deadline = None
    if deadline is not None:
        if type(deadline) not in (int, float):
            _fail()
        try:
            normalized_deadline = float(deadline)
        except OverflowError:
            pass
        if normalized_deadline is None or not math.isfinite(normalized_deadline):
            _fail()

    def check_deadline() -> None:
        if normalized_deadline is not None and time.monotonic() >= normalized_deadline:
            raise ProjectSettingsDeadlineError("KiCad project settings deadline expired")

    check_deadline()
    decoded: Any | None = None
    try:
        if type(payload) is not bytes or len(payload) > _MAX_PROJECT_BYTES:
            _fail()
        text = payload.decode("utf-8", errors="strict")
        _preflight_drc_json(text, check_deadline=check_deadline)
        decoded = json.loads(
            text,
            object_pairs_hook=_drc_object_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
        check_deadline()
        _validate_drc_json_tree(decoded, check_deadline=check_deadline)
    except ProjectSettingsDeadlineError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError):
        decoded = None
    check_deadline()
    if decoded is None or type(decoded) is not dict:
        _fail()
    _validate_text_variables(decoded.get("text_variables", {}))
    check_deadline()
    return decoded


def extract_project_variables(payload: bytes, *, deadline: float | None = None) -> dict[str, str]:
    """Return a private copy for the fixed schematic filename resolver."""
    return _validate_text_variables(
        parse_project_document(payload, deadline=deadline).get("text_variables", {})
    )
