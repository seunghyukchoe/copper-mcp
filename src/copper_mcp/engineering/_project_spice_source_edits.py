"""Standalone bounded CST edit kernel for confined project SPICE source preparation."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import NoReturn

from copper_mcp.adapters.cst import Splice, line_indent, span
from copper_mcp.adapters.sexpr import SExpr, children, is_quoted_atom, parse_sexpr
from copper_mcp.board_ir import ParseLimits
from copper_mcp.engineering.capture import CaptureLimits

_MODEL_DIRECTORY = ".copper-spice-models"
_SIMULATION_FIELDS = frozenset({"Sim.Library", "Sim.Name", "Sim.Pins"})


class ProjectSpiceSourceError(ValueError):
    """A fixed redacted refusal from confined SPICE source preparation."""


def _fail(message: str = "project SPICE source preparation refused") -> NoReturn:
    raise ProjectSpiceSourceError(message)


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail("project SPICE source preparation deadline expired")


@dataclass(frozen=True, slots=True, repr=False)
class _DesiredFields:
    captured_library_path: str
    generated_library_path: str
    definition_name: str
    pins: tuple[tuple[str, str], ...]

    def __repr__(self) -> str:
        return "<_DesiredFields redacted>"


def _project_relative(path: str, parent: PurePosixPath) -> str:
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        _fail()
    if parsed.as_posix() != path or "$" in path or "\\" in path or path.startswith("~"):
        _fail()
    parent_parts = () if parent.as_posix() == "." else parent.parts
    if parsed.parts[: len(parent_parts)] != parent_parts or len(parsed.parts) == len(parent_parts):
        _fail()
    relative = PurePosixPath(*parsed.parts[len(parent_parts) :]).as_posix()
    if relative == _MODEL_DIRECTORY or relative.startswith(_MODEL_DIRECTORY + "/"):
        _fail()
    return relative


def _property_values(symbol: SExpr, deadline: float) -> dict[str, SExpr]:
    result: dict[str, SExpr] = {}
    for prop in children(symbol, "property"):
        _check(deadline)
        if (
            len(prop.items) < 3
            or not isinstance(prop.items[1], str)
            or not isinstance(prop.items[2], str)
            or not is_quoted_atom(prop.items[1])
            or not is_quoted_atom(prop.items[2])
            or prop.items[1] in result
        ):
            _fail()
        result[prop.items[1]] = prop
    return result


def _simulation_name(name: str) -> bool:
    lowered = name.casefold()
    return lowered.startswith("sim.") or lowered.startswith("spice")


def _walk(root: SExpr, deadline: float) -> Iterator[SExpr]:
    pending = [root]
    while pending:
        _check(deadline)
        node = pending.pop()
        yield node
        pending.extend(item for item in node.items[1:] if isinstance(item, SExpr))


def _quoted_value_span(prop: SExpr, text: str, deadline: float) -> tuple[int, int]:
    _check(deadline)
    start, end = span(prop, text)
    _check(deadline)
    quoted: list[tuple[int, int]] = []
    index = start
    while index < end:
        if index % 4096 == 0:
            _check(deadline)
        if text[index] != '"':
            index += 1
            continue
        token_start = index
        index += 1
        escaped = False
        while index < end:
            character = text[index]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                index += 1
                quoted.append((token_start, index))
                break
            index += 1
        else:
            _fail()
        if len(quoted) == 2:
            _check(deadline)
            return quoted[1]
    _fail()


def _tokens(value: str, separators: str, deadline: float) -> tuple[str, ...]:
    """Split only on the caller's explicit ASCII separators, without Unicode folding."""

    tokens: list[str] = []
    start: int | None = None
    for index, character in enumerate(value):
        if index % 4096 == 0:
            _check(deadline)
        if character in separators:
            if start is not None:
                tokens.append(value[start:index])
                _check(deadline)
                start = None
        elif start is None:
            start = index
    if start is not None:
        tokens.append(value[start:])
        _check(deadline)
    return tuple(tokens)


def _parse_existing_pins(value: str, deadline: float) -> tuple[tuple[str, str], ...]:
    pairs = []
    pins: set[str] = set()
    ports: set[str] = set()
    # tao::pegtl::ascii::space: HT, LF, VT, FF, CR and ordinary ASCII space.
    for token in _tokens(value, " \t\n\v\f\r", deadline):
        _check(deadline)
        if token.count("=") != 1:
            _fail()
        pin, port = token.split("=", 1)
        if not pin or not port or pin in pins or port in ports:
            _fail()
        pins.add(pin)
        ports.add(port)
        pairs.append((pin, port))
    if not pairs:
        _fail()
    return tuple(sorted(pairs))


