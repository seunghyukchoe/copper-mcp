"""Bounded captured-source interpreter for the private placed-pin census.

This module owns source syntax, library/raw/instance/template records, and cumulative parsing
budgets.  It does not import or invoke the census assembler.
"""

from __future__ import annotations

import re
import time
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import NoReturn

from copper_mcp.adapters.sexpr import SExpr, SExprError, is_quoted_atom, parse_sexpr
from copper_mcp.board_ir import ParseLimits
from copper_mcp.engineering.capture import CaptureLimits

_MAX_SYMBOL_OCCURRENCES = 100_000
_MAX_PIN_OCCURRENCES = 250_000
_MAX_SAVED_PIN_RECORDS = 250_000
_MAX_LIBRARY_PINS_PER_BODY = 100_000
_MAX_FIELDS = 1_000_000
_MAX_AGGREGATE_WORK = 2_000_000
_MAX_TEXT_BYTES = 4_096
_MAX_SELECTOR = (1 << 31) - 1
_MAX_UUID_PATH_DEPTH = 16
_NATIVE_EMPTY_NAME_VERSION = 20250318
_INTEGER = re.compile(r"0|[1-9][0-9]*")
_ELECTRICAL_TYPES = frozenset(
    {
        "input",
        "output",
        "bidirectional",
        "tri_state",
        "passive",
        "unspecified",
        "power_in",
        "power_out",
        "open_collector",
        "open_emitter",
        "unconnected",
        "no_connect",
        "free",
    }
)
_PIN_SHAPES = frozenset(
    {
        "line",
        "inverted",
        "clock",
        "inverted_clock",
        "input_low",
        "clock_low",
        "output_low",
        "edge_clock_high",
        "non_logic",
    }
)


class ProjectPinCensusError(ValueError):
    """A fixed, context-free refusal that never includes private source values."""


def _fail(message: str) -> NoReturn:
    raise ProjectPinCensusError(message)


@dataclass(slots=True)
class _Budget:
    work: int = 0
    fields: int = 0
    symbol_occurrences: int = 0
    pin_occurrences: int = 0
    saved_pin_records: int = 0

    def step(self, deadline: float, count: int = 1) -> None:
        _check_deadline(deadline)
        self.work += count
        if self.work > _MAX_AGGREGATE_WORK:
            _fail("project pin census aggregate work budget exceeded")

    def field(self, deadline: float) -> None:
        self.step(deadline)
        self.fields += 1
        if self.fields > _MAX_FIELDS:
            _fail("project pin census field budget exceeded")

    def symbol(self, deadline: float) -> None:
        self.step(deadline)
        self.symbol_occurrences += 1
        if self.symbol_occurrences > _MAX_SYMBOL_OCCURRENCES:
            _fail("project pin census symbol occurrence budget exceeded")

    def pin(self, deadline: float) -> None:
        self.step(deadline)
        self.pin_occurrences += 1
        if self.pin_occurrences > _MAX_PIN_OCCURRENCES:
            _fail("project pin census pin occurrence budget exceeded")

    def saved_pin(self, deadline: float) -> None:
        self.step(deadline)
        self.saved_pin_records += 1
        if self.saved_pin_records > _MAX_SAVED_PIN_RECORDS:
            _fail("project pin census saved pin record budget exceeded")


@dataclass(frozen=True, slots=True)
class _Alternate:
    name: str
    electrical_type: str


@dataclass(frozen=True, slots=True)
class _LibraryPin:
    unit: int
    body_style: int
    number: str
    raw_name: str
    declared_name: str
    electrical_type: str
    hidden: bool
    alternates: tuple[_Alternate, ...]


@dataclass(frozen=True, slots=True)
class _LibraryBody:
    pins: tuple[_LibraryPin, ...]
    units: frozenset[int]
    body_styles: frozenset[int]
    unit_body_styles: frozenset[tuple[int, int]]


@dataclass(frozen=True, slots=True)
class _RawPin:
    number: str
    source_uuid: str
    selected_alternate: str | None


@dataclass(frozen=True, slots=True)
class _Instance:
    reference: str
    unit: int


@dataclass(frozen=True, slots=True)
class _PlacedTemplate:
    source_symbol_uuid: str
    library_id: str
    body_style: int
    in_bom: bool
    on_board: bool
    dnp: bool
    raw_pins: tuple[_RawPin, ...]
    instances: Mapping[str, _Instance]
    body: _LibraryBody


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail("project pin census deadline expired")


