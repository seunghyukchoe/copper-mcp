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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.sexpr import SExpr, SExprError, children, parse_sexpr
from copper_mcp.board_ir import (
    Arc,
    BoardIRSnapshot,
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
from copper_mcp.models import SCHEMA_VERSION
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

SCENE_VERSION = "0.1.0"

#: Objects the router treats as given, versus objects a proposal could add or change.
_STATIC_KINDS = ("outline", "pads", "keepouts", "rules")
_MUTABLE_KINDS = ("segments", "arcs", "vias", "zones")

_REQUIRED_FIELDS = ("board", "constraints", "region")
_OPTIONAL_FIELDS = ("layers", "include_annotations", "include_render")
_REGION_FIELDS = ("min_x_nm", "min_y_nm", "max_x_nm", "max_y_nm", "around_ref_id", "radius_nm")
_MAX_REF_CHARACTERS = 200

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "kind": self.kind,
            "layer_ids": list(self.layer_ids),
            "ref_stability": self.ref_stability,
            "geometry": dict(self.geometry),
        }


@dataclass(slots=True)
class _Budget:
    """Object and vertex ceilings, charged as the scene is built."""

    max_objects: int
    max_vertices: int
    objects: int = 0
    vertices: int = 0
    omitted: int = 0
    ceiling_hit: str | None = None

    def admit(self, vertex_count: int) -> bool:
        if self.ceiling_hit is not None:
            self.omitted += 1
            return False
        if self.objects >= self.max_objects:
            self.ceiling_hit = "max_scene_objects"
            self.omitted += 1
            return False
        if self.vertices + vertex_count > self.max_vertices:
            self.ceiling_hit = "max_scene_vertices"
            self.omitted += 1
            return False
        self.objects += 1
        self.vertices += vertex_count
        return True


def _ref_stability(ref_id: str) -> str:
    """Report how durable one reference is, so a caller knows what it is holding.

    ``native`` is the object's own KiCad UUID and survives unrelated edits. ``content_derived``
    is a hash of the object's geometry, so it moves whenever that object changes and must be
    re-read before reuse. ``request_scoped`` belongs to the request rather than to the board —
    the net class echoed back under ``rules`` is the only such id today — and naming it
    separately keeps it from polluting the board-reference durability signal.
    """

    if ":kicad:" in ref_id:
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


def _object_bounds(
    snapshot: BoardIRSnapshot,
) -> dict[str, tuple[int, int, int, int]]:
    """Index every referenced object's bounding box for around_ref resolution."""

    bounds: dict[str, tuple[int, int, int, int]] = {}
    content = snapshot.content
    for contour in content.outline:
        bounds[contour.id] = _ring_bounds(contour.outer)
    for pad in content.pads:
        half_x, half_y = (pad.size_x_nm + 1) // 2, (pad.size_y_nm + 1) // 2
        if pad.rotation_udeg // 90_000_000 % 2 == 1:
            half_x, half_y = half_y, half_x
        bounds[pad.id] = (
            pad.center.x - half_x,
            pad.center.y - half_y,
            pad.center.x + half_x,
            pad.center.y + half_y,
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
        half = (arc.width_nm + 1) // 2
        xs = [arc.start.x, arc.mid.x, arc.end.x]
        ys = [arc.start.y, arc.mid.y, arc.end.y]
        bounds[arc.id] = (min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)
    for zone in content.zones:
        bounds[zone.id] = _ring_bounds(zone.boundary)
    for keepout in content.keepouts:
        bounds[keepout.id] = _ring_bounds(keepout.boundary)
    return bounds


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
        return SceneRegion(
            min_x_nm=anchor[0] - radius,
            min_y_nm=anchor[1] - radius,
            max_x_nm=anchor[2] + radius,
            max_y_nm=anchor[3] + radius,
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
            "drill_nm": (None if pad.drill_x_nm is None else [pad.drill_x_nm, pad.drill_y_nm]),
        },
        ref_stability=_ref_stability(pad.id),
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
    )


def _read_annotations(source: bytes, limits: ParseLimits) -> tuple[SceneAnnotation, ...]:
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
        layer_id = layer_of(node)
        for slot, payload in enumerate(leading_atoms(node)):
            if not payload:
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
    return tuple(collected)


