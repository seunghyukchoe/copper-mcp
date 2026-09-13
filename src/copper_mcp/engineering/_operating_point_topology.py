"""Bounded DC topology preflight for the confined operating-point pipeline."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Literal, NoReturn, cast

from copper_mcp.engineering.spice_export import _TOKEN
from copper_mcp.engineering.spice_model_library import (
    _SUFFIX_SCALES,
    SpiceDefinition,
    SpiceModelLibraryError,
    _identifier,
    _logical_lines,
    _number,
    _parse_element,
    parse_spice_model_library,
)

_MAX_LIBRARIES = 16
_MAX_LIBRARY_BYTES = 1024 * 1024
_MAX_MODEL_BYTES = 16 * 1024 * 1024
_MAX_ROWS = 10_000
_MAX_ROW_TOKENS = 66
_MAX_EDGES = 100_000
_MAX_WORK = 100_000
_MAX_NODES = 100_000
_MAX_DEPTH = 128
_MAX_PATH_BYTES = 4096
_MAX_RETAINED_PATH_BYTES = 16 * 1024 * 1024
_MAX_TOKEN_BYTES = 4096
_INSTANCE = re.compile(r"[DX][A-Za-z0-9_.:-]{0,256}\Z")


class OperatingPointTopologyError(ValueError):
    """Fixed refusal without model, node, stimulus, or circuit disclosure."""


def _fail(message: str = "operating-point topology preflight refused") -> NoReturn:
    raise OperatingPointTopologyError(message)


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail("operating-point topology preflight deadline expired")


def _deadline(value: object) -> float:
    if type(value) not in (int, float):
        _fail("operating-point topology preflight controls are malformed")
    if type(value) is int and not -(1 << 1023) <= value <= 1 << 1023:
        _fail("operating-point topology preflight controls are malformed")
    result = float(cast(int | float, value))
    if not math.isfinite(result):
        _fail("operating-point topology preflight controls are malformed")
    _check(result)
    return result


def _token(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= _MAX_TOKEN_BYTES:
        _fail()
    text = value
    if not text.isascii() or _TOKEN.fullmatch(text) is None:
        _fail()
    return text


def _model_name(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 64:
        _fail()
    text = value
    if not text.isascii() or not _identifier(text.encode("ascii")):
        _fail()
    return text


def _admit_inputs(
    rows: object,
    model_sources: object,
    voltage_edges: object,
    current_edges: object,
    deadline: float,
) -> None:
    if type(rows) is not tuple or not 1 <= len(rows) <= _MAX_ROWS:
        _fail()
    if type(model_sources) is not tuple or not 1 <= len(model_sources) <= _MAX_LIBRARIES:
        _fail()
    total_model_bytes = 0
    for source in model_sources:
        _check(deadline)
        if type(source) is not bytes or not source or len(source) > _MAX_LIBRARY_BYTES:
            _fail()
        if len(source) > _MAX_MODEL_BYTES - total_model_bytes:
            _fail()
        total_model_bytes += len(source)
    for row in rows:
        _check(deadline)
        if type(row) is not tuple or not 3 <= len(row) <= _MAX_ROW_TOKENS:
            _fail()
        for value in row:
            _check(deadline)
            _token(value)
        if (
            _INSTANCE.fullmatch(row[0]) is None
            or (row[0].startswith("D") and len(row) != 4)
            or (not row[0].startswith(("D", "X")))
        ):
            _fail()
        _model_name(row[-1])
    if type(voltage_edges) is not tuple or type(current_edges) is not tuple:
        _fail()
    if len(voltage_edges) + len(current_edges) > _MAX_EDGES:
        _fail()
    for edges in (voltage_edges, current_edges):
        for edge in edges:
            _check(deadline)
            if type(edge) is not tuple or len(edge) != 2:
                _fail()
            first = _token(edge[0])
            second = _token(edge[1])
            if _node_key(first) == _node_key(second):
                _fail()


@dataclass(frozen=True, slots=True, repr=False)
class _Element:
    name: str
    kind: Literal["R", "C", "L", "D", "X"]
    nodes: tuple[str, ...]
    dependency: str | None

    def __repr__(self) -> str:
        return "<_OperatingPointElement redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class _Model:
    name: str
    kind: Literal["diode", "subcircuit"]
    ports: tuple[str, ...]
    elements: tuple[_Element, ...]

    def __repr__(self) -> str:
        return "<_OperatingPointModel redacted>"


def _positive_number(value: bytes) -> bool:
    if not _number(value):
        return False
    normalized = value.lower()
    suffix = b""
    if normalized.endswith(b"meg"):
        suffix = b"meg"
    elif normalized[-1:] in _SUFFIX_SCALES:
        suffix = normalized[-1:]
    numeric = normalized[: -len(suffix)] if suffix else normalized
    try:
        parsed = float(numeric) * _SUFFIX_SCALES.get(suffix, 1.0)
    except ValueError:
        return False
    return math.isfinite(parsed) and parsed > 0


def _reparse_definition(definition: SpiceDefinition, deadline: float) -> _Model:
    _check(deadline)
    if definition.kind == "diode":
        lines = _logical_lines(definition.source, deadline)
        if len(lines) != 1 or not lines[0].text.lower().startswith(b".model"):
            _fail()
        return _Model(definition.name, "diode", ("A", "K"), ())
    lines = _logical_lines(definition.source, deadline)
    if len(lines) < 2:
        _fail()
    opener = lines[0].text.split()
    closer = lines[-1].text.split()
    if (
        len(opener) < 3
        or opener[0].lower() != b".subckt"
        or opener[1].decode("ascii") != definition.name
        or tuple(item.decode("ascii") for item in opener[2:]) != definition.ports
        or not closer
        or closer[0].lower() != b".ends"
    ):
        _fail()
    if any(port.casefold() == "gnd" for port in definition.ports):
        _fail()
    elements: list[_Element] = []
    used_ports: set[str] = set()
    formal_ports = {port.casefold() for port in definition.ports}
    for line in lines[1:-1]:
        _check(deadline)
        parsed = _parse_element(line, deadline)
        tokens = line.text.split()
        if parsed.kind in {"R", "C", "L"}:
            if not _positive_number(tokens[3]):
                _fail()
            node_tokens = tokens[1:3]
            dependency = None
        elif parsed.kind == "D":
            node_tokens = tokens[1:3]
            dependency = tokens[3].decode("ascii")
        else:
            node_tokens = tokens[1:-1]
            dependency = tokens[-1].decode("ascii")
        nodes = tuple(node.decode("ascii") for node in node_tokens)
        used_ports.update(node.casefold() for node in nodes if node.casefold() in formal_ports)
        elements.append(_Element(parsed.name.decode("ascii"), parsed.kind, nodes, dependency))
    if used_ports != formal_ports:
        _fail()
    return _Model(definition.name, "subcircuit", definition.ports, tuple(elements))


def _parse_models(model_sources: tuple[bytes, ...], deadline: float) -> dict[str, _Model]:
    models: dict[str, _Model] = {}
    for source in model_sources:
        _check(deadline)
        library = parse_spice_model_library(source, deadline=deadline)
        for definition in library.definitions:
            _check(deadline)
            key = definition.name.casefold()
            if key in models:
                _fail()
            models[key] = _reparse_definition(definition, deadline)
    return models


def _node_key(name: str) -> str:
    lowered = name.casefold()
    return "0" if lowered in {"0", "gnd"} else lowered


class _UnionFind:
    __slots__ = ("_deadline", "_parent", "_rank")

    def __init__(self, deadline: float) -> None:
        self._parent = [0]
        self._rank = [0]
        self._deadline = deadline

    def add(self) -> None:
        _check(self._deadline)
        index = len(self._parent)
        self._parent.append(index)
        self._rank.append(0)

    def find(self, node: int) -> int:
        root = node
        while self._parent[root] != root:
            _check(self._deadline)
            root = self._parent[root]
        while self._parent[node] != node:
            _check(self._deadline)
            parent = self._parent[node]
            self._parent[node] = root
            node = parent
        return root

    def union(self, first: int, second: int) -> bool:
        left = self.find(first)
        right = self.find(second)
        if left == right:
            return False
        if self._rank[left] < self._rank[right]:
            left, right = right, left
        self._parent[right] = left
        if self._rank[left] == self._rank[right]:
            self._rank[left] += 1
        return True


def _validate_graph(
    rows: tuple[tuple[str, ...], ...],
    models: dict[str, _Model],
    voltage_edges: tuple[tuple[str, str], ...],
    current_edges: tuple[tuple[str, str], ...],
    deadline: float,
) -> None:
    global_nodes = {"0": 0}
    local_nodes: dict[tuple[int, str], int] = {}
    connections = [0]
    dc = _UnionFind(deadline)
    constrained = _UnionFind(deadline)
    instance_paths: set[str] = set()
    local_qualified_nodes: set[str] = set()
    next_instance = 0
    work = 0
    retained_path_bytes = 0

    def step() -> None:
        nonlocal work
        _check(deadline)
        work += 1
        if work > _MAX_WORK:
            _fail()

    def new_node() -> int:
        if len(connections) >= _MAX_NODES:
            _fail()
        node = len(connections)
        connections.append(0)
        dc.add()
        constrained.add()
        return node

    def retain_path(path: str) -> None:
        nonlocal retained_path_bytes
        _check(deadline)
        size = len(path.encode("ascii"))
        if size > _MAX_PATH_BYTES or size > _MAX_RETAINED_PATH_BYTES - retained_path_bytes:
            _fail()
        retained_path_bytes += size

    def qualified_path(parent: str, child: str) -> str:
        _check(deadline)
        child_key = child.casefold()
        parent_size = len(parent.encode("ascii"))
        child_size = len(child_key.encode("ascii"))
        if parent_size > _MAX_PATH_BYTES - 1 - child_size:
            _fail()
        return parent + "." + child_key

    def global_node(name: str) -> int:
        key = _node_key(name)
        node = global_nodes.get(key)
        if node is None:
            node = new_node()
            global_nodes[key] = node
        return node

    def scoped_node(instance: int, instance_path: str, name: str, formals: dict[str, int]) -> int:
        key = _node_key(name)
        if key == "0":
            return 0
        formal = formals.get(key)
        if formal is not None:
            return formal
        scoped = (instance, key)
        node = local_nodes.get(scoped)
        if node is None:
            qualified = qualified_path(instance_path, key)
            if qualified in global_nodes or qualified in local_qualified_nodes:
                _fail()
            retain_path(qualified)
            local_qualified_nodes.add(qualified)
            node = new_node()
            local_nodes[scoped] = node
        return node

    def primitive(kind: str, first: int, second: int) -> None:
        connections[first] += 1
        connections[second] += 1
        if kind in {"R", "L", "D", "V"}:
            dc.union(first, second)
        if kind in {"L", "V"} and (first == second or not constrained.union(first, second)):
            _fail()

    tasks: list[tuple[_Model, int, dict[str, int], int, str]] = []
    for row in rows:
        _check(deadline)
        instance_key = row[0].casefold()
        if instance_key in instance_paths:
            _fail()
        retain_path(instance_key)
        instance_paths.add(instance_key)
        model = models.get(row[-1].casefold())
        if model is None:
            _fail()
        actual_nodes = tuple(global_node(name) for name in row[1:-1])
        if row[0].startswith("D"):
            if model.kind != "diode" or len(actual_nodes) != 2:
                _fail()
            step()
            primitive("D", actual_nodes[0], actual_nodes[1])
            continue
        if model.kind != "subcircuit" or len(actual_nodes) != len(model.ports):
            _fail()
        step()
        next_instance += 1
        tasks.append(
            (
                model,
                next_instance,
                dict(zip((port.casefold() for port in model.ports), actual_nodes, strict=True)),
                1,
                instance_key,
            )
        )

    while tasks:
        _check(deadline)
        model, instance, formals, depth, instance_path = tasks.pop()
        if depth > _MAX_DEPTH:
            _fail()
        for element in model.elements:
            step()
            nodes = tuple(
                scoped_node(instance, instance_path, name, formals) for name in element.nodes
            )
            if element.kind == "X":
                if element.dependency is None:
                    _fail()
                dependency = models.get(element.dependency.casefold())
                if (
                    dependency is None
                    or dependency.kind != "subcircuit"
                    or len(nodes) != len(dependency.ports)
                ):
                    _fail()
                child_path = qualified_path(instance_path, element.name)
                if child_path in instance_paths:
                    _fail()
                retain_path(child_path)
                instance_paths.add(child_path)
                next_instance += 1
                tasks.append(
                    (
                        dependency,
                        next_instance,
                        dict(
                            zip(
                                (port.casefold() for port in dependency.ports),
                                nodes,
                                strict=True,
                            )
                        ),
                        depth + 1,
                        child_path,
                    )
                )
            else:
                if len(nodes) != 2:
                    _fail()
                if element.kind == "D":
                    if element.dependency is None:
                        _fail()
                    diode = models.get(element.dependency.casefold())
                    if diode is None or diode.kind != "diode":
                        _fail()
                primitive(element.kind, nodes[0], nodes[1])

    def stimulus(edges: tuple[tuple[str, str], ...], kind: str) -> None:
        for first_name, second_name in edges:
            _check(deadline)
            first = global_nodes.get(_node_key(first_name))
            second = global_nodes.get(_node_key(second_name))
            if first is None or second is None:
                _fail()
            primitive(kind, first, second)

    stimulus(current_edges, "I")
    stimulus(voltage_edges, "V")
    ground = dc.find(0)
    for node in range(1, len(connections)):
        _check(deadline)
        if connections[node] < 2 or dc.find(node) != ground:
            _fail()


def _validate_dc_topology(
    rows: tuple[tuple[str, ...], ...],
    model_sources: tuple[bytes, ...],
    voltage_edges: tuple[tuple[str, str], ...],
    current_edges: tuple[tuple[str, str], ...],
    deadline: float,
) -> None:
    _admit_inputs(rows, model_sources, voltage_edges, current_edges, deadline)
    models = _parse_models(model_sources, deadline)
    _validate_graph(rows, models, voltage_edges, current_edges, deadline)
    _check(deadline)


def validate_dc_topology(
    rows: tuple[tuple[str, ...], ...],
    model_sources: tuple[bytes, ...],
    voltage_edges: tuple[tuple[str, str], ...],
    current_edges: tuple[tuple[str, str], ...],
    *,
    deadline: float,
) -> None:
    """Refuse unsupported DC topology without executing or repairing the circuit."""

    active_deadline = _deadline(deadline)
    completed = False
    try:
        _validate_dc_topology(rows, model_sources, voltage_edges, current_edges, active_deadline)
        completed = True
    except OperatingPointTopologyError:
        raise
    except (
        AttributeError,
        IndexError,
        KeyError,
        OverflowError,
        RecursionError,
        SpiceModelLibraryError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        pass
    if not completed:
        _check(active_deadline)
        _fail()


__all__ = ["OperatingPointTopologyError", "validate_dc_topology"]
