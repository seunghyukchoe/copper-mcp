from __future__ import annotations

import hashlib
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
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
    PadCopperEnvelope,
    PadKind,
    PadShape,
    PointNM,
    Ring,
    SourceInfo,
    make_content,
    make_snapshot,
)
from copper_mcp.routing import (
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRoutePath,
    LayeredRouteRequest,
    LayeredRouteVia,
    canonical_layered_candidate_bytes,
)
from copper_mcp.routing.layered_candidate_verifier import (
    LayeredCandidateVerificationCode,
    LayeredCandidateVerificationLimits,
    LayeredPhysicalValidation,
    _point_in_pad_envelope,
    verify_layered_candidate,
)

F_CU = "layer:F.Cu"
B_CU = "layer:B.Cu"
NET_ID = "net:audio"
IN1_CU = "layer:In1.Cu"
IN2_CU = "layer:In2.Cu"
FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
FOUR_LAYER_FIXTURE = (
    Path(__file__).parent / "fixtures" / "route-candidate" / "four-layer-blocked-outers.kicad_pcb"
)


def _rectangle(min_x: int, min_y: int, max_x: int, max_y: int) -> Ring:
    return Ring(
        (
            PointNM(min_x, min_y),
            PointNM(max_x, min_y),
            PointNM(max_x, max_y),
            PointNM(min_x, max_y),
        )
    )


def _simple_snapshot() -> BoardIRSnapshot:
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
            center=PointNM(1_000, 5_000),
            rotation_udeg=0,
            shape=PadShape.RECT,
            kind=PadKind.SMD,
            size_x_nm=400,
            size_y_nm=400,
            roundrect_radius_nm=None,
            drill_x_nm=None,
            drill_y_nm=None,
            layer_ids=(F_CU,),
        ),
        Pad(
            id="pad:02",
            net_id=NET_ID,
            center=PointNM(9_000, 5_000),
            rotation_udeg=0,
            shape=PadShape.RECT,
            kind=PadKind.SMD,
            size_x_nm=400,
            size_y_nm=400,
            roundrect_radius_nm=None,
            drill_x_nm=None,
            drill_y_nm=None,
            layer_ids=(F_CU,),
        ),
    )
    content = make_content(
        source=SourceInfo(
            format="test",
            revision=f"sha256:{'a' * 64}",
            format_version="1",
            generator="layered-candidate-verifier-test",
        ),
        outline=(OutlineContour(id="contour:main", outer=_rectangle(0, 0, 10_000, 10_000)),),
        copper_layers=(Layer(id=F_CU, name="F.Cu", index=0), Layer(id=B_CU, name="B.Cu", index=1)),
        nets=(Net(id=NET_ID, name="AUDIO"),),
        constraints=ConstraintSet(
            net_classes=(net_class,),
            assignments=(NetClassAssignment(net_id=NET_ID, net_class_id=net_class.id),),
        ),
        footprints=(
            Footprint(
                id="footprint:verifier",
                origin=PointNM(1_000, 5_000),
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=("pad:01", "pad:02"),
            ),
        ),
        pads=pads,
    )
    return make_snapshot(content)


def _simple_candidate() -> tuple[BoardIRSnapshot, object]:
    snapshot = _simple_snapshot()
    result = LayeredBoardRouter().propose(
        snapshot,
        LayeredRouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=NET_ID,
            start_pad_id="pad:01",
            end_pad_id="pad:02",
            start_layer_id=F_CU,
            end_layer_id=F_CU,
            grid_step_nm=1_000,
            settings=LayeredAStarSettings(via_cost=2),
        ),
    )
    assert result.candidate is not None
    return snapshot, result.candidate


