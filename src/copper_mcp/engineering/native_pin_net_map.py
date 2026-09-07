"""Bounded private KiCad XML pin-to-net evidence, bound to a placed-pin census."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from copper_mcp.engineering import component_netlist as _xml
from copper_mcp.engineering._pin_net_source import (
    _BOUNDS,
    _DEADLINE,
    _MALFORMED,
    _check_deadline,
    _component_refusal,
    _fail,
    _SourcePinGroup,
    _text,
    _validate_census,
)
from copper_mcp.engineering._pin_net_source import (
    NativePinNetMapError as NativePinNetMapError,
)
from copper_mcp.engineering._pin_net_source import (
    SourcePinAlias as SourcePinAlias,
)
from copper_mcp.engineering.component_netlist import ComponentNetlist
from copper_mcp.engineering.project_pin_census import ProjectPinCensus

_MAX_NET_CODE = (1 << 31) - 1
_MAX_NET_CODE_TEXT = str(_MAX_NET_CODE)
_INTEGER = re.compile(r"[1-9][0-9]*")


@dataclass(frozen=True, slots=True, repr=False)
class NativePinNet:
    reference: str
    pin_number: str
    library_id: str
    net_code: str
    net_name: str
    net_class: str
    pinfunction: str | None
    electrical_type: str
    no_connect: bool
    aliases: tuple[SourcePinAlias, ...]

    def __repr__(self) -> str:
        return "<NativePinNet redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class NativePinNetMap:
    census_digest: str
    component_inventory_digest: str
    backend_version: str
    nodes: tuple[NativePinNet, ...]
    virtual_pin_count: int

    @property
    def digest(self) -> str:
        return self._digest(math.inf)

    def _digest(self, deadline: float) -> str:
        """Hash all immutable evidence using canonical streaming JSON."""

        def node_key(node: NativePinNet) -> tuple[str, str]:
            _check_deadline(deadline)
            return node.reference, node.pin_number

        _check_deadline(deadline)
        ordered = sorted(self.nodes, key=node_key)
        digest = hashlib.sha256(b"copper-mcp/native-pin-net-map/v1\x00")

        def write(value: object) -> None:
            _check_deadline(deadline)
            digest.update(json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii"))

        def write_alias(alias: SourcePinAlias) -> None:
            _check_deadline(deadline)
            digest.update(b'{"sheet_uuid_path":')
            write(alias.sheet_uuid_path)
            digest.update(b',"source_pin_uuid":')
            write(alias.source_pin_uuid)
            digest.update(b',"source_symbol_uuid":')
            write(alias.source_symbol_uuid)
            digest.update(b"}")

        def write_node(node: NativePinNet) -> None:
            _check_deadline(deadline)
            digest.update(b'{"aliases":[')
            for alias_index, alias in enumerate(node.aliases):
                _check_deadline(deadline)
                if alias_index:
                    digest.update(b",")
                write_alias(alias)
            digest.update(b'],"electrical_type":')
            write(node.electrical_type)
            digest.update(b',"library_id":')
            write(node.library_id)
            digest.update(b',"net_class":')
            write(node.net_class)
            digest.update(b',"net_code":')
            write(node.net_code)
            digest.update(b',"net_name":')
            write(node.net_name)
            digest.update(b',"no_connect":')
            write(node.no_connect)
            digest.update(b',"pin_number":')
            write(node.pin_number)
            digest.update(b',"pinfunction":')
            write(node.pinfunction)
            digest.update(b',"reference":')
            write(node.reference)
            digest.update(b"}")

        digest.update(b'{"backend_version":')
        write(self.backend_version)
        digest.update(b',"census_digest":')
        write(self.census_digest)
        digest.update(b',"component_inventory_digest":')
        write(self.component_inventory_digest)
        digest.update(b',"nodes":[')
        for index, node in enumerate(ordered):
            _check_deadline(deadline)
            if index:
                digest.update(b",")
            write_node(node)
        digest.update(b'],"virtual_pin_count":')
        write(self.virtual_pin_count)
        digest.update(b"}")
        _check_deadline(deadline)
        return "sha256:" + digest.hexdigest()

    def __repr__(self) -> str:
        return "<NativePinNetMap redacted>"


def _leaf(element: ET.Element, attributes: frozenset[str]) -> None:
    if set(element.attrib) != attributes or len(element) or element.text is not None:
        _fail()


def _positive_code(value: object) -> str:
    code = _text(value)
    if _INTEGER.fullmatch(code) is None:
        _fail()
    if len(code) > len(_MAX_NET_CODE_TEXT) or (
        len(code) == len(_MAX_NET_CODE_TEXT) and code > _MAX_NET_CODE_TEXT
    ):
        _fail(_BOUNDS)
    return code


def _node_key(node: NativePinNet, deadline: float) -> tuple[str, str]:
    _check_deadline(deadline)
    return node.reference, node.pin_number


def _validate_nets(
    root: ET.Element,
    aliases_by_key: dict[tuple[str, str], _SourcePinGroup],
    components: dict[str, str],
    deadline: float,
) -> tuple[NativePinNet, ...]:
    containers = tuple(child for child in root if child.tag == "nets")
    if len(containers) != 1:
        _fail()
    container = containers[0]
    if (
        container.attrib
        or (container.text is not None and container.text.strip())
        or any(child.tag != "net" for child in container)
    ):
        _fail()
    codes: set[str] = set()
    names: set[str] = set()
    nodes: list[NativePinNet] = []
    keys: set[tuple[str, str]] = set()
    for net_index, net in enumerate(container):
        if net_index % _xml._DEADLINE_NODE_INTERVAL == 0:
            _check_deadline(deadline)
        if set(net.attrib) != {"code", "name", "class"} or not len(net):
            _fail()
        if net.text is not None and net.text.strip():
            _fail()
        code = _positive_code(net.attrib["code"])
        name = _text(net.attrib["name"], allow_empty=True)
        net_class = _text(net.attrib["class"], allow_empty=True)
        if code in codes or name in names:
            _fail()
        codes.add(code)
        names.add(name)
        for node in net:
            _check_deadline(deadline)
            attributes = set(node.attrib)
            if node.tag != "node" or not {"ref", "pin", "pintype"}.issubset(attributes):
                _fail()
            if not attributes.issubset({"ref", "pin", "pintype", "pinfunction"}):
                _fail()
            _leaf(node, frozenset(attributes))
            reference = _text(node.attrib["ref"])
            number = _text(node.attrib["pin"])
            key = (reference, number)
            if key in keys or key not in aliases_by_key or reference not in components:
                _fail()
            keys.add(key)
            raw_type = _text(node.attrib["pintype"])
            no_connect = raw_type.endswith("+no_connect")
            electrical_type = raw_type[: -len("+no_connect")] if no_connect else raw_type
            if not electrical_type or "+no_connect" in electrical_type:
                _fail()
            function = node.attrib.get("pinfunction")
            if function is not None:
                function = _text(function)
            group = aliases_by_key[key]
            expected_function = f"{group.effective_name}_{number}" if group.effective_name else None
            if electrical_type != group.electrical_type or function != expected_function:
                _fail()
            nodes.append(
                NativePinNet(
                    reference,
                    number,
                    components[reference],
                    code,
                    name,
                    net_class,
                    function,
                    electrical_type,
                    no_connect,
                    group.aliases,
                )
            )
    if keys != set(aliases_by_key):
        _fail()
    return tuple(sorted(nodes, key=lambda node: _node_key(node, deadline)))


def parse_native_pin_net_map(
    payload: bytes,
    *,
    census: ProjectPinCensus,
    expected_source: str,
    expected_sheet_paths: tuple[str, ...],
    deadline: float,
    max_bytes: int,
) -> NativePinNetMap:
    """Parse one bounded format-E native observation without inferring connectivity."""

    admitted: tuple[bytes, str, tuple[str, ...], float, int] | None = None
    root: ET.Element | None = None
    inventory = None
    refusal = _MALFORMED
    try:
        data, source, sheets, active_deadline, _ = _xml._validate_inputs(
            payload, expected_source, expected_sheet_paths, deadline, max_bytes
        )
        _xml._reject_xml_directives(data, active_deadline)
        root = _xml._parse_xml(data, active_deadline)
        if root.tag != "export" or root.attrib != {"version": "E"}:
            _fail()
        if (
            sum(child.tag == "design" for child in root) != 1
            or sum(child.tag == "components" for child in root) != 1
            or sum(child.tag == "nets" for child in root) != 1
        ):
            _fail()
        validated_sheets = _xml._validate_design(root, source, sheets, active_deadline)
        inventory = _xml._validate_components(root, validated_sheets, active_deadline)
        admitted = data, source, sheets, active_deadline, 0
    except _xml.ComponentNetlistError as error:
        refusal = _component_refusal(error)
    if admitted is None or root is None or inventory is None:
        if type(deadline) in (int, float) and not isinstance(deadline, bool):
            try:
                if time.monotonic() >= float(deadline):
                    _fail(_DEADLINE)
            except OverflowError:
                pass
        _fail(refusal)
    _, _, _, active_deadline, _ = admitted
    aliases_by_key, census_components, virtual_pin_count, census_digest = _validate_census(
        census, active_deadline
    )
    components = {component.reference: component.library_id for component in inventory}
    if set(components.items()) != census_components:
        _fail()
    nodes = _validate_nets(root, aliases_by_key, components, active_deadline)
    inventory_digest: str | None = None
    try:
        inventory_digest = ComponentNetlist(
            inventory, validated_sheets, _xml._BACKEND_VERSION
        )._digest(active_deadline)
    except _xml.ComponentNetlistError as error:
        refusal = _component_refusal(error)
    _check_deadline(active_deadline)
    if inventory_digest is None:
        _fail(refusal)
    result = NativePinNetMap(
        census_digest,
        inventory_digest,
        _xml._BACKEND_VERSION,
        nodes,
        virtual_pin_count,
    )
    result._digest(active_deadline)
    _check_deadline(active_deadline)
    return result


__all__ = [
    "NativePinNet",
    "NativePinNetMap",
    "NativePinNetMapError",
    "SourcePinAlias",
    "parse_native_pin_net_map",
]
