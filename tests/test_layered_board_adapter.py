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
    PadCopperEnvelope,
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
    VerifiedFill,
    canonical_layered_candidate_bytes,
    fill_binding_for,
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
# Re-pinned once, in the fix for issue #104 (D-152), from
# sha256:dc1fcf371857653df95fd7f9a7a2f7fcb16dbc19308144864cd1e23eeb63ab0e.  The router did not
# change and LAYERED_ROUTER_VERSION did not move: the *fixture* changed.  It declared its copper
# layers with IDs KiCad never writes (In1.Cu=2, In2.Cu=4, B.Cu=6 - CopperMCP's own mistaken
# position*2 rule), and a candidate ID binds the board revision, which is a digest of the file's
# bytes.  Correcting the fixture to KiCad's real numbering therefore re-addresses it.  No published
# artifact is affected, because this address only ever named this repository's own test board.
FOUR_LAYER_CANDIDATE_ID = "sha256:efff3a13e708233dcc1e45b4b26b12ee9762b812e970d7e46f28070605b97fe0"
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


def test_custom_pad_copper_envelope_is_a_routing_obstacle_beyond_anchor() -> None:
    """Over-reader rule: primitive-only copper must block the layered route."""

    base = _two_layer_snapshot()
    blocking_pad = Pad(
        id="pad:custom-blocker",
        net_id=None,
        center=PointNM(5_000, 5_000),
        rotation_udeg=0,
        shape=PadShape.RECT,
        kind=PadKind.SMD,
        size_x_nm=400,
        size_y_nm=400,
        roundrect_radius_nm=None,
        drill_x_nm=None,
        drill_y_nm=None,
        layer_ids=(LAYER_ID,),
        copper_envelope=PadCopperEnvelope(-1_500, -1_500, 1_500, 1_500),
    )
    footprint = base.content.footprints[0]
    snapshot = make_snapshot(
        replace(
            base.content,
            footprints=(replace(footprint, pad_ids=(*footprint.pad_ids, blocking_pad.id)),),
            pads=(*base.content.pads, blocking_pad),
        )
    )

    candidate = _candidate(LayeredBoardRouter().propose(snapshot, _request(snapshot)))

    assert candidate.patch.paths[0].vertices != (
        PointNM(1_000, 5_000),
        PointNM(9_000, 5_000),
    )
    assert all(
        not (3_500 <= point.x <= 6_500 and 3_500 <= point.y <= 6_500)
        for path in candidate.patch.paths
        for point in path.vertices
    )


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
    # Stable across ADR-0106 by construction, not by luck: this route was searched against
    # the conservative zone envelopes, so it records no fill binding and the canonical
    # identity payload omits the key entirely.
    assert candidate.fill_binding is None
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
    # Stable across ADR-0106 by construction, not by luck: this route was searched against
    # the conservative zone envelopes, so it records no fill binding and the canonical
    # identity payload omits the key entirely.
    assert candidate.fill_binding is None
    assert [(via.start_layer_id, via.end_layer_id) for via in candidate.patch.vias] == [
        (LAYER_ID, BACK_LAYER_ID),
        (BACK_LAYER_ID, LAYER_ID),
    ]
    assert verify_layered_candidate_id(candidate)


