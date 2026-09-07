"""Bounded extraction of private component metadata from a KiCad XML netlist.

Parsing establishes native syntax and source identity only.  It does not establish BOM,
placement, electrical, fabrication, model, execution, or apply authority.
"""

from __future__ import annotations

import math
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import NoReturn, cast
from xml.etree import ElementTree as ET

from copper_mcp.optimization.contracts import digest_document

_BACKEND_VERSION = "10.0.5"
_EXPECTED_TOOL = f"Eeschema {_BACKEND_VERSION}"
_XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>'
_MAX_NETLIST_BYTES = 64 * 1024 * 1024
_MAX_XML_DEPTH = 32
_MAX_XML_ELEMENTS = 250_000
_MAX_ELEMENT_ATTRIBUTES = 16
_MAX_VALUE_BYTES = 4_096
_MAX_TOTAL_VALUE_BYTES = 16 * 1024 * 1024
_MAX_SHEETS = 512
_MAX_COMPONENTS = 100_000
_MAX_COMPONENT_FIELDS = 256
_MAX_SYMBOL_UUIDS = 128
_PARSE_CHUNK_BYTES = 64 * 1024
_DEADLINE_NODE_INTERVAL = 1_024
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHEET_NUMBER = re.compile(r"[1-9][0-9]*")
_KNOWN_FLAGS = frozenset(
    {"exclude_from_bom", "exclude_from_board", "exclude_from_pos_files", "dnp"}
)
_MALFORMED = "KiCad component netlist is malformed"
_BOUNDS = "KiCad component netlist exceeds its bounds"
_DEADLINE = "KiCad component netlist deadline expired"


class ComponentNetlistError(ValueError):
    """A fixed, non-disclosing refusal from the native component boundary."""


def _fail(message: str = _MALFORMED) -> NoReturn:
    raise ComponentNetlistError(message)


@dataclass(frozen=True, slots=True, repr=False)
class NativeComponent:
    """One native nonvirtual component record; its representation is always redacted."""

    reference: str
    value: str
    footprint: str
    datasheet: str
    library_id: str
    sheet_path: str
    symbol_uuids: tuple[str, ...]
    excluded_from_bom: bool
    excluded_from_board: bool
    dnp: bool

    def __repr__(self) -> str:
        return "<NativeComponent redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class ComponentNetlist:
    """Private immutable native metadata without an engineering verdict or authority."""

    components: tuple[NativeComponent, ...]
    sheet_paths: tuple[str, ...]
    backend_version: str

    @property
    def digest(self) -> str:
        return digest_document(
            "copper-mcp/component-netlist/v1",
            {
                "backend_version": self.backend_version,
                "components": [
                    asdict(component)
                    for component in sorted(self.components, key=lambda item: item.reference)
                ],
                "sheet_paths": sorted(self.sheet_paths),
            },
        )

    def __repr__(self) -> str:
        return "<ComponentNetlist redacted>"


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail(_DEADLINE)


def _bounded_text(value: object, *, allow_empty: bool = True) -> str:
    if type(value) is not str or (not allow_empty and not value):
        _fail()
    text = value
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in text):
        _fail()
    if len(text.encode("utf-8")) > _MAX_VALUE_BYTES:
        _fail(_BOUNDS)
    return text


def _canonical_uuid(value: object) -> str:
    text = _bounded_text(value, allow_empty=False)
    if _UUID.fullmatch(text) is None:
        _fail()
    return text


def _canonical_sheet_path(value: object) -> str:
    path = _bounded_text(value, allow_empty=False)
    if path == "/":
        return path
    if not path.startswith("/") or not path.endswith("/"):
        _fail()
    segments = path[1:-1].split("/")
    if not segments or len(segments) > _MAX_XML_DEPTH:
        _fail(_BOUNDS)
    for segment in segments:
        _canonical_uuid(segment)
    return path