def _bounded_text(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        _fail("project pin census source is malformed")
    text = value
    if len(text.encode("utf-8")) > _MAX_TEXT_BYTES or any(
        unicodedata.category(character) in {"Cc", "Cs"} for character in text
    ):
        _fail("project pin census source is malformed")
    return text


def _canonical_uuid(value: object) -> str:
    text = _bounded_text(value)
    parsed: uuid.UUID | None = None
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, ValueError):
        pass
    if parsed is None or str(parsed) != text:
        _fail("project pin census UUID is malformed")
    return text


def _canonical_sheet_path(value: object) -> str:
    path = _bounded_text(value)
    if not path.startswith("/") or path.endswith("/"):
        _fail("project pin census instance path is malformed")
    segments = path[1:].split("/")
    if not 1 <= len(segments) <= _MAX_UUID_PATH_DEPTH:
        _fail("project pin census instance path is malformed")
    for segment in segments:
        _canonical_uuid(segment)
    return path


def _strict_integer(value: object, *, allow_zero: bool) -> int:
    if type(value) is not str or is_quoted_atom(value) or _INTEGER.fullmatch(value) is None:
        _fail("project pin census integer is malformed")
    parsed = int(value)
    minimum = 0 if allow_zero else 1
    if not minimum <= parsed <= _MAX_SELECTOR:
        _fail("project pin census integer is malformed")
    return parsed


def _direct(expression: SExpr, head: str, budget: _Budget, deadline: float) -> tuple[SExpr, ...]:
    matches: list[SExpr] = []
    for item in expression.items[1:]:
        budget.field(deadline)
        if isinstance(item, SExpr) and item.head == head:
            matches.append(item)
    return tuple(matches)


def _single_atom(
    expression: SExpr,
    budget: _Budget,
    deadline: float,
    *,
    allow_empty: bool = False,
) -> str:
    budget.field(deadline)
    if len(expression.items) != 2 or not isinstance(expression.items[1], str):
        _fail("project pin census source is malformed")
    return _bounded_text(expression.items[1], allow_empty=allow_empty)


def _one(
    expression: SExpr,
    head: str,
    budget: _Budget,
    deadline: float,
) -> SExpr:
    matches = _direct(expression, head, budget, deadline)
    if len(matches) != 1:
        _fail("project pin census source fields are ambiguous")
    return matches[0]


def _optional_bool(
    expression: SExpr,
    head: str,
    default: bool,
    budget: _Budget,
    deadline: float,
) -> bool:
    matches = _direct(expression, head, budget, deadline)
    if not matches:
        return default
    if len(matches) != 1 or len(matches[0].items) != 2:
        _fail("project pin census boolean is malformed")
    value = matches[0].items[1]
    budget.field(deadline)
    if not isinstance(value, str) or is_quoted_atom(value) or value not in {"yes", "no"}:
        _fail("project pin census boolean is malformed")
    return value == "yes"


def _source_version(root: SExpr, budget: _Budget, deadline: float) -> int:
    value = _single_atom(_one(root, "version", budget, deadline), budget, deadline)
    if len(value) != 8 or not value.isascii() or not value.isdecimal():
        _fail("project pin census source version is malformed")
    return int(value)


def _parse_expression(payload: bytes, limits: CaptureLimits, deadline: float) -> SExpr:
    parsed: SExpr | None = None
    try:
        parsed = parse_sexpr(
            payload,
            replace(ParseLimits(), max_input_bytes=limits.max_file_bytes),
            check_deadline=lambda: _check_deadline(deadline),
        )
    except SExprError:
        pass
    if parsed is None:
        _fail("project pin census source syntax is unsupported")
    return parsed


def _pin_text_field(
    pin: SExpr,
    head: str,
    budget: _Budget,
    deadline: float,
    *,
    allow_empty: bool,
) -> str:
    field = _one(pin, head, budget, deadline)
    if len(field.items) not in {2, 3} or not isinstance(field.items[1], str):
        _fail("project pin census library pin is malformed")
    value = _bounded_text(field.items[1], allow_empty=allow_empty)
    if len(field.items) == 3:
        effects = field.items[2]
        if not isinstance(effects, SExpr) or effects.head != "effects":
            _fail("project pin census library pin is malformed")
    return value