def test_real_four_layer_fixture_pins_its_committed_candidate_identity() -> None:
    """Pin a candidate built from a committed four-layer board in KiCad's real copper numbering.

    The fixture now declares ``F.Cu=0, In1.Cu=4, In2.Cu=6, B.Cu=2`` - front-to-back declaration
    order, non-ascending IDs - as KiCad writes it.  It previously carried CopperMCP's own invented
    numbering, which is why a defect that refused every real four-layer board went unseen (#104);
    see ``docs/research/kicad-copper-layer-numbering-v1.md``.

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
    # Stable across ADR-0106 by construction, not by luck: this route was searched against
    # the conservative zone envelopes, so it records no fill binding and the canonical
    # identity payload omits the key entirely.
    assert candidate.fill_binding is None
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


# --- Freshness-verified fill obstacles (ADR-0070) ---------------------------------------------

FILL_NET_ID = "net:power"
FILL_ZONE_BOUNDS = (3_000, 3_000, 7_000, 7_000)
FIXTURE_REVISION = f"sha256:{'a' * 64}"
# Track half-width (100 nm) plus the governing clearance (100 nm).  A candidate centreline must
# clear every modelled island by at least this much, and the assertion below checks it exactly.
FILL_CLEARANCE_NM = 200


def _fill_snapshot() -> BoardIRSnapshot:
    """A two-layer board whose only direct front route runs through a foreign pour.

    The back layer carries a full-height track wall, so a via detour cannot substitute for the
    corridor under test; the conservative answer has to be a front-layer detour around the whole
    zone outline.
    """

    base = _two_layer_snapshot(
        keepouts=(
            _keepout(
                "keepout:back-wall",
                (BACK_LAYER_ID,),
                (4_000, 0, 6_000, 10_000),
                tracks=True,
                vias=False,
            ),
        )
    )
    foreign_net = Net(id=FILL_NET_ID, name="POWER")
    audio_class = base.content.constraints.net_classes[0]
    return make_snapshot(
        make_content(
            source=base.content.source,
            outline=base.content.outline,
            copper_layers=base.content.copper_layers,
            nets=(*base.content.nets, foreign_net),
            constraints=replace(
                base.content.constraints,
                assignments=(
                    *base.content.constraints.assignments,
                    NetClassAssignment(net_id=foreign_net.id, net_class_id=audio_class.id),
                ),
            ),
            footprints=base.content.footprints,
            pads=base.content.pads,
            zones=(
                Zone(
                    id="zone:power",
                    net_id=foreign_net.id,
                    layer_id=LAYER_ID,
                    boundary=_rectangle(*FILL_ZONE_BOUNDS),
                    clearance_nm=100,
                    min_thickness_nm=100,
                    thermal_gap_nm=100,
                    thermal_bridge_width_nm=100,
                ),
            ),
            keepouts=base.content.keepouts,
        )
    )


def _island(
    bounds: tuple[int, int, int, int],
    *,
    revision: str = FIXTURE_REVISION,
    net_id: str = FILL_NET_ID,
    layer_id: str = LAYER_ID,
) -> VerifiedFill:
    min_x, min_y, max_x, max_y = bounds
    return VerifiedFill(
        net_id=net_id,
        layer_id=layer_id,
        points=(
            PointNM(min_x, min_y),
            PointNM(max_x, min_y),
            PointNM(max_x, max_y),
            PointNM(min_x, max_y),
        ),
        source_revision=revision,
    )


def _clears_island(candidate: object, bounds: tuple[int, int, int, int]) -> bool:
    """Exact integer check that no routed centreline comes within clearance of the island."""

    min_x, min_y, max_x, max_y = bounds
    for path in candidate.patch.paths:  # type: ignore[attr-defined]
        if path.layer_id != LAYER_ID:
            continue
        for start, end in zip(path.vertices, path.vertices[1:], strict=False):
            gap_x = max(0, min_x - max(start.x, end.x), min(start.x, end.x) - max_x)
            gap_y = max(0, min_y - max(start.y, end.y), min(start.y, end.y) - max_y)
            if gap_x * gap_x + gap_y * gap_y < FILL_CLEARANCE_NM * FILL_CLEARANCE_NM:
                return False
    return True


def test_verified_fill_opens_a_layered_corridor_the_zone_envelope_forbids() -> None:
    snapshot = _fill_snapshot()
    router = LayeredBoardRouter()
    bounds = (3_000, 6_000, 7_000, 7_000)
    island = _island(bounds)

    conservative = _candidate(router.propose(snapshot, _request(snapshot)))
    fill_aware = _candidate(router.propose(snapshot, _request(snapshot, verified_fill=(island,))))

    assert conservative.cost.wire_length_nm == 14_000
    assert fill_aware.cost.wire_length_nm == 8_000
    assert fill_aware.cost.via_count == 0
    assert fill_aware.patch.paths[0].vertices == (PointNM(1_000, 5_000), PointNM(9_000, 5_000))
    # A tighter obstacle must not become an unsafe one: the straight corridor still clears the
    # island copper by the full governing clearance.
    assert _clears_island(fill_aware, bounds)
    assert verify_layered_candidate_id(fill_aware)
    # Fill evidence changes geometry, so it must change the content-addressed identity too.
    assert fill_aware.candidate_id != conservative.candidate_id
    replayed = router.propose(snapshot, _request(snapshot, verified_fill=(island,))).candidate
    assert replayed == fill_aware


def test_growing_a_verified_island_never_cheapens_the_layered_route() -> None:
    """Metamorphic: nested islands inside one outline must be monotone in route cost.

    This is the direction that matters.  Shrinking the evidence is allowed to open corridors —
    that is the whole feature — but growing it must never do so, and no island may ever beat the
    conservative envelope by more than the copper it proved absent.
    """

    snapshot = _fill_snapshot()
    router = LayeredBoardRouter()
    nested = (
        (3_000, 6_000, 7_000, 7_000),
        (3_000, 5_000, 7_000, 7_000),
        (3_000, 4_000, 7_000, 7_000),
        FILL_ZONE_BOUNDS,
    )

    costs: list[int] = []
    for bounds in nested:
        candidate = _candidate(
            router.propose(snapshot, _request(snapshot, verified_fill=(_island(bounds),)))
        )
        assert _clears_island(candidate, bounds)
        costs.append(candidate.cost.wire_length_nm)

    conservative = _candidate(router.propose(snapshot, _request(snapshot)))
    assert costs == sorted(costs)
    # The largest island is the outline itself, so it can do no better than the envelope it
    # replaced, and no verified island may ever cost more than routing conservatively.
    assert costs[-1] == conservative.cost.wire_length_nm
    assert all(cost <= conservative.cost.wire_length_nm for cost in costs)


def test_verified_fill_from_another_board_revision_fails_closed() -> None:
    snapshot = _fill_snapshot()
    stale = _island((3_000, 6_000, 7_000, 7_000), revision=OTHER_REVISION)

    result = LayeredBoardRouter().propose(snapshot, _request(snapshot, verified_fill=(stale,)))

    assert result.candidate is None
    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredRouteFailureCode.STALE_REVISION
    assert "different board revision" in result.diagnostic.message


def test_verified_fill_without_a_matching_zone_fails_closed() -> None:
    snapshot = _fill_snapshot()
    orphan = _island((3_000, 6_000, 7_000, 7_000), layer_id=BACK_LAYER_ID)

    result = LayeredBoardRouter().propose(snapshot, _request(snapshot, verified_fill=(orphan,)))

    assert result.candidate is None
    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY
    assert "matching Board IR zone" in result.diagnostic.message


def test_verified_fill_escaping_its_zone_outline_fails_closed() -> None:
    """An island wider than the zone backing it is not clipped fill, so it is not evidence.

    Without this gate the replacement could retire a genuinely blocking envelope in favour of a
    box that does not cover it — the one way this feature could model less copper than exists.
    """

    snapshot = _fill_snapshot()
    escaping = _island((3_000, 6_000, 7_500, 7_000))

    result = LayeredBoardRouter().propose(snapshot, _request(snapshot, verified_fill=(escaping,)))

    assert result.candidate is None
    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY
    assert "escapes its backing" in result.diagnostic.message


@pytest.mark.parametrize(
    ("fill", "expected"),
    [
        ([], "must be a tuple"),
        (("island",), "must be a VerifiedFill"),
        ((_island((3_000, 6_000, 7_000, 7_000), net_id="power"),), "identity is malformed"),
        ((_island((3_000, 6_000, 7_000, 7_000), layer_id="F.Cu"),), "identity is malformed"),
        (
            (_island((3_000, 6_000, 7_000, 7_000), revision="sha256:zz"),),
            "source revision is malformed",
        ),
        (
            (
                VerifiedFill(
                    net_id=FILL_NET_ID,
                    layer_id=LAYER_ID,
                    points=(PointNM(0, 0), PointNM(1_000, 0)),
                    source_revision=FIXTURE_REVISION,
                ),
            ),
            "not a bounded polygon",
        ),
        (
            (
                VerifiedFill(
                    net_id=FILL_NET_ID,
                    layer_id=LAYER_ID,
                    points=(PointNM(0, 0), PointNM(1_000, 0), (1_000, 1_000)),  # type: ignore[arg-type]
                    source_revision=FIXTURE_REVISION,
                ),
            ),
            "vertex is malformed",
        ),
    ],
)
def test_malformed_verified_fill_is_refused_at_the_request_boundary(
    fill: object, expected: str
) -> None:
    snapshot = _fill_snapshot()

    result = LayeredBoardRouter().propose(snapshot, _request(snapshot, verified_fill=fill))

    assert result.candidate is None
    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST
    assert expected in result.diagnostic.message


@pytest.mark.parametrize("vertex_count", [43_889, 500_000])
def test_calibrated_fill_island_sizes_pass_shape_validation(vertex_count: int) -> None:
    import copper_mcp.routing.layered_board_adapter as adapter

    island = VerifiedFill(
        net_id=FILL_NET_ID,
        layer_id=LAYER_ID,
        points=(PointNM(3_000, 3_000),) * vertex_count,
        source_revision=FIXTURE_REVISION,
    )

    assert adapter._invalid_verified_fill((island,)) is None


def test_fill_island_above_calibrated_ceiling_is_refused_before_bounds_or_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _fill_snapshot()
    router = LayeredBoardRouter()
    candidate = _candidate(router.propose(snapshot, _request(snapshot)))
    over_ceiling = VerifiedFill(
        net_id=FILL_NET_ID,
        layer_id=LAYER_ID,
        points=(PointNM(3_000, 3_000),) * 500_001,
        source_revision=FIXTURE_REVISION,
    )
    request = _request(snapshot, verified_fill=(over_ceiling,))

    def unexpected_work(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("over-ceiling fill reached bounds or hashing")

    import copper_mcp.routing.layered_board_adapter as adapter

    monkeypatch.setattr(adapter, "_points_bounds", unexpected_work)
    monkeypatch.setattr(adapter, "fill_binding_for", unexpected_work)
    proposed = router.propose(snapshot, request)
    replayed = router.replay(snapshot, candidate, request)
    malformed_request = router.propose(snapshot, replace(request, seed=-1))

    for refusal in (proposed, replayed):
        assert refusal.candidate is None
        assert refusal.diagnostic is not None
        assert refusal.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST
        assert refusal.diagnostic.message == "verified fill island is not a bounded polygon"
        assert FILL_NET_ID not in refusal.diagnostic.message
    assert malformed_request.diagnostic is not None
    assert malformed_request.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST
    assert malformed_request.diagnostic.message == "seed is malformed"


def test_excess_verified_fill_island_count_remains_invalid() -> None:
    snapshot = _fill_snapshot()
    island = _island((3_000, 6_000, 7_000, 7_000))

    result = LayeredBoardRouter().propose(
        snapshot,
        _request(snapshot, verified_fill=(island,) * 4_097),
    )

    assert result.candidate is None
    assert result.diagnostic is not None
    assert result.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST
    assert result.diagnostic.message == "verified fill island count exceeds the obstacle ceiling"


def test_verified_fill_aggregate_walk_has_an_exact_obstacle_check_ceiling() -> None:
    import copper_mcp.routing.layered_board_adapter as adapter

    points = (PointNM(0, 0),) * 500_000
    island = VerifiedFill(
        net_id=FILL_NET_ID,
        layer_id=LAYER_ID,
        points=points,
        source_revision=FIXTURE_REVISION,
    )

    assert adapter._verified_fill_over_check_budget((island,) * 20) is None
    assert adapter._verified_fill_over_check_budget((island,) * 21) is not None
    assert adapter._verified_fill_over_check_budget([island]) is None
    assert adapter._verified_fill_over_check_budget(("secret",)) is None


def test_unbounded_verified_fill_is_refused_before_any_bounding_box_work() -> None:
    """Keep the immutable #189 mutation node while exercising the calibrated bounds."""
    test_verified_fill_aggregate_walk_has_an_exact_obstacle_check_ceiling()


