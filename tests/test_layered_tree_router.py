from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import (
    FootprintSide,
    Keepout,
    NetClass,
    PointNM,
    Ring,
    Segment,
    Zone,
    make_content,
    make_snapshot,
)
from copper_mcp.routing.astar import VerifiedFill, fill_binding_for
from copper_mcp.routing.layered_astar import LayeredAStarSettings
from copper_mcp.routing.layered_contracts import LayeredRouteFailureCode, LayeredRoutePath
from copper_mcp.routing.layered_tree_contracts import (
    LayeredTreeTerminal,
    canonical_layered_tree_candidate_bytes,
    with_layered_tree_candidate_id,
)
from copper_mcp.routing.layered_tree_router import LayeredTreeRequest, LayeredTreeRouter
from copper_mcp.routing.layered_tree_verifier import (
    verify_layered_tree_candidate,
    verify_reparsed_layered_tree_connectivity,
)

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "layered-tree-4layer.kicad_pcb"


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        "class:default",
        "Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _case(source: bytes | None = None):
    profile = _profile()
    source = source or FIXTURE.read_bytes()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None and not conversion.diagnostics
    snapshot = conversion.snapshot
    net_id = snapshot.content.pads[0].net_id
    pads = tuple(
        sorted(
            (pad for pad in snapshot.content.pads if pad.net_id == net_id),
            key=lambda pad: pad.id,
        )
    )
    layers = tuple(
        sorted(snapshot.content.copper_layers, key=lambda layer: (layer.index, layer.id))
    )
    request = LayeredTreeRequest(
        snapshot.snapshot_digest,
        pads[0].net_id,
        tuple(
            LayeredTreeTerminal(
                pad.id, next(layer.id for layer in layers if layer.id in pad.layer_ids)
            )
            for pad in pads
        ),
        grid_step_nm=1_000_000,
        settings=LayeredAStarSettings(),
    )
    result = LayeredTreeRouter().propose(snapshot, request)
    assert result.candidate is not None and result.diagnostic is None
    return profile, source, snapshot, request, result.candidate


def _changed_snapshot(snapshot, **changes):
    content = snapshot.content
    return make_snapshot(
        make_content(
            source=content.source,
            outline=changes.get("outline", content.outline),
            copper_layers=changes.get("copper_layers", content.copper_layers),
            nets=content.nets,
            constraints=changes.get("constraints", content.constraints),
            footprints=changes.get("footprints", content.footprints),
            pads=changes.get("pads", content.pads),
            vias=changes.get("vias", content.vias),
            segments=changes.get("segments", content.segments),
            arcs=content.arcs,
            zones=changes.get("zones", content.zones),
            keepouts=changes.get("keepouts", content.keepouts),
        )
    )


def test_three_pad_tree_is_deterministic_complete_and_layer_aware() -> None:
    _profile_value, _source, snapshot, request, candidate = _case()
    assert candidate == LayeredTreeRouter().propose(snapshot, request).candidate
    assert candidate.metrics.branch_count == 2
    assert tuple(item.pad_id for item in candidate.terminals) == tuple(
        sorted(pad.id for pad in snapshot.content.pads if pad.net_id == candidate.net_id)
    )
    assert {path.layer_id for branch in candidate.branches for path in branch.paths} >= {
        "layer:F.Cu",
        "layer:B.Cu",
        "layer:In1.Cu",
    }
    assert verify_layered_tree_candidate(candidate, snapshot, expected_request=request).ok


def test_xy_crossings_on_different_layers_do_not_connect_the_net() -> None:
    _profile_value, _source, snapshot, _request, candidate = _case()
    pads = {pad.id: pad for pad in snapshot.content.pads}
    crossing_points = (
        PointNM(30_000_000, 15_000_000),
        PointNM(20_000_000, 15_000_000),
        PointNM(30_000_000, 15_000_000),
    )
    segments = tuple(
        Segment(
            id=f"segment:test:{index}",
            net_id=candidate.net_id,
            layer_id=terminal.layer_id,
            start=pads[terminal.pad_id].center,
            end=crossing_points[index],
            width_nm=candidate.width_nm,
        )
        for index, terminal in enumerate(candidate.terminals)
    )
    disconnected = make_snapshot(replace(snapshot.content, segments=segments))
    result = verify_reparsed_layered_tree_connectivity(
        disconnected,
        candidate.net_id,
        tuple(item.pad_id for item in candidate.terminals),
    )
    assert not result.ok and result.code == "disconnected_terminal"