def _validate_inputs(
    payload: object,
    expected_source: object,
    expected_sheet_paths: object,
    deadline: object,
    max_bytes: object,
) -> tuple[bytes, str, tuple[str, ...], float, int]:
    if type(deadline) not in (int, float) or isinstance(deadline, bool):
        _fail()
    normalized_deadline: float | None = None
    try:
        normalized_deadline = float(cast("int | float", deadline))
    except OverflowError:
        normalized_deadline = None
    if normalized_deadline is None or not math.isfinite(normalized_deadline):
        _fail()
    if type(max_bytes) is not int or isinstance(max_bytes, bool):
        _fail()
    if not 1 <= max_bytes <= _MAX_NETLIST_BYTES:
        _fail(_BOUNDS)
    if type(payload) is not bytes or not payload or len(payload) > max_bytes:
        _fail(_BOUNDS)
    source = _bounded_text(expected_source, allow_empty=False)
    if type(expected_sheet_paths) is not tuple:
        _fail()
    if not 1 <= len(expected_sheet_paths) <= _MAX_SHEETS:
        _fail(_BOUNDS)
    sheets: list[str] = []
    for index, item in enumerate(expected_sheet_paths):
        if index % _DEADLINE_NODE_INTERVAL == 0:
            _check_deadline(normalized_deadline)
        sheets.append(_canonical_sheet_path(item))
    if "/" not in sheets or len(set(sheets)) != len(sheets):
        _fail()
    _check_deadline(normalized_deadline)
    return payload, source, tuple(sorted(sheets)), normalized_deadline, max_bytes


def _reject_xml_directives(payload: bytes, deadline: float) -> None:
    if not payload.startswith(_XML_DECLARATION):
        _fail()
    remainder = memoryview(payload)[len(_XML_DECLARATION) :]
    carry = b""
    for offset in range(0, len(remainder), _PARSE_CHUNK_BYTES):
        _check_deadline(deadline)
        chunk = bytes(remainder[offset : offset + _PARSE_CHUNK_BYTES])
        window = carry + chunk
        if b"<!" in window or b"<?" in window:
            _fail()
        carry = window[-1:]
    _check_deadline(deadline)


def _element_value_bytes(value: str) -> int:
    size = len(value.encode("utf-8"))
    if size > _MAX_VALUE_BYTES:
        _fail(_BOUNDS)
    return size


def _parse_xml(payload: bytes, deadline: float) -> ET.Element:
    parser: ET.XMLPullParser[ET.Element[str]] = ET.XMLPullParser(events=("start", "end"))
    root: ET.Element | None = None
    depth = 0
    nodes = 0
    total_value_bytes = 0
    malformed = False
    try:
        for offset in range(0, len(payload), _PARSE_CHUNK_BYTES):
            _check_deadline(deadline)
            parser.feed(payload[offset : offset + _PARSE_CHUNK_BYTES])
            for raw_event in parser.read_events():
                event, element = cast("tuple[str, ET.Element[str]]", raw_event)
                if event == "start":
                    depth += 1
                    nodes += 1
                    if root is None:
                        root = element
                    if depth > _MAX_XML_DEPTH or nodes > _MAX_XML_ELEMENTS:
                        _fail(_BOUNDS)
                    if len(element.attrib) > _MAX_ELEMENT_ATTRIBUTES:
                        _fail(_BOUNDS)
                    for key, value in element.attrib.items():
                        total_value_bytes += _element_value_bytes(key)
                        total_value_bytes += _element_value_bytes(value)
                else:
                    for text_value in (element.text, element.tail):
                        if text_value is not None:
                            total_value_bytes += _element_value_bytes(text_value)
                    if element.tail is not None and element.tail.strip():
                        _fail()
                    depth -= 1
                if nodes % _DEADLINE_NODE_INTERVAL == 0:
                    _check_deadline(deadline)
                if total_value_bytes > _MAX_TOTAL_VALUE_BYTES:
                    _fail(_BOUNDS)
        parser.close()
        for raw_event in parser.read_events():
            event, element = cast("tuple[str, ET.Element[str]]", raw_event)
            if event == "start":
                depth += 1
                nodes += 1
                if root is None:
                    root = element
            else:
                depth -= 1
            if depth > _MAX_XML_DEPTH or nodes > _MAX_XML_ELEMENTS:
                _fail(_BOUNDS)
    except (ET.ParseError, RecursionError):
        malformed = True
    if malformed or root is None or depth != 0:
        _fail()
    # An end event does not seal its tail: a later feed can append text without
    # emitting that event again. Keep early admission checks, then account for
    # the completed tree independently so no late value escapes the budget.
    total_value_bytes = 0
    for index, element in enumerate(root.iter()):
        if index % _DEADLINE_NODE_INTERVAL == 0:
            _check_deadline(deadline)
        for key, value in element.attrib.items():
            total_value_bytes += _element_value_bytes(key)
            total_value_bytes += _element_value_bytes(value)
        for text_value in (element.text, element.tail):
            if text_value is not None:
                total_value_bytes += _element_value_bytes(text_value)
        if element.tail is not None and element.tail.strip():
            _fail()
        if total_value_bytes > _MAX_TOTAL_VALUE_BYTES:
            _fail(_BOUNDS)
    _check_deadline(deadline)
    return root