_NATIVE_TEXT_ESCAPES = {
    "dblquote": '"',
    "quote": "'",
    "lt": "<",
    "gt": ">",
    "backslash": "\\",
    "slash": "/",
    "bar": "|",
    "comma": ",",
    "colon": ":",
    "space": " ",
    "dollar": "$",
    "tab": "\t",
    "return": "\n",
    "brace": "{",
}


def _unescape_literal_text(value: str, deadline: float) -> str:
    """Decode only fixed KiCad UnescapeString tokens; refuse dynamic or unknown braces."""

    result: list[str] = []
    index = 0
    while index < len(value):
        if index % 4096 == 0:
            _check(deadline)
        character = value[index]
        if character in "$~^_" and index + 1 < len(value) and value[index + 1] == "{":
            _fail()
        if character == "}":
            _fail()
        if character != "{":
            result.append(character)
            index += 1
            continue
        end = index + 1
        while end < len(value) and value[end] != "}":
            if end % 4096 == 0:
                _check(deadline)
            if value[end] == "{":
                _fail()
            end += 1
        if end >= len(value):
            _fail()
        token = value[index + 1 : end]
        replacement = _NATIVE_TEXT_ESCAPES.get(token)
        if replacement is None:
            _fail()
        result.append(replacement)
        index = end + 1
    _check(deadline)
    return "".join(result)


def _refuse_dynamic_or_directive_text(parsed: SExpr, deadline: float) -> None:
    """Refuse only screen-level text; no unresolved GetShownText expansion is attempted."""

    for node in (*children(parsed, "text"), *children(parsed, "text_box")):
        _check(deadline)
        if len(node.items) < 2 or not isinstance(node.items[1], str):
            _fail()
        shown_text = _unescape_literal_text(node.items[1], deadline)
        for line in _tokens(shown_text, "\r\n", deadline):
            _check(deadline)
            upper = line.upper()
            if upper.startswith("."):
                _fail()
            coupling = _tokens(upper, " \t", deadline)
            if (
                len(coupling) >= 4
                and coupling[0].startswith("K")
                and coupling[1].startswith("L")
                and coupling[2].startswith("L")
            ):
                _fail()


def _field_text(name: str, value: str) -> str:
    return (
        f"(property {json.dumps(name, ensure_ascii=False)} "
        f"{json.dumps(value, ensure_ascii=False)} (at 0 0 0) "
        "(effects (font (size 1.27 1.27)) hide))"
    )


def _json_string_size(parts: Iterable[str], deadline: float) -> int:
    size = 2
    characters = 0
    for part in parts:
        _check(deadline)
        for character in part:
            characters += 1
            if characters % 4096 == 0:
                _check(deadline)
            if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
                size += 2
            elif ord(character) < 0x20:
                size += 6
            else:
                size += len(character.encode("utf-8"))
    _check(deadline)
    return size


def _pin_parts(pins: tuple[tuple[str, str], ...]) -> Iterator[str]:
    for index, (pin, port) in enumerate(pins):
        if index:
            yield " "
        yield pin
        yield "="
        yield port


def _field_text_size(name: str, value_parts: Iterable[str], deadline: float) -> int:
    prefix = len(b"(property ")
    separator = 1
    suffix = len(b" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))")
    return (
        prefix
        + _json_string_size((name,), deadline)
        + separator
        + _json_string_size(value_parts, deadline)
        + suffix
    )


def _target_value_parts(target: _DesiredFields, name: str) -> Iterable[str]:
    if name == "Sim.Library":
        return (target.generated_library_path,)
    if name == "Sim.Name":
        return (target.definition_name,)
    return _pin_parts(target.pins)


def _target_values(target: _DesiredFields) -> dict[str, str]:
    return {
        "Sim.Library": target.generated_library_path,
        "Sim.Name": target.definition_name,
        "Sim.Pins": " ".join(f"{pin}={port}" for pin, port in target.pins),
    }