def test_verifier_detects_via_in_custom_copper_only_area() -> None:
    """The conservative endpoint check must include custom primitive copper."""

    snapshot = _simple_snapshot()
    pad = replace(
        snapshot.content.pads[0],
        copper_envelope=PadCopperEnvelope(-2_000, -1_000, 2_000, 1_000),
    )
    custom_pad = pad

    # The point is outside the 400 nm anchor but inside the custom copper envelope.
    anchor_edge = (custom_pad.size_x_nm + 1) // 2
    envelope_only_point = PointNM(custom_pad.center.x + 1_500, custom_pad.center.y)
    assert envelope_only_point.x > custom_pad.center.x + anchor_edge
    assert _point_in_pad_envelope(envelope_only_point, custom_pad)
    assert not _point_in_pad_envelope(
        PointNM(custom_pad.center.x + 2_500, custom_pad.center.y), custom_pad
    )


def _blocked_candidate(*, end_on_back: bool) -> tuple[BoardIRSnapshot, object]:
    profile = KiCadConstraintProfile(
        net_classes=(
            NetClass(
                id="class:default",
                name="Default",
                clearance_nm=250_000,
                track_width_nm=250_000,
                via_diameter_nm=800_000,
                via_drill_nm=400_000,
            ),
        ),
        default_net_class_id="class:default",
    )
    conversion = parse_kicad_bytes(FIXTURE.read_bytes(), profile)
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    pads = snapshot.content.pads
    if end_on_back:
        pads = tuple(
            replace(pad, layer_ids=(B_CU,)) if pad.id.endswith("000000000004") else pad
            for pad in pads
        )
        snapshot = make_snapshot(replace(snapshot.content, pads=pads))
    net = next(pad.net_id for pad in snapshot.content.pads if pad.net_id is not None)
    endpoints = tuple(pad for pad in snapshot.content.pads if pad.net_id == net)
    result = LayeredBoardRouter().propose(
        snapshot,
        LayeredRouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net,
            start_pad_id=endpoints[0].id,
            end_pad_id=endpoints[1].id,
            start_layer_id=F_CU,
            end_layer_id=B_CU if end_on_back else F_CU,
            grid_step_nm=1_000,
            settings=LayeredAStarSettings(via_cost=2),
        ),
    )
    assert result.candidate is not None
    return snapshot, result.candidate


def _restamp(candidate: object, **changes: object):
    patch = changes.get("patch")
    if patch is not None:
        wire_length = patch.wire_length_nm
        via_count = len(patch.vias)
        changes["cost"] = replace(
            candidate.cost,
            wire_length_nm=wire_length,
            via_count=via_count,
            via_cost_units=via_count * candidate.settings.via_cost,
            total_search_cost_units=wire_length + via_count * candidate.settings.via_cost,
        )
        changes["metrics"] = replace(
            candidate.metrics,
            wire_length_nm=wire_length,
            vias=via_count,
            bend_count=patch.bend_count,
        )
    changed = replace(candidate, **changes)
    provisional = replace(changed, candidate_id=f"sha256:{'0' * 64}")
    digest = f"sha256:{hashlib.sha256(canonical_layered_candidate_bytes(provisional)).hexdigest()}"
    return replace(provisional, candidate_id=digest)


def _via_chain_patch(candidate: object, via_count: int):
    """Build a structurally valid, monotonically separated two-layer via chain."""

    start = candidate.patch.paths[0].vertices[0]
    end = candidate.patch.paths[-1].vertices[-1]
    centers = tuple(
        PointNM(start.x + index * 1_000_000, start.y + 5_000_000 + index * 1_000_000)
        for index in range(via_count)
    )
    paths = [LayeredRoutePath(F_CU, (start, centers[0]))]
    for index, (current, following) in enumerate(pairwise(centers)):
        paths.append(
            LayeredRoutePath(
                B_CU if index % 2 == 0 else F_CU,
                (current, PointNM(following.x, current.y), following),
            )
        )
    paths.append(
        LayeredRoutePath(
            B_CU if via_count % 2 else F_CU,
            (centers[-1], PointNM(centers[-1].x, end.y), end),
        )
    )
    vias = tuple(
        LayeredRouteVia(
            id=f"via:layered:{index:04d}",
            center=center,
            diameter_nm=candidate.patch.via_diameter_nm,
            drill_nm=candidate.patch.via_drill_nm,
            start_layer_id=F_CU,
            end_layer_id=B_CU,
        )
        for index, center in enumerate(centers)
    )
    return replace(candidate.patch, paths=tuple(paths), vias=vias)