@dataclass(frozen=True, slots=True)
class CircuitScene:
    """One immutable, region-scoped observation of a board."""

    board_path: str
    board_revision: str
    request: CircuitSceneRequest
    supported: bool
    snapshot_digest: str | None = None
    region: SceneRegion | None = None
    static_objects: Mapping[str, tuple[SceneObject, ...]] = field(default_factory=dict)
    mutable_objects: Mapping[str, tuple[SceneObject, ...]] = field(default_factory=dict)
    annotations: tuple[SceneAnnotation, ...] = ()
    render: SceneRenderEvidence | None = None
    render_bytes: bytes | None = None
    objects_omitted: int = 0
    ceiling_hit: str | None = None
    content_derived_ref_count: int = 0
    request_scoped_ref_count: int = 0
    conversion_diagnostic_counts: Mapping[str, int] = field(default_factory=dict)
    scene_version: str = SCENE_VERSION
    schema_version: str = SCHEMA_VERSION

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
            "static": {
                name: [item.to_dict() for item in self.static_objects.get(name, ())]
                for name in _STATIC_KINDS
            },
            "mutable": {
                name: [item.to_dict() for item in self.mutable_objects.get(name, ())]
                for name in _MUTABLE_KINDS
            },
            "annotations": [item.to_dict() for item in self.annotations],
            # Evidence only. The bytes themselves are delivered as a capability by the MCP
            # gateway or written to an explicit path by the CLI, never inlined here.
            "render": None if self.render is None else self.render.to_dict(),
            "truncation": {
                "objects_returned": total,
                "objects_omitted": self.objects_omitted,
                "ceiling_hit": self.ceiling_hit,
            },
            "ref_stability": {
                "all_board_refs_native": self.content_derived_ref_count == 0,
                "content_derived_count": self.content_derived_ref_count,
                "request_scoped_count": self.request_scoped_ref_count,
            },
            "conversion_diagnostic_counts": dict(self.conversion_diagnostic_counts),
        }


def observe_board_scene(payload: Any, settings: Settings) -> CircuitScene:
    """Observe one workspace board as a bounded, region-scoped typed scene."""

    if not isinstance(settings, Settings):
        raise CircuitSceneError("scene settings are malformed")
    request = parse_circuit_scene_request(payload)

    board = read_workspace_file(
        settings.workspace,
        request.board,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    relative_path = board.path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    source = board.content
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"

    default_limits = ParseLimits()
    limits = replace(
        default_limits,
        max_input_bytes=min(default_limits.max_input_bytes, settings.max_board_bytes),
    )
    conversion = parse_kicad_bytes(source, request.profile(), limits)
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
    budget = _Budget(
        max_objects=settings.max_scene_objects,
        max_vertices=settings.max_scene_vertices,
    )
    every_layer = tuple(layer.id for layer in content.copper_layers)

    static: dict[str, list[SceneObject]] = {name: [] for name in _STATIC_KINDS}
    mutable: dict[str, list[SceneObject]] = {name: [] for name in _MUTABLE_KINDS}

    def consider(
        candidate: SceneObject,
        layer_ids: tuple[str, ...],
        vertex_count: int,
        bucket: list[SceneObject],
    ) -> None:
        """Admit one object if it meets the region, the layer filter and the ceilings."""

        box = bounds.get(candidate.ref_id)
        if box is None or not region.overlaps(*box):
            return
        if not _selected(layer_ids, request.layers):
            return
        if not budget.admit(vertex_count):
            return
        bucket.append(candidate)

    for contour in content.outline:
        consider(
            SceneObject(
                ref_id=contour.id,
                kind="outline",
                layer_ids=(),
                geometry={"outer_nm": _points(contour.outer)},
                ref_stability=_ref_stability(contour.id),
            ),
            every_layer,
            len(contour.outer.points),
            static["outline"],
        )
    for pad in content.pads:
        consider(_pad_object(pad), tuple(pad.layer_ids), 4, static["pads"])
    for keepout in content.keepouts:
        consider(
            _keepout_object(keepout),
            tuple(keepout.layer_ids),
            len(keepout.boundary.points),
            static["keepouts"],
        )
    for segment in content.segments:
        consider(_segment_object(segment), (segment.layer_id,), 2, mutable["segments"])
    for arc in content.arcs:
        consider(_arc_object(arc), (arc.layer_id,), 3, mutable["arcs"])
    for via in content.vias:
        consider(_via_object(via, every_layer), every_layer, 4, mutable["vias"])
    for zone in content.zones:
        consider(
            _zone_object(zone),
            (zone.layer_id,),
            len(zone.boundary.points),
            mutable["zones"],
        )

    # Rules are board-wide rather than positional, so they are reported whole.
    for net_class in content.constraints.net_classes:
        if budget.admit(1):
            static["rules"].append(
                SceneObject(
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
            )

    annotations: tuple[SceneAnnotation, ...] = ()
    if request.include_annotations:
        annotations = _read_annotations(source, limits)

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
        static_objects={name: tuple(items) for name, items in static.items()},
        mutable_objects={name: tuple(items) for name, items in mutable.items()},
        annotations=annotations,
        objects_omitted=budget.omitted,
        ceiling_hit=budget.ceiling_hit,
        render=render_evidence,
        render_bytes=render_bytes,
        content_derived_ref_count=content_derived,
        request_scoped_ref_count=request_scoped,
    )