def test_disconnected_third_pad_and_invalid_via_span_are_refused() -> None:
    _profile_value, _source, snapshot, request, candidate = _case()
    last_branch = candidate.branches[-1]
    last_path = last_branch.paths[-1]
    end = last_path.vertices[-1]
    previous = last_path.vertices[-2]
    shifted = (
        PointNM(end.x + 1_000_000, end.y)
        if previous.y == end.y
        else PointNM(end.x, end.y + 1_000_000)
    )
    broken_path = LayeredRoutePath(last_path.layer_id, (*last_path.vertices[:-1], shifted))
    broken_branch = replace(last_branch, paths=(*last_branch.paths[:-1], broken_path))
    branches = (*candidate.branches[:-1], broken_branch)
    disconnected = replace(
        candidate,
        candidate_id=f"sha256:{'0' * 64}",
        branches=branches,
        metrics=replace(
            candidate.metrics,
            wire_length_nm=sum(branch.wire_length_nm for branch in branches),
            bend_count=sum(branch.bend_count for branch in branches),
        ),
    )
    disconnected = with_layered_tree_candidate_id(disconnected)
    assert verify_layered_tree_candidate(disconnected, snapshot).code == "branch_endpoint_mismatch"
    assert LayeredTreeRouter().replay(snapshot, disconnected, request).candidate is None

    branch = candidate.branches[0]
    invalid_via = replace(branch.vias[0], end_layer_id="layer:In1.Cu")
    altered_branch = replace(branch, vias=(invalid_via, *branch.vias[1:]))
    altered = replace(
        candidate,
        candidate_id=f"sha256:{'0' * 64}",
        branches=(altered_branch, *candidate.branches[1:]),
    )
    altered = with_layered_tree_candidate_id(altered)
    assert verify_layered_tree_candidate(altered, snapshot).code == "via_discontinuity"


@pytest.mark.parametrize("change", ["pad", "rule", "layer", "foreign_copper"])
def test_duplicate_branches_and_changed_board_identity_are_refused(change: str) -> None:
    _profile_value, _source, snapshot, request, candidate = _case()
    with pytest.raises(ValueError, match="branches"):
        replace(candidate, branches=(candidate.branches[0], candidate.branches[0]))
    if change == "pad":
        changed_pad = replace(
            snapshot.content.pads[-1],
            center=PointNM(
                snapshot.content.pads[-1].center.x + 1_000_000,
                snapshot.content.pads[-1].center.y,
            ),
        )
        changed = _changed_snapshot(snapshot, pads=(*snapshot.content.pads[:-1], changed_pad))
    elif change == "rule":
        net_class = replace(
            snapshot.content.constraints.net_classes[0],
            track_width_nm=snapshot.content.constraints.net_classes[0].track_width_nm + 1,
        )
        changed = _changed_snapshot(
            snapshot,
            constraints=replace(snapshot.content.constraints, net_classes=(net_class,)),
        )
    elif change == "layer":
        changed = _changed_snapshot(
            snapshot,
            copper_layers=(
                replace(snapshot.content.copper_layers[0], name="Front.Cu"),
                *snapshot.content.copper_layers[1:],
            ),
        )
    else:
        keepout = Keepout(
            "keepout:foreign",
            ("layer:B.Cu",),
            Ring(
                (
                    PointNM(19_000_000, 14_000_000),
                    PointNM(21_000_000, 14_000_000),
                    PointNM(21_000_000, 16_000_000),
                    PointNM(19_000_000, 16_000_000),
                )
            ),
            True,
            True,
            False,
            False,
            False,
        )
        changed = _changed_snapshot(snapshot, keepouts=(keepout,))
    assert verify_layered_tree_candidate(candidate, changed).code == "stale_revision"
    if change == "foreign_copper":
        restamped = with_layered_tree_candidate_id(
            replace(
                candidate,
                candidate_id=f"sha256:{'0' * 64}",
                base_revision=changed.snapshot_digest,
            )
        )
        replay = LayeredTreeRouter().replay(
            changed,
            restamped,
            replace(request, board_revision=changed.snapshot_digest),
        )
        assert replay.candidate is None


def test_selected_copper_and_zones_remain_explicit_refusals() -> None:
    _profile_value, _source, snapshot, request, _candidate = _case()
    selected_segment = Segment(
        "segment:selected",
        request.net_id,
        "layer:F.Cu",
        PointNM(10_000_000, 15_000_000),
        PointNM(20_000_000, 15_000_000),
        250_000,
    )
    selected = _changed_snapshot(snapshot, segments=(selected_segment,))
    selected_result = LayeredTreeRouter().propose(
        selected, replace(request, board_revision=selected.snapshot_digest)
    )
    assert selected_result.diagnostic is not None
    assert selected_result.diagnostic.code is LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY

    zone = Zone(
        "zone:selected",
        request.net_id,
        "layer:F.Cu",
        Ring(
            (
                PointNM(1_000_000, 1_000_000),
                PointNM(2_000_000, 1_000_000),
                PointNM(2_000_000, 2_000_000),
                PointNM(1_000_000, 2_000_000),
            )
        ),
        250_000,
        250_000,
        250_000,
        250_000,
    )
    zoned = _changed_snapshot(snapshot, zones=(zone,))
    zoned_result = LayeredTreeRouter().propose(
        zoned, replace(request, board_revision=zoned.snapshot_digest)
    )
    assert zoned_result.diagnostic is not None
    assert zoned_result.diagnostic.code is LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY


