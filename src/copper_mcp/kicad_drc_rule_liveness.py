"""Private construction and report checks for KiCad custom-rule liveness."""

from __future__ import annotations

import json
import math
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, NoReturn

_MARKER_PREFIX = "__copper_mcp_rule_liveness_"


class KiCadDrcRuleLivenessError(ValueError):
    """Fixed refusal without project, rule, report, UUID, or marker context."""


def _fail() -> NoReturn:
    raise KiCadDrcRuleLivenessError("KiCad custom-rule liveness was not established")


def _check(deadline: float) -> None:
    finite = False
    if type(deadline) in (int, float):
        try:
            finite = math.isfinite(deadline)
        except (OverflowError, TypeError):
            pass
    if not finite:
        _fail()
    if time.monotonic() >= deadline:
        raise KiCadDrcRuleLivenessError("KiCad custom-rule liveness deadline expired")


@dataclass(frozen=True, slots=True, repr=False)
class RuleLivenessWitness:
    item_uuid: str
    marker: str

    def __post_init__(self) -> None:
        if (
            type(self.item_uuid) is not str
            or type(self.marker) is not str
            or not self.marker.startswith(_MARKER_PREFIX)
            or not 1 <= len(self.marker) <= 128
        ):
            _fail()
        try:
            parsed = uuid.UUID(self.item_uuid)
        except (AttributeError, ValueError):
            _fail()
        if str(parsed) != self.item_uuid:
            _fail()

    def __repr__(self) -> str:
        return "<RuleLivenessWitness redacted>"


def custom_rule_path(board_relative: str) -> str:
    if type(board_relative) is not str or not board_relative.lower().endswith(".kicad_pcb"):
        _fail()
    return str(PurePosixPath(board_relative).with_suffix(".kicad_dru"))


def has_custom_rules(context: object, board_relative: str) -> bool:
    if type(context) is not dict:
        _fail()
    return custom_rule_path(board_relative) in context


def admit_rule_liveness_context(
    context: object,
    board_relative: object,
    *,
    max_file_bytes: object,
    max_context_files: object,
    max_context_bytes: object,
    deadline: float,
) -> None:
    """Admit every key and byte ceiling before hashing or writing a private store."""

    _check(deadline)
    if (
        type(context) is not dict
        or type(board_relative) is not str
        or type(max_file_bytes) is not int
        or type(max_context_files) is not int
        or type(max_context_bytes) is not int
        or min(max_file_bytes, max_context_files, max_context_bytes) < 1
        or len(context) > max_context_files
        or board_relative not in context
        or not board_relative.lower().endswith(".kicad_pcb")
    ):
        _fail()
    aliases: set[str] = set()
    total = 0
    for name, payload in context.items():
        _check(deadline)
        if (
            type(name) is not str
            or type(payload) is not bytes
            or not name
            or len(name) > 4096
            or "\x00" in name
        ):
            _fail()
        invalid_text = False
        try:
            invalid_text = len(name.encode("utf-8")) > 4096
        except UnicodeError:
            invalid_text = True
        if invalid_text:
            _fail()
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or path.as_posix() != name
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            _fail()
        alias = unicodedata.normalize("NFC", name).casefold()
        if alias in aliases:
            _fail()
        aliases.add(alias)
        if len(payload) > max_file_bytes:
            _fail()
        total += len(payload)
        if total > max_context_bytes:
            _fail()
    _check(deadline)


def _strict_json(payload: bytes, deadline: float) -> dict[str, Any]:
    from copper_mcp.kicad_cli import (
        _drc_object_pairs,
        _finite_json_float,
        _preflight_drc_json,
        _reject_json_constant,
        _validate_drc_json_tree,
    )

    def checkpoint() -> None:
        _check(deadline)

    checkpoint()
    decoded: Any = None
    try:
        text = payload.decode("utf-8", errors="strict")
        _preflight_drc_json(text, check_deadline=checkpoint)
        decoded = json.loads(
            text,
            object_pairs_hook=_drc_object_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
        _validate_drc_json_tree(decoded, check_deadline=checkpoint)
    except KiCadDrcRuleLivenessError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError):
        decoded = None
    checkpoint()
    if type(decoded) is not dict:
        _fail()
    return decoded


def _project_derivative(payload: bytes | None, deadline: float) -> bytes | None:
    if payload is None:
        return None
    elif type(payload) is bytes:
        project = _strict_json(payload, deadline)
    else:
        _fail()
    board = project.get("board", {})
    if type(board) is not dict:
        _fail()
    design = board.get("design_settings", {})
    if type(design) is not dict:
        _fail()
    severities = design.get("rule_severities", {})
    if type(severities) is not dict:
        _fail()
    _check(deadline)
    return payload


def _rules_have_tokens(payload: bytes, deadline: float) -> bool:
    index = 0
    while index < len(payload):
        if index % 4096 == 0:
            _check(deadline)
        byte = payload[index]
        if byte in b" \t\r\n":
            index += 1
            continue
        if byte == ord("#"):
            newline = payload.find(b"\n", index + 1)
            if newline < 0:
                return False
            index = newline + 1
            continue
        return True
    return False