def _parse_library_pin(
    expression: SExpr,
    unit: int,
    body_style: int,
    source_version: int,
    budget: _Budget,
    deadline: float,
) -> _LibraryPin:
    budget.step(deadline)
    if (
        len(expression.items) < 3
        or not isinstance(expression.items[1], str)
        or is_quoted_atom(expression.items[1])
        or expression.items[1] not in _ELECTRICAL_TYPES
        or not isinstance(expression.items[2], str)
        or is_quoted_atom(expression.items[2])
        or expression.items[2] not in _PIN_SHAPES
    ):
        _fail("project pin census library pin is malformed")
    electrical_type = expression.items[1]
    raw_name = _pin_text_field(expression, "name", budget, deadline, allow_empty=True)
    raw_number = _pin_text_field(expression, "number", budget, deadline, allow_empty=False)
    declared_name = (
        "" if source_version < _NATIVE_EMPTY_NAME_VERSION and raw_name == "~" else raw_name
    )
    number = "" if source_version < _NATIVE_EMPTY_NAME_VERSION and raw_number == "~" else raw_number
    if not number:
        _fail("project pin census pin number is malformed")

    hidden = False
    hidden_seen = False
    alternates: list[_Alternate] = []
    alternate_names: set[str] = set()
    seen_singletons: set[str] = set()
    for tail in expression.items[3:]:
        budget.field(deadline)
        if isinstance(tail, str):
            if is_quoted_atom(tail) or tail != "hide" or hidden_seen:
                _fail("project pin census library pin is malformed")
            hidden = True
            hidden_seen = True
            continue
        if tail.head in {"at", "length", "name", "number"}:
            if tail.head in seen_singletons:
                _fail("project pin census library pin fields are ambiguous")
            seen_singletons.add(tail.head)
            continue
        if tail.head == "hide":
            if hidden_seen or len(tail.items) != 2:
                _fail("project pin census library pin fields are ambiguous")
            value = tail.items[1]
            if not isinstance(value, str) or is_quoted_atom(value) or value not in {"yes", "no"}:
                _fail("project pin census library pin is malformed")
            hidden = value == "yes"
            hidden_seen = True
            continue
        if tail.head == "alternate":
            if (
                len(tail.items) != 4
                or not isinstance(tail.items[1], str)
                or not isinstance(tail.items[2], str)
                or is_quoted_atom(tail.items[2])
                or tail.items[2] not in _ELECTRICAL_TYPES
                or not isinstance(tail.items[3], str)
                or is_quoted_atom(tail.items[3])
                or tail.items[3] not in _PIN_SHAPES
            ):
                _fail("project pin census alternate pin is malformed")
            name = _bounded_text(tail.items[1])
            if name in alternate_names:
                _fail("project pin census alternate pins are ambiguous")
            alternate_names.add(name)
            alternates.append(_Alternate(name, tail.items[2]))
            continue
        _fail("project pin census library pin profile is unsupported")

    if not {"name", "number"} <= seen_singletons:
        _fail("project pin census library pin is malformed")
    return _LibraryPin(
        unit,
        body_style,
        number,
        raw_name,
        declared_name,
        electrical_type,
        hidden,
        tuple(sorted(alternates, key=lambda item: item.name)),
    )


def _nested_selectors(name: str, base_name: str) -> tuple[int, int]:
    prefix = base_name + "_"
    if not name.startswith(prefix):
        _fail("project pin census library body selector is malformed")
    parts = name[len(prefix) :].split("_")
    if len(parts) != 2:
        _fail("project pin census library body selector is malformed")
    return (
        _strict_integer(parts[0], allow_zero=True),
        _strict_integer(parts[1], allow_zero=True),
    )