def _foreign_zone_case():
    _profile_value, _source, snapshot, request, _candidate = _case(
        FIXTURE.with_name("layered-tree-ordinary-4layer.kicad_pcb").read_bytes()
    )
    foreign_net = next(net.id for net in snapshot.content.nets if net.id != request.net_id)
    points = tuple(
        PointNM(x * 1_000_000, y * 1_000_000) for x, y in ((1, 1), (4, 1), (4, 4), (1, 4))
    )
    zone = Zone(
        "zone:foreign", foreign_net, "layer:F.Cu", Ring(points), 250000, 250000, 250000, 250000
    )
    snapshot = _changed_snapshot(snapshot, zones=(zone,))
    fill = VerifiedFill(foreign_net, "layer:F.Cu", points, snapshot.content.source.revision)
    return snapshot, replace(request, board_revision=snapshot.snapshot_digest), fill


def test_foreign_zones_route_and_tree_replay_binds_fill_in_both_directions():
    snapshot, request, fill = _foreign_zone_case()
    router = LayeredTreeRouter()
    plain = router.propose(snapshot, request).candidate
    request_with_fill = replace(request, verified_fill=(fill,))
    candidate = router.propose(snapshot, request_with_fill).candidate
    assert candidate is not None and plain is not None
    assert candidate.branches == plain.branches
    assert plain.fill_binding is None
    assert b"fill_binding" not in canonical_layered_tree_candidate_bytes(plain)
    assert candidate.fill_binding == fill_binding_for((fill,))
    assert router.replay(snapshot, candidate, request_with_fill).candidate == candidate
    assert verify_layered_tree_candidate(candidate, snapshot, expected_request=request_with_fill).ok
    for value, replay_request in ((candidate, request), (plain, request_with_fill)):
        refusal = router.replay(snapshot, value, replay_request)
        assert refusal.candidate is None
        assert refusal.diagnostic.code is LayeredRouteFailureCode.FILL_EVIDENCE_MISMATCH
        assert not verify_layered_tree_candidate(
            value, snapshot, expected_request=replay_request
        ).ok


@pytest.mark.parametrize("mutation", ["stale", "orphan", "escape", "malformed", "obstacle-budget"])
def test_tree_zone_evidence_refuses_without_dropping_the_obstacle(mutation):
    snapshot, request, fill = _foreign_zone_case()
    expected = LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY
    if mutation == "stale":
        fill = replace(fill, source_revision="sha256:" + "a" * 64)
        expected = LayeredRouteFailureCode.STALE_REVISION
    elif mutation == "orphan":
        fill = replace(fill, net_id="net:absent")
    elif mutation == "escape":
        fill = replace(
            fill, points=tuple(PointNM(point.x + 4_000_000, point.y) for point in fill.points)
        )
    elif mutation == "obstacle-budget":
        full = LayeredTreeRouter().propose(snapshot, request).candidate
        assert full is not None
        request = replace(
            request, settings=replace(request.settings, max_obstacles=full.metrics.obstacles - 1)
        )
        expected = LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED
    if mutation == "malformed":
        request = replace(request, verified_fill=[fill])
        expected = LayeredRouteFailureCode.INVALID_REQUEST
    else:
        request = replace(request, verified_fill=(fill,))
    result = LayeredTreeRouter().propose(snapshot, request)
    assert result.candidate is None
    assert result.diagnostic.code is expected