def _four_layer_candidate() -> tuple[BoardIRSnapshot, object]:
    """Route the committed, KiCad 10.0.5-accepted four-layer fixture with no monkeypatching."""

    profile = KiCadConstraintProfile(
        net_classes=(
            NetClass(
                id="class:default",
                name="Default",
                clearance_nm=250_000,
                track_width_nm=250_000,
                via_diameter_nm=800_000,
                via_drill_nm=400_000,
            ),
        ),
        default_net_class_id="class:default",
    )
    conversion = parse_kicad_bytes(FOUR_LAYER_FIXTURE.read_bytes(), profile)
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    endpoints = tuple(
        pad for pad in snapshot.content.pads if pad.center.x in (10_000_000, 30_000_000)
    )
    result = LayeredBoardRouter().propose(
        snapshot,
        LayeredRouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=endpoints[0].net_id,
            start_pad_id=endpoints[0].id,
            end_pad_id=endpoints[1].id,
            start_layer_id=F_CU,
            end_layer_id=F_CU,
            grid_step_nm=1_000_000,
            settings=LayeredAStarSettings(via_cost=2),
        ),
    )
    assert result.candidate is not None
    return snapshot, result.candidate


def test_verifies_simple_candidate_and_exposes_physical_nonclaim() -> None:
    snapshot, candidate = _simple_candidate()

    result = verify_layered_candidate(
        candidate,
        snapshot,
        expected_board_revision=snapshot.snapshot_digest,
        expected_start_pad_id="pad:01",
        expected_end_pad_id="pad:02",
    )

    assert result.ok
    assert result.verified
    assert result.diagnostic.code is LayeredCandidateVerificationCode.VERIFIED
    assert result.physical_validation is LayeredPhysicalValidation.NOT_MODELED
    assert result.path_count == 1
    assert result.via_count == 0


def test_rejects_stale_snapshot_and_tampered_canonical_identity() -> None:
    snapshot, candidate = _simple_candidate()

    stale = verify_layered_candidate(
        candidate,
        snapshot,
        expected_board_revision=f"sha256:{'b' * 64}",
    )
    assert stale.diagnostic.code is LayeredCandidateVerificationCode.STALE_REVISION

    tampered = replace(candidate, candidate_id=f"sha256:{'c' * 64}")
    identity = verify_layered_candidate(tampered, snapshot)
    assert identity.diagnostic.code is LayeredCandidateVerificationCode.INVALID_CANDIDATE


def test_rejects_restamped_dimensions_that_disagree_with_the_bound_net_class() -> None:
    snapshot, candidate = _blocked_candidate(end_on_back=True)
    altered_vias = tuple(
        replace(via, diameter_nm=700_000, drill_nm=350_000) for via in candidate.patch.vias
    )
    altered_patch = replace(
        candidate.patch,
        width_nm=200_000,
        via_diameter_nm=700_000,
        via_drill_nm=350_000,
        vias=altered_vias,
    )
    restamped = _restamp(candidate, patch=altered_patch)

    result = verify_layered_candidate(restamped, snapshot)

    assert result.diagnostic.code is LayeredCandidateVerificationCode.INVALID_CANDIDATE


