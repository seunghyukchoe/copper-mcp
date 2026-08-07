"""Bounded, region-scoped observation of one board as a typed Circuit Scene.

The scene is the authority. Any render is an advisory orientation aid, and this module emits
none: a model that receives only the structured scene must be able to work from it alone,
because MCP hosts routinely drop images and vision models cannot ground EDA geometry anyway.

Two properties shape the whole module. Objects are referenced by the Board IR identity they
already carry, so a model names things by id and never by coordinate. And every string the
board's author controls — silkscreen, fabrication text, net names, footprint properties — is
confined to a separately typed ``annotations`` collection marked untrusted, never interpolated
into a field that reads as instruction.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from math import isqrt
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.kicad_board_ir import FOREIGN_ROOT_DIAGNOSTIC_CODE
from copper_mcp.adapters.sexpr import SExpr, SExprError, children, parse_sexpr
from copper_mcp.board_ir import (
    Arc,
    BoardIRSnapshot,
    Footprint,
    Keepout,
    NetClass,
    Pad,
    ParseLimits,
    PointNM,
    Ring,
    Segment,
    Via,
    Zone,
)
from copper_mcp.config import Settings
from copper_mcp.kicad_ipc import capture_live_board
from copper_mcp.models import SCHEMA_VERSION
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.request_boundary import (
    CONSTRAINT_FIELDS,
    MAX_JSON_SAFE_INTEGER,
    RequestError,
    board_path,
    boolean,
    copper_layer,
    integer,
    known_fields,
    mapping,
    net_class_constraints,
    required_fields,
    text,
)
from copper_mcp.scene_render import SceneRenderEvidence
from copper_mcp.security import read_workspace_file

SCENE_VERSION = "0.3.0"

#: Objects the router treats as given, versus objects a proposal could add or change.
_STATIC_KINDS = ("outline", "footprints", "pads", "keepouts", "rules")
_MUTABLE_KINDS = ("segments", "arcs", "vias", "zones")

#: The single permitted value of a withheld kind's ``observation`` field.
#:
#: A one-value literal, for the same reason ``untrusted_board_author`` is one: there is no
#: spelling here that could mean "observed and empty". A kind is either a list — complete for
#: the region and layer filter — or this object. See ADR-0088.
WITHHELD_BY_CEILING = "withheld_by_ceiling"

_REQUIRED_FIELDS = ("board", "constraints", "region")
_OPTIONAL_FIELDS = ("layers", "include_annotations", "include_render")
_REGION_FIELDS = ("min_x_nm", "min_y_nm", "max_x_nm", "max_y_nm", "around_ref_id", "radius_nm")
_MAX_REF_CHARACTERS = 200
_SHA256_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")

#: Board nodes whose text the board's author controls. None of it is ever trusted.
_ANNOTATION_HEADS = ("gr_text", "fp_text")


class CircuitSceneError(RequestError):
    """Raised when a scene request is malformed or cannot be honoured."""


@dataclass(frozen=True, slots=True)
class SceneRegion:
    """One resolved observation window in exact board nanometres."""

    min_x_nm: int
    min_y_nm: int
    max_x_nm: int
    max_y_nm: int
    source: str

    def contains_point(self, point: PointNM) -> bool:
        return (
            self.min_x_nm <= point.x <= self.max_x_nm and self.min_y_nm <= point.y <= self.max_y_nm
        )

    def overlaps(self, min_x: int, min_y: int, max_x: int, max_y: int) -> bool:
        return (
            min_x <= self.max_x_nm
            and self.min_x_nm <= max_x
            and min_y <= self.max_y_nm
            and self.min_y_nm <= max_y
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_x_nm": self.min_x_nm,
            "min_y_nm": self.min_y_nm,
            "max_x_nm": self.max_x_nm,
            "max_y_nm": self.max_y_nm,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class SceneAnnotation:
    """One board-author-controlled string, quarantined away from every other field.

    ``trust`` carries a single permitted value so the field cannot be quietly widened: any
    future code that wants to mark a string trusted has to change this type deliberately.
    """

    ref_id: str
    layer_id: str | None
    origin: str
    text: str
    trust: str = "untrusted_board_author"

    def __post_init__(self) -> None:
        if self.trust != "untrusted_board_author":
            raise CircuitSceneError("scene annotations carry exactly one trust level")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "layer_id": self.layer_id,
            "origin": self.origin,
            "trust": self.trust,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class SceneObject:
    """One referenced board object with exact geometry and no author-controlled text."""

    ref_id: str
    kind: str
    layer_ids: tuple[str, ...]
    geometry: Mapping[str, Any]
    ref_stability: str
    #: Whether the board's author pinned this object. ``None`` means the kind has no such
    #: concept (an outline contour, a net class), which is different from "not locked".
    #:
    #: Deliberately a field rather than a third partition. The static/mutable split is by
    #: *kind* - what a proposal may change at all - and is exhaustive; lockedness is a
    #: property of an individual object that its author can toggle without changing what kind
    #: of thing it is. A third collection would make the partition non-exhaustive and force
    #: every consumer to look in three places to find all the segments.
    locked: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "kind": self.kind,
            "layer_ids": list(self.layer_ids),
            "ref_stability": self.ref_stability,
            "locked": self.locked,
            "geometry": dict(self.geometry),
        }


@dataclass(frozen=True, slots=True)
class WithheldKind:
    """One whole object kind the ceilings could not carry, stated instead of emptied.

    This replaces the kind's array rather than sitting beside it. That is the entire point:
    a separate truncation record next to ``vias: []`` is only read by a caller who already
    suspects truncation, and the caller who most needs the warning is the one who reads the
    list and believes it. A value of a different JSON type cannot be mistaken for an empty
    one — every naive read of it either raises or reports that objects exist.
    """

    ceiling_hit: str
    objects_omitted: int
    observation: str = WITHHELD_BY_CEILING

    def __post_init__(self) -> None:
        if self.observation != WITHHELD_BY_CEILING:
            raise CircuitSceneError("a withheld scene kind carries exactly one observation value")
        if self.ceiling_hit not in ("max_scene_objects", "max_scene_vertices"):
            raise CircuitSceneError("a withheld scene kind names an object or vertex ceiling")
        if self.objects_omitted < 1:
            raise CircuitSceneError("a withheld scene kind withholds at least one object")

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation": self.observation,
            "ceiling_hit": self.ceiling_hit,
            "objects_omitted": self.objects_omitted,
        }


def _ref_stability(ref_id: str) -> str:
    """Report how durable one reference is, so a caller knows what it is holding.

    ``native`` is anchored in the source file's own KiCad identities and survives unrelated
    edits: either the object's own KiCad UUID, or — for the one contour assembled from
    ``Edge.Cuts`` segments — the hash of its member segments' UUIDs (ADR-0088), which moves only
    when the member set itself changes. ``content_derived`` is a hash of the source revision, so
    it moves whenever *anything* in the file changes and must be re-read before reuse.
    ``request_scoped`` belongs to the request rather than to the board — the net class echoed
    back under ``rules`` is the only such id today — and naming it separately keeps it from
    polluting the board-reference durability signal.
    """

    if ":kicad:" in ref_id or ":assembled:" in ref_id:
        return "native"
    return "content_derived" if ":derived:" in ref_id else "request_scoped"


def _ring_bounds(ring: Ring) -> tuple[int, int, int, int]:
    xs = [point.x for point in ring.points]
    ys = [point.y for point in ring.points]
    return min(xs), min(ys), max(xs), max(ys)


def _points(ring: Ring) -> list[list[int]]:
    return [[point.x, point.y] for point in ring.points]


@dataclass(frozen=True, slots=True)
class CircuitSceneRequest:
    """One validated, immutable scene request built from untrusted input."""

    board: str
    constraints: NetClass
    region: Mapping[str, Any]
    layers: tuple[str, ...]
    include_annotations: bool
    include_render: bool = False

    def profile(self) -> KiCadConstraintProfile:
        return KiCadConstraintProfile(
            net_classes=(self.constraints,),
            default_net_class_id=self.constraints.id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "board": self.board,
            "layers": list(self.layers),
            "include_annotations": self.include_annotations,
            "include_render": self.include_render,
            "constraints": {field: getattr(self.constraints, field) for field in CONSTRAINT_FIELDS},
            "region": dict(self.region),
        }


def _region_fields(payload: Any) -> Mapping[str, Any]:
    fields = mapping("region", payload)
    known_fields("region", fields, frozenset(_REGION_FIELDS))
    resolved: dict[str, Any] = {}
    for name in ("min_x_nm", "min_y_nm", "max_x_nm", "max_y_nm", "radius_nm"):
        if name in fields:
            resolved[name] = integer(
                f"region.{name}",
                fields[name],
                minimum=-MAX_JSON_SAFE_INTEGER,
                maximum=MAX_JSON_SAFE_INTEGER,
            )
    if "around_ref_id" in fields:
        resolved["around_ref_id"] = text(
            "region.around_ref_id", fields["around_ref_id"], maximum=_MAX_REF_CHARACTERS
        )
    box = {"min_x_nm", "min_y_nm", "max_x_nm", "max_y_nm"}
    has_box = box <= set(resolved)
    has_ref = "around_ref_id" in resolved
    if has_box == has_ref:
        raise CircuitSceneError(
            "region must be either a complete bounding box or one around_ref_id"
        )
    if has_box:
        if (
            resolved["min_x_nm"] > resolved["max_x_nm"]
            or resolved["min_y_nm"] > resolved["max_y_nm"]
        ):
            raise CircuitSceneError("region bounds must be ordered")
        if "radius_nm" in resolved:
            raise CircuitSceneError("a bounding-box region does not take a radius")
    elif "radius_nm" not in resolved:
        raise CircuitSceneError("an around_ref_id region requires a radius")
    elif resolved["radius_nm"] < 1:
        raise CircuitSceneError("an around_ref_id radius must be positive")
    elif set(resolved) & box:
        raise CircuitSceneError("an around_ref_id region does not take bounds")
    return resolved


def parse_circuit_scene_request(payload: Any) -> CircuitSceneRequest:
    """Validate one untrusted scene request without echoing unvalidated input."""

    try:
        fields = mapping("request", payload)
        known_fields("request", fields, frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS))
        required_fields("request", fields, _REQUIRED_FIELDS)
        raw_layers = fields.get("layers", [])
        if not isinstance(raw_layers, list | tuple):
            raise CircuitSceneError("layers must be a list of copper layer names")
        if len(raw_layers) > 64:
            raise CircuitSceneError("too many layers were requested")
        layers = tuple(
            copper_layer(f"layers[{index}]", value) for index, value in enumerate(raw_layers)
        )
        if len(set(layers)) != len(layers):
            raise CircuitSceneError("layers must be unique")
        return CircuitSceneRequest(
            board=board_path(fields["board"]),
            constraints=net_class_constraints(fields["constraints"]),
            region=_region_fields(fields["region"]),
            layers=layers,
            include_annotations=boolean(
                "include_annotations", fields.get("include_annotations", False)
            ),
            include_render=boolean("include_render", fields.get("include_render", False)),
        )
    except CircuitSceneError:
        raise
    except RequestError as error:
        raise CircuitSceneError(str(error)) from error


_QUARTER_UDEG = 90_000_000


def _ceil_sqrt(value: int) -> int:
    """Smallest integer whose square is at least ``value``. Exact, no floating point."""

    if value <= 0:
        return 0
    root = isqrt(value)
    return root if root * root == value else root + 1


def _pad_half_extents(pad: Pad) -> tuple[int, int]:
    """Half extents of an axis-aligned box that contains the pad, whatever its angle.

    Board IR accepts any pad angle, not only quarter turns: KiCad rejects a non-orthogonal
    *footprint* transform but a pad may carry its own 45-degree angle. Swapping width and
    height on quadrant parity alone is therefore only correct for quarter turns, and
    under-bounds every other angle - the direction that makes a region query miss a pad.

    Quarter turns keep their exact extents. Any other angle falls back to the circumscribed
    circle of the pad rectangle, which contains it at every rotation and needs no trigonometry
    to compute exactly.
    """

    if pad.rotation_udeg % _QUARTER_UDEG == 0:
        half_x, half_y = (pad.size_x_nm + 1) // 2, (pad.size_y_nm + 1) // 2
        if pad.rotation_udeg // _QUARTER_UDEG % 2 == 1:
            half_x, half_y = half_y, half_x
        return half_x, half_y
    half = _ceil_sqrt(pad.size_x_nm * pad.size_x_nm + pad.size_y_nm * pad.size_y_nm)
    half = (half + 1) // 2
    return half, half


def _circumcentre(start: PointNM, mid: PointNM, end: PointNM) -> tuple[int, int, int] | None:
    """Return the arc's centre as an exact rational ``(x_numerator, y_numerator, denominator)``.

    Returns ``None`` when the three points are collinear, which has no circle.
    """

    ax, ay = start.x, start.y
    bx, by = mid.x, mid.y
    cx, cy = end.x, end.y
    denominator = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if denominator == 0:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    x_numerator = a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)
    y_numerator = a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)
    if denominator < 0:
        return -x_numerator, -y_numerator, -denominator
    return x_numerator, y_numerator, denominator


def _sweep_half(anchor: tuple[int, int], vector: tuple[int, int]) -> int:
    """Which half-turn counter-clockwise from ``anchor`` a direction falls in."""

    cross = anchor[0] * vector[1] - anchor[1] * vector[0]
    if cross > 0:
        return 0
    if cross == 0 and anchor[0] * vector[0] + anchor[1] * vector[1] > 0:
        return 0
    return 1


def _ccw_at_or_before(
    anchor: tuple[int, int], first: tuple[int, int], second: tuple[int, int]
) -> bool:
    """Whether ``first`` is reached no later than ``second`` sweeping CCW from ``anchor``.

    Exact integer comparison standing in for an angle. Comparing dot products directly would
    be wrong: a dot product scales with a vector's length, and the cardinal directions tested
    here are unit vectors while the arc's own points are radius-length, so the two are not on
    the same scale. Splitting the turn into halves and then comparing a single cross product -
    decisive because two directions inside one half-turn differ by less than a half turn -
    depends only on direction, never on magnitude.
    """

    first_half, second_half = _sweep_half(anchor, first), _sweep_half(anchor, second)
    if first_half != second_half:
        return first_half < second_half
    return first[0] * second[1] - first[1] * second[0] >= 0


def _arc_bounds(arc: Arc) -> tuple[int, int, int, int]:
    """Bound an arc including the bulge between its endpoints.

    Start, middle and end points alone under-bound every arc whose extreme point is not one of
    them, so a region touching only the bulge would miss the arc entirely. The four cardinal
    points of the arc's circle are the only places an axis-aligned extreme can occur, so each
    one that the sweep actually crosses is folded in.
    """

    half = (arc.width_nm + 1) // 2
    xs = [arc.start.x, arc.mid.x, arc.end.x]
    ys = [arc.start.y, arc.mid.y, arc.end.y]
    minimum_x, minimum_y = min(xs), min(ys)
    maximum_x, maximum_y = max(xs), max(ys)

    centre = _circumcentre(arc.start, arc.mid, arc.end)
    if centre is not None:
        centre_x, centre_y, denominator = centre

        def scaled(point: PointNM) -> tuple[int, int]:
            return point.x * denominator - centre_x, point.y * denominator - centre_y

        anchor = scaled(arc.start)
        radius_squared = anchor[0] * anchor[0] + anchor[1] * anchor[1]
        if radius_squared > 0:
            end_vector = scaled(arc.end)
            forward = _ccw_at_or_before(anchor, scaled(arc.mid), end_vector)
            radius = _ceil_sqrt(radius_squared)
            for direction, axis in (
                ((1, 0), "east"),
                ((0, 1), "south"),
                ((-1, 0), "west"),
                ((0, -1), "north"),
            ):
                before_end = _ccw_at_or_before(anchor, direction, end_vector)
                # Sweeping the other way, the arc is everything the forward sweep is not.
                if not (before_end if forward else not before_end):
                    continue
                # ``radius`` is a ceiling, so these bounds stay outside the true circle.
                if axis == "east":
                    maximum_x = max(maximum_x, -(-(centre_x + radius) // denominator))
                elif axis == "west":
                    minimum_x = min(minimum_x, (centre_x - radius) // denominator)
                elif axis == "south":
                    maximum_y = max(maximum_y, -(-(centre_y + radius) // denominator))
                else:
                    minimum_y = min(minimum_y, (centre_y - radius) // denominator)

    return minimum_x - half, minimum_y - half, maximum_x + half, maximum_y + half


def _object_bounds(
    snapshot: BoardIRSnapshot,
) -> dict[str, tuple[int, int, int, int]]:
    """Index every referenced object's bounding box for around_ref resolution.

    Bounds here decide which objects a region query returns, so they must **over**-approximate.
    Returning an object whose true geometry lies slightly outside the window is a harmless
    false positive; omitting one that overlaps tells a caller the board is empty where it is
    not, and nothing downstream can recover from that.
    """

    bounds: dict[str, tuple[int, int, int, int]] = {}
    content = snapshot.content
    for contour in content.outline:
        bounds[contour.id] = _ring_bounds(contour.outer)
    for pad in content.pads:
        half_x, half_y = _pad_half_extents(pad)
        bounds[pad.id] = (
            pad.center.x - half_x,
            pad.center.y - half_y,
            pad.center.x + half_x,
            pad.center.y + half_y,
        )
    for footprint in content.footprints:
        footprint_boxes = [bounds[pad_id] for pad_id in footprint.pad_ids]
        footprint_boxes.extend(_ring_bounds(ring) for ring in footprint.courtyards)
        footprint_boxes.extend(
            (
                circle.center.x - circle.radius_nm,
                circle.center.y - circle.radius_nm,
                circle.center.x + circle.radius_nm,
                circle.center.y + circle.radius_nm,
            )
            for circle in footprint.courtyard_circles
        )
        if footprint_boxes:
            bounds[footprint.id] = (
                min(box[0] for box in footprint_boxes),
                min(box[1] for box in footprint_boxes),
                max(box[2] for box in footprint_boxes),
                max(box[3] for box in footprint_boxes),
            )
        else:
            bounds[footprint.id] = (
                footprint.origin.x,
                footprint.origin.y,
                footprint.origin.x,
                footprint.origin.y,
            )
    for via in content.vias:
        half = (via.diameter_nm + 1) // 2
        bounds[via.id] = (
            via.center.x - half,
            via.center.y - half,
            via.center.x + half,
            via.center.y + half,
        )
    for segment in content.segments:
        half = (segment.width_nm + 1) // 2
        bounds[segment.id] = (
            min(segment.start.x, segment.end.x) - half,
            min(segment.start.y, segment.end.y) - half,
            max(segment.start.x, segment.end.x) + half,
            max(segment.start.y, segment.end.y) + half,
        )
    for arc in content.arcs:
        bounds[arc.id] = _arc_bounds(arc)
    for zone in content.zones:
        bounds[zone.id] = _ring_bounds(zone.boundary)
    for keepout in content.keepouts:
        bounds[keepout.id] = _ring_bounds(keepout.boundary)
    return bounds


def _clamped(value: int) -> int:
    """Hold a coordinate inside the range the response contract advertises."""

    return max(-MAX_JSON_SAFE_INTEGER, min(MAX_JSON_SAFE_INTEGER, value))


def _resolve_region(
    request: CircuitSceneRequest,
    bounds: Mapping[str, tuple[int, int, int, int]],
) -> SceneRegion:
    region = request.region
    if "around_ref_id" in region:
        reference = region["around_ref_id"]
        anchor = bounds.get(reference)
        if anchor is None:
            # The caller supplied the id, so naming it back is an echo of its own input
            # rather than a disclosure; the message still avoids quoting it.
            raise CircuitSceneError("the requested reference does not exist on this board")
        radius = int(region["radius_nm"])
        # Clamped, not wrapped. Both the radius and the anchor are individually inside the
        # advertised range, but their sum need not be, and a window that silently exceeded it
        # would violate the contract this scene is about to be validated against. Clamping is
        # lossless here: every coordinate on a board is inside the range, so a window already
        # covering the range cannot select anything more by growing.
        return SceneRegion(
            min_x_nm=_clamped(anchor[0] - radius),
            min_y_nm=_clamped(anchor[1] - radius),
            max_x_nm=_clamped(anchor[2] + radius),
            max_y_nm=_clamped(anchor[3] + radius),
            source="around_ref",
        )
    return SceneRegion(
        min_x_nm=int(region["min_x_nm"]),
        min_y_nm=int(region["min_y_nm"]),
        max_x_nm=int(region["max_x_nm"]),
        max_y_nm=int(region["max_y_nm"]),
        source="explicit",
    )


def _selected(layer_ids: Iterable[str], requested: tuple[str, ...]) -> bool:
    if not requested:
        return True
    wanted = {f"layer:{name}" for name in requested}
    return any(layer_id in wanted for layer_id in layer_ids)


def _pad_object(pad: Pad) -> SceneObject:
    return SceneObject(
        ref_id=pad.id,
        kind="pad",
        layer_ids=tuple(pad.layer_ids),
        geometry={
            "center_nm": [pad.center.x, pad.center.y],
            "size_nm": [pad.size_x_nm, pad.size_y_nm],
            "rotation_udeg": pad.rotation_udeg,
            "shape": str(pad.shape),
            "kind": str(pad.kind),
            "net_id": pad.net_id,
            "roundrect_radius_nm": pad.roundrect_radius_nm,
            "drill_nm": (None if pad.drill_x_nm is None else [pad.drill_x_nm, pad.drill_y_nm]),
        },
        ref_stability=_ref_stability(pad.id),
        locked=pad.locked,
    )


def _footprint_object(footprint: Footprint) -> SceneObject:
    layer_id = "layer:F.Cu" if footprint.side.value == "front" else "layer:B.Cu"
    return SceneObject(
        ref_id=footprint.id,
        kind="footprint",
        layer_ids=(layer_id,),
        geometry={
            "origin_nm": [footprint.origin.x, footprint.origin.y],
            "rotation_udeg": footprint.rotation_udeg,
            "side": footprint.side.value,
            "pad_ids": list(footprint.pad_ids),
            "courtyards_nm": [_points(ring) for ring in footprint.courtyards],
            # The key appears only when a circle exists, which keeps every previously
            # observable scene revision byte-identical.
            **(
                {
                    "courtyard_circles_nm": [
                        [circle.center.x, circle.center.y, circle.radius_nm]
                        for circle in footprint.courtyard_circles
                    ]
                }
                if footprint.courtyard_circles
                else {}
            ),
        },
        ref_stability=_ref_stability(footprint.id),
        locked=footprint.locked,
    )


def _segment_object(segment: Segment) -> SceneObject:
    return SceneObject(
        ref_id=segment.id,
        kind="segment",
        layer_ids=(segment.layer_id,),
        geometry={
            "start_nm": [segment.start.x, segment.start.y],
            "end_nm": [segment.end.x, segment.end.y],
            "width_nm": segment.width_nm,
            "net_id": segment.net_id,
        },
        ref_stability=_ref_stability(segment.id),
        locked=segment.locked,
    )


def _arc_object(arc: Arc) -> SceneObject:
    return SceneObject(
        ref_id=arc.id,
        kind="arc",
        layer_ids=(arc.layer_id,),
        geometry={
            "start_nm": [arc.start.x, arc.start.y],
            "mid_nm": [arc.mid.x, arc.mid.y],
            "end_nm": [arc.end.x, arc.end.y],
            "width_nm": arc.width_nm,
            "net_id": arc.net_id,
        },
        ref_stability=_ref_stability(arc.id),
        locked=arc.locked,
    )


def _via_object(via: Via, layer_ids: tuple[str, ...]) -> SceneObject:
    return SceneObject(
        ref_id=via.id,
        kind="via",
        layer_ids=layer_ids,
        geometry={
            "center_nm": [via.center.x, via.center.y],
            "diameter_nm": via.diameter_nm,
            "drill_nm": via.drill_nm,
            "net_id": via.net_id,
        },
        ref_stability=_ref_stability(via.id),
        locked=via.locked,
    )


def _zone_object(zone: Zone) -> SceneObject:
    return SceneObject(
        ref_id=zone.id,
        kind="zone",
        layer_ids=(zone.layer_id,),
        geometry={
            "boundary_nm": _points(zone.boundary),
            "net_id": zone.net_id,
            "clearance_nm": zone.clearance_nm,
            "min_thickness_nm": zone.min_thickness_nm,
        },
        ref_stability=_ref_stability(zone.id),
        locked=zone.locked,
    )


def _keepout_object(keepout: Keepout) -> SceneObject:
    return SceneObject(
        ref_id=keepout.id,
        kind="keepout",
        layer_ids=tuple(keepout.layer_ids),
        geometry={
            "boundary_nm": _points(keepout.boundary),
            "prohibit_tracks": keepout.prohibit_tracks,
            "prohibit_vias": keepout.prohibit_vias,
            "prohibit_pads": keepout.prohibit_pads,
        },
        ref_stability=_ref_stability(keepout.id),
        locked=keepout.locked,
    )


@dataclass(frozen=True, slots=True)
class _KindSpec:
    """One object kind: how to filter it, what it costs, and how to build it.

    Filtering and costing are separated from building so the ceilings can be spent over kinds
    that are counted but not yet materialised. Nothing is constructed for a kind that will be
    withheld, which keeps peak memory at the object ceiling rather than at the board's size.
    """

    name: str
    static: bool
    items: tuple[Any, ...]
    layer_ids: Callable[[Any], tuple[str, ...]]
    vertices: Callable[[Any], int]
    build: Callable[[Any], SceneObject]
    #: Rules are board-wide rather than positional, so no region can exclude them.
    positional: bool = True


def _eligible(
    spec: _KindSpec,
    bounds: Mapping[str, tuple[int, int, int, int]],
    region: SceneRegion,
    layers: tuple[str, ...],
) -> Iterator[Any]:
    """Yield the kind's items that meet the region and the layer filter, in board order."""

    for item in spec.items:
        if spec.positional:
            box = bounds.get(item.id)
            if box is None or not region.overlaps(*box):
                continue
        if not _selected(spec.layer_ids(item), layers):
            continue
        yield item


