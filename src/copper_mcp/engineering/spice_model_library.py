"""Bounded reader for the private ``spice-passive-diode/v1`` library profile."""

from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass
from typing import Literal, NoReturn, cast

_MAX_CONTENT_BYTES = 1024 * 1024
_MAX_LINES = 10_000
_MAX_LINE_BYTES = 8192
_MAX_DEFINITIONS = 128
_MAX_PORTS = 64
_MAX_ELEMENTS = 4096
_MAX_EXPANDED_ELEMENTS = 100_000
_MAX_NUMERIC_BYTES = 64
_IDENTIFIER = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_NUMBER = re.compile(
    rb"[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))(?:[eE][+-]?[0-9]+)?"
    rb"(?:[fFpPnNuUmMkKgGtT]|[mM][eE][gG])?\Z"
)
_MODEL_HEADER = re.compile(
    rb"\.model[ \t]+([A-Za-z_][A-Za-z0-9_]{0,63})[ \t]+D[ \t]*\((.*)\)\Z", re.IGNORECASE
)
_PARAMETERS = frozenset({b"IS", b"N", b"RS", b"CJO", b"VJ", b"M", b"TT", b"BV", b"IBV", b"TNOM"})
_SUFFIX_SCALES = {
    b"f": 1e-15,
    b"p": 1e-12,
    b"n": 1e-9,
    b"u": 1e-6,
    b"m": 1e-3,
    b"k": 1e3,
    b"meg": 1e6,
    b"g": 1e9,
    b"t": 1e12,
}


class SpiceModelLibraryError(ValueError):
    """A fixed refusal that never discloses captured model text."""


@dataclass(frozen=True, slots=True, repr=False)
class SpiceDefinition:
    name: str
    kind: Literal["diode", "subcircuit"]
    ports: tuple[str, ...]
    source: bytes
    dependencies: tuple[str, ...]

    def __repr__(self) -> str:
        return "<SpiceDefinition redacted>"

    @property
    def digest(self) -> str:
        return _digest(self.source, math.inf)


@dataclass(frozen=True, slots=True, repr=False)
class SpiceModelLibrary:
    content: bytes
    definitions: tuple[SpiceDefinition, ...]

    def __repr__(self) -> str:
        return "<SpiceModelLibrary redacted>"

    @property
    def digest(self) -> str:
        return _digest(self.content, math.inf)


@dataclass(frozen=True, slots=True)
class _LogicalLine:
    text: bytes
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _Element:
    kind: Literal["R", "C", "L", "D", "X"]
    name: bytes
    dependency: bytes | None
    port_count: int


@dataclass(frozen=True, slots=True)
class _DefinitionRecord:
    definition: SpiceDefinition
    elements: tuple[_Element, ...]


def _refuse() -> NoReturn:
    raise SpiceModelLibraryError("SPICE model library is malformed")


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise SpiceModelLibraryError("SPICE model library deadline expired")


def _validate_deadline(deadline: float) -> float:
    if type(deadline) not in (int, float):
        _refuse()
    if type(deadline) is int and not -(1 << 1023) <= deadline <= 1 << 1023:
        _refuse()
    value = float(deadline)
    if not math.isfinite(value):
        _refuse()
    return value


def _digest(content: bytes, deadline: float) -> str:
    digest = hashlib.sha256()
    for offset in range(0, len(content), 64 * 1024):
        _check_deadline(deadline)
        digest.update(content[offset : offset + 64 * 1024])
    _check_deadline(deadline)
    return "sha256:" + digest.hexdigest()


def _identifier(value: bytes) -> bool:
    return _IDENTIFIER.fullmatch(value) is not None


def _node(value: bytes) -> bool:
    if _identifier(value):
        return True
    return bool(value) and len(value) <= 64 and value.isdigit()


def _number(value: bytes) -> bool:
    if len(value) > _MAX_NUMERIC_BYTES or _NUMBER.fullmatch(value) is None:
        return False
    normalized = value.lower()
    suffix = b""
    if normalized.endswith(b"meg"):
        suffix = b"meg"
    elif normalized[-1:] in _SUFFIX_SCALES:
        suffix = normalized[-1:]
    numeric = normalized[: -len(suffix)] if suffix else normalized
    try:
        parsed = float(numeric)
        return math.isfinite(parsed) and math.isfinite(parsed * _SUFFIX_SCALES.get(suffix, 1.0))
    except ValueError:
        return False