def test_later_branch_attaches_to_prior_copper_at_the_only_via_portal() -> None:
    _profile_value, _source, snapshot, request, _candidate = _case()
    front, back = snapshot.content.copper_layers[0], snapshot.content.copper_layers[-1]
    back = replace(back, index=1)
    centers = (
        PointNM(10_000_000, 10_000_000),
        PointNM(30_000_000, 10_000_000),
        PointNM(10_000_000, 30_000_000),
    )
    pads = tuple(
        replace(
            pad,
            center=center,
            layer_ids=(front.id,) if index == 0 else (back.id,),
        )
        for index, (pad, center) in enumerate(zip(snapshot.content.pads, centers, strict=True))
    )
    footprints = tuple(
        replace(
            footprint,
            origin=centers[index],
            side=FootprintSide.FRONT if index == 0 else FootprintSide.BACK,
        )
        for index, footprint in enumerate(snapshot.content.footprints)
    )
    outline = replace(
        snapshot.content.outline[0],
        outer=Ring(
            (
                PointNM(0, 0),
                PointNM(40_000_000, 0),
                PointNM(40_000_000, 40_000_000),
                PointNM(0, 40_000_000),
            )
        ),
    )

    def via_keepout(identifier: int, bounds: tuple[int, int, int, int]) -> Keepout:
        left, bottom, right, top = bounds
        return Keepout(
            f"keepout:portal:{identifier}",
            (front.id, back.id),
            Ring(
                (
                    PointNM(left, bottom),
                    PointNM(right, bottom),
                    PointNM(right, top),
                    PointNM(left, top),
                )
            ),
            False,
            True,
            False,
            False,
            False,
        )

    keepouts = (
        via_keepout(0, (0, 0, 17_000_000, 40_000_000)),
        via_keepout(1, (23_000_000, 0, 40_000_000, 40_000_000)),
        via_keepout(2, (17_000_000, 0, 23_000_000, 17_000_000)),
        via_keepout(3, (17_000_000, 23_000_000, 23_000_000, 40_000_000)),
    )
    portal = _changed_snapshot(
        snapshot,
        outline=(outline,),
        copper_layers=(front, back),
        footprints=footprints,
        pads=pads,
        keepouts=keepouts,
    )
    terminals = tuple(LayeredTreeTerminal(pad.id, pad.layer_ids[0]) for pad in pads)
    routed = LayeredTreeRouter().propose(
        portal,
        LayeredTreeRequest(
            portal.snapshot_digest,
            request.net_id,
            terminals,
            grid_step_nm=1_000_000,
            settings=replace(request.settings, max_vias=1),
        ),
    )
    assert routed.candidate is not None, routed.diagnostic
    assert routed.candidate.metrics.vias == 1
    assert routed.candidate.branches[1].vias == ()
    assert routed.candidate.branches[1].attachment_layer_id == back.id
    assert verify_layered_tree_candidate(routed.candidate, portal).ok


def test_aggregate_exhaustion_and_late_cancellation_are_fixed_failures() -> None:
    _profile_value, _source, snapshot, request, _candidate = _case()
    exhausted = LayeredTreeRouter().propose(
        snapshot,
        replace(request, settings=replace(request.settings, max_expansions=1)),
    )
    assert exhausted.diagnostic is not None
    assert exhausted.diagnostic.code is LayeredRouteFailureCode.SEARCH_BUDGET_EXCEEDED

    calls = 0

    def cancel_late() -> bool:
        nonlocal calls
        calls += 1
        return calls > 8

    cancelled = LayeredTreeRouter().propose(snapshot, request, cancelled=cancel_late)
    assert cancelled.diagnostic is not None
    assert cancelled.diagnostic.code is LayeredRouteFailureCode.CANCELLED
    assert calls > 8


def test_contract_admits_thirty_two_terminals_without_a_three_pad_cap() -> None:
    _profile_value, _source, snapshot, request, candidate = _case()
    template = snapshot.content.pads[0]
    pads = tuple(
        replace(
            template,
            id=f"pad:tree:{index:02d}",
            center=PointNM(5_000_000 + index * 5_000_000, 15_000_000),
            layer_ids=(request.terminals[index % len(request.terminals)].layer_id,),
        )
        for index in range(32)
    )
    footprints = tuple(
        replace(
            snapshot.content.footprints[0],
            id=f"footprint:tree:{index:02d}",
            origin=pad.center,
            pad_ids=(pad.id,),
        )
        for index, pad in enumerate(pads)
    )
    outline = replace(
        snapshot.content.outline[0],
        outer=replace(
            snapshot.content.outline[0].outer,
            points=(
                PointNM(0, 0),
                PointNM(170_000_000, 0),
                PointNM(170_000_000, 30_000_000),
                PointNM(0, 30_000_000),
            ),
        ),
    )
    expanded = _changed_snapshot(
        snapshot,
        footprints=footprints,
        pads=pads,
        outline=(outline,),
    )
    terminals = tuple(LayeredTreeTerminal(pad.id, pad.layer_ids[0]) for pad in pads)
    result = LayeredTreeRouter().propose(
        expanded,
        LayeredTreeRequest(
            expanded.snapshot_digest,
            candidate.net_id,
            terminals,
            grid_step_nm=1_000_000,
            settings=replace(
                request.settings,
                max_expansions=1_000_000,
                max_nodes=500_000,
                max_obstacle_checks=10_000_000,
            ),
        ),
    )
    assert result.candidate is not None
    assert result.candidate.metrics.branch_count == 31
