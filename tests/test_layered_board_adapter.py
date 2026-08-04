from __future__ import annotations

from dataclasses import replace

import pytest

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
from copper_mcp.routing import (
    LAYERED_ROUTER_VERSION,
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteFailureCode,
    LayeredRouteRequest,
    verify_layered_candidate_id,
)

LAYER_ID = "layer:F.Cu"
OTHER_REVISION = f"sha256:{'b' * 64}"
BACK_LAYER_ID = "layer:B.Cu"
NET_ID = "net:audio"


def _rectangle(min_x: int, min_y: int, max_x: int, max_y: int) -> Ring:
    return Ring(
        (
            PointNM(min_x, min_y),
            PointNM(max_x, min_y),
            PointNM(max_x, max_y),
            PointNM(min_x, max_y),
        )
    )


def _two_layer_snapshot(
    *,
    start: tuple[int, int] = (1_000, 5_000),
    end: tuple[int, int] = (9_000, 5_000),
    keepouts: tuple[Keepout, ...] = (),
) -> BoardIRSnapshot:
    net_class = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=100,
        track_width_nm=200,
        via_diameter_nm=600,
        via_drill_nm=300,
    )
    pads = (
        Pad(
            id="pad:01",
            net_id=NET_ID,
            center=PointNM(*start),
            rotation_udeg=0,
            shape=PadShape.RECT,
            kind=PadKind.SMD,
            size_x_nm=400,
            size_y_nm=400,
            roundrect_radius_nm=None,
            drill_x_nm=None,
            drill_y_nm=None,
            layer_ids=(LAYER_ID,),
        ),
        Pad(
            id="pad:02",
            net_id=NET_ID,
            center=PointNM(*end),
            rotation_udeg=0,
            shape=PadShape.RECT,
            kind=PadKind.SMD,
            size_x_nm=400,
            size_y_nm=400,
            roundrect_radius_nm=None,
            drill_x_nm=None,
            drill_y_nm=None,
            layer_ids=(LAYER_ID,),
        ),
    )
    content = make_content(
        source=SourceInfo(
            format="test",
            revision=f"sha256:{'a' * 64}",
            format_version="1",
            generator="layered-routing-fixture",
        ),
        outline=(OutlineContour(id="contour:main", outer=_rectangle(0, 0, 10_000, 10_000)),),
        copper_layers=(
            Layer(id=LAYER_ID, name="F.Cu", index=0),
            Layer(id=BACK_LAYER_ID, name="B.Cu", index=1),
        ),
        nets=(Net(id=NET_ID, name="AUDIO"),),
        constraints=ConstraintSet(
            net_classes=(net_class,),
            assignments=(NetClassAssignment(net_id=NET_ID, net_class_id=net_class.id),),
        ),
        footprints=(
            Footprint(
                id="footprint:routing-fixture",
                origin=PointNM(*start),
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=("pad:01", "pad:02"),
            ),
        ),
        pads=pads,
        keepouts=keepouts,
    )
    return make_snapshot(content)


def _keepout(
    identifier: str,
    layer_ids: tuple[str, ...],
    bounds: tuple[int, int, int, int],
    *,
    tracks: bool,
    vias: bool,
) -> Keepout:
    return Keepout(
        id=identifier,
        layer_ids=layer_ids,
        boundary=_rectangle(*bounds),
        prohibit_tracks=tracks,
        prohibit_vias=vias,
        prohibit_pads=False,
        prohibit_zones=False,
        prohibit_footprints=False,
    )


def _request(snapshot, **changes: object) -> LayeredRouteRequest:
    defaults: dict[str, object] = {
        "board_revision": snapshot.snapshot_digest,
        "net_id": NET_ID,
        "start_pad_id": "pad:01",
        "end_pad_id": "pad:02",
        "start_layer_id": LAYER_ID,
        "end_layer_id": LAYER_ID,
        "grid_step_nm": 1_000,
        "settings": LayeredAStarSettings(via_cost=2),
    }
    defaults.update(changes)
    return LayeredRouteRequest(**defaults)  # type: ignore[arg-type]


