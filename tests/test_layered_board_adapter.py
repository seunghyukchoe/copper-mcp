from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
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
    Zone,
    make_content,
    make_snapshot,
)
from copper_mcp.routing import (
    LAYERED_ROUTER_VERSION,
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteFailureCode,
    LayeredRouteRequest,
    canonical_layered_candidate_bytes,
    verify_layered_candidate_id,
)

LAYER_ID = "layer:F.Cu"
OTHER_REVISION = f"sha256:{'b' * 64}"
BACK_LAYER_ID = "layer:B.Cu"
INNER_LAYER_ID = "layer:In1.Cu"
NET_ID = "net:audio"

_FIXTURES = Path(__file__).parent / "fixtures" / "route-candidate"
BLOCKED_PAD_FIXTURE = _FIXTURES / "blocked-pad.kicad_pcb"
FOUR_LAYER_FIXTURE = _FIXTURES / "four-layer-blocked-outers.kicad_pcb"
REAL_FIXTURE_NET_CLASS = NetClass(
    id="class:default",
    name="Default",
    clearance_nm=250_000,
    track_width_nm=250_000,
    via_diameter_nm=800_000,
    via_drill_nm=400_000,
)

# Committed candidate identities.  These are the whole point of a content-addressed candidate: if
# either digest changes, persisted candidates stop verifying and the change is a breaking one that
# must bump LAYERED_ROUTER_VERSION and be declared in the ADR, the ledger, and the CHANGELOG.
LEGACY_TWO_LAYER_CANDIDATE_ID = (
    "sha256:5ea134fc319c5a7fa4b7d64b9e6cc47b8439f60c821391c3c3e4c46678f82818"
)
FOUR_LAYER_CANDIDATE_ID = "sha256:dc1fcf371857653df95fd7f9a7a2f7fcb16dbc19308144864cd1e23eeb63ab0e"
THREE_LAYER_CANDIDATE_ID = "sha256:31c68bbe5333a1eea0d7894df1799338718f72d90276d857741d1fcb8ce3c3ac"


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


def test_three_layer_board_routes_only_through_the_inner_signal_layer() -> None:
    """A full-stack via may land on the otherwise clear inner signal layer.

    The counterpart two-layer fixture blocks both available layers, establishing the committed
    oracle for this generalized-stack increment without claiming serializable KiCad output.
    """

    front_wall = _keepout(
        "keepout:front-wall",
        (LAYER_ID,),
        (4_000, 0, 6_000, 10_000),
        tracks=True,
        vias=False,
    )
    back_wall = _keepout(
        "keepout:back-wall",
        (BACK_LAYER_ID,),
        (4_000, 0, 6_000, 10_000),
        tracks=True,
        vias=False,
    )
    two_layer = _two_layer_snapshot(keepouts=(front_wall, back_wall))
    blocked = LayeredBoardRouter().propose(two_layer, _request(two_layer))
    assert blocked.diagnostic is not None
    assert blocked.diagnostic.code is LayeredRouteFailureCode.NO_PATH

    three_layer = make_snapshot(
        replace(
            two_layer.content,
            copper_layers=(
                Layer(id=LAYER_ID, name="F.Cu", index=0),
                Layer(id=INNER_LAYER_ID, name="In1.Cu", index=1),
                Layer(id=BACK_LAYER_ID, name="B.Cu", index=2),
            ),
        )
    )
    candidate = _candidate(
        LayeredBoardRouter().propose(
            three_layer,
            _request(
                three_layer,
                settings=LayeredAStarSettings(via_cost=2, max_vias=2),
            ),
        )
    )

    assert {path.layer_id for path in candidate.patch.paths} == {LAYER_ID, INNER_LAYER_ID}
    assert candidate.cost.via_count == 2
    assert all(
        (via.start_layer_id, via.end_layer_id) == (LAYER_ID, BACK_LAYER_ID)
        for via in candidate.patch.vias
    )
    # Committed three-layer identity; see the module-level note on why these digests are pinned.
    assert candidate.candidate_id == THREE_LAYER_CANDIDATE_ID
    assert verify_layered_candidate_id(candidate)