def _allocate_kinds(
    demand: tuple[tuple[int, int], ...],
    max_objects: int,
    max_vertices: int,
) -> tuple[frozenset[int], dict[int, WithheldKind], str | None]:
    """Decide which kinds are observed whole. A kind never comes back partly filled.

    Kinds are offered the budget in ascending object count, ties broken by the fixed
    declaration order, which is deterministic for a given board revision and request. Smallest
    first is the greedy order that maximises how many kinds are observed completely, and it
    also happens to put the two kinds a caller needs to bound a follow-up request — the outline
    and the rules — ahead of the tens of thousands of segments that used to consume everything.

    A kind that does not fit is skipped rather than ending the allocation, so one enormous kind
    cannot withhold the small ones behind it.
    """

    objects = 0
    vertices = 0
    admitted: set[int] = set()
    withheld: dict[int, WithheldKind] = {}
    first_ceiling: str | None = None
    for index in sorted(range(len(demand)), key=lambda index: (demand[index][0], index)):
        count, cost = demand[index]
        if count == 0:
            # An empty kind costs nothing and is admitted, so an empty array in the response
            # always means the region genuinely holds none of that kind.
            admitted.add(index)
            continue
        if objects + count > max_objects:
            withheld[index] = WithheldKind(ceiling_hit="max_scene_objects", objects_omitted=count)
        elif vertices + cost > max_vertices:
            withheld[index] = WithheldKind(ceiling_hit="max_scene_vertices", objects_omitted=count)
        else:
            objects += count
            vertices += cost
            admitted.add(index)
            continue
        if first_ceiling is None:
            first_ceiling = withheld[index].ceiling_hit
    return frozenset(admitted), withheld, first_ceiling


