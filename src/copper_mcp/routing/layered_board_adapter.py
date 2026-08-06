"""Board IR adapter for the bounded, proposal-only ordered-layer router.

The adapter is intentionally conservative.  It accepts two through eight ordered signal layers, a
rectangular hole-free board, two pads, and foreign orthogonal copper/rectangular keepout envelopes.
It emits an immutable layered candidate but does not write KiCad bytes, call KiCad, or grant apply
authority.

The single exception to "conservative" is optional freshness-verified zone fill (ADR-0021,
ADR-0070): islands a caller has already proved current for this board revision replace the foreign
zone's outline envelope with their own bounding boxes, after the revision, backing-zone, and
outline-containment gates all pass.  Unverified zones, failed gates, and absent evidence all keep
the envelope, so every direction of error stays refusal-side.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import TypeAlias, cast

from copper_mcp.board_ir import (
    BoardIRSnapshot,
    BoardIRValidationError,
    Pad,
    PadShape,
    PointNM,
    Ring,
    Segment,
    verify_snapshot,
)
from copper_mcp.board_ir.types import Layer, NetClass
from copper_mcp.routing.astar import VerifiedFill
from copper_mcp.routing.layered_astar import (
    MAX_EXPLICIT_VIAS,
    MAX_LAYERS,
    LayeredAStarRequest,
    LayeredAStarResult,
    LayeredAStarSettings,
    LayeredFailureCode,
    LayeredObstacle,
    LayeredPoint,
    LayeredStep,
    effective_max_vias,
    route_layered,
)
from copper_mcp.routing.layered_contracts import (
    LayeredRouteCandidate,
    LayeredRouteCost,
    LayeredRouteDiagnostic,
    LayeredRouteFailureCode,
    LayeredRouteMetrics,
    LayeredRoutePatch,
    LayeredRoutePath,
    LayeredRouteResult,
    LayeredRouteVia,
    canonical_layered_candidate_bytes,
    verify_layered_candidate_id,
)

_Rect: TypeAlias = tuple[int, int, int, int]
CancellationCheck: TypeAlias = Callable[[], bool]
LAYERED_ROUTER_VERSION = "layered-board-a-star/0.1.0"
LAYERED_ROUTING_POLICY = "board-layered-a-star-v1"
_EMPTY_DIGEST = f"sha256:{'0' * 64}"
_MAX_SAFE_INT = (1 << 53) - 1

# Keep these limits aligned with the pure layered planner.  The adapter validates settings before
# deriving physical obstacles, so malformed values cannot reach arithmetic or budget comparisons
# and escape the non-throwing result contract.
_MAX_COST = 1_000_000_000
_MAX_EXPANSIONS = 1_000_000
_MAX_NODES = 500_000
_MAX_OBSTACLES = 4_096
_MAX_OBSTACLE_CHECKS = 10_000_000
_MAX_FILL_VERTICES = 4_096


@dataclass(frozen=True, slots=True)
class LayeredRouteRequest:
    """A two-pad Board IR routing request over a bounded ordered signal stack.

    ``start_layer_id`` and ``end_layer_id`` are explicit because through-hole pads can be
    reachable on several layers.  Omitting one is accepted only when the corresponding pad exposes
    exactly one supported signal layer.
    """

    board_revision: str
    net_id: str
    start_pad_id: str
    end_pad_id: str
    seed: int = 0
    start_layer_id: str | None = None
    end_layer_id: str | None = None
    expected_revision: str | None = None
    grid_step_nm: int = 250_000
    settings: LayeredAStarSettings = field(default_factory=LayeredAStarSettings)
    verified_fill: tuple[VerifiedFill, ...] = ()
    """Poured copper a caller has already bound to freshness evidence (ADR-0021).

    Supplying nothing keeps the conservative behaviour: every foreign zone contributes its whole
    outline envelope.  Supplying islands is a claim that they are the *complete* filled copper for
    their ``(net_id, layer_id)`` on this exact board revision, and preparation verifies the
    revision, the backing zone, and outline containment before it will shrink an envelope.
    """


def _digest(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        return False
    try:
        int(value[7:], 16)
    except ValueError:
        return False
    return True


def _typed(value: object, prefix: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and 1 <= len(value.removeprefix(prefix)) <= 160
        and all(
            character.isascii() and (character.isalnum() or character in "_.:-")
            for character in value
        )
    )


def _invalid_request(request: object) -> str | None:
    if not isinstance(request, LayeredRouteRequest):
        return "request must be a LayeredRouteRequest"
    if not _digest(request.board_revision):
        return "board revision is malformed"
    if request.expected_revision is not None and not _digest(request.expected_revision):
        return "expected revision is malformed"
    if (
        request.expected_revision is not None
        and request.expected_revision != request.board_revision
    ):
        return "board revision does not match expected revision"
    for name, value, prefix in (
        ("net ID", request.net_id, "net:"),
        ("start pad ID", request.start_pad_id, "pad:"),
        ("end pad ID", request.end_pad_id, "pad:"),
    ):
        if not _typed(value, prefix):
            return f"{name} is malformed"
    if request.start_pad_id == request.end_pad_id:
        return "route endpoints must be distinct"
    layer_values: tuple[tuple[str, str | None], ...] = (
        ("start layer ID", request.start_layer_id),
        ("end layer ID", request.end_layer_id),
    )
    for name, layer_value in layer_values:
        if layer_value is not None and not _typed(layer_value, "layer:"):
            return f"{name} is malformed"
    if (
        isinstance(request.seed, bool)
        or not isinstance(request.seed, int)
        or not 0 <= request.seed <= _MAX_SAFE_INT
    ):
        return "seed is malformed"
    if (
        isinstance(request.grid_step_nm, bool)
        or not isinstance(request.grid_step_nm, int)
        or not 0 < request.grid_step_nm <= _MAX_SAFE_INT
    ):
        return "grid step must be positive"
    settings_obj: object = request.settings
    if not isinstance(settings_obj, LayeredAStarSettings):
        return "settings must be a LayeredAStarSettings value"
    settings_limits: tuple[tuple[str, object, int], ...] = (
        ("move cost", settings_obj.move_cost, _MAX_COST),
        ("via cost", settings_obj.via_cost, _MAX_COST),
        ("expansion budget", settings_obj.max_expansions, _MAX_EXPANSIONS),
        ("node budget", settings_obj.max_nodes, _MAX_NODES),
        ("obstacle budget", settings_obj.max_obstacles, _MAX_OBSTACLES),
        ("obstacle-check budget", settings_obj.max_obstacle_checks, _MAX_OBSTACLE_CHECKS),
    )
    for setting_name, setting_value, setting_maximum in settings_limits:
        if (
            isinstance(setting_value, bool)
            or not isinstance(setting_value, int)
            or not 1 <= setting_value <= setting_maximum
        ):
            return f"{setting_name} must be a positive integer"
    if settings_obj.max_vias is not None and (
        isinstance(settings_obj.max_vias, bool)
        or not isinstance(settings_obj.max_vias, int)
        or not 0 <= settings_obj.max_vias <= MAX_EXPLICIT_VIAS
    ):
        return "via budget must be a non-negative integer"
    return _invalid_verified_fill(request.verified_fill)


def _invalid_verified_fill(fill: object) -> str | None:
    """Reject malformed fill evidence at the input boundary, before any snapshot work.

    Every island is later scanned once for its bounding box, so both the island count and the
    vertex count are hard input limits rather than post-hoc metrics: a caller cannot buy unbounded
    preparation work by handing over one enormous ring.
    """

    if not isinstance(fill, tuple):
        return "verified fill must be a tuple"
    if len(fill) > _MAX_OBSTACLES:
        return "verified fill island count exceeds the obstacle ceiling"
    for island in fill:
        if not isinstance(island, VerifiedFill):
            return "verified fill entry must be a VerifiedFill value"
        if not _typed(island.net_id, "net:") or not _typed(island.layer_id, "layer:"):
            return "verified fill island identity is malformed"
        if not _digest(island.source_revision):
            return "verified fill source revision is malformed"
        points: object = island.points
        if not isinstance(points, tuple) or not 3 <= len(points) <= _MAX_FILL_VERTICES:
            return "verified fill island is not a bounded polygon"
        for point in points:
            if not isinstance(point, PointNM):
                return "verified fill island vertex is malformed"
            if abs(point.x) > _MAX_SAFE_INT or abs(point.y) > _MAX_SAFE_INT:
                return "verified fill island vertex is out of range"
    return None


def _diagnostic(
    code: LayeredRouteFailureCode,
    message: str,
    *,
    search: LayeredAStarResult | None = None,
) -> LayeredRouteResult:
    metrics = search.metrics if search is not None else None
    return LayeredRouteResult(
        diagnostic=LayeredRouteDiagnostic(
            code=code,
            message=message,
            expanded_states=metrics.expanded_nodes if metrics is not None else 0,
            obstacle_checks=metrics.obstacle_checks if metrics is not None else 0,
        )
    )


def _axis_aligned_rectangle(ring: Ring) -> _Rect | None:
    points = ring.points
    xs = sorted({point.x for point in points})
    ys = sorted({point.y for point in points})
    if len(points) != 4 or len(xs) != 2 or len(ys) != 2:
        return None
    if {(point.x, point.y) for point in points} != {
        (xs[0], ys[0]),
        (xs[0], ys[1]),
        (xs[1], ys[0]),
        (xs[1], ys[1]),
    }:
        return None
    return xs[0], ys[0], xs[1], ys[1]


def _pad_bounds(pad: Pad) -> _Rect:
    if pad.rotation_udeg:
        # The sum-of-sides envelope is conservative for arbitrary rotation. Exact polygon
        # projection belongs to the KiCad adapter, not this proposal seam.
        half_x = half_y = (pad.size_x_nm + pad.size_y_nm + 1) // 2
    elif pad.shape is PadShape.RECT:
        half_x = (pad.size_x_nm + 1) // 2
        half_y = (pad.size_y_nm + 1) // 2
    elif pad.shape in {PadShape.CIRCLE, PadShape.OVAL, PadShape.ROUNDRECT}:
        half_x = (pad.size_x_nm + 1) // 2
        half_y = (pad.size_y_nm + 1) // 2
    else:  # pragma: no cover - Board IR validation keeps this enum closed
        raise ValueError("pad shape is unsupported")
    return (
        pad.center.x - half_x,
        pad.center.y - half_y,
        pad.center.x + half_x,
        pad.center.y + half_y,
    )


def _segment_bounds(segment: Segment) -> _Rect | None:
    start, end = segment.start, segment.end
    if start.x != end.x and start.y != end.y:
        return None
    half_width = (segment.width_nm + 1) // 2
    return (
        min(start.x, end.x) - half_width,
        min(start.y, end.y) - half_width,
        max(start.x, end.x) + half_width,
        max(start.y, end.y) + half_width,
    )


def _points_bounds(points: tuple[PointNM, ...]) -> _Rect:
    """Return the exact integer bounding box of a non-empty point ring."""

    return (
        min(point.x for point in points),
        min(point.y for point in points),
        max(point.x for point in points),
        max(point.y for point in points),
    )


def _contains(outer: _Rect, inner: _Rect) -> bool:
    """Exact integer closed containment of one axis-aligned rectangle in another."""

    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def _inflate(rectangle: _Rect, margin: int) -> _Rect:
    return (
        rectangle[0] - margin,
        rectangle[1] - margin,
        rectangle[2] + margin,
        rectangle[3] + margin,
    )


def _cell_obstacle(
    rectangle: _Rect,
    *,
    layer: int,
    origin: PointNM,
    step: int,
) -> LayeredObstacle:
    # Add half a cell: node-only occupancy otherwise lets an edge pass between two apparently
    # clear centers while crossing a physical obstacle.
    margin = (step + 1) // 2
    inflated = _inflate(rectangle, margin)
    min_x = -((-(inflated[0] - origin.x)) // step)
    min_y = -((-(inflated[1] - origin.y)) // step)
    max_x = -((-(inflated[2] - origin.x)) // step)
    max_y = -((-(inflated[3] - origin.y)) // step)
    return LayeredObstacle(layer, min_x, min_y, max_x, max_y)


def _resolve_net_class(snapshot: BoardIRSnapshot, net_id: str) -> NetClass | None:
    constraints = snapshot.content.constraints
    assignment = next((item for item in constraints.assignments if item.net_id == net_id), None)
    if assignment is None:
        return None
    return next(
        (item for item in constraints.net_classes if item.id == assignment.net_class_id),
        None,
    )


def _layer_order(snapshot: BoardIRSnapshot) -> tuple[Layer, ...]:
    return tuple(sorted(snapshot.content.copper_layers, key=lambda layer: (layer.index, layer.id)))


def has_exactly_two_signal_layers(snapshot: BoardIRSnapshot) -> bool:
    """Return whether a converted board remains within the public two-layer contract.

    The generalized router is an internal seam.  File-backed preview, live preview, and durable
    jobs call this predicate explicitly before they hand a snapshot to that seam.
    """

    layers = _layer_order(snapshot)
    return len(layers) == 2 and all(layer.kind == "signal" for layer in layers)


def _map_failure(result: LayeredAStarResult) -> LayeredRouteResult:
    assert result.diagnostic is not None
    code = {
        LayeredFailureCode.INVALID_REQUEST: LayeredRouteFailureCode.INVALID_REQUEST,
        LayeredFailureCode.STALE_REVISION: LayeredRouteFailureCode.STALE_REVISION,
        LayeredFailureCode.GRID_BUDGET_EXCEEDED: LayeredRouteFailureCode.GRID_BUDGET_EXCEEDED,
        LayeredFailureCode.OBSTACLE_BUDGET_EXCEEDED: (
            LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED
        ),
        LayeredFailureCode.SEARCH_BUDGET_EXCEEDED: LayeredRouteFailureCode.SEARCH_BUDGET_EXCEEDED,
        LayeredFailureCode.CANCELLED: LayeredRouteFailureCode.CANCELLED,
        LayeredFailureCode.NO_PATH: LayeredRouteFailureCode.NO_PATH,
    }[result.diagnostic.code]
    return _diagnostic(code, result.diagnostic.message, search=result)


def _compress(points: list[PointNM]) -> tuple[PointNM, ...]:
    compressed: list[PointNM] = []
    for point in points:
        if len(compressed) >= 2:
            first, middle = compressed[-2:]
            if (first.x == middle.x == point.x) or (first.y == middle.y == point.y):
                compressed[-1] = point
                continue
        compressed.append(point)
    return tuple(compressed)


def _via_span(layer_ids: tuple[str, ...], from_layer: int, to_layer: int) -> tuple[str, str]:
    """Return the recorded layer pair of one full-stack through via.

    Board IR v0.2 models only the full-stack through via, and every consumer reads the recorded
    pair as an unordered span: the KiCad serializer and the structural verifier both compare it as
    a set, and the serializer always writes the canonical outer ordering.  The order of the pair
    therefore carries no physical meaning.

    On exactly two signal layers the traversed pair *is* the outer stack span, so the historical
    traversal ordering is retained verbatim.  That ordering is part of the candidate identity bytes
    of every two-layer candidate ever issued, so changing it would silently invalidate persisted
    ADR-0043 jobs, ADR-0047 manifests, and ADR-0048 exports.  On three through eight layers there
    is no legacy identity and a traversed inner pair would misstate a full-stack via as a
    blind/buried one, so the canonical outer span is recorded instead.
    """

    if len(layer_ids) == 2:
        return layer_ids[from_layer], layer_ids[to_layer]
    return layer_ids[0], layer_ids[-1]


def _paths_and_vias(
    steps: tuple[LayeredStep, ...],
    *,
    layer_ids: tuple[str, ...],
    origin: PointNM,
    start_point: PointNM,
    end_point: PointNM,
    step_nm: int,
    via_diameter_nm: int,
    via_drill_nm: int,
) -> tuple[tuple[LayeredRoutePath, ...], tuple[LayeredRouteVia, ...]] | None:
    if not steps or steps[0].kind != "start":
        return None
    paths: list[LayeredRoutePath] = []
    vias: list[LayeredRouteVia] = []
    current_layer = steps[0].layer
    current_points: list[PointNM] = []

    def physical(item: LayeredStep) -> PointNM:
        return PointNM(origin.x + item.x * step_nm, origin.y + item.y * step_nm)

    if physical(steps[0]) != start_point or physical(steps[-1]) != end_point:
        return None
    current_points.append(physical(steps[0]))
    for previous, current in pairwise(steps):
        if current.kind == "via":
            if (
                previous.x != current.x
                or previous.y != current.y
                or previous.layer == current.layer
            ):
                return None
            # Padstack-aware modes may eventually support a via directly on an endpoint pad.  The
            # current adapter blocks that transition conservatively; retain the zero-length guard
            # so a future mode cannot manufacture a degenerate path segment.
            if len(current_points) >= 2:
                paths.append(LayeredRoutePath(layer_ids[current_layer], _compress(current_points)))
            span = _via_span(layer_ids, previous.layer, current.layer)
            vias.append(
                LayeredRouteVia(
                    id=f"via:layered:{len(vias):04d}",
                    center=physical(previous),
                    diameter_nm=via_diameter_nm,
                    drill_nm=via_drill_nm,
                    start_layer_id=span[0],
                    end_layer_id=span[1],
                )
            )
            current_layer = current.layer
            current_points = [physical(current)]
            continue
        if current.kind != "move" or current.layer != previous.layer:
            return None
        if abs(current.x - previous.x) + abs(current.y - previous.y) != 1:
            return None
        current_points.append(physical(current))
    if len(current_points) >= 2:
        paths.append(LayeredRoutePath(layer_ids[current_layer], _compress(current_points)))
    if not paths:
        return None
    if paths[0].vertices[0] != start_point and (not vias or vias[0].center != start_point):
        return None
    if paths[-1].vertices[-1] != end_point and (not vias or vias[-1].center != end_point):
        return None
    return tuple(paths), tuple(vias)


class LayeredBoardRouter:
    """Pure Board IR → layered candidate adapter; never writes or invokes KiCad."""

    @property
    def name(self) -> str:
        return LAYERED_ROUTING_POLICY

    def propose(
        self,
        snapshot: BoardIRSnapshot,
        request: LayeredRouteRequest,
        *,
        cancelled: CancellationCheck | None = None,
    ) -> LayeredRouteResult:
        malformed = _invalid_request(request)
        if malformed is not None:
            if (
                isinstance(request, LayeredRouteRequest)
                and request.expected_revision is not None
                and _digest(request.board_revision)
                and _digest(request.expected_revision)
                and request.expected_revision != request.board_revision
            ):
                return _diagnostic(LayeredRouteFailureCode.STALE_REVISION, malformed)
            return _diagnostic(LayeredRouteFailureCode.INVALID_REQUEST, malformed)
        snapshot_obj: object = snapshot
        if not isinstance(snapshot_obj, BoardIRSnapshot):
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_SNAPSHOT, "board snapshot type is invalid"
            )
        snapshot = snapshot_obj
        if request.board_revision != snapshot.snapshot_digest:
            return _diagnostic(
                LayeredRouteFailureCode.STALE_REVISION, "request is stale for the Board IR snapshot"
            )
        try:
            verify_snapshot(snapshot)
        except (BoardIRValidationError, TypeError, ValueError):
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_SNAPSHOT, "Board IR snapshot verification failed"
            )
        cancelled_obj: object = cancelled
        if cancelled_obj is not None and not callable(cancelled_obj):
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST, "cancellation check must be callable"
            )
        cancellation_check = cast(CancellationCheck | None, cancelled_obj)

        layers = _layer_order(snapshot)
        if not 2 <= len(layers) <= MAX_LAYERS or any(layer.kind != "signal" for layer in layers):
            return _diagnostic(
                LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
                "layered routing requires two through eight ordered signal layers",
            )
        layer_ids = tuple(layer.id for layer in layers)
        layer_index = {layer.id: index for index, layer in enumerate(layers)}
        net_class = _resolve_net_class(snapshot, request.net_id)
        if net_class is None:
            return _diagnostic(
                LayeredRouteFailureCode.UNSUPPORTED_CONSTRAINT, "net class assignment is missing"
            )
        constraints = snapshot.content.constraints
        if any(
            request.net_id in {rule.positive_net_id, rule.negative_net_id}
            for rule in constraints.differential_pairs
        ) or any(rule.net_id == request.net_id for rule in constraints.length_rules):
            return _diagnostic(
                LayeredRouteFailureCode.UNSUPPORTED_CONSTRAINT,
                "the selected net has an unmodeled length or differential constraint",
            )
        outline = (
            snapshot.content.outline[0].outer
            if len(snapshot.content.outline) == 1 and not snapshot.content.outline[0].holes
            else None
        )
        board = _axis_aligned_rectangle(outline) if outline is not None else None
        if board is None:
            return _diagnostic(
                LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
                "layered routing requires one rectangular hole-free outline",
            )
        pads = {pad.id: pad for pad in snapshot.content.pads}
        start_pad, end_pad = pads.get(request.start_pad_id), pads.get(request.end_pad_id)
        if (
            start_pad is None
            or end_pad is None
            or start_pad.net_id != request.net_id
            or end_pad.net_id != request.net_id
        ):
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "route endpoints are not pads on the selected net",
            )
        net_pads = tuple(pad for pad in snapshot.content.pads if pad.net_id == request.net_id)
        if len(net_pads) != 2:
            return _diagnostic(
                LayeredRouteFailureCode.UNSUPPORTED_CONSTRAINT,
                "layered routing requires exactly two pads",
            )
        for pad in (start_pad, end_pad):
            if not set(pad.layer_ids) & set(layer_ids):
                return _diagnostic(
                    LayeredRouteFailureCode.INVALID_REQUEST,
                    "a route endpoint has no signal-layer access",
                )
        start_access = tuple(layer_id for layer_id in layer_ids if layer_id in start_pad.layer_ids)
        end_access = tuple(layer_id for layer_id in layer_ids if layer_id in end_pad.layer_ids)
        if request.start_layer_id is None and len(start_access) != 1:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "start layer must be explicit for a multi-layer endpoint",
            )
        if request.end_layer_id is None and len(end_access) != 1:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "end layer must be explicit for a multi-layer endpoint",
            )
        start_layer_id = request.start_layer_id or next(
            (layer_id for layer_id in layer_ids if layer_id in start_pad.layer_ids), None
        )
        end_layer_id = request.end_layer_id or next(
            (layer_id for layer_id in layer_ids if layer_id in end_pad.layer_ids), None
        )
        if start_layer_id not in layer_ids or end_layer_id not in layer_ids:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "requested endpoint layer is not a signal layer",
            )
        if start_layer_id not in start_pad.layer_ids or end_layer_id not in end_pad.layer_ids:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "requested endpoint layer is not exposed by its pad",
            )
        if start_pad.center == end_pad.center:
            return _diagnostic(
                LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
                "coincident route endpoints are unsupported",
            )
        if (
            any(item.net_id == request.net_id for item in snapshot.content.segments)
            or any(item.net_id == request.net_id for item in snapshot.content.vias)
            or any(item.net_id == request.net_id for item in snapshot.content.zones)
            # This adapter keeps its own rectangle-only obstacle model, which refuses even a
            # diagonal foreign segment and never derives an arc obstacle. The blanket arc
            # refusal is what makes that safe, so it deliberately stays stricter than the
            # polygon-capable single-layer router until this model grows an arc obstacle too.
            or snapshot.content.arcs
        ):
            return _diagnostic(
                LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
                "selected-net copper and arcs are outside the first layered proposal contract",
            )

        step = request.grid_step_nm
        origin = start_pad.center
        delta_x = end_pad.center.x - origin.x
        delta_y = end_pad.center.y - origin.y
        if delta_x % step != 0 or delta_y % step != 0:
            return _diagnostic(
                LayeredRouteFailureCode.OFF_GRID,
                "pad-center delta is not divisible by the grid step",
            )
        half_width = (net_class.track_width_nm + 1) // 2
        via_half = (net_class.via_diameter_nm + 1) // 2
        edge_margin = max(half_width, via_half)
        safe = (
            board[0] + edge_margin,
            board[1] + edge_margin,
            board[2] - edge_margin,
            board[3] - edge_margin,
        )
        if safe[0] > safe[2] or safe[1] > safe[3]:
            return _diagnostic(
                LayeredRouteFailureCode.NO_PATH, "track width does not fit inside the board"
            )
        min_x = -((-(safe[0] - origin.x)) // step)
        min_y = -((-(safe[1] - origin.y)) // step)
        max_x = (safe[2] - origin.x) // step
        max_y = (safe[3] - origin.y) // step
        goal = (delta_x // step, delta_y // step)
        if (
            not min_x <= 0 <= max_x
            or not min_y <= 0 <= max_y
            or not min_x <= goal[0] <= max_x
            or not min_y <= goal[1] <= max_y
        ):
            return _diagnostic(
                LayeredRouteFailureCode.NO_PATH, "a route endpoint cannot contain the track width"
            )

        track_obstacles: list[LayeredObstacle] = []
        via_obstacles: list[LayeredObstacle] = []
        clearance_by_class = {item.id: item.clearance_nm for item in constraints.net_classes}
        clearance_by_net = {
            assignment.net_id: clearance_by_class.get(
                assignment.net_class_id, net_class.clearance_nm
            )
            for assignment in constraints.assignments
        }
        widest_clearance = max([net_class.clearance_nm, *clearance_by_class.values()])
        grid_margin = (step + 1) // 2

        def add_obstacle(
            rectangle: _Rect,
            layer: int,
            margin: int,
            *,
            via: bool = False,
        ) -> bool:
            """Append one derived obstacle, refusing before constructing past the ceiling.

            The obstacle budget is a hard input-boundary limit, not merely a post-hoc metric.  In
            particular, a pad or foreign via can produce multiple envelopes; checking the count
            after the loops would still allocate and quantize all of those envelopes.  Returning
            ``False`` lets the caller terminate at the first rejected envelope without raising
            through the proposal-only API.
            """
            if len(track_obstacles) + len(via_obstacles) >= request.settings.max_obstacles:
                return False
            obstacle = _cell_obstacle(
                _inflate(rectangle, margin), layer=layer, origin=origin, step=step
            )
            (via_obstacles if via else track_obstacles).append(obstacle)
            return True

        for pad in snapshot.content.pads:
            if pad.id in {start_pad.id, end_pad.id}:
                continue
            rectangle = _pad_bounds(pad)
            pad_clearance = (
                clearance_by_net.get(pad.net_id, widest_clearance)
                if pad.net_id is not None
                else widest_clearance
            )
            clearance = max(net_class.clearance_nm, pad_clearance)
            for layer_id in set(pad.layer_ids) & set(layer_ids):
                layer = layer_index[layer_id]
                if not add_obstacle(
                    rectangle,
                    layer,
                    (net_class.track_width_nm + 1) // 2 + clearance,
                ) or not add_obstacle(rectangle, layer, via_half + clearance, via=True):
                    return _diagnostic(
                        LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                        "layered physical obstacle count exceeds the configured budget",
                    )

        # A via transition at an endpoint would become via-in-pad for the SMD pads supported by
        # this proposal seam.  The candidate contract intentionally carries no IPC-4761
        # padstack/treatment evidence, so reserve the endpoint pad envelopes for tracks only and
        # refuse via transitions on every supported layer.  This is conservative for through-hole
        # pads as well; a future padstack-aware route mode can relax it with explicit evidence.
        endpoint_clearance = max(net_class.clearance_nm, widest_clearance)
        for endpoint_pad in (start_pad, end_pad):
            endpoint_rectangle = _pad_bounds(endpoint_pad)
            for layer in range(len(layers)):
                if not add_obstacle(
                    endpoint_rectangle,
                    layer,
                    via_half + endpoint_clearance,
                    via=True,
                ):
                    return _diagnostic(
                        LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                        "layered physical obstacle count exceeds the configured budget",
                    )
        for segment in snapshot.content.segments:
            if segment.net_id == request.net_id:
                continue
            if segment.layer_id not in layer_index:
                continue
            segment_rectangle = _segment_bounds(segment)
            if segment_rectangle is None:
                return _diagnostic(
                    LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
                    "diagonal foreign segments are not modeled",
                )
            rectangle = segment_rectangle
            clearance = max(
                net_class.clearance_nm, clearance_by_net.get(segment.net_id, widest_clearance)
            )
            if not add_obstacle(
                rectangle,
                layer_index[segment.layer_id],
                half_width + clearance,
            ) or not add_obstacle(
                rectangle,
                layer_index[segment.layer_id],
                via_half + clearance,
                via=True,
            ):
                return _diagnostic(
                    LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                    "layered physical obstacle count exceeds the configured budget",
                )
        for via in snapshot.content.vias:
            if via.net_id == request.net_id:
                continue
            radius = (via.diameter_nm + 1) // 2
            rectangle = (
                via.center.x - radius,
                via.center.y - radius,
                via.center.x + radius,
                via.center.y + radius,
            )
            clearance = max(
                net_class.clearance_nm, clearance_by_net.get(via.net_id, widest_clearance)
            )
            for layer in range(len(layers)):
                # The foreign via rectangle already contains its own radius. Inflate it by the
                # candidate copper envelope as well: tracks need half-width clearance, while a
                # candidate via transition needs the candidate via radius.
                if not add_obstacle(rectangle, layer, half_width + clearance) or not add_obstacle(
                    rectangle, layer, via_half + clearance, via=True
                ):
                    return _diagnostic(
                        LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                        "layered physical obstacle count exceeds the configured budget",
                    )
        # Freshness-bound fill (ADR-0021) is the only evidence that may make an obstacle smaller,
        # so it is checked before it is spent.  A zone family keeps its conservative outline
        # envelope unless every gate below passes for its islands.
        zone_bounds_by_key: dict[tuple[str, str], list[_Rect]] = {}
        zone_clearance_by_key: dict[tuple[str, str], int] = {}
        for zone in snapshot.content.zones:
            key = (zone.net_id, zone.layer_id)
            zone_bounds_by_key.setdefault(key, []).append(_points_bounds(zone.boundary.points))
            zone_clearance_by_key[key] = max(zone_clearance_by_key.get(key, 0), zone.clearance_nm)
        verified_fill_keys: set[tuple[str, str]] = set()
        island_bounds: list[tuple[VerifiedFill, _Rect]] = []
        for island in request.verified_fill:
            if island.source_revision != snapshot.content.source.revision:
                return _diagnostic(
                    LayeredRouteFailureCode.STALE_REVISION,
                    "verified zone fill was established against a different board revision",
                )
            key = (island.net_id, island.layer_id)
            backing = zone_bounds_by_key.get(key)
            if backing is None:
                return _diagnostic(
                    LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
                    "verified zone fill is not backed by a matching Board IR zone",
                )
            bounds = _points_bounds(island.points)
            # KiCad clips poured copper to the zone outline, so this holds for honest evidence.
            # Checking it is what turns "the replacement only ever shrinks the obstacle" from an
            # assumption about the filler into a verified precondition of the replacement.
            if not any(_contains(outline, bounds) for outline in backing):
                return _diagnostic(
                    LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
                    "verified zone fill escapes its backing Board IR zone outline",
                )
            verified_fill_keys.add(key)
            island_bounds.append((island, bounds))
        for zone in snapshot.content.zones:
            if zone.net_id == request.net_id:
                continue
            if (zone.net_id, zone.layer_id) in verified_fill_keys:
                # The verified islands below are this family's complete copper, so the outline
                # envelope would only re-block the pour's own voids.
                continue
            rectangle = _points_bounds(zone.boundary.points)
            if zone.layer_id in layer_index:
                zone_clearance = max(
                    net_class.clearance_nm,
                    zone.clearance_nm,
                    clearance_by_net.get(zone.net_id, widest_clearance),
                )
                if not add_obstacle(
                    rectangle,
                    layer_index[zone.layer_id],
                    half_width + zone_clearance,
                ) or not add_obstacle(
                    rectangle,
                    layer_index[zone.layer_id],
                    via_half + zone_clearance,
                    via=True,
                ):
                    return _diagnostic(
                        LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                        "layered physical obstacle count exceeds the configured budget",
                    )
        for island, rectangle in island_bounds:
            # The island is a polygon and this lattice model is rectangular, so it is carried as
            # its bounding box.  That still over-approximates the real copper, and containment
            # above bounds it by the zone box it replaced part of.  A same-net island is
            # unreachable here: it would need a same-net zone to back it, and a selected-net zone
            # is refused as unmodeled copper well before this point.
            if island.layer_id not in layer_index:
                continue
            island_clearance = max(
                net_class.clearance_nm,
                zone_clearance_by_key.get((island.net_id, island.layer_id), 0),
                clearance_by_net.get(island.net_id, widest_clearance),
            )
            if not add_obstacle(
                rectangle,
                layer_index[island.layer_id],
                half_width + island_clearance,
            ) or not add_obstacle(
                rectangle,
                layer_index[island.layer_id],
                via_half + island_clearance,
                via=True,
            ):
                return _diagnostic(
                    LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                    "layered physical obstacle count exceeds the configured budget",
                )
        for keepout in snapshot.content.keepouts:
            rectangle = _points_bounds(keepout.boundary.points)
            for layer_id in set(keepout.layer_ids) & set(layer_ids):
                layer = layer_index[layer_id]
                if keepout.prohibit_tracks:
                    if not add_obstacle(rectangle, layer, half_width + grid_margin):
                        return _diagnostic(
                            LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                            "layered physical obstacle count exceeds the configured budget",
                        )
                if keepout.prohibit_vias:
                    if not add_obstacle(
                        rectangle,
                        layer,
                        (net_class.via_diameter_nm + 1) // 2 + net_class.clearance_nm,
                        via=True,
                    ):
                        return _diagnostic(
                            LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                            "layered physical obstacle count exceeds the configured budget",
                        )
        if len(track_obstacles) + len(via_obstacles) > request.settings.max_obstacles:
            return _diagnostic(
                LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                "layered physical obstacle count exceeds the configured budget",
            )

        abstract = LayeredAStarRequest(
            board_revision=snapshot.snapshot_digest,
            expected_revision=request.board_revision,
            bounds=(min_x, min_y, max_x, max_y),
            start=LayeredPoint(0, 0, layer_index[start_layer_id]),
            goal=LayeredPoint(goal[0], goal[1], layer_index[end_layer_id]),
            obstacles=tuple(track_obstacles),
            via_obstacles=tuple(via_obstacles),
            layers=tuple(range(len(layers))),
            settings=request.settings,
        )
        searched = route_layered(abstract, cancelled=cancellation_check)
        if not searched.ok:
            return _map_failure(searched)
        assert searched.path is not None
        converted = _paths_and_vias(
            searched.path,
            layer_ids=layer_ids,
            origin=origin,
            start_point=start_pad.center,
            end_point=end_pad.center,
            step_nm=step,
            via_diameter_nm=net_class.via_diameter_nm,
            via_drill_nm=net_class.via_drill_nm,
        )
        if converted is None:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "layered search returned discontinuous geometry",
                search=searched,
            )
        paths, vias = converted
        via_limit = effective_max_vias(request.settings, len(layers))
        if via_limit is not None and len(vias) > via_limit:
            return _diagnostic(
                LayeredRouteFailureCode.SEARCH_BUDGET_EXCEEDED,
                "layered route exceeds its effective via budget",
                search=searched,
            )
        patch = LayeredRoutePatch(
            net_id=request.net_id,
            width_nm=net_class.track_width_nm,
            via_diameter_nm=net_class.via_diameter_nm,
            via_drill_nm=net_class.via_drill_nm,
            paths=paths,
            vias=vias,
        )
        metrics = LayeredRouteMetrics(
            expanded_states=searched.metrics.expanded_nodes,
            discovered_states=searched.metrics.discovered_nodes,
            peak_frontier_states=searched.metrics.peak_frontier_nodes,
            obstacle_checks=searched.metrics.obstacle_checks,
            move_steps=searched.metrics.move_steps,
            vias=len(vias),
            wire_length_nm=patch.wire_length_nm,
            bend_count=patch.bend_count,
        )
        cost = LayeredRouteCost(
            wire_length_nm=patch.wire_length_nm,
            via_count=len(vias),
            via_cost_units=len(vias) * request.settings.via_cost,
            total_search_cost_units=searched.metrics.path_cost,
        )
        candidate = LayeredRouteCandidate(
            candidate_id=_EMPTY_DIGEST,
            base_revision=snapshot.snapshot_digest,
            start_pad_id=start_pad.id,
            end_pad_id=end_pad.id,
            patch=patch,
            cost=cost,
            metrics=metrics,
            settings=request.settings,
            router_version=LAYERED_ROUTER_VERSION,
            policy=LAYERED_ROUTING_POLICY,
            seed=request.seed,
        )
        candidate = replace(
            candidate,
            candidate_id=f"sha256:{hashlib.sha256(canonical_layered_candidate_bytes(candidate)).hexdigest()}",
        )
        verify_layered_candidate_id(candidate)
        return LayeredRouteResult(candidate=candidate)