def _fresh_witness(context: dict[str, bytes], deadline: float) -> RuleLivenessWitness:
    def contains(payload: bytes, needle: bytes) -> bool:
        overlap = len(needle) - 1
        previous = b""
        for offset in range(0, len(payload), 64 * 1024):
            _check(deadline)
            chunk = previous + payload[offset : offset + 64 * 1024]
            if needle in chunk:
                return True
            previous = chunk[-overlap:]
        return False

    for _attempt in range(4):
        _check(deadline)
        item_uuid = str(uuid.uuid4())
        marker = _MARKER_PREFIX + item_uuid.replace("-", "_")
        needle_uuid = item_uuid.encode("ascii")
        needle_marker = marker.encode("ascii")
        if all(
            not contains(payload, needle_uuid) and not contains(payload, needle_marker)
            for payload in context.values()
        ):
            return RuleLivenessWitness(item_uuid, marker)
    _fail()


def build_rule_liveness_context(
    context: object,
    board_relative: str,
    *,
    max_file_bytes: int,
    max_context_files: int,
    max_context_bytes: int,
    deadline: float,
) -> tuple[dict[str, bytes], RuleLivenessWitness]:
    """Return a bounded private derivative; never alter the supplied context or rule prefix."""

    _check(deadline)
    admit_rule_liveness_context(
        context,
        board_relative,
        max_file_bytes=max_file_bytes,
        max_context_files=max_context_files,
        max_context_bytes=max_context_bytes,
        deadline=deadline,
    )
    assert type(context) is dict
    rules_relative = custom_rule_path(board_relative)
    if rules_relative not in context:
        _fail()
    admitted: dict[str, bytes] = {}
    total = 0
    for name, payload in context.items():
        _check(deadline)
        assert type(name) is str and type(payload) is bytes
        total += len(payload)
        if total > max_context_bytes:
            _fail()
        admitted[name] = payload
    witness = _fresh_witness(admitted, deadline)
    board = admitted[board_relative]
    closing_index = len(board) - 1
    while closing_index >= 0 and board[closing_index] in b" \t\r\n":
        if closing_index % 4096 == 0:
            _check(deadline)
        closing_index -= 1
    if closing_index < 0 or board[closing_index] != ord(")"):
        _fail()
    item = (
        b'\n  (gr_text "'
        + witness.marker.encode("ascii")
        + b'" (at -100 -100) (layer "F.Cu")\n'
        + b'    (uuid "'
        + witness.item_uuid.encode("ascii")
        + b'") (effects (font (size 1 1) (thickness 0.15))))\n'
    )
    projected_board_bytes = len(board) + len(item)
    if projected_board_bytes > max_file_bytes:
        _fail()
    rules = admitted[rules_relative]
    sentinel = (
        '\n(rule "CopperMCP rule loading probe '
        + witness.item_uuid
        + '"\n'
        + "  (severity error)\n"
        + "  (condition \"A.Type == 'Text' && A.Text == '"
        + witness.marker
        + "'\")\n"
        + '  (constraint assertion "0 == 1"))\n'
    ).encode("ascii")
    version = b"\n(version 1)\n" if not _rules_have_tokens(rules, deadline) else b""
    projected_rule_bytes = len(rules) + len(version) + len(sentinel)
    if projected_rule_bytes > max_file_bytes:
        _fail()
    projected_total = total - len(board) - len(rules) + projected_board_bytes + projected_rule_bytes
    if projected_total > max_context_bytes:
        _fail()
    board_derivative = board[:closing_index] + item + board[closing_index:]
    rules_derivative = rules + version + sentinel
    project_relative = str(PurePosixPath(board_relative).with_suffix(".kicad_pro"))
    project_derivative = _project_derivative(admitted.get(project_relative), deadline)
    replacements = {
        board_relative: board_derivative,
        rules_relative: rules_derivative,
    }
    if project_derivative is not None:
        replacements[project_relative] = project_derivative
    result = dict(admitted)
    for name, payload in replacements.items():
        _check(deadline)
        if len(payload) > max_file_bytes:
            _fail()
        result[name] = payload
    if len(result) > max_context_files:
        _fail()
    derivative_total = 0
    for payload in result.values():
        _check(deadline)
        derivative_total += len(payload)
        if derivative_total > max_context_bytes:
            _fail()
    _check(deadline)
    return result, witness


def require_rule_liveness_witness(
    payload: bytes,
    witness: RuleLivenessWitness,
    *,
    deadline: float,
) -> None:
    """Require proof that KiCad retained the augmented rules instead of parser fallback.

    KiCad 10.0.5 ignores the boolean returned while compiling an assertion expression, so this
    witness intentionally makes no claim that every original assertion expression compiled.
    """

    _check(deadline)
    if type(payload) is not bytes or type(witness) is not RuleLivenessWitness:
        _fail()
    report = _strict_json(payload, deadline)
    violations = report.get("violations")
    if type(violations) is not list or len(violations) > 100_000:
        _fail()
    for finding in violations:
        _check(deadline)
        if (
            type(finding) is not dict
            or finding.get("type") != "assertion_failure"
            or finding.get("severity") != "error"
            or finding.get("excluded", False) is not False
        ):
            continue
        items = finding.get("items")
        if (
            type(items) is list
            and len(items) == 1
            and type(items[0]) is dict
            and items[0].get("uuid") == witness.item_uuid
        ):
            _check(deadline)
            return
    _fail()


__all__: list[str] = []