def test_verified_fill_aggregate_preflight_reads_lengths_without_touching_vertices() -> None:
    import copper_mcp.routing.layered_board_adapter as adapter

    class LengthOnlyPoints(tuple[PointNM, ...]):
        def __iter__(self):  # type: ignore[no-untyped-def]
            raise AssertionError("aggregate preflight iterated fill vertices")

        def __getitem__(self, index):  # type: ignore[no-untyped-def]
            raise AssertionError("aggregate preflight accessed a fill vertex")

    points = LengthOnlyPoints((PointNM(0, 0),) * 500_000)
    island = VerifiedFill(
        net_id=FILL_NET_ID,
        layer_id=LAYER_ID,
        points=points,
        source_revision=FIXTURE_REVISION,
    )

    assert adapter._verified_fill_over_check_budget((island,) * 20) is None
    assert adapter._verified_fill_over_check_budget((island,) * 21) is not None


def test_verified_fill_aggregate_budget_is_typed_and_matches_replay_without_echo() -> None:
    snapshot = _fill_snapshot()
    router = LayeredBoardRouter()
    candidate = _candidate(router.propose(snapshot, _request(snapshot)))
    points = (PointNM(0, 0),) * 500_000
    island = VerifiedFill(
        net_id=FILL_NET_ID,
        layer_id=LAYER_ID,
        points=points,
        source_revision=FIXTURE_REVISION,
    )
    request = _request(snapshot, verified_fill=(island,) * 21)

    proposed = router.propose(snapshot, request)
    replayed = router.replay(snapshot, candidate, request)
    malformed = router.propose(snapshot, replace(request, seed=-1))

    for refusal in (proposed, replayed):
        assert refusal.candidate is None
        assert refusal.diagnostic is not None
        assert refusal.diagnostic.code is LayeredRouteFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED
        assert refusal.diagnostic.message == (
            "verified fill vertex count exceeds the fill-validation ceiling "
            "(max_verified_fill_vertices=10000000)"
        )
        assert FILL_NET_ID not in refusal.diagnostic.message
    assert malformed.diagnostic is not None
    assert malformed.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST
    assert malformed.diagnostic.message == "seed is malformed"


