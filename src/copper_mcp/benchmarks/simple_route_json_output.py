"""Convert routed SimpleRouteJson output into the closed external-candidate documents.

This is an import-side adapter, not a trust boundary.  It preserves raw JSON number tokens,
binds every trace to exactly one imported electrical net, and emits only the existing v1/v2
foreign documents.  The production disposer remains solely responsible for candidate identity,
Board IR legality, and acceptance.

The supported upstream shape is pinned to tscircuit's ``SimpleRouteJson`` and
``SimplifiedPcbTrace`` declarations at commit
``2010a730f172d979f95c11eb6836e922e565061d``:
https://github.com/tscircuit/tscircuit-autorouter/blob/2010a730f172d979f95c11eb6836e922e565061d/lib/types/srj-types.ts

Nothing here is reachable from MCP, CLI, apply, persistence, or the production routing package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from itertools import pairwise
from typing import Any, Final

from copper_mcp.benchmarks.simple_route_json import (
    ImportedNet,
    ImportedProblem,
    SimpleRouteJsonImportError,
    SimpleRouteJsonImportLimits,
    import_simple_route_json,
    mm_token_to_nm,
)
from copper_mcp.board_ir import PointNM, verify_snapshot
from copper_mcp.routing.external_candidate_verifier import (
    EXTERNAL_ROUTE_CANDIDATE_SCHEMA,
    EXTERNAL_ROUTE_PATCH_SCHEMA,
)

_JSON_SAFE_INTEGER: Final = (1 << 53) - 1
_MAX_DOCUMENT_BYTES: Final = 4_000_000
_MAX_TRACES: Final = 4_096
_MAX_ROUTE_ITEMS_PER_TRACE: Final = 4_096
_MAX_TOTAL_ROUTE_ITEMS: Final = 4_096
_MAX_NUMBER_TOKEN_LENGTH: Final = 40
_MAX_EXTENT_NM: Final = 1_000_000_000
_TRACE_KEYS: Final = frozenset(
    {
        "type",
        "pcb_trace_id",
        "__replaces_pcb_trace_id",
        "connection_name",
        "connectsTo",
        "route",
    }
)
_TRACE_REQUIRED_KEYS: Final = frozenset({"type", "pcb_trace_id", "connection_name", "route"})
_WIRE_KEYS: Final = frozenset(
    {"route_type", "x", "y", "width", "layer", "start_pcb_port_id", "end_pcb_port_id"}
)
_WIRE_REQUIRED_KEYS: Final = frozenset({"route_type", "x", "y", "width", "layer"})
_CONNECTION_ALIAS_FIELDS: Final = (
    "rootConnectionName",
    "netConnectionName",
    "__netConnectionName",
)
_CONNECTION_ALIAS_LIST_FIELDS: Final = ("mergedConnectionNames", "__rootConnectionNames")


class _NumberToken(str):
    """A JSON number's exact source spelling, distinguishable from a JSON string."""


class OutputAdapterRefusalCode(StrEnum):
    """Stable fail-closed taxonomy before a foreign document reaches the disposer."""

    MALFORMED_DOCUMENT = "malformed_document"
    BUDGET_EXCEEDED = "budget_exceeded"
    SOURCE_MISMATCH = "source_mismatch"
    AMBIGUOUS_NET_OWNERSHIP = "ambiguous_net_ownership"
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"
    DISCONTINUOUS_PATH = "discontinuous_path"
    ENDPOINT_MISMATCH = "endpoint_mismatch"


@dataclass(frozen=True, slots=True)
class SimpleRouteJsonOutputAdapterError(ValueError):
    """One typed refusal that never echoes source-controlled values."""

    code: OutputAdapterRefusalCode
    message: str
    locator: str = "output"

    def __str__(self) -> str:
        return f"{self.code} at {self.locator}: {self.message}"


@dataclass(frozen=True, slots=True)
class SimpleRouteJsonOutputLimits:
    """Server-owned work ceilings for one untrusted routed result."""

    max_document_bytes: int = _MAX_DOCUMENT_BYTES
    max_traces: int = _MAX_TRACES
    max_route_items_per_trace: int = _MAX_ROUTE_ITEMS_PER_TRACE
    max_total_route_items: int = _MAX_TOTAL_ROUTE_ITEMS
    max_number_token_length: int = _MAX_NUMBER_TOKEN_LENGTH
    max_extent_nm: int = _MAX_EXTENT_NM

    def __post_init__(self) -> None:
        maxima = {
            "max_document_bytes": _MAX_DOCUMENT_BYTES,
            "max_traces": _MAX_TRACES,
            "max_route_items_per_trace": _MAX_ROUTE_ITEMS_PER_TRACE,
            "max_total_route_items": _MAX_TOTAL_ROUTE_ITEMS,
            "max_number_token_length": _MAX_NUMBER_TOKEN_LENGTH,
            "max_extent_nm": _MAX_EXTENT_NM,
        }
        for field_name, maximum in maxima.items():
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{field_name} must be an integer from 1 through {maximum}")


