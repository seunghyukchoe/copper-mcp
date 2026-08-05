from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

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
    canonical_layered_candidate_bytes,
)
from copper_mcp.routing.layered_candidate_verifier import (
    LayeredCandidateVerificationCode,
    LayeredCandidateVerificationLimits,
    LayeredPhysicalValidation,
    verify_layered_candidate,
)

F_CU = "layer:F.Cu"
B_CU = "layer:B.Cu"
NET_ID = "net:audio"
FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"


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