def _demand(
    spec: _KindSpec,
    bounds: Mapping[str, tuple[int, int, int, int]],
    region: SceneRegion,
    layers: tuple[str, ...],
) -> tuple[int, int]:
    """How many objects and vertices this kind would cost if it were observed completely."""

    count = 0
    vertices = 0
    for item in _eligible(spec, bounds, region, layers):
        count += 1
        vertices += spec.vertices(item)
    return count, vertices


def _outline_object(contour: Any) -> SceneObject:
    return SceneObject(
        ref_id=contour.id,
        kind="outline",
        layer_ids=(),
        geometry={"outer_nm": _points(contour.outer)},
        ref_stability=_ref_stability(contour.id),
    )


def _net_class_object(net_class: NetClass) -> SceneObject:
    return SceneObject(
        ref_id=net_class.id,
        kind="net_class",
        layer_ids=(),
        geometry={
            "clearance_nm": net_class.clearance_nm,
            "track_width_nm": net_class.track_width_nm,
            "via_diameter_nm": net_class.via_diameter_nm,
            "via_drill_nm": net_class.via_drill_nm,
        },
        ref_stability=_ref_stability(net_class.id),
    )


def _footprint_detail_units(footprint: Footprint) -> int:
    """Footprint origin, pad relationships and courtyard vertices all cost vertex budget."""

    return (
        1
        + len(footprint.pad_ids)
        + sum(len(ring.points) for ring in footprint.courtyards)
        + len(footprint.courtyard_circles)
    )