def _candidate(result):
    assert result.ok
    assert result.candidate is not None
    assert result.diagnostic is None
    return result.candidate


def test_same_layer_board_ir_route_is_deterministic_and_content_addressed() -> None:
    snapshot = _two_layer_snapshot()
    request = _request(snapshot)
    router = LayeredBoardRouter()

    first = _candidate(router.propose(snapshot, request))
    second = _candidate(router.propose(snapshot, request))

    assert router.name == "board-layered-a-star-v1"
    assert first.router_version == LAYERED_ROUTER_VERSION
    assert first == second
    assert first.cost.via_count == 0
    assert first.patch.paths[0].layer_id == LAYER_ID
    assert first.patch.paths[0].vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))
    assert verify_layered_candidate_id(first)

    another_seed = _candidate(router.propose(snapshot, replace(request, seed=1)))
    assert another_seed.patch == first.patch
    assert another_seed.candidate_id != first.candidate_id


def test_track_keepout_routes_on_back_layer_and_emits_two_vias() -> None:
    wall = _keepout(
        "keepout:front-wall",
        (LAYER_ID,),
        (4_000, 0, 6_000, 10_000),
        tracks=True,
        vias=False,
    )
    snapshot = _two_layer_snapshot(keepouts=(wall,))

    candidate = _candidate(LayeredBoardRouter().propose(snapshot, _request(snapshot)))

    assert candidate.cost.via_count == 2
    assert {path.layer_id for path in candidate.patch.paths} == {LAYER_ID, BACK_LAYER_ID}
    assert candidate.patch.vias[0].center.x < 4_000
    assert candidate.patch.vias[-1].center.x > 6_000
    assert verify_layered_candidate_id(candidate)


def test_via_keepout_and_track_wall_fail_closed_without_a_path() -> None:
    wall = _keepout(
        "keepout:front-wall",
        (LAYER_ID,),
        (4_000, 0, 6_000, 10_000),
        tracks=True,
        vias=False,
    )
    no_vias = _keepout(
        "keepout:no-vias",
        (LAYER_ID, BACK_LAYER_ID),
        (0, 0, 10_000, 10_000),
        tracks=False,
        vias=True,
    )
    snapshot = _two_layer_snapshot(keepouts=(wall, no_vias))
    result = LayeredBoardRouter().propose(snapshot, _request(snapshot))

    assert not result.ok
    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredRouteFailureCode.NO_PATH


def test_stale_off_grid_and_ambiguous_endpoint_requests_are_rejected() -> None:
    router = LayeredBoardRouter()
    snapshot = _two_layer_snapshot()

    stale = router.propose(snapshot, _request(snapshot, board_revision=OTHER_REVISION))
    assert stale.diagnostic is not None
    assert stale.diagnostic.code is LayeredRouteFailureCode.STALE_REVISION

    off_grid_snapshot = _two_layer_snapshot(end=(9_500, 5_000))
    off_grid = router.propose(off_grid_snapshot, _request(off_grid_snapshot))
    assert off_grid.diagnostic is not None
    assert off_grid.diagnostic.code is LayeredRouteFailureCode.OFF_GRID

    dual_layer_pads = tuple(
        replace(pad, layer_ids=(LAYER_ID, BACK_LAYER_ID)) for pad in snapshot.content.pads
    )
    ambiguous_snapshot = make_snapshot(replace(snapshot.content, pads=dual_layer_pads))
    ambiguous_request = replace(
        _request(ambiguous_snapshot), start_layer_id=None, end_layer_id=None
    )
    ambiguous = router.propose(ambiguous_snapshot, ambiguous_request)
    assert ambiguous.diagnostic is not None
    assert ambiguous.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST


def test_tampering_with_a_candidate_breaks_its_content_digest() -> None:
    snapshot = _two_layer_snapshot()
    candidate = _candidate(LayeredBoardRouter().propose(snapshot, _request(snapshot)))
    tampered = replace(candidate, patch=replace(candidate.patch, width_nm=201))

    with pytest.raises(ValueError, match="candidate ID"):
        verify_layered_candidate_id(tampered)