def test_via_budget_matches_legacy_two_layer_and_explicit_restamped_candidates() -> None:
    back_snapshot, back_candidate = _blocked_candidate(end_on_back=True)
    snapshot, candidate = _simple_candidate()
    at_limit = _restamp(
        back_candidate,
        patch=_via_chain_patch(back_candidate, 65),
        settings=LayeredAStarSettings(via_cost=2, max_vias=65),
    )
    over_limit = _restamp(
        candidate,
        patch=_via_chain_patch(candidate, 66),
        settings=LayeredAStarSettings(via_cost=2, max_vias=65),
    )
    legacy_unset = _restamp(candidate, patch=_via_chain_patch(candidate, 66))

    assert verify_layered_candidate(at_limit, back_snapshot).ok
    assert (
        verify_layered_candidate(over_limit, snapshot).diagnostic.code
        is LayeredCandidateVerificationCode.BUDGET_EXCEEDED
    )
    assert verify_layered_candidate(legacy_unset, snapshot).ok


def test_verifies_full_stack_via_transition_to_an_inner_signal_layer() -> None:
    snapshot = _simple_snapshot()
    inner = "layer:In1.Cu"
    stacked_snapshot = make_snapshot(
        replace(
            snapshot.content,
            copper_layers=(
                Layer(id=F_CU, name="F.Cu", index=0),
                Layer(id=inner, name="In1.Cu", index=1),
                Layer(id=B_CU, name="B.Cu", index=2),
            ),
        )
    )
    _, simple = _simple_candidate()
    enter_inner = PointNM(2_000, 5_000)
    leave_inner = PointNM(8_000, 5_000)
    patch = replace(
        simple.patch,
        paths=(
            LayeredRoutePath(F_CU, (PointNM(1_000, 5_000), enter_inner)),
            LayeredRoutePath(inner, (enter_inner, leave_inner)),
            LayeredRoutePath(F_CU, (leave_inner, PointNM(9_000, 5_000))),
        ),
        vias=(
            LayeredRouteVia(
                id="via:layered:0000",
                center=enter_inner,
                diameter_nm=600,
                drill_nm=300,
                start_layer_id=F_CU,
                end_layer_id=B_CU,
            ),
            LayeredRouteVia(
                id="via:layered:0001",
                center=leave_inner,
                diameter_nm=600,
                drill_nm=300,
                start_layer_id=F_CU,
                end_layer_id=B_CU,
            ),
        ),
    )
    candidate = _restamp(simple, base_revision=stacked_snapshot.snapshot_digest, patch=patch)

    result = verify_layered_candidate(candidate, stacked_snapshot)

    assert result.ok, result.diagnostic
    invalid_span = _restamp(
        candidate,
        patch=replace(
            patch,
            vias=tuple(replace(via, end_layer_id=inner) for via in patch.vias),
        ),
    )
    assert len(invalid_span.patch.vias) == 2
    refused = verify_layered_candidate(invalid_span, stacked_snapshot)
    assert refused.diagnostic.code is LayeredCandidateVerificationCode.VIA_DISCONTINUITY


def test_rejects_disconnected_path_from_via_and_cross_path_intersection() -> None:
    snapshot, candidate = _blocked_candidate(end_on_back=True)
    first_path, second_path = candidate.patch.paths
    via = candidate.patch.vias[0]

    disconnected_path = replace(
        second_path,
        vertices=(PointNM(via.center.x + 1, via.center.y), second_path.vertices[-1]),
    )
    disconnected = _restamp(
        candidate,
        patch=replace(candidate.patch, paths=(first_path, disconnected_path)),
    )
    result = verify_layered_candidate(disconnected, snapshot)
    assert result.diagnostic.code is LayeredCandidateVerificationCode.VIA_DISCONTINUITY

    # A single path can legally contain non-collinear interior vertices, so construct a bounded
    # self-crossing route with no duplicate edge.  The pair scan must reject the crossing rather
    # than mistaking it for a permitted bend.
    _, simple = _simple_candidate()
    crossing_path = LayeredRoutePath(
        F_CU,
        (
            PointNM(1_000, 5_000),
            PointNM(1_000, 4_000),
            PointNM(5_000, 4_000),
            PointNM(5_000, 6_000),
            PointNM(3_000, 6_000),
            PointNM(3_000, 3_000),
            PointNM(7_000, 3_000),
            PointNM(7_000, 5_000),
            PointNM(9_000, 5_000),
        ),
    )
    crossing = _restamp(
        simple,
        patch=replace(simple.patch, paths=(crossing_path,), vias=()),
    )
    cross_result = verify_layered_candidate(crossing, _simple_snapshot())
    assert cross_result.diagnostic.code is LayeredCandidateVerificationCode.DUPLICATE_GEOMETRY