def _kind_specs(content: Any, every_layer: tuple[str, ...]) -> tuple[_KindSpec, ...]:
    """The nine object kinds, in the fixed declaration order the response reports them in.

    The order is what breaks ties in ``_allocate_kinds``; it is not a priority. Nothing here
    depends on the board, so two observations of one revision produce the same specs.
    """

    return (
        _KindSpec(
            name="outline",
            static=True,
            items=tuple(content.outline),
            layer_ids=lambda _: every_layer,
            vertices=lambda contour: len(contour.outer.points),
            build=_outline_object,
        ),
        _KindSpec(
            name="footprints",
            static=True,
            items=tuple(content.footprints),
            layer_ids=lambda footprint: (
                ("layer:F.Cu",) if footprint.side.value == "front" else ("layer:B.Cu",)
            ),
            vertices=_footprint_detail_units,
            build=_footprint_object,
        ),
        _KindSpec(
            name="pads",
            static=True,
            items=tuple(content.pads),
            layer_ids=lambda pad: tuple(pad.layer_ids),
            vertices=lambda _: 4,
            build=_pad_object,
        ),
        _KindSpec(
            name="keepouts",
            static=True,
            items=tuple(content.keepouts),
            layer_ids=lambda keepout: tuple(keepout.layer_ids),
            vertices=lambda keepout: len(keepout.boundary.points),
            build=_keepout_object,
        ),
        _KindSpec(
            name="rules",
            static=True,
            items=tuple(content.constraints.net_classes),
            layer_ids=lambda _: every_layer,
            vertices=lambda _: 1,
            build=_net_class_object,
            positional=False,
        ),
        _KindSpec(
            name="segments",
            static=False,
            items=tuple(content.segments),
            layer_ids=lambda segment: (segment.layer_id,),
            vertices=lambda _: 2,
            build=_segment_object,
        ),
        _KindSpec(
            name="arcs",
            static=False,
            items=tuple(content.arcs),
            layer_ids=lambda arc: (arc.layer_id,),
            vertices=lambda _: 3,
            build=_arc_object,
        ),
        _KindSpec(
            name="vias",
            static=False,
            items=tuple(content.vias),
            layer_ids=lambda _: every_layer,
            vertices=lambda _: 4,
            build=lambda via: _via_object(via, every_layer),
        ),
        _KindSpec(
            name="zones",
            static=False,
            items=tuple(content.zones),
            layer_ids=lambda zone: (zone.layer_id,),
            vertices=lambda zone: len(zone.boundary.points),
            build=_zone_object,
        ),
    )


