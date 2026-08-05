"""Bounded connectivity-parity verification for Circuit Intent KiCad exports.

The verifier consumes, but never invokes, KiCad's documented ``kicadxml`` netlist
export.  It accepts only the deterministic CopperMCP v0.1 schematic subset and
requires the supplied schematic bytes to be an exact renderer replay before it
examines the exported connectivity.

Format sources:
- https://docs.kicad.org/10.0/en/cli/cli.html#schematic-export-netlist
- https://docs.kicad.org/8.0/en/eeschema/eeschema.html#the-nets-section
- https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NoReturn
from xml.etree import ElementTree as ET

from copper_mcp.circuit_ir import CircuitIntentSnapshot, ComponentKind, verify_snapshot

from .kicad_schematic import MAX_RENDERED_SCHEMATIC_BYTES, render_kicad_schematic

MAX_KICAD_XML_NETLIST_BYTES = 512_000
MAX_KICAD_XML_ELEMENTS = 8_192
MAX_KICAD_XML_DEPTH = 32
MAX_KICAD_XML_TEXT_BYTES = 256_000

_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_TOOL = re.compile(r"^Eeschema [0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9._-]+)?$")
_XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>'


class KiCadSchematicParityErrorCode(StrEnum):
    """Stable, non-disclosing failure classes for parity verification."""

    INPUT_INVALID = "input.invalid"
    BUDGET_EXCEEDED = "budget.exceeded"
    SOURCE_MISMATCH = "source.mismatch"
    XML_INVALID = "xml.invalid"
    XML_UNSUPPORTED = "xml.unsupported"
    COMPONENT_MISMATCH = "component.mismatch"
    CONNECTIVITY_MISMATCH = "connectivity.mismatch"


class KiCadSchematicParityError(ValueError):
    """Raised when source replay or exported connectivity cannot be proved."""

    def __init__(self, code: KiCadSchematicParityErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: KiCadSchematicParityErrorCode, message: str) -> NoReturn:
    raise KiCadSchematicParityError(code, message)


@dataclass(frozen=True, slots=True)
class KiCadSchematicParityLimits:
    """Caller-tightenable XML budgets for one untrusted KiCad netlist export."""

    max_netlist_bytes: int = MAX_KICAD_XML_NETLIST_BYTES
    max_elements: int = MAX_KICAD_XML_ELEMENTS
    max_depth: int = MAX_KICAD_XML_DEPTH
    max_text_bytes: int = MAX_KICAD_XML_TEXT_BYTES

    def __post_init__(self) -> None:
        ceilings = (
            ("max_netlist_bytes", self.max_netlist_bytes, MAX_KICAD_XML_NETLIST_BYTES),
            ("max_elements", self.max_elements, MAX_KICAD_XML_ELEMENTS),
            ("max_depth", self.max_depth, MAX_KICAD_XML_DEPTH),
            ("max_text_bytes", self.max_text_bytes, MAX_KICAD_XML_TEXT_BYTES),
        )
        for name, value, ceiling in ceilings:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("schematic parity limits must be positive integers")
            if value > ceiling:
                raise ValueError(f"{name} cannot exceed the schematic parity ceiling")


@dataclass(frozen=True, slots=True)
class KiCadSchematicParityEvidence:
    """Redacted evidence returned only after every parity gate passes."""

    intent_digest: str
    schematic_digest: str
    netlist_digest: str
    netlist_format_version: str
    component_count: int
    net_count: int
    connection_count: int
    source_replay: Literal["passed"]
    component_parity: Literal["passed"]
    connectivity_parity: Literal["passed"]

    def __post_init__(self) -> None:
        for digest in (self.intent_digest, self.schematic_digest, self.netlist_digest):
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise ValueError("schematic parity evidence digest is malformed")
        if self.netlist_format_version != "E":
            raise ValueError("schematic parity netlist format is unsupported")
        counts = (self.component_count, self.net_count, self.connection_count)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in counts
        ):
            raise ValueError("schematic parity evidence counts are malformed")
        if (
            self.source_replay != "passed"
            or self.component_parity != "passed"
            or self.connectivity_parity != "passed"
        ):
            raise ValueError("schematic parity evidence cannot represent a failed gate")


_ALLOWED_CHILDREN: dict[str, frozenset[str]] = {
    "export": frozenset({"design", "components", "libparts", "libraries", "nets"}),
    "design": frozenset({"source", "date", "tool", "sheet"}),
    "sheet": frozenset({"title_block"}),
    "title_block": frozenset({"title", "company", "rev", "date", "source", "comment"}),
    "components": frozenset({"comp"}),
    "comp": frozenset(
        {
            "value",
            "description",
            "fields",
            "libsource",
            "property",
            "sheetpath",
            "tstamps",
            "units",
        }
    ),
    "fields": frozenset({"field"}),
    "units": frozenset({"unit"}),
    "unit": frozenset({"pins"}),
    "pins": frozenset({"pin"}),
    "libparts": frozenset({"libpart"}),
    "libpart": frozenset({"description", "fields", "pins"}),
    "libraries": frozenset(),
    "nets": frozenset({"net"}),
    "net": frozenset({"node"}),
}

_LEAF_TAGS = frozenset(
    {
        "source",
        "date",
        "tool",
        "title",
        "company",
        "rev",
        "comment",
        "value",
        "description",
        "field",
        "libsource",
        "property",
        "sheetpath",
        "tstamps",
        "pin",
        "node",
    }
)

_ALLOWED_ATTRIBUTES: dict[str, frozenset[str]] = {
    "export": frozenset({"version"}),
    "sheet": frozenset({"number", "name", "tstamps"}),
    "comment": frozenset({"number", "value"}),
    "comp": frozenset({"ref"}),
    "field": frozenset({"name"}),
    "libsource": frozenset({"lib", "part", "description"}),
    "property": frozenset({"name", "value"}),
    "sheetpath": frozenset({"names", "tstamps"}),
    "unit": frozenset({"name"}),
    "pin": frozenset({"num", "name", "type"}),
    "libpart": frozenset({"lib", "part"}),
    "net": frozenset({"code", "name", "class"}),
    "node": frozenset({"ref", "pin", "pintype"}),
}


def _parse_netlist(payload: bytes, limits: KiCadSchematicParityLimits) -> ET.Element:
    if type(payload) is not bytes or not payload:  # exact bytes avoid mutable parse inputs
        _fail(KiCadSchematicParityErrorCode.INPUT_INVALID, "KiCad XML netlist must be bytes")
    if len(payload) > limits.max_netlist_bytes:
        _fail(
            KiCadSchematicParityErrorCode.BUDGET_EXCEEDED,
            "KiCad XML netlist exceeds the byte budget",
        )
    if not payload.startswith(_XML_DECLARATION):
        _fail(
            KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
            "KiCad XML declaration is unsupported",
        )
    remainder = payload[len(_XML_DECLARATION) :]
    if b"<!" in payload or b"<?" in remainder:
        _fail(
            KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
            "KiCad XML declarations and directives are unsupported",
        )
    try:
        root = ET.fromstring(payload)  # noqa: S314 - DTD/entities are rejected before parsing.
    except (ET.ParseError, ValueError) as error:
        raise KiCadSchematicParityError(
            KiCadSchematicParityErrorCode.XML_INVALID,
            "KiCad XML netlist is malformed",
        ) from error

    element_count = 0
    text_bytes = 0
    stack: list[tuple[ET.Element, int]] = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        element_count += 1
        if element_count > limits.max_elements or depth > limits.max_depth:
            _fail(
                KiCadSchematicParityErrorCode.BUDGET_EXCEEDED,
                "KiCad XML netlist exceeds its structural budget",
            )
        if not isinstance(element.tag, str) or element.tag not in (
            _ALLOWED_CHILDREN.keys() | _LEAF_TAGS
        ):
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "KiCad XML netlist contains an unsupported element",
            )
        allowed_attributes = _ALLOWED_ATTRIBUTES.get(element.tag, frozenset())
        if not set(element.attrib).issubset(allowed_attributes):
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "KiCad XML netlist contains an unsupported attribute",
            )
        for key, value in element.attrib.items():
            encoded_size = len(key.encode("utf-8")) + len(value.encode("utf-8"))
            if encoded_size > 4_096:
                _fail(
                    KiCadSchematicParityErrorCode.BUDGET_EXCEEDED,
                    "KiCad XML netlist text exceeds its value budget",
                )
            text_bytes += encoded_size
        for text_value in (element.text, element.tail):
            if text_value is not None:
                encoded_size = len(text_value.encode("utf-8"))
                if encoded_size > 4_096:
                    _fail(
                        KiCadSchematicParityErrorCode.BUDGET_EXCEEDED,
                        "KiCad XML netlist text exceeds its value budget",
                    )
                text_bytes += encoded_size
        if text_bytes > limits.max_text_bytes:
            _fail(
                KiCadSchematicParityErrorCode.BUDGET_EXCEEDED,
                "KiCad XML netlist exceeds its text budget",
            )
        if element.tail is not None and element.tail.strip():
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "KiCad XML netlist contains unsupported mixed content",
            )
        children = list(element)
        allowed_children = _ALLOWED_CHILDREN.get(element.tag)
        if allowed_children is None:
            if children:
                _fail(
                    KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                    "KiCad XML leaf contains unsupported content",
                )
        else:
            if element.text is not None and element.text.strip():
                _fail(
                    KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                    "KiCad XML container contains unsupported mixed content",
                )
            if any(child.tag not in allowed_children for child in children):
                _fail(
                    KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                    "KiCad XML netlist contains unsupported structure",
                )
        stack.extend((child, depth + 1) for child in reversed(children))
    return root


def _direct(parent: ET.Element, tag: str) -> list[ET.Element]:
    return [child for child in parent if child.tag == tag]


def _one(parent: ET.Element, tag: str) -> ET.Element:
    matches = _direct(parent, tag)
    if len(matches) != 1:
        _fail(
            KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
            "KiCad XML netlist structure is unsupported",
        )
    return matches[0]


def _text(element: ET.Element) -> str:
    return element.text or ""


def _validate_export_shell(root: ET.Element, snapshot: CircuitIntentSnapshot) -> None:
    if root.tag != "export" or root.attrib != {"version": "E"}:
        _fail(
            KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
            "KiCad XML netlist format is unsupported",
        )
    if [child.tag for child in root] != [
        "design",
        "components",
        "libparts",
        "libraries",
        "nets",
    ]:
        _fail(
            KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
            "KiCad XML netlist sections are unsupported",
        )

    design = _one(root, "design")
    if len(_direct(design, "source")) != 1 or len(_direct(design, "date")) != 1:
        _fail(
            KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
            "KiCad XML design metadata is unsupported",
        )
    tool = _text(_one(design, "tool"))
    if not _TOOL.fullmatch(tool):
        _fail(
            KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
            "KiCad XML export tool is unsupported",
        )
    sheet = _one(design, "sheet")
    if sheet.attrib != {"number": "1", "name": "/", "tstamps": "/"}:
        _fail(
            KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
            "hierarchical KiCad XML exports are unsupported",
        )
    title_block = _one(sheet, "title_block")
    if _text(_one(title_block, "title")) != snapshot.content.title:
        _fail(
            KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
            "KiCad XML title does not match Circuit Intent",
        )
    comments: dict[str, str] = {}
    for comment in _direct(title_block, "comment"):
        if set(comment.attrib) != {"number", "value"}:
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "KiCad XML title metadata is unsupported",
            )
        number = comment.attrib["number"]
        if number in comments:
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "KiCad XML title metadata is duplicated",
            )
        comments[number] = comment.attrib["value"]
    expected_counts = (
        "Circuit Intent counts: "
        f"components={len(snapshot.content.components)}, "
        f"nets={len(snapshot.content.nets)}, ports={len(snapshot.content.ports)}"
    )
    if comments.get("1") != "Generated from CopperMCP Circuit Intent IR 0.1.0":
        _fail(
            KiCadSchematicParityErrorCode.SOURCE_MISMATCH,
            "KiCad XML source schema marker does not match",
        )
    if comments.get("2") != f"Circuit Intent source: {snapshot.snapshot_digest}":
        _fail(
            KiCadSchematicParityErrorCode.SOURCE_MISMATCH,
            "KiCad XML source digest does not match",
        )
    if comments.get("3") != expected_counts:
        _fail(
            KiCadSchematicParityErrorCode.SOURCE_MISMATCH,
            "KiCad XML source counts do not match",
        )
    if set(comments) != {str(index) for index in range(1, 10)}:
        _fail(
            KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
            "KiCad XML title metadata is incomplete",
        )
    libraries = _one(root, "libraries")
    if libraries.attrib or list(libraries):
        _fail(
            KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
            "external KiCad libraries are unsupported",
        )


def _validate_components(root: ET.Element, snapshot: CircuitIntentSnapshot) -> None:
    expected_by_reference = {
        component.reference: component for component in snapshot.content.components
    }
    components = _one(root, "components")
    actual_references: set[str] = set()
    for exported in _direct(components, "comp"):
        if set(exported.attrib) != {"ref"}:
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "KiCad XML component identity is unsupported",
            )
        reference = exported.attrib["ref"]
        component = expected_by_reference.get(reference)
        if component is None or reference in actual_references:
            _fail(
                KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
                "KiCad XML components do not match Circuit Intent",
            )
        actual_references.add(reference)
        if _text(_one(exported, "value")) != component.value:
            _fail(
                KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
                "KiCad XML component values do not match Circuit Intent",
            )
        library = _one(exported, "libsource")
        expected_part = "R" if component.kind is ComponentKind.RESISTOR else "C"
        if (
            set(library.attrib) != {"lib", "part", "description"}
            or library.attrib["lib"] != "CopperMCP"
            or library.attrib["part"] != expected_part
        ):
            _fail(
                KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
                "KiCad XML component libraries do not match Circuit Intent",
            )
        sheetpath = _one(exported, "sheetpath")
        if sheetpath.attrib != {"names": "/", "tstamps": "/"}:
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "hierarchical KiCad XML components are unsupported",
            )
        properties: dict[str, str | None] = {}
        for prop in _direct(exported, "property"):
            name = prop.attrib.get("name")
            if name is None or name in properties:
                _fail(
                    KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                    "KiCad XML component properties are unsupported",
                )
            properties[name] = prop.attrib.get("value")
        if set(properties) != {"Sheetname", "Sheetfile", "exclude_from_board"}:
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "KiCad XML component properties are unsupported",
            )
        if properties["Sheetname"] != "Root" or properties["exclude_from_board"] is not None:
            _fail(
                KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
                "KiCad XML board-exclusion state does not match the schematic subset",
            )
        sheetfile = properties["Sheetfile"]
        if sheetfile is None or not sheetfile.endswith(".kicad_sch"):
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "KiCad XML component source path is unsupported",
            )
        units = _one(exported, "units")
        unit = _one(units, "unit")
        if unit.attrib != {"name": "A"}:
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "multi-unit KiCad XML components are unsupported",
            )
        pins = _one(unit, "pins")
        pin_numbers = [pin.attrib.get("num") for pin in _direct(pins, "pin")]
        if len(pin_numbers) != 2 or set(pin_numbers) != {"1", "2"}:
            _fail(
                KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
                "KiCad XML component pins do not match Circuit Intent",
            )
        if any(set(pin.attrib) != {"num"} for pin in _direct(pins, "pin")):
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "KiCad XML component pin metadata is unsupported",
            )
    if actual_references != set(expected_by_reference):
        _fail(
            KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
            "KiCad XML components do not match Circuit Intent",
        )

    expected_parts = {
        "R" if component.kind is ComponentKind.RESISTOR else "C"
        for component in snapshot.content.components
    }
    actual_parts: set[str] = set()
    for libpart in _direct(_one(root, "libparts"), "libpart"):
        if set(libpart.attrib) != {"lib", "part"} or libpart.attrib["lib"] != "CopperMCP":
            _fail(
                KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
                "KiCad XML library parts do not match the schematic subset",
            )
        part = libpart.attrib["part"]
        if part in actual_parts:
            _fail(
                KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
                "KiCad XML library parts are duplicated",
            )
        actual_parts.add(part)
        pins = _one(libpart, "pins")
        pin_elements = _direct(pins, "pin")
        if len(pin_elements) != 2 or {pin.attrib.get("num") for pin in pin_elements} != {"1", "2"}:
            _fail(
                KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
                "KiCad XML library pins do not match the schematic subset",
            )
        if any(
            set(pin.attrib) != {"num", "name", "type"} or pin.attrib["type"] != "passive"
            for pin in pin_elements
        ):
            _fail(
                KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
                "KiCad XML library pin types do not match the schematic subset",
            )
    if actual_parts != expected_parts:
        _fail(
            KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
            "KiCad XML library parts do not match Circuit Intent",
        )


def _validate_connectivity(root: ET.Element, snapshot: CircuitIntentSnapshot) -> None:
    reference_by_id = {
        component.id: component.reference for component in snapshot.content.components
    }
    expected = {
        net.name: {
            (reference_by_id[connection.component_id], connection.pin)
            for connection in net.connections
        }
        for net in snapshot.content.nets
    }
    actual: dict[str, set[tuple[str, str]]] = {}
    codes: set[str] = set()
    for net in _direct(_one(root, "nets"), "net"):
        if set(net.attrib) != {"code", "name", "class"} or net.attrib["class"] != "Default":
            _fail(
                KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                "KiCad XML net metadata is unsupported",
            )
        code = net.attrib["code"]
        name = net.attrib["name"]
        if not re.fullmatch(r"[1-9][0-9]*", code) or code in codes or name in actual:
            _fail(
                KiCadSchematicParityErrorCode.CONNECTIVITY_MISMATCH,
                "KiCad XML nets are duplicated or malformed",
            )
        codes.add(code)
        nodes: set[tuple[str, str]] = set()
        for node in _direct(net, "node"):
            if set(node.attrib) != {"ref", "pin", "pintype"}:
                _fail(
                    KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
                    "KiCad XML node metadata is unsupported",
                )
            if node.attrib["pintype"] != "passive":
                _fail(
                    KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
                    "KiCad XML pin types do not match the schematic subset",
                )
            membership = (node.attrib["ref"], node.attrib["pin"])
            if membership in nodes:
                _fail(
                    KiCadSchematicParityErrorCode.CONNECTIVITY_MISMATCH,
                    "KiCad XML net contains a duplicate connection",
                )
            nodes.add(membership)
        actual[name] = nodes
    if actual != expected:
        _fail(
            KiCadSchematicParityErrorCode.CONNECTIVITY_MISMATCH,
            "KiCad XML connectivity does not match Circuit Intent",
        )


def verify_kicad_schematic_parity(
    snapshot: CircuitIntentSnapshot,
    schematic: bytes,
    kicad_xml_netlist: bytes,
    limits: KiCadSchematicParityLimits | None = None,
) -> KiCadSchematicParityEvidence:
    """Prove exact source replay and KiCad-exported connectivity parity.

    This pure function does not run KiCad and does not claim ERC.  The caller owns
    the fixed-argument, private-environment CLI invocation that produced the XML.
    """

    if not isinstance(snapshot, CircuitIntentSnapshot):
        _fail(
            KiCadSchematicParityErrorCode.INPUT_INVALID,
            "schematic parity requires a Circuit Intent snapshot",
        )
    if type(schematic) is not bytes or not schematic:
        _fail(
            KiCadSchematicParityErrorCode.INPUT_INVALID,
            "schematic parity requires immutable schematic bytes",
        )
    if len(schematic) > MAX_RENDERED_SCHEMATIC_BYTES:
        _fail(
            KiCadSchematicParityErrorCode.BUDGET_EXCEEDED,
            "schematic exceeds the renderer byte budget",
        )
    if limits is not None and not isinstance(limits, KiCadSchematicParityLimits):
        _fail(
            KiCadSchematicParityErrorCode.INPUT_INVALID,
            "schematic parity limits are malformed",
        )
    active_limits = limits or KiCadSchematicParityLimits()

    verify_snapshot(snapshot)
    artifact = render_kicad_schematic(snapshot)
    if schematic != artifact.content:
        _fail(
            KiCadSchematicParityErrorCode.SOURCE_MISMATCH,
            "schematic bytes are not the deterministic Circuit Intent derivative",
        )

    root = _parse_netlist(kicad_xml_netlist, active_limits)
    _validate_export_shell(root, snapshot)
    _validate_components(root, snapshot)
    _validate_connectivity(root, snapshot)
    return KiCadSchematicParityEvidence(
        intent_digest=snapshot.snapshot_digest,
        schematic_digest=artifact.artifact_digest,
        netlist_digest=f"sha256:{hashlib.sha256(kicad_xml_netlist).hexdigest()}",
        netlist_format_version="E",
        component_count=len(snapshot.content.components),
        net_count=len(snapshot.content.nets),
        connection_count=sum(len(net.connections) for net in snapshot.content.nets),
        source_replay="passed",
        component_parity="passed",
        connectivity_parity="passed",
    )


__all__ = [
    "MAX_KICAD_XML_DEPTH",
    "MAX_KICAD_XML_ELEMENTS",
    "MAX_KICAD_XML_NETLIST_BYTES",
    "MAX_KICAD_XML_TEXT_BYTES",
    "KiCadSchematicParityError",
    "KiCadSchematicParityErrorCode",
    "KiCadSchematicParityEvidence",
    "KiCadSchematicParityLimits",
    "verify_kicad_schematic_parity",
]