def _validate_content_bytes(content: bytes, deadline: float) -> None:
    for offset, value in enumerate(content):
        if offset % (64 * 1024) == 0:
            _check_deadline(deadline)
        if value == 13:
            if offset + 1 == len(content) or content[offset + 1] != 10:
                _refuse()
        elif value not in {9, 10} and not 32 <= value <= 126:
            _refuse()
    _check_deadline(deadline)


def _logical_lines(content: bytes, deadline: float) -> tuple[_LogicalLine, ...]:
    physical = content.splitlines(keepends=True)
    if len(physical) > _MAX_LINES:
        _refuse()
    logical: list[_LogicalLine] = []
    offset = 0
    previous_code_end: int | None = None
    for raw in physical:
        _check_deadline(deadline)
        start = offset
        offset += len(raw)
        line = raw[:-2] if raw.endswith(b"\r\n") else raw[:-1] if raw.endswith(b"\n") else raw
        if len(line) > _MAX_LINE_BYTES:
            _refuse()
        stripped = line.strip(b" \t")
        if not stripped or stripped.startswith(b"*"):
            previous_code_end = None
            continue
        if stripped.startswith(b"+"):
            if not logical or previous_code_end != start:
                _refuse()
            combined = logical[-1].text + b" " + stripped[1:].strip(b" \t")
            if len(combined) > _MAX_LINE_BYTES:
                _refuse()
            prior = logical[-1]
            logical[-1] = _LogicalLine(combined, prior.start, offset)
            previous_code_end = offset
            continue
        if b";" in stripped or b"$" in stripped or b"//" in stripped or b"\\" in stripped:
            _refuse()
        if len(stripped) > _MAX_LINE_BYTES:
            _refuse()
        logical.append(_LogicalLine(stripped, start, offset))
        previous_code_end = offset
    return tuple(logical)


def _parse_model(line: _LogicalLine, content: bytes, deadline: float) -> _DefinitionRecord:
    _check_deadline(deadline)
    header = _MODEL_HEADER.fullmatch(line.text)
    if header is None:
        _refuse()
    parameters = header.group(2).split()
    if not parameters:
        _refuse()
    seen: set[bytes] = set()
    for parameter in parameters:
        _check_deadline(deadline)
        key, separator, value = parameter.partition(b"=")
        normalized = key.upper()
        if (
            separator != b"="
            or normalized not in _PARAMETERS
            or normalized in seen
            or not _number(value)
        ):
            _refuse()
        seen.add(normalized)
    definition = SpiceDefinition(
        header.group(1).decode("ascii"), "diode", ("A", "K"), content[line.start : line.end], ()
    )
    return _DefinitionRecord(definition, ())


def _parse_element(line: _LogicalLine, deadline: float) -> _Element:
    _check_deadline(deadline)
    tokens = line.text.split()
    if not tokens:
        _refuse()
    kind = tokens[0][:1].upper()
    if kind not in {b"R", b"C", b"L", b"D", b"X"} or not _identifier(tokens[0]):
        _refuse()
    if kind in {b"R", b"C", b"L"}:
        if len(tokens) != 4 or not all(_node(node) for node in tokens[1:3]):
            _refuse()
        if not _number(tokens[3]):
            _refuse()
        return _Element(cast(Literal["R", "C", "L"], kind.decode("ascii")), tokens[0], None, 0)
    if kind == b"D":
        if (
            len(tokens) != 4
            or not all(_node(node) for node in tokens[1:3])
            or not _identifier(tokens[3])
        ):
            _refuse()
        return _Element("D", tokens[0], tokens[3], 0)
    if (
        len(tokens) < 3
        or not all(_node(node) for node in tokens[1:-1])
        or not _identifier(tokens[-1])
    ):
        _refuse()
    return _Element("X", tokens[0], tokens[-1], len(tokens) - 2)


