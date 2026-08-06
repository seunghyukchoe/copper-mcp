"""Import tscircuit SimpleRouteJson routing problems as Board IR snapshots.

SimpleRouteJson ("SRJ") is the interchange the tscircuit autorouting ecosystem uses to state a
routing problem: a layer count, a minimum trace width, a rectangular board ``bounds``, a list of
rectangular or oval ``obstacles``, and a list of ``connections`` naming the points that must end
up on one net.  This module converts one such document into an ordinary
:class:`~copper_mcp.board_ir.BoardIRSnapshot` plus the routing requests it implies, so an external
corpus reaches the deterministic router through exactly the same canonical verification and typed
refusals as a KiCad board does.

This is a benchmark seam, not a tool surface.  It has no MCP exposure, no apply authority, and no
file mutation; it produces a snapshot and a list of requests and nothing else.

Direction of error
------------------

Every mapping here is chosen so an imported problem is never *easier* than the source document.
The single invariant, stated once and enforced everywhere below, is:

    **Every imported copper rectangle contains the source shape it stands for.**

Concretely:

* A ``rect`` obstacle maps to its own extent, exactly, whenever its millimetre tokens are exact at
  nanometre resolution — which is every clean SRJ document.  When a token carries sub-nanometre
  digits (an SRJ produced by a JavaScript pipeline is full of ``2.9000000000000004``), the
  rectangle's low edges are floored and its high edges ceiled in **exact decimal arithmetic**, so
  the mapping is outward by at most one nanometre per edge and never inward.
* An ``oval`` obstacle blocks as its axis-aligned bounding rectangle, which strictly contains the
  oval.
* An obstacle that is a net's own copper becomes a Board IR pad over that same outward rectangle,
  keeping ``oval`` as an oval pad so its *attachment* core stays the inscribed central rectangle
  rather than the bounding box.  A pad is the one place the invariant costs something: an
  outward-rounded pad can offer at most one nanometre of attachment copper the source document
  did not state.  That is recorded as ``max_outward_rounding_nm`` rather than hidden, and it is
  four orders of magnitude below the imported clearance.
* An obstacle naming any layer that the declared stack does not contain blocks on **every**
  declared copper layer.  Widening is conservative; dropping the obstacle would not be.
* The board outline rounds the other way — inward — because outline is routing *room*, so
  rounding it outward would hand the router area the document never gave it.
* Anything the adapter cannot represent — an unknown obstacle type, a connection point that no
  obstacle anchors, an obstacle claimed by two different nets — refuses the whole document with a
  typed :class:`ImportRefusalCode`.  No element is ever silently dropped.

Units
-----

SRJ coordinates are millimetres written as JSON numbers.  The adapter parses the document with
``parse_float``/``parse_int`` hooks so every number arrives as its **literal source token**, never
as a float, and converts that token through :class:`decimal.Decimal` at exactly
``1000000`` nanometres per millimetre.  For a token with at most six fractional digits and no
exponent the result is identical to :func:`copper_mcp.board_ir.mm_to_nm`, the rule the KiCad
adapter uses; :func:`mm_token_to_nm` is tested against it directly.  ``NaN``, ``Infinity`` and
``-Infinity`` are rejected before conversion, a token longer than
:attr:`SimpleRouteJsonImportLimits.max_number_token_length` is rejected as a budget, and every
converted value is bounded by :attr:`SimpleRouteJsonImportLimits.max_extent_nm`.  Because no
binary floating point participates, ``0.1`` mm is exactly ``100000`` nm rather than whatever the
nearest double happens to be.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException, localcontext
from enum import StrEnum
from typing import Any

from copper_mcp.board_ir import (
    BoardIRSnapshot,
    ConstraintSet,
    Footprint,
    FootprintSide,
    Keepout,
    Layer,
    Net,
    NetClass,
    NetClassAssignment,
    OutlineContour,
    Pad,
    PadKind,
    PadShape,
    PointNM,
    Ring,
    SourceInfo,
    make_content,
    make_snapshot,
)
from copper_mcp.board_ir.validation import BoardIRValidationError

#: Recorded in every artifact so a replay can tell which mapping produced a number.
SIMPLE_ROUTE_JSON_ADAPTER_VERSION = "simple-route-json-import-v1"

#: Human-readable statement of the millimetre rule, copied verbatim into benchmark artifacts.
MM_TO_NM_RULE = (
    "SimpleRouteJson millimetre values are read as their literal JSON tokens, never as floats, "
    "and converted through decimal.Decimal at exactly 1000000 nanometres per millimetre. A token "
    "with at most six fractional digits and no exponent converts identically to "
    "copper_mcp.board_ir.mm_to_nm. Sub-nanometre residue is resolved by rounding copper outward "
    "(floor the low edge, ceil the high edge) and the board outline inward, both in exact decimal "
    "arithmetic, so every imported copper rectangle contains the source shape and the outline "
    "never grows."
)

#: Exact nanometres per millimetre, as a decimal scale factor.
_NM_PER_MM = Decimal(1_000_000)
#: Decimal precision high enough that no coordinate arithmetic here is ever inexact.
_DECIMAL_PRECISION = 80

_TOP_LAYER = "top"
_BOTTOM_LAYER = "bottom"
_SUPPORTED_OBSTACLE_TYPES = frozenset({"rect", "oval"})


class ImportRefusalCode(StrEnum):
    """Stable taxonomy for an SRJ document this adapter declines to represent."""

    MALFORMED_DOCUMENT = "malformed_document"
    BUDGET_EXCEEDED = "budget_exceeded"
    UNSUPPORTED_UNIT = "unsupported_unit"
    UNSUPPORTED_LAYER_COUNT = "unsupported_layer_count"
    UNSUPPORTED_OBSTACLE = "unsupported_obstacle"
    UNSUPPORTED_CONNECTION_POINT = "unsupported_connection_point"
    UNANCHORED_CONNECTION_POINT = "unanchored_connection_point"
    AMBIGUOUS_NET_OWNERSHIP = "ambiguous_net_ownership"
    DEGENERATE_GEOMETRY = "degenerate_geometry"
    PAD_OUTSIDE_BOUNDS = "pad_outside_bounds"
    BOARD_IR_REJECTED = "board_ir_rejected"


@dataclass(frozen=True, slots=True)
class SimpleRouteJsonImportError(ValueError):
    """One typed, non-echoing refusal for a document the adapter will not represent."""

    code: ImportRefusalCode
    message: str
    locator: str = "document"

    def __str__(self) -> str:
        return f"{self.code} at {self.locator}: {self.message}"


@dataclass(frozen=True, slots=True)
class SimpleRouteJsonImportLimits:
    """Closed budgets applied to an untrusted external document before any conversion."""

    max_document_bytes: int = 4_000_000
    max_layers: int = 8
    max_obstacles: int = 2_048
    max_connections: int = 512
    max_points_per_connection: int = 512
    max_extent_nm: int = 1_000_000_000
    max_number_token_length: int = 40

    def __post_init__(self) -> None:
        for name in (
            "max_document_bytes",
            "max_layers",
            "max_obstacles",
            "max_connections",
            "max_points_per_connection",
            "max_extent_nm",
            "max_number_token_length",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class ImportPolicy:
    """Constraint values SimpleRouteJson does not carry, declared by the caller.

    SRJ states ``minTraceWidth`` and nothing else.  Clearance, via diameter and via drill are
    therefore benchmark policy rather than corpus data, so they are named here, recorded in the
    artifact, and folded into the imported snapshot's constraint digest.
    """

    clearance_nm: int = 200_000
    via_diameter_nm: int = 600_000
    via_drill_nm: int = 300_000

    def __post_init__(self) -> None:
        if self.clearance_nm < 0 or self.via_drill_nm < 1 or self.via_diameter_nm < 1:
            raise ValueError("import policy constraints must be non-negative integers")
        if self.via_drill_nm >= self.via_diameter_nm:
            raise ValueError("via drill must be smaller than via diameter")

    def payload(self) -> dict[str, int]:
        """Return the recorded configuration projection of this policy."""

        return {
            "clearance_nm": self.clearance_nm,
            "via_diameter_nm": self.via_diameter_nm,
            "via_drill_nm": self.via_drill_nm,
        }


DEFAULT_IMPORT_POLICY = ImportPolicy()


@dataclass(frozen=True, slots=True)
class ImportedNet:
    """One electrical net recovered from the document's connections."""

    net_id: str
    layer_id: str
    pad_ids: tuple[str, ...]
    source_connection_names: tuple[str, ...]
    #: Provable lower bound on any rectilinear tree touching every pad of this net.
    pad_gap_lower_bound_nm: int
    #: Exact pad-centre Manhattan distance, present only for a two-pad net.
    two_pin_centre_manhattan_nm: int | None

    @property
    def pad_count(self) -> int:
        """Return how many pads this net must join."""

        return len(self.pad_ids)