def test_two_layer_return_via_pins_its_committed_candidate_identity() -> None:
    """Pin the exact bytes of a two-layer candidate whose second via is a RETURN transition.

    Layered candidates are content-addressed and are persisted by ADR-0043 durable jobs, ADR-0047
    manifests, and ADR-0048 exports.  A candidate recorded before the ordered-layer seam states its
    via span in traversal order, so this route's second via reads ``B.Cu -> F.Cu``.  Nothing else in
    the suite compares a candidate against a committed digest, so a normalization that reorders that
    pair changes 32 bytes of a 1,527-byte payload, keeps the length identical, and silently
    invalidates every persisted candidate while 1,500 tests stay green.  This pin is that alarm.
    """

    profile = KiCadConstraintProfile(
        net_classes=(REAL_FIXTURE_NET_CLASS,), default_net_class_id=REAL_FIXTURE_NET_CLASS.id
    )
    conversion = parse_kicad_bytes(BLOCKED_PAD_FIXTURE.read_bytes(), profile)
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    net_id = snapshot.content.pads[0].net_id
    pads = tuple(pad for pad in snapshot.content.pads if pad.net_id == net_id)

    candidate = _candidate(
        LayeredBoardRouter().propose(
            snapshot,
            LayeredRouteRequest(
                board_revision=snapshot.snapshot_digest,
                net_id=pads[0].net_id,
                start_pad_id=pads[0].id,
                end_pad_id=pads[1].id,
                start_layer_id=LAYER_ID,
                end_layer_id=LAYER_ID,
                grid_step_nm=1_000,
                settings=LayeredAStarSettings(via_cost=2),
            ),
        )
    )

    assert len(canonical_layered_candidate_bytes(candidate)) == 1_527
    assert candidate.candidate_id == LEGACY_TWO_LAYER_CANDIDATE_ID
    assert [(via.start_layer_id, via.end_layer_id) for via in candidate.patch.vias] == [
        (LAYER_ID, BACK_LAYER_ID),
        (BACK_LAYER_ID, LAYER_ID),
    ]
    assert verify_layered_candidate_id(candidate)


def test_real_four_layer_fixture_pins_its_committed_candidate_identity() -> None:
    """Pin a candidate built from a committed, KiCad 10.0.5-accepted four-layer board.

    Three or more layers have no legacy identity to preserve, and a traversed inner pair would
    misstate a full-stack through via as a blind/buried one Board IR v0.2 cannot represent, so the
    recorded span is the canonical outer pair.  Pinning the digest makes that rule a committed
    contract rather than an implementation detail.
    """

    profile = KiCadConstraintProfile(
        net_classes=(REAL_FIXTURE_NET_CLASS,), default_net_class_id=REAL_FIXTURE_NET_CLASS.id
    )
    conversion = parse_kicad_bytes(FOUR_LAYER_FIXTURE.read_bytes(), profile)
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    assert [layer.id for layer in snapshot.content.copper_layers] == [
        LAYER_ID,
        INNER_LAYER_ID,
        "layer:In2.Cu",
        BACK_LAYER_ID,
    ]
    endpoints = tuple(
        pad for pad in snapshot.content.pads if pad.center.x in (10_000_000, 30_000_000)
    )

    candidate = _candidate(
        LayeredBoardRouter().propose(
            snapshot,
            LayeredRouteRequest(
                board_revision=snapshot.snapshot_digest,
                net_id=endpoints[0].net_id,
                start_pad_id=endpoints[0].id,
                end_pad_id=endpoints[1].id,
                start_layer_id=LAYER_ID,
                end_layer_id=LAYER_ID,
                grid_step_nm=1_000_000,
                settings=LayeredAStarSettings(via_cost=2),
            ),
        )
    )

    assert candidate.candidate_id == FOUR_LAYER_CANDIDATE_ID
    assert [path.layer_id for path in candidate.patch.paths] == [
        LAYER_ID,
        INNER_LAYER_ID,
        LAYER_ID,
    ]
    assert all(
        (via.start_layer_id, via.end_layer_id) == (LAYER_ID, BACK_LAYER_ID)
        for via in candidate.patch.vias
    )
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