def _parse_library_body(
    expression: SExpr,
    library_id: str,
    source_version: int,
    budget: _Budget,
    deadline: float,
) -> _LibraryBody:
    base_name = library_id.split(":", 1)[1]
    pins: list[_LibraryPin] = []
    units = {1}
    body_styles = {1}
    selectors: set[tuple[int, int]] = set()
    has_specific_unit = False
    nested_names: set[str] = set()

    for item in expression.items[2:]:
        budget.field(deadline)
        if not isinstance(item, SExpr):
            _fail("project pin census library body is malformed")
        if item.head == "pin":
            if len(pins) >= _MAX_LIBRARY_PINS_PER_BODY:
                _fail("project pin census library pin traversal budget exceeded")
            pins.append(_parse_library_pin(item, 1, 1, source_version, budget, deadline))
            selectors.add((1, 1))
            has_specific_unit = True
        elif item.head == "symbol":
            if len(item.items) < 2 or not isinstance(item.items[1], str):
                _fail("project pin census library body selector is malformed")
            nested_name = _bounded_text(item.items[1])
            if nested_name in nested_names:
                _fail("project pin census library body selectors are ambiguous")
            nested_names.add(nested_name)
            unit, body_style = _nested_selectors(nested_name, base_name)
            units.add(unit)
            body_styles.add(body_style)
            selectors.add((unit, body_style))
            has_specific_unit = has_specific_unit or unit > 0
            for nested_item in item.items[2:]:
                budget.field(deadline)
                if not isinstance(nested_item, SExpr):
                    _fail("project pin census library body is malformed")
                if nested_item.head == "pin":
                    if len(pins) >= _MAX_LIBRARY_PINS_PER_BODY:
                        _fail("project pin census library pin traversal budget exceeded")
                    pins.append(
                        _parse_library_pin(
                            nested_item,
                            unit,
                            body_style,
                            source_version,
                            budget,
                            deadline,
                        )
                    )
                elif nested_item.head == "symbol":
                    _fail("project pin census nested library body is unsupported")

    if not has_specific_unit:
        # A common-only symbol has the implicit first unit. Common pins must not
        # invent a missing combination once the library declares specific units.
        common_selectors = set()
        for _, style in selectors:
            budget.step(deadline)
            common_selectors.add((1, style))
        selectors = common_selectors
    return _LibraryBody(tuple(pins), frozenset(units), frozenset(body_styles), frozenset(selectors))


def _parse_raw_pin(
    expression: SExpr,
    source_version: int,
    budget: _Budget,
    deadline: float,
) -> _RawPin:
    budget.saved_pin(deadline)
    if len(expression.items) < 2 or not isinstance(expression.items[1], str):
        _fail("project pin census placed pin is malformed")
    raw_number = _bounded_text(expression.items[1])
    number = "" if source_version < _NATIVE_EMPTY_NAME_VERSION and raw_number == "~" else raw_number
    if not number:
        _fail("project pin census pin number is malformed")
    uuid_fields: list[SExpr] = []
    alternate_fields: list[SExpr] = []
    for item in expression.items[2:]:
        budget.field(deadline)
        if not isinstance(item, SExpr):
            _fail("project pin census placed pin is malformed")
        if item.head == "uuid":
            uuid_fields.append(item)
        elif item.head == "alternate":
            alternate_fields.append(item)
        else:
            _fail("project pin census placed pin profile is unsupported")
    if len(uuid_fields) != 1 or len(alternate_fields) > 1:
        _fail("project pin census placed pin fields are ambiguous")
    source_uuid = _canonical_uuid(_single_atom(uuid_fields[0], budget, deadline))
    alternate = _single_atom(alternate_fields[0], budget, deadline) if alternate_fields else None
    return _RawPin(number, source_uuid, alternate)


def _parse_instances(symbol: SExpr, budget: _Budget, deadline: float) -> Mapping[str, _Instance]:
    instances_node = _one(symbol, "instances", budget, deadline)
    instances: dict[str, _Instance] = {}
    for project in instances_node.items[1:]:
        budget.field(deadline)
        if (
            not isinstance(project, SExpr)
            or project.head != "project"
            or len(project.items) < 2
            or not isinstance(project.items[1], str)
        ):
            _fail("project pin census instance records are malformed")
        _bounded_text(project.items[1])
        for path_node in project.items[2:]:
            budget.field(deadline)
            if (
                not isinstance(path_node, SExpr)
                or path_node.head != "path"
                or len(path_node.items) < 2
                or not isinstance(path_node.items[1], str)
            ):
                _fail("project pin census instance records are malformed")
            path = _canonical_sheet_path(path_node.items[1])
            references: list[str] = []
            units: list[int] = []
            for field in path_node.items[2:]:
                budget.field(deadline)
                if not isinstance(field, SExpr):
                    _fail("project pin census instance record is malformed")
                if field.head == "reference":
                    references.append(_single_atom(field, budget, deadline))
                elif field.head == "unit":
                    units.append(
                        _strict_integer(_single_atom(field, budget, deadline), allow_zero=False)
                    )
                elif field.head in {"value", "footprint"}:
                    _single_atom(field, budget, deadline, allow_empty=True)
                elif field.head == "variant":
                    _fail("project pin census instance variants are unsupported")
                else:
                    _fail("project pin census instance profile is unsupported")
            if len(references) != 1 or len(units) != 1 or path in instances:
                _fail("project pin census instance paths are missing or ambiguous")
            instances[path] = _Instance(references[0], units[0])
    if not instances:
        _fail("project pin census instance paths are missing or ambiguous")
    # The backing dictionary is local and never exposed to consumers.
    # https://docs.python.org/3.11/library/types.html#types.MappingProxyType
    return MappingProxyType(instances)