@dataclass(frozen=True, slots=True)
class ImportStatistics:
    """Exactly what the conversion did, so a report never has to infer it."""

    layer_count: int
    obstacle_count: int
    rect_obstacles: int
    oval_obstacles: int
    keepouts: int
    pads: int
    connection_count: int
    connection_point_count: int
    net_count: int
    routable_net_count: int
    #: Obstacles whose blocking geometry strictly contains the source shape.
    over_approximated_obstacles: int
    #: Obstacles widened to the whole stack because they named an undeclared layer.
    layer_widened_obstacles: int
    #: Largest outward edge movement in nanometres; zero for a document exact at nm resolution.
    max_outward_rounding_nm: int

    def payload(self) -> dict[str, int]:
        """Return the recorded projection of these counts."""

        return {
            "connection_count": self.connection_count,
            "connection_point_count": self.connection_point_count,
            "keepouts": self.keepouts,
            "layer_count": self.layer_count,
            "layer_widened_obstacles": self.layer_widened_obstacles,
            "max_outward_rounding_nm": self.max_outward_rounding_nm,
            "net_count": self.net_count,
            "obstacle_count": self.obstacle_count,
            "over_approximated_obstacles": self.over_approximated_obstacles,
            "oval_obstacles": self.oval_obstacles,
            "pads": self.pads,
            "rect_obstacles": self.rect_obstacles,
            "routable_net_count": self.routable_net_count,
        }