def test_malformed_request_is_not_misclassified_as_stale_without_expected_revision() -> None:
    snapshot = _two_layer_snapshot()
    request = _request(snapshot, net_id="not-a-net")

    result = LayeredBoardRouter().propose(snapshot, request)

    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST


def test_malformed_expected_revision_is_invalid_even_when_it_differs() -> None:
    snapshot = _two_layer_snapshot()
    request = _request(snapshot, expected_revision="not-a-digest")

    result = LayeredBoardRouter().propose(snapshot, request)

    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST


def test_malformed_board_revision_is_invalid_even_with_a_valid_expected_digest() -> None:
    snapshot = _two_layer_snapshot()
    request = _request(snapshot, board_revision="bad", expected_revision=OTHER_REVISION)

    result = LayeredBoardRouter().propose(snapshot, request)

    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST


def test_malformed_layered_settings_fail_closed_before_physical_obstacle_budgeting() -> None:
    snapshot = _two_layer_snapshot()
    malformed = replace(LayeredAStarSettings(), max_obstacles="invalid")
    request = _request(snapshot, settings=malformed)

    result = LayeredBoardRouter().propose(snapshot, request)

    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST


def test_foreign_zone_uses_its_net_class_clearance_in_both_envelopes() -> None:
    """A foreign zone's assigned class is part of the physical clearance rule."""

    base = _two_layer_snapshot()
    strict_class = NetClass(
        id="class:strict",
        name="Strict",
        clearance_nm=1_200,
        track_width_nm=200,
        via_diameter_nm=600,
        via_drill_nm=300,
    )
    foreign_net = Net(id="net:foreign", name="FOREIGN")
    foreign_zone = Zone(
        id="zone:foreign",
        net_id=foreign_net.id,
        layer_id=LAYER_ID,
        boundary=_rectangle(4_000, 3_000, 6_000, 4_000),
        clearance_nm=100,
        min_thickness_nm=100,
        thermal_gap_nm=100,
        thermal_bridge_width_nm=100,
    )
    constraints = replace(
        base.content.constraints,
        net_classes=(*base.content.constraints.net_classes, strict_class),
        assignments=(
            *base.content.constraints.assignments,
            NetClassAssignment(net_id=foreign_net.id, net_class_id=strict_class.id),
        ),
    )
    snapshot = make_snapshot(
        make_content(
            source=base.content.source,
            outline=base.content.outline,
            copper_layers=base.content.copper_layers,
            nets=(*base.content.nets, foreign_net),
            constraints=constraints,
            footprints=base.content.footprints,
            pads=base.content.pads,
            vias=base.content.vias,
            segments=base.content.segments,
            arcs=base.content.arcs,
            zones=(foreign_zone,),
            keepouts=base.content.keepouts,
        )
    )

    result = LayeredBoardRouter().propose(snapshot, _request(snapshot))

    candidate = _candidate(result)
    # The 1,200 nm class clearance requires the route centreline to clear the zone's upper
    # edge by at least 1,300 nm; on the 1,000 nm lattice that means the y=7,000 detour.
    assert candidate.patch.paths[0].vertices[0] == PointNM(1_000, 5_000)
    assert any(vertex.y >= 7_000 for path in candidate.patch.paths for vertex in path.vertices)


def test_layered_obstacle_ceiling_stops_before_constructing_the_next_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hard obstacle budget must stop quantization at the first rejected envelope."""

    snapshot = _two_layer_snapshot()
    calls = 0

    import copper_mcp.routing.layered_board_adapter as adapter

    original = adapter._cell_obstacle

    def counted(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(adapter, "_cell_obstacle", counted)
    result = LayeredBoardRouter().propose(
        snapshot,
        _request(snapshot, settings=LayeredAStarSettings(max_obstacles=1)),
    )

    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED
    assert calls == 1


def test_tampering_with_a_candidate_breaks_its_content_digest() -> None:
    snapshot = _two_layer_snapshot()
    candidate = _candidate(LayeredBoardRouter().propose(snapshot, _request(snapshot)))
    tampered = replace(candidate, patch=replace(candidate.patch, width_nm=201))

    with pytest.raises(ValueError, match="candidate ID"):
        verify_layered_candidate_id(tampered)