def test_verified_fill_envelopes_are_charged_against_the_obstacle_budget() -> None:
    """Two islands cost more budget than the single envelope they replaced, and are billed.

    The fixture's conservative model is four endpoint via envelopes, one zone pair, and one
    keepout: seven envelopes.  Replacing that zone with two islands makes nine, so a budget of
    eight is exactly the boundary that separates the two models.
    """

    snapshot = _fill_snapshot()
    islands = (
        _island((3_000, 6_000, 7_000, 7_000)),
        _island((3_000, 3_000, 7_000, 3_200)),
    )
    budget = LayeredAStarSettings(via_cost=2, max_obstacles=8)

    conservative = LayeredBoardRouter().propose(snapshot, _request(snapshot, settings=budget))
    fill_aware = LayeredBoardRouter().propose(
        snapshot, _request(snapshot, verified_fill=islands, settings=budget)
    )
    generous = LayeredBoardRouter().propose(
        snapshot,
        _request(
            snapshot,
            verified_fill=islands,
            settings=LayeredAStarSettings(via_cost=2, max_obstacles=9),
        ),
    )

    assert _candidate(conservative).cost.wire_length_nm == 14_000
    assert fill_aware.candidate is None
    assert fill_aware.diagnostic is not None
    assert fill_aware.diagnostic.code is LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED
    assert _candidate(generous).cost.wire_length_nm == 8_000