def _source_edit_plan(
    path: str,
    content: bytes,
    desired: dict[tuple[str, str], _DesiredFields],
    limits: CaptureLimits,
    deadline: float,
    *,
    materialize: bool,
) -> tuple[tuple[Splice, ...], int]:
    parsed = parse_sexpr(
        content,
        ParseLimits(max_input_bytes=limits.max_file_bytes),
        check_deadline=lambda: _check(deadline),
    )
    if parsed.head != "kicad_sch":
        _fail()
    text = content.decode("utf-8", errors="strict")
    caches = children(parsed, "lib_symbols")
    if len(caches) != 1:
        _fail()
    for node in _walk(caches[0], deadline):
        if node.head == "property" and len(node.items) > 1 and isinstance(node.items[1], str):
            if _simulation_name(node.items[1]):
                _fail()
    symbols: dict[str, SExpr] = {}
    bound = {symbol_uuid for source_path, symbol_uuid in desired if source_path == path}
    splices: list[Splice] = []
    projected_size = len(content)
    _refuse_dynamic_or_directive_text(parsed, deadline)
    for symbol in children(parsed, "symbol"):
        _check(deadline)
        uuids = children(symbol, "uuid")
        if (
            len(uuids) != 1
            or len(uuids[0].items) != 2
            or not isinstance(uuids[0].items[1], str)
            or uuids[0].items[1] in symbols
        ):
            _fail()
        symbol_uuid = uuids[0].items[1]
        symbols[symbol_uuid] = symbol
        properties = _property_values(symbol, deadline)
        simulation = {name for name in properties if _simulation_name(name)}
        if simulation - _SIMULATION_FIELDS:
            _fail()
        present = simulation & _SIMULATION_FIELDS
        if symbol_uuid not in bound:
            if present:
                _fail()
            continue
        target = desired[(path, symbol_uuid)]
        values = _target_values(target) if materialize else None
        if present and present != _SIMULATION_FIELDS:
            _fail()
        if present:
            current_library = properties["Sim.Library"].items[2]
            current_name = properties["Sim.Name"].items[2]
            current_pins = properties["Sim.Pins"].items[2]
            if (
                not isinstance(current_library, str)
                or not isinstance(current_name, str)
                or not isinstance(current_pins, str)
                or _project_relative(current_library, PurePosixPath("."))
                != target.captured_library_path
                or current_name != target.definition_name
                or _parse_existing_pins(current_pins, deadline) != target.pins
            ):
                _fail()
            for name in sorted(_SIMULATION_FIELDS):
                start, end = _quoted_value_span(properties[name], text, deadline)
                projected_size += _json_string_size(
                    _target_value_parts(target, name), deadline
                ) - len(text[start:end].encode("utf-8"))
                if materialize:
                    assert values is not None
                    splices.append(Splice(start, end, json.dumps(values[name], ensure_ascii=False)))
        else:
            references = children(symbol, "property")
            if not references:
                _fail()
            insertion = references[0].offset
            indent = line_indent(text, insertion)
            separator_size = len(("\n" + indent).encode("utf-8"))
            projected_size += (
                sum(
                    _field_text_size(name, _target_value_parts(target, name), deadline)
                    for name in ("Sim.Library", "Sim.Name", "Sim.Pins")
                )
                + 3 * separator_size
            )
            if materialize:
                assert values is not None
                replacement = ("\n" + indent).join(
                    _field_text(name, values[name])
                    for name in ("Sim.Library", "Sim.Name", "Sim.Pins")
                )
                splices.append(Splice(insertion, insertion, replacement + "\n" + indent))
    if not bound <= set(symbols):
        _fail()
    _check(deadline)
    return tuple(splices), projected_size


def _source_splices(
    path: str,
    content: bytes,
    desired: dict[tuple[str, str], _DesiredFields],
    limits: CaptureLimits,
    deadline: float,
) -> tuple[Splice, ...]:
    return _source_edit_plan(path, content, desired, limits, deadline, materialize=True)[0]


def _splice(content: bytes, splices: tuple[Splice, ...], deadline: float) -> bytes:
    """Apply character-domain splices in one bounded forward pass."""

    _check(deadline)
    text = content.decode("utf-8", errors="strict")
    ordered = sorted(splices, key=lambda item: (item.start, item.end))
    pieces: list[str] = []
    cursor = 0
    previous: Splice | None = None
    for item in ordered:
        _check(deadline)
        if item.end > len(text) or item.start < cursor:
            _fail()
        if (
            previous is not None
            and item.start == previous.start
            and item.is_insertion
            and previous.is_insertion
        ):
            _fail()
        pieces.extend((text[cursor : item.start], item.replacement))
        cursor = item.end
        previous = item
    pieces.append(text[cursor:])
    _check(deadline)
    result = "".join(pieces).encode("utf-8", errors="strict")
    _check(deadline)
    return result