@dataclass(frozen=True, slots=True)
class ImportedProblem:
    """One SRJ document converted into a verified snapshot and its routing work."""

    name: str
    document_sha256: str
    snapshot: BoardIRSnapshot
    nets: tuple[ImportedNet, ...]
    statistics: ImportStatistics
    track_width_nm: int
    policy: ImportPolicy
    adapter_version: str = SIMPLE_ROUTE_JSON_ADAPTER_VERSION

    @property
    def routable_nets(self) -> tuple[ImportedNet, ...]:
        """Return the nets that carry at least two pads and so imply a routing request."""

        return tuple(net for net in self.nets if net.pad_count >= 2)


def _refuse(
    code: ImportRefusalCode, message: str, locator: str = "document"
) -> SimpleRouteJsonImportError:
    return SimpleRouteJsonImportError(code, message, locator)


def _reject_constant(token: str) -> Any:
    raise _refuse(
        ImportRefusalCode.UNSUPPORTED_UNIT,
        "SimpleRouteJson coordinates must be finite decimal millimetres",
        "number",
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _refuse(
                ImportRefusalCode.MALFORMED_DOCUMENT,
                "SimpleRouteJson objects must not repeat a key",
                key,
            )
        result[key] = value
    return result


def mm_token_to_nm(token: object, locator: str, limits: SimpleRouteJsonImportLimits) -> Decimal:
    """Convert one literal millimetre token to exact nanometres, without rounding.

    ``token`` is the raw JSON number text preserved by the parse hooks, never a float, so the
    returned :class:`~decimal.Decimal` is the document's value and not an approximation of it.
    Rounding, where a value needs it, happens later and in a direction chosen per geometry role.
    A value outside ``+/- limits.max_extent_nm`` is refused rather than clamped, because a
    silently clamped coordinate is a different problem, not a harder one.
    """

    if not isinstance(token, str):
        raise _refuse(
            ImportRefusalCode.UNSUPPORTED_UNIT,
            "a millimetre value must be a JSON number",
            locator,
        )
    if len(token) > limits.max_number_token_length:
        raise _refuse(
            ImportRefusalCode.BUDGET_EXCEEDED, "number token exceeds its length budget", locator
        )
    # The context manager only widens precision; every refusal is raised after it closes, so a
    # typed refusal never unwinds through a generator-based context manager on its way out.
    value: Decimal | None = None
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        try:
            candidate = Decimal(token) * _NM_PER_MM
        except (DecimalException, ValueError, ArithmeticError):
            candidate = None
        if candidate is not None and candidate.is_finite():
            value = +candidate
    if value is None:
        raise _refuse(
            ImportRefusalCode.UNSUPPORTED_UNIT,
            "millimetre token is not a finite decimal number",
            locator,
        )
    if abs(value) > limits.max_extent_nm:
        raise _refuse(
            ImportRefusalCode.BUDGET_EXCEEDED,
            "coordinate is outside the supported board extent",
            locator,
        )
    return value


def _floor_nm(value: Decimal) -> int:
    """Return the greatest integer nanometre not above ``value``."""

    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        return int(value.to_integral_value(rounding="ROUND_FLOOR"))


def _ceil_nm(value: Decimal) -> int:
    """Return the least integer nanometre not below ``value``."""

    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        return int(value.to_integral_value(rounding="ROUND_CEILING"))


def _outward_rectangle(
    centre_x: Decimal, centre_y: Decimal, width: Decimal, height: Decimal
) -> tuple[tuple[int, int, int, int], int]:
    """Return the smallest integer rectangle containing the exact one, and its rounding cost.

    The returned cost is the largest number of nanometres any single edge moved outward, which is
    zero for a document whose tokens are exact at nanometre resolution.  It is the quantity the
    benchmark reports so that "over-approximated" is a measured claim rather than an assertion.
    """

    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        half_x = width / 2
        half_y = height / 2
        exact = (centre_x - half_x, centre_y - half_y, centre_x + half_x, centre_y + half_y)
    rectangle = (
        _floor_nm(exact[0]),
        _floor_nm(exact[1]),
        _ceil_nm(exact[2]),
        _ceil_nm(exact[3]),
    )
    cost = max(
        _ceil_nm(exact[0] - rectangle[0]),
        _ceil_nm(exact[1] - rectangle[1]),
        _ceil_nm(Decimal(rectangle[2]) - exact[2]),
        _ceil_nm(Decimal(rectangle[3]) - exact[3]),
    )
    return rectangle, max(cost, 0)


def _even_rectangle(rectangle: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Widen a rectangle outward until a Board IR pad can carry it centred and exact.

    A Board IR pad is a centre plus a size, so its extent is symmetric about an integer point.
    Growing the high edge by one nanometre when a span is odd keeps the pad a superset of the
    rectangle; shrinking the low edge would too, but growing keeps every adjustment in the same
    outward direction as every other rounding decision in this module.
    """

    min_x, min_y, max_x, max_y = rectangle
    if (max_x - min_x) % 2 != 0:
        max_x += 1
    if (max_y - min_y) % 2 != 0:
        max_y += 1
    return min_x, min_y, max_x, max_y


def _require_mapping(value: object, locator: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _refuse(ImportRefusalCode.MALFORMED_DOCUMENT, "expected a JSON object", locator)
    return value


def _require_list(value: object, locator: str) -> list[Any]:
    if not isinstance(value, list):
        raise _refuse(ImportRefusalCode.MALFORMED_DOCUMENT, "expected a JSON array", locator)
    return value


def _require_text(value: object, locator: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise _refuse(ImportRefusalCode.MALFORMED_DOCUMENT, "expected a bounded string", locator)
    return value


def _layer_ids(layer_count: int) -> tuple[str, ...]:
    """Return the tscircuit layer names of an ``n``-layer stack, in physical order."""

    if layer_count == 1:
        return (_TOP_LAYER,)
    inner = tuple(f"inner{index}" for index in range(1, layer_count - 1))
    return (_TOP_LAYER, *inner, _BOTTOM_LAYER)


@dataclass(slots=True)
class _RawObstacle:
    index: int
    kind: str
    #: Smallest integer-nanometre rectangle containing the source shape.
    rectangle: tuple[int, int, int, int]
    #: Largest outward edge movement in nanometres; zero for an exact document.
    rounding_nm: int
    layer_ids: tuple[str, ...]
    widened: bool
    connected_to: tuple[str, ...]
    owner: int | None = None


@dataclass(slots=True)
class _RawPoint:
    connection_index: int
    point_id: str
    x: Decimal
    y: Decimal
    layer_id: str


@dataclass(slots=True)
class _NetBuilder:
    connection_indices: list[int] = field(default_factory=list)
    obstacle_indices: list[int] = field(default_factory=list)


class _UnionFind:
    """Deterministic union-find over connection indices."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[max(left_root, right_root)] = min(left_root, right_root)


def _parse_document(document: bytes, limits: SimpleRouteJsonImportLimits) -> dict[str, Any]:
    if not isinstance(document, bytes | bytearray):
        raise _refuse(ImportRefusalCode.MALFORMED_DOCUMENT, "document must be raw bytes")
    if len(document) > limits.max_document_bytes:
        raise _refuse(ImportRefusalCode.BUDGET_EXCEEDED, "document exceeds the byte budget")
    try:
        text = bytes(document).decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise _refuse(
            ImportRefusalCode.MALFORMED_DOCUMENT, "document is not strict UTF-8"
        ) from error
    try:
        value = json.loads(
            text,
            parse_float=str,
            parse_int=str,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except json.JSONDecodeError as error:
        raise _refuse(ImportRefusalCode.MALFORMED_DOCUMENT, "document is not valid JSON") from error
    return _require_mapping(value, "root")


def _read_layers(
    root: dict[str, Any], limits: SimpleRouteJsonImportLimits
) -> tuple[tuple[str, ...], tuple[Layer, ...]]:
    raw = root.get("layerCount")
    if not isinstance(raw, str) or not raw.isdigit():
        raise _refuse(
            ImportRefusalCode.UNSUPPORTED_LAYER_COUNT,
            "layerCount must be a positive JSON integer",
            "layerCount",
        )
    layer_count = int(raw)
    if not 1 <= layer_count <= limits.max_layers:
        raise _refuse(
            ImportRefusalCode.UNSUPPORTED_LAYER_COUNT,
            "layerCount is outside the supported stack range",
            "layerCount",
        )
    names = _layer_ids(layer_count)
    layers = tuple(
        Layer(id=f"layer:{name}", name=name, index=index, kind="signal")
        for index, name in enumerate(names)
    )
    return names, layers


def _read_bounds(
    root: dict[str, Any], limits: SimpleRouteJsonImportLimits
) -> tuple[int, int, int, int]:
    """Return the board outline rectangle, rounded *inward* to integer nanometres.

    Outline is routing room rather than copper, so it is the one rectangle here that rounds
    inward: an outward-rounded outline would hand the router area the document never granted.
    """

    bounds = _require_mapping(root.get("bounds"), "bounds")
    values: dict[str, Decimal] = {}
    for key in ("minX", "maxX", "minY", "maxY"):
        if key not in bounds:
            raise _refuse(
                ImportRefusalCode.MALFORMED_DOCUMENT, "bounds is incomplete", f"bounds.{key}"
            )
        values[key] = mm_token_to_nm(bounds[key], f"bounds.{key}", limits)
    rectangle = (
        _ceil_nm(values["minX"]),
        _ceil_nm(values["minY"]),
        _floor_nm(values["maxX"]),
        _floor_nm(values["maxY"]),
    )
    if rectangle[0] >= rectangle[2] or rectangle[1] >= rectangle[3]:
        raise _refuse(ImportRefusalCode.DEGENERATE_GEOMETRY, "board bounds have no area", "bounds")
    return rectangle


def _read_obstacles(
    root: dict[str, Any],
    stack: tuple[str, ...],
    limits: SimpleRouteJsonImportLimits,
) -> list[_RawObstacle]:
    raw_obstacles = _require_list(root.get("obstacles", []), "obstacles")
    if len(raw_obstacles) > limits.max_obstacles:
        raise _refuse(ImportRefusalCode.BUDGET_EXCEEDED, "obstacle budget exceeded", "obstacles")
    obstacles: list[_RawObstacle] = []
    for index, entry in enumerate(raw_obstacles):
        locator = f"obstacles[{index}]"
        item = _require_mapping(entry, locator)
        kind = _require_text(item.get("type"), f"{locator}.type", maximum=64)
        if kind not in _SUPPORTED_OBSTACLE_TYPES:
            raise _refuse(
                ImportRefusalCode.UNSUPPORTED_OBSTACLE,
                "the adapter represents rect and oval obstacles only",
                locator,
            )
        centre = _require_mapping(item.get("center"), f"{locator}.center")
        centre_x = mm_token_to_nm(centre.get("x"), f"{locator}.center.x", limits)
        centre_y = mm_token_to_nm(centre.get("y"), f"{locator}.center.y", limits)
        width = mm_token_to_nm(item.get("width"), f"{locator}.width", limits)
        height = mm_token_to_nm(item.get("height"), f"{locator}.height", limits)
        if width <= 0 or height <= 0:
            raise _refuse(
                ImportRefusalCode.DEGENERATE_GEOMETRY,
                "an obstacle extent must be positive",
                locator,
            )
        rectangle, rounding_nm = _outward_rectangle(centre_x, centre_y, width, height)
        named = _require_list(item.get("layers", []), f"{locator}.layers")
        layer_names = tuple(
            _require_text(name, f"{locator}.layers[{position}]", maximum=64)
            for position, name in enumerate(named)
        )
        if not layer_names:
            raise _refuse(
                ImportRefusalCode.UNSUPPORTED_OBSTACLE,
                "an obstacle must name at least one layer",
                locator,
            )
        # Widening rather than dropping: an obstacle that names a layer this stack does not
        # declare blocks the whole stack.  The alternative — keeping only the names that
        # resolve — would quietly discard the parts of the obstacle we cannot place.
        widened = any(name not in stack for name in layer_names)
        resolved = stack if widened else tuple(dict.fromkeys(layer_names))
        connected = tuple(
            _require_text(value, f"{locator}.connectedTo[{position}]", maximum=256)
            for position, value in enumerate(
                _require_list(item.get("connectedTo", []), f"{locator}.connectedTo")
            )
        )
        obstacles.append(
            _RawObstacle(
                index=index,
                kind=kind,
                rectangle=rectangle,
                rounding_nm=rounding_nm,
                layer_ids=tuple(f"layer:{name}" for name in resolved),
                widened=widened,
                connected_to=connected,
            )
        )
    return obstacles


def _read_points(
    root: dict[str, Any],
    stack: tuple[str, ...],
    limits: SimpleRouteJsonImportLimits,
) -> tuple[list[_RawPoint], list[str]]:
    raw_connections = _require_list(root.get("connections", []), "connections")
    if len(raw_connections) > limits.max_connections:
        raise _refuse(
            ImportRefusalCode.BUDGET_EXCEEDED, "connection budget exceeded", "connections"
        )
    points: list[_RawPoint] = []
    names: list[str] = []
    for index, entry in enumerate(raw_connections):
        locator = f"connections[{index}]"
        item = _require_mapping(entry, locator)
        names.append(_require_text(item.get("name"), f"{locator}.name"))
        raw_points = _require_list(item.get("pointsToConnect", []), f"{locator}.pointsToConnect")
        if not raw_points:
            # Refused rather than skipped: a connection that names no point cannot be represented
            # as a net, and quietly passing over it would be the one thing this adapter never does.
            raise _refuse(
                ImportRefusalCode.MALFORMED_DOCUMENT,
                "a connection must name at least one point",
                locator,
            )
        if len(raw_points) > limits.max_points_per_connection:
            raise _refuse(
                ImportRefusalCode.BUDGET_EXCEEDED, "connection point budget exceeded", locator
            )
        for position, raw_point in enumerate(raw_points):
            point_locator = f"{locator}.pointsToConnect[{position}]"
            point = _require_mapping(raw_point, point_locator)
            layer_name = _require_text(point.get("layer"), f"{point_locator}.layer", maximum=64)
            if layer_name not in stack:
                raise _refuse(
                    ImportRefusalCode.UNSUPPORTED_CONNECTION_POINT,
                    "a connection point names a layer outside the declared stack",
                    point_locator,
                )
            identifier = point.get("pointId", point.get("pcb_port_id"))
            points.append(
                _RawPoint(
                    connection_index=index,
                    point_id=_require_text(identifier, f"{point_locator}.pointId", maximum=256),
                    x=mm_token_to_nm(point.get("x"), f"{point_locator}.x", limits),
                    y=mm_token_to_nm(point.get("y"), f"{point_locator}.y", limits),
                    layer_id=f"layer:{layer_name}",
                )
            )
    return points, names


def _assign_nets(
    points: list[_RawPoint], obstacles: list[_RawObstacle], connection_count: int
) -> dict[int, _NetBuilder]:
    """Group connections into electrical nets and bind each obstacle to at most one.

    Two connections that name the same point are one net, because a Board IR pad belongs to
    exactly one net and the document has just said the same copper serves both.  An obstacle is a
    net's own copper when its ``connectedTo`` names one of that net's points; an obstacle claimed
    by two different nets means the document's connectivity and its connection list disagree, and
    that is a refusal rather than a guess.

    ``connection_count`` is the document's own count rather than one derived from ``points``, so
    every connection reaches a builder even if the last ones contribute nothing distinguishing.
    """

    union = _UnionFind(connection_count)
    by_point_id: dict[str, list[int]] = {}
    for point in points:
        by_point_id.setdefault(point.point_id, []).append(point.connection_index)
    for connection_indices in by_point_id.values():
        for other in connection_indices[1:]:
            union.union(connection_indices[0], other)

    point_root: dict[str, int] = {
        point_id: union.find(indices[0]) for point_id, indices in by_point_id.items()
    }
    builders: dict[int, _NetBuilder] = {}
    for index in range(connection_count):
        builders.setdefault(union.find(index), _NetBuilder()).connection_indices.append(index)

    for obstacle in obstacles:
        claimants = {
            point_root[identifier]
            for identifier in obstacle.connected_to
            if identifier in point_root
        }
        if len(claimants) > 1:
            raise _refuse(
                ImportRefusalCode.AMBIGUOUS_NET_OWNERSHIP,
                "one obstacle is claimed by two different imported nets",
                f"obstacles[{obstacle.index}]",
            )
        if claimants:
            owner = claimants.pop()
            obstacle.owner = owner
            builders[owner].obstacle_indices.append(obstacle.index)
    return builders


def _anchor_points(
    points: list[_RawPoint],
    obstacles: list[_RawObstacle],
    builders: dict[int, _NetBuilder],
    union_of: dict[int, int],
) -> None:
    """Refuse a connection point that no obstacle of its own net covers.

    Without this the adapter would happily emit a net whose requested endpoint is nowhere near
    the copper it imported, and the resulting measurement would describe a problem the corpus
    never stated.
    """

    for point in points:
        root = union_of[point.connection_index]
        covered = any(
            obstacles[index].rectangle[0] <= point.x <= obstacles[index].rectangle[2]
            and obstacles[index].rectangle[1] <= point.y <= obstacles[index].rectangle[3]
            and point.layer_id in obstacles[index].layer_ids
            for index in builders[root].obstacle_indices
        )
        if not covered:
            raise _refuse(
                ImportRefusalCode.UNANCHORED_CONNECTION_POINT,
                "a connection point is not covered by any obstacle its own net owns",
                f"connections[{point.connection_index}]",
            )


def import_simple_route_json(
    name: str,
    document: bytes,
    *,
    policy: ImportPolicy = DEFAULT_IMPORT_POLICY,
    limits: SimpleRouteJsonImportLimits | None = None,
) -> ImportedProblem:
    """Convert one SimpleRouteJson document into a verified Board IR routing problem.

    Raises :class:`SimpleRouteJsonImportError` with a typed
    :class:`ImportRefusalCode` for any document the adapter will not represent.  It never returns
    a partially converted problem: an element it cannot map refuses the whole document.
    """

    limits = limits or SimpleRouteJsonImportLimits()
    _require_text(name, "name", maximum=128)
    digest = hashlib.sha256(bytes(document)).hexdigest()
    root = _parse_document(document, limits)
    stack, layers = _read_layers(root, limits)
    min_x, min_y, max_x, max_y = _read_bounds(root, limits)
    # Track width rounds *up*: a wider trace is the harder problem, so a rounded width can never
    # make an imported board easier to route than the document stated.
    track_width_nm = _ceil_nm(mm_token_to_nm(root.get("minTraceWidth"), "minTraceWidth", limits))
    if track_width_nm < 1:
        raise _refuse(
            ImportRefusalCode.DEGENERATE_GEOMETRY,
            "minTraceWidth must be a positive millimetre value",
            "minTraceWidth",
        )
    obstacles = _read_obstacles(root, stack, limits)
    points, connection_names = _read_points(root, stack, limits)
    builders = _assign_nets(points, obstacles, len(connection_names))
    union_of: dict[int, int] = {
        index: root_index
        for root_index, builder in builders.items()
        for index in builder.connection_indices
    }
    _anchor_points(points, obstacles, builders, union_of)

    net_ids: dict[int, str] = {}
    nets: list[Net] = []
    assignments: list[NetClassAssignment] = []
    for ordinal, root_index in enumerate(sorted(builders)):
        if not builders[root_index].obstacle_indices:
            continue
        net_id = f"net:n{ordinal}"
        net_ids[root_index] = net_id
        nets.append(Net(id=net_id, name=f"srj-net-{ordinal}"))
        assignments.append(NetClassAssignment(net_id=net_id, net_class_id="class:srj-default"))

    pads: list[Pad] = []
    footprints: list[Footprint] = []
    keepouts: list[Keepout] = []
    pad_rects: dict[str, tuple[int, int, int, int]] = {}
    pads_by_net: dict[str, list[str]] = {}
    over_approximated = 0
    max_rounding_nm = 0
    for obstacle in obstacles:
        rectangle = obstacle.rectangle
        owner_net = net_ids.get(obstacle.owner) if obstacle.owner is not None else None
        if obstacle.kind == "oval" or obstacle.rounding_nm > 0:
            over_approximated += 1
        max_rounding_nm = max(max_rounding_nm, obstacle.rounding_nm)
        if owner_net is None:
            keepouts.append(
                Keepout(
                    id=f"keepout:o{obstacle.index}",
                    layer_ids=obstacle.layer_ids,
                    boundary=_rectangle_ring(rectangle),
                    prohibit_tracks=True,
                    prohibit_vias=True,
                    prohibit_pads=False,
                    prohibit_zones=True,
                    prohibit_footprints=False,
                )
            )
            continue
        # A pad is a centre plus a size, so it needs an even span; the widening is outward like
        # every other rounding here.
        pad_rectangle = _even_rectangle(rectangle)
        max_rounding_nm = max(
            max_rounding_nm,
            pad_rectangle[2] - rectangle[2],
            pad_rectangle[3] - rectangle[3],
        )
        if not (min_x <= pad_rectangle[0] and pad_rectangle[2] <= max_x):
            raise _refuse(
                ImportRefusalCode.PAD_OUTSIDE_BOUNDS,
                "a net's own copper extends outside the declared board bounds",
                f"obstacles[{obstacle.index}]",
            )
        if not (min_y <= pad_rectangle[1] and pad_rectangle[3] <= max_y):
            raise _refuse(
                ImportRefusalCode.PAD_OUTSIDE_BOUNDS,
                "a net's own copper extends outside the declared board bounds",
                f"obstacles[{obstacle.index}]",
            )
        pad_id = f"pad:o{obstacle.index}"
        centre = PointNM(
            (pad_rectangle[0] + pad_rectangle[2]) // 2,
            (pad_rectangle[1] + pad_rectangle[3]) // 2,
        )
        # An oval pad's Board IR blocking extent is its bounding box while its attachment core is
        # the inscribed central rectangle, so the two directions of error stay correct without
        # the adapter having to model the shape itself.
        shape = PadShape.OVAL if obstacle.kind == "oval" else PadShape.RECT
        pads.append(
            Pad(
                id=pad_id,
                net_id=owner_net,
                center=centre,
                rotation_udeg=0,
                shape=shape,
                kind=PadKind.SMD,
                size_x_nm=pad_rectangle[2] - pad_rectangle[0],
                size_y_nm=pad_rectangle[3] - pad_rectangle[1],
                roundrect_radius_nm=None,
                drill_x_nm=None,
                drill_y_nm=None,
                layer_ids=obstacle.layer_ids,
            )
        )
        footprints.append(
            Footprint(
                id=f"footprint:o{obstacle.index}",
                origin=centre,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=(pad_id,),
            )
        )
        pad_rects[pad_id] = pad_rectangle
        pads_by_net.setdefault(owner_net, []).append(pad_id)

    net_class = NetClass(
        id="class:srj-default",
        name="SimpleRouteJson default",
        clearance_nm=policy.clearance_nm,
        track_width_nm=track_width_nm,
        via_diameter_nm=policy.via_diameter_nm,
        via_drill_nm=policy.via_drill_nm,
    )
    outline = OutlineContour(
        id="contour:bounds", outer=_rectangle_ring((min_x, min_y, max_x, max_y))
    )
    source = SourceInfo(
        format="simple-route-json",
        revision=f"sha256:{digest}",
        format_version="tscircuit-simple-route-json",
        generator=SIMPLE_ROUTE_JSON_ADAPTER_VERSION,
    )
    try:
        content = make_content(
            source=source,
            outline=(outline,),
            copper_layers=layers,
            nets=tuple(nets),
            constraints=ConstraintSet(net_classes=(net_class,), assignments=tuple(assignments)),
            footprints=tuple(footprints),
            pads=tuple(pads),
            keepouts=tuple(keepouts),
        )
        snapshot = make_snapshot(content)
    except (BoardIRValidationError, ValueError) as error:
        raise _refuse(
            ImportRefusalCode.BOARD_IR_REJECTED,
            "the converted board was rejected by Board IR validation",
        ) from error

    imported_nets = tuple(
        _build_net(
            net_id=net_ids[root_index],
            root_index=root_index,
            builder=builders[root_index],
            connection_names=connection_names,
            pad_ids=tuple(sorted(pads_by_net.get(net_ids[root_index], ()))),
            pad_rects=pad_rects,
            points=points,
            union_of=union_of,
            default_layer_id=layers[0].id,
        )
        for root_index in sorted(builders)
        if root_index in net_ids
    )
    statistics = ImportStatistics(
        layer_count=len(layers),
        obstacle_count=len(obstacles),
        rect_obstacles=sum(1 for item in obstacles if item.kind == "rect"),
        oval_obstacles=sum(1 for item in obstacles if item.kind == "oval"),
        keepouts=len(keepouts),
        pads=len(pads),
        connection_count=len(connection_names),
        connection_point_count=len(points),
        net_count=len(imported_nets),
        routable_net_count=sum(1 for net in imported_nets if net.pad_count >= 2),
        over_approximated_obstacles=over_approximated,
        layer_widened_obstacles=sum(1 for item in obstacles if item.widened),
        max_outward_rounding_nm=max_rounding_nm,
    )
    return ImportedProblem(
        name=name,
        document_sha256=digest,
        snapshot=snapshot,
        nets=imported_nets,
        statistics=statistics,
        track_width_nm=track_width_nm,
        policy=policy,
    )


def _build_net(
    *,
    net_id: str,
    root_index: int,
    builder: _NetBuilder,
    connection_names: list[str],
    pad_ids: tuple[str, ...],
    pad_rects: dict[str, tuple[int, int, int, int]],
    points: list[_RawPoint],
    union_of: dict[int, int],
    default_layer_id: str,
) -> ImportedNet:
    """Describe one imported net, including the layer its routing request will name.

    The layer is the one the net's first connection point names, in document order.  A net whose
    points disagree, or whose pads do not all reach that layer, is not silently repaired here:
    the request is still emitted and the router refuses it with its own typed code, which is the
    outcome the benchmark is meant to record.
    """

    rectangles = [pad_rects[pad_id] for pad_id in pad_ids]
    layer_id = next(
        (point.layer_id for point in points if union_of[point.connection_index] == root_index),
        default_layer_id,
    )
    return ImportedNet(
        net_id=net_id,
        layer_id=layer_id,
        pad_ids=pad_ids,
        source_connection_names=tuple(
            connection_names[index] for index in sorted(builder.connection_indices)
        ),
        pad_gap_lower_bound_nm=_pad_gap_lower_bound(rectangles),
        two_pin_centre_manhattan_nm=_two_pin_centre_manhattan(rectangles),
    )


def _pad_gap_lower_bound(rectangles: list[tuple[int, int, int, int]]) -> int:
    """Return a provable lower bound on any rectilinear tree touching every pad.

    Such a tree must reach the rightmost pad's left edge from the leftmost pad's right edge, so
    its x-extent is at least ``max(min_x) - min(max_x)`` when that is positive, and likewise in
    y.  The bound is not tight — it ignores every obstacle and every bend — which is exactly why
    it is safe to compare a router against.
    """

    if len(rectangles) < 2:
        return 0
    x_gap = max(0, max(item[0] for item in rectangles) - min(item[2] for item in rectangles))
    y_gap = max(0, max(item[1] for item in rectangles) - min(item[3] for item in rectangles))
    return x_gap + y_gap


def _two_pin_centre_manhattan(rectangles: list[tuple[int, int, int, int]]) -> int | None:
    """Return the exact centre-to-centre Manhattan distance of a two-pad net."""

    if len(rectangles) != 2:
        return None
    first, second = rectangles
    first_centre = ((first[0] + first[2]) // 2, (first[1] + first[3]) // 2)
    second_centre = ((second[0] + second[2]) // 2, (second[1] + second[3]) // 2)
    return abs(first_centre[0] - second_centre[0]) + abs(first_centre[1] - second_centre[1])


def _rectangle_ring(rectangle: tuple[int, int, int, int]) -> Ring:
    """Return the closed four-corner ring of an axis-aligned rectangle."""

    min_x, min_y, max_x, max_y = rectangle
    return Ring(
        points=(
            PointNM(min_x, min_y),
            PointNM(max_x, min_y),
            PointNM(max_x, max_y),
            PointNM(min_x, max_y),
        )
    )