def _read_annotations(
    source: bytes, limits: ParseLimits, ceiling: int
) -> tuple[tuple[SceneAnnotation, ...], int]:
    """Collect every board-author-controlled string, out of band from Board IR.

    Board IR deliberately carries no text, which is the right default: none of it is needed to
    reason about geometry and all of it is attacker-controlled on a board someone else authored.
    It is read here only when a caller explicitly asks, and only into the quarantined field.
    """

    try:
        root = parse_sexpr(source, limits)
    except SExprError as error:
        raise CircuitSceneError("board source could not be parsed for annotations") from error

    collected: list[SceneAnnotation] = []
    omitted = 0

    def leading_atoms(node: SExpr) -> tuple[str, ...]:
        """Return the payload strings before the first nested field.

        A text node is ``(gr_text "hi" (at ...) (layer ...))``, so the flat-payload helper
        rejects it outright. Only the leading run is author payload; everything after the
        first nested list is structure.
        """

        payload: list[str] = []
        for value in node.items[1:]:
            if not isinstance(value, str):
                break
            payload.append(value)
        return tuple(payload)

    def layer_of(node: SExpr) -> str | None:
        found = children(node, "layer")
        if not found:
            return None
        values = leading_atoms(found[0])
        return f"layer:{values[0]}" if len(values) == 1 else None

    def add(node: SExpr, origin: str, prefix: str) -> None:
        # Every leading atom is emitted separately. A property is ``(property "Name" "Value")``
        # and the *name* is as author-controlled as the value, so neither may be promoted into
        # a structural field like ``origin`` where it would read as our own vocabulary.
        nonlocal omitted
        layer_id = layer_of(node)
        for slot, payload in enumerate(leading_atoms(node)):
            if not payload:
                continue
            if len(collected) >= ceiling:
                # Every other collection in this response is charged against a ceiling; this
                # one used not to be, so a board with enough properties could grow the
                # response past the length its own contract advertises. Counted, not dropped
                # silently: the caller is told how many strings it is not seeing.
                omitted += 1
                continue
            digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
            collected.append(
                SceneAnnotation(
                    ref_id=f"annotation:{prefix}:{len(collected):04d}:{slot}:{digest}",
                    layer_id=layer_id,
                    origin=origin,
                    text=payload,
                )
            )

    for head in _ANNOTATION_HEADS:
        for node in children(root, head):
            add(node, "board_text", head)
    # Root-level (property ...) nodes are deliberately not read. The Board IR adapter rejects
    # any board that carries them, so this reader — which only ever runs on a supported board —
    # could never see one, and advertising the origin would describe an unreachable branch.
    for footprint_index, footprint in enumerate(children(root, "footprint")):
        for node in children(footprint, "fp_text"):
            add(node, "silkscreen", f"fp{footprint_index}")
        for node in children(footprint, "property"):
            add(node, "footprint_property", f"fp{footprint_index}")
    return tuple(collected), omitted