def _body_style(symbol: SExpr, budget: _Budget, deadline: float) -> int:
    modern = _direct(symbol, "body_style", budget, deadline)
    legacy = _direct(symbol, "convert", budget, deadline)
    if len(modern) > 1 or len(legacy) > 1 or (modern and legacy):
        _fail("project pin census body style is ambiguous")
    if not modern and not legacy:
        return 1
    return _strict_integer(_single_atom((modern or legacy)[0], budget, deadline), allow_zero=False)


def _parse_template(
    symbol: SExpr,
    caches: dict[str, SExpr],
    bodies: dict[str, _LibraryBody],
    source_version: int,
    budget: _Budget,
    deadline: float,
) -> _PlacedTemplate:
    library_id = _single_atom(_one(symbol, "lib_id", budget, deadline), budget, deadline)
    source_symbol_uuid = _canonical_uuid(
        _single_atom(_one(symbol, "uuid", budget, deadline), budget, deadline)
    )
    body_style = _body_style(symbol, budget, deadline)

    global_units = _direct(symbol, "unit", budget, deadline)
    if len(global_units) > 1:
        _fail("project pin census global unit is ambiguous")
    if global_units:
        _strict_integer(_single_atom(global_units[0], budget, deadline), allow_zero=False)

    raw_pins = tuple(
        _parse_raw_pin(item, source_version, budget, deadline)
        for item in _direct(symbol, "pin", budget, deadline)
    )
    if len({item.number for item in raw_pins}) != len(raw_pins) or len(
        {item.source_uuid for item in raw_pins}
    ) != len(raw_pins):
        _fail("project pin census placed pins are ambiguous")

    if library_id not in bodies:
        bodies[library_id] = _parse_library_body(
            caches[library_id], library_id, source_version, budget, deadline
        )
    body = bodies[library_id]
    if body_style not in body.body_styles:
        _fail("project pin census body style is unknown")
    applicable_list: list[_LibraryPin] = []
    for pin in body.pins:
        budget.step(deadline)
        if pin.body_style in {0, body_style}:
            applicable_list.append(pin)
    applicable = tuple(applicable_list)
    if len({pin.number for pin in applicable}) != len(applicable):
        _fail("project pin census library pin numbers are ambiguous")
    if {pin.number for pin in applicable} != {pin.number for pin in raw_pins}:
        _fail("project pin census placed pin inventory does not match its library body")
    library_by_number = {pin.number: pin for pin in applicable}
    for raw_pin in raw_pins:
        budget.step(deadline)
        if raw_pin.selected_alternate is not None:
            resolved = False
            for alternate in library_by_number[raw_pin.number].alternates:
                budget.step(deadline)
                if alternate.name == raw_pin.selected_alternate:
                    resolved = True
                    break
            if not resolved:
                _fail("project pin census selected alternate is unresolved")

    return _PlacedTemplate(
        source_symbol_uuid,
        library_id,
        body_style,
        _optional_bool(symbol, "in_bom", True, budget, deadline),
        _optional_bool(symbol, "on_board", True, budget, deadline),
        _optional_bool(symbol, "dnp", False, budget, deadline),
        raw_pins,
        _parse_instances(symbol, budget, deadline),
        body,
    )


def _parse_source(
    payload: bytes, limits: CaptureLimits, budget: _Budget, deadline: float
) -> tuple[_PlacedTemplate, ...]:
    root = _parse_expression(payload, limits, deadline)
    if root.head != "kicad_sch":
        _fail("project pin census source header is malformed")
    source_version = _source_version(root, budget, deadline)
    library_nodes = _direct(root, "lib_symbols", budget, deadline)
    if len(library_nodes) != 1:
        _fail("project pin census symbol cache is missing or ambiguous")
    caches: dict[str, SExpr] = {}
    for symbol in _direct(library_nodes[0], "symbol", budget, deadline):
        if len(symbol.items) < 2 or not isinstance(symbol.items[1], str):
            _fail("project pin census cached symbol identity is malformed")
        name = _bounded_text(symbol.items[1])
        if name in caches:
            _fail("project pin census cached symbol identities are ambiguous")
        caches[name] = symbol

    bodies: dict[str, _LibraryBody] = {}
    symbols = tuple(
        _parse_template(item, caches, bodies, source_version, budget, deadline)
        for item in _direct(root, "symbol", budget, deadline)
    )
    if len({item.source_symbol_uuid for item in symbols}) != len(symbols):
        _fail("project pin census symbol UUIDs are ambiguous")
    return symbols