def _refuse(
    code: OutputAdapterRefusalCode, message: str, locator: str = "output"
) -> SimpleRouteJsonOutputAdapterError:
    return SimpleRouteJsonOutputAdapterError(code, message, locator)


def _reject_constant(_token: str) -> Any:
    raise _refuse(
        OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
        "numbers must be finite decimal values",
        "number",
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _refuse(
                OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
                "JSON objects must not repeat a key",
                "object",
            )
        result[key] = value
    return result


def _parse_document(
    document: object, limits: SimpleRouteJsonOutputLimits, *, locator: str
) -> dict[str, Any]:
    if type(document) not in {bytes, bytearray}:
        raise _refuse(
            OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
            "document must be raw bytes",
            locator,
        )
    assert isinstance(document, bytes | bytearray)
    if len(document) > limits.max_document_bytes:
        raise _refuse(
            OutputAdapterRefusalCode.BUDGET_EXCEEDED,
            "document exceeds the byte budget",
            locator,
        )
    try:
        text = bytes(document).decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise _refuse(
            OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
            "document is not strict UTF-8",
            locator,
        ) from error
    try:
        value = json.loads(
            text,
            parse_float=_NumberToken,
            parse_int=_NumberToken,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (json.JSONDecodeError, RecursionError) as error:
        raise _refuse(
            OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
            "document is not valid bounded JSON",
            locator,
        ) from error
    if type(value) is not dict:
        raise _refuse(
            OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
            "document root must be an object",
            locator,
        )
    return value


def _text(value: object, locator: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 256:
        raise _refuse(
            OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
            "expected bounded non-empty text",
            locator,
        )
    return value


def _list(value: object, locator: str) -> list[Any]:
    if type(value) is not list:
        raise _refuse(
            OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
            "expected an array",
            locator,
        )
    return value


def _closed_mapping(
    value: object,
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    locator: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise _refuse(
            OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
            "expected an object",
            locator,
        )
    assert isinstance(value, dict)
    if len(value) > len(allowed) or not all(key in allowed for key in value):
        raise _refuse(
            OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
            "object contains an unknown field",
            locator,
        )
    if not all(key in value for key in required):
        raise _refuse(
            OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
            "object is incomplete",
            locator,
        )
    return value


def _source_is_exact(
    source_document: object,
    problem: object,
) -> ImportedProblem:
    if type(problem) is not ImportedProblem or type(source_document) not in {bytes, bytearray}:
        raise _refuse(
            OutputAdapterRefusalCode.SOURCE_MISMATCH,
            "source and imported problem do not form an exact pair",
            "source",
        )
    assert isinstance(problem, ImportedProblem)
    assert isinstance(source_document, bytes | bytearray)
    digest = sha256(bytes(source_document)).hexdigest()
    if digest != problem.document_sha256:
        raise _refuse(
            OutputAdapterRefusalCode.SOURCE_MISMATCH,
            "source digest does not match the imported problem",
            "source",
        )
    try:
        replay = import_simple_route_json(
            problem.name,
            bytes(source_document),
            policy=problem.policy,
        )
        verify_snapshot(problem.snapshot)
    except (SimpleRouteJsonImportError, TypeError, ValueError) as error:
        raise _refuse(
            OutputAdapterRefusalCode.SOURCE_MISMATCH,
            "source replay or imported snapshot is invalid",
            "source",
        ) from error
    if (
        replay.document_sha256 != problem.document_sha256
        or replay.snapshot.snapshot_digest != problem.snapshot.snapshot_digest
        or replay.snapshot.content.source.revision != problem.snapshot.content.source.revision
        or replay.nets != problem.nets
        or replay.track_width_nm != problem.track_width_nm
        or replay.policy != problem.policy
        or replay.adapter_version != problem.adapter_version
    ):
        raise _refuse(
            OutputAdapterRefusalCode.SOURCE_MISMATCH,
            "source replay does not match the imported problem",
            "source",
        )
    return replay


def _selected_net(problem: ImportedProblem, net_id: object) -> ImportedNet:
    if type(net_id) is not str:
        raise _refuse(
            OutputAdapterRefusalCode.AMBIGUOUS_NET_OWNERSHIP,
            "selected net is not uniquely identified",
            "net",
        )
    matches = [net for net in problem.routable_nets if net.net_id == net_id]
    if len(matches) != 1:
        raise _refuse(
            OutputAdapterRefusalCode.AMBIGUOUS_NET_OWNERSHIP,
            "selected net is not uniquely identified",
            "net",
        )
    return matches[0]


def _aliases_by_net(source: dict[str, Any], problem: ImportedProblem) -> dict[str, frozenset[str]]:
    aliases: dict[str, set[str]] = {net.net_id: set() for net in problem.nets}
    raw_connections = _list(source.get("connections"), "source.connections")
    if len(raw_connections) != sum(len(net.source_connection_names) for net in problem.nets):
        raise _refuse(
            OutputAdapterRefusalCode.SOURCE_MISMATCH,
            "source connection projection does not match the imported problem",
            "source.connections",
        )
    by_name: dict[str, set[str]] = {}
    for net in problem.nets:
        for name in net.source_connection_names:
            by_name.setdefault(name, set()).add(net.net_id)
    for position, raw_connection in enumerate(raw_connections):
        locator = f"source.connections[{position}]"
        if type(raw_connection) is not dict:
            raise _refuse(
                OutputAdapterRefusalCode.SOURCE_MISMATCH,
                "source connection is malformed",
                locator,
            )
        connection = raw_connection
        name = _text(connection.get("name"), f"{locator}.name")
        owners = by_name.get(name, set())
        for owner in owners:
            aliases[owner].add(name)
            for field_name in _CONNECTION_ALIAS_FIELDS:
                value = connection.get(field_name)
                if value is not None:
                    aliases[owner].add(_text(value, f"{locator}.{field_name}"))
            for field_name in _CONNECTION_ALIAS_LIST_FIELDS:
                values = connection.get(field_name)
                if values is None:
                    continue
                for index, value in enumerate(_list(values, f"{locator}.{field_name}")):
                    aliases[owner].add(_text(value, f"{locator}.{field_name}[{index}]"))
            for index, raw_point in enumerate(
                _list(connection.get("pointsToConnect"), f"{locator}.pointsToConnect")
            ):
                if type(raw_point) is not dict:
                    raise _refuse(
                        OutputAdapterRefusalCode.SOURCE_MISMATCH,
                        "source connection point is malformed",
                        f"{locator}.pointsToConnect[{index}]",
                    )
                identifier = raw_point.get("pointId", raw_point.get("pcb_port_id"))
                if identifier is not None:
                    aliases[owner].add(
                        _text(identifier, f"{locator}.pointsToConnect[{index}].pointId")
                    )
    return {net_id: frozenset(values) for net_id, values in aliases.items()}


def _bind_traces(
    output: dict[str, Any],
    aliases: dict[str, frozenset[str]],
    limits: SimpleRouteJsonOutputLimits,
) -> list[tuple[str, dict[str, Any]]]:
    raw_traces = _list(output.get("traces"), "output.traces")
    if not raw_traces:
        raise _refuse(
            OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
            "routed output must contain at least one trace",
            "output.traces",
        )
    if len(raw_traces) > limits.max_traces:
        raise _refuse(
            OutputAdapterRefusalCode.BUDGET_EXCEEDED,
            "trace budget exceeded",
            "output.traces",
        )
    bound: list[tuple[str, dict[str, Any]]] = []
    trace_ids: set[str] = set()
    route_item_count = 0
    for index, raw_trace in enumerate(raw_traces):
        locator = f"output.traces[{index}]"
        trace = _closed_mapping(
            raw_trace,
            allowed=_TRACE_KEYS,
            required=_TRACE_REQUIRED_KEYS,
            locator=locator,
        )
        if trace.get("type") != "pcb_trace":
            raise _refuse(
                OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
                "trace has an unsupported type",
                locator,
            )
        trace_id = _text(trace.get("pcb_trace_id"), f"{locator}.pcb_trace_id")
        if trace_id in trace_ids:
            raise _refuse(
                OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
                "trace identifiers must be unique",
                locator,
            )
        trace_ids.add(trace_id)
        replacement = trace.get("__replaces_pcb_trace_id")
        if replacement is not None:
            _text(replacement, f"{locator}.__replaces_pcb_trace_id")
        connection_name = _text(trace.get("connection_name"), f"{locator}.connection_name")
        owners = {
            net_id for net_id, net_aliases in aliases.items() if connection_name in net_aliases
        }
        if len(owners) != 1:
            raise _refuse(
                OutputAdapterRefusalCode.AMBIGUOUS_NET_OWNERSHIP,
                "trace connection does not resolve to exactly one imported net",
                locator,
            )
        owner = owners.pop()
        raw_references = trace.get("connectsTo")
        if raw_references is not None:
            reference_list = _list(raw_references, f"{locator}.connectsTo")
            if len(reference_list) > limits.max_route_items_per_trace:
                raise _refuse(
                    OutputAdapterRefusalCode.BUDGET_EXCEEDED,
                    "trace relation budget exceeded",
                    f"{locator}.connectsTo",
                )
            for position, value in enumerate(reference_list):
                _text(value, f"{locator}.connectsTo[{position}]")
        route = _list(trace.get("route"), f"{locator}.route")
        if len(route) > limits.max_route_items_per_trace:
            raise _refuse(
                OutputAdapterRefusalCode.BUDGET_EXCEEDED,
                "per-trace route item budget exceeded",
                f"{locator}.route",
            )
        route_item_count += len(route)
        if route_item_count > limits.max_total_route_items:
            raise _refuse(
                OutputAdapterRefusalCode.BUDGET_EXCEEDED,
                "total route item budget exceeded",
                "output.traces",
            )
        bound.append((owner, trace))

    owner_by_trace_id = {
        _text(trace.get("pcb_trace_id"), "output.trace.pcb_trace_id"): owner
        for owner, trace in bound
    }
    for index, (owner, trace) in enumerate(bound):
        raw_references = trace.get("connectsTo")
        if raw_references is None:
            continue
        assert isinstance(raw_references, list)
        for position, reference in enumerate(raw_references):
            assert isinstance(reference, str)
            reference_owners = {
                net_id for net_id, net_aliases in aliases.items() if reference in net_aliases
            }
            trace_owner = owner_by_trace_id.get(reference)
            if trace_owner is not None:
                reference_owners.add(trace_owner)
            if reference_owners != {owner}:
                raise _refuse(
                    OutputAdapterRefusalCode.AMBIGUOUS_NET_OWNERSHIP,
                    "trace relation is unknown or contradicts its connection",
                    f"output.traces[{index}].connectsTo[{position}]",
                )
    return bound


def _integer_nm(
    token: object,
    locator: str,
    limits: SimpleRouteJsonOutputLimits,
) -> int:
    if type(token) is not _NumberToken:
        raise _refuse(
            OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
            "millimetre value must be a JSON number",
            locator,
        )
    conversion_limits = SimpleRouteJsonImportLimits(
        max_extent_nm=limits.max_extent_nm,
        max_number_token_length=limits.max_number_token_length,
    )
    try:
        value = mm_token_to_nm(token, locator, conversion_limits)
    except SimpleRouteJsonImportError as error:
        code = (
            OutputAdapterRefusalCode.BUDGET_EXCEEDED
            if error.code.value == OutputAdapterRefusalCode.BUDGET_EXCEEDED.value
            else OutputAdapterRefusalCode.MALFORMED_DOCUMENT
        )
        raise _refuse(code, "millimetre token is invalid", locator) from error
    if value != value.to_integral_value():
        raise _refuse(
            OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
            "sub-nanometre route geometry is outside the accepted set",
            locator,
        )
    integer = int(value)
    if not -_JSON_SAFE_INTEGER <= integer <= _JSON_SAFE_INTEGER:
        raise _refuse(
            OutputAdapterRefusalCode.BUDGET_EXCEEDED,
            "converted coordinate exceeds the integer budget",
            locator,
        )
    return integer


def _layer_id(problem: ImportedProblem, value: object, locator: str) -> str:
    layer_name = _text(value, locator)
    matches = [
        layer.id for layer in problem.snapshot.content.copper_layers if layer.name == layer_name
    ]
    if len(matches) != 1:
        raise _refuse(
            OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
            "route names an undeclared layer",
            locator,
        )
    return matches[0]


def _convert_trace(
    trace: dict[str, Any],
    *,
    trace_index: int,
    problem: ImportedProblem,
    selected: ImportedNet,
    limits: SimpleRouteJsonOutputLimits,
) -> tuple[list[dict[str, object]], int, tuple[PointNM, ...]]:
    locator = f"output.traces[{trace_index}].route"
    route = _list(trace.get("route"), locator)
    if not 2 <= len(route) <= limits.max_route_items_per_trace:
        code = (
            OutputAdapterRefusalCode.BUDGET_EXCEEDED
            if len(route) > limits.max_route_items_per_trace
            else OutputAdapterRefusalCode.DISCONTINUOUS_PATH
        )
        raise _refuse(code, "route must contain a bounded continuous path", locator)
    points: list[PointNM] = []
    width_nm: int | None = None
    for item_index, raw_item in enumerate(route):
        item_locator = f"{locator}[{item_index}]"
        if type(raw_item) is not dict:
            raise _refuse(
                OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
                "route item must be an object",
                item_locator,
            )
        route_type = raw_item.get("route_type")
        if route_type != "wire":
            raise _refuse(
                OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
                "route item is outside the wire-only accepted set",
                item_locator,
            )
        item = _closed_mapping(
            raw_item,
            allowed=_WIRE_KEYS,
            required=_WIRE_REQUIRED_KEYS,
            locator=item_locator,
        )
        for field_name in ("start_pcb_port_id", "end_pcb_port_id"):
            value = item.get(field_name)
            if value is not None:
                _text(value, f"{item_locator}.{field_name}")
        layer_id = _layer_id(problem, item.get("layer"), f"{item_locator}.layer")
        if layer_id != selected.layer_id:
            raise _refuse(
                OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
                "route changes or disagrees with the imported net layer",
                item_locator,
            )
        current_width = _integer_nm(item.get("width"), f"{item_locator}.width", limits)
        if current_width < 1:
            raise _refuse(
                OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
                "route width must be positive",
                item_locator,
            )
        if width_nm is None:
            width_nm = current_width
        elif current_width != width_nm:
            raise _refuse(
                OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
                "variable-width routes are outside the accepted set",
                item_locator,
            )
        try:
            points.append(
                PointNM(
                    _integer_nm(item.get("x"), f"{item_locator}.x", limits),
                    _integer_nm(item.get("y"), f"{item_locator}.y", limits),
                )
            )
        except SimpleRouteJsonOutputAdapterError:
            raise
        except ValueError as error:
            raise _refuse(
                OutputAdapterRefusalCode.BUDGET_EXCEEDED,
                "converted point is outside Board IR limits",
                item_locator,
            ) from error
    assert width_nm is not None
    segments: list[dict[str, object]] = []
    for start, end in pairwise(points):
        if start == end or (start.x != end.x and start.y != end.y):
            raise _refuse(
                OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
                "route edge must be non-zero and rectilinear",
                locator,
            )
        segments.append(
            {
                "layer_id": selected.layer_id,
                "width_nm": width_nm,
                "start": {"x_nm": start.x, "y_nm": start.y},
                "end": {"x_nm": end.x, "y_nm": end.y},
            }
        )
    return segments, width_nm, tuple(points)


def _reverse_segments(segments: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {**segment, "start": segment["end"], "end": segment["start"]}
        for segment in reversed(segments)
    ]


def _require_complete_tree(
    paths: list[tuple[PointNM, ...]], problem: ImportedProblem, selected: ImportedNet
) -> None:
    pads = {pad.id: pad for pad in problem.snapshot.content.pads}
    required = {pads[pad_id].center for pad_id in selected.pad_ids if pad_id in pads}
    vertices = {point for path in paths for point in path}
    if len(required) != selected.pad_count or not required.issubset(vertices):
        raise _refuse(
            OutputAdapterRefusalCode.ENDPOINT_MISMATCH,
            "multi-pad output does not retain every imported endpoint",
            "output.traces",
        )
    adjacency: dict[PointNM, set[PointNM]] = {point: set() for point in vertices}
    edges: set[frozenset[PointNM]] = set()
    edge_count = 0
    for path in paths:
        for start, end in pairwise(path):
            adjacency[start].add(end)
            adjacency[end].add(start)
            edges.add(frozenset((start, end)))
            edge_count += 1
    reached: set[PointNM] = set()
    frontier = [next(iter(required))]
    while frontier:
        point = frontier.pop()
        if point in reached:
            continue
        reached.add(point)
        frontier.extend(adjacency[point] - reached)
    if not required.issubset(reached):
        raise _refuse(
            OutputAdapterRefusalCode.DISCONTINUOUS_PATH,
            "multi-pad paths do not form one connected endpoint graph",
            "output.traces",
        )
    if len(reached) != len(vertices) or edge_count != len(edges) or len(edges) != len(vertices) - 1:
        raise _refuse(
            OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
            "multi-pad output must retain one acyclic route tree",
            "output.traces",
        )


def adapt_simple_route_json_output(
    source_document: bytes,
    problem: ImportedProblem,
    output_document: bytes,
    *,
    net_id: str,
    limits: SimpleRouteJsonOutputLimits | None = None,
) -> dict[str, object]:
    """Convert one selected net from a routed SRJ result to the existing closed document.

    The returned dictionary remains untrusted and must be passed to
    :func:`verify_external_route_candidate`; this function never constructs or accepts a route
    candidate itself.
    """

    limits = limits or SimpleRouteJsonOutputLimits()
    if type(limits) is not SimpleRouteJsonOutputLimits:
        raise _refuse(
            OutputAdapterRefusalCode.BUDGET_EXCEEDED,
            "adapter limits are invalid",
            "limits",
        )
    source = _parse_document(source_document, limits, locator="source")
    output = _parse_document(output_document, limits, locator="output")
    checked_problem = _source_is_exact(source_document, problem)
    selected = _selected_net(checked_problem, net_id)

    expected_keys = frozenset(source) | {"traces"}
    if len(output) != len(expected_keys) or not all(key in output for key in expected_keys):
        raise _refuse(
            OutputAdapterRefusalCode.SOURCE_MISMATCH,
            "output root does not preserve the source field set",
        )
    if any(output[key] != value for key, value in source.items() if key != "traces"):
        raise _refuse(
            OutputAdapterRefusalCode.SOURCE_MISMATCH,
            "output changes the source routing problem",
        )

    aliases = _aliases_by_net(source, checked_problem)
    bound = _bind_traces(output, aliases, limits)
    selected_traces = [trace for owner, trace in bound if owner == selected.net_id]
    if not selected_traces:
        raise _refuse(
            OutputAdapterRefusalCode.AMBIGUOUS_NET_OWNERSHIP,
            "routed output has no trace for the selected net",
            "output.traces",
        )
    converted = [
        _convert_trace(
            trace,
            trace_index=index,
            problem=checked_problem,
            selected=selected,
            limits=limits,
        )
        for index, trace in enumerate(selected_traces)
    ]
    widths = {width for _, width, _ in converted}
    if len(widths) != 1:
        raise _refuse(
            OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
            "multi-path output must use one width",
            "output.traces",
        )
    pads = {pad.id: pad for pad in checked_problem.snapshot.content.pads}
    start_pad_id = selected.pad_ids[0]
    end_pad_id = selected.pad_ids[-1]
    if start_pad_id not in pads or end_pad_id not in pads:
        raise _refuse(
            OutputAdapterRefusalCode.SOURCE_MISMATCH,
            "imported endpoint projection is incomplete",
            "source",
        )
    common: dict[str, object] = {
        "problem_revision": checked_problem.snapshot.snapshot_digest,
        "start_pad_id": start_pad_id,
        "end_pad_id": end_pad_id,
        "vias": [],
    }
    if selected.pad_count == 2:
        if len(converted) != 1:
            raise _refuse(
                OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
                "two-pad output must be one continuous trace",
                "output.traces",
            )
        segments, _, points = converted[0]
        start = pads[start_pad_id].center
        end = pads[end_pad_id].center
        if points[0] == end and points[-1] == start:
            segments = _reverse_segments(segments)
            points = tuple(reversed(points))
        if points[0] != start or points[-1] != end:
            raise _refuse(
                OutputAdapterRefusalCode.ENDPOINT_MISMATCH,
                "route does not terminate on the selected imported endpoints",
                "output.traces",
            )
        return {"schema": EXTERNAL_ROUTE_CANDIDATE_SCHEMA, **common, "segments": segments}

    paths = [points for _, _, points in converted]
    if len(paths) > selected.pad_count - 1:
        raise _refuse(
            OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
            "multi-pad output has more paths than a tree can retain",
            "output.traces",
        )
    _require_complete_tree(paths, checked_problem, selected)
    return {
        "schema": EXTERNAL_ROUTE_PATCH_SCHEMA,
        **common,
        "paths": [{"segments": segments} for segments, _, _ in converted],
    }


__all__ = [
    "OutputAdapterRefusalCode",
    "SimpleRouteJsonOutputAdapterError",
    "SimpleRouteJsonOutputLimits",
    "adapt_simple_route_json_output",
]