def _direct(parent: ET.Element, tag: str) -> tuple[ET.Element, ...]:
    return tuple(child for child in parent if child.tag == tag)


def _one(parent: ET.Element, tag: str) -> ET.Element:
    matches = _direct(parent, tag)
    if len(matches) != 1:
        _fail()
    return matches[0]


def _leaf_text(element: ET.Element, *, attributes: frozenset[str] = frozenset()) -> str:
    if set(element.attrib) != attributes or len(element):
        _fail()
    return _bounded_text(element.text or "")


def _empty_leaf(element: ET.Element) -> None:
    if len(element) or (element.text is not None and element.text.strip()):
        _fail()


def _validate_design(
    root: ET.Element,
    expected_source: str,
    expected_sheet_paths: tuple[str, ...],
    deadline: float,
) -> tuple[str, ...]:
    design = _one(root, "design")
    if design.attrib:
        _fail()
    if _leaf_text(_one(design, "source")) != expected_source:
        _fail()
    if _leaf_text(_one(design, "tool")) != _EXPECTED_TOOL:
        _fail()

    sheets = _direct(design, "sheet")
    if not 1 <= len(sheets) <= _MAX_SHEETS:
        _fail(_BOUNDS)
    paths: list[str] = []
    numbers: set[str] = set()
    for index, sheet in enumerate(sheets):
        if index % _DEADLINE_NODE_INTERVAL == 0:
            _check_deadline(deadline)
        if set(sheet.attrib) != {"number", "name", "tstamps"}:
            _fail()
        number = _bounded_text(sheet.attrib["number"], allow_empty=False)
        _bounded_text(sheet.attrib["name"], allow_empty=False)
        path = _canonical_sheet_path(sheet.attrib["tstamps"])
        if _SHEET_NUMBER.fullmatch(number) is None or number in numbers or path in paths:
            _fail()
        numbers.add(number)
        paths.append(path)
    canonical = tuple(sorted(paths))
    if canonical != expected_sheet_paths:
        _fail()
    _check_deadline(deadline)
    return canonical


def _component_fields(component: ET.Element, deadline: float) -> tuple[str, str]:
    containers = _direct(component, "fields")
    if len(containers) > 1:
        _fail()
    if not containers:
        return "", ""
    container = containers[0]
    if container.attrib:
        _fail()
    fields = tuple(container)
    if len(fields) > _MAX_COMPONENT_FIELDS:
        _fail(_BOUNDS)
    selected: dict[str, str] = {}
    for index, field in enumerate(fields):
        if index % 64 == 0:
            _check_deadline(deadline)
        if field.tag != "field" or set(field.attrib) != {"name"}:
            _fail()
        name = _bounded_text(field.attrib["name"], allow_empty=False)
        value = _leaf_text(field, attributes=frozenset({"name"}))
        if name in {"Footprint", "Datasheet"}:
            if name in selected:
                _fail()
            selected[name] = value
    return selected.get("Footprint", ""), selected.get("Datasheet", "")


def _bound_optional_field(component: ET.Element, tag: str, named_value: str) -> str:
    direct = _direct(component, tag)
    if len(direct) > 1:
        _fail()
    if not direct:
        if named_value:
            _fail()
        return ""
    value = _leaf_text(direct[0])
    if value != named_value:
        _fail()
    return value


def _component_flags(component: ET.Element, deadline: float) -> tuple[bool, bool, bool]:
    properties = _direct(component, "property")
    if len(properties) > _MAX_COMPONENT_FIELDS:
        _fail(_BOUNDS)
    flags: set[str] = set()
    for index, prop in enumerate(properties):
        if index % 64 == 0:
            _check_deadline(deadline)
        keys = set(prop.attrib)
        if keys not in ({"name"}, {"name", "value"}):
            _fail()
        _empty_leaf(prop)
        name = _bounded_text(prop.attrib["name"], allow_empty=False)
        if "value" in prop.attrib:
            _bounded_text(prop.attrib["value"])
            continue
        if name not in _KNOWN_FLAGS or name in flags:
            _fail()
        flags.add(name)
    return (
        "exclude_from_bom" in flags,
        "exclude_from_board" in flags,
        "dnp" in flags,
    )


def _library_id(component: ET.Element) -> str:
    source = _one(component, "libsource")
    keys = set(source.attrib)
    if not {"lib", "part"}.issubset(keys) or not keys.issubset({"lib", "part", "description"}):
        _fail()
    _empty_leaf(source)
    library = _bounded_text(source.attrib["lib"], allow_empty=False)
    part = _bounded_text(source.attrib["part"], allow_empty=False)
    if ":" in library or ":" in part:
        _fail()
    if "description" in source.attrib:
        _bounded_text(source.attrib["description"])
    return _bounded_text(f"{library}:{part}", allow_empty=False)