def test_refuses_endpoint_via_and_physical_validation_claim() -> None:
    snapshot, endpoint_via = _blocked_candidate(end_on_back=True)
    endpoint_via = _restamp(
        endpoint_via,
        patch=replace(
            endpoint_via.patch,
            vias=(
                replace(
                    endpoint_via.patch.vias[0],
                    center=next(
                        pad.center
                        for pad in snapshot.content.pads
                        if pad.id == endpoint_via.end_pad_id
                    ),
                ),
            ),
        ),
    )
    endpoint_result = verify_layered_candidate(endpoint_via, snapshot)
    assert (
        endpoint_result.diagnostic.code is LayeredCandidateVerificationCode.UNSUPPORTED_ENDPOINT_VIA
    )

    structural_snapshot, structural_candidate = _blocked_candidate(end_on_back=True)
    physical_result = verify_layered_candidate(
        structural_candidate,
        structural_snapshot,
        require_physical_validation=True,
    )
    assert (
        physical_result.diagnostic.code
        is LayeredCandidateVerificationCode.PHYSICAL_VALIDATION_NOT_MODELED
    )
    assert not physical_result.ok


def test_caps_intersection_work_before_pair_scan() -> None:
    snapshot, candidate = _simple_candidate()
    result = verify_layered_candidate(
        candidate,
        snapshot,
        limits=LayeredCandidateVerificationLimits(max_pair_checks=1),
    )
    # One straight path has no pair to scan, so a tiny pair budget remains valid.  This guards the
    # bounded-limit object itself without making an unverifiable performance claim.
    assert result.ok


def test_accepts_a_legacy_two_layer_return_via_recorded_in_traversal_order() -> None:
    """A full-stack via span is unordered, so both recorded orderings must verify.

    Every two-layer candidate ever issued records its span in traversal order, so a route that
    returns to the front layer carries a ``B.Cu -> F.Cu`` via.  The KiCad serializer has always
    compared this pair as a set and always writes the canonical outer ordering, so the recorded
    order carries no physical meaning.  Reading it as ordered would make every persisted ADR-0043
    job, ADR-0047 manifest, and ADR-0048 export un-verifiable and therefore un-DRC-able.
    """

    snapshot, candidate = _blocked_candidate(end_on_back=False)
    recorded = [(via.start_layer_id, via.end_layer_id) for via in candidate.patch.vias]
    assert recorded == [(F_CU, B_CU), (B_CU, F_CU)]
    assert verify_layered_candidate(candidate, snapshot).ok

    normalized = _restamp(
        candidate,
        patch=replace(
            candidate.patch,
            vias=tuple(
                replace(via, start_layer_id=F_CU, end_layer_id=B_CU) for via in candidate.patch.vias
            ),
        ),
    )
    assert normalized.candidate_id != candidate.candidate_id
    assert verify_layered_candidate(normalized, snapshot).ok
    # Order-independence is not permissiveness: the span must still be the outer stack pair.  A
    # degenerate span cannot even be constructed, and an inner span is refused on the four-layer
    # fixture by test_real_four_layer_fixture_verifies_and_keeps_full_stack_spans.
    with pytest.raises(ValueError, match="two distinct layers"):
        replace(candidate.patch.vias[0], start_layer_id=B_CU, end_layer_id=B_CU)


