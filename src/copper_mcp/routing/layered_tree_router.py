"""Private bounded Board IR adapter for complete multi-pin layered trees."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import cast

from copper_mcp.board_ir import BoardIRSnapshot, PointNM, verify_snapshot
from copper_mcp.routing._layered_tree_cancel import (
    LayeredTreeCancellationError,
    poll_cancelled,
)
from copper_mcp.routing.layered_astar import (
    MAX_EXPLICIT_VIAS,
    MAX_LAYERS,
    LayeredAStarRequest,
    LayeredAStarSettings,
    LayeredFailureCode,
    LayeredObstacle,
    LayeredPoint,
    route_layered,
)
from copper_mcp.routing.layered_board_adapter import (
    _axis_aligned_rectangle,
    _cell_obstacle,
    _inflate,
    _layer_order,
    _pad_bounds,
    _paths_and_vias,
    _points_bounds,
    _resolve_net_class,
    _segment_bounds,
)
from copper_mcp.routing.layered_contracts import (
    LayeredRouteDiagnostic,
    LayeredRouteFailureCode,
    LayeredRouteVia,
)
from copper_mcp.routing.layered_tree_contracts import (
    LAYERED_TREE_POLICY,
    LAYERED_TREE_ROUTER_VERSION,
    LayeredTreeBranch,
    LayeredTreeCandidate,
    LayeredTreeMetrics,
    LayeredTreeResult,
    LayeredTreeTerminal,
    layered_tree_physical_edges,
    layered_tree_physical_vias,
    with_layered_tree_candidate_id,
)
from copper_mcp.routing.layered_tree_verifier import _admit_candidate_shape

_MAX_SAFE_INT = (1 << 53) - 1
_MAX_COST = 1_000_000_000
_MAX_EXPANSIONS = 1_000_000
_MAX_NODES = 500_000
_MAX_OBSTACLES = 4_096
_MAX_OBSTACLE_CHECKS = 10_000_000


class _LayeredTreeCancelledError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class LayeredTreeRequest:
    board_revision: str
    net_id: str
    terminals: tuple[LayeredTreeTerminal, ...]
    grid_step_nm: int = 250_000
    seed: int = 0
    settings: LayeredAStarSettings = field(default_factory=LayeredAStarSettings)


def _digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _typed(value: object, prefix: str) -> bool:
    return (
        type(value) is str
        and value.startswith(prefix)
        and 1 <= len(value.removeprefix(prefix)) <= 160
        and all(
            character.isascii() and (character.isalnum() or character in "_.:-")
            for character in value
        )
    )


def _invalid_request(request: object) -> bool:
    if type(request) is not LayeredTreeRequest:
        return True
    if not _digest(request.board_revision) or not _typed(request.net_id, "net:"):
        return True
    if (
        type(request.terminals) is not tuple
        or not 2 <= len(request.terminals) <= 32
        or any(type(item) is not LayeredTreeTerminal for item in request.terminals)
    ):
        return True
    pad_ids = tuple(item.pad_id for item in request.terminals)
    if pad_ids != tuple(sorted(set(pad_ids))):
        return True
    if type(request.grid_step_nm) is not int or not 1 <= request.grid_step_nm <= _MAX_SAFE_INT:
        return True
    if type(request.seed) is not int or not 0 <= request.seed <= _MAX_SAFE_INT - 32:
        return True
    settings = request.settings
    if type(settings) is not LayeredAStarSettings:
        return True
    for value, maximum in (
        (settings.move_cost, _MAX_COST),
        (settings.via_cost, _MAX_COST),
        (settings.max_expansions, _MAX_EXPANSIONS),
        (settings.max_nodes, _MAX_NODES),
        (settings.max_obstacles, _MAX_OBSTACLES),
        (settings.max_obstacle_checks, _MAX_OBSTACLE_CHECKS),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            return True
    return settings.max_vias is not None and (
        type(settings.max_vias) is not int or not 0 <= settings.max_vias <= MAX_EXPLICIT_VIAS
    )


def _diagnostic(
    code: LayeredRouteFailureCode,
    message: str,
    *,
    expanded: int = 0,
    checks: int = 0,
) -> LayeredTreeResult:
    return LayeredTreeResult(diagnostic=LayeredRouteDiagnostic(code, message, expanded, checks))


def _mapped_failure(code: LayeredFailureCode) -> LayeredRouteFailureCode:
    return {
        LayeredFailureCode.INVALID_REQUEST: LayeredRouteFailureCode.INVALID_REQUEST,
        LayeredFailureCode.STALE_REVISION: LayeredRouteFailureCode.STALE_REVISION,
        LayeredFailureCode.GRID_BUDGET_EXCEEDED: LayeredRouteFailureCode.GRID_BUDGET_EXCEEDED,
        LayeredFailureCode.OBSTACLE_BUDGET_EXCEEDED: (
            LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED
        ),
        LayeredFailureCode.SEARCH_BUDGET_EXCEEDED: LayeredRouteFailureCode.SEARCH_BUDGET_EXCEEDED,
        LayeredFailureCode.CANCELLED: LayeredRouteFailureCode.CANCELLED,
        LayeredFailureCode.NO_PATH: LayeredRouteFailureCode.NO_PATH,
    }[code]


def _tree_via_limit(settings: LayeredAStarSettings, layer_count: int) -> int:
    if settings.max_vias is not None:
        return settings.max_vias
    return 256 if layer_count == 2 else 64


def _nearest_connected(
    terminal: LayeredTreeTerminal,
    connected: tuple[LayeredTreeTerminal, ...],
    centers: dict[str, PointNM],
) -> LayeredTreeTerminal:
    start = centers[terminal.pad_id]
    return min(
        connected,
        key=lambda item: (
            abs(start.x - centers[item.pad_id].x) + abs(start.y - centers[item.pad_id].y),
            item.pad_id,
        ),
    )


def _add_attachment_points(
    anchors: dict[str, set[PointNM]],
    branch: LayeredTreeBranch,
    *,
    layer_ids: tuple[str, ...],
    step_nm: int,
    current_points: int,
    max_points: int,
    checkpoint: Callable[[], None],
) -> int | None:
    for path in branch.paths:
        for start, end in zip(path.vertices, path.vertices[1:], strict=False):
            distance = abs(start.x - end.x) + abs(start.y - end.y)
            count = distance // step_nm
            delta_x = 0 if start.x == end.x else step_nm if end.x > start.x else -step_nm
            delta_y = 0 if start.y == end.y else step_nm if end.y > start.y else -step_nm
            for index in range(count + 1):
                checkpoint()
                point = PointNM(start.x + index * delta_x, start.y + index * delta_y)
                if point not in anchors[path.layer_id]:
                    current_points += 1
                    if current_points > max_points:
                        return None
                    anchors[path.layer_id].add(point)
    for via in branch.vias:
        for layer_id in layer_ids:
            checkpoint()
            if via.center not in anchors[layer_id]:
                current_points += 1
                if current_points > max_points:
                    return None
                anchors[layer_id].add(via.center)
    return current_points


class LayeredTreeRouter:
    """Generate one deterministic tree while retaining the complete Board IR snapshot."""

    def replay(
        self,
        snapshot: BoardIRSnapshot,
        candidate: LayeredTreeCandidate,
        request: LayeredTreeRequest,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> LayeredTreeResult:
        if type(candidate) is not LayeredTreeCandidate:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST, "layered tree candidate is malformed"
            )
        if _admit_candidate_shape(candidate) is None:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "layered tree candidate exceeds its structural budget",
            )
        try:
            candidate.__post_init__()
        except (TypeError, ValueError):
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "layered tree candidate is malformed",
            )
        result = self.propose(snapshot, request, cancelled=cancelled)
        try:
            if poll_cancelled(cancelled):
                return _diagnostic(LayeredRouteFailureCode.CANCELLED, "layered tree was cancelled")
        except LayeredTreeCancellationError:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "layered tree cancellation check failed",
            )
        if result.candidate != candidate:
            return _diagnostic(
                LayeredRouteFailureCode.FILL_EVIDENCE_MISMATCH,
                "layered tree does not match deterministic replay",
            )
        return result

    def propose(
        self,
        snapshot: BoardIRSnapshot,
        request: LayeredTreeRequest,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> LayeredTreeResult:
        if _invalid_request(request):
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST, "layered tree request is malformed"
            )
        if type(snapshot) is not BoardIRSnapshot:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_SNAPSHOT, "Board IR snapshot is malformed"
            )
        if request.board_revision != snapshot.snapshot_digest:
            return _diagnostic(
                LayeredRouteFailureCode.STALE_REVISION,
                "layered tree request is stale for the Board IR snapshot",
            )
        try:
            verify_snapshot(snapshot)
        except (TypeError, ValueError):
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_SNAPSHOT,
                "Board IR snapshot verification failed",
            )
        cancelled_object: object = cancelled
        if cancelled_object is not None and not callable(cancelled_object):
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "layered tree cancellation check is malformed",
            )
        cancellation_check = cast(Callable[[], bool] | None, cancelled_object)
        try:
            if poll_cancelled(cancellation_check):
                return _diagnostic(LayeredRouteFailureCode.CANCELLED, "layered tree was cancelled")
        except LayeredTreeCancellationError:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "layered tree cancellation check failed",
            )

        def checkpoint() -> None:
            if poll_cancelled(cancellation_check):
                raise _LayeredTreeCancelledError

        layers = _layer_order(snapshot)
        if not 2 <= len(layers) <= MAX_LAYERS or any(layer.kind != "signal" for layer in layers):
            return _diagnostic(
                LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
                "layered tree requires two through eight ordered signal layers",
            )
        layer_ids = tuple(layer.id for layer in layers)
        layer_index = {layer.id: index for index, layer in enumerate(layers)}
        pads = {pad.id: pad for pad in snapshot.content.pads}
        net_pads = tuple(pad for pad in snapshot.content.pads if pad.net_id == request.net_id)
        requested_ids = tuple(item.pad_id for item in request.terminals)
        if tuple(sorted(pad.id for pad in net_pads)) != requested_ids:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "layered tree terminals are not the complete selected-net pad set",
            )
        for terminal in request.terminals:
            pad = pads.get(terminal.pad_id)
            if (
                pad is None
                or pad.net_id != request.net_id
                or terminal.layer_id not in layer_index
                or terminal.layer_id not in pad.layer_ids
            ):
                return _diagnostic(
                    LayeredRouteFailureCode.INVALID_REQUEST,
                    "layered tree terminal access is invalid",
                )
        net_class = _resolve_net_class(snapshot, request.net_id)
        if net_class is None:
            return _diagnostic(
                LayeredRouteFailureCode.UNSUPPORTED_CONSTRAINT,
                "selected net class is missing",
            )
        constraints = snapshot.content.constraints
        if any(
            request.net_id in {rule.positive_net_id, rule.negative_net_id}
            for rule in constraints.differential_pairs
        ) or any(rule.net_id == request.net_id for rule in constraints.length_rules):
            return _diagnostic(
                LayeredRouteFailureCode.UNSUPPORTED_CONSTRAINT,
                "selected net has an unsupported routed constraint",
            )
        if (
            any(item.net_id == request.net_id for item in snapshot.content.segments)
            or any(item.net_id == request.net_id for item in snapshot.content.vias)
            or snapshot.content.zones
            or snapshot.content.arcs
        ):
            return _diagnostic(
                LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
                "selected-net copper, zones, and arcs are unsupported for layered trees",
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
                "layered tree requires one rectangular hole-free outline",
            )

        root = request.terminals[0]
        origin = pads[root.pad_id].center
        step = request.grid_step_nm
        coordinates: dict[str, tuple[int, int]] = {}
        for terminal in request.terminals:
            center = pads[terminal.pad_id].center
            delta_x, delta_y = center.x - origin.x, center.y - origin.y
            if delta_x % step or delta_y % step:
                return _diagnostic(
                    LayeredRouteFailureCode.OFF_GRID,
                    "layered tree terminal is off the shared routing grid",
                )
            coordinates[terminal.pad_id] = delta_x // step, delta_y // step

        half_width = (net_class.track_width_nm + 1) // 2
        via_half = (net_class.via_diameter_nm + 1) // 2
        edge_margin = max(half_width, via_half)
        safe = (
            board[0] + edge_margin,
            board[1] + edge_margin,
            board[2] - edge_margin,
            board[3] - edge_margin,
        )
        min_x = -((-(safe[0] - origin.x)) // step)
        min_y = -((-(safe[1] - origin.y)) // step)
        max_x = (safe[2] - origin.x) // step
        max_y = (safe[3] - origin.y) // step
        if (
            safe[0] > safe[2]
            or safe[1] > safe[3]
            or any(
                not min_x <= point[0] <= max_x or not min_y <= point[1] <= max_y
                for point in coordinates.values()
            )
        ):
            return _diagnostic(
                LayeredRouteFailureCode.NO_PATH,
                "a layered tree terminal cannot contain the routed dimensions",
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
            rectangle: tuple[int, int, int, int], layer: int, margin: int, *, via: bool = False
        ) -> bool:
            if len(track_obstacles) + len(via_obstacles) >= request.settings.max_obstacles:
                return False
            obstacle = _cell_obstacle(
                _inflate(rectangle, margin), layer=layer, origin=origin, step=step
            )
            (via_obstacles if via else track_obstacles).append(obstacle)
            return True

        terminal_ids = set(requested_ids)
        for pad in snapshot.content.pads:
            rectangle = _pad_bounds(pad)
            if pad.id in terminal_ids:
                for layer in range(len(layers)):
                    if not add_obstacle(
                        rectangle,
                        layer,
                        via_half + widest_clearance,
                        via=True,
                    ):
                        return _diagnostic(
                            LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                            "layered tree obstacle budget is exhausted",
                        )
                continue
            clearance = max(
                net_class.clearance_nm,
                widest_clearance
                if pad.net_id is None
                else clearance_by_net.get(pad.net_id, widest_clearance),
            )
            for layer_id in set(pad.layer_ids) & set(layer_ids):
                layer = layer_index[layer_id]
                if not add_obstacle(rectangle, layer, half_width + clearance) or not add_obstacle(
                    rectangle, layer, via_half + clearance, via=True
                ):
                    return _diagnostic(
                        LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                        "layered tree obstacle budget is exhausted",
                    )
        for segment in snapshot.content.segments:
            segment_rectangle = _segment_bounds(segment)
            if segment.layer_id not in layer_index:
                continue
            if segment_rectangle is None:
                return _diagnostic(
                    LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY,
                    "diagonal foreign segments are unsupported",
                )
            clearance = max(
                net_class.clearance_nm,
                widest_clearance
                if segment.net_id is None
                else clearance_by_net.get(segment.net_id, widest_clearance),
            )
            layer = layer_index[segment.layer_id]
            if not add_obstacle(
                segment_rectangle, layer, half_width + clearance
            ) or not add_obstacle(segment_rectangle, layer, via_half + clearance, via=True):
                return _diagnostic(
                    LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                    "layered tree obstacle budget is exhausted",
                )
        for via in snapshot.content.vias:
            radius = (via.diameter_nm + 1) // 2
            rectangle = (
                via.center.x - radius,
                via.center.y - radius,
                via.center.x + radius,
                via.center.y + radius,
            )
            clearance = max(
                net_class.clearance_nm,
                widest_clearance
                if via.net_id is None
                else clearance_by_net.get(via.net_id, widest_clearance),
            )
            for layer in range(len(layers)):
                if not add_obstacle(rectangle, layer, half_width + clearance) or not add_obstacle(
                    rectangle, layer, via_half + clearance, via=True
                ):
                    return _diagnostic(
                        LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                        "layered tree obstacle budget is exhausted",
                    )
        for keepout in snapshot.content.keepouts:
            rectangle = _points_bounds(keepout.boundary.points)
            for layer_id in set(keepout.layer_ids) & set(layer_ids):
                layer = layer_index[layer_id]
                if keepout.prohibit_tracks and not add_obstacle(
                    rectangle, layer, half_width + grid_margin
                ):
                    return _diagnostic(
                        LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                        "layered tree obstacle budget is exhausted",
                    )
                if keepout.prohibit_vias and not add_obstacle(
                    rectangle, layer, via_half + net_class.clearance_nm, via=True
                ):
                    return _diagnostic(
                        LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
                        "layered tree obstacle budget is exhausted",
                    )

        branches: list[LayeredTreeBranch] = []
        connected: tuple[LayeredTreeTerminal, ...] = (root,)
        centers = {item.pad_id: pads[item.pad_id].center for item in request.terminals}
        expanded = discovered = checks = moves = bends = 0
        peak = 0
        via_limit = _tree_via_limit(request.settings, len(layers))
        shared_vias: dict[PointNM, LayeredRouteVia] = {}
        anchors: dict[str, set[PointNM]] = {layer_id: set() for layer_id in layer_ids}
        anchors[root.layer_id].add(origin)
        anchor_count = 1
        for branch_index, terminal in enumerate(request.terminals[1:]):
            try:
                checkpoint()
            except _LayeredTreeCancelledError:
                return _diagnostic(
                    LayeredRouteFailureCode.CANCELLED,
                    "layered tree was cancelled",
                    expanded=expanded,
                    checks=checks,
                )
            except LayeredTreeCancellationError:
                return _diagnostic(
                    LayeredRouteFailureCode.INVALID_REQUEST,
                    "layered tree cancellation check failed",
                    expanded=expanded,
                    checks=checks,
                )
            destination = _nearest_connected(terminal, connected, centers)
            attachment_point = centers[destination.pad_id]
            attachment_layer_id = destination.layer_id
            prior_points = tuple(sorted(anchors[terminal.layer_id]))
            if prior_points:
                start = centers[terminal.pad_id]
                attachment_point = min(
                    prior_points,
                    key=lambda point: (
                        abs(start.x - point.x) + abs(start.y - point.y),
                        point.x,
                        point.y,
                    ),
                )
                attachment_layer_id = terminal.layer_id
                destination = root
            if (
                attachment_point == centers[terminal.pad_id]
                and terminal.layer_id == attachment_layer_id
            ):
                branches.append(
                    LayeredTreeBranch(
                        branch_id=f"branch:layered-tree:{branch_index:02d}",
                        start_pad_id=terminal.pad_id,
                        end_pad_id=destination.pad_id,
                        attachment_point=attachment_point,
                        attachment_layer_id=attachment_layer_id,
                        paths=(),
                        attachment_only=True,
                    )
                )
                connected += (terminal,)
                continue
            remaining_expansions = request.settings.max_expansions - expanded
            remaining_nodes = request.settings.max_nodes - discovered - anchor_count
            remaining_checks = request.settings.max_obstacle_checks - checks
            remaining_unique_vias = via_limit - len(shared_vias)
            branch_via_allowance = remaining_unique_vias
            if branch_via_allowance < 1 and terminal.layer_id != attachment_layer_id:
                return _diagnostic(
                    LayeredRouteFailureCode.SEARCH_BUDGET_EXCEEDED,
                    "layered tree cumulative via budget is exhausted",
                    expanded=expanded,
                    checks=checks,
                )
            if min(remaining_expansions, remaining_nodes, remaining_checks) < 1:
                return _diagnostic(
                    LayeredRouteFailureCode.SEARCH_BUDGET_EXCEEDED,
                    "layered tree cumulative search budget is exhausted",
                    expanded=expanded,
                    checks=checks,
                )
            start_xy = coordinates[terminal.pad_id]
            end_xy = (
                (attachment_point.x - origin.x) // step,
                (attachment_point.y - origin.y) // step,
            )
            try:
                searched = route_layered(
                    LayeredAStarRequest(
                        board_revision=snapshot.snapshot_digest,
                        expected_revision=request.board_revision,
                        bounds=(min_x, min_y, max_x, max_y),
                        start=LayeredPoint(
                            start_xy[0], start_xy[1], layer_index[terminal.layer_id]
                        ),
                        goal=LayeredPoint(end_xy[0], end_xy[1], layer_index[attachment_layer_id]),
                        obstacles=tuple(track_obstacles),
                        via_obstacles=tuple(via_obstacles),
                        layers=tuple(range(len(layers))),
                        settings=replace(
                            request.settings,
                            max_expansions=remaining_expansions,
                            max_nodes=remaining_nodes,
                            max_obstacle_checks=remaining_checks,
                            max_vias=branch_via_allowance,
                        ),
                    ),
                    cancelled=lambda: poll_cancelled(cancellation_check),
                )
            except LayeredTreeCancellationError:
                return _diagnostic(
                    LayeredRouteFailureCode.INVALID_REQUEST,
                    "layered tree cancellation check failed",
                    expanded=expanded,
                    checks=checks,
                )
            expanded += searched.metrics.expanded_nodes
            discovered += searched.metrics.discovered_nodes
            checks += searched.metrics.obstacle_checks
            moves += searched.metrics.move_steps
            peak = max(peak, searched.metrics.peak_frontier_nodes)
            if searched.path is None:
                assert searched.diagnostic is not None
                return _diagnostic(
                    _mapped_failure(searched.diagnostic.code),
                    "layered tree branch search failed",
                    expanded=expanded,
                    checks=checks,
                )
            converted = _paths_and_vias(
                searched.path,
                layer_ids=layer_ids,
                origin=origin,
                start_point=centers[terminal.pad_id],
                end_point=attachment_point,
                step_nm=step,
                via_diameter_nm=net_class.via_diameter_nm,
                via_drill_nm=net_class.via_drill_nm,
            )
            if converted is None:
                return _diagnostic(
                    LayeredRouteFailureCode.INVALID_REQUEST,
                    "layered tree branch geometry is discontinuous",
                    expanded=expanded,
                    checks=checks,
                )
            paths, raw_vias = converted
            normalized_vias = []
            for proposed_via in raw_vias:
                existing = shared_vias.get(proposed_via.center)
                if existing is None:
                    existing = replace(
                        proposed_via,
                        id=f"via:layered-tree:{len(shared_vias):04d}",
                    )
                    shared_vias[proposed_via.center] = existing
                elif (
                    existing.center != proposed_via.center
                    or existing.diameter_nm != proposed_via.diameter_nm
                    or existing.drill_nm != proposed_via.drill_nm
                    or {existing.start_layer_id, existing.end_layer_id}
                    != {proposed_via.start_layer_id, proposed_via.end_layer_id}
                ):
                    return _diagnostic(
                        LayeredRouteFailureCode.INVALID_REQUEST,
                        "layered tree shared via geometry conflicts",
                        expanded=expanded,
                        checks=checks,
                    )
                normalized_vias.append(existing)
            vias = tuple(normalized_vias)
            branch = LayeredTreeBranch(
                branch_id=f"branch:layered-tree:{branch_index:02d}",
                start_pad_id=terminal.pad_id,
                end_pad_id=destination.pad_id,
                attachment_point=attachment_point,
                attachment_layer_id=attachment_layer_id,
                paths=paths,
                vias=vias,
            )
            branches.append(branch)
            try:
                updated_anchor_count = _add_attachment_points(
                    anchors,
                    branch,
                    layer_ids=layer_ids,
                    step_nm=step,
                    current_points=anchor_count,
                    max_points=max(0, request.settings.max_nodes - discovered),
                    checkpoint=checkpoint,
                )
            except _LayeredTreeCancelledError:
                return _diagnostic(
                    LayeredRouteFailureCode.CANCELLED,
                    "layered tree was cancelled",
                    expanded=expanded,
                    checks=checks,
                )
            except LayeredTreeCancellationError:
                return _diagnostic(
                    LayeredRouteFailureCode.INVALID_REQUEST,
                    "layered tree cancellation check failed",
                    expanded=expanded,
                    checks=checks,
                )
            if updated_anchor_count is None:
                return _diagnostic(
                    LayeredRouteFailureCode.SEARCH_BUDGET_EXCEEDED,
                    "layered tree attachment-node budget is exhausted",
                    expanded=expanded,
                    checks=checks,
                )
            anchor_count = updated_anchor_count
            connected += (terminal,)
            bends += branch.bend_count

        physical_edges = layered_tree_physical_edges(tuple(branches))
        physical_vias = layered_tree_physical_vias(tuple(branches))
        if len(physical_vias) > via_limit:
            return _diagnostic(
                LayeredRouteFailureCode.SEARCH_BUDGET_EXCEEDED,
                "layered tree cumulative via budget is exhausted",
                expanded=expanded,
                checks=checks,
            )
        length = sum(
            abs(start.x - end.x) + abs(start.y - end.y) for _layer, start, end in physical_edges
        )

        try:
            checkpoint()
        except _LayeredTreeCancelledError:
            return _diagnostic(
                LayeredRouteFailureCode.CANCELLED,
                "layered tree was cancelled",
                expanded=expanded,
                checks=checks,
            )
        except LayeredTreeCancellationError:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "layered tree cancellation check failed",
                expanded=expanded,
                checks=checks,
            )
        candidate = LayeredTreeCandidate(
            candidate_id=f"sha256:{'0' * 64}",
            base_revision=snapshot.snapshot_digest,
            net_id=request.net_id,
            terminals=request.terminals,
            grid_step_nm=request.grid_step_nm,
            width_nm=net_class.track_width_nm,
            via_diameter_nm=net_class.via_diameter_nm,
            via_drill_nm=net_class.via_drill_nm,
            branches=tuple(branches),
            metrics=LayeredTreeMetrics(
                branch_count=len(branches),
                obstacles=len(track_obstacles) + len(via_obstacles),
                attachment_nodes=anchor_count,
                expanded_states=expanded,
                discovered_states=discovered,
                peak_frontier_states=peak,
                obstacle_checks=checks,
                move_steps=moves,
                vias=len(physical_vias),
                wire_length_nm=length,
                bend_count=bends,
            ),
            settings=request.settings,
            router_version=LAYERED_TREE_ROUTER_VERSION,
            policy=LAYERED_TREE_POLICY,
            seed=request.seed,
        )
        try:
            candidate = with_layered_tree_candidate_id(candidate, checkpoint=checkpoint)
            checkpoint()
        except _LayeredTreeCancelledError:
            return _diagnostic(
                LayeredRouteFailureCode.CANCELLED,
                "layered tree was cancelled",
                expanded=expanded,
                checks=checks,
            )
        except LayeredTreeCancellationError:
            return _diagnostic(
                LayeredRouteFailureCode.INVALID_REQUEST,
                "layered tree cancellation check failed",
                expanded=expanded,
                checks=checks,
            )
        return LayeredTreeResult(candidate=candidate)


__all__: list[str] = []