def _component_sheet_path(component: ET.Element, expected_sheet_paths: frozenset[str]) -> str:
    sheet = _one(component, "sheetpath")
    if set(sheet.attrib) != {"names", "tstamps"}:
        _fail()
    _empty_leaf(sheet)
    _bounded_text(sheet.attrib["names"], allow_empty=False)
    path = _canonical_sheet_path(sheet.attrib["tstamps"])
    if path not in expected_sheet_paths:
        _fail()
    return path


def _symbol_uuids(component: ET.Element) -> tuple[str, ...]:
    node = _one(component, "tstamps")
    text = _leaf_text(node)
    values = text.split(" ")
    if not 1 <= len(values) <= _MAX_SYMBOL_UUIDS or any(not value for value in values):
        _fail()
    uuids = tuple(_canonical_uuid(value) for value in values)
    if len(set(uuids)) != len(uuids):
        _fail()
    return tuple(sorted(uuids))


def _reference(component: ET.Element) -> str:
    if set(component.attrib) != {"ref"}:
        _fail()
    reference = _bounded_text(component.attrib["ref"], allow_empty=False)
    if reference.startswith("#") or any(character.isspace() for character in reference):
        _fail()
    return reference


def _validate_components(
    root: ET.Element, expected_sheet_paths: tuple[str, ...], deadline: float
) -> tuple[NativeComponent, ...]:
    container = _one(root, "components")
    if container.attrib or any(child.tag != "comp" for child in container):
        _fail()
    exported = tuple(container)
    if len(exported) > _MAX_COMPONENTS:
        _fail(_BOUNDS)
    references: set[str] = set()
    symbol_identities: set[tuple[str, str]] = set()
    expected_sheet_set = frozenset(expected_sheet_paths)
    components: list[NativeComponent] = []
    for component in exported:
        _check_deadline(deadline)
        reference = _reference(component)
        if reference in references:
            _fail()
        references.add(reference)
        value = _leaf_text(_one(component, "value"))
        named_footprint, named_datasheet = _component_fields(component, deadline)
        footprint = _bound_optional_field(component, "footprint", named_footprint)
        datasheet = _bound_optional_field(component, "datasheet", named_datasheet)
        symbol_uuids = _symbol_uuids(component)
        sheet_path = _component_sheet_path(component, expected_sheet_set)
        identities = {(sheet_path, symbol_uuid) for symbol_uuid in symbol_uuids}
        if symbol_identities.intersection(identities):
            _fail()
        symbol_identities.update(identities)
        excluded_from_bom, excluded_from_board, dnp = _component_flags(component, deadline)
        components.append(
            NativeComponent(
                reference=reference,
                value=value,
                footprint=footprint,
                datasheet=datasheet,
                library_id=_library_id(component),
                sheet_path=sheet_path,
                symbol_uuids=symbol_uuids,
                excluded_from_bom=excluded_from_bom,
                excluded_from_board=excluded_from_board,
                dnp=dnp,
            )
        )
    _check_deadline(deadline)
    return tuple(sorted(components, key=lambda item: item.reference))


def parse_component_netlist(
    payload: bytes,
    *,
    expected_source: str,
    expected_sheet_paths: tuple[str, ...],
    deadline: float,
    max_bytes: int,
) -> ComponentNetlist:
    """Parse one bounded KiCad 10.0.5 format-E component inventory.

    The result is private native metadata.  Empty components are valid and no returned value
    represents a BOM, placement, engineering, model-validation, execution, or apply verdict.
    """

    data, source, expected_sheets, active_deadline, _ = _validate_inputs(
        payload, expected_source, expected_sheet_paths, deadline, max_bytes
    )
    _reject_xml_directives(data, active_deadline)
    root = _parse_xml(data, active_deadline)
    if root.tag != "export" or root.attrib != {"version": "E"}:
        _fail()
    if len(_direct(root, "design")) != 1 or len(_direct(root, "components")) != 1:
        _fail()
    sheets = _validate_design(root, source, expected_sheets, active_deadline)
    components = _validate_components(root, sheets, active_deadline)
    _check_deadline(active_deadline)
    result = ComponentNetlist(
        components=components,
        sheet_paths=sheets,
        backend_version=_BACKEND_VERSION,
    )
    _check_deadline(active_deadline)
    return result


__all__ = [
    "ComponentNetlist",
    "ComponentNetlistError",
    "NativeComponent",
    "parse_component_netlist",
]