# --- A layered candidate records the model that produced it (ADR-0106) -------------------------


def test_a_layered_candidate_records_the_fill_that_produced_it() -> None:
    """The binding is present exactly when fill produced the route, and never otherwise."""

    snapshot = _fill_snapshot()
    router = LayeredBoardRouter()
    island = _island((3_000, 6_000, 7_000, 7_000))

    envelope = _candidate(router.propose(snapshot, _request(snapshot)))
    fill_aware = _candidate(router.propose(snapshot, _request(snapshot, verified_fill=(island,))))

    assert envelope.fill_binding is None
    assert fill_aware.fill_binding == fill_binding_for((island,))
    assert fill_aware.fill_binding is not None
    # The binding is in the identity when it exists, so the two candidates can never collide.
    assert fill_aware.candidate_id != envelope.candidate_id
    assert b'"fill_binding"' not in canonical_layered_candidate_bytes(envelope)
    assert b'"fill_binding"' in canonical_layered_candidate_bytes(fill_aware)
    # The omission is exactly the width of the key it would have added, which is the reason the
    # committed two-, three- and four-layer identities above did not move.
    stripped = canonical_layered_candidate_bytes(replace(fill_aware, fill_binding=None))
    assert b'"fill_binding"' not in stripped
    assert len(canonical_layered_candidate_bytes(fill_aware)) - len(stripped) == len(
        b'"fill_binding":"",'
    ) + len(fill_aware.fill_binding)


def test_a_layered_fill_binding_must_be_content_addressed() -> None:
    snapshot = _fill_snapshot()
    candidate = _candidate(LayeredBoardRouter().propose(snapshot, _request(snapshot)))

    with pytest.raises(ValueError, match="fill binding"):
        replace(candidate, fill_binding="not-a-digest")


