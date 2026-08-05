"""Pinned semantic fixture for the exact-local-repair negotiated-admission experiment.

The values reconstruct the original ``_crossing_snapshot`` and ``_requests`` experiment described
in ``exact-local-repair-negotiated-integration-gate.md``. This helper is deliberately independent
of the test module so a benchmark never imports tests as production input. Its equivalence to the
test semantic builder and settings is asserted in the routing-congestion regression suite.
"""

from __future__ import annotations

from copper_mcp.board_ir import (
    BoardIRSnapshot,
    ConstraintSet,
    Footprint,
    FootprintSide,
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
from copper_mcp.routing import AStarSettings, RouteRequest

PREDECLARED_SOURCE_COMMIT = "965d8fc97ddeb720251cb7863c7b62310637f301"
EXPECTED_SNAPSHOT_DIGEST = "sha256:9ad048f6f439a7e71be4c1f115d8a205f00c92f0853e0c140725906c1acdb245"
BOARD_SOURCE = f"sha256:{'c' * 64}"
LAYER_ID = "layer:F.Cu"
HORIZONTAL_NET = "net:horizontal"
VERTICAL_NET = "net:vertical"


def settings() -> AStarSettings:
    """Return the exact caps used by the pinned semantic experiment."""

    return AStarSettings(
        grid_step_nm=1_000_000,
        bend_penalty_nm=500_000,
        proximity_penalty_nm=0,
        max_grid_nodes=256,
        max_expansions=5_000,
        max_obstacles=64,
        max_obstacle_checks=100_000,
    )


def build_snapshot() -> BoardIRSnapshot:
    """Reconstruct the original two-net crossing snapshot without reading test code."""

    def pad(identifier: str, net_id: str, centre: tuple[int, int]) -> Pad:
        return Pad(
            id=identifier,
            net_id=net_id,
            center=PointNM(*centre),
            rotation_udeg=0,
            shape=PadShape.RECT,
            kind=PadKind.SMD,
            size_x_nm=400_000,
            size_y_nm=400_000,
            roundrect_radius_nm=None,
            drill_x_nm=None,
            drill_y_nm=None,
            layer_ids=(LAYER_ID,),
        )

    pads = (
        pad("pad:h1", HORIZONTAL_NET, (2_000_000, 5_000_000)),
        pad("pad:h2", HORIZONTAL_NET, (10_000_000, 5_000_000)),
        pad("pad:v1", VERTICAL_NET, (6_000_000, 1_000_000)),
        pad("pad:v2", VERTICAL_NET, (6_000_000, 9_000_000)),
    )
    net_class = NetClass(
        id="class:signal",
        name="Signal",
        clearance_nm=100_000,
        track_width_nm=200_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    snapshot = make_snapshot(
        make_content(
            source=SourceInfo(
                format="test",
                revision=BOARD_SOURCE,
                format_version="1",
                generator="negotiated-routing-test",
            ),
            outline=(
                OutlineContour(
                    id="contour:board",
                    outer=Ring(
                        (
                            PointNM(0, 0),
                            PointNM(12_000_000, 0),
                            PointNM(12_000_000, 10_000_000),
                            PointNM(0, 10_000_000),
                        )
                    ),
                ),
            ),
            copper_layers=(Layer(id=LAYER_ID, name="F.Cu", index=0, kind="signal"),),
            nets=(
                Net(id=HORIZONTAL_NET, name="HORIZONTAL"),
                Net(id=VERTICAL_NET, name="VERTICAL"),
            ),
            constraints=ConstraintSet(
                net_classes=(net_class,),
                assignments=(
                    NetClassAssignment(net_id=HORIZONTAL_NET, net_class_id=net_class.id),
                    NetClassAssignment(net_id=VERTICAL_NET, net_class_id=net_class.id),
                ),
            ),
            footprints=(
                Footprint(
                    id="footprint:h",
                    origin=pads[0].center,
                    rotation_udeg=0,
                    side=FootprintSide.FRONT,
                    pad_ids=(pads[0].id, pads[1].id),
                ),
                Footprint(
                    id="footprint:v",
                    origin=pads[2].center,
                    rotation_udeg=0,
                    side=FootprintSide.FRONT,
                    pad_ids=(pads[2].id, pads[3].id),
                ),
            ),
            pads=pads,
        )
    )
    if snapshot.snapshot_digest != EXPECTED_SNAPSHOT_DIGEST:
        raise RuntimeError("the pinned exact-local-repair semantic fixture changed")
    return snapshot


def build_requests(snapshot: BoardIRSnapshot) -> tuple[RouteRequest, RouteRequest]:
    """Return the original request ordering and seeds bound to a supplied pinned snapshot."""

    return (
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=HORIZONTAL_NET,
            layer_id=LAYER_ID,
            seed=7,
            settings=settings(),
        ),
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=VERTICAL_NET,
            layer_id=LAYER_ID,
            seed=11,
            settings=settings(),
        ),
    )
