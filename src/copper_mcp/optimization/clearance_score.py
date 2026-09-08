"""Private whole-composition IR-class clearance measurement, never native DRC evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, fields, replace
from fractions import Fraction
from itertools import combinations, pairwise
from math import isqrt
from typing import Literal, NoReturn, cast

from copper_mcp.adapters.sexpr import QuotedAtom
from copper_mcp.board_ir import canonical
from copper_mcp.board_ir import types as ir
from copper_mcp.board_ir.limits import ParseLimits
from copper_mcp.board_ir.outline_arc import _segment_distance_squared
from copper_mcp.board_ir.validation import validate_content
from copper_mcp.optimization.contracts import OptimizationError
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.worker import OptimizationExecutionProbe

_METHOD = "composed-ir-class-clearance/v1"
_MAX_OBJECTS = 4096
_MAX_PAIR_CHECKS = 1_000_000
_MAX_TREE_NODES = 131_072
_MAX_TEXT_BYTES = 4 * 1024 * 1024
_MAX_HASH_BYTES = 16 * 1024 * 1024
_MAX_VERTICES = 8192
_MAX_RING_VERTICES = 256
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RECORD_TYPES = (
    ir.BoardIRSnapshot,
    ir.BoardIRContent,
    ir.SourceInfo,
    ir.UnitSystem,
    ir.ConstraintSet,
    ir.Layer,
    ir.Net,
    ir.NetClass,
    ir.NetClassAssignment,
    ir.DifferentialPairRule,
    ir.LengthRule,
    ir.OutlineContour,
    ir.Ring,
    ir.PointNM,
    ir.Footprint,
    ir.CourtyardCircle,
    ir.Pad,
    ir.PadCopperEnvelope,
    ir.Segment,
    ir.Via,
    ir.Arc,
    ir.Zone,
    ir.Keepout,
)
_ENUM_TYPES = (
    ir.FootprintSide,
    ir.PadKind,
    ir.PadShape,
    ir.ViaKind,
    ir.ZonePadConnection,
    ir.ZoneIslandRemoval,
)
_Point = tuple[int, int]
_Reason = Literal[
    "unsupported_geometry",
    "unsupported_rules",
    "unsupported_layers",
    "unassigned_copper",
    "no_comparable_pair",
]


class ClearanceMeasurementError(OptimizationError):
    """Fixed refusal without private geometry or subordinate exception context."""


def _fail() -> NoReturn:
    raise ClearanceMeasurementError("composed clearance measurement refused")


@dataclass(slots=True)
class _Work:
    probe: OptimizationExecutionProbe
    geometry_checks: int = 0

    def check(self) -> None:
        self.probe.checkpoint()

    def spend(self, count: int, *, geometry: bool = False) -> None:
        self.check()
        self.probe.reserve(ResourceUsage(obstacle_checks=count))
        if geometry:
            self.geometry_checks += count
        self.check()


@dataclass(slots=True)
class _Admission:
    work: _Work
    max_objects: int
    nodes: int = 0
    text_bytes: int = 0
    vertices: int = 0
    validation_checks: int = 0

    def visit(self, value: object, depth: int = 0) -> None:
        self.work.check()
        self.nodes += 1
        if self.nodes > _MAX_TREE_NODES or depth > 12:
            _fail()
        cls = type(value)
        if value is None or cls is bool or cls in _ENUM_TYPES:
            return
        if type(value) is int:
            if not -ir.JSON_SAFE_INTEGER <= value <= ir.JSON_SAFE_INTEGER:
                _fail()
            return
        # The KiCad parser retains its immutable quoted-string tag in source and names.
        # Preserve that exact type; subclasses could add callbacks and remain inadmissible.
        if type(value) is str or type(value) is QuotedAtom:
            if len(value) > 4096:
                _fail()
            self.text_bytes += len(value.encode("utf-8"))
            if self.text_bytes > _MAX_TEXT_BYTES:
                _fail()
            return
        if type(value) is tuple:
            if len(value) > self.max_objects or len(value) > _MAX_TREE_NODES - self.nodes:
                _fail()
            self.work.spend(len(value))
            for item in value:
                self.visit(item, depth + 1)
            return
        if cls not in _RECORD_TYPES:
            _fail()
        if type(value) is ir.Ring and (
            type(value.points) is not tuple or len(value.points) > _MAX_RING_VERTICES
        ):
            _fail()
        # Only these exact IR types may expose fields or execute their local validators.
        members = fields(cls)
        self.work.spend(len(members))
        for member in members:
            self.visit(getattr(value, member.name), depth + 1)
        if type(value) is ir.Ring:
            count = len(value.points)
            self.vertices += count
            if count > _MAX_RING_VERTICES or self.vertices > _MAX_VERTICES:
                _fail()
            self.validation_checks += count * (count - 1) // 2
        elif type(value) is ir.Footprint:
            count = sum(
                len(group)
                for group in (
                    value.courtyards,
                    value.courtyard_circles,
                    value.far_side_courtyards,
                    value.far_side_courtyard_circles,
                )
            )
            if count > 64:
                _fail()
            self.validation_checks += count * count
        if self.validation_checks > _MAX_PAIR_CHECKS:
            _fail()
        # A shallow dataclass reconstruction invokes the exact admitted type's validator.
        replace(cast(ir.BoardIRSnapshot, value))


def _hash_document(document: object, work: _Work, *, prefix: bytes = b"") -> str:
    work.check()
    digest = hashlib.sha256(prefix)
    size = 0
    encoder = json.JSONEncoder(
        sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    for token in encoder.iterencode(document):
        work.check()
        encoded = token.encode("utf-8")
        size += len(encoded)
        if size > _MAX_HASH_BYTES:
            _fail()
        digest.update(encoded)
    # Match Board IR's canonical trailing newline for its two existing identities.
    if not prefix:
        digest.update(b"\n")
    work.check()
    return "sha256:" + digest.hexdigest()


def _admit_snapshot(snapshot: ir.BoardIRSnapshot, max_objects: int, work: _Work) -> None:
    if type(snapshot) is not ir.BoardIRSnapshot or type(snapshot.content) is not ir.BoardIRContent:
        _fail()
    content = snapshot.content
    if type(content.constraints) is not ir.ConstraintSet:
        _fail()
    groups = (
        content.outline,
        content.copper_layers,
        content.nets,
        content.footprints,
        content.pads,
        content.segments,
        content.vias,
        content.arcs,
        content.zones,
        content.keepouts,
        content.constraints.net_classes,
        content.constraints.assignments,
        content.constraints.differential_pairs,
        content.constraints.length_rules,
    )
    if any(type(group) is not tuple for group in groups) or sum(map(len, groups)) > max_objects:
        _fail()
    admission = _Admission(work, max_objects)
    admission.visit(snapshot)
    # Reserve the bounded validation pass before its polygon/courtyard comparisons.
    work.spend(admission.nodes + admission.validation_checks)
    validate_content(
        content,
        ParseLimits(
            max_objects=max_objects,
            max_vertices_per_ring=_MAX_RING_VERTICES,
            max_total_vertices=_MAX_VERTICES,
            max_intersection_tests=_MAX_PAIR_CHECKS,
        ),
    )
    work.check()
    if canonical.normalize_content(content) != content:
        _fail()
    work.check()
    if _hash_document(canonical._constraint_payload(content), work) != content.constraint_digest:
        _fail()
    if _hash_document(canonical._content_payload(content), work) != snapshot.snapshot_digest:
        _fail()


@dataclass(frozen=True, slots=True, repr=False)
class ComposedClearance:
    """Signed, downward-rounded IR-class margin; geometry_checks counts reserved primitive work.

    Object count covers every copper object, including unsupported objects on unavailable results.
    This profile measures surface spacing only, not drill, edge or unseen native custom rules.
    """

    status: Literal["measured", "unavailable"]
    reason: _Reason | None
    minimum_margin_nm: int | None
    snapshot_digest: str
    constraint_digest: str
    method_version: str
    object_count: int
    pair_checks: int
    comparable_pairs: int
    geometry_checks: int
    digest: str

    def __repr__(self) -> str:
        return "<ComposedClearance redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class _Copper:
    net: str
    layers: frozenset[str]
    points: tuple[_Point, ...]
    radius2: int

    def __repr__(self) -> str:
        return "<ClearanceCopper redacted>"


def _pad_core(pad: ir.Pad) -> tuple[tuple[_Point, ...], int]:
    width, height = pad.size_x_nm, pad.size_y_nm
    radius = 0
    points: tuple[_Point, ...]
    if pad.shape is ir.PadShape.CIRCLE:
        radius, points = width, ((0, 0),)
    elif pad.shape is ir.PadShape.OVAL:
        radius = min(width, height)
        points = ((-(width - radius), -(height - radius)), (width - radius, height - radius))
    else:
        if pad.shape is ir.PadShape.ROUNDRECT:
            assert pad.roundrect_radius_nm is not None
            radius = 2 * pad.roundrect_radius_nm
        x, y = width - radius, height - radius
        points = ((-x, -y), (x, -y), (x, y), (-x, y))
    for _ in range(pad.rotation_udeg // 90_000_000 % 4):
        points = tuple((y, -x) for x, y in points)
    return tuple(
        dict.fromkeys((x + 2 * pad.center.x, y + 2 * pad.center.y) for x, y in points)
    ), radius


def _copper(content: ir.BoardIRContent, work: _Work) -> tuple[tuple[_Copper, ...], _Reason | None]:
    if not 2 <= len(content.copper_layers) <= 8 or any(
        layer.kind != "signal" for layer in content.copper_layers
    ):
        return (), "unsupported_layers"
    if (
        content.constraints.differential_pairs
        or content.constraints.length_rules
        or content.keepouts
    ):
        return (), "unsupported_rules"
    if content.arcs or content.zones:
        return (), "unsupported_geometry"
    copper = []
    objects: tuple[ir.Pad | ir.Segment | ir.Via, ...] = (
        *content.pads,
        *content.segments,
        *content.vias,
    )
    for item in objects:
        work.spend(1)
        if item.net_id is None:
            return (), "unassigned_copper"
        if isinstance(item, ir.Pad):
            if item.copper_envelope is not None or (
                item.shape is not ir.PadShape.CIRCLE and item.rotation_udeg % 90_000_000
            ):
                return (), "unsupported_geometry"
            points, radius = _pad_core(item)
            layers = frozenset(item.layer_ids)
        elif isinstance(item, ir.Segment):
            points = ((2 * item.start.x, 2 * item.start.y), (2 * item.end.x, 2 * item.end.y))
            radius, layers = item.width_nm, frozenset((item.layer_id,))
        else:
            points = ((2 * item.center.x, 2 * item.center.y),)
            radius = item.diameter_nm
            layers = frozenset(layer.id for layer in content.copper_layers)
        copper.append(_Copper(item.net_id, layers, points, radius))
    return tuple(copper), None


def _edges(points: tuple[_Point, ...]) -> tuple[tuple[_Point, _Point], ...]:
    if len(points) == 1:
        return ((points[0], points[0]),)
    if len(points) == 2:
        return ((points[0], points[1]),)
    return tuple(pairwise((*points, points[0])))


def _cross(a: _Point, b: _Point, c: _Point) -> int:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _intersect(a: _Point, b: _Point, c: _Point, d: _Point) -> bool:
    # Doubled derived coordinates can exceed PointNM's input range; use exact integer tuples.
    return (
        max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0]))
        and max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1]))
        and _cross(a, b, c) * _cross(a, b, d) <= 0
        and _cross(c, d, a) * _cross(c, d, b) <= 0
    )


def _contains(points: tuple[_Point, ...], point: _Point) -> bool:
    return len(points) == 4 and (
        min(p[0] for p in points) <= point[0] <= max(p[0] for p in points)
        and min(p[1] for p in points) <= point[1] <= max(p[1] for p in points)
    )


def _distance_squared(left: _Copper, right: _Copper, work: _Work) -> Fraction:
    work.spend(2, geometry=True)
    if _contains(left.points, right.points[0]) or _contains(right.points, left.points[0]):
        return Fraction(0)
    minimum: Fraction | None = None
    for a, b in _edges(left.points):
        for c, d in _edges(right.points):
            work.spend(5, geometry=True)  # intersection plus four point-to-segment checks
            if _intersect(a, b, c, d):
                return Fraction(0)
            distance = min(
                _segment_distance_squared(a, b, c),
                _segment_distance_squared(a, b, d),
                _segment_distance_squared(c, d, a),
                _segment_distance_squared(c, d, b),
            )
            minimum = distance if minimum is None else min(minimum, distance)
    assert minimum is not None
    return minimum


def measure_composed_clearance(
    snapshot: ir.BoardIRSnapshot,
    probe: OptimizationExecutionProbe,
    *,
    expected_snapshot_digest: str,
    max_objects: int = _MAX_OBJECTS,
    max_pair_checks: int = _MAX_PAIR_CHECKS,
) -> ComposedClearance:
    """Measure complete foreign-net IR-class margins or return an explicit unavailable result."""
    result = None
    try:
        if type(max_objects) is not int or not 1 <= max_objects <= _MAX_OBJECTS:
            _fail()
        if type(max_pair_checks) is not int or not 1 <= max_pair_checks <= _MAX_PAIR_CHECKS:
            _fail()
        if (
            type(expected_snapshot_digest) is not str
            or len(expected_snapshot_digest) != 71
            or _DIGEST.fullmatch(expected_snapshot_digest) is None
        ):
            _fail()
        work = _Work(probe)
        work.check()
        _admit_snapshot(snapshot, max_objects, work)
        if snapshot.snapshot_digest != expected_snapshot_digest:
            _fail()
        content = snapshot.content
        copper, reason = _copper(content, work)
        minimum = None
        pair_checks = comparable_pairs = 0
        if reason is None:
            pair_count = len(copper) * (len(copper) - 1) // 2
            if pair_count > max_pair_checks:
                _fail()
            work.spend(pair_count)  # Includes every same-net and nonoverlapping-layer pair.
            classes = {item.id: item.clearance_nm for item in content.constraints.net_classes}
            clearances = {
                item.net_id: classes[item.net_class_id] for item in content.constraints.assignments
            }
            for left, right in combinations(copper, 2):
                work.check()
                pair_checks += 1
                if left.net == right.net or left.layers.isdisjoint(right.layers):
                    continue
                comparable_pairs += 1
                distance = _distance_squared(left, right, work)
                separation = (
                    max(
                        0,
                        isqrt(distance.numerator // distance.denominator)
                        - left.radius2
                        - right.radius2,
                    )
                    // 2
                )
                margin = separation - max(clearances[left.net], clearances[right.net])
                minimum = margin if minimum is None else min(minimum, margin)
            if minimum is None:
                reason = "no_comparable_pair"
        observed = ComposedClearance(
            "measured" if reason is None else "unavailable",
            reason,
            minimum,
            snapshot.snapshot_digest,
            content.constraint_digest,
            _METHOD,
            sum(
                len(group)
                for group in (
                    content.pads,
                    content.segments,
                    content.vias,
                    content.arcs,
                    content.zones,
                )
            ),
            pair_checks,
            comparable_pairs,
            work.geometry_checks,
            "",
        )
        document = asdict(observed)
        del document["digest"]
        digest = _hash_document(document, work, prefix=b"copper-mcp/composed-clearance/v1\x00")
        completed = replace(observed, digest=digest)
        work.check()
        result = completed
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError, RuntimeError):
        pass
    if result is None:
        _fail()
    return result
