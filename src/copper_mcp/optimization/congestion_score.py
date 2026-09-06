"""Bounded coarse track/via occupancy measurement on an actual routed Board IR."""

from __future__ import annotations

from copper_mcp.board_ir import BoardIRSnapshot
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.worker import OptimizationExecutionProbe


def track_via_density(
    snapshot: BoardIRSnapshot, grid_nm: int, probe: OptimizationExecutionProbe
) -> int:
    """Count squared excess net occupancy in coarse cells; this is never a clearance proof.

    Straight copper and through-via bounding boxes define the versioned measurement. Zones,
    pads and arcs remain router obstacles but are outside this soft demand-density metric.
    """

    occupied: dict[tuple[str, int, int], set[str]] = {}
    boxes: list[tuple[str, str, int, int, int, int]] = []
    for segment in snapshot.content.segments:
        radius = (segment.width_nm + 1) // 2
        boxes.append(
            (
                segment.layer_id,
                segment.net_id or segment.id,
                min(segment.start.x, segment.end.x) - radius,
                min(segment.start.y, segment.end.y) - radius,
                max(segment.start.x, segment.end.x) + radius,
                max(segment.start.y, segment.end.y) + radius,
            )
        )
    for via in snapshot.content.vias:
        radius = (via.diameter_nm + 1) // 2
        for layer in snapshot.content.copper_layers:
            boxes.append(
                (
                    layer.id,
                    via.net_id or via.id,
                    via.center.x - radius,
                    via.center.y - radius,
                    via.center.x + radius,
                    via.center.y + radius,
                )
            )
    for layer_id, net_id, left, top, right, bottom in boxes:
        left, top, right, bottom = (
            left // grid_nm,
            top // grid_nm,
            right // grid_nm,
            bottom // grid_nm,
        )
        cells = (right - left + 1) * (bottom - top + 1)
        probe.reserve(ResourceUsage(obstacle_checks=cells))
        for x in range(left, right + 1):
            for y in range(top, bottom + 1):
                occupied.setdefault((layer_id, x, y), set()).add(net_id)
            if x % 128 == 0:
                probe.checkpoint()
    return sum(max(0, len(nets) - 1) ** 2 for nets in occupied.values())
