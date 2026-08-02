"""Frozen Board IR v0.1 domain types and exact unit conversions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

BOARD_IR_SCHEMA = "copper.board-ir"
BOARD_IR_SCHEMA_VERSION = "0.1.0"
JSON_SAFE_INTEGER = (1 << 53) - 1
NM_PER_MM = 1_000_000
UDEG_PER_DEGREE = 1_000_000
FULL_ROTATION_UDEG = 360 * UDEG_PER_DEGREE

_SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
_TYPED_ID = re.compile(
    r"^(?:layer|net|class|pad|via|segment|arc|zone|keepout|contour|rule):"
    r"[A-Za-z0-9_.:-]{1,160}$"
)
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


def _utf8_text(name: str, value: str, *, maximum: int = 512) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name} is malformed")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} contains an invalid Unicode surrogate") from error


def _typed_id(name: str, value: str, prefix: str) -> None:
    if not isinstance(value, str) or not value.startswith(prefix) or not _TYPED_ID.fullmatch(value):
        raise ValueError(f"{name} must be a stable {prefix.rstrip(':')} ID")


def _integer(name: str, value: int, *, minimum: int, maximum: int = JSON_SAFE_INTEGER) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported integer range")


def _positive(name: str, value: int) -> None:
    _integer(name, value, minimum=1)


def _sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be content-addressed with sha256")


def _tuple_of(name: str, value: object, item_type: type[object]) -> None:
    if not isinstance(value, tuple) or not all(isinstance(item, item_type) for item in value):
        raise ValueError(f"{name} must be an immutable tuple of {item_type.__name__}")


def mm_to_nm(token: str) -> int:
    """Convert a KiCad decimal millimetre token exactly, without rounding."""

    if not isinstance(token, str) or len(token) > 64 or not _DECIMAL.fullmatch(token):
        raise ValueError("millimetres must use non-exponent decimal notation")
    negative = token.startswith("-")
    unsigned = token[1:] if negative else token
    whole, _, fraction = unsigned.partition(".")
    retained = fraction[:6].ljust(6, "0")
    if any(digit != "0" for digit in fraction[6:]):
        raise ValueError("millimetre value has sub-nanometre precision")
    value = int(whole) * NM_PER_MM + int(retained)
    if negative:
        value = -value
    _integer("nanometre value", value, minimum=-JSON_SAFE_INTEGER)
    return 0 if value == 0 else value


def nm_to_mm(value: int) -> str:
    """Return the shortest exact non-exponent millimetre representation."""

    _integer("nanometre value", value, minimum=-JSON_SAFE_INTEGER)
    negative = value < 0
    whole, remainder = divmod(abs(value), NM_PER_MM)
    fraction = f"{remainder:06d}".rstrip("0")
    rendered = f"{whole}.{fraction}" if fraction else str(whole)
    return f"-{rendered}" if negative else rendered


def normalize_rotation_udeg(token: str) -> int:
    """Convert decimal degrees exactly to normalized integer microdegrees."""

    if not isinstance(token, str) or len(token) > 64 or not _DECIMAL.fullmatch(token):
        raise ValueError("rotation must use non-exponent decimal notation")
    negative = token.startswith("-")
    unsigned = token[1:] if negative else token
    whole, _, fraction = unsigned.partition(".")
    retained = fraction[:6].ljust(6, "0")
    if any(digit != "0" for digit in fraction[6:]):
        raise ValueError("rotation has sub-microdegree precision")
    raw = int(whole) * UDEG_PER_DEGREE + int(retained)
    if negative:
        raw = -raw
    _integer("rotation", raw, minimum=-JSON_SAFE_INTEGER)
    return raw % FULL_ROTATION_UDEG


@dataclass(frozen=True, slots=True, order=True)
class PointNM:
    """One exact Cartesian point in integer nanometres."""

    x: int
    y: int

    def __post_init__(self) -> None:
        _integer("x", self.x, minimum=-JSON_SAFE_INTEGER)
        _integer("y", self.y, minimum=-JSON_SAFE_INTEGER)


def signed_double_area(points: tuple[PointNM, ...]) -> int:
    """Return the exact signed doubled area; positive is counter-clockwise."""

    return sum(
        point.x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )


@dataclass(frozen=True, slots=True)
class Ring:
    """A closed polygon ring with no repeated closing vertex."""

    points: tuple[PointNM, ...]

    def __post_init__(self) -> None:
        _tuple_of("ring points", self.points, PointNM)
        if len(self.points) < 3 or len(set(self.points)) < 3:
            raise ValueError("ring must contain at least three distinct points")
        if len(set(self.points)) != len(self.points):
            raise ValueError("ring vertices must not repeat")
        if self.points[0] == self.points[-1]:
            raise ValueError("ring must omit its repeated closing point")
        if signed_double_area(self.points) == 0:
            raise ValueError("ring area must be non-zero")


@dataclass(frozen=True, slots=True)
class UnitSystem:
    """Closed unit declaration for Board IR v0.1."""

    distance: str = "nm"
    angle: str = "udeg"

    def __post_init__(self) -> None:
        if self.distance != "nm" or self.angle != "udeg":
            raise ValueError("Board IR v0.1 requires nm and udeg units")


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """Content-addressed source-file identity."""

    format: str
    revision: str
    format_version: str
    generator: str | None = None

    def __post_init__(self) -> None:
        _utf8_text("source format", self.format, maximum=64)
        _sha256("source revision", self.revision)
        _utf8_text("source format version", self.format_version, maximum=64)
        if self.generator is not None:
            _utf8_text("source generator", self.generator, maximum=128)


@dataclass(frozen=True, slots=True)
class Layer:
    """One copper layer in physical stack order."""

    id: str
    name: str
    index: int
    kind: str = "signal"

    def __post_init__(self) -> None:
        _typed_id("layer ID", self.id, "layer:")
        _utf8_text("layer name", self.name, maximum=128)
        _integer("layer index", self.index, minimum=0, maximum=255)
        if self.kind not in {"signal", "plane", "mixed"}:
            raise ValueError("layer kind is unsupported")


@dataclass(frozen=True, slots=True)
class Net:
    """One electrical net; names are data and IDs are routing identities."""

    id: str
    name: str

    def __post_init__(self) -> None:
        _typed_id("net ID", self.id, "net:")
        _utf8_text("net name", self.name, maximum=512)


@dataclass(frozen=True, slots=True)
class NetClass:
    """Typed default clearance, width, and via rules for a net class."""

    id: str
    name: str
    clearance_nm: int
    track_width_nm: int
    via_diameter_nm: int
    via_drill_nm: int

    def __post_init__(self) -> None:
        _typed_id("net-class ID", self.id, "class:")
        _utf8_text("net-class name", self.name, maximum=128)
        _integer("clearance", self.clearance_nm, minimum=0)
        _positive("track width", self.track_width_nm)
        _positive("via diameter", self.via_diameter_nm)
        _positive("via drill", self.via_drill_nm)
        if self.via_drill_nm >= self.via_diameter_nm:
            raise ValueError("via drill must be smaller than via diameter")


@dataclass(frozen=True, slots=True)
class NetClassAssignment:
    """Assign one net to exactly one net class."""

    net_id: str
    net_class_id: str

    def __post_init__(self) -> None:
        _typed_id("net ID", self.net_id, "net:")
        _typed_id("net-class ID", self.net_class_id, "class:")


@dataclass(frozen=True, slots=True)
class DifferentialPairRule:
    """Explicit differential-pair geometry and skew limits."""

    id: str
    positive_net_id: str
    negative_net_id: str
    width_nm: int
    gap_nm: int
    max_skew_nm: int

    def __post_init__(self) -> None:
        _typed_id("rule ID", self.id, "rule:")
        _typed_id("positive net ID", self.positive_net_id, "net:")
        _typed_id("negative net ID", self.negative_net_id, "net:")
        if self.positive_net_id == self.negative_net_id:
            raise ValueError("differential-pair nets must differ")
        _positive("differential-pair width", self.width_nm)
        _positive("differential-pair gap", self.gap_nm)
        _integer("maximum skew", self.max_skew_nm, minimum=0)


@dataclass(frozen=True, slots=True)
class LengthRule:
    """Inclusive routed-length bounds for one net."""

    id: str
    net_id: str
    minimum_nm: int
    maximum_nm: int

    def __post_init__(self) -> None:
        _typed_id("rule ID", self.id, "rule:")
        _typed_id("net ID", self.net_id, "net:")
        _integer("minimum length", self.minimum_nm, minimum=0)
        _integer("maximum length", self.maximum_nm, minimum=0)
        if self.minimum_nm > self.maximum_nm:
            raise ValueError("minimum length cannot exceed maximum length")


@dataclass(frozen=True, slots=True)
class ConstraintSet:
    """All typed routing constraints carried by a snapshot."""

    net_classes: tuple[NetClass, ...]
    assignments: tuple[NetClassAssignment, ...]
    differential_pairs: tuple[DifferentialPairRule, ...] = ()
    length_rules: tuple[LengthRule, ...] = ()

    def __post_init__(self) -> None:
        _tuple_of("net classes", self.net_classes, NetClass)
        _tuple_of("net-class assignments", self.assignments, NetClassAssignment)
        _tuple_of("differential-pair rules", self.differential_pairs, DifferentialPairRule)
        _tuple_of("length rules", self.length_rules, LengthRule)
        if not self.net_classes:
            raise ValueError("at least one net class is required")


@dataclass(frozen=True, slots=True)
class OutlineContour:
    """One outer Edge.Cuts ring and optional hole rings."""

    id: str
    outer: Ring
    holes: tuple[Ring, ...] = ()

    def __post_init__(self) -> None:
        _typed_id("contour ID", self.id, "contour:")
        if not isinstance(self.outer, Ring):
            raise ValueError("contour outer boundary must be a ring")
        _tuple_of("contour holes", self.holes, Ring)


class PadKind(StrEnum):
    SMD = "smd"
    THROUGH_HOLE = "through_hole"
    NPTH = "np_through_hole"


class PadShape(StrEnum):
    CIRCLE = "circle"
    RECT = "rect"
    OVAL = "oval"
    ROUNDRECT = "roundrect"


@dataclass(frozen=True, slots=True)
class Pad:
    """One exact pad access object."""

    id: str
    net_id: str | None
    center: PointNM
    rotation_udeg: int
    shape: PadShape
    kind: PadKind
    size_x_nm: int
    size_y_nm: int
    roundrect_radius_nm: int | None
    drill_x_nm: int | None
    drill_y_nm: int | None
    layer_ids: tuple[str, ...]
    locked: bool = False

    def __post_init__(self) -> None:
        _typed_id("pad ID", self.id, "pad:")
        if not isinstance(self.center, PointNM):
            raise ValueError("pad center must be a PointNM")
        if self.net_id is not None:
            _typed_id("net ID", self.net_id, "net:")
        if not isinstance(self.shape, PadShape) or not isinstance(self.kind, PadKind):
            raise ValueError("pad kind and shape must use Board IR enums")
        _integer("pad rotation", self.rotation_udeg, minimum=0, maximum=FULL_ROTATION_UDEG - 1)
        _positive("pad width", self.size_x_nm)
        _positive("pad height", self.size_y_nm)
        if self.shape is PadShape.CIRCLE and self.size_x_nm != self.size_y_nm:
            raise ValueError("circle pad dimensions must be equal")
        if self.shape is PadShape.ROUNDRECT:
            if self.roundrect_radius_nm is None:
                raise ValueError("roundrect pad requires an exact corner radius")
            _positive("roundrect radius", self.roundrect_radius_nm)
            if self.roundrect_radius_nm * 2 > min(self.size_x_nm, self.size_y_nm):
                raise ValueError("roundrect radius cannot exceed half the short pad side")
        elif self.roundrect_radius_nm is not None:
            raise ValueError("only roundrect pads may carry a corner radius")
        if (self.drill_x_nm is None) is not (self.drill_y_nm is None):
            raise ValueError("pad drill dimensions must both be present or absent")
        if self.drill_x_nm is not None and self.drill_y_nm is not None:
            _positive("pad drill width", self.drill_x_nm)
            _positive("pad drill height", self.drill_y_nm)
            if self.drill_x_nm > self.size_x_nm or self.drill_y_nm > self.size_y_nm:
                raise ValueError("pad drill cannot exceed pad size")
        if self.kind is PadKind.SMD and self.drill_x_nm is not None:
            raise ValueError("SMD pads cannot carry a drill")
        if self.kind is not PadKind.SMD and self.drill_x_nm is None:
            raise ValueError("through-hole pads require a drill")
        if self.kind is PadKind.NPTH and self.net_id is not None:
            raise ValueError("NPTH pads cannot belong to an electrical net")
        _tuple_of("pad layer IDs", self.layer_ids, str)
        if not self.layer_ids:
            raise ValueError("pad must reference at least one copper layer")
        for layer_id in self.layer_ids:
            _typed_id("layer ID", layer_id, "layer:")
        if not isinstance(self.locked, bool):
            raise ValueError("pad locked flag must be boolean")


class ViaKind(StrEnum):
    THROUGH = "through"


@dataclass(frozen=True, slots=True)
class Via:
    id: str
    net_id: str
    center: PointNM
    diameter_nm: int
    drill_nm: int
    start_layer_id: str
    end_layer_id: str
    kind: ViaKind = ViaKind.THROUGH
    locked: bool = False

    def __post_init__(self) -> None:
        _typed_id("via ID", self.id, "via:")
        _typed_id("net ID", self.net_id, "net:")
        if not isinstance(self.center, PointNM):
            raise ValueError("via center must be a PointNM")
        if not isinstance(self.kind, ViaKind):
            raise ValueError("via kind must use the Board IR enum")
        if self.kind is not ViaKind.THROUGH:
            raise ValueError("Board IR v0.1 supports through vias only")
        _positive("via diameter", self.diameter_nm)
        _positive("via drill", self.drill_nm)
        if self.drill_nm >= self.diameter_nm:
            raise ValueError("via drill must be smaller than via diameter")
        _typed_id("start layer ID", self.start_layer_id, "layer:")
        _typed_id("end layer ID", self.end_layer_id, "layer:")
        if self.start_layer_id == self.end_layer_id:
            raise ValueError("via layer span must contain two distinct layers")
        if not isinstance(self.locked, bool):
            raise ValueError("via locked flag must be boolean")


@dataclass(frozen=True, slots=True)
class Segment:
    id: str
    net_id: str
    layer_id: str
    start: PointNM
    end: PointNM
    width_nm: int
    locked: bool = False

    def __post_init__(self) -> None:
        _typed_id("segment ID", self.id, "segment:")
        _typed_id("net ID", self.net_id, "net:")
        _typed_id("layer ID", self.layer_id, "layer:")
        if not isinstance(self.start, PointNM) or not isinstance(self.end, PointNM):
            raise ValueError("segment endpoints must be PointNM values")
        if self.start == self.end:
            raise ValueError("segment endpoints must differ")
        _positive("segment width", self.width_nm)
        if not isinstance(self.locked, bool):
            raise ValueError("segment locked flag must be boolean")


@dataclass(frozen=True, slots=True)
class Arc:
    id: str
    net_id: str
    layer_id: str
    start: PointNM
    mid: PointNM
    end: PointNM
    width_nm: int
    locked: bool = False

    def __post_init__(self) -> None:
        _typed_id("arc ID", self.id, "arc:")
        _typed_id("net ID", self.net_id, "net:")
        _typed_id("layer ID", self.layer_id, "layer:")
        if not all(isinstance(point, PointNM) for point in (self.start, self.mid, self.end)):
            raise ValueError("arc control points must be PointNM values")
        if len({self.start, self.mid, self.end}) != 3:
            raise ValueError("arc points must be distinct")
        cross = (self.mid.x - self.start.x) * (self.end.y - self.start.y) - (
            self.mid.y - self.start.y
        ) * (self.end.x - self.start.x)
        if cross == 0:
            raise ValueError("arc points must not be collinear")
        _positive("arc width", self.width_nm)
        if not isinstance(self.locked, bool):
            raise ValueError("arc locked flag must be boolean")


class ZonePadConnection(StrEnum):
    """How pads connect to a copper zone."""

    THERMAL = "thermal"
    THROUGH_HOLE_THERMAL = "through_hole_thermal"
    SOLID = "solid"
    NONE = "none"


class ZoneIslandRemoval(StrEnum):
    """Supported island-removal policies for solid copper zones."""

    ALWAYS = "always"
    NEVER = "never"


@dataclass(frozen=True, slots=True)
class Zone:
    id: str
    net_id: str
    layer_id: str
    boundary: Ring
    clearance_nm: int
    min_thickness_nm: int
    thermal_gap_nm: int
    thermal_bridge_width_nm: int
    priority: int = 0
    pad_connection: ZonePadConnection = ZonePadConnection.THERMAL
    island_removal: ZoneIslandRemoval = ZoneIslandRemoval.ALWAYS
    fill_mode: str = "solid"
    locked: bool = False

    def __post_init__(self) -> None:
        _typed_id("zone ID", self.id, "zone:")
        _typed_id("net ID", self.net_id, "net:")
        _typed_id("layer ID", self.layer_id, "layer:")
        if not isinstance(self.boundary, Ring):
            raise ValueError("zone boundary must be a ring")
        _integer("zone clearance", self.clearance_nm, minimum=0)
        _positive("zone minimum thickness", self.min_thickness_nm)
        _integer("zone thermal gap", self.thermal_gap_nm, minimum=0)
        _integer("zone thermal bridge width", self.thermal_bridge_width_nm, minimum=0)
        _integer("zone priority", self.priority, minimum=0)
        if not isinstance(self.pad_connection, ZonePadConnection):
            raise ValueError("zone pad connection must use the Board IR enum")
        if not isinstance(self.island_removal, ZoneIslandRemoval):
            raise ValueError("zone island removal must use the Board IR enum")
        if self.pad_connection in {
            ZonePadConnection.THERMAL,
            ZonePadConnection.THROUGH_HOLE_THERMAL,
        }:
            _positive("zone thermal gap", self.thermal_gap_nm)
            _positive("zone thermal bridge width", self.thermal_bridge_width_nm)
        if self.fill_mode != "solid":
            raise ValueError("Board IR v0.1 supports solid zones only")
        if not isinstance(self.locked, bool):
            raise ValueError("zone locked flag must be boolean")


@dataclass(frozen=True, slots=True)
class Keepout:
    id: str
    layer_ids: tuple[str, ...]
    boundary: Ring
    prohibit_tracks: bool
    prohibit_vias: bool
    prohibit_pads: bool
    prohibit_zones: bool
    prohibit_footprints: bool
    locked: bool = False

    def __post_init__(self) -> None:
        _typed_id("keepout ID", self.id, "keepout:")
        _tuple_of("keepout layer IDs", self.layer_ids, str)
        if not self.layer_ids:
            raise ValueError("keepout must reference at least one copper layer")
        if not isinstance(self.boundary, Ring):
            raise ValueError("keepout boundary must be a ring")
        for layer_id in self.layer_ids:
            _typed_id("layer ID", layer_id, "layer:")
        for name in (
            "prohibit_tracks",
            "prohibit_vias",
            "prohibit_pads",
            "prohibit_zones",
            "prohibit_footprints",
            "locked",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"keepout {name} flag must be boolean")


@dataclass(frozen=True, slots=True)
class BoardIRContent:
    """Canonical board body hashed by the snapshot envelope."""

    units: UnitSystem
    source: SourceInfo
    constraint_digest: str
    outline: tuple[OutlineContour, ...]
    copper_layers: tuple[Layer, ...]
    nets: tuple[Net, ...]
    constraints: ConstraintSet
    pads: tuple[Pad, ...] = ()
    vias: tuple[Via, ...] = ()
    segments: tuple[Segment, ...] = ()
    arcs: tuple[Arc, ...] = ()
    zones: tuple[Zone, ...] = ()
    keepouts: tuple[Keepout, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.units, UnitSystem) or not isinstance(self.source, SourceInfo):
            raise ValueError("Board IR units and source metadata are malformed")
        _sha256("constraint digest", self.constraint_digest)
        _tuple_of("outline", self.outline, OutlineContour)
        _tuple_of("copper layers", self.copper_layers, Layer)
        _tuple_of("nets", self.nets, Net)
        if not isinstance(self.constraints, ConstraintSet):
            raise ValueError("Board IR constraints must be a ConstraintSet")
        _tuple_of("pads", self.pads, Pad)
        _tuple_of("vias", self.vias, Via)
        _tuple_of("segments", self.segments, Segment)
        _tuple_of("arcs", self.arcs, Arc)
        _tuple_of("zones", self.zones, Zone)
        _tuple_of("keepouts", self.keepouts, Keepout)
        if not self.outline:
            raise ValueError("board outline must contain at least one contour")
        if not self.copper_layers:
            raise ValueError("board must contain at least one copper layer")


@dataclass(frozen=True, slots=True)
class BoardIRSnapshot:
    """Self-verifying content-addressed Board IR envelope."""

    snapshot_digest: str
    content: BoardIRContent
    schema: str = BOARD_IR_SCHEMA
    schema_version: str = BOARD_IR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.content, BoardIRContent):
            raise ValueError("snapshot content must be a BoardIRContent")
        if self.schema != BOARD_IR_SCHEMA:
            raise ValueError("Board IR schema discriminator is unsupported")
        if self.schema_version != BOARD_IR_SCHEMA_VERSION:
            raise ValueError("Board IR schema version is unsupported")
        _sha256("snapshot digest", self.snapshot_digest)
