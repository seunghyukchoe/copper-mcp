from __future__ import annotations

import random
from dataclasses import replace

import pytest

from copper_mcp.board_ir import (
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
from copper_mcp.routing import AStarRouter, AStarSettings, RouteFailureCode, RouteRequest
from copper_mcp.routing import astar as astar_module
from copper_mcp.routing.oracle import run_dijkstra_oracle
from copper_mcp.routing.spatial_index import ConservativeSpatialIndex, SpatialIndexEntry

LAYER_ID = "layer:F.Cu"
NET_ID = "net:audio"


def _ring(bounds: tuple[int, int, int, int]) -> Ring:
    min_x, min_y, max_x, max_y = bounds
    return Ring(
        (
            PointNM(min_x, min_y),
            PointNM(max_x, min_y),
            PointNM(max_x, max_y),
            PointNM(min_x, max_y),
        )
    )


def _routing_fixture() -> tuple[object, RouteRequest]:
    layer = Layer(id=LAYER_ID, name="F.Cu", index=0, kind="signal")
    net = Net(id=NET_ID, name="AUDIO")
    net_class = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=100,
        track_width_nm=200,
        via_diameter_nm=600,
        via_drill_nm=300,
    )
    start = PointNM(1_000, 10_000)
    end = PointNM(19_000, 10_000)
    pads = (
        Pad(
            id="pad:01",
            net_id=NET_ID,
            center=start,
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
            center=end,
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
    # One central barrier makes the path choose a detour. The remaining objects are deliberately
    # remote; an indexed query should avoid handing them to exact predicates.
    keepout_bounds = (
        (9_000, 9_000, 11_000, 11_000),
        *tuple(
            (
                1_000 + (index % 7) * 2_500,
                1_500 if index % 2 else 17_000,
                2_000 + (index % 7) * 2_500,
                2_000 if index % 2 else 17_500,
            )
            for index in range(15)
        ),
    )
    content = make_content(
        source=SourceInfo(
            format="test",
            revision="sha256:" + "a" * 64,
            format_version="1",
            generator="spatial-index-test",
        ),
        outline=(_outline(),),
        copper_layers=(layer,),
        nets=(net,),
        constraints=ConstraintSet(
            net_classes=(net_class,),
            assignments=(NetClassAssignment(net_id=NET_ID, net_class_id=net_class.id),),
        ),
        footprints=(
            Footprint(
                id="footprint:fixture",
                origin=start,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=("pad:01", "pad:02"),
            ),
        ),
        pads=pads,
        keepouts=tuple(
            Keepout(
                id=f"keepout:{index:02d}",
                layer_ids=(LAYER_ID,),
                boundary=_ring(bounds),
                prohibit_tracks=True,
                prohibit_vias=True,
                prohibit_pads=False,
                prohibit_zones=False,
                prohibit_footprints=False,
            )
            for index, bounds in enumerate(keepout_bounds)
        ),
    )
    snapshot = make_snapshot(content)
    settings = AStarSettings(
        grid_step_nm=1_000,
        bend_penalty_nm=500,
        proximity_penalty_nm=50,
        max_grid_nodes=1_000,
        max_expansions=5_000,
        max_obstacles=128,
        max_obstacle_checks=1_000_000,
    )
    request = RouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=NET_ID,
        layer_id=LAYER_ID,
        seed=7,
        settings=settings,
    )
    return snapshot, request


def _outline() -> OutlineContour:
    return OutlineContour(id="contour:main", outer=_ring((0, 0, 20_000, 20_000)))


def _legacy_values(
    entries: tuple[SpatialIndexEntry[str], ...], bounds: tuple[int, int, int, int]
) -> tuple[str, ...]:
    return tuple(
        entry.value
        for entry in entries
        if entry.bounds[0] <= bounds[2]
        and bounds[0] <= entry.bounds[2]
        and entry.bounds[1] <= bounds[3]
        and bounds[1] <= entry.bounds[3]
    )


def test_spatial_index_is_deterministic_and_has_no_false_negatives() -> None:
    rng = random.Random(20260805)  # noqa: S311 - deterministic test data, not a secret
    entries = tuple(
        SpatialIndexEntry(
            ordinal=index,
            bounds=(
                x := rng.randrange(-20_000, 20_000),
                y := rng.randrange(-20_000, 20_000),
                x + rng.randrange(1, 2_000),
                y + rng.randrange(1, 2_000),
            ),
            value=f"obstacle:{index:03d}",
        )
        for index in range(64)
    )
    index = ConservativeSpatialIndex(entries, min_index_entries=0)
    assert index.indexed
    for _ in range(500):
        x = rng.randrange(-22_000, 22_000)
        y = rng.randrange(-22_000, 22_000)
        bounds = (x, y, x + rng.randrange(0, 3_000), y + rng.randrange(0, 3_000))
        expected = _legacy_values(entries, bounds)
        assert index.query(bounds) == expected
        with_stats, candidates_examined = index.query_with_stats(bounds)
        assert with_stats == expected
        assert candidates_examined >= len(with_stats)
        assert with_stats == index.query(bounds)


def test_spatial_index_falls_back_for_small_or_giant_inputs() -> None:
    small = ConservativeSpatialIndex(
        (SpatialIndexEntry(ordinal=0, bounds=(0, 0, 10, 10), value="one"),),
        min_index_entries=8,
    )
    assert not small.indexed
    assert small.query((100, 100, 101, 101)) == ("one",)

    giant = ConservativeSpatialIndex(
        tuple(
            SpatialIndexEntry(
                ordinal=index,
                bounds=(-1_000_000, -1_000_000, 1_000_000, 1_000_000),
                value=str(index),
            )
            for index in range(16)
        ),
        min_index_entries=0,
        max_bucket_entries=16,
    )
    assert not giant.indexed
    assert giant.query((2_000_000, 2_000_000, 2_000_001, 2_000_001)) == tuple(
        str(index) for index in range(16)
    )


def test_indexed_and_linear_router_replays_have_identical_route_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, request = _routing_fixture()
    router = AStarRouter()

    monkeypatch.setattr(astar_module, "_SPATIAL_INDEX_MIN_ENTRIES", 10_000)
    legacy = router.propose(snapshot, request)
    legacy_oracle = run_dijkstra_oracle(snapshot, request)

    monkeypatch.setattr(astar_module, "_SPATIAL_INDEX_MIN_ENTRIES", 0)
    indexed = router.propose(snapshot, request)
    indexed_oracle = run_dijkstra_oracle(snapshot, request)

    assert legacy.candidate is not None
    assert indexed.candidate is not None
    assert legacy.candidate.patch == indexed.candidate.patch
    assert legacy.candidate.cost == indexed.candidate.cost
    assert legacy.candidate.metrics.expanded_states == indexed.candidate.metrics.expanded_states
    assert indexed.candidate.metrics.obstacle_checks < legacy.candidate.metrics.obstacle_checks
    assert legacy_oracle.ok and indexed_oracle.ok
    assert legacy_oracle.total_cost_nm == indexed_oracle.total_cost_nm
    assert legacy_oracle.bend_count == indexed_oracle.bend_count
    assert legacy_oracle.proximity_steps == indexed_oracle.proximity_steps
    assert indexed_oracle.obstacle_checks < legacy_oracle.obstacle_checks


def test_indexed_router_preserves_cancellation_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, request = _routing_fixture()
    monkeypatch.setattr(astar_module, "_SPATIAL_INDEX_MIN_ENTRIES", 0)
    result = AStarRouter().propose(snapshot, request, cancelled=lambda: True)
    assert result.candidate is None
    assert result.diagnostic is not None
    assert result.diagnostic.code is RouteFailureCode.CANCELLED


def test_indexed_router_enforces_obstacle_check_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, request = _routing_fixture()
    monkeypatch.setattr(astar_module, "_SPATIAL_INDEX_MIN_ENTRIES", 0)
    within_budget = AStarRouter().propose(
        snapshot,
        replace(request, settings=replace(request.settings, max_obstacle_checks=400)),
    )
    assert within_budget.candidate is not None
    assert within_budget.candidate.metrics.obstacle_checks <= 400

    exhausted = AStarRouter().propose(
        snapshot,
        replace(request, settings=replace(request.settings, max_obstacle_checks=200)),
    )
    assert exhausted.candidate is None
    assert exhausted.diagnostic is not None
    assert exhausted.diagnostic.code is RouteFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED
    assert exhausted.diagnostic.obstacle_checks == 200