def test_an_empty_layered_pour_and_no_pour_are_the_same_obstacle_model() -> None:
    """An empty pour hands the router the model no pour hands it, so it is the same candidate."""

    snapshot = _fill_snapshot()
    router = LayeredBoardRouter()

    without = _candidate(router.propose(snapshot, _request(snapshot)))
    empty = _candidate(router.propose(snapshot, _request(snapshot, verified_fill=())))

    assert fill_binding_for(()) is None
    assert without.fill_binding is None
    assert empty == without


def test_a_layered_candidate_replays_under_the_fill_that_produced_it() -> None:
    snapshot = _fill_snapshot()
    router = LayeredBoardRouter()
    island = _island((3_000, 6_000, 7_000, 7_000))
    request = _request(snapshot, verified_fill=(island,))

    candidate = _candidate(router.propose(snapshot, request))
    replayed = router.replay(snapshot, candidate, request)

    assert replayed.candidate == candidate
    assert replayed.diagnostic is None
    # Not vacuous in the other half either: the envelope candidate replays under no fill.
    envelope_request = _request(snapshot)
    envelope = _candidate(router.propose(snapshot, envelope_request))
    assert router.replay(snapshot, envelope, envelope_request).candidate == envelope


def test_layered_replay_without_the_producing_fill_refuses_instead_of_routing_differently() -> None:
    """The understated direction, which is issue #163's shape on the layered path.

    A foreign zone's outline envelope over-approximates the exact pour that retired it, so a
    replay that lost the fill is *stricter* than the route that produced the candidate.  Left
    unguarded it does not raise: it silently returns the 14,000 nm envelope route and the
    serialization boundary blames the candidate for the disagreement its own verifier caused.
    """

    snapshot = _fill_snapshot()
    router = LayeredBoardRouter()
    island = _island((3_000, 6_000, 7_000, 7_000))
    candidate = _candidate(router.propose(snapshot, _request(snapshot, verified_fill=(island,))))

    refused = router.replay(snapshot, candidate, _request(snapshot))

    assert refused.candidate is None
    assert refused.diagnostic is not None
    assert refused.diagnostic.code is LayeredRouteFailureCode.FILL_EVIDENCE_MISMATCH
    # The unguarded behaviour is a *different route*, not an error, which is why the refusal has
    # to be stated rather than inferred from a downstream mismatch.
    assert candidate.cost.wire_length_nm == 8_000
    assert _candidate(router.propose(snapshot, _request(snapshot))).cost.wire_length_nm == 14_000


def test_layered_replay_refuses_fill_a_candidate_was_never_routed_under() -> None:
    """The overstated direction, and the dangerous one.

    An envelope-routed candidate replayed *with* fill searches a looser obstacle model than the
    one that produced it: the fill retires envelopes the original search never saw retired.  A
    replay that agreed under that model would confirm geometry the router never proved.
    """

    snapshot = _fill_snapshot()
    router = LayeredBoardRouter()
    island = _island((3_000, 6_000, 7_000, 7_000))
    envelope = _candidate(router.propose(snapshot, _request(snapshot)))

    refused = router.replay(snapshot, envelope, _request(snapshot, verified_fill=(island,)))

    assert refused.candidate is None
    assert refused.diagnostic is not None
    assert refused.diagnostic.code is LayeredRouteFailureCode.FILL_EVIDENCE_MISMATCH
    assert envelope.fill_binding is None


def test_layered_replay_refuses_a_reordered_pour_that_would_reach_the_same_route() -> None:
    """The binding covers island order, so an identical obstacle *set* is still not the model.

    This is the test that shows the equality is on the recorded binding rather than on an
    incidental geometry disagreement: reordering the two islands produces a candidate equal in
    every field except the binding and the identity it feeds, so a replay that compared only
    geometry would agree.
    """

    snapshot = _fill_snapshot()
    router = LayeredBoardRouter()
    islands = (
        _island((3_000, 6_000, 7_000, 7_000)),
        _island((3_000, 3_000, 7_000, 3_200)),
    )
    settings = LayeredAStarSettings(via_cost=2, max_obstacles=9)
    candidate = _candidate(
        router.propose(snapshot, _request(snapshot, verified_fill=islands, settings=settings))
    )
    reordered_request = _request(
        snapshot, verified_fill=tuple(reversed(islands)), settings=settings
    )

    # The reordered model would reach a byte-identical route: everything but the binding matches.
    would_agree = _candidate(router.propose(snapshot, reordered_request))
    assert replace(would_agree, fill_binding=None, candidate_id=candidate.candidate_id) == replace(
        candidate, fill_binding=None
    )
    assert would_agree.fill_binding != candidate.fill_binding

    refused = router.replay(snapshot, candidate, reordered_request)

    assert refused.candidate is None
    assert refused.diagnostic is not None
    assert refused.diagnostic.code is LayeredRouteFailureCode.FILL_EVIDENCE_MISMATCH