@dataclass(frozen=True, slots=True)
class CircuitScene:
    """One immutable, region-scoped observation of a board."""

    board_path: str
    board_revision: str
    request: CircuitSceneRequest
    supported: bool
    snapshot_digest: str | None = None
    region: SceneRegion | None = None
    #: Only kinds that were observed **completely**. A kind absent from these mappings is
    #: present in ``withheld_kinds`` instead, so an in-process reader that indexes a kind
    #: raises rather than receiving an empty tuple that reads as "none on this board".
    static_objects: Mapping[str, tuple[SceneObject, ...]] = field(default_factory=dict)
    mutable_objects: Mapping[str, tuple[SceneObject, ...]] = field(default_factory=dict)
    withheld_kinds: Mapping[str, WithheldKind] = field(default_factory=dict)
    annotations: tuple[SceneAnnotation, ...] = ()
    render: SceneRenderEvidence | None = None
    render_bytes: bytes | None = None
    objects_omitted: int = 0
    annotations_omitted: int = 0
    ceiling_hit: str | None = None
    content_derived_ref_count: int = 0
    request_scoped_ref_count: int = 0
    conversion_diagnostic_counts: Mapping[str, int] = field(default_factory=dict)
    scene_version: str = SCENE_VERSION
    schema_version: str = SCHEMA_VERSION

    def _kind(
        self, name: str, observed: Mapping[str, tuple[SceneObject, ...]]
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Render one kind: a complete array, or the object that says why there is none.

        The two are different JSON types on purpose. A caller that iterates a withheld kind,
        indexes it, or asks whether it is empty gets an error or a truthy answer — never the
        silent "this board has no vias" that an empty array used to give it.

        On a supported board a kind that reached neither collection was never decided, and the
        one thing this method must not do is fall back to ``[]`` — that is the defect, arriving
        through a default argument instead of through a budget. It raises instead.
        """

        withheld = self.withheld_kinds.get(name)
        if withheld is not None:
            return withheld.to_dict()
        if self.supported and name not in observed:
            raise CircuitSceneError("a supported scene must decide every object kind")
        return [item.to_dict() for item in observed.get(name, ())]

    def to_dict(self) -> dict[str, Any]:
        """Return a detached plain dictionary; mutating it cannot alter this scene."""

        total = sum(len(group) for group in self.static_objects.values()) + sum(
            len(group) for group in self.mutable_objects.values()
        )
        return {
            "schema_version": self.schema_version,
            "scene_version": self.scene_version,
            "board_path": self.board_path,
            "board_revision": self.board_revision,
            "snapshot_digest": self.snapshot_digest,
            "supported": self.supported,
            "request": self.request.to_dict(),
            "region": None if self.region is None else self.region.to_dict(),
            "static": {name: self._kind(name, self.static_objects) for name in _STATIC_KINDS},
            "mutable": {name: self._kind(name, self.mutable_objects) for name in _MUTABLE_KINDS},
            "annotations": [item.to_dict() for item in self.annotations],
            # Evidence only. The bytes themselves are delivered as a capability by the MCP
            # gateway or written to an explicit path by the CLI, never inlined here.
            "render": None if self.render is None else self.render.to_dict(),
            "truncation": {
                "objects_returned": total,
                "objects_omitted": self.objects_omitted,
                "annotations_returned": len(self.annotations),
                "annotations_omitted": self.annotations_omitted,
                # Names the first ceiling reached. The two ``*_omitted`` counts are the
                # authoritative signal, because objects and annotations are charged against
                # separate budgets and both can truncate in one response.
                #
                # This record is a summary, never the only place truncation is stated:
                # ``objects_omitted`` is exactly the sum of the withheld kinds, and each of
                # those says so in its own slot under ``static`` or ``mutable``.
                "ceiling_hit": self.ceiling_hit,
            },
            "ref_stability": {
                "all_board_refs_native": self.content_derived_ref_count == 0,
                "content_derived_count": self.content_derived_ref_count,
                "request_scoped_count": self.request_scoped_ref_count,
            },
            "conversion_diagnostic_counts": dict(self.conversion_diagnostic_counts),
        }


def _observe_board_scene(
    payload: Any,
    settings: Settings,
    *,
    source: bytes | None = None,
    board_path_override: str | None = None,
) -> CircuitScene:
    """Build one scene from either a confined file or one already-captured live snapshot."""

    if not isinstance(settings, Settings):
        raise CircuitSceneError("scene settings are malformed")
    request = parse_circuit_scene_request(payload)

    if source is None:
        board = read_workspace_file(
            settings.workspace,
            request.board,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
        )
        board_source = board.content
        relative_path = board.path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    else:
        if board_path_override is None:
            raise CircuitSceneError("a live scene requires a bounded board label")
        board_source = source
        relative_path = board_path_override
        if request.include_render:
            raise CircuitSceneError("live scene rendering is not available")
    board_revision = f"sha256:{hashlib.sha256(board_source).hexdigest()}"

    limits = parse_limits_for(settings)
    conversion = parse_kicad_bytes(board_source, request.profile(), limits)
    if any(
        diagnostic.code == FOREIGN_ROOT_DIAGNOSTIC_CODE for diagnostic in conversion.diagnostics
    ):
        # A foreign S-expression root is not an unsupported board, it is not a board at all,
        # and reporting it as ``supported: false`` puts a wrong-document-type answer in the
        # same bucket as a KiCad board carrying a construct this converter cannot model. Both
        # observer paths refuse it by type, so neither one can be talked into publishing a
        # board_revision, a snapshot digest, or a topology summary for a foreign document.
        raise CircuitSceneError("the observed source is not a KiCad board document")
    if conversion.snapshot is None or conversion.diagnostics:
        counts: dict[str, int] = {}
        for diagnostic in conversion.diagnostics:
            counts[diagnostic.code] = counts.get(diagnostic.code, 0) + 1
        return CircuitScene(
            board_path=relative_path,
            board_revision=board_revision,
            request=request,
            supported=False,
            conversion_diagnostic_counts=counts,
        )

    snapshot = conversion.snapshot
    content = snapshot.content
    bounds = _object_bounds(snapshot)
    region = _resolve_region(request, bounds)
    every_layer = tuple(layer.id for layer in content.copper_layers)

    specs = _kind_specs(content, every_layer)
    demand = tuple(_demand(spec, bounds, region, request.layers) for spec in specs)
    admitted, withheld_by_index, ceiling_hit = _allocate_kinds(
        demand,
        max_objects=settings.max_scene_objects,
        max_vertices=settings.max_scene_vertices,
    )

    static: dict[str, tuple[SceneObject, ...]] = {}
    mutable: dict[str, tuple[SceneObject, ...]] = {}
    withheld: dict[str, WithheldKind] = {}
    for index, spec in enumerate(specs):
        if index not in admitted:
            withheld[spec.name] = withheld_by_index[index]
            continue
        built = tuple(spec.build(item) for item in _eligible(spec, bounds, region, request.layers))
        (static if spec.static else mutable)[spec.name] = built

    annotations: tuple[SceneAnnotation, ...] = ()
    annotations_omitted = 0
    if request.include_annotations:
        annotations, annotations_omitted = _read_annotations(
            board_source, limits, settings.max_scene_annotations
        )

    render_evidence: SceneRenderEvidence | None = None
    render_bytes: bytes | None = None
    if request.include_render:
        # Reached only on a supported board. A board Board IR cannot represent might still be
        # drawable by KiCad, but returning a picture of a board whose semantics we could not
        # produce is exactly the inversion ADR-0022 forbids: it invites a reader to trust the
        # render precisely where there is nothing to check it against.
        from copper_mcp.kicad_cli import run_scene_render

        render_evidence, render_bytes = run_scene_render(request.board, settings)
        if render_evidence.source_revision != board_revision:
            # The scene and the render must describe the same bytes. They are read
            # separately, so this is the only thing that makes them one observation.
            raise CircuitSceneError("the board changed while its render was being produced")

    emitted = [item for group in (*static.values(), *mutable.values()) for item in group]
    content_derived = sum(1 for item in emitted if item.ref_stability == "content_derived")
    request_scoped = sum(1 for item in emitted if item.ref_stability == "request_scoped")
    return CircuitScene(
        board_path=relative_path,
        board_revision=board_revision,
        request=request,
        supported=True,
        snapshot_digest=snapshot.snapshot_digest,
        region=region,
        static_objects=static,
        mutable_objects=mutable,
        withheld_kinds=withheld,
        annotations=annotations,
        objects_omitted=sum(item.objects_omitted for item in withheld.values()),
        annotations_omitted=annotations_omitted,
        ceiling_hit=ceiling_hit or ("max_scene_annotations" if annotations_omitted else None),
        render=render_evidence,
        render_bytes=render_bytes,
        content_derived_ref_count=content_derived,
        request_scoped_ref_count=request_scoped,
    )


def observe_board_scene(payload: Any, settings: Settings) -> CircuitScene:
    """Observe one confined workspace board as a bounded, region-scoped typed scene."""

    return _observe_board_scene(payload, settings)


def observe_live_board_scene(
    payload: Any,
    settings: Settings,
    *,
    client_factory: Any = None,
) -> CircuitScene:
    """Bind one active KiCad IPC snapshot to the Circuit Scene revision contract.

    The request uses the literal board label ``"live"`` rather than a filesystem path.  The
    returned ``board_revision`` and ``snapshot_digest`` are derived from the same source bytes
    handed to the Board IR converter. This makes the scene self-consistent. Live placement and
    routing proposals have their own revision-bound, candidate-only compare-and-swap gates;
    DRC, fill, editor mutation, and apply remain separate authorities.
    """

    if not isinstance(settings, Settings):
        raise CircuitSceneError("scene settings are malformed")
    if not isinstance(payload, Mapping) or payload.get("board") != "live":
        raise CircuitSceneError("live scene requests must set board to 'live'")
    request_payload = dict(payload)
    expected_board_revision = request_payload.pop("expect_board_revision", None)
    expected_snapshot_digest = request_payload.pop("expect_snapshot_digest", None)
    for name, value in (
        ("expect_board_revision", expected_board_revision),
        ("expect_snapshot_digest", expected_snapshot_digest),
    ):
        if value is not None and (
            not isinstance(value, str) or _SHA256_DIGEST.fullmatch(value) is None
        ):
            raise CircuitSceneError(f"{name} is malformed")
    validated_request = parse_circuit_scene_request(request_payload)
    if validated_request.include_render:
        raise CircuitSceneError("live scene rendering is not available")
    snapshot = capture_live_board(settings, client_factory=client_factory)
    if (
        expected_board_revision is not None
        and expected_board_revision != snapshot.observation.board_digest
    ):
        raise CircuitSceneError("live board revision is stale")
    scene = _observe_board_scene(
        request_payload,
        settings,
        source=snapshot.source,
        board_path_override="live",
    )
    if expected_snapshot_digest is not None and expected_snapshot_digest != scene.snapshot_digest:
        raise CircuitSceneError("live Board IR snapshot is stale")
    return scene
