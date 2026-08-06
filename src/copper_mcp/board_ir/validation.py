"""Cross-object and topology validation for immutable Board IR content."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from copper_mcp.board_ir.limits import ParseBudget, ParseLimits
from copper_mcp.board_ir.types import BoardIRContent, PointNM, Ring

_SCHEMA_MAX_COPPER_LAYERS = 64
_SCHEMA_MAX_NET_CLASSES = 10_000
_SCHEMA_MAX_DIFFERENTIAL_PAIRS = 10_000
_SCHEMA_MAX_OBJECTS = 250_000
_SCHEMA_MAX_RING_POINTS = 100_000
_SCHEMA_MAX_COURTYARDS_PER_FOOTPRINT = 64


def _require_orthogonal_courtyard(ring: Ring, locator: str) -> None:
    """Reject courtyard topology whose exact filled region we do not model.

    Board IR stores all courtyard contours as ordinary rings.  The accepted adapter subset is
    deliberately smaller: a simple closed chain made only of horizontal and vertical segments.
    That admits KiCad ``fp_rect``, unfilled orthogonal ``fp_poly``, and closed orthogonal
    ``fp_line`` chains without pretending that an arc, a curve, a diagonal, or a partially open
    construction has a trustworthy collision area.  General ring validation below still owns
    non-zero-area and self-intersection checks.
    """

    points = ring.points
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        # Exactly one coordinate changes along an axis-aligned edge.  A diagonal edge, or a
        # repeated corner, fails this before it can reach the exact orthogonal overlap test.
        if (start.x == end.x) == (start.y == end.y):
            raise BoardIRValidationError(
                "unsupported.topology",
                "Board IR v0.2 courtyard edges must be axis-aligned",
                locator,
            )


@dataclass(frozen=True, slots=True)
class BoardIRValidationError(ValueError):
    """A stable validation failure suitable for an adapter diagnostic."""

    code: str
    message: str
    source_locator: str = "content"

    def __str__(self) -> str:
        return f"{self.code} at {self.source_locator}: {self.message}"


def _require_unique(values: Iterable[str], *, kind: str, locator: str | None = None) -> set[str]:
    """Require distinct values, naming the invariant without echoing any board content.

    ``kind`` reaches the caller inside a refusal message, so it must stay a fixed string chosen by
    this module.  Anything derived from the board — an object ID, a footprint name — belongs in
    ``locator``, which the adapter deliberately does not echo.
    """

    materialized = list(values)
    unique = set(materialized)
    if len(unique) != len(materialized):
        raise BoardIRValidationError("identity.duplicate", f"duplicate {kind}", locator or kind)
    return unique


def _orientation(a: PointNM, b: PointNM, c: PointNM) -> int:
    cross = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
    return (cross > 0) - (cross < 0)


def _on_segment(a: PointNM, b: PointNM, point: PointNM) -> bool:
    return min(a.x, b.x) <= point.x <= max(a.x, b.x) and min(a.y, b.y) <= point.y <= max(a.y, b.y)


def _segments_intersect(a: PointNM, b: PointNM, c: PointNM, d: PointNM) -> bool:
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return (
        (o1 == 0 and _on_segment(a, b, c))
        or (o2 == 0 and _on_segment(a, b, d))
        or (o3 == 0 and _on_segment(c, d, a))
        or (o4 == 0 and _on_segment(c, d, b))
    )


def _validate_ring(
    ring: Ring,
    *,
    locator: str,
    limits: ParseLimits,
    intersection_budget: list[int],
) -> None:
    size = len(ring.points)
    if size > min(limits.max_vertices_per_ring, _SCHEMA_MAX_RING_POINTS):
        raise BoardIRValidationError(
            ParseBudget.VERTICES_PER_RING.value, "ring vertex budget exceeded", locator
        )
    for first in range(size):
        a = ring.points[first]
        b = ring.points[(first + 1) % size]
        for second in range(first + 1, size):
            if second in {first, (first + 1) % size}:
                continue
            if first == 0 and second == size - 1:
                continue
            intersection_budget[0] += 1
            if intersection_budget[0] > limits.max_intersection_tests:
                raise BoardIRValidationError(
                    ParseBudget.INTERSECTION_TESTS.value,
                    "polygon intersection-test budget exceeded",
                    locator,
                )
            c = ring.points[second]
            d = ring.points[(second + 1) % size]
            if _segments_intersect(a, b, c, d):
                raise BoardIRValidationError(
                    "geometry.self_intersection", "ring must not self-intersect", locator
                )


def validate_content(content: BoardIRContent, limits: ParseLimits | None = None) -> None:
    """Validate budgets, references, identities, and exact polygon topology."""

    limits = limits or ParseLimits()
    if len(content.outline) != 1:
        raise BoardIRValidationError(
            "unsupported.topology", "Board IR v0.2 requires exactly one outline contour", "outline"
        )
    if content.outline[0].holes:
        raise BoardIRValidationError(
            "unsupported.topology", "Board IR v0.2 does not support outline holes", "outline"
        )
    if len(content.copper_layers) > _SCHEMA_MAX_COPPER_LAYERS:
        raise BoardIRValidationError(
            "schema.limit", "copper-layer schema limit exceeded", "copper_layers"
        )
    if len(content.constraints.net_classes) > _SCHEMA_MAX_NET_CLASSES:
        raise BoardIRValidationError(
            "schema.limit", "net-class schema limit exceeded", "constraints.net_classes"
        )
    if len(content.constraints.differential_pairs) > _SCHEMA_MAX_DIFFERENTIAL_PAIRS:
        raise BoardIRValidationError(
            "schema.limit",
            "differential-pair schema limit exceeded",
            "constraints.differential_pairs",
        )
    object_groups = (
        content.outline,
        content.copper_layers,
        content.nets,
        content.constraints.net_classes,
        content.constraints.assignments,
        content.constraints.differential_pairs,
        content.constraints.length_rules,
        content.footprints,
        content.pads,
        content.vias,
        content.segments,
        content.arcs,
        content.zones,
        content.keepouts,
    )
    object_count = sum(len(group) for group in object_groups)
    if object_count > min(limits.max_objects, _SCHEMA_MAX_OBJECTS):
        raise BoardIRValidationError(ParseBudget.OBJECTS.value, "object budget exceeded")

    layer_ids = _require_unique((layer.id for layer in content.copper_layers), kind="layer ID")
    _require_unique((layer.name for layer in content.copper_layers), kind="layer name")
    _require_unique((str(layer.index) for layer in content.copper_layers), kind="layer index")
    ordered_layers = tuple(sorted(content.copper_layers, key=lambda item: item.index))
    if tuple(layer.index for layer in ordered_layers) != tuple(range(len(ordered_layers))):
        raise BoardIRValidationError(
            "unsupported.topology",
            "copper-layer indices must be contiguous physical ordinals",
            "copper_layers",
        )
    net_ids = _require_unique((net.id for net in content.nets), kind="net ID")
    _require_unique((net.name for net in content.nets), kind="net name")
    class_ids = _require_unique(
        (net_class.id for net_class in content.constraints.net_classes), kind="net-class ID"
    )

    assignment_nets = _require_unique(
        (assignment.net_id for assignment in content.constraints.assignments),
        kind="net-class assignment",
    )
    if assignment_nets != net_ids:
        raise BoardIRValidationError(
            "constraint.assignment", "every net must have exactly one net-class assignment"
        )
    for assignment in content.constraints.assignments:
        if assignment.net_class_id not in class_ids:
            raise BoardIRValidationError(
                "reference.unknown", "assignment references an unknown net class", assignment.net_id
            )

    _require_unique(
        [rule.id for rule in content.constraints.differential_pairs]
        + [rule.id for rule in content.constraints.length_rules],
        kind="constraint rule ID",
    )
    for pair_rule in content.constraints.differential_pairs:
        if pair_rule.positive_net_id not in net_ids or pair_rule.negative_net_id not in net_ids:
            raise BoardIRValidationError(
                "reference.unknown",
                "differential pair references an unknown net",
                pair_rule.id,
            )
    for length_rule in content.constraints.length_rules:
        if length_rule.net_id not in net_ids:
            raise BoardIRValidationError(
                "reference.unknown", "length rule references an unknown net", length_rule.id
            )

    identity_groups: tuple[tuple[str, ...], ...] = (
        tuple(item.id for item in content.outline),
        tuple(item.id for item in content.footprints),
        tuple(item.id for item in content.pads),
        tuple(item.id for item in content.vias),
        tuple(item.id for item in content.segments),
        tuple(item.id for item in content.arcs),
        tuple(item.id for item in content.zones),
        tuple(item.id for item in content.keepouts),
    )
    _require_unique((item for group in identity_groups for item in group), kind="geometry ID")

    pad_ids = {pad.id for pad in content.pads}
    owned_pad_ids: list[str] = []
    for footprint in content.footprints:
        if len(footprint.courtyards) > _SCHEMA_MAX_COURTYARDS_PER_FOOTPRINT:
            raise BoardIRValidationError(
                "schema.limit",
                "footprint courtyard limit exceeded",
                footprint.id,
            )
        _require_unique(
            footprint.pad_ids, kind="pad ownership within one footprint", locator=footprint.id
        )
        if not set(footprint.pad_ids) <= pad_ids:
            raise BoardIRValidationError(
                "reference.unknown",
                "footprint references an unknown pad",
                footprint.id,
            )
        owned_pad_ids.extend(footprint.pad_ids)
    owned_pad_set = _require_unique(owned_pad_ids, kind="footprint pad ownership")
    if owned_pad_set != pad_ids:
        raise BoardIRValidationError(
            "reference.unowned",
            "every pad must belong to exactly one footprint",
            "footprints",
        )

    def require_net(net_id: str | None, locator: str) -> None:
        if net_id is not None and net_id not in net_ids:
            raise BoardIRValidationError(
                "reference.unknown", "item references an unknown net", locator
            )

    def require_layers(references: tuple[str, ...], locator: str) -> None:
        _require_unique(references, kind="layer reference within one item", locator=locator)
        if not set(references) <= layer_ids:
            raise BoardIRValidationError(
                "unknown.layer", "item references an unknown copper layer", locator
            )

    for pad in content.pads:
        require_net(pad.net_id, pad.id)
        require_layers(pad.layer_ids, pad.id)
    for via in content.vias:
        require_net(via.net_id, via.id)
        require_layers((via.start_layer_id, via.end_layer_id), via.id)
        stack_endpoints = {ordered_layers[0].id, ordered_layers[-1].id}
        if {via.start_layer_id, via.end_layer_id} != stack_endpoints:
            raise BoardIRValidationError(
                "unsupported.construct",
                "through via must span the complete copper stack",
                via.id,
            )
    for segment in content.segments:
        require_net(segment.net_id, segment.id)
        require_layers((segment.layer_id,), segment.id)
    for arc in content.arcs:
        require_net(arc.net_id, arc.id)
        require_layers((arc.layer_id,), arc.id)
    for zone in content.zones:
        require_net(zone.net_id, zone.id)
        require_layers((zone.layer_id,), zone.id)
    for keepout in content.keepouts:
        require_layers(keepout.layer_ids, keepout.id)

    rings: list[tuple[str, Ring]] = []
    for contour in content.outline:
        rings.append((f"{contour.id}.outer", contour.outer))
        rings.extend(
            (f"{contour.id}.holes[{index}]", ring) for index, ring in enumerate(contour.holes)
        )
    rings.extend((f"{zone.id}.boundary", zone.boundary) for zone in content.zones)
    rings.extend((f"{keepout.id}.boundary", keepout.boundary) for keepout in content.keepouts)
    for footprint in content.footprints:
        for index, courtyard in enumerate(footprint.courtyards):
            locator = f"{footprint.id}.courtyards[{index}]"
            _require_orthogonal_courtyard(courtyard, locator)
            rings.append((locator, courtyard))
    total_vertices = sum(len(ring.points) for _, ring in rings)
    if total_vertices > limits.max_total_vertices:
        raise BoardIRValidationError(
            ParseBudget.TOTAL_VERTICES.value, "total vertex budget exceeded"
        )
    intersection_budget = [0]
    for locator, ring in rings:
        _validate_ring(
            ring,
            locator=locator,
            limits=limits,
            intersection_budget=intersection_budget,
        )