def _parse_subcircuit(
    lines: tuple[_LogicalLine, ...],
    start_index: int,
    content: bytes,
    deadline: float,
    max_elements: int,
) -> tuple[_DefinitionRecord, int]:
    opener = lines[start_index]
    tokens = opener.text.split()
    if len(tokens) < 3 or tokens[0].lower() != b".subckt" or not _identifier(tokens[1]):
        _refuse()
    ports = tokens[2:]
    if len(ports) > _MAX_PORTS or not all(_node(port) and port != b"0" for port in ports):
        _refuse()
    normalized_ports = [port.lower() for port in ports]
    if len(set(normalized_ports)) != len(normalized_ports):
        _refuse()
    elements: list[_Element] = []
    names: set[bytes] = set()
    index = start_index + 1
    while index < len(lines):
        _check_deadline(deadline)
        line = lines[index]
        words = line.text.split()
        if words and words[0].lower() == b".ends":
            if len(words) > 2 or (len(words) == 2 and words[1].lower() != tokens[1].lower()):
                _refuse()
            source = content[opener.start : line.end]
            dependencies = tuple(
                element.dependency.decode("ascii")
                for element in elements
                if element.dependency is not None
            )
            definition = SpiceDefinition(
                tokens[1].decode("ascii"),
                "subcircuit",
                tuple(port.decode("ascii") for port in ports),
                source,
                dependencies,
            )
            return _DefinitionRecord(definition, tuple(elements)), index + 1
        if words and words[0].startswith(b"."):
            _refuse()
        if len(elements) >= max_elements:
            _refuse()
        element = _parse_element(line, deadline)
        normalized_name = element.name.lower()
        if normalized_name in names:
            _refuse()
        names.add(normalized_name)
        elements.append(element)
        index += 1
    _refuse()


def _validate_records(records: tuple[_DefinitionRecord, ...], deadline: float) -> None:
    definitions: dict[bytes, _DefinitionRecord] = {}
    for record in records:
        _check_deadline(deadline)
        key = record.definition.name.encode("ascii").lower()
        if key in definitions:
            _refuse()
        definitions[key] = record
    expanded: dict[bytes, int] = {}
    visiting: set[bytes] = set()
    for root, record in definitions.items():
        _check_deadline(deadline)
        if record.definition.kind == "diode":
            expanded[root] = 1
            continue
        if root in expanded:
            continue
        stack: list[tuple[bytes, bool]] = [(root, False)]
        while stack:
            _check_deadline(deadline)
            name, complete = stack.pop()
            if name in expanded:
                continue
            current = definitions[name]
            if complete:
                total = 0
                for element in current.elements:
                    _check_deadline(deadline)
                    if element.kind == "X":
                        assert element.dependency is not None
                        total += 1 + expanded[element.dependency.lower()]
                    else:
                        total += 1
                    if total > _MAX_EXPANDED_ELEMENTS:
                        _refuse()
                expanded[name] = total
                visiting.remove(name)
                continue
            if name in visiting:
                _refuse()
            visiting.add(name)
            stack.append((name, True))
            for element in reversed(current.elements):
                _check_deadline(deadline)
                if element.dependency is None:
                    continue
                dependency = definitions.get(element.dependency.lower())
                if dependency is None:
                    _refuse()
                if element.kind == "D" and dependency.definition.kind != "diode":
                    _refuse()
                if element.kind == "X":
                    if dependency.definition.kind != "subcircuit" or element.port_count != len(
                        dependency.definition.ports
                    ):
                        _refuse()
                target = element.dependency.lower()
                if target in visiting:
                    _refuse()
                if target not in expanded:
                    stack.append((target, False))


def parse_spice_model_library(content: bytes, *, deadline: float) -> SpiceModelLibrary:
    """Parse one finite, non-executable diode/subcircuit model library."""
    active_deadline = _validate_deadline(deadline)
    _check_deadline(active_deadline)
    if type(content) is not bytes or not content or len(content) > _MAX_CONTENT_BYTES:
        _refuse()
    _validate_content_bytes(content, active_deadline)
    lines = _logical_lines(content, active_deadline)
    records: list[_DefinitionRecord] = []
    source_elements = 0
    index = 0
    while index < len(lines):
        _check_deadline(active_deadline)
        line = lines[index]
        directive = line.text.split(maxsplit=1)[0].lower()
        if directive == b".model":
            records.append(_parse_model(line, content, active_deadline))
            index += 1
        elif directive == b".subckt":
            record, index = _parse_subcircuit(
                lines, index, content, active_deadline, _MAX_ELEMENTS - source_elements
            )
            records.append(record)
            source_elements += len(record.elements)
        else:
            _refuse()
        if len(records) > _MAX_DEFINITIONS:
            _refuse()
        if source_elements > _MAX_ELEMENTS:
            _refuse()
    if not records:
        _refuse()
    frozen_records = tuple(records)
    _validate_records(frozen_records, active_deadline)
    definitions = tuple(record.definition for record in frozen_records)
    _check_deadline(active_deadline)
    return SpiceModelLibrary(content, definitions)