def test_layered_replay_refuses_malformed_fill_before_comparing_a_binding() -> None:
    snapshot = _fill_snapshot()
    router = LayeredBoardRouter()
    candidate = _candidate(router.propose(snapshot, _request(snapshot)))

    refused = router.replay(snapshot, candidate, _request(snapshot, verified_fill=["island"]))

    assert refused.candidate is None
    assert refused.diagnostic is not None
    assert refused.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST


IN2_LAYER_ID = "layer:In2.Cu"


def _stacked_fill_snapshot() -> BoardIRSnapshot:
    """A four-layer board carrying the *same* foreign pour on In1.Cu and In2.Cu.

    Two islands over this board can be byte-identical in net, geometry and source revision and
    differ only in the layer they were proved on -- and both are legitimate evidence, because each
    has its own backing zone. That is the shape the binding's layer component exists for.
    """

    base = _fill_snapshot()

    def _zone(identifier: str, layer_id: str) -> Zone:
        return Zone(
            id=identifier,
            net_id=FILL_NET_ID,
            layer_id=layer_id,
            boundary=_rectangle(*FILL_ZONE_BOUNDS),
            clearance_nm=100,
            min_thickness_nm=100,
            thermal_gap_nm=100,
            thermal_bridge_width_nm=100,
        )

    return make_snapshot(
        replace(
            base.content,
            copper_layers=(
                Layer(id=LAYER_ID, name="F.Cu", index=0),
                Layer(id=INNER_LAYER_ID, name="In1.Cu", index=1),
                Layer(id=IN2_LAYER_ID, name="In2.Cu", index=2),
                Layer(id=BACK_LAYER_ID, name="B.Cu", index=3),
            ),
            zones=(
                *base.content.zones,
                _zone("zone:power-in1", INNER_LAYER_ID),
                _zone("zone:power-in2", IN2_LAYER_ID),
            ),
        )
    )


def test_the_layered_binding_distinguishes_the_layer_a_pour_was_proved_on() -> None:
    """The binding's layer component, tested by behaviour rather than by an anchor.

    Copper on In1.Cu and the same copper on In2.Cu are different obstacle models, and the only
    thing separating these two candidates is which layer the evidence was proved on: the islands
    agree in net, vertices and source revision, and the resulting candidates agree in **every**
    field but the binding and the identity it feeds. So a replay comparing geometry would accept
    either, and a binding blind to `layer_id` would too.
    """

    snapshot = _stacked_fill_snapshot()
    router = LayeredBoardRouter()
    settings = LayeredAStarSettings(via_cost=2, max_vias=4)
    on_in1 = _island(FILL_ZONE_BOUNDS, layer_id=INNER_LAYER_ID)
    on_in2 = _island(FILL_ZONE_BOUNDS, layer_id=IN2_LAYER_ID)

    assert replace(on_in1, layer_id="") == replace(on_in2, layer_id="")
    assert fill_binding_for((on_in1,)) != fill_binding_for((on_in2,))

    in1_request = _request(snapshot, verified_fill=(on_in1,), settings=settings)
    in2_request = _request(snapshot, verified_fill=(on_in2,), settings=settings)
    candidate = _candidate(router.propose(snapshot, in1_request))
    other = _candidate(router.propose(snapshot, in2_request))
    # Nothing but the binding separates them, so nothing but the binding can refuse the swap.
    assert replace(candidate, fill_binding=None, candidate_id=other.candidate_id) == replace(
        other, fill_binding=None
    )

    refused = router.replay(snapshot, candidate, in2_request)

    assert refused.candidate is None
    assert refused.diagnostic is not None
    assert refused.diagnostic.code is LayeredRouteFailureCode.FILL_EVIDENCE_MISMATCH
    # Not vacuous: the same candidate replays under the layer it was actually proved on.
    assert router.replay(snapshot, candidate, in1_request).candidate == candidate