def test_rejects_route_copper_crossing_a_full_stack_via_barrel() -> None:
    """A full-stack via drills every layer, so copper over its centre is joined to it.

    The chain model describes exactly two joins per via: the end of the preceding path and the
    start of the following one.  Any other contact is an unmodelled connection that makes the
    replayed chain a false description of the real topology.  On two layers the same-layer
    intersection scan covered this implicitly; from three layers up a track on a layer no other
    path uses can run straight through a barrel unnoticed, so it is checked explicitly.
    """

    snapshot, candidate = _four_layer_candidate()
    start = PointNM(10_000_000, 15_000_000)
    end = PointNM(30_000_000, 15_000_000)
    first = PointNM(17_000_000, 15_000_000)
    second = PointNM(25_000_000, 15_000_000)
    third = PointNM(21_000_000, 15_000_000)

    def _via(index: int, center: PointNM):
        return LayeredRouteVia(
            id=f"via:layered:{index:04d}",
            center=center,
            diameter_nm=candidate.patch.via_diameter_nm,
            drill_nm=candidate.patch.via_drill_nm,
            start_layer_id=F_CU,
            end_layer_id=B_CU,
        )

    # Every structural invariant the verifier checked before this fix holds: four paths for three
    # vias, each via joining consecutive path endpoints across differing layers, canonical outer
    # spans, and no same-layer overlap.  The only defect is three-dimensional: the In1.Cu path runs
    # from x=17 to x=25 straight over via 2's barrel at x=21, and the returning F.Cu path runs over
    # via 1's barrel at x=25.
    crossing = _restamp(
        candidate,
        patch=replace(
            candidate.patch,
            paths=(
                LayeredRoutePath(F_CU, (start, first)),
                LayeredRoutePath(IN1_CU, (first, second)),
                LayeredRoutePath(IN2_CU, (second, third)),
                LayeredRoutePath(F_CU, (third, end)),
            ),
            vias=(_via(0, first), _via(1, second), _via(2, third)),
        ),
    )

    result = verify_layered_candidate(crossing, snapshot)

    assert not result.ok
    assert result.diagnostic.code is LayeredCandidateVerificationCode.DUPLICATE_GEOMETRY
    assert "barrel" in result.diagnostic.message

    # Re-ordering the same three transitions so no path spans another via's centre restores a
    # candidate the chain model describes exactly, proving the check rejects the crossing rather
    # than the multilayer shape.
    monotone = _restamp(
        candidate,
        patch=replace(
            candidate.patch,
            paths=(
                LayeredRoutePath(F_CU, (start, first)),
                LayeredRoutePath(IN1_CU, (first, third)),
                LayeredRoutePath(IN2_CU, (third, second)),
                LayeredRoutePath(F_CU, (second, end)),
            ),
            vias=(_via(0, first), _via(1, third), _via(2, second)),
        ),
    )
    assert verify_layered_candidate(monotone, snapshot).ok


def test_real_four_layer_fixture_verifies_and_keeps_full_stack_spans() -> None:
    """Prove the multilayer guards on real KiCad bytes, not a hand-built snapshot."""

    snapshot, candidate = _four_layer_candidate()

    assert [layer.id for layer in snapshot.content.copper_layers] == [F_CU, IN1_CU, IN2_CU, B_CU]
    assert [path.layer_id for path in candidate.patch.paths] == [F_CU, IN1_CU, F_CU]
    assert all(
        (via.start_layer_id, via.end_layer_id) == (F_CU, B_CU) for via in candidate.patch.vias
    )
    assert verify_layered_candidate(candidate, snapshot).ok

    inner_span = _restamp(
        candidate,
        patch=replace(
            candidate.patch,
            vias=tuple(
                replace(via, start_layer_id=F_CU, end_layer_id=IN1_CU)
                for via in candidate.patch.vias
            ),
        ),
    )
    assert (
        verify_layered_candidate(inner_span, snapshot).diagnostic.code
        is LayeredCandidateVerificationCode.VIA_DISCONTINUITY
    )
